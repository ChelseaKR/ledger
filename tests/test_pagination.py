"""Tests for result pagination (``ledger.pagination`` + the browse/search render).

The browse page used to render every listable record at once, which does not scale
and makes one unwieldy page for assistive tech and slow links. These tests pin the
paging maths — clamping out-of-range pages instead of erroring, correct windows and
1-based "showing X-Y of N" indices — and that the rendered page shows only the
current slice with a query-preserving pager.
"""

from __future__ import annotations

import threading
import urllib.request
from collections.abc import Iterator
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import pytest

from ledger import pagination
from ledger.config import Config
from ledger.ingest import Archive
from ledger.models import AccessPolicy, DisclosedRecord, DublinCore, Record
from ledger.render import _browse_main_html
from ledger.server import make_server


def _records(n: int) -> list[DisclosedRecord]:
    return [
        DisclosedRecord(
            record_id=f"rec-{i:03d}",
            title=f"Record {i:03d}",
            dublin_core={"subject": ["mutual aid"]},
            fields={},
            payloads=(),
            content_warnings=(),
            withheld=(),
        )
        for i in range(n)
    ]


# --- the pure paging maths -------------------------------------------------


def test_paginate_windows_the_first_page() -> None:
    page = pagination.paginate(_records(25), 1, per_page=10)
    assert [r.record_id for r in page.items] == [f"rec-{i:03d}" for i in range(10)]
    assert page.number == 1
    assert page.total == 25
    assert page.pages == 3
    assert (page.start_index, page.end_index) == (1, 10)
    assert page.has_next and not page.has_prev


def test_paginate_last_partial_page() -> None:
    page = pagination.paginate(_records(25), 3, per_page=10)
    assert [r.record_id for r in page.items] == [
        "rec-020",
        "rec-021",
        "rec-022",
        "rec-023",
        "rec-024",
    ]
    assert (page.start_index, page.end_index) == (21, 25)
    assert page.has_prev and not page.has_next


def test_paginate_clamps_out_of_range_pages() -> None:
    """A too-large or non-positive page lands on the nearest real page, never errors."""
    high = pagination.paginate(_records(25), 999, per_page=10)
    assert high.number == 3  # clamped to the last page
    low = pagination.paginate(_records(25), 0, per_page=10)
    assert low.number == 1
    negative = pagination.paginate(_records(25), -5, per_page=10)
    assert negative.number == 1


def test_paginate_empty_set_is_one_empty_page() -> None:
    page = pagination.paginate([], 1, per_page=10)
    assert page.items == ()
    assert page.pages == 1
    assert page.total == 0
    assert (page.start_index, page.end_index) == (0, 0)
    assert not page.has_prev and not page.has_next


def test_paginate_floors_per_page_at_one() -> None:
    page = pagination.paginate(_records(3), 1, per_page=0)
    assert len(page.items) == 1
    assert page.pages == 3


# --- the rendered page -----------------------------------------------------


def test_browse_renders_only_the_current_page() -> None:
    html = _browse_main_html(
        _records(25),
        heading="Browse",
        page=1,
        per_page=10,
        current_path="/",
    )
    # First page: record 000 present, record 010 (page 2) absent.
    assert "Record 000" in html
    assert "Record 010" not in html
    # The status line reports the window over the full total.
    assert "Showing 1-10 of 25 record(s)." in html


def test_pager_links_preserve_the_query_and_swap_only_page() -> None:
    html = _browse_main_html(
        _records(25),
        heading="Subject: mutual aid",
        page=2,
        per_page=10,
        current_path="/?subject=mutual+aid",
    )
    assert 'aria-label="Pagination"' in html
    assert "Page 2 of 3" in html
    # Prev/Next keep the facet filter and change only the page number.
    assert 'href="/?subject=mutual+aid&amp;page=1"' in html
    assert 'href="/?subject=mutual+aid&amp;page=3"' in html
    # No stale or duplicate page parameter is carried along.
    assert "page=2&" not in html


def test_no_pager_when_everything_fits_on_one_page() -> None:
    html = _browse_main_html(_records(5), heading="Browse", page=1, per_page=10, current_path="/")
    assert 'aria-label="Pagination"' not in html
    assert "Showing 1-5 of 5 record(s)." in html


# --- end-to-end through the server -----------------------------------------


@pytest.fixture
def server_base(tmp_path: Path) -> Iterator[str]:
    """A running browse server seeded with 22 public records (so it spans two pages)."""
    config = Config.default("Pagination Archive", tmp_path / "arc")
    archive = Archive.init(config)
    for i in range(22):
        record = Record(
            title=f"Record {i:03d}",
            default_policy=AccessPolicy.PUBLIC,
            dublin_core=DublinCore(title=[f"Record {i:03d}"], publisher=[config.archive_name]),
        )
        archive.ingest({}, record, agent="pagination-test", now="2026-06-16T12:00:00Z")
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


def test_browse_paginates_over_http(server_base: str) -> None:
    """The default page size bounds the browse page; ?page= walks to the rest."""
    first = _get(server_base, "/")
    assert "Showing 1-20 of 22 record(s)." in first
    assert 'aria-label="Pagination"' in first
    assert "Page 1 of 2" in first
    assert 'href="/?page=2"' in first

    second = _get(server_base, "/?page=2")
    assert "Showing 21-22 of 22 record(s)." in second
    assert "Page 2 of 2" in second


def test_out_of_range_page_is_clamped_not_an_error(server_base: str) -> None:
    """A stale ?page=999 lands on the last real page instead of 500-ing."""
    body = _get(server_base, "/?page=999")
    assert "Showing 21-22 of 22 record(s)." in body
