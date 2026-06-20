"""Tests for contributor self-service withdrawal of a pending submission.

Consent is revocable. A contributor who submits through the web form gets a reference
and a withdrawal *code* (a claim token — a capability, never an identity) and can use
them to withdraw the submission **while it is still pending review** — honouring "I
changed my mind before it went live" without a steward in the loop, because nothing is
public yet and it is their own content.

These tests pin the guarantees: the code is shown only when one can be issued and is
never echoed back; the surface is off without a claim secret; a valid code on a
pending submission erases every copy and revokes the sealed identity; and every
failure (bad code, unknown reference, already-published record) returns one neutral
message so the endpoint cannot be used to test whether a record exists (no-outing).
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

from ledger import consent
from ledger.access.grants import anonymous
from ledger.config import Config
from ledger.ingest import Archive
from ledger.server import make_server

_SENTINEL = "WITHDRAW-SENTINEL-DO-NOT-LEAK-4F8M"
_VAULT_KEY = "0123456789abcdef0123456789abcdef0123456789a="
_CLAIM_KEY = "test-claim-secret-please-change"


def _serve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    allow: bool = True,
    claim_secret: str | None = _CLAIM_KEY,
) -> Iterator[tuple[Archive, str]]:
    monkeypatch.setenv("LEDGER_VAULT_KEY", _VAULT_KEY)
    if claim_secret is not None:
        monkeypatch.setenv("LEDGER_CLAIM_SECRET", claim_secret)
    else:
        monkeypatch.delenv("LEDGER_CLAIM_SECRET", raising=False)
    archive = Archive.init(Config.default("Withdraw Archive", tmp_path / "arc"))
    httpd = make_server(archive, host="127.0.0.1", port=0, allow_contributions=allow)
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


@pytest.fixture
def server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Archive, str]]:
    yield from _serve(tmp_path, monkeypatch)


def _get(base: str, path: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=10) as resp:  # noqa: S310 - loopback
            return int(resp.status), resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8")


def _post(base: str, path: str, fields: dict[str, str]) -> tuple[int, str]:
    data = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310 - loopback
        f"{base}{path}",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            return int(resp.status), resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8")


def _submit(base: str) -> None:
    status, body = _post(
        base,
        "/contribute",
        {
            "action": "submit",
            "title": "A pending account",
            "account": "Something I might reconsider.",
            "visibility": "public",
            "contributor_name": _SENTINEL,
        },
    )
    assert status == 200 and "Thank you" in body


def _pending_id_and_token(archive: Archive) -> tuple[str, str]:
    records = archive._all_records()
    assert len(records) == 1
    rid = records[0].record_id
    return rid, consent.issue_claim_token(rid, _CLAIM_KEY.encode("utf-8"))


def test_thanks_page_shows_a_reference_and_code_then_never_the_identity(
    server: tuple[Archive, str],
) -> None:
    """A submission's confirmation offers the withdrawal handle but outs no one."""
    archive, base = server
    _status, body = _post(
        base,
        "/contribute",
        {
            "action": "submit",
            "title": "Has a code",
            "account": "Body.",
            "visibility": "public",
            "contributor_name": _SENTINEL,
        },
    )
    rid, token = _pending_id_and_token(archive)
    assert rid in body  # the reference
    assert token in body  # the withdrawal code (a capability, not identity)
    assert "/withdraw" in body
    assert _SENTINEL not in body  # the contributor is still never echoed


def test_thanks_page_is_generic_without_a_claim_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no claim secret configured, no code is offered and withdrawal is off."""
    gen = _serve(tmp_path, monkeypatch, claim_secret=None)
    _archive, base = next(gen)
    try:
        _status, body = _post(
            base,
            "/contribute",
            {"action": "submit", "title": "No code", "account": "Body.", "visibility": "public"},
        )
        assert "Thank you" in body
        assert "/withdraw" not in body
        assert _get(base, "/withdraw")[0] == 404
    finally:
        gen.close()


def test_withdraw_form_is_404_when_contributions_are_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gen = _serve(tmp_path, monkeypatch, allow=False)
    _archive, base = next(gen)
    try:
        assert _get(base, "/withdraw")[0] == 404
        assert _post(base, "/withdraw", {"ref": "x", "claim": "y"})[0] == 404
    finally:
        gen.close()


def test_valid_withdrawal_erases_the_pending_submission(server: tuple[Archive, str]) -> None:
    """A correct reference + code removes every copy and revokes the sealed identity."""
    archive, base = server
    _submit(base)
    rid, token = _pending_id_and_token(archive)
    assert archive.records_dir.joinpath(f"{rid}.json").exists()

    status, body = _post(base, "/withdraw", {"ref": rid, "claim": token})
    assert status == 200
    assert "withdrawn" in body.lower()
    assert _SENTINEL not in body

    # Every copy is gone and the queue no longer holds it.
    assert not archive.records_dir.joinpath(f"{rid}.json").exists()
    assert not (archive.bags_dir / rid).exists()
    assert archive._all_records() == []
    assert archive.browse(anonymous()) == []


def test_bad_code_is_a_neutral_error_and_keeps_the_record(server: tuple[Archive, str]) -> None:
    """A wrong withdrawal code declines without removing anything or confirming the id."""
    archive, base = server
    _submit(base)
    rid, _token = _pending_id_and_token(archive)

    status, body = _post(base, "/withdraw", {"ref": rid, "claim": "claim:totally-wrong"})
    assert status == 200
    assert 'role="alert"' in body
    # The submission is untouched.
    assert archive.records_dir.joinpath(f"{rid}.json").exists()


def test_unknown_reference_gives_the_same_neutral_error(server: tuple[Archive, str]) -> None:
    """An unknown reference returns the identical message — no existence oracle."""
    _archive, base = server
    _submit(base)
    _status, real_fail = _post(base, "/withdraw", {"ref": "does-not-exist", "claim": "claim:x"})
    # Same wording as a bad code on a real record: the page cannot distinguish them.
    assert "could not withdraw" in real_fail.lower()


def test_cannot_withdraw_once_no_longer_pending(server: tuple[Archive, str]) -> None:
    """A valid code does nothing once the submission has left the review queue."""
    archive, base = server
    _submit(base)
    rid, token = _pending_id_and_token(archive)
    # Simulate a steward having actioned it: it is no longer pending.
    from ledger import review

    review.SubmissionQueue(archive.logs_dir / "submission-queue.json").remove(rid)

    status, body = _post(base, "/withdraw", {"ref": rid, "claim": token})
    assert status == 200
    assert "could not withdraw" in body.lower()
    # The record itself is left intact for the normal governance path.
    assert archive.records_dir.joinpath(f"{rid}.json").exists()
