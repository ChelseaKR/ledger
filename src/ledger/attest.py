"""Condition attestation for ``SEALED_CONDITIONAL`` — 2-of-N before a seal opens.

The ``SEALED_CONDITIONAL`` tier holds an archive's most mission-defining promises:
"open after my death", "open when the group dissolves", "open when the estate is
cleared". :func:`ledger.access.policy.is_visible` already opens such a field once its
``unseal_condition`` appears in ``conditions_met`` — but until this module nothing
ever populated that set, so the tier silently degraded to steward-only.

This is the missing machinery. A steward *proposes* an attestation that a named
condition has been met; a second, distinct steward *approves* it. Only once
:data:`ATTEST_THRESHOLD` distinct stewards concur (2 — one steward must never be able
to declare a contributor dead and spring their sealed record open) is the condition
written into the archive's durable attested-conditions set and a PREMIS
``POLICY_CHANGE`` event recorded. :func:`attested_conditions` then returns that set for
every read path to pass as ``conditions_met``, so the seal opens uniformly wherever a
record is disclosed.

It leans on what already exists rather than inventing a parallel mechanism: proposals
and their *distinct*-steward approvals live in a
:class:`~ledger.dualcontrol.ProposalStore` (action kind ``"attest"``); the durable
outcome is an append-only :class:`~ledger.metadata.premis.PremisLog`, the same
accountable, replica-checkable shape every other policy change uses. Writes are atomic
(temp file + ``os.replace``), mirroring the proposal store.

No-outing rule: an attestation names only a *condition* string and steward ids — never
a contributor identity, a sealed value, or which record it unseals. A stolen
attested-conditions file reveals that, say, ``group-dissolved`` is true; nothing about
any person.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ledger.dualcontrol import ActionProposal, ProposalStore
from ledger.metadata.premis import PremisLog
from ledger.models import PremisEvent, PremisEventType, now_iso

__all__ = ["ATTEST_THRESHOLD", "AttestStore", "attested_conditions"]

# The fixed 2-of-N quorum an attestation requires, independent of
# ``config.dual_control_threshold`` (which may be 1). Declaring that a condition —
# a death, a dissolution — has been met is exactly the kind of act that must never
# rest on a single steward, so this floor is not lowered by config.
ATTEST_THRESHOLD: int = 2

# On-disk state, kept beside the other archive logs under ``logs/`` so it lives and
# travels with the rest of the accountable record.
_PROPOSALS_FILENAME = "attestations.json"
_ATTESTED_FILENAME = "attested-conditions.json"
_PREMIS_FILENAME = "attestations.premis.json"


def _read_attested(path: Path) -> frozenset[str]:
    """The set of conditions attested-met at ``path`` (empty if absent/corrupt).

    Fail-closed: any read or parse failure yields the empty set, so a damaged file
    keeps every conditional seal *closed* rather than springing one open (safety)."""
    if not path.exists():
        return frozenset()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return frozenset()
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(str(c) for c in raw)


def _write_attested(path: Path, conditions: frozenset[str]) -> None:
    """Persist ``conditions`` atomically (sorted for a deterministic, stable file)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(sorted(conditions), ensure_ascii=False, indent=2)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)


def attested_conditions(logs_dir: Path) -> frozenset[str]:
    """The conditions attested-met for the archive whose logs live under ``logs_dir``.

    The one read the access layer needs: pass the result as ``conditions_met`` to
    :func:`ledger.access.disclose` / :func:`ledger.access.policy.is_visible` and every
    ``SEALED_CONDITIONAL`` field whose condition is in the set opens. Fail-closed on a
    missing or unreadable file (returns the empty set)."""
    return _read_attested(Path(logs_dir) / _ATTESTED_FILENAME)


class AttestStore:
    """Durable store for condition attestations under an archive's ``logs/`` dir.

    Wraps a :class:`~ledger.dualcontrol.ProposalStore` (a JSON file dedicated to
    ``attest`` proposals, kept separate from the general dual-control proposals so the
    ordinary ``ledger approve`` path never tries to *execute* one) plus the durable
    attested-conditions set and its PREMIS log. It is mechanism, not policy: the CLI
    validates a proposed condition against the config vocabulary before it ever
    reaches here.
    """

    def __init__(self, logs_dir: Path) -> None:
        self._dir = Path(logs_dir)
        self._proposals = ProposalStore(self._dir / _PROPOSALS_FILENAME)

    # --- proposal & approval ------------------------------------------------

    def propose(
        self, condition: str, steward: str, *, reason: str = "", now: str = ""
    ) -> ActionProposal:
        """File an ``attest`` proposal that ``condition`` has been met, by ``steward``.

        The proposer counts as the first of the two required approvals, exactly as in
        the dual-control model; a *distinct* steward must still approve before the
        condition is recorded."""
        return self._proposals.add(
            ActionProposal(
                action="attest",
                target=condition,
                reason=reason,
                proposer=steward,
                created_at=now or now_iso(),
            )
        )

    def approve(
        self, proposal_id: str, steward: str, *, now: str = "", threshold: int = ATTEST_THRESHOLD
    ) -> tuple[ActionProposal, bool]:
        """Approve an attestation; record the condition once ``threshold`` stewards concur.

        Routes the approval through :meth:`ProposalStore.approve` (which counts only
        *distinct* stewards, so one steward approving twice never reaches a quorum of
        two). Returns ``(proposal, attested_now)`` where ``attested_now`` is ``True``
        exactly on the approval that tipped the proposal over the threshold — the
        approval that writes the condition into the attested set and records the
        ``POLICY_CHANGE`` event."""
        proposal = self._proposals.approve(proposal_id, steward)
        if not proposal.is_ready(threshold):
            return proposal, False
        self._record_attested(proposal, agent=steward, now=now or now_iso())
        self._proposals.mark(proposal.proposal_id, "executed")
        return proposal, True

    # --- reads --------------------------------------------------------------

    def open_proposals(self) -> list[ActionProposal]:
        """Attest proposals still awaiting a second steward's approval."""
        return self._proposals.open_proposals()

    def get(self, proposal_id: str) -> ActionProposal | None:
        """The attest proposal with ``proposal_id``, or ``None``."""
        return self._proposals.get(proposal_id)

    def attested(self) -> frozenset[str]:
        """The set of conditions attested-met for this archive."""
        return _read_attested(self._dir / _ATTESTED_FILENAME)

    # --- durable outcome ----------------------------------------------------

    def _record_attested(self, proposal: ActionProposal, *, agent: str, now: str) -> None:
        """Add the condition to the attested set and append a PREMIS ``POLICY_CHANGE``.

        The set is the machine-readable authority the access layer consults; the
        append-only PREMIS event is the accountable, replica-checkable *why*. Both are
        no-outing safe — a condition string plus steward ids, never an identity."""
        condition = proposal.target
        attested_path = self._dir / _ATTESTED_FILENAME
        _write_attested(attested_path, _read_attested(attested_path) | {condition})

        event = PremisEvent(
            event_type=PremisEventType.POLICY_CHANGE,
            agent=agent,
            outcome="success",
            detail=(
                f"condition attested and now met: {condition} "
                f"({proposal.approved_count()} distinct stewards)"
            ),
            # A condition is archive-level, not tied to one record, so there is no
            # single linked object (and naming one could hint at what it unseals).
            linked_object=None,
            event_datetime=now,
        )
        log_path = self._dir / _PREMIS_FILENAME
        log = PremisLog.read(log_path) if log_path.exists() else PremisLog()
        log.record(event)
        log.write(log_path)
