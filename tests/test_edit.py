"""Tests for contributor edit/correction of a pending submission.

A contributor who kept their reference and code can *correct* a submission while it
is still pending review — change the title, account, visibility, or content warnings —
without a steward in the loop, because nothing is public yet and it is their own
content. These tests pin: the surface is gated like withdrawal; a load prefills the
current values; a save persists the change while preserving the record id and the
sealed identity; and a bad reference/code returns one neutral error (no oracle).
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
from ledger.access.grants import build_grant
from ledger.config import Config
from ledger.ingest import Archive
from ledger.models import AccessPolicy
from ledger.server import make_server

_SENTINEL = "EDIT-SENTINEL-DO-NOT-LEAK-2Q7P"
_VAULT_KEY = "0123456789abcdef0123456789abcdef0123456789a="
_CLAIM_KEY = "test-claim-key-please-change"


def _serve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    allow: bool = True,
    claim: str | None = _CLAIM_KEY,
) -> Iterator[tuple[Archive, str]]:
    monkeypatch.setenv("LEDGER_VAULT_KEY", _VAULT_KEY)
    if claim is not None:
        monkeypatch.setenv("LEDGER_CLAIM_SECRET", claim)
    else:
        monkeypatch.delenv("LEDGER_CLAIM_SECRET", raising=False)
    archive = Archive.init(Config.default("Edit Archive", tmp_path / "arc"))
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
            "title": "Original title",
            "account": "Original account.",
            "visibility": "community",
            "contributor_name": _SENTINEL,
        },
    )
    assert status == 200 and "Thank you" in body


def _id_and_token(archive: Archive) -> tuple[str, str]:
    records = archive._all_records()
    assert len(records) == 1
    rid = records[0].record_id
    return rid, consent.issue_claim_token(rid, _CLAIM_KEY.encode("utf-8"))


def test_edit_is_404_when_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gen = _serve(tmp_path, monkeypatch, allow=False)
    _archive, base = next(gen)
    try:
        assert _get(base, "/edit")[0] == 404
        assert _post(base, "/edit", {"ref": "x", "claim": "y"})[0] == 404
    finally:
        gen.close()


def test_load_prefills_the_current_values(server: tuple[Archive, str]) -> None:
    """A valid reference + code loads the submission's current values into the form."""
    archive, base = server
    _submit(base)
    rid, token = _id_and_token(archive)
    status, body = _post(base, "/edit", {"action": "load", "ref": rid, "claim": token})
    assert status == 200
    assert "Original title" in body
    assert "Original account." in body
    # The community radio is pre-selected from the record's current visibility.
    assert 'id="vis-community"' in body and "checked" in body


def test_save_updates_the_record_and_keeps_identity(server: tuple[Archive, str]) -> None:
    """Saving a correction rewrites the pending record but preserves its sealed identity."""
    archive, base = server
    _submit(base)
    rid, token = _id_and_token(archive)

    status, body = _post(
        base,
        "/edit",
        {
            "action": "save",
            "ref": rid,
            "claim": token,
            "title": "Corrected title",
            "account": "Corrected account.",
            "visibility": "public",
        },
    )
    assert status == 200
    assert "saved" in body.lower()

    record = archive.get(rid)
    assert record.title == "Corrected title"
    assert record.field_named("account").value == "Corrected account."
    # Visibility rode onto the account field; the record stays sealed-pending.
    assert record.field_named("account").policy is AccessPolicy.PUBLIC
    assert record.default_policy is AccessPolicy.SEALED_UNTIL
    # The sealed identity is preserved through the edit.
    assert record.identity_ref is not None
    unseal = build_grant("reviewer", identity_unseal=[record.identity_ref])
    assert archive.resolve_identity(rid, unseal).name == _SENTINEL


def test_bad_code_is_a_neutral_error_and_changes_nothing(server: tuple[Archive, str]) -> None:
    archive, base = server
    _submit(base)
    rid, _token = _id_and_token(archive)
    status, body = _post(
        base,
        "/edit",
        {"action": "save", "ref": rid, "claim": "claim:wrong", "title": "Hacked", "account": "x"},
    )
    assert status == 200
    assert 'role="alert"' in body
    assert archive.get(rid).title == "Original title"  # unchanged


def test_cannot_edit_once_no_longer_pending(server: tuple[Archive, str]) -> None:
    archive, base = server
    _submit(base)
    rid, token = _id_and_token(archive)
    from ledger import review

    review.SubmissionQueue(archive.logs_dir / "submission-queue.json").remove(rid)
    status, body = _post(
        base,
        "/edit",
        {"action": "save", "ref": rid, "claim": token, "title": "New", "account": "New."},
    )
    assert status == 200
    assert 'role="alert"' in body
    assert archive.get(rid).title == "Original title"


def test_save_validation_error_is_localized_and_keeps_the_record(
    server: tuple[Archive, str],
) -> None:
    archive, base = server
    _submit(base)
    rid, token = _id_and_token(archive)
    status, body = _post(
        base,
        "/edit?lang=es",
        {"action": "save", "ref": rid, "claim": token, "title": "", "account": ""},
    )
    assert status == 200
    assert "Se requiere un título." in body
    assert archive.get(rid).title == "Original title"
