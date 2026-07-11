"""Tests for the accessible print-edition booklet (``ledger.print_edition``, EXP-08).

Covers the two properties the ideation risk note calls out explicitly: the
booklet is PUBLIC-only *by construction* (no caller parameter can widen it), and
content warnings render before content in the same HTML.
"""

from __future__ import annotations

from pathlib import Path

from ledger.accessibility_check import check_html
from ledger.config import Config
from ledger.ingest import Archive
from ledger.models import AccessPolicy, DublinCore, Field, Record
from ledger.print_edition import build_print_edition, record_fixity_digest

_NOW = "2026-06-16T12:00:00Z"


def _archive(tmp_path: Path) -> Archive:
    config = Config.default("Booklet Test Archive", tmp_path / "arc")
    return Archive.init(config)


def _ingest_public(archive: Archive, *, cw: list[str] | None = None) -> str:
    record = Record(
        title="A public zine entry",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(title=["A public zine entry"], date=["1994"]),
        fields=[Field(name="story", value="told in public", policy=AccessPolicy.PUBLIC)],
        content_warnings=cw or [],
    )
    archive.ingest({}, record, agent="test", now=_NOW)
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


def test_print_edition_is_public_only_by_construction(tmp_path: Path) -> None:
    """A COMMUNITY-only record never appears, even if explicitly requested by id."""
    archive = _archive(tmp_path)
    public_id = _ingest_public(archive)
    community_id = _ingest_community_only(archive)

    out = tmp_path / "booklet.html"
    result = build_print_edition(archive, out, record_ids=[public_id, community_id], now=_NOW)

    assert result.records_included == 1
    html = out.read_text(encoding="utf-8")
    assert "A public zine entry" in html
    assert "Community-only note" not in html


def test_print_edition_content_warning_renders_before_content(tmp_path: Path) -> None:
    """The content-warning block's markup precedes the descriptive-metadata list."""
    archive = _archive(tmp_path)
    _ingest_public(archive, cw=["outing"])

    out = tmp_path / "booklet.html"
    build_print_edition(archive, out, now=_NOW)
    html = out.read_text(encoding="utf-8")

    cw_index = html.index('class="cw"')
    dc_index = html.index('class="dc"')
    assert cw_index < dc_index
    assert "outing" in html


def test_print_edition_shows_visible_fixity_text_always(tmp_path: Path) -> None:
    """The plain-text fixity line is present whether or not segno is installed."""
    archive = _archive(tmp_path)
    _ingest_public(archive)

    out = tmp_path / "booklet.html"
    build_print_edition(archive, out, base_url="https://archive.example", now=_NOW)
    html = out.read_text(encoding="utf-8")

    assert "SHA-256: <code>" in html
    assert "https://archive.example/record/" in html


def test_record_fixity_digest_is_deterministic() -> None:
    """The same disclosed record always hashes to the same fixity digest."""
    from ledger.models import DisclosedRecord

    rec = DisclosedRecord(
        record_id="abc",
        title="Title",
        dublin_core={"date": ["1994"]},
        fields={"story": "told"},
        payloads=(),
        content_warnings=(),
        withheld=(),
    )
    assert record_fixity_digest(rec) == record_fixity_digest(rec)
    assert len(record_fixity_digest(rec)) == 64  # hex SHA-256


def test_print_edition_html_passes_accessibility_check(tmp_path: Path) -> None:
    """The booklet passes the same structural FIX-12 gate as the live site."""
    archive = _archive(tmp_path)
    _ingest_public(archive, cw=["outing"])

    out = tmp_path / "booklet.html"
    build_print_edition(archive, out, now=_NOW)
    html = out.read_text(encoding="utf-8")

    assert check_html(html, label=str(out)) == []


def test_print_edition_no_records_still_produces_valid_booklet(tmp_path: Path) -> None:
    """An empty archive still yields a well-formed, accessible booklet (no crash)."""
    archive = _archive(tmp_path)
    out = tmp_path / "booklet.html"
    result = build_print_edition(archive, out, now=_NOW)
    assert result.records_included == 0
    assert check_html(out.read_text(encoding="utf-8"), label=str(out)) == []
