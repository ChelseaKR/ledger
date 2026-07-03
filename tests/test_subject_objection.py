"""Tests for named-subject consent standing (RM12/EXP-04) over the live server.

A record may name people who did not contribute it. At ingest the contributor can
declare how many people the record names; the server mints one *subject token* per
person (a capability, never an identity), shows the clear tokens exactly once on the
contribution receipt for out-of-band hand-off, and persists only SHA-256 hashes of
them. A person who later holds their token can file a *verified* objection that a
steward records with a time-bound response window.

These tests pin the guarantees end to end:

* contributing with ``named_subjects_count=2`` shows two subject tokens once and
  writes only their hashes to disk (the clear tokens appear in no stored file —
  the no-outing discipline of :mod:`tests.test_no_outing` applied to capabilities);
* a POST to ``/record/{id}/object`` carrying a valid subject token files a
  ``subject-objection`` (verified) request with a recorded ``due_by``;
* an invalid or absent token falls back to the existing tokenless ``object`` flow.
"""

from __future__ import annotations

import re
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
from ledger.config import Config
from ledger.ingest import Archive
from ledger.models import AccessPolicy, DublinCore, Field, Record
from ledger.server import make_server

_VAULT_KEY = "0123456789abcdef0123456789abcdef0123456789a="
_CLAIM_KEY = "test-claim-secret-please-change"
_SUBJECT_RE = re.compile(r"subject:[0-9a-f]{64}")


def _serve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    objection_response_days: int = 7,
) -> Iterator[tuple[Archive, str]]:
    monkeypatch.setenv("LEDGER_VAULT_KEY", _VAULT_KEY)
    monkeypatch.setenv("LEDGER_CLAIM_SECRET", _CLAIM_KEY)
    config = Config.default("Subject Archive", tmp_path / "arc")
    config.objection_response_days = objection_response_days
    archive = Archive.init(config)
    httpd = make_server(archive, host="127.0.0.1", port=0, allow_contributions=True)
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


def _submit_with_subjects(base: str, count: int) -> tuple[int, str]:
    return _post(
        base,
        "/contribute",
        {
            "action": "submit",
            "title": "An account that names other people",
            "account": "We organised together.",
            "visibility": "public",
            "named_subjects_count": str(count),
        },
    )


def _only_record_id(archive: Archive) -> str:
    records = archive._all_records()
    assert len(records) == 1
    return records[0].record_id


def _open_requests(archive: Archive) -> list[consent.ConsentRequest]:
    store = consent.ConsentRequestStore(archive.logs_dir / "consent-requests.json")
    return store.all()


def _public_record_with_subject_token(archive: Archive) -> tuple[str, str]:
    """Ingest a *listable* public record and register one subject token for it.

    The ``/object`` route only accepts an objection when the record is listable to
    the viewer (a sealed-pending contribution 404s), so a subject-objection test
    needs a published record. This mirrors, out of band, exactly what the server
    does at contribute time: mint a subject token and store only its hash."""
    record = Record(
        title="A public account that names people",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(
            title=["A public account that names people"],
            publisher=[archive.config.archive_name],
        ),
        fields=[Field(name="story", value="Names some people.", policy=AccessPolicy.PUBLIC)],
    )
    archive.ingest({}, record, now="2026-07-01T00:00:00Z")
    token = consent.issue_subject_token(record.record_id, 0, _CLAIM_KEY.encode("utf-8"))
    consent.SubjectTokenStore(Path(archive.logs_dir) / "subject-tokens.json").register(
        record.record_id, [consent.subject_token_hash(token)]
    )
    return record.record_id, token


def test_receipt_shows_two_subject_tokens(server: tuple[Archive, str]) -> None:
    """named_subjects_count=2 surfaces exactly two distinct subject tokens once."""
    _archive, base = server
    status, body = _submit_with_subjects(base, 2)
    assert status == 200 and "Thank you" in body
    tokens = _SUBJECT_RE.findall(body)
    assert len(tokens) == 2
    assert len(set(tokens)) == 2


def test_only_hashes_persisted_no_clear_tokens_on_disk(server: tuple[Archive, str]) -> None:
    """The clear subject tokens appear in NO stored file — only their hashes do."""
    archive, base = server
    _status, body = _submit_with_subjects(base, 2)
    tokens = _SUBJECT_RE.findall(body)
    assert len(tokens) == 2

    # No clear token may appear anywhere under the store tree (no-outing for caps).
    store_root = Path(archive.store_root)
    logs_dir = Path(archive.logs_dir)
    scanned = list(store_root.rglob("*")) + list(logs_dir.rglob("*"))
    for path in scanned:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for token in tokens:
            assert token not in text, f"clear subject token leaked into {path}"

    # But the hashes ARE persisted, so a presented token can be verified later.
    subject_store = consent.SubjectTokenStore(logs_dir / "subject-tokens.json")
    record_id = _only_record_id(archive)
    stored = subject_store.hashes_for(record_id)
    assert sorted(stored) == sorted(consent.subject_token_hash(t) for t in tokens)


def test_valid_token_files_a_verified_subject_objection(server: tuple[Archive, str]) -> None:
    """A valid subject token yields a verified subject-objection with a due date."""
    archive, base = server
    record_id, token = _public_record_with_subject_token(archive)

    status, page = _post(
        base,
        f"/record/{record_id}/object",
        {"message": "Please remove my name from this record.", "token": token},
    )
    assert status == 200 and "objection was received" in page

    reqs = _open_requests(archive)
    assert len(reqs) == 1
    assert reqs[0].kind == "subject-objection"
    assert reqs[0].due_by  # a recorded, time-bound response window (RM12)
    assert reqs[0].resolved_at == ""  # not yet answered


def test_invalid_token_falls_back_to_unverified_object(server: tuple[Archive, str]) -> None:
    """A wrong token keeps the existing tokenless object flow (unverified)."""
    archive, base = server
    record_id, _token = _public_record_with_subject_token(archive)

    status, page = _post(
        base,
        f"/record/{record_id}/object",
        {"message": "I object.", "token": "subject:" + "0" * 64},
    )
    assert status == 200 and "objection was received" in page

    reqs = _open_requests(archive)
    assert len(reqs) == 1
    assert reqs[0].kind == "object"
    assert reqs[0].due_by == ""


def test_tokenless_object_is_unverified(server: tuple[Archive, str]) -> None:
    """An objection with no token at all is filed as a plain (unverified) object."""
    archive, base = server
    record_id, _token = _public_record_with_subject_token(archive)

    status, page = _post(
        base, f"/record/{record_id}/object", {"message": "No token here, but I object."}
    )
    assert status == 200 and "objection was received" in page
    reqs = _open_requests(archive)
    assert reqs[0].kind == "object"


def test_no_subjects_mints_no_tokens(server: tuple[Archive, str]) -> None:
    """Omitting named_subjects_count mints nothing and shows no subject token."""
    archive, base = server
    _status, body = _post(
        base,
        "/contribute",
        {
            "action": "submit",
            "title": "A record that names no one else",
            "account": "Just me.",
            "visibility": "public",
        },
    )
    assert _SUBJECT_RE.findall(body) == []
    subject_store = consent.SubjectTokenStore(Path(archive.logs_dir) / "subject-tokens.json")
    assert subject_store.hashes_for(_only_record_id(archive)) == []
