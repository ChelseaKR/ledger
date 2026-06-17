"""End-to-end tests for the accessible browse server (:mod:`ledger.server`).

A real :class:`http.server.HTTPServer` is bound on an ephemeral port (port 0) and
driven on a background thread, then exercised over loopback exactly as a browser
or a monitor would. The tests assert three things on every record-bearing route:

* it returns ``200`` (or the documented status), so the surface actually works;
* the response carries the WCAG 2.2 AA structure the project promises — a
  declared ``lang``, a "skip to content" link, a ``<main>`` landmark, and a data
  ``<table>`` with a ``<caption>`` (accessibility, verified on the live HTML);
* a sealed field value and a contributor identity sentinel appear in **no**
  response body — HTML, JSON, or health (the no-outing rule, end to end).
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import redirect_stderr, redirect_stdout
from http.server import HTTPServer
from io import StringIO
from pathlib import Path

import pytest

from ledger.config import Config
from ledger.ingest import Archive
from ledger.models import AccessPolicy, DublinCore, Field, Record
from ledger.server import make_server

# A loud identity sentinel sealed into the vault; it must surface on no response.
_SENTINEL_NAME = "SENTINEL-SERVER-DO-NOT-LEAK-7Q4X"
# A distinct sealed-FIELD sentinel: legitimately at rest in the manifest, but it
# must be absent from the anonymous public read path (selective disclosure).
_SEALED_FIELD = "SEALED-FIELD-SERVER-9Z2K"

_VAULT_KEY = b"0123456789abcdef0123456789abcdef0123456789a="
_NOW = "2026-06-16T12:00:00Z"


def _build_archive(tmp_path: Path) -> tuple[Archive, str]:
    """Init an archive and ingest one public record with a sealed field + identity.

    Returns the archive and the public record's id. The record publishes a public
    field, seals a field carrying the sealed-field sentinel, and seals a contributor
    identity sentinel into the vault, so a single request can probe every guarantee.
    """
    from ledger.identity import ContributorIdentity

    config = Config.default("Server Test Archive", tmp_path / "arc")
    archive = Archive.init(config)
    record = Record(
        title="Thursday gatherings",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(
            title=["Thursday gatherings"],
            description=["A short synthetic oral history."],
            publisher=[config.archive_name],
        ),
        fields=[
            Field(name="story", value="A public account.", policy=AccessPolicy.PUBLIC),
            Field(
                name="real_names",
                value=f"roster: {_SEALED_FIELD}",
                policy=AccessPolicy.SEALED_UNTIL,
            ),
        ],
    )
    identity = ContributorIdentity(name=_SENTINEL_NAME)
    archive.ingest(
        {},
        record,
        identity=identity,
        vault_key=_VAULT_KEY,
        agent="server-test",
        now=_NOW,
    )
    return archive, record.record_id


@pytest.fixture
def server(tmp_path: Path) -> Iterator[tuple[HTTPServer, str, str]]:
    """A running browse server on an ephemeral port; yields (server, base_url, rid).

    Binds on port 0 so the OS picks a free port (no fixed-port flakiness), runs the
    server loop on a daemon thread, and tears it down cleanly. Server log output is
    captured and discarded so a noisy handler does not pollute test output (and so a
    separate test can assert logs are identity-free).
    """
    archive, record_id = _build_archive(tmp_path)
    httpd = make_server(archive, host="127.0.0.1", port=0)
    host, port = httpd.server_address[0], httpd.server_address[1]
    host_s = host.decode("ascii") if isinstance(host, (bytes, bytearray)) else str(host)
    base = f"http://{host_s}:{int(port)}"
    sink = StringIO()
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    with redirect_stderr(sink), redirect_stdout(sink):
        thread.start()
        try:
            yield httpd, base, record_id
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()


def _get(base: str, path: str) -> tuple[int, str, dict[str, str]]:
    """GET ``base + path`` over loopback; return (status, body, headers)."""
    url = f"{base}{path}"
    request = urllib.request.Request(url)  # noqa: S310 - loopback URL we constructed
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
        body = response.read().decode("utf-8")
        status = int(response.status)
        headers = {k.lower(): v for k, v in response.headers.items()}
    return status, body, headers


# --- routes return 200 and well-formed bodies ------------------------------


def test_browse_root_returns_200_accessible_html(server: tuple[HTTPServer, str, str]) -> None:
    """``GET /`` returns 200 with the full accessible page structure.

    Asserts the WCAG 2.2 AA structure on the live HTML: a declared ``lang``, a
    skip-to-content link, a ``<main>`` landmark, and a data table with a caption.
    """
    _httpd, base, _rid = server
    status, body, headers = _get(base, "/")
    assert status == 200
    assert headers.get("content-type", "").startswith("text/html")
    # Document language (WCAG 3.1.1).
    assert "<html lang=" in body
    # Skip link, the first focusable element (WCAG 2.4.1).
    assert 'class="skip-link"' in body
    assert "#main" in body
    # Main landmark (WCAG 1.3.1).
    assert '<main id="main"' in body
    # A data table with a caption and scoped headers (WCAG 1.3.1).
    assert "<table" in body
    assert "<caption>" in body
    assert 'scope="col"' in body
    # Exactly one h1.
    assert body.count("<h1>") == 1


def test_record_route_returns_200(server: tuple[HTTPServer, str, str]) -> None:
    """``GET /record/{id}`` returns 200 for a listable record."""
    _httpd, base, rid = server
    status, body, _headers = _get(base, f"/record/{rid}")
    assert status == 200
    assert "<html lang=" in body
    assert '<main id="main"' in body
    assert "Thursday gatherings" in body


def test_api_records_returns_200_json(server: tuple[HTTPServer, str, str]) -> None:
    """``GET /api/records`` returns 200 with a JSON ``records`` array."""
    _httpd, base, rid = server
    status, body, headers = _get(base, "/api/records")
    assert status == 200
    assert headers.get("content-type", "").startswith("application/json")
    data = json.loads(body)
    assert "records" in data
    ids = [r["record_id"] for r in data["records"]]
    assert rid in ids


def test_api_record_returns_200_json(server: tuple[HTTPServer, str, str]) -> None:
    """``GET /api/record/{id}`` returns 200 with that record's disclosed shape."""
    _httpd, base, rid = server
    status, body, _headers = _get(base, f"/api/record/{rid}")
    assert status == 200
    data = json.loads(body)
    assert data["record_id"] == rid
    # The disclosed shape has no identity_ref field by construction.
    assert "identity_ref" not in data


def test_healthz_returns_200_with_status_only_for_outsiders(
    server: tuple[HTTPServer, str, str],
) -> None:
    """``GET /healthz`` gives an outsider status + all_verified, but NOT the counts.

    The absolute counts include sealed/community records, so revealing them to the
    public would leak the total archive size and let an observer poll for new
    sealed records (P2-2). Counts are gated to a steward grant (asserted in
    test_server_remediation, which provisions one)."""
    _httpd, base, _rid = server
    status, body, headers = _get(base, "/healthz")
    assert status == 200
    assert headers.get("content-type", "").startswith("application/json")
    data = json.loads(body)
    assert data["status"] == "ok"
    assert data["all_verified"] is True
    assert "fixity" not in data  # counts are not exposed to outsiders


# --- the no-outing rule, across every response -----------------------------


def test_identity_absent_from_all_responses(server: tuple[HTTPServer, str, str]) -> None:
    """The sealed identity sentinel appears on no response body — HTML or JSON."""
    _httpd, base, rid = server
    bodies = [
        _get(base, "/")[1],
        _get(base, f"/record/{rid}?proceed=1")[1],
        _get(base, "/search?q=Thursday")[1],
        _get(base, "/api/records")[1],
        _get(base, f"/api/record/{rid}")[1],
        _get(base, "/healthz")[1],
    ]
    for body in bodies:
        assert _SENTINEL_NAME not in body


def test_sealed_field_absent_from_anonymous_view(server: tuple[HTTPServer, str, str]) -> None:
    """The sealed FIELD value is withheld from the anonymous public read path.

    The public record is listable and its public field is shown, but the sealed
    field's value is never sent to an anonymous viewer; the field is honestly named
    as withheld instead (selective disclosure).
    """
    _httpd, base, rid = server
    record_html = _get(base, f"/record/{rid}?proceed=1")[1]
    record_json = _get(base, f"/api/record/{rid}")[1]
    assert _SEALED_FIELD not in record_html
    assert _SEALED_FIELD not in record_json
    # The public field IS present, proving the record is genuinely disclosed.
    assert "A public account." in record_html
    # To an OUTSIDER (anonymous) the API discloses only a COUNT of withheld parts,
    # never their names, so the redaction set can't be scraped as targeting metadata.
    data = json.loads(record_json)
    assert data.get("withheld_count", 0) >= 1
    assert "withheld" not in data  # no per-field names/reasons for an outsider
    assert "real_names" not in data["fields"]
    assert "real_names" not in record_json


def test_server_log_is_identity_free(tmp_path: Path) -> None:
    """The server's access log never carries an identity or a query string.

    Drives the server while capturing its log output, then asserts the sealed
    identity sentinel and a search query are absent from the captured log (the log
    is scrubbed to method + path-only + status).
    """
    archive, record_id = _build_archive(tmp_path)
    httpd = make_server(archive, host="127.0.0.1", port=0)
    host, port = httpd.server_address[0], httpd.server_address[1]
    host_s = host.decode("ascii") if isinstance(host, (bytes, bytearray)) else str(host)
    base = f"http://{host_s}:{int(port)}"
    sink = StringIO()
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    with redirect_stderr(sink), redirect_stdout(sink):
        thread.start()
        try:
            _get(base, "/")
            _get(base, f"/record/{record_id}?proceed=1")
            _get(base, "/search?q=SENTINEL-SERVER-DO-NOT-LEAK-7Q4X")
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()
    logs = sink.getvalue()
    assert _SENTINEL_NAME not in logs
    # The search term (which echoed the sentinel) must not survive in the log; the
    # path is logged with its query string stripped.
    assert "q=" not in logs


# --- security headers and 404 neutrality -----------------------------------


def test_responses_carry_security_headers(server: tuple[HTTPServer, str, str]) -> None:
    """Every response sets nosniff and a strict Content-Security-Policy."""
    _httpd, base, _rid = server
    _status, _body, headers = _get(base, "/")
    assert headers.get("x-content-type-options") == "nosniff"
    assert "default-src 'none'" in headers.get("content-security-policy", "")


def test_unknown_record_renders_neutral_404(server: tuple[HTTPServer, str, str]) -> None:
    """An unknown record id renders the same neutral 404 page (no existence leak)."""
    _httpd, base, _rid = server
    request = urllib.request.Request(f"{base}/record/does-not-exist")  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=10):  # noqa: S310
            raised = False
    except urllib.error.HTTPError as exc:
        raised = True
        assert exc.code == 404
        body = exc.read().decode("utf-8")
        assert "Not found" in body
    assert raised, "expected a 404 for an unknown record"
