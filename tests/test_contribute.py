"""Tests for the contributor submission surface (``ledger.contribute`` + the server).

The contribution path is the one place an ordinary contributor can add a record, so
these tests pin its two safety guarantees: a submission is **sealed-pending** (never
published by inaction — Hard Rule 2), and a contributor's contact is **sealed and
never echoed** (the no-outing rule — Hard Rule 1). They also confirm the write path
is **off by default** — a read-only deployment never grows a /contribute endpoint by
surprise.
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

from ledger import contribute
from ledger.access.grants import anonymous, build_grant
from ledger.config import Config
from ledger.errors import LedgerError
from ledger.ingest import Archive, serialize_record
from ledger.models import AccessPolicy
from ledger.server import make_server

_SENTINEL = "SENTINEL-CONTRIBUTE-DO-NOT-LEAK-8H3W"
_VAULT_KEY = "0123456789abcdef0123456789abcdef0123456789a="


# --- unit: parsing & validation --------------------------------------------


@pytest.mark.disclosure
def test_parse_submission_is_sealed_pending_by_default(tmp_path: Path) -> None:
    """A valid submission becomes a sealed-pending record, whatever visibility is asked."""
    config = Config.default("Parse Archive", tmp_path / "arc")
    form = {"title": "A march", "account": "What happened.", "visibility": "public"}
    sub = contribute.parse_submission(form, config)
    # The record never lists publicly by inaction: the default policy stays sealed.
    assert sub.record.default_policy is AccessPolicy.SEALED_UNTIL
    # The contributor's requested visibility rides on the account field for later.
    account = next(f for f in sub.record.fields if f.name == "account")
    assert account.policy is AccessPolicy.PUBLIC
    assert sub.identity is None


@pytest.mark.disclosure
def test_parse_submission_filters_content_warnings_to_vocabulary(tmp_path: Path) -> None:
    """Only content warnings in the archive's controlled vocabulary are kept."""
    config = Config.default("CW Archive", tmp_path / "arc")
    known = config.content_warnings[0]
    form = {
        "title": "t",
        "account": "a",
        f"cw_{known}": "1",
        "cw_not-a-real-tag": "1",  # must be ignored
    }
    sub = contribute.parse_submission(form, config)
    assert sub.record.content_warnings == [known]


@pytest.mark.disclosure
def test_parse_submission_seals_identity_when_contact_given(tmp_path: Path) -> None:
    """A name/contact becomes an identity to seal; it is not placed on the record."""
    config = Config.default("Id Archive", tmp_path / "arc")
    form = {"title": "t", "account": "a", "contributor_name": _SENTINEL}
    sub = contribute.parse_submission(form, config)
    assert sub.identity is not None
    assert sub.identity.name == _SENTINEL
    # The sentinel is nowhere in the record itself (it goes only to the vault).
    assert _SENTINEL not in serialize_record(sub.record)


@pytest.mark.disclosure
@pytest.mark.parametrize("missing", [{"account": "a"}, {"title": "t"}, {}])
def test_parse_submission_rejects_missing_required_fields(
    tmp_path: Path, missing: dict[str, str]
) -> None:
    """A submission with no title or no account is rejected with a generic error."""
    config = Config.default("Reject Archive", tmp_path / "arc")
    with pytest.raises(LedgerError):
        contribute.parse_submission(missing, config)


@pytest.mark.disclosure
def test_parse_submission_rejects_oversized_account(tmp_path: Path) -> None:
    """An over-long account is rejected rather than stored (robustness)."""
    config = Config.default("Big Archive", tmp_path / "arc")
    form = {"title": "t", "account": "x" * 20_001}
    with pytest.raises(LedgerError):
        contribute.parse_submission(form, config)


# --- integration: the live server -------------------------------------------


def _server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, allow: bool
) -> Iterator[tuple[Archive, str]]:
    monkeypatch.setenv("LEDGER_VAULT_KEY", _VAULT_KEY)
    archive = Archive.init(Config.default("Contribute Archive", tmp_path / "arc"))
    httpd = make_server(archive, host="127.0.0.1", port=0, allow_contributions=allow)
    port = httpd.server_address[1]
    base = f"http://127.0.0.1:{int(port)}"
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
def open_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Archive, str]]:
    """A running server with the contribution surface ENABLED and a vault key set."""
    yield from _server(tmp_path, monkeypatch, allow=True)


@pytest.fixture
def closed_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Archive, str]]:
    """A running server with the contribution surface DISABLED (the default)."""
    yield from _server(tmp_path, monkeypatch, allow=False)


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
        f"{base}{path}",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            return int(resp.status), resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8")


@pytest.mark.accessibility
def test_contribute_form_is_served_and_accessible_when_enabled(
    open_server: tuple[Archive, str],
) -> None:
    """``GET /contribute`` returns the accessible form when contributions are on."""
    _archive, base = open_server
    status, body = _get(base, "/contribute")
    assert status == 200
    assert '<main id="main"' in body
    assert body.count("<h1>") == 1
    assert 'action="/contribute"' in body
    assert 'for="title"' in body and 'id="title"' in body
    assert 'for="account"' in body and 'id="account"' in body


def test_contribution_flow_is_localized(open_server: tuple[Archive, str]) -> None:
    """A Spanish reader gets the form chrome and the confirmation in Spanish (I2)."""
    _archive, base = open_server
    _status, form = _get(base, "/contribute?lang=es")
    # The write path a non-English contributor most needs is now in their language.
    assert "Contribuir al archivo" in form  # heading
    assert "Su relato" in form  # the account label
    assert "Enviar para revisión" in form  # submit button
    assert "Contribute to the archive" not in form  # English equivalents gone
    # The confirmation page is localized too (cookie from the form carries the choice).
    _status, thanks = _post(
        base,
        "/contribute?lang=es",
        {"action": "submit", "title": "Hola", "account": "Un relato.", "visibility": "public"},
    )
    assert "Gracias" in thanks


@pytest.mark.disclosure
def test_contribute_is_404_when_disabled(closed_server: tuple[Archive, str]) -> None:
    """The write path is off by default: both GET and POST 404 when not enabled."""
    _archive, base = closed_server
    assert _get(base, "/contribute")[0] == 404
    assert _post(base, "/contribute", {"title": "t", "account": "a"})[0] == 404


@pytest.mark.disclosure
def test_submission_lands_sealed_pending_and_outs_no_one(
    open_server: tuple[Archive, str],
) -> None:
    """A POST seals the record pending review, seals the identity, and echoes nothing."""
    archive, base = open_server
    status, body = _post(
        base,
        "/contribute",
        {
            "action": "submit",
            "title": "Thursday gathering",
            "account": "A public account of the night.",
            "visibility": "public",
            "contributor_name": _SENTINEL,
        },
    )
    assert status == 200
    # The confirmation page never reflects the contributor's identity back.
    assert _SENTINEL not in body
    assert "Thank you" in body

    # The record exists but is sealed-pending: anonymous browse cannot see it.
    assert archive.browse(anonymous()) == []

    # Exactly one record was stored, and it carries a sealed identity ref.
    records = archive._all_records()
    assert len(records) == 1
    record = records[0]
    assert record.default_policy is AccessPolicy.SEALED_UNTIL
    assert record.identity_ref is not None
    # The sentinel is nowhere on the record manifest (it lives only in the vault).
    assert _SENTINEL not in serialize_record(record)

    # The identity resolves only under an explicit unseal grant (the seal is real).
    unseal = build_grant("reviewer", identity_unseal=[record.identity_ref])
    resolved = archive.resolve_identity(record.record_id, unseal)
    assert resolved.name == _SENTINEL


@pytest.mark.disclosure
def test_submission_without_contact_needs_no_identity(
    open_server: tuple[Archive, str],
) -> None:
    """A contribution with no contact stores a record with no identity ref."""
    archive, base = open_server
    status, _body = _post(
        base,
        "/contribute",
        {"action": "submit", "title": "Anon note", "account": "No contact given."},
    )
    assert status == 200
    record = archive._all_records()[0]
    assert record.identity_ref is None


@pytest.mark.disclosure
def test_invalid_submission_re_renders_form_with_error(
    open_server: tuple[Archive, str],
) -> None:
    """A submission missing required fields gets a 400 and a re-rendered form."""
    _archive, base = open_server
    status, body = _post(base, "/contribute", {"action": "submit", "title": "", "account": ""})
    assert status == 400
    assert 'role="alert"' in body
    assert 'action="/contribute"' in body  # the form is shown again to fix it


@pytest.mark.disclosure
def test_preview_shows_stranger_view_without_storing(
    open_server: tuple[Archive, str],
) -> None:
    """The default action previews what a stranger sees and stores nothing."""
    archive, base = open_server
    status, body = _post(
        base,
        "/contribute",
        {
            "action": "preview",
            "title": "Thursday gathering",
            "account": "A public account of the night.",
            "visibility": "public",
            "contributor_name": _SENTINEL,
        },
    )
    assert status == 200
    panel = _preview_panel(body)
    # A public submission's account text appears in the stranger panel...
    assert "A public account of the night." in panel
    # ...but the sealed contact never appears in the panel, and nothing was stored.
    assert _SENTINEL not in panel
    assert archive._all_records() == []
    # The form below is re-filled so the contributor never loses what they typed.
    assert 'value="submit"' in body
    assert f'value="{_SENTINEL}"' in body  # prefilled in their own contact field


def _preview_panel(body: str) -> str:
    """Extract just the stranger-view preview panel from the rendered page."""
    start = body.index('<section class="preview"')
    return body[start : body.index("</section>", start)]


@pytest.mark.disclosure
def test_preview_of_sealed_shows_a_stranger_sees_nothing(
    open_server: tuple[Archive, str],
) -> None:
    """Previewing a community/sealed submission says a stranger sees nothing."""
    _archive, base = open_server
    status, body = _post(
        base,
        "/contribute",
        {
            "action": "preview",
            "title": "Held back",
            "account": "Secret account.",
            "visibility": "community",
        },
    )
    assert status == 200
    panel = _preview_panel(body)
    assert "A stranger sees nothing" in panel
    assert "Secret account." not in panel  # the account is not exposed to a stranger


# --- file upload (backlog A2) ----------------------------------------------

# A minimal payload carrying the PNG signature; sniffing reads only the prefix.
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _post_multipart(
    base: str,
    path: str,
    fields: dict[str, str],
    *,
    file: tuple[str, str, bytes] | None = None,
) -> tuple[int, str]:
    """POST a ``multipart/form-data`` body, optionally with one ``(name, type, bytes)`` file."""
    boundary = "----ledgerTestBoundary7e3"
    chunks: list[bytes] = []
    for key, value in fields.items():
        chunks.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
            f"{value}\r\n".encode()
        )
    if file is not None:
        filename, content_type, data = file
        chunks.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="upload"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n".encode()
            + data
            + b"\r\n"
        )
    chunks.append(f"--{boundary}--\r\n".encode())
    body = b"".join(chunks)
    req = urllib.request.Request(  # noqa: S310 - loopback
        f"{base}{path}",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            return int(resp.status), resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8")


@pytest.mark.accessibility
def test_form_offers_an_accessible_file_input(open_server: tuple[Archive, str]) -> None:
    """The form is multipart and carries a labelled file input (backlog A2)."""
    _archive, base = open_server
    _status, body = _get(base, "/contribute")
    assert 'enctype="multipart/form-data"' in body
    assert 'type="file"' in body and 'id="upload"' in body
    assert 'for="upload"' in body  # the input is labelled


@pytest.mark.disclosure
def test_valid_upload_is_ingested_sealed_pending(open_server: tuple[Archive, str]) -> None:
    """A real image attaches as a sealed-pending payload with a server-sniffed type."""
    archive, base = open_server
    status, body = _post_multipart(
        base,
        "/contribute",
        {
            "action": "submit",
            "title": "A photographed flyer",
            "account": "A scan of a community flyer.",
            "visibility": "public",
        },
        # The filename lies (".txt") and the declared type lies; the bytes are a PNG.
        file=("flyer.txt", "text/plain", _PNG),
    )
    assert status == 200
    assert "Thank you" in body

    # Nothing is public by inaction, and exactly one record was stored.
    assert archive.browse(anonymous()) == []
    records = archive._all_records()
    assert len(records) == 1
    record = records[0]
    assert record.default_policy is AccessPolicy.SEALED_UNTIL

    # The payload is stored with the server-DETERMINED type, not the client's claim,
    # and follows the record's sealed-pending policy until a steward reviews it.
    assert len(record.payloads) == 1
    payload = record.payloads[0]
    assert payload.media_type == "image/png"
    assert payload.policy is AccessPolicy.SEALED_UNTIL
    assert payload.size_bytes == len(_PNG)


@pytest.mark.disclosure
def test_dot_dot_filename_is_sanitised_not_crashing(open_server: tuple[Archive, str]) -> None:
    """A '..' upload filename is stored under a safe name, never crashing the handler.

    Security review: '..' once survived filename sanitisation and the handler wrote
    to ``tmpdir / '..'`` (a directory) — an unhandled error. It must now ingest under
    a safe filename instead."""
    archive, base = open_server
    status, body = _post_multipart(
        base,
        "/contribute",
        {
            "action": "submit",
            "title": "Tricky name",
            "account": "An account.",
            "visibility": "public",
        },
        file=("..", "image/png", _PNG),
    )
    assert status == 200
    assert "Thank you" in body
    record = archive._all_records()[0]
    assert len(record.payloads) == 1
    # The stored payload carries a safe, non-traversing filename.
    assert record.payloads[0].filename == "file"


@pytest.mark.disclosure
def test_forged_file_type_is_rejected_and_stores_nothing(
    open_server: tuple[Archive, str],
) -> None:
    """A file whose bytes are not an allowlisted type is refused; nothing is stored."""
    archive, base = open_server
    status, body = _post_multipart(
        base,
        "/contribute",
        {
            "action": "submit",
            "title": "Not really an image",
            "account": "An account.",
            "visibility": "public",
        },
        # A .png name and an image content-type, but the bytes are not any known type.
        file=("evil.png", "image/png", b"#!/bin/sh\nrm -rf /\n"),
    )
    assert status == 400
    assert 'role="alert"' in body  # the form comes back with an error to fix
    assert archive._all_records() == []  # the forged upload was never stored


@pytest.mark.disclosure
def test_oversized_upload_is_rejected(
    open_server: tuple[Archive, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file larger than the cap is refused before anything is stored."""
    from ledger import upload

    # Shrink the cap so the test stays small; the body stays under the read cap
    # (cap + 1 MiB), so the friendly size error is what rejects it.
    monkeypatch.setattr(upload, "MAX_UPLOAD_BYTES", 1024)
    archive, base = open_server
    status, body = _post_multipart(
        base,
        "/contribute",
        {
            "action": "submit",
            "title": "Too big",
            "account": "An account.",
            "visibility": "public",
        },
        file=("big.png", "image/png", _PNG + b"\x00" * 4096),
    )
    assert status == 400
    assert 'role="alert"' in body
    assert archive._all_records() == []


@pytest.mark.disclosure
def test_multipart_text_only_submission_still_works(
    open_server: tuple[Archive, str],
) -> None:
    """A multipart POST with no file behaves exactly like the text-only path."""
    archive, base = open_server
    status, body = _post_multipart(
        base,
        "/contribute",
        {
            "action": "submit",
            "title": "Text only",
            "account": "No file attached.",
            "visibility": "community",
        },
    )
    assert status == 200
    assert "Thank you" in body
    records = archive._all_records()
    assert len(records) == 1
    assert records[0].payloads == []
