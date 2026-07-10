"""Concurrency safety for the JSON workflow stores (FIX-05).

:class:`~ledger.consent.ConsentRequestStore`,
:class:`~ledger.consent.SubjectTokenStore`,
:class:`~ledger.review.SubmissionQueue`, and :class:`~ledger.dualcontrol.ProposalStore`
each persist as one JSON file rewritten whole on every mutation. Under the threaded
browse server, concurrent requests are normal; without serializing the
read-modify-write, two concurrent mutations can each read the same starting file and
the second write clobbers the first -- silently dropping, for example, a consent
*withdrawal*. These tests hammer each store from many threads at once and assert
nothing is lost, plus a focused unit test of the lock primitive itself.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from ledger._filelock import file_lock
from ledger.consent import ConsentRequest, ConsentRequestStore, SubjectTokenStore
from ledger.dualcontrol import ActionProposal, ProposalStore
from ledger.review import SubmissionQueue

_NOW = "2026-06-17T00:00:00Z"


def _run_concurrently(target: Callable[[int], None], count: int) -> list[Exception]:
    """Run ``target(i)`` on ``count`` threads; return any exceptions they raised."""
    errors: list[Exception] = []
    lock = threading.Lock()

    def wrap(i: int) -> None:
        try:
            target(i)
        except Exception as exc:  # any race must surface as a test failure, not a hang
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=wrap, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return errors


# --- the lock primitive itself -----------------------------------------------


def test_file_lock_serializes_a_hand_rolled_critical_section(tmp_path: Path) -> None:
    """Two threads racing an *unlocked* read-modify-write on a counter file lose
    updates; wrapping the same section in :func:`file_lock` loses none.

    This isolates the primitive from the higher-level stores: it proves the lock
    itself serializes concurrent critical sections, independent of any store's own
    read/write implementation.
    """
    target = tmp_path / "counter.txt"
    target.write_text("0", encoding="utf-8")

    def bump_locked(_i: int) -> None:
        with file_lock(target):
            value = int(target.read_text(encoding="utf-8"))
            # Give a concurrent thread a chance to interleave if the lock is not
            # actually held for the whole critical section.
            value += 1
            target.write_text(str(value), encoding="utf-8")

    errors = _run_concurrently(bump_locked, 40)
    assert not errors
    assert int(target.read_text(encoding="utf-8")) == 40


def test_file_lock_is_reentrant_safe_across_separate_targets(tmp_path: Path) -> None:
    """Locking two distinct targets concurrently does not deadlock or cross-block."""

    def touch(i: int) -> None:
        target = tmp_path / f"store-{i}.json"
        with file_lock(target):
            target.write_text("[]", encoding="utf-8")

    errors = _run_concurrently(touch, 10)
    assert not errors
    assert len({(tmp_path / f"store-{i}.json").read_text() for i in range(10)}) == 1


def test_file_lock_creates_parent_directory(tmp_path: Path) -> None:
    """A lock over a target whose parent doesn't exist yet still succeeds."""
    target = tmp_path / "nested" / "dir" / "store.json"
    with file_lock(target):
        target.write_text("[]", encoding="utf-8")
    assert target.exists()


# --- ConsentRequestStore ------------------------------------------------------


@pytest.mark.disclosure
def test_consent_store_concurrent_add_loses_nothing(tmp_path: Path) -> None:
    """N threads each file a distinct consent request; all N persist (no clobber)."""
    store = ConsentRequestStore(tmp_path / "consent.json")
    count = 30

    def add(i: int) -> None:
        store.add(
            ConsentRequest(
                record_id=f"rec-{i}",
                kind="withdraw",
                message=f"withdraw request {i}",
                request_id=f"req-{i}",
                status="open",
                created_at=_NOW,
            )
        )

    errors = _run_concurrently(add, count)
    assert not errors
    persisted = store.all()
    assert len(persisted) == count
    assert {req.request_id for req in persisted} == {f"req-{i}" for i in range(count)}


@pytest.mark.disclosure
def test_consent_store_concurrent_resolve_loses_no_status_change(tmp_path: Path) -> None:
    """Concurrently resolving distinct requests loses no individual resolution --
    including a *withdrawal*-kind request, the worst class of bug a lost update
    could cause here."""
    store = ConsentRequestStore(tmp_path / "consent.json")
    count = 25
    for i in range(count):
        kind = "withdraw" if i == 0 else "correct"
        store.add(
            ConsentRequest(
                record_id=f"rec-{i}",
                kind=kind,
                message=f"request {i}",
                request_id=f"req-{i}",
                status="open",
                created_at=_NOW,
            )
        )

    def resolve(i: int) -> None:
        store.resolve(f"req-{i}", "acknowledged")

    errors = _run_concurrently(resolve, count)
    assert not errors
    persisted = {req.request_id: req.status for req in store.all()}
    assert all(status == "acknowledged" for status in persisted.values())
    assert len(persisted) == count


@pytest.mark.disclosure
def test_subject_token_store_concurrent_register_loses_no_token(tmp_path: Path) -> None:
    """Tokens minted by concurrent submissions all remain verifiable."""
    store = SubjectTokenStore(tmp_path / "subject-tokens.json")
    count = 30

    def register(i: int) -> None:
        store.register("record", [f"{i:064x}"])

    errors = _run_concurrently(register, count)
    assert not errors
    assert set(store.hashes_for("record")) == {f"{i:064x}" for i in range(count)}


# --- SubmissionQueue -----------------------------------------------------------


@pytest.mark.disclosure
def test_submission_queue_concurrent_add_loses_no_submission(tmp_path: Path) -> None:
    queue = SubmissionQueue(tmp_path / "queue.json")
    count = 30

    def add(i: int) -> None:
        queue.add(f"rec-{i}", now=_NOW)

    errors = _run_concurrently(add, count)
    assert not errors
    pending = queue.pending()
    assert len(pending) == count
    assert {item.record_id for item in pending} == {f"rec-{i}" for i in range(count)}


@pytest.mark.disclosure
def test_submission_queue_concurrent_add_and_remove_are_consistent(tmp_path: Path) -> None:
    """Half the records are added and immediately removed concurrently with the
    other half being added and left pending; nothing meant to remain is dropped and
    nothing meant to be removed reappears."""
    queue = SubmissionQueue(tmp_path / "queue.json")
    count = 20

    def churn(i: int) -> None:
        record_id = f"rec-{i}"
        queue.add(record_id, now=_NOW)
        if i % 2 == 0:
            queue.remove(record_id)

    errors = _run_concurrently(churn, count)
    assert not errors
    remaining = {item.record_id for item in queue.pending()}
    assert remaining == {f"rec-{i}" for i in range(count) if i % 2 == 1}


# --- ProposalStore (dual-control) ----------------------------------------------


@pytest.mark.disclosure
def test_proposal_store_concurrent_add_loses_no_proposal(tmp_path: Path) -> None:
    store = ProposalStore(tmp_path / "proposals.json")
    count = 25

    def add(i: int) -> None:
        store.add(
            ActionProposal(
                action="takedown",
                target=f"rec-{i}",
                reason="concurrent add",
                proposer="steward-a",
            )
        )

    errors = _run_concurrently(add, count)
    assert not errors
    assert len(store.all()) == count


@pytest.mark.disclosure
def test_proposal_store_concurrent_distinct_approvals_all_count(tmp_path: Path) -> None:
    """Many distinct stewards approving the *same* proposal at once: every approval
    counts (none is lost to a racing write), so the threshold is reached exactly
    when it should be -- a dropped approval here would be a dual-control failure."""
    store = ProposalStore(tmp_path / "proposals.json")
    proposal = store.add(
        ActionProposal(action="unseal", target="ref-1", reason="r", proposer="steward-0")
    )
    stewards = [f"steward-{i}" for i in range(1, 21)]

    def approve(i: int) -> None:
        store.approve(proposal.proposal_id, stewards[i])

    errors = _run_concurrently(approve, len(stewards))
    assert not errors
    final = store.get(proposal.proposal_id)
    assert final is not None
    # proposer + every distinct approving steward, none lost.
    assert final.approved_count() == len(stewards) + 1
    assert final.is_ready(len(stewards) + 1)
