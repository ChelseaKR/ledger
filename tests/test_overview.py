"""Tests for the collection overview (``/overview``).

An at-a-glance finding aid: the total number of *public* records, the top subjects/
types/languages as browse links, and the date span. It must summarise only what is
publicly visible, so a sealed record never adds to a count (no-outing rule / P2-2).
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
from ledger.models import AccessPolicy, DublinCore, Record

_SEALED_TITLE = "SEALED-OVERVIEW-TITLE"


@pytest.fixture
def server_base(tmp_path: Path) -> Iterator[str]:
    from ledger.server import make_server

    config = Config.default("Overview Archive", tmp_path / "arc")
    archive = Archive.init(config)
    public = [
        ("Flyer 1990", "protest", "1990"),
        ("Zine 2004", "mutual aid", "2004"),
        ("Photo 2019", "protest", "2019"),
    ]
    for title, subject, date in public:
        archive.ingest(
            {},
            Record(
                title=title,
                default_policy=AccessPolicy.PUBLIC,
                dublin_core=DublinCore(
                    title=[title], subject=[subject], date=[date], publisher=[config.archive_name]
                ),
            ),
            agent="t",
            now="2026-06-20T00:00:00Z",
        )
    # A sealed-pending record that must never enter the public overview counts.
    archive.ingest(
        {},
        Record(
            title=_SEALED_TITLE,
            default_policy=AccessPolicy.SEALED_UNTIL,
            dublin_core=DublinCore(title=[_SEALED_TITLE], subject=["secret"]),
        ),
        agent="t",
        now="2026-06-20T00:00:00Z",
    )
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


def test_overview_summarises_only_public_records(server_base: str) -> None:
    body = _get(server_base, "/overview")
    # Three public records, the date span across them, and facet browse links.
    assert "3 public record(s)." in body
    assert "Spanning 1990 to 2019." in body
    assert 'href="/?subject=protest"' in body
    assert "(2)" in body  # protest appears on two public records
    # The sealed record contributes nothing — not its title, not its subject.
    assert _SEALED_TITLE not in body
    assert "secret" not in body


def test_overview_is_linked_in_the_nav(server_base: str) -> None:
    body = _get(server_base, "/")
    assert 'href="/overview"' in body
