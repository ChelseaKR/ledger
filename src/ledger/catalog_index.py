"""A disclosure-safe sqlite catalog index — FIX-04 (docs/ideation/02-large-scale-fixes.md).

Every browse, search, OAI, sitemap, and CSV request routes through
:meth:`~ledger.ingest.Archive._all_records`, which used to glob and JSON-parse
*every* ``records/*.json`` manifest on *each* request. At a few thousand records
that is seconds of CPU per page, and the growing latency is itself a timing signal
(the same concern RM3's access-timing analysis raises, from the other side).

This module is a stdlib-only (``sqlite3`` — no new dependency) cache of the exact
same identity-free manifests already written to ``records/`` by
:func:`ledger.ingest.serialize_record`. Two properties make it safe:

* **Never a second source of truth.** ``records/`` on disk is always authoritative.
  :func:`sync_and_read` validates every cached row against the *current* mtime and
  size of its source file before trusting it, re-reading and re-caching anything
  that changed and dropping anything that vanished. So the cache can never serve
  content that is stale relative to disk — no matter *how* ``records/`` was last
  written (through :meth:`~ledger.ingest.Archive.ingest`, ``apply_update``, a
  steward's redaction tool, or, in a test, a direct file write). A record whose
  consent was just tightened to STEWARDS-only can never keep appearing to an
  anonymous browse because a cache entry lagged behind (safety, no-outing rule).
* **Never a second disclosure surface.** It stores nothing that ``records/*.json``
  does not already hold verbatim — a record manifest already carries no in-memory
  identity (``serialize_record`` refuses to emit one), only the opaque
  ``identity_ref`` token, exactly like the on-disk fast-lookup copy every read path
  already trusts. :meth:`~ledger.ingest.Archive.disclose` remains the *only* gate a
  viewer's grant passes through; this module only makes assembling the *candidate*
  record list fast, feeding ``is_listable``/``disclose`` the same inputs they
  always got from a direct scan.

Because every read re-validates file metadata, an *unchanged* archive costs one
``stat`` per record instead of one full file read *and* JSON parse — the parse is
what actually costs "seconds of CPU" at a few thousand records, and it is now paid
only for records that changed since the last read. ``ledger reindex``
(:func:`rebuild`) forces a from-scratch pass (ignoring any existing cache) and
atomically swaps in the result via :func:`Path.replace`, so a concurrent reader
never observes a half-written index and a crash mid-rebuild leaves the previous
index intact (integrity, resilience). ``rm -rf store/index`` followed by
``reindex`` always reproduces the same cached content, because every cached
manifest is copied verbatim from its source file rather than re-derived
(reproducibility).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

__all__ = [
    "INDEX_DIRNAME",
    "INDEX_FILENAME",
    "index_path",
    "rebuild",
    "sync_and_read",
]

INDEX_DIRNAME = "index"
INDEX_FILENAME = "catalog.sqlite3"

# Milliseconds sqlite will wait for a writer lock before raising "database is
# locked" -- generous enough that two concurrent syncs (e.g. two browse requests
# under the threaded server) never hard-fail on one modest box (operability).
_BUSY_TIMEOUT_MS = 5000
_VERSIONS_SUFFIX = ".versions.json"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    record_id TEXT PRIMARY KEY,
    manifest TEXT NOT NULL,
    mtime_ns INTEGER NOT NULL,
    ctime_ns INTEGER NOT NULL,
    size INTEGER NOT NULL
);
"""


def index_path(store_root: Path) -> Path:
    """The one canonical index location for an archive rooted at ``store_root``."""
    return Path(store_root) / INDEX_DIRNAME / INDEX_FILENAME


def _connect(path: Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=_BUSY_TIMEOUT_MS / 1000)
    conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
    conn.executescript(_SCHEMA)
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(records)")}
    if "ctime_ns" not in columns:
        # In-place migration for indexes created by the first FIX-04 draft. A
        # zero value deliberately forces every row to refresh on its next sync.
        conn.execute("ALTER TABLE records ADD COLUMN ctime_ns INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    return conn


def sync_and_read(path: Path, records_dir: Path) -> list[str]:
    """Return every current record manifest's text, syncing the cache against disk.

    ``records_dir`` (``records/``) is walked once with ``stat`` per file — cheap
    compared to reading and JSON-parsing every manifest. A file whose ``(mtime_ns,
    size)`` matches its cached row is served straight from the cache with no read
    at all; anything new, changed, or previously uncached is read fresh (and the
    cache updated); anything cached but no longer on disk is dropped from the
    cache and the result. The set returned is therefore always exactly "what is on
    disk right now" — the cache can only ever save work, never change the answer
    (see the module docstring on why that is the safety-critical property here).

    Returns ``[]`` if ``records_dir`` does not exist yet (a brand-new archive).
    """
    records_dir = Path(records_dir)
    if not records_dir.exists():
        return []

    on_disk: dict[str, tuple[Path, int, int, int]] = {}
    for file in records_dir.glob("*.json"):
        # Version-history indexes share this directory but are not manifests and
        # must not inflate reindex counts or enter the browse candidate cache.
        if file.name.endswith(_VERSIONS_SUFFIX):
            continue
        try:
            st = file.stat()
        except OSError:
            continue
        on_disk[file.stem] = (file, st.st_mtime_ns, st.st_ctime_ns, st.st_size)

    conn = _connect(Path(path))
    try:
        cached = {
            row[0]: (row[1], row[2], row[3])
            for row in conn.execute(
                "SELECT record_id, manifest, mtime_ns, ctime_ns, size FROM records"
            )
        }
        texts: list[str] = []
        upserts: list[tuple[str, str, int, int, int]] = []
        for record_id, (file, mtime_ns, ctime_ns, size) in on_disk.items():
            hit = cached.get(record_id)
            if (
                hit is not None
                and hit[1] == mtime_ns
                and hit[2] == ctime_ns
                and hit[3] == size
            ):
                texts.append(hit[0])
                continue
            try:
                text = file.read_text(encoding="utf-8")
            except OSError:
                # A file that vanished or became unreadable between the stat and
                # the read is simply omitted -- the caller's own manifest parsing
                # already tolerates a missing/bad record (degradability).
                continue
            texts.append(text)
            upserts.append((record_id, text, mtime_ns, ctime_ns, size))
        stale_ids = [record_id for record_id in cached if record_id not in on_disk]

        if upserts or stale_ids:
            with conn:
                conn.executemany(
                    "INSERT INTO records (record_id, manifest, mtime_ns, ctime_ns, size) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(record_id) DO UPDATE SET "
                    "manifest = excluded.manifest, "
                    "mtime_ns = excluded.mtime_ns, "
                    "ctime_ns = excluded.ctime_ns, "
                    "size = excluded.size",
                    upserts,
                )
                conn.executemany(
                    "DELETE FROM records WHERE record_id = ?",
                    [(record_id,) for record_id in stale_ids],
                )
    finally:
        conn.close()
    return texts


def rebuild(path: Path, records_dir: Path) -> int:
    """Force a from-scratch rebuild of the index at ``path`` from ``records_dir``.

    FIX-04's explicit, deterministic rebuild path (``ledger reindex``): every
    manifest is re-read and re-parsed from disk (an empty temp index means
    :func:`sync_and_read` has nothing to trust as cached), then the result is
    atomically swapped into ``path`` with :func:`Path.replace` so a concurrent
    reader never sees a half-built index. Returns the number of records indexed.
    """
    path = Path(path)
    tmp = path.with_name(path.name + ".rebuild.tmp")
    if tmp.exists():
        tmp.unlink()
    count = len(sync_and_read(tmp, records_dir))
    tmp.replace(path)
    return count
