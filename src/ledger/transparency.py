"""Legal-process transparency log (EXP-10): a warrant-canary-style ``/transparency``.

The threat model (`docs/THREAT-MODEL.md` §4.2) tells a steward facing a subpoena to
"consult counsel," but gives the *community* no instrument for seeing that a demand
happened, or for noticing the silence when a steward can no longer say so. This
module is the durable, tamper-evident log a steward re-attests to on a cadence — a
warrant canary in the established sense: a dated statement that is refreshed on
schedule, whose *absence* or staleness is itself the signal.

Two things this module deliberately does **not** do, on purpose:

1. **It does not write legal text.** Whether a canary has any legal effect, and
   what it may safely say, varies by jurisdiction and depends on facts only a
   lawyer can weigh (gag orders, national-security-letter nondisclosure, a
   steward's own risk) — EXP-10's own risk note is explicit that the *substance*
   is a legal gate, not a code gate: "must not ship without counsel review." This
   module therefore ships no default or example statement text at all; every
   :class:`Attestation` carries an honest ``counsel_reviewed`` flag, the CLI
   (``ledger transparency attest``) requires ``--statement`` explicitly rather
   than falling back to placeholder copy, and ``/transparency`` renders an
   unmissable warning on any attestation not marked reviewed
   (`docs/TRANSPARENCY.md`).
2. **It does not claim cryptographic non-repudiation it cannot back.** Each
   attestation is chained by a SHA-256 digest over the previous entry's digest
   (tamper-evidence: editing, reordering, or deleting a past entry breaks the
   chain, exactly like the PREMIS event log and FIX-06's planned chain heads). A
   steward may additionally paste an out-of-band ``signature`` (e.g. the output of
   ``ssh-keygen -Y sign``) into an attestation; this module stores it opaquely and
   documents how to verify it (`docs/TRANSPARENCY.md`) but does not verify it
   itself — that stronger, publicly-verifiable signing mechanism is shared work
   with EXP-01 and is not yet built. Calling the chain a "signature" would overstate
   what one HMAC-free SHA-256 chain proves; it is described here as what it is.

No-outing: an attestation counts legal demands by type and carries free-text
composed by a steward. Like every other steward-written field in this project
(a takedown reason, a moderation note), it is held to the same rule the rest of the
codebase holds itself to — never a contributor identity or a sealed value — but
that is a *content* discipline for the steward writing it, not something this
module can enforce structurally.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ledger._filelock import file_lock
from ledger.errors import LedgerError

__all__ = [
    "DEMAND_TYPES",
    "Attestation",
    "TransparencyLog",
    "days_since",
    "is_stale",
]

# A closed, small vocabulary (mirrors ``dualcontrol.ACTIONS``): an unrecognized
# demand type is rejected at the boundary rather than silently accepted into a
# statement nobody chose the wording for (correctness, least surprise).
DEMAND_TYPES: frozenset[str] = frozenset(
    {
        "subpoena",
        "search_warrant",
        "court_order",
        "national_security_letter",
        "other",
    }
)


def _canonical(payload: Mapping[str, object]) -> bytes:
    """Deterministic JSON bytes for hashing: sorted keys, no whitespace, no locale."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def _validate_counts(counts: Mapping[str, int]) -> None:
    unknown = set(counts) - DEMAND_TYPES
    if unknown:
        raise LedgerError(
            f"unknown demand type(s) {sorted(unknown)}; expected {sorted(DEMAND_TYPES)}"
        )
    for kind, count in counts.items():
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise LedgerError(f"demand count for {kind!r} must be a non-negative integer")


def _validate_digest(field_name: str, digest: str) -> None:
    if digest and (len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)):
        raise LedgerError(f"{field_name} must be empty or a SHA-256 hex digest")


@dataclass(frozen=True)
class Attestation:
    """One dated, chained re-attestation of the archive's legal-demand posture.

    ``demand_counts`` is cumulative-to-date counts by :data:`DEMAND_TYPES`
    (never a case number, a requester's name, or any detail a gag order might
    cover — a steward under a nondisclosure order attests only the count, or, if
    even that is unsafe, stops attesting at all, which is exactly the silence a
    canary is built to make legible). ``digest`` chains to ``prev_digest`` so the
    *sequence* of attestations is tamper-evident even though no single entry is
    cryptographically signed by this module (see the module docstring).
    """

    attested_date: str
    attested_by: str
    statement_text: str
    demand_counts: Mapping[str, int] = field(default_factory=dict)
    counsel_reviewed: bool = False
    counsel_review_note: str = ""
    signature: str = ""
    prev_digest: str = ""
    digest: str = ""

    def __post_init__(self) -> None:
        try:
            datetime.strptime(self.attested_date, "%Y-%m-%d")
        except ValueError as exc:
            raise LedgerError("attested_date must be a valid YYYY-MM-DD date") from exc
        if not self.attested_by.strip():
            raise LedgerError("attested_by must not be empty")
        if not self.statement_text.strip():
            raise LedgerError("statement_text must not be empty")
        if type(self.counsel_reviewed) is not bool:
            raise LedgerError("counsel_reviewed must be a boolean")
        if self.counsel_reviewed and not self.counsel_review_note.strip():
            raise LedgerError("counsel_review_note is required when counsel_reviewed is true")
        _validate_counts(self.demand_counts)
        _validate_digest("prev_digest", self.prev_digest)
        _validate_digest("digest", self.digest)

    def content_digest(self) -> str:
        """SHA-256 of this attestation's content plus ``prev_digest`` (the chain link)."""
        payload = {
            "attested_date": self.attested_date,
            "attested_by": self.attested_by,
            "statement_text": self.statement_text,
            "demand_counts": dict(sorted(self.demand_counts.items())),
            "counsel_reviewed": self.counsel_reviewed,
            "counsel_review_note": self.counsel_review_note,
            "signature": self.signature,
            "prev_digest": self.prev_digest,
        }
        return hashlib.sha256(_canonical(payload)).hexdigest()

    def total_demands(self) -> int:
        return sum(self.demand_counts.values())

    def to_dict(self) -> dict[str, object]:
        return {
            "attested_date": self.attested_date,
            "attested_by": self.attested_by,
            "statement_text": self.statement_text,
            "demand_counts": dict(sorted(self.demand_counts.items())),
            "counsel_reviewed": self.counsel_reviewed,
            "counsel_review_note": self.counsel_review_note,
            "signature": self.signature,
            "prev_digest": self.prev_digest,
            "digest": self.digest,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Attestation:
        raw_counts = data.get("demand_counts", {})
        if not isinstance(raw_counts, dict):
            raise LedgerError("demand_counts must be an object")
        counts: dict[str, int] = {}
        for key, value in raw_counts.items():
            if not isinstance(key, str) or type(value) is not int:
                raise LedgerError("demand_counts must map strings to integers")
            counts[key] = value
        counsel_reviewed = data.get("counsel_reviewed", False)
        if type(counsel_reviewed) is not bool:
            raise LedgerError("counsel_reviewed must be a boolean")

        def text(field_name: str) -> str:
            value = data.get(field_name, "")
            if not isinstance(value, str):
                raise LedgerError(f"{field_name} must be a string")
            return value

        return cls(
            attested_date=text("attested_date"),
            attested_by=text("attested_by"),
            statement_text=text("statement_text"),
            demand_counts=counts,
            counsel_reviewed=counsel_reviewed,
            counsel_review_note=text("counsel_review_note"),
            signature=text("signature"),
            prev_digest=text("prev_digest"),
            digest=text("digest"),
        )


class TransparencyLog:
    """A durable, append-only, hash-chained store of :class:`Attestation` entries.

    Persistence mirrors ``dualcontrol.ProposalStore``: a single JSON array,
    written atomically (temp file + ``os.replace``) so a crash mid-write can never
    leave a half-written, unparseable log (integrity, fault-tolerance).
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def all(self) -> list[Attestation]:
        """Every attestation ever filed, oldest first."""
        return self._read()

    def latest(self) -> Attestation | None:
        """The most recent attestation, or ``None`` if the archive has never attested."""
        items = self._read()
        return items[-1] if items else None

    def append(
        self,
        *,
        attested_date: str,
        attested_by: str,
        statement_text: str,
        demand_counts: Mapping[str, int] | None = None,
        counsel_reviewed: bool = False,
        counsel_review_note: str = "",
        signature: str = "",
    ) -> Attestation:
        """Re-attest: append a new, chained entry and persist it.

        The new entry's ``prev_digest`` is the previous entry's ``digest`` (``""``
        for the first-ever attestation), so :func:`verify_chain` can detect any
        edit, reorder, or deletion of history — the same tamper-evidence discipline
        as the PREMIS event log, applied to the archive's legal-demand posture.
        """
        try:
            with file_lock(self._path):
                items = self._read()
                prev_digest = items[-1].digest if items else ""
                draft = Attestation(
                    attested_date=attested_date,
                    attested_by=attested_by,
                    statement_text=statement_text,
                    demand_counts=dict(demand_counts or {}),
                    counsel_reviewed=counsel_reviewed,
                    counsel_review_note=counsel_review_note,
                    signature=signature,
                    prev_digest=prev_digest,
                )
                entry = Attestation(
                    attested_date=draft.attested_date,
                    attested_by=draft.attested_by,
                    statement_text=draft.statement_text,
                    demand_counts=draft.demand_counts,
                    counsel_reviewed=draft.counsel_reviewed,
                    counsel_review_note=draft.counsel_review_note,
                    signature=draft.signature,
                    prev_digest=draft.prev_digest,
                    digest=draft.content_digest(),
                )
                items.append(entry)
                self._write(items)
                return entry
        except OSError as exc:
            raise LedgerError(f"transparency log could not be written: {self._path}") from exc

    # --- persistence ---------------------------------------------------------

    def _read(self) -> list[Attestation]:
        if not self._path.exists():
            return []
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise LedgerError(f"transparency log could not be read: {self._path}") from exc
        except ValueError as exc:
            raise LedgerError(f"transparency log is not valid JSON: {self._path}") from exc
        if not isinstance(raw, list):
            raise LedgerError("transparency log must contain a JSON list")
        if not all(isinstance(item, dict) for item in raw):
            raise LedgerError("every transparency log entry must be an object")
        return [Attestation.from_dict(item) for item in raw]

    def _write(self, items: list[Attestation]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps([a.to_dict() for a in items], ensure_ascii=False, indent=2)
        tmp = self._path.with_name(f"{self._path.name}.{os.getpid()}.tmp")
        try:
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise


def verify_chain(entries: list[Attestation]) -> bool:
    """Whether ``entries`` (oldest first) form an unbroken, unaltered digest chain.

    Recomputes each entry's digest from its own content and checks it both matches
    the stored ``digest`` and links to the predecessor's — so a third party with
    only the log file (no trust in the steward) can detect a rolled-back, edited,
    or reordered history, the same "detect a rollback from the log alone" bar
    EXP-01 sets for the archive-health attestation.
    """
    prev = ""
    for entry in entries:
        if entry.prev_digest != prev:
            return False
        if entry.content_digest() != entry.digest:
            return False
        prev = entry.digest
    return True


def days_since(date_str: str, *, now: datetime | None = None) -> int | None:
    """Whole days between ``date_str`` (``YYYY-MM-DD``) and ``now`` (default: today).

    Returns ``None`` for an unparseable date rather than raising: a display helper
    must degrade honestly, never crash a page over a malformed historical entry.
    """
    try:
        attested = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return None
    current = now if now is not None else datetime.now(UTC)
    delta = (current - attested).days
    return delta if delta >= 0 else None


def is_stale(latest: Attestation | None, cadence_days: int, *, now: datetime | None = None) -> bool:
    """Whether the archive is overdue to re-attest.

    True when there has never been an attestation, or the most recent one is older
    than ``cadence_days`` — the honest signal a canary exists to give: staleness
    (or silence) is shown, never quietly treated as "still current" (no-outing's
    sibling rule here: never render a stale attestation as current).
    """
    if latest is None:
        return True
    since = days_since(latest.attested_date, now=now)
    return since is None or since > cadence_days
