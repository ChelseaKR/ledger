"""EXP-14 — Reading-room enclave: aggregate research access to sealed corpora.

Historians and researchers (user research D1) want the evidentiary value of the
90% of an archive that is sealed, without any one record ever being disclosed.
This module lets a researcher ask a steward-approved *aggregate* question —
"how many records mention evictions, by year" — over the whole corpus,
including records no ordinary grant may even list, and answers it only as a
suppressed, k-anonymity-floored count. It never returns a record, a field
value, or a record id to a caller outside this module.

Three safety properties, each structural rather than procedural:

1. **Never interactive; always human-approved.** A query is a closed-vocabulary
   :class:`AggregateQuery` (a Dublin-Core-derived ``dimension`` to count by, plus
   at most one ``match_field contains match_term`` filter — never free-form code
   or a record id) that must be *proposed* and then dual-control-approved
   (:mod:`ledger.dualcontrol`, action ``"aggregate-query"``) before it executes.
   A steward acting alone can never both write and answer a query.
2. **k-anonymity floor, not best-effort.** Any bucket whose matching-record count
   is below the archive's ``config.reading_room_k_floor`` is suppressed
   (:class:`Bucket` reports ``count=None``), and the query's ``total`` is
   suppressed too whenever any bucket is, so a reader can never recover a
   suppressed cell as ``total - sum(other cells)`` — the classic differencing
   attack via one query's own total. A caller may ask for a *stricter* k than the
   floor, never a laxer one (fail-closed).
3. **Cross-query differencing guard.** Two different queries whose matching-record
   sets differ by fewer than the k-floor would let a reader isolate the
   difference (frequently one record) by comparing the two answers, even though
   each individually looks safe. This module remembers the exact matching-id set
   of every query it has ever *answered* (steward-side only, in
   ``logs/reading-room-history.json`` — never served) and refuses (fails closed,
   :class:`~ledger.errors.AggregationRefused`) any new query whose matching set is
   a near-miss of a prior one.

Every execution attempt — answered or refused — is appended to
``logs/reading-room-queries.premis.json`` as a PREMIS
:attr:`~ledger.models.PremisEventType.QUERY` event, so the enclave has "a
published audit trail" (docs/ideation/03-expansions.md, EXP-14's excellence bar)
without ever naming a record.

Scope, honestly stated: this operates only on
:class:`~ledger.models.Record.dublin_core` — the collection-level descriptive
metadata that :func:`ledger.access.policy.disclose` already treats as never
per-field sealed — never on ``fields``, payloads, or identity. It is a first,
real implementation of the workflow the ideation doc describes; it does not (yet)
run against a steward-side index (EXP-14 lists FIX-04's indexed-reads machinery
as future infrastructure this can move onto) and its differencing guard is a
pairwise near-miss check, not a full privacy-budget accountant. Enabling it for
a real collection is a per-collection governance decision with a privacy SME
gate, exactly as the ideation doc requires — this module makes that decision
safe to say yes to, it does not make the decision for a community.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass

from ledger._filelock import file_lock
from ledger.dualcontrol import ActionProposal, ProposalStore
from ledger.errors import AggregationRefused, LedgerError
from ledger.ingest import Archive
from ledger.metadata.premis import PremisLog
from ledger.models import PremisEvent, PremisEventType, Record, now_iso

__all__ = [
    "AGGREGATE_QUERY_ACTION",
    "MATCH_FIELDS",
    "QUERY_DIMENSIONS",
    "AggregateQuery",
    "AggregateResult",
    "Bucket",
    "ReadingRoomEnclave",
]

#: The dual-control action name this module gates on (see :mod:`ledger.dualcontrol`).
AGGREGATE_QUERY_ACTION = "aggregate-query"

# Closed, small vocabularies — never a freeform predicate, never a raw field name a
# contributor chose. Each is a Dublin Core element that `access.policy.disclose`
# already passes through as collection-level, not per-field sealed, so aggregating
# over it (in COUNT form, k-suppressed) does not cross a new confidentiality line.
QUERY_DIMENSIONS: frozenset[str] = frozenset({"year", "type", "subject"})
MATCH_FIELDS: frozenset[str] = frozenset({"subject", "type", "description"})

_YEAR_RE = re.compile(r"(\d{4})")


@dataclass(frozen=True)
class AggregateQuery:
    """A steward-authored, closed-vocabulary aggregate question.

    Deliberately not a general query language: a researcher supplies which
    Dublin-Core-derived ``dimension`` to count records by, and at most one
    ``match_field contains match_term`` filter (case-insensitive substring). There
    is no way to name a record, request a raw value, or compose an open-ended
    predicate — every possible query is auditable at a glance from its
    :meth:`signature`, and the small vocabulary keeps the differencing-attack
    surface small enough for :class:`ReadingRoomEnclave` to actually guard.
    """

    dimension: str
    reason: str
    match_field: str | None = None
    match_term: str | None = None

    def __post_init__(self) -> None:
        if self.dimension not in QUERY_DIMENSIONS:
            raise LedgerError(
                f"unknown reading-room dimension {self.dimension!r}; "
                f"expected one of {sorted(QUERY_DIMENSIONS)}"
            )
        if not self.reason.strip():
            raise LedgerError("an aggregate query must carry a steward-auditable reason")
        if self.match_field is not None:
            if self.match_field not in MATCH_FIELDS:
                raise LedgerError(
                    f"unknown reading-room match field {self.match_field!r}; "
                    f"expected one of {sorted(MATCH_FIELDS)}"
                )
            if not self.match_term or not self.match_term.strip():
                raise LedgerError("match_term is required when match_field is set")
        elif self.match_term is not None:
            raise LedgerError("match_term requires match_field to be set")

    def signature(self) -> str:
        """A stable, content-free label safe for logs and dual-control targets."""
        term = (self.match_term or "").strip().lower()
        return f"{self.dimension}:{self.match_field or '-'}:{term}"

    def to_dict(self) -> dict[str, object]:
        return {
            "dimension": self.dimension,
            "reason": self.reason,
            "match_field": self.match_field or "",
            "match_term": self.match_term or "",
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> AggregateQuery:
        match_field = str(data.get("match_field") or "") or None
        match_term = str(data.get("match_term") or "") or None
        return cls(
            dimension=str(data.get("dimension", "")),
            reason=str(data.get("reason", "")),
            match_field=match_field,
            match_term=match_term,
        )


@dataclass(frozen=True)
class Bucket:
    """One group's answer: a count, or ``None`` if suppressed below the k-floor."""

    label: str
    count: int | None


@dataclass(frozen=True)
class AggregateResult:
    """The honest answer to one aggregate query, suppression already applied.

    ``buckets`` lists every group that had at least one matching record before
    suppression, so a suppressed group's *existence* is still visible (honesty
    without disclosure — the same posture the reading-room server takes with a
    redacted field). ``total`` is suppressed to ``None`` whenever any bucket is,
    closing the "total minus the cells you can see" differencing attack within a
    single query.
    """

    dimension: str
    match_field: str | None
    match_term: str | None
    k_floor: int
    buckets: tuple[Bucket, ...]
    total: int | None
    suppressed_buckets: int

    def to_dict(self) -> dict[str, object]:
        return {
            "dimension": self.dimension,
            "match_field": self.match_field,
            "match_term": self.match_term,
            "k_floor": self.k_floor,
            "buckets": [{"label": b.label, "count": b.count} for b in self.buckets],
            "total": self.total,
            "suppressed_buckets": self.suppressed_buckets,
        }


# --- pure query evaluation (no I/O, no dual-control, no suppression) --------


def _year_of(record: Record) -> str | None:
    for value in record.dublin_core.date:
        m = _YEAR_RE.search(value)
        if m:
            return m.group(1)
    return None


def _dimension_labels(record: Record, dimension: str) -> list[str]:
    """The bucket label(s) ``record`` contributes to for ``dimension``.

    ``year`` and ``type`` take the record's first value (single-bucket, so bucket
    counts partition the matching set); ``subject`` is genuinely multi-valued (a
    record about both "eviction" and "housing" belongs in both buckets), matching
    how :func:`ledger.search.facets` already treats subject elsewhere. Never
    returns a value that is not already in ``record.dublin_core`` — this function
    cannot manufacture a label from field or payload content.
    """
    if dimension == "year":
        year = _year_of(record)
        return [year] if year else []
    if dimension == "type":
        values = [v.strip().lower() for v in record.dublin_core.type if v.strip()]
        return values[:1]
    if dimension == "subject":
        return sorted({v.strip().lower() for v in record.dublin_core.subject if v.strip()})
    return []  # unreachable: AggregateQuery.__post_init__ closes the vocabulary


def _matches(record: Record, match_field: str | None, match_term: str | None) -> bool:
    if match_field is None:
        return True
    term = (match_term or "").strip().lower()
    values = getattr(record.dublin_core, match_field, [])
    return any(term in v.lower() for v in values)


def _evaluate(
    records: Sequence[Record], query: AggregateQuery
) -> tuple[dict[str, set[str]], set[str]]:
    """Return ``(bucket label -> matching record ids, all matching record ids)``.

    Pure, steward-side-only maths: operates on raw :class:`Record` objects
    (including records no ordinary grant may even list) and returns record ids
    only for this module's own suppression and differencing-guard logic. Nothing
    that calls this may forward a record id, or any bucket built from it, to a
    caller outside :class:`ReadingRoomEnclave` — the enclave only ever emits a
    suppressed :class:`AggregateResult`.
    """
    buckets: dict[str, set[str]] = {}
    matching: set[str] = set()
    for record in records:
        if not _matches(record, query.match_field, query.match_term):
            continue
        labels = _dimension_labels(record, query.dimension)
        if not labels:
            continue
        matching.add(record.record_id)
        for label in labels:
            buckets.setdefault(label, set()).add(record.record_id)
    return buckets, matching


def _suppress(buckets: dict[str, set[str]], k_floor: int) -> tuple[tuple[Bucket, ...], int]:
    """Apply k-anonymity cell suppression; return sorted buckets + suppressed count.

    Sorted by label (not by count) for deterministic, reproducible output — the
    same corpus and query always print the same way (predictability).
    """
    cells = sorted(((label, len(ids)) for label, ids in buckets.items()), key=lambda kv: kv[0])
    suppressed = any(n < k_floor for _, n in cells)
    visible = [Bucket(label=label, count=n) for label, n in cells if n >= k_floor]
    if suppressed:
        # Never publish a label that exists only in a sub-k sealed cell; even the
        # number of such labels is sensitive category-cardinality information.
        visible.append(Bucket(label="[suppressed]", count=None))
    return tuple(visible), int(suppressed)


def _differencing_risk(matching: set[str], history: Sequence[frozenset[str]], k_floor: int) -> bool:
    """Whether ``matching`` is a near-miss (0 < difference < k) of any past answer.

    Two answered queries whose matching sets differ by fewer than ``k_floor``
    records would let a reader isolate the difference by comparing the two
    (a)nswers even if each looked individually safe — the textbook differencing
    attack. A difference of exactly 0 (the identical question, answered again) is
    not a risk: nothing new is learned by re-asking. Fails closed: any near-miss
    refuses the whole query, not just the risky cells.
    """
    for prior in history:
        diff = len(matching ^ prior)
        if 0 < diff < k_floor:
            return True
    return False


class ReadingRoomEnclave:
    """Dual-approved, k-anonymity-floored aggregate-query access to a sealed corpus.

    Wraps an :class:`~ledger.ingest.Archive` with the propose/approve/execute
    workflow: :meth:`propose` files a :class:`~ledger.dualcontrol.ActionProposal`
    (action ``"aggregate-query"``) and privately records the query manifest;
    :meth:`approve` is a thin pass-through to
    :meth:`~ledger.dualcontrol.ProposalStore.approve`; :meth:`execute` runs the
    query only once enough distinct stewards have approved, applies k-anonymity
    suppression and the differencing guard, and logs the outcome. No method here
    ever returns a record, a field value, or a record id.
    """

    def __init__(self, archive: Archive) -> None:
        self.archive = archive
        self.proposals = ProposalStore(archive.logs_dir / "proposals.json")
        self._manifests_path = archive.logs_dir / "reading-room-manifests.json"
        self._history_path = archive.logs_dir / "reading-room-history.json"
        self._events_path = archive.logs_dir / "reading-room-queries.premis.json"

    # --- propose / approve ---------------------------------------------------

    def propose(
        self, query: AggregateQuery, *, proposer: str, now: str | None = None
    ) -> ActionProposal:
        """File ``query`` for dual-control approval; the proposer is its first approval."""
        self._ensure_enabled()
        stamp = now if now is not None else now_iso()
        proposal = self.proposals.add(
            ActionProposal(
                action=AGGREGATE_QUERY_ACTION,
                target=query.signature(),
                reason=query.reason,
                proposer=proposer,
                created_at=stamp,
            )
        )
        self._save_manifest(proposal.proposal_id, query)
        return proposal

    def approve(self, proposal_id: str, steward: str) -> ActionProposal:
        """Add a distinct steward's approval to a pending query proposal."""
        return self.proposals.approve(proposal_id, steward)

    # --- execute ---------------------------------------------------------------

    def execute(
        self,
        proposal_id: str,
        *,
        actor: str,
        k_floor: int | None = None,
        now: str | None = None,
    ) -> AggregateResult:
        """Serialize query execution so history checks and updates are one transaction."""
        self._ensure_enabled()
        try:
            with file_lock(self._history_path):
                return self._execute_locked(
                    proposal_id, actor=actor, k_floor=k_floor, now=now
                )
        except OSError as exc:
            raise LedgerError("reading-room history lock could not be acquired") from exc

    def _execute_locked(
        self,
        proposal_id: str,
        *,
        actor: str,
        k_floor: int | None = None,
        now: str | None = None,
    ) -> AggregateResult:
        """Run ``proposal_id``'s query and return its suppressed result.

        Raises :class:`~ledger.errors.LedgerError` if the proposal is unknown, is
        not an aggregate-query proposal, or has not yet met the dual-control
        threshold (fail-closed — never runs on a hunch that approval is coming).
        Raises :class:`~ledger.errors.AggregationRefused` — logged, not silent —
        if the differencing guard trips. ``k_floor`` may only raise the archive's
        configured floor, never lower it.
        """
        stamp = now if now is not None else now_iso()
        proposal = self.proposals.get(proposal_id)
        if proposal is None or proposal.action != AGGREGATE_QUERY_ACTION:
            raise LedgerError(f"no aggregate-query proposal with id {proposal_id!r}")
        threshold = max(2, self.archive.config.dual_control_threshold)
        if not proposal.is_ready(threshold):
            raise LedgerError(
                f"aggregate-query proposal {proposal_id} has only "
                f"{proposal.approved_count()}/{threshold} approval(s)"
            )

        query = self._load_manifest(proposal_id)
        floor = self.archive.config.reading_room_k_floor
        if k_floor is not None:
            if k_floor < floor:
                raise LedgerError(
                    f"k_floor {k_floor} may not be lower than the archive's "
                    f"reading_room_k_floor ({floor})"
                )
            floor = k_floor

        records = self.archive.all_records_for_aggregation()
        buckets, matching = _evaluate(records, query)
        history = self._read_history()

        if _differencing_risk(matching, history, floor):
            self._log(
                outcome="failure",
                proposal_id=proposal_id,
                actor=actor,
                detail=(
                    f"refused {query.signature()}: differencing risk against a prior answered query"
                ),
                now=stamp,
            )
            raise AggregationRefused(
                f"aggregate-query proposal {proposal_id} refused: differencing risk"
            )

        cells, suppressed = _suppress(buckets, floor)
        total = len(matching) if suppressed == 0 else None
        result = AggregateResult(
            dimension=query.dimension,
            match_field=query.match_field,
            match_term=query.match_term,
            k_floor=floor,
            buckets=cells,
            total=total,
            suppressed_buckets=suppressed,
        )

        self._append_history(matching)
        self.proposals.mark(proposal_id, "executed")
        self._log(
            outcome="success",
            proposal_id=proposal_id,
            actor=actor,
            detail=_safe_detail(query, result),
            now=stamp,
        )
        return result

    def _ensure_enabled(self) -> None:
        if not self.archive.config.reading_room_enabled:
            raise LedgerError(
                "reading-room enclave is disabled; governance must explicitly set "
                "reading_room_enabled=true with dual_control_threshold>=2"
            )

    # --- PREMIS audit trail ------------------------------------------------

    def _log(self, *, outcome: str, proposal_id: str, actor: str, detail: str, now: str) -> None:
        log = PremisLog.read(self._events_path) if self._events_path.exists() else PremisLog()
        log.record(
            PremisEvent(
                event_type=PremisEventType.QUERY,
                agent=actor,
                outcome=outcome,
                detail=detail,
                linked_object=proposal_id,
                event_datetime=now,
            )
        )
        log.write(self._events_path)

    # --- query-manifest persistence (steward-side only; never served) --------

    def _save_manifest(self, proposal_id: str, query: AggregateQuery) -> None:
        with file_lock(self._manifests_path):
            manifests = self._read_manifests()
            manifests[proposal_id] = query.to_dict()
            self._manifests_path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(manifests, ensure_ascii=False, indent=2, sort_keys=True)
            tmp = self._manifests_path.with_name(
                f"{self._manifests_path.name}.{os.getpid()}.tmp"
            )
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, self._manifests_path)

    def _load_manifest(self, proposal_id: str) -> AggregateQuery:
        data = self._read_manifests().get(proposal_id)
        if data is None:
            raise LedgerError(f"no query manifest recorded for proposal {proposal_id!r}")
        return AggregateQuery.from_dict(data)

    def _read_manifests(self) -> dict[str, dict[str, object]]:
        if not self._manifests_path.exists():
            return {}
        try:
            raw = json.loads(self._manifests_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise LedgerError("reading-room query manifests are unreadable") from exc
        if not isinstance(raw, dict) or not all(
            isinstance(key, str) and isinstance(value, dict) for key, value in raw.items()
        ):
            raise LedgerError("reading-room query manifests have an invalid shape")
        return raw

    # --- differencing-guard history (steward-side only; never served) --------

    def _read_history(self) -> list[frozenset[str]]:
        if not self._history_path.exists():
            return []
        try:
            raw = json.loads(self._history_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise LedgerError("reading-room history is unreadable; refusing query") from exc
        if not isinstance(raw, list):
            raise LedgerError("reading-room history has an invalid shape; refusing query")
        if not all(
            isinstance(entry, list) and all(isinstance(item, str) for item in entry)
            for entry in raw
        ):
            raise LedgerError("reading-room history has an invalid entry; refusing query")
        return [frozenset(entry) for entry in raw]

    def _append_history(self, matching: set[str]) -> None:
        history = self._read_history()
        history.append(frozenset(matching))
        self._history_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps([sorted(s) for s in history], ensure_ascii=False, indent=2)
        tmp = self._history_path.with_name(f"{self._history_path.name}.{os.getpid()}.tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, self._history_path)


def _safe_detail(query: AggregateQuery, result: AggregateResult) -> str:
    """A PREMIS-safe detail string: bucket labels and counts, never a record id.

    Bucket labels here are already-published, closed-vocabulary Dublin Core
    values (a year; a subject/type term the collection already uses) — the same
    values :func:`ledger.search.facets` shows publicly for *listable* records —
    so logging them, post-suppression, alongside their counts is the "published
    audit trail" EXP-14 asks for, not a new disclosure.
    """
    cells = ", ".join(
        f"{b.label}={b.count if b.count is not None else 'suppressed'}" for b in result.buckets
    )
    total = result.total if result.total is not None else "suppressed"
    return (
        f"{query.signature()} k={result.k_floor} total={total} "
        f"suppressed_buckets={result.suppressed_buckets} buckets=[{cells}]"
    )
