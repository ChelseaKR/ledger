"""Dual-control: propose → approve → execute, so no one steward acts alone.

The most dangerous operations in ledger — taking a record down, unsealing a
contributor's identity, opening a sealed-pending submission to the public — are the
ones a single compromised or coerced steward could most abuse. When a community sets
``config.dual_control_threshold`` above 1, those actions must be *proposed* and then
approved by that many **distinct** stewards before they execute (user research D1).
A threshold of 1 (the default) is single-steward and changes nothing.

This module is the small, durable record of pending proposals and their approvals.
It is mechanism, not policy: it does not perform the action, it only says whether an
action is *authorized yet*. The caller (CLI/server) performs the action once
:meth:`ProposalStore.is_ready` is true.

No-outing: a proposal carries an ``action`` kind, an opaque ``target`` (a record id
or an identity *ref* — never the identity itself), a steward-written ``reason``, and
the set of approving steward ids. It never holds a contributor name, a sealed value,
or any decrypted identity; the reason is held to the same no-outing rule as every
other steward-written field. Writes are atomic (temp file + ``os.replace``).
"""

from __future__ import annotations

import json
import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from ledger._filelock import file_lock
from ledger.errors import LedgerError

__all__ = ["ACTIONS", "ActionProposal", "ProposalStore"]

# The high-stakes actions dual-control governs. Kept a closed set so a typo'd action
# is rejected at the boundary rather than silently creating an ungoverned proposal.
# ``attest`` records that a named SEALED_CONDITIONAL condition has been met (e.g. a
# contributor's death, a group's dissolution); like the others it must be proposed
# and approved by distinct stewards so no one steward can declare such a thing alone
# (see :mod:`ledger.attest`).
# "aggregate-query" (EXP-14) gates a reading-room enclave query: an aggregate-only
# question over the sealed corpus, never a disclosure of any one record.
ACTIONS: frozenset[str] = frozenset(
    {
        "takedown",
        "unseal",
        "publish",
        "attest",
        "lockdown",
        "stand-up",
        "aggregate-query",
    }
)

_OPEN = "open"
_EXECUTED = "executed"
_CANCELLED = "cancelled"
_STATUSES: frozenset[str] = frozenset({_OPEN, _EXECUTED, _CANCELLED})


@dataclass(frozen=True)
class ActionProposal:
    """A proposed high-stakes action awaiting enough distinct steward approvals.

    ``approvals`` is the set of steward ids that have approved; the proposer is the
    first approver, so a threshold of 1 is satisfied by the proposal itself (which is
    exactly the single-steward behaviour). ``target`` is opaque (a record id or an
    identity ref), never an identity or sealed value.
    """

    action: str
    target: str
    reason: str
    proposer: str
    approvals: frozenset[str] = field(default_factory=frozenset)
    status: str = _OPEN
    proposal_id: str = field(default_factory=lambda: secrets.token_hex(8))
    created_at: str = ""

    def __post_init__(self) -> None:
        if self.action not in ACTIONS:
            raise LedgerError(f"unknown dual-control action; expected one of {sorted(ACTIONS)}")
        if self.status not in _STATUSES:
            raise LedgerError(f"unknown proposal status; expected one of {sorted(_STATUSES)}")
        # The proposer always counts as the first approver.
        object.__setattr__(self, "approvals", frozenset(self.approvals) | {self.proposer})

    def approved_count(self) -> int:
        """Number of distinct stewards who have approved (incl. the proposer)."""
        return len(self.approvals)

    def is_ready(self, threshold: int) -> bool:
        """Whether enough distinct stewards have approved for the action to execute."""
        return self.status == _OPEN and self.approved_count() >= max(1, threshold)

    def to_dict(self) -> dict[str, object]:
        return {
            "proposal_id": self.proposal_id,
            "action": self.action,
            "target": self.target,
            "reason": self.reason,
            "proposer": self.proposer,
            "approvals": sorted(self.approvals),
            "status": self.status,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> ActionProposal:
        approvals = data.get("approvals", [])
        return cls(
            action=str(data.get("action", "")),
            target=str(data.get("target", "")),
            reason=str(data.get("reason", "")),
            proposer=str(data.get("proposer", "")),
            approvals=frozenset(str(a) for a in approvals)
            if isinstance(approvals, list)
            else frozenset(),
            status=str(data.get("status", _OPEN)),
            proposal_id=str(data.get("proposal_id", "")),
            created_at=str(data.get("created_at", "")),
        )


class ProposalStore:
    """A durable store of dual-control proposals and their approvals."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def all(self) -> list[ActionProposal]:
        """Every proposal ever filed, in file order."""
        return self._read()

    def open_proposals(self) -> list[ActionProposal]:
        """Proposals still awaiting approval/execution (``status == open``)."""
        return [p for p in self._read() if p.status == _OPEN]

    def get(self, proposal_id: str) -> ActionProposal | None:
        """The proposal with ``proposal_id``, or ``None``."""
        for p in self._read():
            if p.proposal_id == proposal_id:
                return p
        return None

    def add(self, proposal: ActionProposal) -> ActionProposal:
        """Persist a new proposal (append-only) and return it.

        Locked read-modify-write so a proposal filed concurrently with another
        mutation is never clobbered under the threaded server (dual-control is a
        safety control; a dropped approval could authorize -- or fail to authorize
        -- a high-stakes action).
        """
        with file_lock(self._path):
            items = self._read()
            items.append(proposal)
            self._write(items)
        return proposal

    def approve(self, proposal_id: str, steward: str) -> ActionProposal:
        """Add ``steward`` to a proposal's approvals (distinct) and persist.

        Raises :class:`~ledger.errors.LedgerError` if the proposal is unknown or no
        longer open. Re-approving by the same steward is idempotent — it never
        double-counts toward the threshold (one steward is one approval). The
        read-modify-write is locked so two stewards approving at once both count
        (neither approval is lost to a racing write)."""
        with file_lock(self._path):
            items = self._read()
            for i, p in enumerate(items):
                if p.proposal_id == proposal_id:
                    if p.status != _OPEN:
                        raise LedgerError(f"proposal {proposal_id} is not open")
                    updated = replace(p, approvals=frozenset(p.approvals) | {steward})
                    items[i] = updated
                    self._write(items)
                    return updated
            raise LedgerError(f"no proposal with id {proposal_id!r}")

    def mark(self, proposal_id: str, status: str) -> None:
        """Set a proposal's terminal status (``executed`` or ``cancelled``).

        Locked like :meth:`add`/:meth:`approve` so a terminal-status write cannot
        race and be lost.
        """
        if status not in _STATUSES:
            raise LedgerError(f"unknown proposal status: {status!r}")
        with file_lock(self._path):
            items = self._read()
            for i, p in enumerate(items):
                if p.proposal_id == proposal_id:
                    items[i] = replace(p, status=status)
                    self._write(items)
                    return
            raise LedgerError(f"no proposal with id {proposal_id!r}")

    # --- persistence --------------------------------------------------------

    def _read(self) -> list[ActionProposal]:
        if not self._path.exists():
            return []
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(raw, list):
            return []
        return [ActionProposal.from_dict(item) for item in raw if isinstance(item, dict)]

    def _write(self, items: list[ActionProposal]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps([p.to_dict() for p in items], ensure_ascii=False, indent=2)
        tmp = self._path.with_name(f"{self._path.name}.{os.getpid()}.tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, self._path)
