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
    """An append-only log of accountable moderation decisions.

    Append-only is the core property: actions are added, never edited or removed,
    so the history of who decided what cannot be silently rewritten
    (auditability, tamper-evidence). Serialization is canonical (sorted keys,
    compact) and writes are atomic, so the persisted log is byte-reproducible and
    a crash mid-write cannot truncate it (reproducibility, fault-tolerance).
    """

    def __init__(self, actions: list[ModerationAction] | None = None) -> None:
        """Create a log, optionally seeded from existing actions (e.g. on load)."""
        self._actions: list[ModerationAction] = list(actions) if actions else []

    def record(self, action: ModerationAction) -> None:
        """Append one decision, rejecting any with an empty reason.

        The reason is re-checked here as well as in the constructor so that the
        log itself enforces the justification invariant for every entry it holds
        (accountability).
        """
        _require_reason(action.reason)
        self._actions.append(action)

    @property
    def actions(self) -> list[ModerationAction]:
        """A defensive copy of the recorded actions (the log stays append-only)."""
        return list(self._actions)

    def to_json(self) -> str:
        """Canonical JSON for the whole log (deterministic, hashes identically)."""
        return canonical_json([a.to_dict() for a in self._actions])

    @classmethod
    def from_json(cls, text: str) -> ModerationLog:
        """Parse a log from canonical JSON, re-validating each entry."""
        raw = json.loads(text)
        if not isinstance(raw, list):
            raise ModerationError("moderation log must be a JSON array")
        actions = [ModerationAction.from_dict(entry) for entry in raw]
        return cls(actions)

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
