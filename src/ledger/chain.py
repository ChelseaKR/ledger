"""Hash-chained append-only logs — tamper evidence for the raw-disk attacker.

The PREMIS event log and the moderation log are append-only *only as enforced
by the application* (threat model §4.4): a steward with raw disk access can
still open ``premis.json`` in an editor and rewrite history, then re-seal the
bag's tag manifest to match. BagIt fixity alone cannot catch that, because the
manifest is just another file the same attacker can regenerate.

Hash chaining closes the other half of the gap. Every entry records
``prevHash``: the SHA-256 of the entry immediately before it, folded together
with *that* entry's own ``prevHash``. Because each hash transitively commits to
everything before it (the way a git commit or a blockchain block does), editing
any historical entry — even the very first one — changes the chain's current
head. A local, single-copy check can still be fooled by an attacker willing to
recompute the whole chain forward from their edit, which is exactly why chain
*heads* are compared across independent replicas (:mod:`ledger.replicate`) and
published for community cross-checking (``/proof``): an attacker who does not
control every replica at once cannot make a rewritten history agree with the
heads other copies already hold (auditability, accountability, provability).

Stdlib only: this module adds nothing beyond :mod:`hashlib` and the archive's
own :func:`~ledger.models.canonical_json`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ledger.models import canonical_json

__all__ = [
    "GENESIS_HASH",
    "ChainVerification",
    "build_chain",
    "chain_head",
    "entry_hash",
    "verify_chain",
]

# The prev_hash of the first entry in any chain — a fixed, all-zero sentinel so
# "the start of history" is a well-known value rather than a magic empty string.
GENESIS_HASH: str = "0" * 64


def entry_hash(entry: dict[str, str], prev_hash: str) -> str:
    """The chain hash of ``entry`` given the ``prev_hash`` it was recorded with.

    Computed over the *canonical* JSON of ``entry`` with ``prevHash`` folded in,
    so two archives that recorded the same history byte-for-byte compute the same
    hash (reproducibility), and the result depends on the entry's own content as
    well as on everything that came before it (tamper-evidence): ``prev_hash`` was
    itself an ``entry_hash`` of the prior entry, so this hash transitively commits
    to the whole chain, not just the immediately preceding entry.
    """
    payload = dict(entry)
    payload["prevHash"] = prev_hash
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def build_chain(entries: list[dict[str, str]]) -> list[str]:
    """The ``prev_hash`` each of ``entries`` would receive if appended in order.

    Used both to chain a freshly recorded log and to adopt a legacy (pre-chain)
    log into the chained format: a log with no prior ``prevHash`` values gets a
    fresh chain computed from :data:`GENESIS_HASH` forward, exactly as if every
    entry had been recorded with :meth:`~ledger.metadata.premis.PremisLog.record`
    from an empty log (migration/evolvability). This cannot retroactively prove
    entries recorded before chaining existed were untampered — only entries
    recorded (or migrated) from here on are chain-protected.
    """
    prev_hashes: list[str] = []
    head = GENESIS_HASH
    for entry in entries:
        prev_hashes.append(head)
        head = entry_hash(entry, head)
    return prev_hashes


def chain_head(entries: list[dict[str, str]]) -> str:
    """The chain tip ``entries`` would have if genuinely recorded in this order.

    Deliberately recomputed from *content alone*, never from any stored
    ``prevHash`` on the entries themselves (contrast :func:`verify_chain`, which
    compares against what is stored). That distinction matters: a raw-disk
    attacker who edits an *earlier* entry's content but leaves every entry's
    literal ``prevHash`` field untouched would leave a naive "just hash the last
    entry's stored link" head unchanged, silently defeating cross-replica
    comparison. Re-deriving the whole chain from scratch means editing *any*
    entry — first, middle, or last — always moves this value, which is what makes
    it meaningful to compare across replicas or publish for community
    cross-checking (FIX-06).
    """
    if not entries:
        return GENESIS_HASH
    prev_hashes = build_chain(entries)
    return entry_hash(entries[-1], prev_hashes[-1])


@dataclass(frozen=True)
class ChainVerification:
    """The outcome of walking a log's stored ``prev_hash`` chain end to end.

    ``ok`` is true only when every stored ``prev_hash`` matches what recomputing
    the chain from the entries themselves produces. ``broken_at`` names the first
    divergent index (inspectability: a steward sees exactly where history stopped
    matching, not just that it did) — ``None`` when the chain is intact. ``head``
    is the chain's current tip: the value the *next* recorded entry would carry as
    its ``prev_hash``, and the value compared across replicas to detect a rewrite
    that stayed locally self-consistent.
    """

    ok: bool
    broken_at: int | None
    head: str


def verify_chain(entries: list[dict[str, str]], prev_hashes: list[str]) -> ChainVerification:
    """Recompute the chain over ``entries`` and compare it to the stored ``prev_hashes``.

    A mismatch at index *i* means entry *i* — or the chain metadata around it —
    was altered after being recorded without also recomputing every ``prev_hash``
    from that point forward: precisely the trace a raw-disk editor leaves who does
    not run this same code (auditability). Mismatched lengths (an entry appended
    or removed without updating the parallel ``prev_hashes`` list) are reported as
    broken at the shorter list's length rather than raising (failure transparency:
    a malformed log is a finding, not a crash).
    """
    if len(entries) != len(prev_hashes):
        return ChainVerification(
            ok=False, broken_at=min(len(entries), len(prev_hashes)), head=GENESIS_HASH
        )
    expected = GENESIS_HASH
    for i, (entry, stored) in enumerate(zip(entries, prev_hashes, strict=True)):
        if stored != expected:
            return ChainVerification(ok=False, broken_at=i, head=expected)
        expected = entry_hash(entry, stored)
    return ChainVerification(ok=True, broken_at=None, head=expected)
