"""Tests for composing search with faceted browse.

Search and facets used to be separate paths — a reader could search *or* filter by
a subject, never both. Now they compose: every active facet and the search term
narrow to the intersection, facet links preserve the query (and other facets), the
search form keeps the facets as it posts, and a clear-filters link escapes the
narrowed view.
"""

from __future__ import annotations

import threading
import urllib.request
from collections.abc import Iterator
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import pytest

from ledger.config import Config
from ledger.ingest import Archive
from ledger.models import AccessPolicy, DisclosedRecord, DublinCore, Field, Record
from ledger.render import _browse_main_html, _facets_html
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


# --- facet links compose with the query ------------------------------------


def test_facet_link_preserves_the_search_query() -> None:
    records = [_disclosed("a", subject=["protest"])]
    html = _facets_html(records, current_path="/search?q=march", active=[])
    # Clicking the subject keeps the query and adds the facet.
    assert 'href="/search?q=march&amp;subject=protest"' in html


def test_active_facet_is_marked_and_its_link_toggles_off() -> None:
    records = [_disclosed("a", subject=["protest"])]
    html = _facets_html(records, current_path="/?subject=protest", active=[("subject", "protest")])
    assert 'aria-current="true"' in html
    # The active value's link removes it (toggle off) — back to the bare path.
    assert 'href="/"' in html


def test_facet_link_replaces_a_prior_value_of_the_same_field() -> None:
    records = [_disclosed("a", subject=["housing"])]
    html = _facets_html(records, current_path="/?subject=protest", active=[("subject", "protest")])
    # Choosing a different subject swaps it rather than ANDing the field to nothing.
    assert 'href="/?subject=housing"' in html
    assert "subject=protest&amp;subject=housing" not in html


def test_browse_main_shows_clear_link_and_carries_facets_in_search_form() -> None:
    records = [_disclosed("a", subject=["protest"])]
    html = _browse_main_html(
        records,
        heading="x",
        query="march",
        active_facets=[("subject", "protest")],
        current_path="/search?q=march&subject=protest",
    )
    assert "clear-filters" in html
    # The search form keeps the facet as a hidden input so searching does not drop it.
    assert '<input type="hidden" name="subject" value="protest">' in html


# --- end to end: the intersection -------------------------------------------


@pytest.fixture
def server_base(tmp_path: Path) -> Iterator[str]:
    config = Config.default("Compose Archive", tmp_path / "arc")
    archive = Archive.init(config)
    specs = [
        ("The big march", "protest"),  # matches q=march AND subject=protest
        ("A quiet vigil", "protest"),  # subject=protest, no "march"
        ("Another march", "housing"),  # has "march" but subject=housing
    ]
    for title, subject in specs:
        record = Record(
            title=title,
            default_policy=AccessPolicy.PUBLIC,
            dublin_core=DublinCore(
                title=[title], subject=[subject], publisher=[config.archive_name]
            ),
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


def test_search_within_a_facet_returns_the_intersection(server_base: str) -> None:
    """q=march AND subject=protest yields only the record satisfying both."""
    body = _get(server_base, "/search?q=march&subject=protest")
    assert "The big march" in body
    assert "A quiet vigil" not in body  # subject matches but not the query
    assert "Another march" not in body  # query matches but not the subject
    assert "Showing 1-1 of 1 record(s)." in body
