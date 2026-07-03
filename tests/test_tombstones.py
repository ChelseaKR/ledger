"""Tests for durable takedown tombstones and reattach propagation (FIX-08).

A steward can take a record down while one mirror is offline. The removal cannot be
pushed there and then, so a tombstone remembers it: when the mirror reattaches, the
replication sweep deletes the stale copy it still holds, writes a per-location PREMIS
``TAKEDOWN`` receipt, and marks that location confirmed — and healing must never
re-copy a taken-down bag back. These tests pin that end-to-end "excellent" scenario
plus the store's unit behaviour and the honest, never-overstated ``/consent-status``
rendering.

No-outing: the fixtures carry only collection-level Dublin Core and opaque ids, so a
tombstone, a receipt, and the status page are all checked to name nothing but
location names and opaque record ids.
"""

from __future__ import annotations

import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import pytest

from ledger.config import Config, StorageLocation
from ledger.ingest import Archive
from ledger.metadata.premis import PremisLog
from ledger.models import AccessPolicy, DublinCore, PremisEventType, Record
from ledger.replicate import heal, replicate_bag, verify_replicas
from ledger.server import make_server
from ledger.tombstones import PRIMARY_LOCATION, TombstoneStore

_NOW = "2026-07-01T00:00:00Z"
_LATER = "2026-07-02T00:00:00Z"
_AGENT = "ledger.test"


# --- unit: the store ---------------------------------------------------------


def test_add_is_idempotent_and_preserves_confirmations(tmp_path: Path) -> None:
    store = TombstoneStore(tmp_path)
    store.add("rec-1", _NOW)
    store.confirm("rec-1", "mirror-a", _NOW)
    # A repeated takedown of the same id must not drop the receipt already collected.
    store.add("rec-1", _LATER)
    assert len(store.all()) == 1
    tomb = store.get("rec-1")
    assert tomb is not None
    assert tomb.issued_at == _NOW  # original issue time preserved
    assert tomb.confirmed == {"mirror-a": _NOW}


def test_pending_for_reflects_confirmations(tmp_path: Path) -> None:
    store = TombstoneStore(tmp_path)
    store.add("rec-1", _NOW)
    store.add("rec-2", _NOW)
    assert set(store.pending_for("mirror-a")) == {"rec-1", "rec-2"}
    store.confirm("rec-1", "mirror-a", _NOW)
    assert store.pending_for("mirror-a") == ["rec-2"]
    # Confirming for one location does not confirm for another.
    assert set(store.pending_for("mirror-b")) == {"rec-1", "rec-2"}


def test_confirm_is_first_writer_wins(tmp_path: Path) -> None:
    store = TombstoneStore(tmp_path)
    store.add("rec-1", _NOW)
    store.confirm("rec-1", "mirror-a", _NOW)
    store.confirm("rec-1", "mirror-a", _LATER)  # must not overwrite the receipt time
    assert store.status("rec-1") == {"mirror-a": _NOW}


def test_confirm_unknown_id_raises(tmp_path: Path) -> None:
    from ledger.errors import LedgerError

    store = TombstoneStore(tmp_path)
    with pytest.raises(LedgerError):
        store.confirm("nope", "mirror-a", _NOW)


def test_status_is_none_for_untombstoned_id(tmp_path: Path) -> None:
    assert TombstoneStore(tmp_path).status("never") is None


# --- integration: ingest -> replicate -> offline takedown -> reattach heal ----


def _archive_with_three_mirrors(tmp_path: Path) -> tuple[Archive, list[StorageLocation]]:
    """A fresh archive whose config points at three empty mirror directories."""
    config = Config.default("Tombstone Archive", tmp_path / "arc")
    mirrors = [
        StorageLocation(name=name, path=str(tmp_path / name), kind="mirror")
        for name in ("mirror-a", "mirror-b", "mirror-c")
    ]
    config.locations = mirrors
    archive = Archive.init(config)
    return archive, mirrors


def _ingest_and_replicate(archive: Archive, mirrors: list[StorageLocation], tmp_path: Path) -> str:
    payload = tmp_path / "story.txt"
    payload.write_text("a community keeps its own history\n", encoding="utf-8")
    record = Record(
        title="A public record",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(title=["A public record"]),
    )
    archive.ingest({"story.txt": payload}, record, agent=_AGENT, now=_NOW)
    rid = record.record_id
    for loc in mirrors:
        replicate_bag(archive.bags_dir / rid, loc, agent=_AGENT, now=_NOW)
    return rid


def test_offline_replica_gets_takedown_on_reattach(tmp_path: Path) -> None:
    """The full scenario: one mirror offline at takedown, healed on reattach."""
    archive, mirrors = _archive_with_three_mirrors(tmp_path)
    rid = _ingest_and_replicate(archive, mirrors, tmp_path)
    offline = mirrors[2]  # mirror-c

    # Every replica exists before the takedown.
    for loc in mirrors:
        assert (Path(loc.path) / rid).exists()

    # Take mirror-c offline by renaming its directory, then take the record down.
    parked = Path(str(tmp_path / "mirror-c-parked"))
    Path(offline.path).rename(parked)
    archive.remove_all_copies(rid, now=_NOW)

    # Tombstone written: the two reachable mirrors + the primary are confirmed; the
    # offline mirror is still pending (its copy could not be reached).
    store = TombstoneStore(archive.logs_dir)
    status = store.status(rid)
    assert status is not None
    confirmed_mirrors = {name for name in status if name != PRIMARY_LOCATION}
    assert confirmed_mirrors == {"mirror-a", "mirror-b"}  # 2 confirmed
    assert PRIMARY_LOCATION in status
    assert rid in store.pending_for("mirror-c")

    # The reachable mirrors' copies are already gone; the offline one still holds it.
    assert not (Path(mirrors[0].path) / rid).exists()
    assert not (Path(mirrors[1].path) / rid).exists()
    assert (parked / rid).exists()

    # Reattach the mirror and run the healing sweep with the tombstone store.
    parked.rename(Path(offline.path))
    events = heal(rid, mirrors, agent=_AGENT, now=_LATER, tombstones=store)

    # The stale copy is gone and heal never recreated the tombstoned bag anywhere.
    for loc in mirrors:
        assert not (Path(loc.path) / rid).exists()
    assert all(e.event_type is not PremisEventType.REPLICATION for e in events)

    # The tombstone is now fully confirmed, including the once-offline mirror.
    fresh = TombstoneStore(archive.logs_dir)
    assert not fresh.pending_for("mirror-c")
    assert set(fresh.status(rid) or {}) >= {PRIMARY_LOCATION, "mirror-a", "mirror-b", "mirror-c"}

    # A per-location TAKEDOWN receipt for the once-offline mirror exists in the log,
    # naming only the opaque id and the location — never the title.
    log = PremisLog.read(archive.logs_dir / "takedowns.premis.json")
    takedowns = [e for e in log.events if e.event_type is PremisEventType.TAKEDOWN]
    receipts_for_c = [e for e in takedowns if e.linked_object == rid and "mirror-c" in e.detail]
    assert receipts_for_c, "expected a TAKEDOWN receipt for mirror-c"
    for event in takedowns:
        assert "A public record" not in event.detail  # no content leaks into a receipt


def test_verify_replicas_applies_pending_tombstone(tmp_path: Path) -> None:
    """verify_replicas with a store removes a tombstoned copy before reporting."""
    archive, mirrors = _archive_with_three_mirrors(tmp_path)
    rid = _ingest_and_replicate(archive, mirrors, tmp_path)
    offline = mirrors[2]
    parked = Path(str(tmp_path / "mirror-c-parked"))
    Path(offline.path).rename(parked)
    archive.remove_all_copies(rid, now=_NOW)
    parked.rename(Path(offline.path))

    store = TombstoneStore(archive.logs_dir)
    statuses = verify_replicas(rid, mirrors, tombstones=store, agent=_AGENT, now=_LATER)
    # Every replica now reports gone (ok=False, missing) — honest for a taken-down bag.
    assert all(not s.ok for s in statuses)
    assert not (Path(offline.path) / rid).exists()
    assert not store.pending_for("mirror-c")


# --- integration: /consent-status never overstates completion ----------------


@pytest.fixture
def server(tmp_path: Path) -> Iterator[tuple[Archive, list[StorageLocation], str]]:
    archive, mirrors = _archive_with_three_mirrors(tmp_path)
    rid = _ingest_and_replicate(archive, mirrors, tmp_path)
    httpd = make_server(archive, host="127.0.0.1", port=0)
    base = f"http://127.0.0.1:{int(httpd.server_address[1])}"
    sink = StringIO()
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    with redirect_stderr(sink), redirect_stdout(sink):
        thread.start()
        try:
            yield archive, mirrors, f"{base}::{rid}"
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()


def _get(url: str) -> tuple[int, str]:
    req = urllib.request.Request(url)  # noqa: S310 - loopback
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            return int(resp.status), resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8")


def test_consent_status_reports_pending_then_complete(
    server: tuple[Archive, list[StorageLocation], str],
) -> None:
    """The status page shows honest per-location progress and never overstates it."""
    archive, mirrors, packed = server
    base, rid = packed.split("::")

    # File a withdraw request so /consent-status has a reference to look up.
    from ledger.consent import ConsentRequest, ConsentRequestStore

    cstore = ConsentRequestStore(archive.logs_dir / "consent-requests.json")
    req = ConsentRequest(record_id=rid, kind="withdraw", message="please remove")
    cstore.add(req)
    ref = req.request_id

    # Take the record down with mirror-c offline: two of three mirrors confirm.
    offline = mirrors[2]
    parked = Path(offline.path).parent / "mirror-c-parked"
    Path(offline.path).rename(parked)
    archive.remove_all_copies(rid, now=_NOW)

    status, body = _get(f"{base}/consent-status?ref={ref}")
    assert status == 200
    # Honest: 3 of 4 confirmed (primary + 2 mirrors), and mirror-c still pending.
    assert "3 of 4" in body
    assert "mirror-c" in body
    assert "confirmed at every storage location" not in body
    assert "please remove" not in body  # never echo the private message

    # Reattach and heal: now every location is confirmed and the page says so.
    parked.rename(Path(offline.path))
    store = TombstoneStore(archive.logs_dir)
    heal(rid, mirrors, agent=_AGENT, now=_LATER, tombstones=store)

    status, body = _get(f"{base}/consent-status?ref={ref}")
    assert "confirmed at every storage location" in body
    assert "please remove" not in body
