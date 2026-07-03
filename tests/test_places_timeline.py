"""Tests for the place (coverage) and timeline (date) browse views — roadmap EX3.

The "map" is a framework-free, no-external-asset *browse by place*: a heading-
structured list of Dublin Core ``coverage`` values, each a count and a link into the
composed ``?coverage=`` facet query. The timeline is a *browse by year* rendered two
equivalent ways — an ordered list and a table — mirroring the browse page's list+table
non-visual equivalent. Both read only through :meth:`Archive.browse`/disclosed records,
so neither can surface a value a viewer may not see (no-outing rule). Records with no
place or no date are omitted with a plain count note rather than mis-bucketed.
"""

from __future__ import annotations

import threading
import urllib.request
from collections.abc import Iterator
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import pytest

from ledger import search
from ledger.config import Config
from ledger.ingest import Archive
from ledger.models import AccessPolicy, DisclosedRecord, DublinCore, Field, Record
from ledger.render import _places_html, _timeline_html
from ledger.server import make_server


def _disclosed(rid: str, **dc: list[str]) -> DisclosedRecord:
    return DisclosedRecord(
        record_id=rid,
        title=rid,
        dublin_core=dc,
        fields={},
        payloads=(),
        content_warnings=(),
        withheld=(),
    )


# --- facet_by_coverage / group_by_year (pure) -------------------------------


def test_facet_by_coverage_counts_places() -> None:
    records = [
        _disclosed("a", coverage=["Oakland"]),
        _disclosed("b", coverage=["Oakland", "Fresno"]),
        _disclosed("c"),  # no place — not counted
    ]
    facets = search.facet_by_coverage(records)
    counts = {f.value: f.count for f in facets}
    assert counts == {"Oakland": 2, "Fresno": 1}
    # Every facet is tagged with the coverage field so the UI can route it.
    assert all(f.field == "coverage" for f in facets)


def test_group_by_year_buckets_and_orders_chronologically() -> None:
    records = [
        _disclosed("late", date=["2001-06"]),
        _disclosed("early", date=["1994-05-01"]),
        _disclosed("also94", date=["1994"]),
        _disclosed("undated"),  # omitted from groups
        _disclosed("nodateval", date=[""]),  # omitted
    ]
    groups = search.group_by_year(records)
    years = [year for year, _ in groups]
    assert years == ["1994", "2001"]  # ascending
    # 1994 gathers both the full date and the bare year, in input order.
    y94 = dict(groups)["1994"]
    assert [r.record_id for r in y94] == ["early", "also94"]


def test_group_by_year_parses_full_iso_timestamp() -> None:
    records = [_disclosed("ts", date=["2010-03-04T12:00:00Z"])]
    assert search.group_by_year(records) == [("2010", records)]


# --- _places_html (pure) ----------------------------------------------------


def test_places_html_lists_places_with_counts_and_links() -> None:
    records = [
        _disclosed("a", coverage=["Oakland"]),
        _disclosed("b", coverage=["Oakland"]),
        _disclosed("c"),  # no place
    ]
    html = _places_html(records)
    assert "<h1>Browse by place</h1>" in html
    assert '<a href="/?coverage=Oakland">Oakland</a>' in html
    assert '<span class="muted">(2)</span>' in html
    # The one placeless record is omitted, but the omission is noted honestly.
    assert "1 record(s) name no place and are not shown here." in html


def test_places_html_empty_state() -> None:
    html = _places_html([_disclosed("a"), _disclosed("b")])
    assert "No records name a place yet." in html
    assert "/?coverage=" not in html


def test_places_html_escapes_and_quotes_a_crafted_place() -> None:
    html = _places_html([_disclosed("a", coverage=['x"><script>'])])
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


# --- _timeline_html (pure) --------------------------------------------------


def test_timeline_html_renders_list_and_table() -> None:
    records = [
        _disclosed("a", date=["1994"]),
        _disclosed("b", date=["2001"]),
        _disclosed("c"),  # undated
    ]
    html = _timeline_html(records)
    assert "<h1>Browse by time</h1>" in html
    # The ordered-list view.
    assert '<ol class="timeline">' in html
    assert '<p class="year">1994</p>' in html
    assert '<a href="/record/a">a</a>' in html
    # The table equivalent, with the caption + scoped headers the a11y gate needs.
    assert '<table class="timeline-table">' in html
    assert "<caption>" in html
    assert '<th scope="col">Year</th>' in html
    # The undated record is omitted with a plain count note.
    assert "1 record(s) carry no date and are not shown here." in html


def test_timeline_html_empty_state() -> None:
    html = _timeline_html([_disclosed("a"), _disclosed("b")])
    assert "No records carry a date yet." in html
    assert '<ol class="timeline">' not in html


# --- end to end over the real server ----------------------------------------


@pytest.fixture
def server_base(tmp_path: Path) -> Iterator[str]:
    config = Config.default("Places Archive", tmp_path / "arc")
    archive = Archive.init(config)
    specs = [
        ("The Oakland march", "Oakland", "1994", "protest"),
        ("An Oakland vigil", "Oakland", "2001", "protest"),
        ("A Fresno flyer", "Fresno", "1994", "housing"),
        ("Undated, placeless note", "", "", "protest"),
    ]
    for title, place, date, subject in specs:
        dc = DublinCore(title=[title], subject=[subject], publisher=[config.archive_name])
        if place:
            dc.coverage = [place]
        if date:
            dc.date = [date]
        record = Record(
            title=title,
            default_policy=AccessPolicy.PUBLIC,
            dublin_core=dc,
            fields=[Field(name="account", value=title, policy=AccessPolicy.PUBLIC)],
        )
        archive.ingest({}, record, agent="t", now="2026-06-20T00:00:00Z")
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


def _get(base: str, path: str) -> str:
    with urllib.request.urlopen(f"{base}{path}", timeout=10) as resp:  # noqa: S310 - loopback
        return resp.read().decode("utf-8")


def test_places_route_lists_coverage_with_counts_and_links(server_base: str) -> None:
    body = _get(server_base, "/places")
    assert '<a href="/?coverage=Oakland">Oakland</a>' in body
    assert '<a href="/?coverage=Fresno">Fresno</a>' in body
    assert '<span class="muted">(2)</span>' in body  # two Oakland records
    # The placeless record is omitted, but its omission is stated.
    assert "1 record(s) name no place and are not shown here." in body


def test_timeline_route_groups_by_year_with_list_and_table(server_base: str) -> None:
    body = _get(server_base, "/timeline")
    assert '<ol class="timeline">' in body
    assert '<p class="year">1994</p>' in body
    assert '<p class="year">2001</p>' in body
    assert '<table class="timeline-table">' in body
    assert '<th scope="col">Year</th>' in body
    assert "1 record(s) carry no date and are not shown here." in body


def test_coverage_facet_composes_with_subject_facet(server_base: str) -> None:
    """?coverage=Oakland AND subject=protest yields only the record satisfying both."""
    body = _get(server_base, "/?coverage=Oakland&subject=protest")
    assert "The Oakland march" in body
    assert "An Oakland vigil" in body
    assert "A Fresno flyer" not in body  # right subject, wrong place
    assert "Undated, placeless note" not in body  # right subject, no place
    assert "Showing 1-2 of 2 record(s)." in body


def test_coverage_facet_alone_narrows_to_the_place(server_base: str) -> None:
    body = _get(server_base, "/?coverage=Fresno")
    assert "A Fresno flyer" in body
    assert "The Oakland march" not in body


def test_nav_links_to_places_and_timeline(server_base: str) -> None:
    body = _get(server_base, "/")
    assert '<a href="/places">Places</a>' in body
    assert '<a href="/timeline">Timeline</a>' in body
