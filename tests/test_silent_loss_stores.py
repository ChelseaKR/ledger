"""The remaining silent-loss defects in the archive's JSON stores (#155, #154).

`docs/MULTIYEAR-PLAN.md` MP-02 and MP-03. Three of the archive's small JSON stores
persist as one document that a mutation rewrites *whole*: read the list, modify it,
write a temp file, rename it over the target. `src/ledger/_filelock.py` states the
failure that shape has, and states it in the strongest terms this repository uses:

    two concurrent POSTs can both read the same starting file, each append its own
    change, and the second rename clobbers the first -- silently dropping, for
    example, a consent *withdrawal* request. A lost withdrawal is the worst class of
    bug this project can have.

Eleven modules took that lesson. `TombstoneStore` did not: `add` and `confirm` are
unlocked read-modify-writes, so concurrent takedowns lose one another. A lost
tombstone is not a lost row -- it is a reattaching replica that is never told to
delete the copy it still holds, so a record a steward took down quietly resurrects
(#155). Hard Rule 4.

`ProposalStore` and `SubmissionQueue` took the lock but not the other half. Both
swallow `(OSError, ValueError)` in `_read` and return `[]`, so a corrupt file reads
as "nothing was ever filed" -- and because every mutation is read-modify-write, the
very next `add()` writes that empty list back and erases the history for real
(#154). `ModerationLogStore` was built against exactly this failure mode and says so
in its own docstring; these two are the stores it was pointing at.

Each test here is written to fail against the tree as it was. The concurrency tests
assert a count the racing version cannot reach; the fail-closed tests assert a raise
where the previous code returned an empty list.
"""

from __future__ import annotations

import functools
import json
import threading
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import pytest

from ledger import i18n
from ledger.access.grants import issue_grant_token
from ledger.config import Config
from ledger.dualcontrol import ActionProposal, ProposalStore
from ledger.errors import LedgerError
from ledger.ingest import Archive
from ledger.review import SubmissionQueue
from ledger.server import make_server
from ledger.tombstones import PRIMARY_LOCATION, TombstoneStore

_NOW = "2026-08-27T00:00:00Z"
_GRANT_SECRET = b"grant-secret-for-this-test-only"
_VAULT_KEY = "0123456789abcdef0123456789abcdef0123456789a="

#: Enough concurrent writers that an unlocked read-modify-write loses most of them.
#: The issue measured ~85% loss at this width; the assertion is on *all* of them, so
#: the test does not depend on the loss rate, only on the loss being non-zero.
_WIDTH = 40


def _run_together(work: list[Callable[[], None]]) -> list[BaseException]:
    """Run every callable in ``work`` at once, released from a common barrier.

    The barrier is what makes the race reliable rather than incidental: without it
    the threads start staggered and an unlocked store can happen to serialize.
    """
    barrier = threading.Barrier(len(work))
    errors: list[BaseException] = []
    lock = threading.Lock()

    def runner(fn: Callable[[], None]) -> None:
        barrier.wait()
        try:
            fn()
        except Exception as exc:  # the test reports every failure, never swallows one
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=runner, args=(fn,)) for fn in work]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return errors


# --- MP-02: TombstoneStore loses tombstones under concurrency (#155) ---------


def test_concurrent_takedowns_record_every_tombstone(tmp_path: Path) -> None:
    """40 takedowns issued at once leave 40 tombstones, not the last few to win.

    A tombstone lost here is a stale copy on a reattaching replica that nothing will
    ever tell to delete (Hard Rule 4). Fails against the unlocked store.
    """
    store = TombstoneStore(tmp_path / "logs")
    errors = _run_together(
        [functools.partial(store.add, f"record-{i:03d}", _NOW) for i in range(_WIDTH)]
    )
    assert errors == []
    assert sorted(t.record_id for t in store.all()) == sorted(
        f"record-{i:03d}" for i in range(_WIDTH)
    )


def test_concurrent_confirmations_are_all_recorded(tmp_path: Path) -> None:
    """40 replicas confirming the same takedown at once leave 40 receipts.

    A lost receipt is the inverse harm of a lost tombstone: the archive under-reports
    which locations have applied a removal, so a contributor is told a copy is still
    pending at a location that already deleted it.
    """
    store = TombstoneStore(tmp_path / "logs")
    store.add("record-shared", _NOW)
    errors = _run_together(
        [
            functools.partial(store.confirm, "record-shared", f"mirror-{i:03d}", _NOW)
            for i in range(_WIDTH)
        ]
    )
    assert errors == []
    assert sorted(store.status("record-shared") or {}) == sorted(
        f"mirror-{i:03d}" for i in range(_WIDTH)
    )


def test_a_takedown_racing_a_confirmation_loses_neither(tmp_path: Path) -> None:
    """Mixed writers on one store: every new tombstone and every receipt survives."""
    store = TombstoneStore(tmp_path / "logs")
    store.add("record-existing", _NOW)
    adds = [functools.partial(store.add, f"record-{i:03d}", _NOW) for i in range(_WIDTH // 2)]
    confirms = [
        functools.partial(store.confirm, "record-existing", f"mirror-{i:03d}", _NOW)
        for i in range(_WIDTH // 2)
    ]
    errors = _run_together([*adds, *confirms])
    assert errors == []
    assert len(store.all()) == _WIDTH // 2 + 1
    assert len(store.status("record-existing") or {}) == _WIDTH // 2


def test_the_primary_location_confirmation_survives_a_racing_takedown(tmp_path: Path) -> None:
    """The primary store's own receipt is not a special case; it races like any other."""
    store = TombstoneStore(tmp_path / "logs")
    store.add("record-primary", _NOW)
    errors = _run_together(
        [functools.partial(store.confirm, "record-primary", PRIMARY_LOCATION, _NOW)]
        + [functools.partial(store.add, f"other-{i:03d}", _NOW) for i in range(_WIDTH - 1)]
    )
    assert errors == []
    assert store.status("record-primary") == {PRIMARY_LOCATION: _NOW}
    assert len(store.all()) == _WIDTH


# --- MP-03: a corrupt store reads as empty, then is truncated (#154) ---------


def _proposal(pid: str) -> ActionProposal:
    return ActionProposal(
        action="publish",
        target="rec-1",
        reason="a stated reason",
        proposer="steward-1",
        proposal_id=pid,
        created_at=_NOW,
    )


@pytest.mark.parametrize(
    ("corruption", "why"),
    [
        ("{not json at all", "bytes that are not JSON"),
        ('{"proposals": []}', "valid JSON of the wrong shape"),
        ("", "an empty file, which is not a JSON document"),
    ],
)
def test_a_corrupt_proposal_store_raises_instead_of_reading_as_empty(
    tmp_path: Path, corruption: str, why: str
) -> None:
    """A proposal file that cannot be parsed is an error, never an empty history.

    Dual control is a safety control: reading a damaged store as "no proposals were
    ever filed" is how an approval that exists stops counting toward a threshold.
    """
    path = tmp_path / "proposals.json"
    path.write_text(corruption, encoding="utf-8")
    with pytest.raises(LedgerError):
        ProposalStore(path).all()


def test_a_corrupt_proposal_store_is_not_truncated_by_the_next_add(tmp_path: Path) -> None:
    """The harm the empty read leads to: the next mutation rewrites the file.

    Pre-change, `_read` returned `[]`, `add` appended one proposal to that empty list
    and wrote it back, and every proposal that was in the damaged file was gone for
    real rather than merely unreadable. The bytes must survive for a human to
    recover.
    """
    path = tmp_path / "proposals.json"
    original = '[{"action": "publish", "target": "rec-1"} CORRUPTED'
    path.write_text(original, encoding="utf-8")
    with pytest.raises(LedgerError):
        ProposalStore(path).add(_proposal("p-new"))
    assert path.read_text(encoding="utf-8") == original


def test_an_unreadable_proposal_store_raises_rather_than_reading_as_empty(
    tmp_path: Path,
) -> None:
    """An OSError on read leaves by the same door as a parse failure."""
    path = tmp_path / "proposals.json"
    path.mkdir()  # a directory where a file belongs: read_text raises OSError
    with pytest.raises(LedgerError):
        ProposalStore(path).all()


@pytest.mark.parametrize(
    "corruption",
    ["{not json at all", '{"queue": []}', ""],
)
def test_a_corrupt_submission_queue_raises_instead_of_reading_as_empty(
    tmp_path: Path, corruption: str
) -> None:
    """A damaged review queue must not render as "nothing awaiting review".

    Hard Rule 2 says nothing is published by inaction. The mirror of that rule is
    that nothing may be *forgotten* by inaction: a submission a contributor made and
    is waiting on must not vanish because the queue file was damaged.
    """
    path = tmp_path / "submission-queue.json"
    path.write_text(corruption, encoding="utf-8")
    with pytest.raises(LedgerError):
        SubmissionQueue(path).pending()


def test_a_corrupt_submission_queue_is_not_truncated_by_the_next_add(tmp_path: Path) -> None:
    """Same harm, same shape: an add against a damaged queue must not erase it."""
    path = tmp_path / "submission-queue.json"
    original = '[{"record_id": "rec-1", "submitted_at": "2026-01-01T00:00:00Z"} CORRUPTED'
    path.write_text(original, encoding="utf-8")
    with pytest.raises(LedgerError):
        SubmissionQueue(path).add("rec-2", now=_NOW)
    assert path.read_text(encoding="utf-8") == original


def test_a_corrupt_submission_queue_is_not_truncated_by_a_remove(tmp_path: Path) -> None:
    """`remove` rewrites the whole file too, so it fails closed for the same reason."""
    path = tmp_path / "submission-queue.json"
    original = "[[[ CORRUPTED"
    path.write_text(original, encoding="utf-8")
    with pytest.raises(LedgerError):
        SubmissionQueue(path).remove("rec-1")
    assert path.read_text(encoding="utf-8") == original


# --- the stores still work, and a missing file is still an empty store -------


def test_a_missing_store_is_still_an_empty_store(tmp_path: Path) -> None:
    """Fail-closed on *corruption*, not on absence: a fresh archive needs no setup."""
    assert ProposalStore(tmp_path / "nothing.json").all() == []
    assert SubmissionQueue(tmp_path / "nothing.json").pending() == []
    assert TombstoneStore(tmp_path / "logs").all() == []


def test_a_wellformed_store_round_trips_unchanged(tmp_path: Path) -> None:
    """The fail-closed read did not tighten what a valid store is allowed to contain."""
    proposals = ProposalStore(tmp_path / "proposals.json")
    proposals.add(_proposal("p-1"))
    proposals.add(_proposal("p-2"))
    assert [p.proposal_id for p in proposals.all()] == ["p-1", "p-2"]

    queue = SubmissionQueue(tmp_path / "queue.json")
    queue.add("rec-1", now=_NOW)
    queue.add("rec-2", now=_NOW)
    queue.remove("rec-1")
    assert [item.record_id for item in queue.pending()] == ["rec-2"]

    tombs = TombstoneStore(tmp_path / "logs")
    tombs.add("rec-1", _NOW)
    tombs.confirm("rec-1", PRIMARY_LOCATION, _NOW)
    assert tombs.status("rec-1") == {PRIMARY_LOCATION: _NOW}
    assert json.loads((tmp_path / "logs" / "tombstones.json").read_text(encoding="utf-8"))


# --- the steward console says "unreadable", never "nothing waiting" ----------


@pytest.fixture
def steward_console(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Path, str]]:
    """A running browse server with a steward grant; yields (logs_dir, base_url)."""
    monkeypatch.setenv("LEDGER_VAULT_KEY", _VAULT_KEY)
    monkeypatch.setenv("LEDGER_GRANT_SECRET", _GRANT_SECRET.decode())
    archive = Archive.init(Config.default("Queue Archive", tmp_path / "arc"))
    grants = tmp_path / "grants.json"
    grants.write_text(
        json.dumps({"steward-1": {"levels": ["public", "stewards"], "is_steward": True}}),
        encoding="utf-8",
    )
    httpd = make_server(
        archive, host="127.0.0.1", port=0, grants_path=grants, allow_contributions=True
    )
    base = f"http://127.0.0.1:{int(httpd.server_address[1])}"
    sink = StringIO()
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    with redirect_stderr(sink), redirect_stdout(sink):
        thread.start()
        try:
            yield archive.logs_dir, base
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()


def _steward_get(base: str, path: str) -> str:
    headers = {"X-Ledger-Grant": issue_grant_token("steward-1", _GRANT_SECRET)}
    req = urllib.request.Request(f"{base}{path}", headers=headers)  # noqa: S310 - loopback URL this test built for the in-process server above; no user-supplied scheme
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - same loopback request object
        return resp.read().decode("utf-8")


def test_the_console_reports_an_unreadable_queue_rather_than_an_empty_one(
    steward_console: tuple[Path, str],
) -> None:
    """A damaged queue file must not render as "no submissions awaiting review".

    The console is where a steward learns that someone is waiting on them. Rendering
    a corrupt queue as an empty one is the failure that lets a contributor's
    submission sit unseen indefinitely, so the page says what is actually wrong.
    """
    logs_dir, base = steward_console
    assert "sw_queue_unreadable" not in _steward_get(base, "/steward")  # key resolves

    (logs_dir / "submission-queue.json").write_text("{ CORRUPTED", encoding="utf-8")
    body = _steward_get(base, "/steward")

    assert "could not be read" in body
    assert "unreadable" in body
    # The empty-queue message must NOT be what a steward is shown instead.
    empty_message = i18n.t("en", "sw_no_submissions")
    assert empty_message not in body
