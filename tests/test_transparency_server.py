"""Tests for the ``/transparency`` page (EXP-10, warrant canary).

Pins the three states a visitor can find the page in: unconfigured, configured but
never attested, and attested — plus that staleness and an unreviewed statement are
shown honestly rather than presented as a current, vetted "all clear."
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
from ledger.transparency import TransparencyLog


def _run_server(archive: Archive) -> Iterator[str]:
    from ledger.server import make_server

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


@pytest.fixture
def unconfigured_base(tmp_path: Path) -> Iterator[str]:
    config = Config.default("Unconfigured Archive", tmp_path / "arc")
    archive = Archive.init(config)
    yield from _run_server(archive)


@pytest.fixture
def never_attested_base(tmp_path: Path) -> Iterator[str]:
    config = Config.default("Never Attested Archive", tmp_path / "arc")
    config.transparency_log_path = str(tmp_path / "transparency.json")
    archive = Archive.init(config)
    yield from _run_server(archive)


@pytest.fixture
def attested_base(tmp_path: Path) -> Iterator[str]:
    log_path = tmp_path / "transparency.json"
    config = Config.default("Attested Archive", tmp_path / "arc")
    config.transparency_log_path = str(log_path)
    config.transparency_cadence_days = 30
    archive = Archive.init(config)
    TransparencyLog(log_path).append(
        attested_date="2026-01-01",
        attested_by="steward-a",
        statement_text="No legal demands received to date.",
        demand_counts={"subpoena": 0},
        counsel_reviewed=False,
    )
    yield from _run_server(archive)


@pytest.mark.disclosure
def test_unconfigured_page_makes_no_claim(unconfigured_base: str) -> None:
    body = _get(unconfigured_base, "/transparency")
    assert "has not configured legal-process transparency" in body
    assert "Attested by" not in body


@pytest.mark.disclosure
def test_never_attested_page_is_honest(never_attested_base: str) -> None:
    body = _get(never_attested_base, "/transparency")
    assert "has not yet" in body
    assert "Attested by" not in body


@pytest.mark.disclosure
def test_attested_page_shows_statement_and_unreviewed_warning(attested_base: str) -> None:
    body = _get(attested_base, "/transparency")
    assert "No legal demands received to date." in body
    assert "Attested by: steward-a." in body
    assert "has <strong>not</strong> been reviewed by counsel" in body
    assert "hash-chain verified intact" in body


@pytest.mark.disclosure
def test_stale_attestation_is_flagged_not_hidden(tmp_path: Path) -> None:
    log_path = tmp_path / "transparency.json"
    config = Config.default("Stale Archive", tmp_path / "arc")
    config.transparency_log_path = str(log_path)
    config.transparency_cadence_days = 1
    archive = Archive.init(config)
    TransparencyLog(log_path).append(
        attested_date="2020-01-01",
        attested_by="steward-a",
        statement_text="An old statement.",
    )
    for base in _run_server(archive):
        body = _get(base, "/transparency")
        assert "Treat this statement as STALE, not current." in body


@pytest.mark.disclosure
def test_transparency_is_linked_in_the_nav(unconfigured_base: str) -> None:
    body = _get(unconfigured_base, "/")
    assert 'href="/transparency"' in body
