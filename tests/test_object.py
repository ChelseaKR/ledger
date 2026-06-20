"""Tests for subject (third-party) objections (backlog B3).

A person *named* in a record they did not contribute can object — ask a steward to
review it — without a contributor claim token. These tests pin that the objection is
queued for a steward, is status-checkable like any request, and never echoes the
objector's message.
"""

from __future__ import annotations

import json
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
from ledger.consent import ConsentRequestStore
from ledger.ingest import Archive
from ledger.models import AccessPolicy, DublinCore, Field, Record
from ledger.server import make_server

_NOW = "2026-06-17T00:00:00Z"
_OBJECTION = "I am named in this and did not agree OBJECTION-NOTE-DO-NOT-ECHO"


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
def server(tmp_path: Path) -> Iterator[tuple[Archive, str, str]]:
    archive = Archive.init(Config.default("Object Archive", tmp_path / "arc"))
    record = Record(
        title="A public account",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(title=["A public account"], publisher=[archive.config.archive_name]),
        fields=[Field(name="story", value="Names some people.", policy=AccessPolicy.PUBLIC)],
    )
    archive.ingest({}, record, now=_NOW)
    httpd = make_server(archive, host="127.0.0.1", port=0, grants_path=_grants_file(tmp_path))
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


def _req(
    base: str, path: str, *, data: dict[str, str] | None = None, steward: bool = False
) -> tuple[int, str]:
    body = urllib.parse.urlencode(data).encode("utf-8") if data is not None else None
    headers = {"X-Ledger-Grant": "steward-1"} if steward else {}
    if body is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(f"{base}{path}", data=body, headers=headers)  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            return int(resp.status), resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8")


@pytest.mark.accessibility
def test_object_form_is_served_and_accessible(server: tuple[Archive, str, str]) -> None:
    """The objection form is reachable (no claim token) and structurally accessible."""
    _archive, base, rid = server
    status, body = _req(base, f"/record/{rid}/object")
    assert status == 200
    assert '<main id="main"' in body
    assert body.count("<h1>") == 1
    assert 'for="message"' in body and 'id="message"' in body
    # No claim-token field — a subject is not the contributor.
    assert 'name="claim"' not in body


@pytest.mark.disclosure
def test_objection_is_queued_for_a_steward_and_status_checkable(
    server: tuple[Archive, str, str],
) -> None:
    """Filing an objection queues a kind=object request, shown to a steward; the
    objector's message is never echoed, and the reference is status-checkable."""
    archive, base, rid = server
    status, body = _req(base, f"/record/{rid}/object", data={"message": _OBJECTION})
    assert status == 200
    assert "objection was received" in body.lower()
    assert _OBJECTION not in body  # the message is not reflected back

    store = ConsentRequestStore(archive.logs_dir / "consent-requests.json")
    reqs = store.all()
    assert len(reqs) == 1
    assert reqs[0].kind == "object"
    ref = reqs[0].request_id

    # The steward console surfaces it, labelled, without echoing the message.
    status, console = _req(base, "/steward", steward=True)
    assert status == 200
    assert rid in console
    assert "named in the record" in console  # the friendly object label
    assert _OBJECTION not in console

    # The objector can check progress with their reference (B2), message not echoed.
    status, sbody = _req(base, f"/consent-status?ref={ref}")
    assert status == 200
    assert "Received" in sbody
    assert _OBJECTION not in sbody


@pytest.mark.disclosure
def test_empty_objection_is_rejected(server: tuple[Archive, str, str]) -> None:
    """An objection with no message re-renders the form with a 400."""
    _archive, base, rid = server
    status, body = _req(base, f"/record/{rid}/object", data={"message": "   "})
    assert status == 400
    assert 'role="alert"' in body


@pytest.mark.disclosure
def test_object_on_unknown_record_is_neutral_404(server: tuple[Archive, str, str]) -> None:
    """Objecting to a record that is not listable is a neutral 404."""
    _archive, base, _rid = server
    assert _req(base, "/record/rec-does-not-exist/object")[0] == 404
    assert _req(base, "/record/rec-does-not-exist/object", data={"message": "x"})[0] == 404
