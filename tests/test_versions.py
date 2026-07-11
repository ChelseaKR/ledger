"""Tests for the living-document version history over the CAS.

Every post-ingest change snapshots the manifest it supersedes into the content store
and notes it in an append-only per-record version index. These tests pin that the
snapshot is taken (and only on a real change of an existing manifest), that the index
is ordered oldest-first, that a snapshot round-trips back to the exact prior record,
and — critically — that a snapshot carries no contributor identity (no-outing rule):
each snapshot is the already-identity-free manifest, so the history can never out a
contributor even though the record was ingested with a sealed identity.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from ledger.config import Config
from ledger.errors import ObjectNotFound
from ledger.identity import ContributorIdentity
from ledger.ingest import Archive, deserialize_record
from ledger.models import (
    AccessPolicy,
    ContentAddress,
    DublinCore,
    PremisEvent,
    PremisEventType,
    Record,
)

_VAULT_KEY = "0123456789abcdef0123456789abcdef0123456789a="
_IDENTITY_SENTINEL = "Jordan-Rivera-DO-NOT-LEAK-9Q2"
_NOW = "2026-06-20T00:00:00Z"


def _event(record_id: str, event_type: PremisEventType, when: str) -> PremisEvent:
    return PremisEvent(
        event_type=event_type,
        agent="steward-1",
        outcome="success",
        detail="test change",
        linked_object=record_id,
        event_datetime=when,
    )


def _archive_with_record(tmp_path: Path, *, with_identity: bool = False) -> tuple[Archive, str]:
    archive = Archive.init(Config.default("Versions Archive", tmp_path / "arc"))
    payload = tmp_path / "f.txt"
    payload.write_text("first take\n", encoding="utf-8")
    record = Record(
        title="Original title",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(title=["Original title"], description=["the first account"]),
    )
    identity = ContributorIdentity(name=_IDENTITY_SENTINEL) if with_identity else None
    archive.ingest(
        {"f.txt": payload},
        record,
        identity=identity,
        vault_key=_VAULT_KEY.encode("ascii") if with_identity else None,
        agent="t",
        now=_NOW,
    )
    return archive, record.record_id


def test_no_versions_before_any_update(tmp_path: Path) -> None:
    """A freshly ingested record has no prior versions yet."""
    archive, rid = _archive_with_record(tmp_path)
    assert archive.record_versions(rid) == []


def test_apply_update_snapshots_the_superseded_manifest(tmp_path: Path) -> None:
    """The first update snapshots the as-ingested manifest into the version index."""
    archive, rid = _archive_with_record(tmp_path)
    before = archive.get(rid)

    updated = replace(before, content_warnings=["outing"])
    archive.apply_update(updated, _event(rid, PremisEventType.MODERATION, _NOW))

    versions = archive.record_versions(rid)
    assert len(versions) == 1
    entry = versions[0]
    assert set(entry.keys()) == {"address", "saved_at", "event_type"}
    assert entry["event_type"] == PremisEventType.MODERATION.value
    # The snapshot is the record as it was BEFORE the update.
    assert archive.get_version(rid, entry["address"]).content_warnings == []


def test_record_versions_are_ordered_oldest_first(tmp_path: Path) -> None:
    """Successive updates append in order; the index reads oldest-first."""
    archive, rid = _archive_with_record(tmp_path)

    step1 = replace(archive.get(rid), title="Second title")
    archive.apply_update(step1, _event(rid, PremisEventType.MODERATION, "2026-06-21T00:00:00Z"))
    step2 = replace(archive.get(rid), title="Third title")
    archive.apply_update(step2, _event(rid, PremisEventType.CONSENT_CHANGE, "2026-06-22T00:00:00Z"))

    versions = archive.record_versions(rid)
    assert [v["event_type"] for v in versions] == [
        PremisEventType.MODERATION.value,
        PremisEventType.CONSENT_CHANGE.value,
    ]
    # Oldest snapshot is the original title; newest prior is the second title.
    assert archive.get_version(rid, versions[0]["address"]).title == "Original title"
    assert archive.get_version(rid, versions[1]["address"]).title == "Second title"
    # The current manifest reflects the last write.
    assert archive.get(rid).title == "Third title"


def test_get_version_round_trips_exactly(tmp_path: Path) -> None:
    """A snapshot deserializes back to the byte-identical prior record."""
    archive, rid = _archive_with_record(tmp_path)
    original = archive.get(rid)

    archive.apply_update(
        replace(original, title="Changed"),
        _event(rid, PremisEventType.MODERATION, _NOW),
    )
    address = archive.record_versions(rid)[0]["address"]
    snapshot = archive.get_version(rid, address)

    from ledger.ingest import serialize_record

    assert serialize_record(snapshot) == serialize_record(original)
    assert snapshot == deserialize_record(serialize_record(original))


def test_get_version_rejects_an_unknown_address(tmp_path: Path) -> None:
    """An address not in the record's index is refused (no arbitrary CAS reads)."""
    archive, rid = _archive_with_record(tmp_path)
    archive.apply_update(
        replace(archive.get(rid), title="Changed"),
        _event(rid, PremisEventType.MODERATION, _NOW),
    )
    with pytest.raises(ObjectNotFound):
        archive.get_version(rid, "sha256:" + "0" * 64)


def test_snapshots_carry_no_contributor_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every snapshot is identity-free even when the record has a sealed identity."""
    monkeypatch.setenv("LEDGER_VAULT_KEY", _VAULT_KEY)
    archive, rid = _archive_with_record(tmp_path, with_identity=True)
    # The manifest carries only the opaque ref, not the name — confirm before snapshot.
    assert archive.get(rid).identity_ref is not None

    archive.apply_update(
        replace(archive.get(rid), title="Renamed"),
        _event(rid, PremisEventType.MODERATION, _NOW),
    )
    for entry in archive.record_versions(rid):
        raw = archive.store.read_bytes(ContentAddress.parse(entry["address"]))
        assert _IDENTITY_SENTINEL not in raw.decode("utf-8")
        # The snapshot deserializes to a record whose identity is only an opaque token.
        snap = archive.get_version(rid, entry["address"])
        assert _IDENTITY_SENTINEL not in (snap.identity_ref or "")


def test_version_index_is_append_only_canonical(tmp_path: Path) -> None:
    """The on-disk index is canonical JSON and only ever grows."""
    import json

    archive, rid = _archive_with_record(tmp_path)
    archive.apply_update(
        replace(archive.get(rid), title="One"),
        _event(rid, PremisEventType.MODERATION, _NOW),
    )
    path = archive.records_dir / f"{rid}.versions.json"
    first = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(first, list) and len(first) == 1

    archive.apply_update(
        replace(archive.get(rid), title="Two"),
        _event(rid, PremisEventType.MODERATION, _NOW),
    )
    second = json.loads(path.read_text(encoding="utf-8"))
    # Append-only: the earlier entry is preserved unchanged and a new one is added.
    assert second[0] == first[0]
    assert len(second) == 2
