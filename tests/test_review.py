"""Tests for the steward submission-review loop (`ledger.review` + the console).

A contribution lands sealed-pending; a steward must make the deliberate act that
opens it. These tests pin that loop: the queue is identity-free, the console shows
pending submissions to a steward only, **publish** opens a record to the requested
visibility, **withhold** holds it for stewards, and neither the queue nor the
console ever leaks a contributor identity.
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

from ledger.access.grants import anonymous, community_member
from ledger.config import Config
from ledger.ingest import Archive
from ledger.models import AccessPolicy
from ledger.review import SubmissionQueue
from ledger.server import make_server

_SENTINEL = "SENTINEL-REVIEW-DO-NOT-LEAK-5R1T"
_VAULT_KEY = "0123456789abcdef0123456789abcdef0123456789a="
_NOW = "2026-06-17T00:00:00Z"


# --- unit: the queue --------------------------------------------------------


@pytest.mark.disclosure
def test_submission_queue_add_pending_remove(tmp_path: Path) -> None:
    """The queue holds opaque ids in order, is idempotent, and removes cleanly."""
    q = SubmissionQueue(tmp_path / "queue.json")
    assert q.pending() == []
    q.add("rec-a", now=_NOW)
    q.add("rec-b", now=_NOW)
    q.add("rec-a", now=_NOW)  # idempotent
    assert [p.record_id for p in q.pending()] == ["rec-a", "rec-b"]
    assert q.contains("rec-a")
    q.remove("rec-a")
    assert [p.record_id for p in q.pending()] == ["rec-b"]
    q.remove("rec-a")  # idempotent
    assert [p.record_id for p in q.pending()] == ["rec-b"]


@pytest.mark.disclosure
def test_submission_queue_entries_are_identity_free(tmp_path: Path) -> None:
    """A queue entry serializes only a record id and a timestamp — never content."""
    q = SubmissionQueue(tmp_path / "queue.json")
    q.add("rec-x", now=_NOW)
    on_disk = (tmp_path / "queue.json").read_text(encoding="utf-8")
    assert "rec-x" in on_disk
    assert set(json.loads(on_disk)[0].keys()) == {"record_id", "submitted_at"}


# --- integration: the live console + review ---------------------------------


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
    """A running server with contributions enabled, a vault key, and a steward grant."""
    monkeypatch.setenv("LEDGER_VAULT_KEY", _VAULT_KEY)
    archive = Archive.init(Config.default("Review Archive", tmp_path / "arc"))
    httpd = make_server(
        archive,
        host="127.0.0.1",
        port=0,
        grants_path=_grants_file(tmp_path),
        allow_contributions=True,
    )
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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Do not follow 3xx, so a test can assert the real redirect status (e.g. 303)."""

    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _req(
    base: str, path: str, *, data: dict[str, str] | None = None, steward: bool = False
) -> tuple[int, str]:
    body = urllib.parse.urlencode(data).encode("utf-8") if data is not None else None
    headers = {"X-Ledger-Grant": "steward-1"} if steward else {}
    if body is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(f"{base}{path}", data=body, headers=headers)  # noqa: S310
    try:
        with _OPENER.open(req, timeout=10) as resp:
            return int(resp.status), resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8")


def _submit(base: str, visibility: str = "public") -> None:
    status, _ = _req(
        base,
        "/contribute",
        data={
            "action": "submit",
            "title": "Thursday gathering",
            "account": "A public account.",
            "visibility": visibility,
            "contributor_name": _SENTINEL,
        },
    )
    assert status == 200


@pytest.mark.disclosure
def test_console_shows_pending_submission_to_steward_only(server: tuple[Archive, str]) -> None:
    """A submitted record appears in the steward console — and never to a non-steward."""
    archive, base = server
    _submit(base)
    rid = archive._all_records()[0].record_id

    # Steward sees it, with action buttons, and no identity sentinel.
    status, body = _req(base, "/steward", steward=True)
    assert status == 200
    assert "Submissions awaiting review" in body
    assert rid in body
    assert 'value="publish"' in body and 'value="withhold"' in body
    assert _SENTINEL not in body

    # A non-steward gets a neutral 404 for the whole console.
    assert _req(base, "/steward")[0] == 404


def test_console_shows_the_requested_visibility_in_the_queue(
    server: tuple[Archive, str],
) -> None:
    """The queue states what publishing would do, so a steward never opens it blind."""
    _archive, base = server
    _submit(base, visibility="community")
    _status, body = _req(base, "/steward", steward=True)
    # The steward sees the target visibility for "Publish (as requested)" inline.
    assert "Would publish as:" in body
    assert "Community only" in body
    assert "anyone may read it" not in body  # not the public phrasing


def test_steward_console_and_audit_are_localized(server: tuple[Archive, str]) -> None:
    """The steward console and audit log render in the steward's language (I2)."""
    _archive, base = server
    _submit(base, visibility="public")
    _status, console = _req(base, "/steward?lang=es", steward=True)
    assert "Consola de administración" in console
    assert "Envíos a la espera de revisión" in console
    assert "Se publicaría como:" in console
    assert "Publicar (como se solicitó)" in console
    assert "Steward console" not in console  # the English heading is gone
    _status, audit = _req(base, "/steward/audit?lang=es", steward=True)
    assert "Registro de auditoría" in audit


def test_console_flags_a_submission_edited_under_review(server: tuple[Archive, str]) -> None:
    """A correction recorded after submission shows an 'Edited' marker in the queue."""
    from ledger.models import PremisEvent, PremisEventType, now_iso

    archive, base = server
    _submit(base)
    rid = archive._all_records()[0].record_id
    # Before any correction the queue carries no edited marker.
    assert "Edited" not in _req(base, "/steward", steward=True)[1]

    # Record a CORRECTION event the way a contributor edit does.
    record = archive.get(rid)
    archive.apply_update(
        record,
        PremisEvent(
            event_type=PremisEventType.CORRECTION,
            agent="contributor",
            outcome="success",
            detail="contributor edited a pending submission",
            linked_object=rid,
            event_datetime=now_iso(),
        ),
    )
    body = _req(base, "/steward", steward=True)[1]
    assert "Edited (1 time)" in body


@pytest.mark.disclosure
def test_publish_opens_record_to_requested_visibility(server: tuple[Archive, str]) -> None:
    """Publishing a sealed-pending submission makes it listable and clears the queue."""
    archive, base = server
    _submit(base, visibility="public")
    rid = archive._all_records()[0].record_id
    assert archive.browse(anonymous()) == []  # sealed-pending: invisible

    status, _ = _req(
        base, f"/steward/submissions/{rid}/review", data={"action": "publish"}, steward=True
    )
    assert status == 303

    # Now anonymous can list it, and it left the queue.
    listed = archive.browse(anonymous())
    assert [r.record_id for r in listed] == [rid]
    assert SubmissionQueue(archive.logs_dir / "submission-queue.json").pending() == []
    # Re-loading the record confirms the default policy was opened to PUBLIC.
    assert archive.get(rid).default_policy is AccessPolicy.PUBLIC


@pytest.mark.disclosure
def test_bulk_withhold_holds_every_selected_submission(server: tuple[Archive, str]) -> None:
    """One bulk action withholds all checked submissions and clears them from the queue."""
    archive, base = server
    _submit(base, visibility="public")
    _submit(base, visibility="public")
    ids = [r.record_id for r in archive._all_records()]
    assert len(ids) == 2

    # The console offers the bulk form and a select checkbox per submission.
    console = _req(base, "/steward", steward=True)[1]
    assert 'id="bulk-withhold"' in console
    assert console.count('name="select"') == 2

    # POST both ids as repeated `select` values to the bulk endpoint.
    body = urllib.parse.urlencode([("select", ids[0]), ("select", ids[1])]).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310 - loopback
        f"{base}/steward/submissions/withhold",
        data=body,
        headers={
            "X-Ledger-Grant": "steward-1",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with _OPENER.open(req, timeout=10) as resp:
            code = int(resp.status)
    except urllib.error.HTTPError as exc:  # _NoRedirect surfaces the 303 here
        code = int(exc.code)
    assert code == 303

    # Both are now withheld (stewards-only) and the queue is empty.
    assert archive.browse(anonymous()) == []
    assert archive.browse(community_member("member")) == []
    assert SubmissionQueue(archive.logs_dir / "submission-queue.json").pending() == []
    for rid in ids:
        assert archive.get(rid).default_policy is AccessPolicy.STEWARDS


def test_bulk_withhold_is_steward_only(server: tuple[Archive, str]) -> None:
    """A non-steward bulk-withhold POST 404s and leaves the queue untouched."""
    archive, base = server
    _submit(base, visibility="public")
    rid = archive._all_records()[0].record_id
    status, _ = _req(base, "/steward/submissions/withhold", data={"select": rid})
    assert status == 404
    assert SubmissionQueue(archive.logs_dir / "submission-queue.json").contains(rid)


@pytest.mark.disclosure
def test_withhold_holds_record_for_stewards(server: tuple[Archive, str]) -> None:
    """Withholding restricts the record to stewards and clears the queue."""
    archive, base = server
    _submit(base, visibility="public")
    rid = archive._all_records()[0].record_id

    status, _ = _req(
        base, f"/steward/submissions/{rid}/review", data={"action": "withhold"}, steward=True
    )
    assert status == 303

    assert archive.browse(anonymous()) == []
    assert archive.browse(community_member("member")) == []
    assert archive.get(rid).default_policy is AccessPolicy.STEWARDS
    assert SubmissionQueue(archive.logs_dir / "submission-queue.json").pending() == []


@pytest.mark.disclosure
def test_non_steward_cannot_review(server: tuple[Archive, str]) -> None:
    """A non-steward review POST 404s and leaves the submission untouched."""
    archive, base = server
    _submit(base)
    rid = archive._all_records()[0].record_id

    assert _req(base, f"/steward/submissions/{rid}/review", data={"action": "publish"})[0] == 404
    # Still pending, still sealed.
    assert SubmissionQueue(archive.logs_dir / "submission-queue.json").contains(rid)
    assert archive.browse(anonymous()) == []
