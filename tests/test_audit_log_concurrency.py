"""Concurrency and fail-closed safety for the archive's PREMIS audit logs.

``tests/test_filelock.py`` covers the JSON *workflow* stores (consent, review,
dual-control, subject tokens). Nothing covered the archive-level PREMIS *logs*, which
have exactly the same shape -- one JSON document that an append rewrites whole -- and
carry the accountability evidence for Hard Rule 4: which records were taken down and
why, which locations have applied a removal, which grants were exercised, and which
supervised aggregate queries ran.

The gap mattered because the guard that looks like it covers these logs does not.
:meth:`~ledger.ingest.Archive.audit_log_chains` verifies each log's ``prevHash``
chain, and a chain answers "was an entry altered", never "was an entry ever
written": each racing writer rebuilds a chain that is perfectly self-consistent over
whatever it happened to read. Measured on the unfixed code, 40 concurrent
``log_takedown`` calls left **1 to 2** events on disk across three trials -- and
``verify_chain().ok`` was ``True`` every time. A log that had silently lost 95% of
its entries reported as intact.

So these tests assert the count, not the chain. The chain is asserted too, alongside
it, precisely to keep on record that the chain alone would have passed.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from ledger.config import Config
from ledger.errors import LedgerError
from ledger.ingest import Archive
from ledger.metadata.premis import PremisLog
from ledger.models import (
    AccessPolicy,
    DublinCore,
    PremisEvent,
    PremisEventType,
    Record,
)
from ledger.replicate import _append_takedown_receipt

_NOW = "2026-06-17T00:00:00Z"

# Enough writers to make a lost update near-certain on the unfixed code (which lost
# 38-39 of 40), while staying fast enough for the merge gate.
_WRITERS = 24


def _archive(tmp_path: Path) -> Archive:
    return Archive.init(Config.default("Audit Log Archive", tmp_path / "arc"))


def _archive_with_record(tmp_path: Path) -> tuple[Archive, str]:
    """An archive holding one ingested public record, for the version-index tests."""
    archive = _archive(tmp_path)
    payload = tmp_path / "f.txt"
    payload.write_text("first take\n", encoding="utf-8")
    record = Record(
        title="versioned",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(title=["versioned"], description=["the first account"]),
    )
    archive.ingest({"f.txt": payload}, record, agent="t", now=_NOW)
    return archive, record.record_id


def _race(target: Callable[[int], None], count: int = _WRITERS) -> list[Exception]:
    """Release ``count`` threads from a common barrier; collect what they raised.

    The barrier matters: without it the threads stagger enough that an unlocked
    read-modify-write can accidentally pass, which would make this test a check that
    cannot fail in the direction it exists to check.
    """
    barrier = threading.Barrier(count)
    errors: list[Exception] = []
    guard = threading.Lock()

    def wrap(i: int) -> None:
        barrier.wait()
        try:
            target(i)
        except Exception as exc:  # a race must surface as a failure, never a hang
            with guard:
                errors.append(exc)

    threads = [threading.Thread(target=wrap, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return errors


def _takedown_event(index: int) -> PremisEvent:
    return PremisEvent(
        event_type=PremisEventType.TAKEDOWN,
        agent="steward",
        outcome="success",
        detail=f"record taken down ({index})",
        linked_object=f"{index:032x}",
        event_datetime=_NOW,
    )


@pytest.mark.preservation
def test_concurrent_takedown_decisions_all_persist(tmp_path: Path) -> None:
    """Every concurrent takedown decision reaches the accountable log.

    A takedown decision lost here is a record removed with no surviving record of
    *why* -- the one thing ``logs/takedowns.premis.json`` exists to outlive the data
    and carry.
    """
    archive = _archive(tmp_path)
    errors = _race(lambda i: archive.log_takedown(_takedown_event(i)))
    assert errors == []

    log = PremisLog.read(archive.logs_dir / "takedowns.premis.json")
    assert len(log.events) == _WRITERS
    # Every writer's own entry, not just the right count.
    assert {e.linked_object for e in log.events} == {f"{i:032x}" for i in range(_WRITERS)}
    # Asserted last and deliberately: on the unfixed code this passed while the
    # assertions above failed. The chain is not the guard here; the count is.
    assert log.verify_chain().ok


@pytest.mark.disclosure
def test_concurrent_grant_uses_all_persist(tmp_path: Path) -> None:
    """Concurrent privileged requests each leave their grant-use audit line.

    ``server.py`` also wraps its own call in a ``threading.Lock``. That lock covers
    one call site in one process; the serialization asserted here lives in the writer,
    so it holds for every caller and across processes.
    """
    archive = _archive(tmp_path)
    errors = _race(lambda i: archive.log_grant_use(f"subject-{i}", "api", now=_NOW))
    assert errors == []

    log = PremisLog.read(archive.logs_dir / "grant-uses.premis.json")
    assert len(log.events) == _WRITERS
    assert {e.agent for e in log.events} == {f"subject-{i}" for i in range(_WRITERS)}
    assert log.verify_chain().ok


@pytest.mark.preservation
def test_takedown_decision_and_replication_receipt_share_one_log_safely(
    tmp_path: Path,
) -> None:
    """The decision writer and the replication receipt writer never lose each other.

    ``Archive.log_takedown`` and ``ledger.replicate._append_takedown_receipt`` write
    the *same* ``takedowns.premis.json`` from different modules, and in production
    from different processes (``apply_tombstones`` is invoked separately from the
    browse server). Half the writers here take each path.
    """
    archive = _archive(tmp_path)
    log_path = archive.logs_dir / "takedowns.premis.json"

    def writer(i: int) -> None:
        event = _takedown_event(i)
        if i % 2:
            _append_takedown_receipt(log_path, event)
        else:
            archive.log_takedown(event)

    assert _race(writer) == []
    log = PremisLog.read(log_path)
    assert len(log.events) == _WRITERS
    assert {e.linked_object for e in log.events} == {f"{i:032x}" for i in range(_WRITERS)}
    assert log.verify_chain().ok


@pytest.mark.preservation
def test_premis_write_leaves_no_stray_temp_file(tmp_path: Path) -> None:
    """Concurrent appends leave exactly the log behind, no ``.tmp`` siblings.

    The temp name used to be derived from ``os.getpid()``, which is the same value in
    every thread of one process: writers opened and truncated one another's temp file
    and then raced to rename a path another had already renamed away. A leftover
    ``.tmp`` beside the logs is the visible residue of that, and ``audit_log_chains``
    globs this directory.
    """
    archive = _archive(tmp_path)
    assert _race(lambda i: archive.log_takedown(_takedown_event(i))) == []
    assert [p.name for p in archive.logs_dir.glob("*.tmp")] == []


@pytest.mark.preservation
def test_concurrent_version_snapshots_all_land(tmp_path: Path) -> None:
    """Concurrent updates to one record each leave a version-index entry.

    A dropped entry orphans a superseded manifest in the CAS: the bytes are still
    stored, but nothing points at them, so a version of the record silently stops
    being reachable through :meth:`~ledger.ingest.Archive.record_versions`.
    """
    archive, rid = _archive_with_record(tmp_path)

    errors = _race(
        lambda i: archive._append_version(rid, f"sha256:{i:064x}", PremisEventType.CORRECTION.value)
    )
    assert errors == []
    entries = archive.record_versions(rid)
    assert len(entries) == _WRITERS
    assert {e["address"] for e in entries} == {f"sha256:{i:064x}" for i in range(_WRITERS)}


@pytest.mark.preservation
def test_damaged_version_index_raises_instead_of_erasing_history(tmp_path: Path) -> None:
    """A corrupt version index fails loudly; the next append does not erase history.

    This is #154's failure mode on a different file. Reading a damaged index as "no
    prior versions" would be survivable on its own -- but the reader feeds a writer,
    so the very next append would rewrite the file with only its own entry and destroy
    every prior snapshot with no exception and no event. Unknown history must never be
    reported, or acted on, as absent history.
    """
    archive, rid = _archive_with_record(tmp_path)
    archive._append_version(rid, "sha256:" + "a" * 64, PremisEventType.CORRECTION.value)
    path = archive.records_dir / f"{rid}.versions.json"
    assert len(json.loads(path.read_text(encoding="utf-8"))) == 1

    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(LedgerError, match="unreadable or not valid JSON"):
        archive.record_versions(rid)
    with pytest.raises(LedgerError, match="unreadable or not valid JSON"):
        archive._append_version(rid, "sha256:" + "b" * 64, PremisEventType.CORRECTION.value)

    # The damaged bytes are still on disk: the failed append neither read past the
    # damage nor overwrote it with a one-entry file.
    assert path.read_text(encoding="utf-8") == "{not valid json"

    path.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
    with pytest.raises(LedgerError, match="must be a JSON list"):
        archive.record_versions(rid)
