"""Content warnings, accountable moderation, and consent/takedown bookkeeping.

Moderation in a community archive is a question of *trust*, not just policy.
Every consequential decision here is justified (a non-empty ``reason``),
attributed (a recorded steward ``actor``), and contestable (an ``appeal`` path
that links back to the action it challenges). The log is append-only, so the
record of who decided what, and why, cannot be quietly rewritten
(accountability, transparency, credibility, auditability).

Content warnings are treated as *structured metadata*, not free-floating prose
baked into a render: they live on the record as data and are surfaced before the
material itself, so a reader can choose to proceed (understandability, safety).

The no-outing rule holds here as everywhere: actors are steward ids, reasons and
detail describe the *decision* and name the *record id*, and nothing in this
module ever places a contributor identity or a sealed value into a log line, a
filename, an exception message, or any serialized output.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field, replace
from pathlib import Path

from ledger.chain import GENESIS_HASH, ChainVerification, build_chain, chain_head
from ledger.chain import verify_chain as _verify_chain
from ledger.errors import ModerationError
from ledger.models import (
    AccessPolicy,
    PremisEvent,
    PremisEventType,
    Record,
    canonical_json,
    now_iso,
)

# The vocabulary of accountable actions. Kept small and documented so the audit
# trail is predictable and the meaning of each entry is unambiguous.
_ACTIONS: frozenset[str] = frozenset({"warn", "takedown", "restore", "consent-change", "appeal"})

# Schema history — mirrors ledger.metadata.premis.PremisLog:
#   1 — a bare JSON array of action dicts (no chaining).
#   2 — {"schemaVersion": 2, "entries": [...]}, each entry an action dict plus a
#       "prevHash" chain-link field (FIX-06: tamper-evident hash-chained logs).
_SCHEMA_VERSION = 2


def _require_reason(reason: str) -> str:
    """Return ``reason`` if it is non-empty, else raise ``ModerationError``.

    A decision without a stated rationale is not accountable; rejecting it at the
    boundary keeps the log trustworthy (accountability, credibility).
    """
    if not reason or not reason.strip():
        raise ModerationError("a moderation decision requires a non-empty reason")
    return reason


@dataclass
class ModerationAction:
    """One accountable moderation decision.

    Each action records *what* was done, *who* did it, *why*, and *to which
    record* — the four facts an audit needs. ``appeal_of`` links an appeal to the
    action it contests, making the dispute path explicit (transparency).

    No field here may carry a contributor identity or a sealed value: ``actor``
    is a steward id, ``reason`` describes the decision, and ``target_record`` is
    an opaque record id (no-outing rule).
    """

    action: str
    actor: str
    reason: str
    target_record: str
    action_id: str = field(default_factory=lambda: secrets.token_hex(16))
    appeal_of: str | None = None
    at: str = field(default_factory=now_iso)

    def __post_init__(self) -> None:
        """Validate the action vocabulary and the required rationale at birth.

        Validating in the constructor means an invalid ``ModerationAction`` can
        never reach the log or be serialized (integrity).
        """
        if self.action not in _ACTIONS:
            raise ModerationError(f"unknown moderation action: {self.action!r}")
        _require_reason(self.reason)
        if self.action == "appeal" and self.appeal_of is None:
            raise ModerationError("an appeal must reference the action it appeals")

    def to_dict(self) -> dict[str, str]:
        """Serialize to a flat string map; omit ``appeal_of`` when absent.

        Only decision metadata is emitted — no identity, no sealed value — so the
        serialized form is always safe to log or persist (no-outing rule).
        """
        out: dict[str, str] = {
            "action": self.action,
            "actor": self.actor,
            "reason": self.reason,
            "target_record": self.target_record,
            "action_id": self.action_id,
            "at": self.at,
        }
        if self.appeal_of is not None:
            out["appeal_of"] = self.appeal_of
        return out

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> ModerationAction:
        """Reconstruct an action from its serialized form.

        Round-trips :meth:`to_dict`; re-validates via ``__post_init__`` so a
        malformed on-disk entry is rejected on read (integrity). A missing required
        field raises :class:`~ledger.errors.ModerationError` (not a bare
        ``KeyError``), matching the documented "rejected on read" contract so a
        caller catches one error family (analyzability).
        """
        try:
            return cls(
                action=data["action"],
                actor=data["actor"],
                reason=data["reason"],
                target_record=data["target_record"],
                action_id=data["action_id"],
                appeal_of=data.get("appeal_of"),
                at=data["at"],
            )
        except KeyError as exc:
            raise ModerationError(f"malformed moderation entry: missing {exc}") from exc


class ModerationLog:
    """An append-only, hash-chained log of accountable moderation decisions.

    Append-only is the core property: actions are added, never edited or removed,
    so the history of who decided what cannot be silently rewritten
    (auditability, tamper-evidence). Serialization is canonical (sorted keys,
    compact) and writes are atomic, so the persisted log is byte-reproducible and
    a crash mid-write cannot truncate it (reproducibility, fault-tolerance).

    Each action also carries a chain link (a ``prevHash`` computed by
    :mod:`ledger.chain`, mirroring :class:`~ledger.metadata.premis.PremisLog`) so
    silently editing a past decision changes :attr:`head`, detectable by
    :meth:`verify_chain` and by comparing heads across replicas (FIX-06).
    """

    def __init__(
        self,
        actions: list[ModerationAction] | None = None,
        prev_hashes: list[str] | None = None,
    ) -> None:
        """Create a log, optionally seeded from existing actions (e.g. on load).

        ``prev_hashes`` is normally left to be derived (see
        :meth:`~ledger.metadata.premis.PremisLog.__init__` for the same pattern);
        pass it only to preserve chain links read verbatim off disk.
        """
        self._actions: list[ModerationAction] = list(actions) if actions else []
        if prev_hashes is not None:
            if len(prev_hashes) != len(self._actions):
                raise ValueError("prev_hashes must have the same length as actions")
            self._prev_hashes: list[str] = list(prev_hashes)
        else:
            self._prev_hashes = build_chain([a.to_dict() for a in self._actions])

    def record(self, action: ModerationAction) -> None:
        """Append one decision, chained to the current head, rejecting any with
        an empty reason.

        The reason is re-checked here as well as in the constructor so that the
        log itself enforces the justification invariant for every entry it holds
        (accountability). Chaining -> tamper-evidence (FIX-06).
        """
        _require_reason(action.reason)
        prev = self.head
        self._actions.append(action)
        self._prev_hashes.append(prev)

    @property
    def actions(self) -> list[ModerationAction]:
        """A defensive copy of the recorded actions (the log stays append-only)."""
        return list(self._actions)

    @property
    def head(self) -> str:
        """The chain hash of the most recent action, or :data:`GENESIS_HASH` if empty.

        Recomputed from the actions' content alone (see
        :meth:`~ledger.metadata.premis.PremisLog.head` for why this — not the
        stored ``prevHash`` on the last action — is what makes an edit anywhere in
        history, not just the latest decision, move this value.
        """
        return chain_head([a.to_dict() for a in self._actions])

    def verify_chain(self) -> ChainVerification:
        """Recompute the chain from the actions and compare it to their stored links.

        See :func:`ledger.chain.verify_chain` and
        :meth:`~ledger.metadata.premis.PremisLog.verify_chain`.
        """
        return _verify_chain([a.to_dict() for a in self._actions], self._prev_hashes)

    def to_json(self) -> str:
        """Canonical JSON for the whole log: a schema-versioned envelope over each
        action's dict form plus its chain link (deterministic, hashes identically)."""
        entries = [
            {**action.to_dict(), "prevHash": prev}
            for action, prev in zip(self._actions, self._prev_hashes, strict=True)
        ]
        return canonical_json({"schemaVersion": _SCHEMA_VERSION, "entries": entries})

    @classmethod
    def from_json(cls, text: str) -> ModerationLog:
        """Parse a log from canonical JSON, re-validating each entry.

        Also reads the legacy (schema 1) bare-array format written before
        chaining existed, adopting it into the chained format the same way
        :meth:`ledger.metadata.premis.PremisLog.from_json` does (evolvability,
        with the documented migration risk: pre-chain history is not itself
        provable, only history recorded or migrated from here on).
        """
        raw = json.loads(text)
        if isinstance(raw, list):
            actions = [ModerationAction.from_dict(entry) for entry in raw]
            return cls(actions)
        if isinstance(raw, dict):
            version = raw.get("schemaVersion")
            if version != _SCHEMA_VERSION:
                raise ModerationError(f"unsupported moderation log schema_version: {version!r}")
            entries = raw.get("entries")
            if not isinstance(entries, list):
                raise ModerationError("moderation log 'entries' must be a list")
            actions = [ModerationAction.from_dict(entry) for entry in entries]
            prev_hashes = [str(entry.get("prevHash", GENESIS_HASH)) for entry in entries]
            return cls(actions, prev_hashes=prev_hashes)
        raise ModerationError(
            "moderation log must be a JSON array (legacy) or a schema-versioned object"
        )

    def write(self, path: Path) -> None:
        """Persist the log atomically (write-temp-then-rename -> fault-tolerance).

        A reader never observes a partially written file: the rename is atomic on
        POSIX filesystems, so the log is either the old contents or the new ones,
        never a truncated middle state (integrity).
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{secrets.token_hex(8)}.tmp")
        tmp.write_text(self.to_json(), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def read(cls, path: Path) -> ModerationLog:
        """Load a log from ``path`` (inverse of :meth:`write`)."""
        return cls.from_json(Path(path).read_text(encoding="utf-8"))


def add_content_warning(
    record: Record,
    warning: str,
    *,
    actor: str,
    reason: str,
    now: str,
) -> tuple[Record, PremisEvent, ModerationAction]:
    """Add a content warning to a copy of ``record``, idempotently.

    Returns the updated record, a PREMIS ``MODERATION`` event, and a ``warn``
    action. The warning is structured metadata on the record, shown before the
    material is rendered, so readers can choose to proceed (understandability,
    safety). Adding a warning that is already present is a no-op on the warning
    list (idempotence -> determinism), but the decision is still recorded for a
    complete audit trail.
    """
    _require_reason(reason)
    warnings = list(record.content_warnings)
    if warning not in warnings:
        warnings.append(warning)
    updated = replace(record, content_warnings=warnings)
    event = PremisEvent(
        event_type=PremisEventType.MODERATION,
        agent=actor,
        outcome="success",
        detail=f"content warning added: {warning}",
        linked_object=record.record_id,
        event_datetime=now,
    )
    action = ModerationAction(
        action="warn",
        actor=actor,
        reason=reason,
        target_record=record.record_id,
        at=now,
    )
    return updated, event, action


def change_consent(
    record: Record,
    new_default_policy: AccessPolicy,
    *,
    actor: str,
    reason: str,
    now: str,
) -> tuple[Record, PremisEvent, ModerationAction]:
    """Update a copy of ``record``'s default policy and record the change.

    Returns the updated record, a PREMIS ``CONSENT_CHANGE`` event, and a
    ``consent-change`` action. Consent is revocable and every change is logged
    with its rationale, so a contributor's evolving wishes are honoured and the
    history is auditable (autonomy, accountability).
    """
    _require_reason(reason)
    updated = replace(record, default_policy=new_default_policy)
    event = PremisEvent(
        event_type=PremisEventType.CONSENT_CHANGE,
        agent=actor,
        outcome="success",
        detail=f"default policy changed to {new_default_policy.value}",
        linked_object=record.record_id,
        event_datetime=now,
    )
    action = ModerationAction(
        action="consent-change",
        actor=actor,
        reason=reason,
        target_record=record.record_id,
        at=now,
    )
    return updated, event, action


def set_field_policy(
    record: Record,
    field_name: str,
    new_policy: AccessPolicy,
    *,
    unseal_at: str | None = None,
    unseal_condition: str | None = None,
    actor: str,
    reason: str,
    now: str,
) -> tuple[Record, PremisEvent, ModerationAction]:
    """Set one descriptive field's disclosure policy, returning a copy + audit trail.

    Where :func:`change_consent` moves the record's *default* policy, this moves a
    *single field's* policy — the granular control that lets a steward embargo or
    seal one sensitive field (a name, a location) while the rest of the record stays
    public (selective disclosure, autonomy). It is the engine-side primitive behind
    the ``ledger seal`` workflow, covering every disclosure shape the core supports:

    * a plain visibility level (``public`` / ``community`` / ``stewards``);
    * a **temporal embargo** — ``sealed-until`` with ``unseal_at`` — that binds every
      tier (including stewards) until the date passes, i.e. time-gated release;
    * a **conditional seal** — ``sealed-conditional`` with ``unseal_condition`` — that
      opens only when a named condition is met;
    * an **absolute seal** (``sealed``) that no grant satisfies.

    ``unseal_at``/``unseal_condition`` are set on the field exactly as given, so
    re-sealing replaces any prior date/condition and moving to a dateless level
    clears them (predictability). Raises :class:`~ledger.errors.ModerationError`
    naming only the field if no such field exists — never a value (no-outing rule).

    Returns the updated record, a PREMIS ``POLICY_CHANGE`` event, and a
    ``consent-change`` action. The event detail names only the field and the new
    policy (and, for an embargo, the public date) — never the withheld value
    (auditability, confidentiality).
    """
    _require_reason(reason)
    if record.field_named(field_name) is None:
        raise ModerationError(f"record has no field named {field_name!r}")
    new_fields = [
        replace(f, policy=new_policy, unseal_at=unseal_at, unseal_condition=unseal_condition)
        if f.name == field_name
        else f
        for f in record.fields
    ]
    updated = replace(record, fields=new_fields)
    # The embargo date is collection-public (the reading-room already shows it as a
    # withheld reason), so naming it in the steward-only audit trail leaks nothing;
    # the field's *value* never appears here.
    when = f" until {unseal_at}" if new_policy is AccessPolicy.SEALED_UNTIL and unseal_at else ""
    event = PremisEvent(
        event_type=PremisEventType.POLICY_CHANGE,
        agent=actor,
        outcome="success",
        detail=f"field {field_name!r} policy changed to {new_policy.value}{when}",
        linked_object=record.record_id,
        event_datetime=now,
    )
    action = ModerationAction(
        action="consent-change",
        actor=actor,
        reason=reason,
        target_record=record.record_id,
        at=now,
    )
    return updated, event, action


def set_payload_policy(
    record: Record,
    filename: str,
    new_policy: AccessPolicy,
    *,
    actor: str,
    reason: str,
    now: str,
) -> tuple[Record, PremisEvent, ModerationAction]:
    """Set one payload's disclosure policy, returning a copy + audit trail.

    The payload counterpart to :func:`set_field_policy`: a steward can restrict or
    open a single attached file (an audio master, a scan) independently of the
    record's descriptive fields. A :class:`~ledger.models.PayloadFile` carries no
    unseal date, so this sets the visibility *level* only — a dated embargo is a
    field-level concept. Raises :class:`~ledger.errors.ModerationError` naming only
    the filename if no such payload exists (no-outing rule).

    Returns the updated record, a PREMIS ``POLICY_CHANGE`` event, and a
    ``consent-change`` action; the detail names only the filename and policy.
    """
    _require_reason(reason)
    if not any(p.filename == filename for p in record.payloads):
        raise ModerationError(f"record has no payload named {filename!r}")
    new_payloads = [
        replace(p, policy=new_policy) if p.filename == filename else p for p in record.payloads
    ]
    updated = replace(record, payloads=new_payloads)
    event = PremisEvent(
        event_type=PremisEventType.POLICY_CHANGE,
        agent=actor,
        outcome="success",
        detail=f"payload {filename!r} policy changed to {new_policy.value}",
        linked_object=record.record_id,
        event_datetime=now,
    )
    action = ModerationAction(
        action="consent-change",
        actor=actor,
        reason=reason,
        target_record=record.record_id,
        at=now,
    )
    return updated, event, action


def takedown(
    record_id: str,
    *,
    actor: str,
    reason: str,
    now: str,
) -> tuple[PremisEvent, ModerationAction]:
    """Record an accountable takedown decision for ``record_id``.

    Returns a PREMIS ``TAKEDOWN`` event and a ``takedown`` action. This function
    records the decision only; the caller is responsible for removing copies and
    propagating the takedown to replicas. Separating the *decision record* from
    the *effect* keeps the audit trail complete even if propagation is retried
    (accountability, separation of concerns).
    """
    _require_reason(reason)
    event = PremisEvent(
        event_type=PremisEventType.TAKEDOWN,
        agent=actor,
        outcome="success",
        detail="record taken down",
        linked_object=record_id,
        event_datetime=now,
    )
    action = ModerationAction(
        action="takedown",
        actor=actor,
        reason=reason,
        target_record=record_id,
        at=now,
    )
    return event, action


def appeal(
    action: ModerationAction,
    *,
    actor: str,
    reason: str,
    now: str,
) -> ModerationAction:
    """Create an appeal of a prior ``action``, linked via ``appeal_of``.

    The appeal targets the same record as the action it contests and carries its
    own actor and rationale, so a disputed decision is part of the same auditable
    chain rather than a separate, unlinked event (transparency, contestability).
    """
    _require_reason(reason)
    return ModerationAction(
        action="appeal",
        actor=actor,
        reason=reason,
        target_record=action.target_record,
        appeal_of=action.action_id,
        at=now,
    )
