"""Tests for the sneakernet courier-package builder (``ledger.export_drive``, EXP-08).

Covers the property that matters most: a package is built from the disclosure
boundary, not a raw bag copy, so a viewer never receives more on a USB drive than
that same viewer could already see live.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ledger.access.grants import anonymous, community_member
from ledger.accessibility_check import check_html
from ledger.bag import validate_bag
from ledger.config import Config
from ledger.export_drive import build_export_drive
from ledger.ingest import Archive
from ledger.models import AccessPolicy, DublinCore, Field, Record

_NOW = "2026-06-16T12:00:00Z"


def _archive(tmp_path: Path) -> Archive:
    config = Config.default("Drive Test Archive", tmp_path / "arc")
    return Archive.init(config)


def _ingest_public_with_file(archive: Archive, tmp_path: Path) -> str:
    payload = tmp_path / "story.txt"
    payload.write_text("a public account", encoding="utf-8")
    record = Record(
        title="Public story",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(title=["Public story"]),
        fields=[Field(name="story", value="told in public", policy=AccessPolicy.PUBLIC)],
        content_warnings=["outing"],
    )
    archive.ingest({"story.txt": payload}, record, agent="test", now=_NOW)
    return record.record_id


def _ingest_community_only(archive: Archive) -> str:
    record = Record(
        title="Community-only note",
        default_policy=AccessPolicy.COMMUNITY,
        dublin_core=DublinCore(title=["Community-only note"]),
        fields=[Field(name="note", value="members only", policy=AccessPolicy.COMMUNITY)],
    )
    archive.ingest({}, record, agent="test", now=_NOW)
    return record.record_id


def test_export_drive_is_disclosure_filtered_not_a_raw_copy(tmp_path: Path) -> None:
    """A default (anonymous) courier package excludes a COMMUNITY-only record."""
    archive = _archive(tmp_path)
    public_id = _ingest_public_with_file(archive, tmp_path)
    community_id = _ingest_community_only(archive)

    out = tmp_path / "drive"
    result = build_export_drive(archive, out, grant=anonymous(), now=_NOW)

    assert result.records_packaged == 1
    assert result.all_bags_valid
    assert (out / "bags" / public_id).is_dir()
    assert not (out / "bags" / community_id).exists()

    index = (out / "index.html").read_text(encoding="utf-8")
    assert "Public story" in index
    assert "Community-only note" not in index


def test_export_drive_widens_with_a_broader_grant(tmp_path: Path) -> None:
    """Choosing a community-member viewer includes the COMMUNITY-only record too."""
    archive = _archive(tmp_path)
    _ingest_public_with_file(archive, tmp_path)
    community_id = _ingest_community_only(archive)

    out = tmp_path / "drive"
    result = build_export_drive(archive, out, grant=community_member("member-1"), now=_NOW)

    assert result.records_packaged == 2
    assert (out / "bags" / community_id).is_dir()


def test_export_drive_bags_validate_with_bag_validate_bag(tmp_path: Path) -> None:
    """Every freshly written bag independently re-validates (RFC 8493 fixity)."""
    archive = _archive(tmp_path)
    record_id = _ingest_public_with_file(archive, tmp_path)

    out = tmp_path / "drive"
    build_export_drive(archive, out, grant=anonymous(), now=_NOW)

    report = validate_bag(out / "bags" / record_id)
    assert report.ok
    assert report.checked > 0


def test_export_drive_checksums_cover_every_file_and_verify(tmp_path: Path) -> None:
    """``CHECKSUMS.sha256`` lists every file on the drive and every hash matches."""
    import hashlib

    archive = _archive(tmp_path)
    _ingest_public_with_file(archive, tmp_path)

    out = tmp_path / "drive"
    build_export_drive(archive, out, grant=anonymous(), now=_NOW)

    checksums = (out / "CHECKSUMS.sha256").read_text(encoding="utf-8").splitlines()
    assert checksums, "checksum file must not be empty"
    on_disk = {
        p.relative_to(out).as_posix()
        for p in out.rglob("*")
        if p.is_file() and p.name != "CHECKSUMS.sha256"
    }
    listed = set()
    for line in checksums:
        digest, _, relpath = line.partition("  ")
        listed.add(relpath)
        actual = hashlib.sha256((out / relpath).read_bytes()).hexdigest()
        assert actual == digest, f"checksum mismatch for {relpath}"
    assert listed == on_disk

    verify_script = out / "verify.sh"
    assert verify_script.exists()
    import os

    assert os.access(verify_script, os.X_OK)


def test_export_drive_static_pages_pass_accessibility_check(tmp_path: Path) -> None:
    """The no-server index and per-record pages pass the FIX-12 structural gate."""
    archive = _archive(tmp_path)
    record_id = _ingest_public_with_file(archive, tmp_path)

    out = tmp_path / "drive"
    build_export_drive(archive, out, grant=anonymous(), now=_NOW)

    for html_file in (out / "index.html", out / "records" / f"{record_id}.html"):
        markup = html_file.read_text(encoding="utf-8")
        assert check_html(markup, label=str(html_file)) == []


def test_export_drive_refuses_a_nonempty_output_directory(tmp_path: Path) -> None:
    """Building into an existing, non-empty directory is refused (never silently merged)."""
    archive = _archive(tmp_path)
    _ingest_public_with_file(archive, tmp_path)

    out = tmp_path / "drive"
    out.mkdir()
    (out / "unrelated.txt").write_text("pre-existing", encoding="utf-8")

    with pytest.raises(FileExistsError):
        build_export_drive(archive, out, grant=anonymous(), now=_NOW)


def test_export_drive_no_identity_on_the_package(tmp_path: Path) -> None:
    """No contributor identity or sentinel reaches any file written to the drive."""
    from ledger.identity import ContributorIdentity

    archive = _archive(tmp_path)
    sentinel_name = "SENTINEL-EXPORT-DRIVE-DO-NOT-LEAK"
    record = Record(
        title="Sealed identity record",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(title=["Sealed identity record"]),
        fields=[Field(name="story", value="told in public", policy=AccessPolicy.PUBLIC)],
    )
    vault_key = b"0123456789abcdef0123456789abcdef0123456789a="
    archive.ingest(
        {},
        record,
        identity=ContributorIdentity(name=sentinel_name, contact="sentinel@invalid"),
        vault_key=vault_key,
        agent="test",
        now=_NOW,
    )

    out = tmp_path / "drive"
    build_export_drive(archive, out, grant=anonymous(), now=_NOW)

    for path in out.rglob("*"):
        if path.is_file():
            text = path.read_bytes()
            assert sentinel_name.encode("utf-8") not in text, f"sentinel leaked in {path}"
