"""Tests for the in-UI steward audit log (backlog D3).

A steward could verify the vault never opened but could not read the archive's own
account of what happened. ``/steward/audit`` renders the aggregated PREMIS events,
read-only and steward-gated. These tests pin that it shows real events, is gated,
and carries no contributor identity.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import pytest

from ledger.access.grants import issue_grant_token
from ledger.config import Config
from ledger.ingest import Archive
from ledger.models import AccessPolicy, DublinCore, Field, PremisEventType, Record
from ledger.server import make_server

_SENTINEL = "SENTINEL-AUDIT-DO-NOT-LEAK-3K7P"
_VAULT_KEY = "0123456789abcdef0123456789abcdef0123456789a="
_GRANT_SECRET = b"audit-test-grant-secret"
_NOW = "2026-06-17T00:00:00Z"


def _grants_file(tmp_path: Path) -> Path:
    path = tmp_path / "grants.json"
    path.write_text(
        json.dumps(
            {"steward-1": {"levels": ["public", "community", "stewards"], "is_steward": True}}
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Archive, str]]:
    monkeypatch.setenv("LEDGER_VAULT_KEY", _VAULT_KEY)
    monkeypatch.setenv("LEDGER_GRANT_SECRET", _GRANT_SECRET.decode())
    archive = Archive.init(Config.default("Audit Archive", tmp_path / "arc"))
    from ledger.identity import ContributorIdentity

    record = Record(
        title="A record",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(title=["A record"], publisher=[archive.config.archive_name]),
        fields=[Field(name="story", value="x", policy=AccessPolicy.PUBLIC)],
    )
    archive.ingest({}, record, identity=ContributorIdentity(name=_SENTINEL), now=_NOW)
    httpd = make_server(archive, host="127.0.0.1", port=0, grants_path=_grants_file(tmp_path))
    base = f"http://127.0.0.1:{int(httpd.server_address[1])}"
    sink = StringIO()
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    with redirect_stderr(sink), redirect_stdout(sink):
        thread.start()
        try:
            yield archive, base
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()


def _get(base: str, path: str, *, steward: bool = False) -> tuple[int, str]:
    headers = {"X-Ledger-Grant": issue_grant_token("steward-1", _GRANT_SECRET)} if steward else {}
    req = urllib.request.Request(f"{base}{path}", headers=headers)  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            return int(resp.status), resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8")


def test_audit_events_aggregates_premis(server: tuple[Archive, str]) -> None:
    """``Archive.audit_events`` gathers events and is identity-free."""
    archive, _base = server
    events = archive.audit_events()
    assert events  # ingestion + fixity events exist
    assert PremisEventType.INGESTION in [e.event_type for e in events]
    assert _SENTINEL not in json.dumps([e.to_dict() for e in events])


@pytest.mark.accessibility
def test_audit_page_is_accessible_to_a_steward(server: tuple[Archive, str]) -> None:
    """A steward sees an accessible audit table; a non-steward gets a neutral 404."""
    _archive, base = server
    status, body = _get(base, "/steward/audit", steward=True)
    assert status == 200
    assert "<h1>Audit log</h1>" in body
    assert "<table" in body and "<caption>" in body and 'scope="col"' in body
    assert "ingestion" in body  # the ingestion event is listed
    assert _SENTINEL not in body

    assert _get(base, "/steward/audit")[0] == 404  # non-steward: neutral 404
