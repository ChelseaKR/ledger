"""Tests for :mod:`ledger.chain` — the hash-chain primitives behind FIX-06.

Covers the chain-building/verification contract in isolation (deterministic,
tamper-detecting, length-mismatch-safe) before :mod:`ledger.metadata.premis` and
:mod:`ledger.moderate` are exercised against it in their own test modules.
"""

from __future__ import annotations

import pytest

from ledger.chain import GENESIS_HASH, build_chain, entry_hash, verify_chain


def _entries() -> list[dict[str, str]]:
    return [
        {"a": "1", "b": "one"},
        {"a": "2", "b": "two"},
        {"a": "3", "b": "three"},
    ]


@pytest.mark.preservation
def test_build_chain_starts_from_genesis() -> None:
    """The first entry's prev_hash is always the genesis sentinel."""
    prev_hashes = build_chain(_entries())
    assert prev_hashes[0] == GENESIS_HASH
    assert len(prev_hashes) == 3


@pytest.mark.preservation
def test_build_chain_is_deterministic() -> None:
    """Identical entries chain to identical prev_hash values every time."""
    assert build_chain(_entries()) == build_chain(_entries())


@pytest.mark.preservation
def test_verify_chain_ok_on_freshly_built_chain() -> None:
    """A chain built by :func:`build_chain` verifies clean, with a stable head."""
    entries = _entries()
    prev_hashes = build_chain(entries)
    result = verify_chain(entries, prev_hashes)
    assert result.ok
    assert result.broken_at is None
    assert result.head == entry_hash(entries[-1], prev_hashes[-1])


@pytest.mark.preservation
def test_verify_chain_empty_log_is_ok_at_genesis() -> None:
    """An empty log trivially verifies, with the head at genesis."""
    result = verify_chain([], [])
    assert result.ok
    assert result.head == GENESIS_HASH


@pytest.mark.preservation
def test_tampering_an_entry_breaks_the_chain_from_that_point() -> None:
    """Editing one entry's content, without recomputing prev_hashes, is detected.

    This is the exact shape of a raw-disk attacker's edit: the entry's bytes
    change but the *next* entry's already-recorded prev_hash does not, so
    re-deriving what that entry's hash *should* be no longer matches.
    """
    entries = _entries()
    prev_hashes = build_chain(entries)
    tampered = [dict(e) for e in entries]
    tampered[1]["b"] = "TAMPERED"

    result = verify_chain(tampered, prev_hashes)
    assert not result.ok
    assert result.broken_at == 2  # entry 2's stored prev_hash no longer matches


@pytest.mark.preservation
def test_tampering_the_first_entry_is_also_detected() -> None:
    """Chaining is transitive: even the oldest entry's edit is caught."""
    entries = _entries()
    prev_hashes = build_chain(entries)
    tampered = [dict(e) for e in entries]
    tampered[0]["b"] = "TAMPERED"

    result = verify_chain(tampered, prev_hashes)
    assert not result.ok
    assert result.broken_at == 1


@pytest.mark.preservation
def test_reordering_entries_breaks_the_chain() -> None:
    """Swapping two entries (without recomputing hashes) is also tamper-evidence."""
    entries = _entries()
    prev_hashes = build_chain(entries)
    reordered = [entries[1], entries[0], entries[2]]

    result = verify_chain(reordered, prev_hashes)
    assert not result.ok


@pytest.mark.preservation
def test_mismatched_lengths_are_reported_broken_not_raised() -> None:
    """A parallel-array length mismatch is a finding, never a crash."""
    entries = _entries()
    prev_hashes = build_chain(entries)[:-1]  # one short

    result = verify_chain(entries, prev_hashes)
    assert not result.ok
    assert result.broken_at == len(prev_hashes)


@pytest.mark.preservation
def test_entry_hash_depends_on_prev_hash() -> None:
    """The same entry content hashes differently under a different prev_hash link.

    This is what makes the chain transitive rather than a per-entry checksum:
    the hash folds in where the entry sits in history, not just its own bytes.
    """
    entry = {"a": "1"}
    assert entry_hash(entry, GENESIS_HASH) != entry_hash(entry, "f" * 64)
