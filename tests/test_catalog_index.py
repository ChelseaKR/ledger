"""Tests for :mod:`ledger.catalog_index` — FIX-04's disclosure-safe catalog cache.

The property under test throughout is the one that matters for safety: the cache
can only ever save work, never change the answer. A ``sync_and_read`` always
returns exactly what a direct scan of ``records/`` would return *right now* — a
changed file is re-read, a deleted file disappears from the result, and an
unchanged file is served from the cache without touching disk again. On top of
that, :class:`~ledger.ingest.Archive` integration tests prove the whole point of
FIX-04: browse/search never re-parses a record that has not changed since the
last read, and a change made through *any* path (the facade, or a direct write to
``records/*.json``, as some lower-level tests and tooling do) is reflected on the
very next read with no explicit invalidation step.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from ledger import catalog_index
from ledger.access.grants import anonymous, steward
from ledger.config import Config
from ledger.ingest import Archive, deserialize_record, serialize_record
from ledger.models import AccessPolicy, DublinCore, Field, Record

pytestmark = pytest.mark.disclosure

_NOW = "2026-06-16T00:00:00Z"


def _record(record_id: str, *, policy: AccessPolicy = AccessPolicy.PUBLIC) -> Record:
    return Record(
        title=f"Record {record_id}",
        record_id=record_id,
        default_policy=policy,
        dublin_core=DublinCore(title=[f"Record {record_id}"], subject=["testing"]),
        fields=[Field(name="body", value="public text", policy=AccessPolicy.PUBLIC)],
        created_at=_NOW,
    )


# --- sync_and_read: the core cache-correctness contract ---------------------


def test_sync_and_read_empty_records_dir_returns_empty(tmp_path: Path) -> None:
    """No ``records/`` directory yet -> no manifests, no crash (fresh archive)."""
    idx = tmp_path / "index" / "catalog.sqlite3"
    assert catalog_index.sync_and_read(idx, tmp_path / "records") == []
    assert not idx.exists(), "reading must not create an index for a nonexistent archive"


def test_sync_and_read_reads_every_manifest(tmp_path: Path) -> None:
    """Every ``records/*.json`` file is returned, verbatim, on a cold cache."""
    records_dir = tmp_path / "records"
    records_dir.mkdir()
    texts = {rid: serialize_record(_record(rid)) for rid in ("a", "b", "c")}
    for rid, text in texts.items():
        (records_dir / f"{rid}.json").write_text(text, encoding="utf-8")

    idx = tmp_path / "index" / "catalog.sqlite3"
    got = catalog_index.sync_and_read(idx, records_dir)
    assert sorted(got) == sorted(texts.values())
    assert idx.exists()


def test_sync_and_read_serves_unchanged_files_from_cache(tmp_path: Path, monkeypatch) -> None:
    """A second read of an unchanged tree never re-opens the source files.

    This is the actual FIX-04 performance property: :func:`Path.read_text` is
    called once per record on the cold read and *zero* times on a warm read of
    the same, unchanged tree.
    """
    records_dir = tmp_path / "records"
    records_dir.mkdir()
    (records_dir / "a.json").write_text(serialize_record(_record("a")), encoding="utf-8")
    (records_dir / "b.json").write_text(serialize_record(_record("b")), encoding="utf-8")
    idx = tmp_path / "index" / "catalog.sqlite3"

    first = catalog_index.sync_and_read(idx, records_dir)
    assert len(first) == 2

    calls = []
    original = Path.read_text

    def counting_read_text(self, *args, **kwargs):
        calls.append(self)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting_read_text)
    second = catalog_index.sync_and_read(idx, records_dir)
    assert sorted(second) == sorted(first)
    assert calls == [], f"warm read touched source files: {calls}"


def test_sync_and_read_reflects_a_changed_file_immediately(tmp_path: Path) -> None:
    """A record rewritten *outside* the Archive facade is picked up on the next read.

    This is the safety-critical case: whatever wrote records/<id>.json last (the
    facade, a moderation tool, a lower-level test), the cache must never keep
    serving the old content once the file has changed underneath it -- a stale
    cache here would mean a tightened consent policy silently fails to take
    effect (a no-outing regression).
    """
    records_dir = tmp_path / "records"
    records_dir.mkdir()
    path = records_dir / "a.json"
    path.write_text(serialize_record(_record("a", policy=AccessPolicy.PUBLIC)), encoding="utf-8")
    idx = tmp_path / "index" / "catalog.sqlite3"

    first = catalog_index.sync_and_read(idx, records_dir)
    (first_record,) = (deserialize_record(t) for t in first)
    assert first_record.default_policy is AccessPolicy.PUBLIC

    tightened = serialize_record(_record("a", policy=AccessPolicy.STEWARDS))
    path.write_text(tightened, encoding="utf-8")

    second = catalog_index.sync_and_read(idx, records_dir)
    (second_record,) = (deserialize_record(t) for t in second)
    assert second_record.default_policy is AccessPolicy.STEWARDS


def test_sync_and_read_drops_a_deleted_file(tmp_path: Path) -> None:
    """A record removed from disk (e.g. a takedown) stops being returned or cached."""
    records_dir = tmp_path / "records"
    records_dir.mkdir()
    path = records_dir / "a.json"
    path.write_text(serialize_record(_record("a")), encoding="utf-8")
    idx = tmp_path / "index" / "catalog.sqlite3"

    assert len(catalog_index.sync_and_read(idx, records_dir)) == 1

    path.unlink()
    remaining = catalog_index.sync_and_read(idx, records_dir)
    assert remaining == []

    conn = sqlite3.connect(idx)
    try:
        (count,) = conn.execute("SELECT COUNT(*) FROM records").fetchone()
    finally:
        conn.close()
    assert count == 0, "a deleted record must not linger as a cached row either"


def test_sync_detects_same_size_rewrite_with_preserved_mtime(tmp_path: Path) -> None:
    """A restored timestamp cannot make changed policy bytes look cache-current."""
    records_dir = tmp_path / "records"
    records_dir.mkdir()
    path = records_dir / "a.json"
    original = serialize_record(_record("a", policy=AccessPolicy.PUBLIC))
    changed = original.replace('"default_policy":"public"', '"default_policy":"sealed"')
    assert len(changed) == len(original)
    path.write_text(original, encoding="utf-8")
    idx = tmp_path / "index" / "catalog.sqlite3"
    catalog_index.sync_and_read(idx, records_dir)
    original_stat = path.stat()

    path.write_text(changed, encoding="utf-8")
    os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

    (refreshed,) = catalog_index.sync_and_read(idx, records_dir)
    assert deserialize_record(refreshed).default_policy is AccessPolicy.SEALED


def test_version_history_index_is_not_a_catalog_record(tmp_path: Path) -> None:
    """The adjacent ``*.versions.json`` file never enters sqlite or reindex counts."""
    records_dir = tmp_path / "records"
    records_dir.mkdir()
    manifest = serialize_record(_record("a"))
    (records_dir / "a.json").write_text(manifest, encoding="utf-8")
    (records_dir / "a.versions.json").write_text("[]", encoding="utf-8")
    idx = tmp_path / "index" / "catalog.sqlite3"

    assert catalog_index.rebuild(idx, records_dir) == 1
    assert catalog_index.sync_and_read(idx, records_dir) == [manifest]


# --- rebuild: the explicit, deterministic path -------------------------------


def test_rebuild_matches_sync_and_read_content(tmp_path: Path) -> None:
    """``rebuild`` yields the same content a cold ``sync_and_read`` would."""
    records_dir = tmp_path / "records"
    records_dir.mkdir()
    texts = {rid: serialize_record(_record(rid)) for rid in ("a", "b")}
    for rid, text in texts.items():
        (records_dir / f"{rid}.json").write_text(text, encoding="utf-8")

    idx = tmp_path / "index" / "catalog.sqlite3"
    count = catalog_index.rebuild(idx, records_dir)
    assert count == 2

    conn = sqlite3.connect(idx)
    try:
        rows = conn.execute("SELECT manifest FROM records").fetchall()
    finally:
        conn.close()
    assert sorted(r[0] for r in rows) == sorted(texts.values())


def test_rm_index_then_rebuild_is_content_identical(tmp_path: Path) -> None:
    """``rm -rf store/index`` followed by a rebuild reproduces the same content."""
    records_dir = tmp_path / "records"
    records_dir.mkdir()
    for rid in ("a", "b", "c"):
        (records_dir / f"{rid}.json").write_text(serialize_record(_record(rid)), encoding="utf-8")

    idx = tmp_path / "index" / "catalog.sqlite3"
    catalog_index.rebuild(idx, records_dir)
    conn = sqlite3.connect(idx)
    try:
        before = sorted(conn.execute("SELECT record_id, manifest FROM records").fetchall())
    finally:
        conn.close()

    idx.unlink()
    idx.parent.rmdir()
    catalog_index.rebuild(idx, records_dir)
    conn = sqlite3.connect(idx)
    try:
        after = sorted(conn.execute("SELECT record_id, manifest FROM records").fetchall())
    finally:
        conn.close()

    assert before == after


def test_rebuild_leaves_no_partial_file_on_a_reader(tmp_path: Path) -> None:
    """``rebuild`` never leaves a stray temp file, and it swaps the index atomically."""
    records_dir = tmp_path / "records"
    records_dir.mkdir()
    (records_dir / "a.json").write_text(serialize_record(_record("a")), encoding="utf-8")
    idx = tmp_path / "index" / "catalog.sqlite3"

    catalog_index.rebuild(idx, records_dir)
    assert idx.exists()
    leftovers = list(idx.parent.glob("*.rebuild.tmp"))
    assert leftovers == [], f"rebuild left a temp file behind: {leftovers}"


# --- Archive integration: the actual FIX-04 read path ------------------------


def test_archive_browse_reflects_a_direct_records_file_edit(tmp_path: Path) -> None:
    """browse() reflects a change even when records/<id>.json is edited directly.

    Regression test for the failure mode the naive "index updated only by
    ingest/apply_update" design had: any writer other than the Archive facade
    (moderation tooling, a lower-level test, a restored backup) must still be
    picked up by the very next browse -- never served a stale, more-permissive
    view (no-outing rule).
    """
    config = Config.default("Index Archive", tmp_path / "arc")
    archive = Archive.init(config)
    record = _record("direct-edit", policy=AccessPolicy.PUBLIC)
    archive.ingest({}, record, now=_NOW)

    before = archive.browse(anonymous(), now=_NOW)
    assert any(r.record_id == "direct-edit" for r in before)

    tightened = _record("direct-edit", policy=AccessPolicy.STEWARDS)
    manifest = serialize_record(tightened)
    (archive.records_dir / "direct-edit.json").write_text(manifest, encoding="utf-8")

    after = archive.browse(anonymous(), now=_NOW)
    assert not any(r.record_id == "direct-edit" for r in after)
    after_steward = archive.browse(steward("s"), now=_NOW)
    assert any(r.record_id == "direct-edit" for r in after_steward)


def test_archive_reindex_is_idempotent_and_counts_records(tmp_path: Path) -> None:
    """``Archive.reindex()`` rebuilds from scratch and reports how many it indexed."""
    config = Config.default("Index Archive", tmp_path / "arc")
    archive = Archive.init(config)
    for rid in ("r1", "r2", "r3"):
        archive.ingest({}, _record(rid), now=_NOW)

    assert archive.reindex() == 3
    # A second reindex (nothing changed) reports the same count.
    assert archive.reindex() == 3
    # And the archive still browses correctly afterward.
    listed = {r.record_id for r in archive.browse(anonymous(), now=_NOW)}
    assert listed == {"r1", "r2", "r3"}


def test_archive_takedown_removes_record_from_next_browse(tmp_path: Path) -> None:
    """remove_all_copies() -> the record is gone from the very next browse.

    No explicit index-invalidation call is needed: the cache notices the
    fast-lookup file vanished the next time anything reads it.
    """
    config = Config.default("Index Archive", tmp_path / "arc")
    archive = Archive.init(config)
    archive.ingest({}, _record("gone"), now=_NOW)
    assert any(r.record_id == "gone" for r in archive.browse(anonymous(), now=_NOW))

    archive.remove_all_copies("gone")
    assert not any(r.record_id == "gone" for r in archive.browse(anonymous(), now=_NOW))
