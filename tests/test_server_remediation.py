"""End-to-end tests for the user-research remediation server routes.

Covers content retrieval (access-controlled, fixity-served), the plain-language
safety pages, the HTML status page, OAI-PMH + sitemap (public only), the
contributor consent flow, the gated steward console, header suppression, static
caching, and that none of the new surfaces leak a contributor identity.
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

from ledger.access.grants import issue_grant_token
from ledger.config import Config
from ledger.consent import issue_claim_token
from ledger.identity import ContributorIdentity
from ledger.ingest import Archive
from ledger.models import AccessPolicy, DublinCore, Field, Record
from ledger.server import make_server

_SENTINEL = "SENTINEL-REMEDIATION-DO-NOT-LEAK-Q9"
_VAULT_KEY = b"0123456789abcdef0123456789abcdef0123456789a="
_CLAIM_SECRET = "test-claim-secret"  # noqa: S105 - test fixture, not a real secret
_GRANT_SECRET = b"remediation-test-grant-secret"
_NOW = "2026-06-16T12:00:00Z"


@pytest.fixture
def site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, str, str]]:
    """A running server seeded with a public record (+identity, +file) and a
    community record (+file); yields (base_url, public_id, community_id)."""
    monkeypatch.setenv("LEDGER_VAULT_KEY", _VAULT_KEY.decode())
    monkeypatch.setenv("LEDGER_CLAIM_SECRET", _CLAIM_SECRET)
    monkeypatch.setenv("LEDGER_GRANT_SECRET", _GRANT_SECRET.decode())
    config = Config.default("Remediation Test Archive", tmp_path / "arc")
    archive = Archive.init(config)

    pub_file = tmp_path / "flyer.txt"
    pub_file.write_text("Pride march 1991, library steps, noon.")
    empty_file = tmp_path / "empty.txt"
    empty_file.write_text("")  # zero-length payloads can enter the CAS
    pub = Record(
        title="Flyer 1991",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(title=["Flyer 1991"], subject=["pride"], type=["flyer"]),
        fields=[Field("text", "public", AccessPolicy.PUBLIC)],
    )
    archive.ingest(
        {pub_file.name: pub_file, empty_file.name: empty_file},
        pub,
        identity=ContributorIdentity(name=_SENTINEL),
        vault_key=_VAULT_KEY,
        now=_NOW,
    )

    comm_file = tmp_path / "runbook.txt"
    comm_file.write_text("community runbook body")
    comm = Record(
        title="Runbook",
        default_policy=AccessPolicy.COMMUNITY,
        dublin_core=DublinCore(title=["Runbook"], subject=["mutual aid"], type=["zine"]),
        fields=[Field("summary", "community", AccessPolicy.COMMUNITY)],
    )
    archive.ingest({comm_file.name: comm_file}, comm, now=_NOW)

    grants = tmp_path / "grants.json"
    grants.write_text(
        json.dumps(
            {
                "member": {"levels": ["public", "community"]},
                "boss": {"levels": ["public", "community", "stewards"], "is_steward": True},
            }
        )
    )
    httpd = make_server(archive, host="127.0.0.1", port=0, grants_path=grants)
    port = int(httpd.server_address[1])
    base = f"http://127.0.0.1:{port}"
    sink = StringIO()
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    with redirect_stderr(sink), redirect_stdout(sink):
        thread.start()
        try:
            yield base, pub.record_id, comm.record_id
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()


def _grant_header(subject: str) -> str:
    """A signed capability token for ``subject`` (the header is now authenticated)."""
    return issue_grant_token(subject, _GRANT_SECRET)


def _get(
    base: str,
    path: str,
    *,
    grant: str | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, str, dict[str, str]]:
    req = urllib.request.Request(f"{base}{path}")  # noqa: S310 - loopback
    if grant:
        req.add_header("X-Ledger-Grant", _grant_header(grant))
    for name, value in (headers or {}).items():
        req.add_header(name, value)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310
            return (
                int(r.status),
                r.read().decode("utf-8"),
                {k.lower(): v for k, v in r.headers.items()},
            )
    except urllib.error.HTTPError as e:
        return (
            int(e.code),
            e.read().decode("utf-8"),
            {k.lower(): v for k, v in e.headers.items()},
        )


def _post(
    base: str, path: str, data: dict[str, str], *, grant: str | None = None
) -> tuple[int, str]:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(f"{base}{path}", data=body)  # noqa: S310 - loopback
    if grant:
        req.add_header("X-Ledger-Grant", _grant_header(grant))
    try:
        with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310
            return int(r.status), r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return int(e.code), e.read().decode("utf-8")


# --- safety surface (P0-4) --------------------------------------------------


@pytest.mark.parametrize("path", ["/about", "/governance", "/how-it-works", "/proof"])
def test_safety_pages_render(site: tuple[str, str, str], path: str) -> None:
    base, _pub, _comm = site
    status, body, _ = _get(base, path)
    assert status == 200
    assert "<h1>" in body and "<main" in body


def test_status_page_is_human_html_not_json(site: tuple[str, str, str]) -> None:
    base, _pub, _comm = site
    status, body, headers = _get(base, "/status")
    assert status == 200
    assert headers["content-type"].startswith("text/html")
    assert "Everything is healthy" in body or "attention" in body
    # the raw JSON still lives at /healthz
    _, hbody, _ = _get(base, "/healthz")
    assert json.loads(hbody)["status"] in {"ok", "degraded"}


# --- content retrieval (P0-3 / C4) ------------------------------------------


def test_public_file_downloads(site: tuple[str, str, str]) -> None:
    base, pub, _comm = site
    status, body, headers = _get(base, f"/record/{pub}/file/flyer.txt")
    assert status == 200
    assert "Pride march 1991" in body
    assert headers["content-type"].startswith("text/plain")


def test_community_file_access_controlled(site: tuple[str, str, str]) -> None:
    base, _pub, comm = site
    assert _get(base, f"/record/{comm}/file/runbook.txt")[0] == 404  # anon denied
    assert _get(base, f"/record/{comm}/file/runbook.txt", grant="member")[0] == 200


def test_file_download_supports_byte_range(site: tuple[str, str, str]) -> None:
    """FIX-03: a ``Range`` request gets a ``206`` with exactly the requested bytes.

    This is what lets a browser seek within served audio/video instead of
    re-downloading the whole file; the content is small here, but the same code
    path serves multi-gigabyte media without ever loading it whole into memory.
    """
    base, pub, _comm = site
    full_status, full_body, _ = _get(base, f"/record/{pub}/file/flyer.txt")
    assert full_status == 200
    status, body, headers = _get(
        base, f"/record/{pub}/file/flyer.txt", headers={"Range": "bytes=0-4"}
    )
    assert status == 206
    assert body == full_body[:5]
    assert headers["content-range"] == f"bytes 0-4/{len(full_body.encode())}"
    assert headers["accept-ranges"] == "bytes"


def test_file_download_rejects_unsatisfiable_range(site: tuple[str, str, str]) -> None:
    base, pub, _comm = site
    status, _body, headers = _get(
        base, f"/record/{pub}/file/flyer.txt", headers={"Range": "bytes=999999-9999999"}
    )
    assert status == 416
    assert headers["content-range"].startswith("bytes */")


def test_empty_file_suffix_range_is_unsatisfiable(site: tuple[str, str, str]) -> None:
    """RFC 9110 §14.1.2: a suffix range against a zero-length resource has no
    last byte to name — it must be a ``416``, never a malformed ``206`` with an
    inverted ``Content-Range`` (``bytes 0--1/0``)."""
    base, pub, _comm = site
    status, _body, headers = _get(
        base, f"/record/{pub}/file/empty.txt", headers={"Range": "bytes=-5"}
    )
    assert status == 416
    assert headers["content-range"] == "bytes */0"
    # and a plain GET of the empty payload still succeeds
    status, body, headers = _get(base, f"/record/{pub}/file/empty.txt")
    assert status == 200
    assert body == ""
    assert headers["content-length"] == "0"


# --- OAI-PMH + sitemap (P2-3), public only ----------------------------------


def test_oai_lists_public_records_only(site: tuple[str, str, str]) -> None:
    base, _pub, _comm = site
    _, body, _ = _get(base, "/oai?verb=ListRecords&metadataPrefix=oai_dc")
    assert "Flyer 1991" in body  # public
    assert "Runbook" not in body  # community-only, not harvested
    assert _get(base, "/oai?verb=Identify")[0] == 200
    _, sm, _ = _get(base, "/sitemap.xml")
    assert "/record/" in sm


# --- contributor consent flow (P0-2) ----------------------------------------


def test_consent_form_and_submission(site: tuple[str, str, str]) -> None:
    base, pub, _comm = site
    assert _get(base, f"/record/{pub}/consent")[0] == 200
    token = issue_claim_token(pub, _CLAIM_SECRET.encode())
    status, body = _post(
        base, f"/record/{pub}/consent", {"kind": "withdraw", "claim": token, "message": "remove"}
    )
    assert status == 200
    assert "Request received" in body and "verified" in body
    # an invalid kind is rejected
    assert _post(base, f"/record/{pub}/consent", {"kind": "nonsense", "claim": token})[0] == 400


# --- steward console (P1-5), gated ------------------------------------------


def test_steward_console_gated_and_shows_requests(site: tuple[str, str, str]) -> None:
    base, pub, _comm = site
    assert _get(base, "/steward")[0] == 404  # anonymous cannot see it exists
    token = issue_claim_token(pub, _CLAIM_SECRET.encode())
    _post(base, f"/record/{pub}/consent", {"kind": "withdraw", "claim": token, "message": "x"})
    status, body, _ = _get(base, "/steward", grant="boss")
    assert status == 200
    assert "withdraw" in body


# --- side-channels + caching (P2-2, P3-1) -----------------------------------


def test_server_header_has_no_version(site: tuple[str, str, str]) -> None:
    base, _pub, _comm = site
    _, _, headers = _get(base, "/")
    assert "0.1" not in headers.get("server", "")
    assert "Python" not in headers.get("server", "")


def test_static_is_cacheable(site: tuple[str, str, str]) -> None:
    base, _pub, _comm = site
    _, _, headers = _get(base, "/static/app.css")
    assert "max-age" in headers.get("cache-control", "")
    assert headers.get("etag")


def test_static_rejects_traversal_and_unknown_names(site: tuple[str, str, str]) -> None:
    """Static serving is a name allowlist: a traversal or unknown name 404s.

    The request value is matched by name against the import-time ``_STATIC_FILES``
    map and never joined into a path, so an encoded ``../`` escape misses the map
    rather than reading outside ``web/static`` (BUG-2; the py/path-injection
    alerts close because user input never reaches a path expression).
    """
    base, _pub, _comm = site
    # Encoded slashes keep the client from normalizing the traversal away, so it
    # reaches the handler as one segment — which is not a known static name.
    status, _b, _h = _get(base, "/static/..%2f..%2fpyproject.toml")
    assert status == 404
    status, _b, _h = _get(base, "/static/does-not-exist.css")
    assert status == 404
    # The one real static file still serves.
    status, _b, _h = _get(base, "/static/app.css")
    assert status == 200


# --- no-outing across every new surface -------------------------------------


def test_no_identity_leak_on_new_surfaces(site: tuple[str, str, str]) -> None:
    base, pub, _comm = site
    surfaces = [
        "/about",
        "/governance",
        "/how-it-works",
        "/proof",
        "/status",
        "/oai?verb=ListRecords&metadataPrefix=oai_dc",
        "/sitemap.xml",
        f"/record/{pub}/consent",
        f"/record/{pub}/file/flyer.txt",
    ]
    for path in surfaces:
        _, body, _ = _get(base, path, grant="boss")
        assert _SENTINEL not in body, f"identity leaked on {path}"


# --- residual: count side-channel gated to stewards (P2-2) -------------------


def test_healthz_counts_gated_to_steward(site: tuple[str, str, str]) -> None:
    base, _pub, _comm = site
    _, anon_body, _ = _get(base, "/healthz")
    anon = json.loads(anon_body)
    assert "fixity" not in anon  # outsider sees no counts (total size not leaked)
    assert anon["all_verified"] is True
    _, boss_body, _ = _get(base, "/healthz", grant="boss")
    boss = json.loads(boss_body)
    assert boss["fixity"]["bags_audited"] >= 2  # a steward sees the real counts
