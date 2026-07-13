"""Tests for the Atom feed of recent public records (``oai.atom_feed_xml`` + server).

A feed lets a reader or aggregator follow a collection as it grows. The guarantees
under test: it is well-formed Atom, it re-serializes only the public (anonymous)
disclosure set so no sealed record or identity can appear (no-outing rule), it orders
newest-first deterministically, and it coerces Dublin Core dates into the RFC 3339
instants Atom requires. The server surface is always the public view, regardless of
the viewer, so this cacheable endpoint can never leak community-only content.
"""

from __future__ import annotations

import threading
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import pytest

from ledger import oai
from ledger.config import Config
from ledger.identity import ContributorIdentity
from ledger.ingest import Archive
from ledger.models import AccessPolicy, DisclosedRecord, DublinCore, Field, Record
from ledger.server import make_server

_NOW = "2026-06-20T00:00:00Z"


def _disclosed(record_id: str, title: str, **dc: list[str]) -> DisclosedRecord:
    return DisclosedRecord(
        record_id=record_id,
        title=title,
        dublin_core=dc,
        fields={},
        payloads=(),
        content_warnings=(),
        withheld=(),
    )


# --- the pure feed renderer -------------------------------------------------


def test_feed_is_well_formed_and_names_the_archive_as_author() -> None:
    """The feed parses as XML and its only author is the collection, never a person."""
    feed = oai.atom_feed_xml(
        [_disclosed("a", "One", description=["A note."])],
        archive_name="People's Archive",
        base_url="http://x.org/",
        now=_NOW,
    )
    ET.fromstring(feed)  # noqa: S314 - our own trusted output; raises if not well-formed
    assert "<author><name>People's Archive</name></author>" in feed
    assert '<summary type="text">A note.</summary>' in feed
    assert 'href="http://x.org/record/a"' in feed


def test_feed_orders_newest_first_by_date_then_id() -> None:
    """Entries sort by Dublin Core date descending, with record_id as a stable tie-break."""
    records = [
        _disclosed("old", "Old", date=["2001"]),
        _disclosed("new", "New", date=["2026-03-10"]),
        _disclosed("mid-b", "Mid B", date=["2010"]),
        _disclosed("mid-a", "Mid A", date=["2010"]),
    ]
    feed = oai.atom_feed_xml(records, archive_name="A", base_url="http://x.org", now=_NOW)
    order = [feed.index(t) for t in ("New", "Mid B", "Mid A", "Old")]
    assert order == sorted(order)  # they appear in exactly this order


def test_feed_coerces_dates_to_rfc3339() -> None:
    """A bare year or Y-M-D becomes a full instant; a full timestamp is kept."""
    records = [
        _disclosed("y", "Year", date=["1999"]),
        _disclosed("d", "Day", date=["2020-07-04"]),
        _disclosed("t", "Stamp", date=["2021-01-02T03:04:05Z"]),
        _disclosed("n", "NoDate"),
    ]
    feed = oai.atom_feed_xml(records, archive_name="A", base_url="http://x.org", now=_NOW)
    assert "<updated>1999-01-01T00:00:00Z</updated>" in feed
    assert "<updated>2020-07-04T00:00:00Z</updated>" in feed
    assert "<updated>2021-01-02T03:04:05Z</updated>" in feed
    # A record with no date falls back to the feed's generation instant.
    assert feed.count(f"<updated>{_NOW}</updated>") >= 2  # the feed itself + the undated entry


def test_feed_normalizes_unpadded_and_mixed_granularity_dates() -> None:
    """Calendar order wins across bare year, month, and unpadded month/day shapes."""
    records = [
        _disclosed("year", "Year only", date=["2021"]),
        _disclosed("may", "Unpadded May", date=["2021-5-1"]),
        _disclosed("dec", "December", date=["2021-12"]),
    ]

    feed = oai.atom_feed_xml(records, archive_name="A", base_url="http://x.org", now=_NOW)

    assert "<updated>2021-05-01T00:00:00Z</updated>" in feed
    assert "<updated>2021-12-01T00:00:00Z</updated>" in feed
    order = [feed.index(title) for title in ("December", "Unpadded May", "Year only")]
    assert order == sorted(order)


def test_feed_normalizes_offsets_before_ordering_and_falls_back_on_invalid_dates() -> None:
    """Offset instants sort chronologically; impossible/free-text dates use ``now``."""
    records = [
        _disclosed("offset", "Earlier by offset", date=["2021-01-01T00:30:00+02:00"]),
        _disclosed("utc", "Later in UTC", date=["2020-12-31T23:00:00Z"]),
        _disclosed("bad-calendar", "Bad calendar", date=["2021-13-40"]),
        _disclosed("free-text", "Free text", date=["sometime after the march"]),
    ]

    feed = oai.atom_feed_xml(records, archive_name="A", base_url="http://x.org", now=_NOW)

    assert "<updated>2020-12-31T22:30:00Z</updated>" in feed
    assert feed.index("Later in UTC") < feed.index("Earlier by offset")
    assert feed.count(f"<updated>{_NOW}</updated>") >= 3  # feed + both invalid values


def test_feed_escapes_markup_in_titles_and_summaries() -> None:
    """Angle brackets and ampersands are escaped, so a title cannot break the XML."""
    feed = oai.atom_feed_xml(
        [_disclosed("x", "Rent <strike> & march", description=["A & B <c>"])],
        archive_name="A",
        base_url="http://x.org",
        now=_NOW,
    )
    ET.fromstring(feed)  # noqa: S314 - our own trusted output
    assert "Rent &lt;strike&gt; &amp; march" in feed
    assert "A &amp; B &lt;c&gt;" in feed


def test_feed_respects_the_limit() -> None:
    """The feed is capped so a huge archive does not produce an unbounded feed."""
    records = [_disclosed(f"r{i}", f"R{i}", date=[f"20{i:02d}"]) for i in range(10)]
    feed = oai.atom_feed_xml(records, archive_name="A", base_url="http://x.org", now=_NOW, limit=3)
    assert feed.count("<entry>") == 3


# --- the server surface -----------------------------------------------------

_SENTINEL = "FEED-SENTINEL-DO-NOT-LEAK-9Z2K"
_VAULT_KEY = b"0123456789abcdef0123456789abcdef0123456789a="


@pytest.fixture
def server_base(tmp_path: Path) -> Iterator[str]:
    """A running server with one public record (sealed identity) and one sealed record."""
    config = Config.default("Feed Archive", tmp_path / "arc")
    archive = Archive.init(config)
    public = Record(
        title="Public flyer",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(
            title=["Public flyer"], description=["A public scan."], date=["2026"]
        ),
        fields=[Field(name="story", value="A public account.", policy=AccessPolicy.PUBLIC)],
    )
    archive.ingest(
        {}, public, identity=ContributorIdentity(name=_SENTINEL), vault_key=_VAULT_KEY, now=_NOW
    )
    sealed = Record(
        title="SEALED-RECORD-TITLE",
        default_policy=AccessPolicy.SEALED_UNTIL,
        dublin_core=DublinCore(title=["SEALED-RECORD-TITLE"]),
    )
    archive.ingest({}, sealed, now=_NOW)

    httpd = make_server(archive, host="127.0.0.1", port=0)
    base = f"http://127.0.0.1:{int(httpd.server_address[1])}"
    sink = StringIO()
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    with redirect_stderr(sink), redirect_stdout(sink):
        thread.start()
        try:
            yield base
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()


def _get(base: str, path: str) -> tuple[str, str]:
    with urllib.request.urlopen(f"{base}{path}", timeout=10) as resp:  # noqa: S310 - loopback
        return resp.read().decode("utf-8"), resp.headers.get("Content-Type", "")


def test_feed_endpoint_serves_atom_with_only_public_content(server_base: str) -> None:
    """``/feed.atom`` returns Atom, lists the public record, and leaks nothing sealed."""
    body, content_type = _get(server_base, "/feed.atom")
    assert "application/atom+xml" in content_type
    ET.fromstring(body)  # noqa: S314 - our own trusted output
    assert "Public flyer" in body
    # Neither the sealed record nor the sealed contributor identity appears.
    assert "SEALED-RECORD-TITLE" not in body
    assert _SENTINEL not in body


def test_html_pages_advertise_the_feed(server_base: str) -> None:
    """The browse page links the feed so a reader/aggregator can auto-discover it."""
    body, _ct = _get(server_base, "/")
    assert 'rel="alternate" type="application/atom+xml"' in body
    assert 'href="/feed.atom"' in body


def test_robots_points_at_the_sitemap_and_hides_non_content(server_base: str) -> None:
    """``/robots.txt`` guides crawlers to the sitemap and away from write/admin paths."""
    body, content_type = _get(server_base, "/robots.txt")
    assert "text/plain" in content_type
    assert "Sitemap: http://127.0.0.1" in body and "/sitemap.xml" in body
    # The write and operator surfaces are kept out of public indexes.
    for path in ("/steward", "/contribute", "/withdraw", "/edit", "/api/"):
        assert f"Disallow: {path}" in body


def test_sitemap_includes_the_browse_root(server_base: str) -> None:
    """The served sitemap leads with the browse root so crawlers reach the feed link."""
    body, _ct = _get(server_base, "/sitemap.xml")
    ET.fromstring(body)  # noqa: S314 - our own trusted output
    assert "<loc>http://127.0.0.1" in body and "/</loc>" in body
