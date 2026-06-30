"""Tests for ``Archive.remove_all_copies`` — the shared destructive removal effect.

Security review: this primitive builds the directory paths it deletes from its
``record_id`` argument, so it must refuse any id that is not a single safe path
component, or a crafted id could turn a removal into a traversal that deletes outside
the archive. A real id is opaque hex, so the guard only ever rejects an attack.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ledger.config import Config
from ledger.errors import LedgerError
from ledger.ingest import Archive

_NOW = "2026-06-20T00:00:00Z"


def _archive_with_record(tmp_path: Path) -> tuple[Archive, str]:
    from ledger.models import AccessPolicy, DublinCore, Record

    archive = Archive.init(Config.default("Remove Archive", tmp_path / "arc"))
    payload = tmp_path / "f.txt"
    payload.write_text("keep then remove\n", encoding="utf-8")
    record = Record(
        title="A record",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(title=["A record"]),
    )
    archive.ingest({"f.txt": payload}, record, agent="t", now=_NOW)
    return archive, record.record_id


def test_remove_all_copies_deletes_a_real_record(tmp_path: Path) -> None:
    archive, rid = _archive_with_record(tmp_path)
    assert (archive.bags_dir / rid).exists()
    removed, _revoked = archive.remove_all_copies(rid)
    assert removed >= 1
    assert not (archive.bags_dir / rid).exists()
    assert not archive.records_dir.joinpath(f"{rid}.json").exists()


@pytest.mark.parametrize(
    "bad_id",
    ["..", ".", "", "../escape", "a/b", "a\\b", "x\x00y"],
)
def test_remove_all_copies_refuses_unsafe_ids(tmp_path: Path, bad_id: str) -> None:
    """A non-component id is refused before any deletion, so it cannot traverse."""
    archive, rid = _archive_with_record(tmp_path)
    sentinel = tmp_path / "outside"
    sentinel.mkdir()
    (sentinel / "keep.txt").write_text("must survive\n", encoding="utf-8")

    with pytest.raises(LedgerError):
        archive.remove_all_copies(bad_id)

    # Nothing real was touched: the legitimate record and an outside dir both survive.
    assert (archive.bags_dir / rid).exists()
    assert (sentinel / "keep.txt").exists()
