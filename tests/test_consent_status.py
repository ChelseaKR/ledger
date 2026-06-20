"""Tests for the consent-request status surface (backlog B2).

A contributor who files a withdraw/tighten/correct/contact request is given a
reference token; ``/consent-status`` lets them check whether a steward has acted —
closing the "revocable was true in the room, not on the website" gap. These tests
pin the lookup and that it never echoes the contributor's private message.
"""

from __future__ import annotations

import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import pytest

from ledger.config import Config
from ledger.consent import ConsentRequest, ConsentRequestStore
from ledger.ingest import Archive
from ledger.models import AccessPolicy, DublinCore, Field, Record
from ledger.server import make_server

_NOW = "2026-06-17T00:00:00Z"
_PRIVATE_MSG = "please hide my name PRIVATE-NOTE-DO-NOT-ECHO"


# --- unit: the store lookup -------------------------------------------------


@pytest.mark.disclosure
def test_store_get_finds_by_reference(tmp_path: Path) -> None:
    """``get`` returns the request for a known reference, and None otherwise."""
    store = ConsentRequestStore(tmp_path / "consent-requests.json")
    req = ConsentRequest(record_id="rec-1", kind="tighten", message="hi")
    store.add(req)
    assert store.get(req.request_id) is not None
    assert store.get(req.request_id).request_id == req.request_id
    assert store.get("deadbeef") is None


# --- integration: the live status page --------------------------------------


@pytest.fixture
def server(tmp_path: Path) -> Iterator[tuple[Archive, str, str]]:
    archive = Archive.init(Config.default("Consent Archive", tmp_path / "arc"))
    record = Record(
        title="Thursday gathering",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(
            title=["Thursday gathering"], publisher=[archive.config.archive_name]
        ),
        fields=[Field(name="story", value="A public account.", policy=AccessPolicy.PUBLIC)],
    )
    archive.ingest({}, record, now=_NOW)
    httpd = make_server(archive, host="127.0.0.1", port=0)
    base = f"http://127.0.0.1:{int(httpd.server_address[1])}"
    sink = StringIO()
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    with redirect_stderr(sink), redirect_stdout(sink):
        thread.start()
        try:
            yield archive, base, record.record_id
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()


def _get(base: str, path: str) -> tuple[int, str]:
    req = urllib.request.Request(f"{base}{path}")  # noqa: S310 - loopback
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            return int(resp.status), resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8")


def _post(base: str, path: str, fields: dict[str, str]) -> tuple[int, str]:
    data = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310 - loopback
        f"{base}{path}", data=data, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            return int(resp.status), resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8")


@pytest.mark.disclosure
def test_consent_status_reports_progress_without_echoing_the_message(
    server: tuple[Archive, str, str],
) -> None:
    """Filing a request then checking the reference shows status, never the message."""
    archive, base, rid = server
    status, _ = _post(base, f"/record/{rid}/consent", {"kind": "tighten", "message": _PRIVATE_MSG})
    assert status == 200

    store = ConsentRequestStore(archive.logs_dir / "consent-requests.json")
    ref = store.all()[0].request_id

    # Open: reports "Received", and never echoes the private message.
    status, body = _get(base, f"/consent-status?ref={ref}")
    assert status == 200
    assert "Received" in body
    assert _PRIVATE_MSG not in body

    # Once a steward resolves it, the status reflects that.
    store.resolve(ref, "resolved")
    _status, body = _get(base, f"/consent-status?ref={ref}")
    assert "Resolved" in body
    assert _PRIVATE_MSG not in body

    # The page is localized: a Spanish reader sees the status in Spanish (I2).
    _status, es_body = _get(base, f"/consent-status?lang=es&ref={ref}")
    assert "Consultar una solicitud" in es_body  # heading
    assert "Resuelta" in es_body  # the resolved status, localized
    assert "Resolved" not in es_body
    assert _PRIVATE_MSG not in es_body


@pytest.mark.disclosure
def test_consent_status_unknown_reference_is_neutral(server: tuple[Archive, str, str]) -> None:
    """An unknown reference gets a neutral 'could not find' page, not an error."""
    _archive, base, _rid = server
    status, body = _get(base, "/consent-status?ref=deadbeefdeadbeef")
    assert status == 200
    assert "could not find" in body


@pytest.mark.accessibility
def test_consent_status_form_is_accessible(server: tuple[Archive, str, str]) -> None:
    """The lookup form is labelled and the page has the accessible shell."""
    _archive, base, _rid = server
    status, body = _get(base, "/consent-status")
    assert status == 200
    assert '<main id="main"' in body
    assert body.count("<h1>") == 1
    assert 'for="ref"' in body and 'id="ref"' in body
