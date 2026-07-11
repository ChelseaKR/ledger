"""End-to-end tests for authenticated, revocable capability grants (FIX-02).

The ``X-Ledger-Grant`` header is an HMAC-signed bearer token
(``subject:expiry:mac`` under ``LEDGER_GRANT_SECRET``). These tests prove the full
loop: a forged, expired, revoked, or unsigned header is indistinguishable from no
header at all (deny by default), while a valid token authenticates a
pre-provisioned subject — and the ``ledger grant`` CLI mints and revokes those
tokens without ever printing the secret.

The observable "did authentication take effect" signal is the disclosure shape of
``GET /api/record/{id}``: a trusted *insider* (community member or steward) sees a
``withheld`` key naming why parts are withheld, while an *outsider* (anonymous)
sees only a ``withheld_count``. So an authenticated insider token flips the JSON
shape; every failure mode leaves it in the anonymous shape.
"""

from __future__ import annotations

import json
import threading
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from http.server import HTTPServer
from io import StringIO
from pathlib import Path

import pytest

from ledger.access.grants import (
    issue_grant_token,
    load_revocations,
    verify_grant_token,
)
from ledger.config import Config
from ledger.identity import ContributorIdentity
from ledger.ingest import Archive
from ledger.models import AccessPolicy, DublinCore, Field, Record
from ledger.server import _GRANT_HEADER, make_server

_SECRET = b"a-test-grant-signing-secret-0123456789"
_SUBJECT = "curator"
_VAULT_KEY = b"0123456789abcdef0123456789abcdef0123456789a="
_NOW = "2026-06-16T12:00:00Z"
_PAST = "2000-01-01T00:00:00Z"
_FUTURE = "2999-01-01T00:00:00Z"


# --- fixtures ---------------------------------------------------------------


def _build_archive(tmp_path: Path) -> tuple[Archive, str]:
    """Init an archive with one public record that also seals a field + identity.

    The sealed field guarantees the disclosed record has a *withheld* part, so the
    insider-vs-outsider JSON shape difference is meaningful.
    """
    config = Config.default("Grant Auth Test Archive", tmp_path / "arc")
    archive = Archive.init(config)
    record = Record(
        title="Community notes",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(title=["Community notes"], publisher=[config.archive_name]),
        fields=[
            Field(name="story", value="A public account.", policy=AccessPolicy.PUBLIC),
            Field(name="roster", value="sealed roster", policy=AccessPolicy.SEALED_UNTIL),
        ],
    )
    archive.ingest(
        {},
        record,
        identity=ContributorIdentity(name="A contributor"),
        vault_key=_VAULT_KEY,
        agent="grant-auth-test",
        now=_NOW,
    )
    return archive, record.record_id


def _write_grants(tmp_path: Path) -> Path:
    """A grants file provisioning ``curator`` as a community-member insider."""
    grants_path = tmp_path / "grants.json"
    grants_path.write_text(
        json.dumps({_SUBJECT: {"levels": ["public", "community"]}}),
        encoding="utf-8",
    )
    return grants_path


@contextmanager
def _running(httpd: HTTPServer) -> Iterator[str]:
    """Run ``httpd`` on a daemon thread over loopback; yield its base URL."""
    host, port = httpd.server_address[0], httpd.server_address[1]
    host_s = host.decode("ascii") if isinstance(host, (bytes, bytearray)) else str(host)
    base = f"http://{host_s}:{int(port)}"
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


def _get(base: str, path: str, *, token: str | None = None) -> tuple[int, str]:
    """GET ``base + path`` over loopback, optionally with an ``X-Ledger-Grant`` token."""
    request = urllib.request.Request(f"{base}{path}")  # noqa: S310 - loopback URL we built
    if token is not None:
        request.add_header(_GRANT_HEADER, token)
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
        return int(response.status), response.read().decode("utf-8")


def _authenticated(base: str, rid: str, token: str | None) -> bool:
    """Whether the API record read for ``rid`` came back in the *insider* shape."""
    status, body = _get(base, f"/api/record/{rid}", token=token)
    assert status == 200
    data = json.loads(body)
    return "withheld" in data


# --- server: forgery, expiry, revocation, no-secret -------------------------


def test_no_header_is_anonymous(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A request with no grant header reads in the outsider shape."""
    monkeypatch.setenv("LEDGER_GRANT_SECRET", _SECRET.decode())
    archive, rid = _build_archive(tmp_path)
    httpd = make_server(archive, host="127.0.0.1", port=0, grants_path=_write_grants(tmp_path))
    with _running(httpd) as base:
        assert _authenticated(base, rid, None) is False


def test_valid_token_authenticates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A valid, unexpired token for a provisioned subject flips to the insider shape."""
    monkeypatch.setenv("LEDGER_GRANT_SECRET", _SECRET.decode())
    archive, rid = _build_archive(tmp_path)
    httpd = make_server(archive, host="127.0.0.1", port=0, grants_path=_write_grants(tmp_path))
    token = issue_grant_token(_SUBJECT, _SECRET, expires_at=_FUTURE)
    with _running(httpd) as base:
        assert _authenticated(base, rid, token) is True


def test_forged_mac_is_anonymous(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A tampered MAC yields a response identical to sending no header at all."""
    monkeypatch.setenv("LEDGER_GRANT_SECRET", _SECRET.decode())
    archive, rid = _build_archive(tmp_path)
    httpd = make_server(archive, host="127.0.0.1", port=0, grants_path=_write_grants(tmp_path))
    good = issue_grant_token(_SUBJECT, _SECRET, expires_at=_FUTURE)
    forged = good[:-1] + ("0" if good[-1] != "0" else "1")  # flip one MAC hex digit
    with _running(httpd) as base:
        none_status, none_body = _get(base, f"/api/record/{rid}", token=None)
        forged_status, forged_body = _get(base, f"/api/record/{rid}", token=forged)
    assert (forged_status, forged_body) == (none_status, none_body)
    assert "withheld" not in json.loads(forged_body)


def test_wrong_secret_token_is_anonymous(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A token signed under a different secret does not authenticate."""
    monkeypatch.setenv("LEDGER_GRANT_SECRET", _SECRET.decode())
    archive, rid = _build_archive(tmp_path)
    httpd = make_server(archive, host="127.0.0.1", port=0, grants_path=_write_grants(tmp_path))
    token = issue_grant_token(_SUBJECT, b"a-totally-different-secret", expires_at=_FUTURE)
    with _running(httpd) as base:
        assert _authenticated(base, rid, token) is False


def test_expired_token_is_anonymous(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A token whose expiry is in the past does not authenticate; a future one does."""
    monkeypatch.setenv("LEDGER_GRANT_SECRET", _SECRET.decode())
    archive, rid = _build_archive(tmp_path)
    httpd = make_server(archive, host="127.0.0.1", port=0, grants_path=_write_grants(tmp_path))
    expired = issue_grant_token(_SUBJECT, _SECRET, expires_at=_PAST)
    live = issue_grant_token(_SUBJECT, _SECRET, expires_at=_FUTURE)
    with _running(httpd) as base:
        assert _authenticated(base, rid, expired) is False
        assert _authenticated(base, rid, live) is True


def test_revoked_subject_is_anonymous(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A valid token for a revoked subject does not authenticate; unrevoking restores it."""
    monkeypatch.setenv("LEDGER_GRANT_SECRET", _SECRET.decode())
    archive, rid = _build_archive(tmp_path)
    grants_path = _write_grants(tmp_path)
    revocations_path = tmp_path / "revocations.json"
    revocations_path.write_text(json.dumps([_SUBJECT]), encoding="utf-8")
    httpd = make_server(
        archive,
        host="127.0.0.1",
        port=0,
        grants_path=grants_path,
        revocations_path=revocations_path,
    )
    token = issue_grant_token(_SUBJECT, _SECRET, expires_at=_FUTURE)
    with _running(httpd) as base:
        # Revoked: the valid MAC is ignored.
        assert _authenticated(base, rid, token) is False
        # Un-revoke on disk (as `grant unrevoke` would) — the same token works
        # again on the very next request, with no server restart.
        revocations_path.write_text(json.dumps([]), encoding="utf-8")
        assert _authenticated(base, rid, token) is True


def test_revocation_takes_effect_without_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ledger grant revoke` while the server is running retracts on the next request.

    Revocation is the emergency brake for a leaked or compelled token; it is only
    an *immediate* retraction if the server consults the file per request rather
    than caching a set at startup — this is the regression test for exactly that.
    """
    monkeypatch.setenv("LEDGER_GRANT_SECRET", _SECRET.decode())
    archive, rid = _build_archive(tmp_path)
    grants_path = _write_grants(tmp_path)
    # No revocations file exists yet — the default beside the grants file is used.
    httpd = make_server(archive, host="127.0.0.1", port=0, grants_path=grants_path)
    token = issue_grant_token(_SUBJECT, _SECRET, expires_at=_FUTURE)
    with _running(httpd) as base:
        assert _authenticated(base, rid, token) is True
        # Revoke while the server is live (what `grant revoke --revocations` writes).
        (tmp_path / "revocations.json").write_text(json.dumps([_SUBJECT]), encoding="utf-8")
        assert _authenticated(base, rid, token) is False


def test_unreadable_revocation_list_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A revocation list that turns to garbage revokes everyone, not no one.

    If the file exists but cannot be parsed, the safe reading is "cannot determine
    who is revoked" — so every token collapses to anonymous (fail closed), rather
    than silently un-revoking every subject (fail open).
    """
    monkeypatch.setenv("LEDGER_GRANT_SECRET", _SECRET.decode())
    archive, rid = _build_archive(tmp_path)
    grants_path = _write_grants(tmp_path)
    revocations_path = tmp_path / "revocations.json"
    revocations_path.write_text("[]", encoding="utf-8")
    httpd = make_server(
        archive,
        host="127.0.0.1",
        port=0,
        grants_path=grants_path,
        revocations_path=revocations_path,
    )
    token = issue_grant_token(_SUBJECT, _SECRET, expires_at=_FUTURE)
    with _running(httpd) as base:
        assert _authenticated(base, rid, token) is True
        revocations_path.write_text("{ not json", encoding="utf-8")
        assert _authenticated(base, rid, token) is False


def test_concurrent_grant_uses_all_audited(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """N concurrent authenticated requests leave exactly N grant-use audit lines.

    The grant-use log is a read-modify-write of one JSON file; without
    serialization the threaded server could interleave two appends and lose an
    audit line. Lost audit lines are unaccounted privileged access — the exact
    thing the log exists to prevent.
    """
    monkeypatch.setenv("LEDGER_GRANT_SECRET", _SECRET.decode())
    archive, rid = _build_archive(tmp_path)
    httpd = make_server(archive, host="127.0.0.1", port=0, grants_path=_write_grants(tmp_path))
    token = issue_grant_token(_SUBJECT, _SECRET, expires_at=_FUTURE)
    request_count = 8
    errors: list[BaseException] = []

    def _hit(base: str) -> None:
        try:
            assert _authenticated(base, rid, token) is True
        except BaseException as exc:  # surfaced to the main thread below
            errors.append(exc)

    with _running(httpd) as base:
        threads = [
            threading.Thread(target=_hit, args=(base,), daemon=True) for _ in range(request_count)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
    assert not errors
    log_path = archive.logs_dir / "grant-uses.premis.json"
    log_data = json.loads(log_path.read_text(encoding="utf-8"))
    # FIX-06: logs are a schema-versioned, hash-chained envelope, not a bare list.
    assert len(log_data["entries"]) == request_count


def test_no_secret_configured_rejects_every_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no LEDGER_GRANT_SECRET set, even a correctly-signed token is anonymous."""
    monkeypatch.delenv("LEDGER_GRANT_SECRET", raising=False)
    archive, rid = _build_archive(tmp_path)
    httpd = make_server(archive, host="127.0.0.1", port=0, grants_path=_write_grants(tmp_path))
    token = issue_grant_token(_SUBJECT, _SECRET, expires_at=_FUTURE)
    with _running(httpd) as base:
        assert _authenticated(base, rid, token) is False


def test_grant_use_is_audited_without_the_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An honoured grant use is logged with the subject + route class, never the token."""
    monkeypatch.setenv("LEDGER_GRANT_SECRET", _SECRET.decode())
    archive, rid = _build_archive(tmp_path)
    httpd = make_server(archive, host="127.0.0.1", port=0, grants_path=_write_grants(tmp_path))
    token = issue_grant_token(_SUBJECT, _SECRET, expires_at=_FUTURE)
    with _running(httpd) as base:
        assert _authenticated(base, rid, token) is True
    log_path = archive.logs_dir / "grant-uses.premis.json"
    assert log_path.exists()
    log_text = log_path.read_text(encoding="utf-8")
    assert _SUBJECT in log_text
    assert "api" in log_text  # the route class
    assert token not in log_text  # the sealed bearer value never lands in the log


# --- token helpers: unit level ----------------------------------------------


def test_verify_round_trip_and_failures() -> None:
    """The helper accepts its own token and rejects every tampering."""
    token = issue_grant_token(_SUBJECT, _SECRET, expires_at=_FUTURE)
    assert verify_grant_token(token, _SECRET, now=_NOW) == _SUBJECT
    # Empty secret => None (fail closed).
    assert verify_grant_token(token, b"", now=_NOW) is None
    # Malformed token => None, not an exception.
    assert verify_grant_token("not-a-token", _SECRET, now=_NOW) is None
    assert verify_grant_token("a:b", _SECRET, now=_NOW) is None
    # Subject with a colon survives the round trip (base64 field encoding).
    weird = "team:alpha:v2"
    weird_token = issue_grant_token(weird, _SECRET, expires_at="")
    assert verify_grant_token(weird_token, _SECRET, now=_NOW) == weird


def test_verify_uses_constant_time_compare(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verification goes through :func:`hmac.compare_digest` (no early-exit compare)."""
    import ledger.access.grants as grants_mod

    calls: list[int] = []
    real = grants_mod.hmac.compare_digest

    def _counting(a: object, b: object) -> bool:
        calls.append(1)
        return real(a, b)

    monkeypatch.setattr(grants_mod.hmac, "compare_digest", _counting)
    token = issue_grant_token(_SUBJECT, _SECRET, expires_at=_FUTURE)
    assert verify_grant_token(token, _SECRET, now=_NOW) == _SUBJECT
    assert calls, "verify_grant_token must compare the MAC with hmac.compare_digest"


# --- CLI round trip ---------------------------------------------------------


def test_cli_grant_issue_prints_verifiable_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`grant issue` prints a token that verifies, and never prints the secret."""
    from ledger import cli

    monkeypatch.setenv("LEDGER_GRANT_SECRET", _SECRET.decode())
    rc = cli.main(["grant", "issue", "--subject", _SUBJECT, "--expires-at", _FUTURE])
    assert rc == 0
    out = capsys.readouterr().out
    token = out.strip()
    assert verify_grant_token(token, _SECRET, now=_NOW) == _SUBJECT
    assert _SECRET.decode() not in out  # the signing secret is never echoed


def test_cli_grant_issue_requires_secret(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`grant issue` fails cleanly when no secret is configured (no unsigned token)."""
    from ledger import cli

    monkeypatch.delenv("LEDGER_GRANT_SECRET", raising=False)
    rc = cli.main(["grant", "issue", "--subject", _SUBJECT])
    assert rc != 0
    assert "LEDGER_GRANT_SECRET" in capsys.readouterr().err


def test_cli_grant_revoke_writes_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`grant revoke` writes the subject into the revocations file."""
    from ledger import cli

    revocations_path = tmp_path / "revocations.json"
    rc = cli.main(
        ["grant", "revoke", "--subject", _SUBJECT, "--revocations", str(revocations_path)]
    )
    assert rc == 0
    assert load_revocations(revocations_path) == {_SUBJECT}
    capsys.readouterr()  # drain


def test_cli_grant_list_flags_revoked(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`grant list` shows provisioned subjects and marks the revoked ones."""
    from ledger import cli

    grants_path = _write_grants(tmp_path)
    revocations_path = tmp_path / "revocations.json"
    revocations_path.write_text(json.dumps([_SUBJECT]), encoding="utf-8")
    rc = cli.main(
        [
            "grant",
            "list",
            "--grants",
            str(grants_path),
            "--revocations",
            str(revocations_path),
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert _SUBJECT in out
    assert "[revoked]" in out
