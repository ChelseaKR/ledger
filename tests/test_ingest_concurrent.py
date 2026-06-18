"""Concurrent-ingest safety: the content store stays intact under contention.

ledger is designed to run on a single inexpensive box, where a steward may well
script a bulk import that ingests many records at once. The content-addressed store
relies on atomic writes (write-temp-then-rename) and content-derived names, so
simultaneous ingests should neither corrupt a stored blob nor lose a record. These
tests assert that property directly rather than by reasoning about it.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from ledger.config import Config
from ledger.fixity import hash_bytes
from ledger.ingest import Archive
from ledger.models import AccessPolicy, ContentAddress, DublinCore, Field, HashAlgo, Record

_NOW = "2026-06-17T00:00:00Z"


def _make_record(archive: Archive, idx: int) -> Record:
    """A small public record, distinct per index so record ids never collide."""
    title = f"record {idx}"
    return Record(
        title=title,
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(title=[title], publisher=[archive.config.archive_name]),
        fields=[Field(name="story", value=f"public account {idx}", policy=AccessPolicy.PUBLIC)],
    )


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


@pytest.mark.preservation
def test_concurrent_distinct_ingests_all_land_and_validate(tmp_path: Path) -> None:
    """Many simultaneous ingests of distinct payloads all store and re-validate."""
    archive = Archive.init(Config.default("Concurrent Archive", tmp_path / "arc"))
    count = 12
    ids: list[str] = []
    lock = threading.Lock()

    def ingest(i: int) -> None:
        source = tmp_path / f"src-{i}.txt"
        source.write_bytes(f"distinct payload {i}\n".encode() * 16)
        record = _make_record(archive, i)
        archive.ingest({source.name: source}, record, agent="concurrent", now=_NOW)
        with lock:
            ids.append(record.record_id)

    errors = _run_concurrently(ingest, count)
    assert not errors, f"concurrent ingest raised: {errors!r}"
    assert len(set(ids)) == count  # no record was lost or overwritten

    reports = archive.audit_fixity()
    assert len(reports) == count
    assert all(report.ok for _name, report in reports)


@pytest.mark.preservation
def test_concurrent_identical_payloads_dedupe_intact(tmp_path: Path) -> None:
    """Identical bytes ingested at once resolve to one intact blob (CAS atomicity)."""
    archive = Archive.init(Config.default("Dedup Archive", tmp_path / "arc"))
    shared = b"a community keeps its own history\n" * 64
    count = 10

    def ingest(i: int) -> None:
        source = tmp_path / f"same-{i}.txt"
        source.write_bytes(shared)  # identical content, distinct filenames
        record = _make_record(archive, i)
        archive.ingest({source.name: source}, record, agent="concurrent", now=_NOW)

    errors = _run_concurrently(ingest, count)
    assert not errors, f"concurrent ingest raised: {errors!r}"

    # The shared bytes are stored exactly once, uncorrupted, despite the contention.
    address = ContentAddress(algo=HashAlgo.SHA256, digest=hash_bytes(shared, HashAlgo.SHA256))
    assert archive.store.exists(address)
    assert archive.store.read_bytes(address) == shared
    assert all(report.ok for _name, report in archive.audit_fixity())
