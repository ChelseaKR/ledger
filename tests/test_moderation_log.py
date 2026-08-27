"""Tests for the archive's durable moderation log (issue #156).

``docs/GOVERNANCE.md`` ("How decisions are recorded"), ``docs/THREAT-MODEL.md``
§4.4, and ``docs/ARCHITECTURE.md`` §1.9 all name the same accountability control:
every consequential moderation decision records **what** was done, **who** did it,
**why** — a required non-empty ``reason`` — and **to which record**. Three of those
four were already durable in the PREMIS event; the *why* was validated at the
boundary by ``moderate._require_reason`` and then dropped when the call returned,
because ``ModerationLog`` was never instantiated outside a unit test.

These tests pin the closed version of that gap, at three levels:

* the store itself — append-only, chained, locked, and fail-closed on a corrupt
  read (the failure mode ``ProposalStore`` is tracked for in #154);
* every live write path — the CLI's ``policy``/``seal``/``cw``/``takedown``, the
  steward console's warn/takedown/review, and a contributor's own withdrawal;
* the read paths — the steward-gated ``/steward/audit`` section and
  ``ledger moderation list|verify``.

Two tests are written deliberately *without* using any API this change added
(``_rationale_is_durable_on_disk``): they assert the promise the docs make rather
than the mechanism, so they are runnable against the code as it was before and
fail there. A guarantee whose test cannot fail is not a guarantee.

No-outing: ``actor`` is a steward id and ``target_record`` an opaque record id, but
``reason`` is prose a human typed, so the suite also asserts the rationale reaches
the steward-gated surface and no other.
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

from ledger import cli, consent
from ledger.access.grants import issue_grant_token
from ledger.config import Config
from ledger.errors import LedgerError, ModerationError
from ledger.ingest import Archive
from ledger.models import PremisEventType
from ledger.moderate import (
    ModerationAction,
    ModerationLogStore,
    moderation_actions,
    verify_moderation_chain,
)
from ledger.server import make_server

# A loud, obviously-fake contributor identity: a leak of this string anywhere the
# moderation log reaches would be unmistakable (the no-outing rule).
_SENTINEL = "SENTINEL-MODLOG-DO-NOT-LEAK-8H2V"

# A rationale distinctive enough that finding it anywhere is unambiguous. The whole
# point of this feature is that this text survives; the whole point of the
# disclosure tests is that it survives *only* behind the steward gate.
_REASON = "RATIONALE-PROBE-9X4Q-doxxing-complaint-from-the-subject"

_VAULT_KEY = "0123456789abcdef0123456789abcdef0123456789a="
_GRANT_SECRET = b"moderation-log-test-grant-secret"
_CLAIM_KEY = "moderation-log-test-claim-secret"
_NOW = "2026-06-17T00:00:00Z"

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


# --- helpers ----------------------------------------------------------------


def _action(
    reason: str = _REASON, *, kind: str = "warn", target: str = "rec-1"
) -> ModerationAction:
    return ModerationAction(action=kind, actor="steward-1", reason=reason, target_record=target)


def _rationale_is_durable_on_disk(root: Path, reason: str) -> bool:
    """Does ``reason`` survive anywhere under the archive root, in any file?

    Deliberately implementation-blind: it names no module, class, or path this
    change introduced, so it can run unchanged against the pre-change tree, where
    it is false. That is what makes it evidence rather than decoration.
    """
    needle = reason.encode("utf-8")
    return any(p.is_file() and needle in p.read_bytes() for p in root.rglob("*"))


def _ingest_via_cli(root: Path, capsys: pytest.CaptureFixture[str]) -> str:
    """``init`` + ``ingest`` one public fixture record; return its record id."""
    assert cli.main(["init", "--root", str(root), "--name", "Moderation Archive"]) == 0
    rc = cli.main(
        [
            "ingest",
            "--root",
            str(root),
            "--title",
            "A public account",
            "--public-field",
            "story=an account anyone may read",
            "--now",
            _NOW,
            str(_FIXTURES / "public.txt"),
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    return next(
        line.split("record_id:")[1].strip()
        for line in out.splitlines()
        if line.startswith("record_id:")
    )


def _grants_file(tmp_path: Path) -> Path:
    path = tmp_path / "grants.json"
    path.write_text(
        json.dumps(
            {"steward-1": {"levels": ["public", "community", "stewards"], "is_steward": True}}
        ),
        encoding="utf-8",
    )
    return path


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Do not follow 3xx, so a test can assert the real redirect status (303)."""

    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _req(
    base: str, path: str, *, data: dict[str, str] | None = None, steward: bool = False
) -> tuple[int, str]:
    body = urllib.parse.urlencode(data).encode("utf-8") if data is not None else None
    headers = {"X-Ledger-Grant": issue_grant_token("steward-1", _GRANT_SECRET)} if steward else {}
    if body is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(f"{base}{path}", data=body, headers=headers)  # noqa: S310 - loopback URL we constructed for the in-process test server
    try:
        with _OPENER.open(req, timeout=10) as resp:
            return int(resp.status), resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8")


@pytest.fixture
def server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Archive, str]]:
    """A running browse server with contributions, a vault key, and a steward grant."""
    monkeypatch.setenv("LEDGER_VAULT_KEY", _VAULT_KEY)
    monkeypatch.setenv("LEDGER_GRANT_SECRET", _GRANT_SECRET.decode())
    monkeypatch.setenv("LEDGER_CLAIM_SECRET", _CLAIM_KEY)
    archive = Archive.init(Config.default("Moderation Archive", tmp_path / "arc"))
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


def _submit(base: str) -> None:
    status, _ = _req(
        base,
        "/contribute",
        data={
            "action": "submit",
            "title": "Thursday gathering",
            "account": "A public account.",
            "visibility": "public",
            "contributor_name": _SENTINEL,
        },
    )
    assert status == 200


# --- the promise, tested without naming the mechanism -----------------------


def test_takedown_rationale_is_durable_on_disk(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A takedown's stated reason survives somewhere in the archive, in any form.

    ``moderate.execute_takedown``'s own docstring promises the accountable decision
    is "durably persisted FIRST -- its audit trail of *why* must outlive the data".
    This asserts exactly that sentence and nothing about how it is kept, so it is
    the test that fails against the pre-change tree (where the reason reached only
    ``_require_reason`` and a discarded return value; the persisted PREMIS event's
    detail is the fixed string "record taken down").
    """
    monkeypatch.setenv("LEDGER_VAULT_KEY", _VAULT_KEY)
    root = tmp_path / "arc"
    rid = _ingest_via_cli(root, capsys)
    assert (
        cli.main(
            [
                "takedown",
                "--root",
                str(root),
                "--id",
                rid,
                "--actor",
                "steward-1",
                "--reason",
                _REASON,
            ]
        )
        == 0
    )
    assert _rationale_is_durable_on_disk(root, _REASON), (
        "the takedown's stated reason is nowhere on disk: the archive records that a "
        "record came down but not what the steward claimed as justification"
    )


def test_content_warning_rationale_is_durable_on_disk(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A content warning's stated reason survives too (same promise, softer action)."""
    monkeypatch.setenv("LEDGER_VAULT_KEY", _VAULT_KEY)
    root = tmp_path / "arc"
    rid = _ingest_via_cli(root, capsys)
    assert (
        cli.main(
            [
                "cw",
                "--root",
                str(root),
                "--id",
                rid,
                "--warning",
                "medical",
                "--actor",
                "steward-1",
                "--reason",
                _REASON,
            ]
        )
        == 0
    )
    assert _rationale_is_durable_on_disk(root, _REASON)


# --- the store --------------------------------------------------------------


def test_fresh_archive_has_an_empty_verified_log(tmp_path: Path) -> None:
    """A new archive has no decisions, and an empty chain still verifies."""
    archive = Archive.init(Config.default("Fresh", tmp_path / "arc"))
    assert moderation_actions(archive) == []
    verification = verify_moderation_chain(archive)
    assert verification.ok and verification.broken_at is None
    assert not archive.moderation_log_path.exists()  # nothing written until something happens


def test_a_recorded_decision_survives_a_reload_with_its_reason(tmp_path: Path) -> None:
    """The four audit facts round-trip through a *separate* store instance."""
    store = ModerationLogStore(tmp_path / "moderation.json")
    store.record(_action())
    reloaded = ModerationLogStore(tmp_path / "moderation.json").actions()
    assert len(reloaded) == 1
    assert reloaded[0].action == "warn"
    assert reloaded[0].actor == "steward-1"
    assert reloaded[0].reason == _REASON
    assert reloaded[0].target_record == "rec-1"


def test_recent_returns_newest_first_and_honours_its_limit(tmp_path: Path) -> None:
    """``recent`` is the reading order an audit view wants, capped but never padded."""
    store = ModerationLogStore(tmp_path / "moderation.json")
    for i in range(5):
        store.record(_action(reason=f"reason-{i}", target=f"rec-{i}"))
    assert [a.reason for a in store.recent(limit=2)] == ["reason-4", "reason-3"]
    assert len(store.recent(limit=99)) == 5  # a cap, not a promise of that many


def test_editing_a_recorded_decision_on_disk_breaks_the_chain(tmp_path: Path) -> None:
    """Rewriting history with raw file access is detected (THREAT-MODEL §4.4)."""
    path = tmp_path / "moderation.json"
    store = ModerationLogStore(path)
    store.record(_action(reason="the reason actually given", target="rec-1"))
    store.record(_action(reason="a later decision", target="rec-2"))
    assert store.verify_chain().ok

    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["entries"][0]["reason"] = "a reason nobody gave"
    path.write_text(json.dumps(doc), encoding="utf-8")

    verification = store.verify_chain()
    assert not verification.ok
    assert verification.broken_at == 1  # the entry after the edited one stops matching


def test_a_corrupt_log_raises_instead_of_reading_as_empty(tmp_path: Path) -> None:
    """Fail-closed: corruption is an error, never "no decisions were ever made".

    This is the ``ProposalStore`` failure mode (#154) refused by construction: were
    the read to swallow the error and return ``[]``, the very next ``record`` would
    rewrite the file with one entry and erase every prior decision, silently.
    """
    path = tmp_path / "moderation.json"
    store = ModerationLogStore(path)
    store.record(_action(reason="a decision that must not vanish"))
    path.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(ModerationError):
        store.actions()
    with pytest.raises(ModerationError):
        store.record(_action(reason="a later decision"))
    # And the corrupt bytes are still there: the failed append destroyed nothing.
    assert path.read_text(encoding="utf-8") == "{not valid json"


def test_an_unreadable_log_raises_rather_than_reading_as_empty(tmp_path: Path) -> None:
    """Not-JSON and not-readable leave by the same door, for the same reason.

    A directory where the log should be is an ``OSError`` rather than a parse
    failure, and it must not be mistaken for "this archive has made no decisions"
    any more than corrupt bytes are.
    """
    path = tmp_path / "moderation.json"
    path.mkdir()  # something is there; it is just not a readable log
    with pytest.raises(ModerationError, match="could not be read"):
        ModerationLogStore(path).actions()


def test_a_write_failure_raises_and_names_only_the_path(tmp_path: Path) -> None:
    """An unwritable store fails loudly, and the error carries no rationale text.

    Mirrors ``test_consent_store_write_failure_raises_without_leaking``: the
    message names the store path, never the steward's prose (the no-outing rule
    applies to error strings too).
    """
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_dir.chmod(0o500)
    try:
        store = ModerationLogStore(log_dir / "moderation.json")
        with pytest.raises(LedgerError, match="could not be written") as excinfo:
            store.record(_action(reason=_REASON))
        assert _REASON not in str(excinfo.value)
    finally:
        log_dir.chmod(0o700)


def test_concurrent_decisions_are_all_recorded(tmp_path: Path) -> None:
    """40 concurrent appends lose none: the critical section is serialized.

    Mirrors ``tests/test_ingest_concurrent.py``. The threaded browse server runs one
    thread per request, so two stewards acting at once is ordinary, not exotic; an
    unlocked read-modify-write drops most of them (see #155 for the same defect in
    ``TombstoneStore``).
    """
    store = ModerationLogStore(tmp_path / "moderation.json")
    errors: list[BaseException] = []

    def worker(i: int) -> None:
        try:
            store.record(_action(reason=f"concurrent reason {i}", target=f"rec-{i}"))
        except Exception as exc:  # the test reports every failure, never swallows one
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(store.actions()) == 40
    assert {a.target_record for a in store.actions()} == {f"rec-{i}" for i in range(40)}
    assert store.verify_chain().ok  # every append chained to the one it actually followed


# --- every live CLI write path ----------------------------------------------


def test_cli_cw_records_an_accountable_warn(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LEDGER_VAULT_KEY", _VAULT_KEY)
    root = tmp_path / "arc"
    rid = _ingest_via_cli(root, capsys)
    assert (
        cli.main(
            [
                "cw",
                "--root",
                str(root),
                "--id",
                rid,
                "--warning",
                "police-violence",
                "--actor",
                "steward-1",
                "--reason",
                _REASON,
            ]
        )
        == 0
    )
    archive = Archive(Config.load(root / "store" / "config.json"))
    recorded = moderation_actions(archive)
    assert [(a.action, a.actor, a.reason, a.target_record) for a in recorded] == [
        ("warn", "steward-1", _REASON, rid)
    ]


def test_cli_policy_records_an_accountable_consent_change(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LEDGER_VAULT_KEY", _VAULT_KEY)
    root = tmp_path / "arc"
    rid = _ingest_via_cli(root, capsys)
    assert (
        cli.main(
            [
                "policy",
                "--root",
                str(root),
                "--id",
                rid,
                "--level",
                "community",
                "--actor",
                "steward-1",
                "--reason",
                _REASON,
            ]
        )
        == 0
    )
    archive = Archive(Config.load(root / "store" / "config.json"))
    recorded = moderation_actions(archive)
    assert [(a.action, a.reason) for a in recorded] == [("consent-change", _REASON)]


def test_cli_seal_records_an_accountable_field_policy_change(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LEDGER_VAULT_KEY", _VAULT_KEY)
    root = tmp_path / "arc"
    rid = _ingest_via_cli(root, capsys)
    assert (
        cli.main(
            [
                "seal",
                "--root",
                str(root),
                "--id",
                rid,
                "--field",
                "story",
                "--level",
                "stewards",
                "--actor",
                "steward-1",
                "--reason",
                _REASON,
            ]
        )
        == 0
    )
    archive = Archive(Config.load(root / "store" / "config.json"))
    assert [(a.action, a.reason) for a in moderation_actions(archive)] == [
        ("consent-change", _REASON)
    ]


def test_cli_takedown_rationale_outlives_the_record_it_was_about(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bag is gone; the account of why it went is not.

    Also pins *why* a separate log is needed: the PREMIS ``TAKEDOWN`` event that
    survives alongside it carries the fixed detail "record taken down", built only
    from the what. It has never carried the rationale and does not now.
    """
    monkeypatch.setenv("LEDGER_VAULT_KEY", _VAULT_KEY)
    root = tmp_path / "arc"
    rid = _ingest_via_cli(root, capsys)
    archive = Archive(Config.load(root / "store" / "config.json"))
    assert (archive.bags_dir / rid).exists()

    assert (
        cli.main(
            [
                "takedown",
                "--root",
                str(root),
                "--id",
                rid,
                "--actor",
                "steward-1",
                "--reason",
                _REASON,
            ]
        )
        == 0
    )

    assert not (archive.bags_dir / rid).exists()  # every copy removed
    recorded = moderation_actions(archive)
    assert [(a.action, a.actor, a.reason, a.target_record) for a in recorded] == [
        ("takedown", "steward-1", _REASON, rid)
    ]
    takedown_events = [
        e for e in archive.audit_events() if e.event_type is PremisEventType.TAKEDOWN
    ]
    assert takedown_events and all(_REASON not in e.detail for e in takedown_events)


# --- every live server write path -------------------------------------------


def test_steward_console_warn_records_the_rationale(server: tuple[Archive, str]) -> None:
    archive, base = server
    _submit(base)
    rid = archive._all_records()[0].record_id
    status, _ = _req(
        base,
        f"/steward/records/{rid}/warn",
        data={"warning": "medical", "reason": _REASON},
        steward=True,
    )
    assert status == 303
    warns = [a for a in moderation_actions(archive) if a.action == "warn"]
    assert [(a.actor, a.reason, a.target_record) for a in warns] == [("steward-1", _REASON, rid)]


def test_steward_console_takedown_records_the_rationale(server: tuple[Archive, str]) -> None:
    archive, base = server
    _submit(base)
    rid = archive._all_records()[0].record_id
    status, _ = _req(
        base, f"/steward/records/{rid}/takedown", data={"reason": _REASON}, steward=True
    )
    assert status == 303
    takedowns = [a for a in moderation_actions(archive) if a.action == "takedown"]
    assert [(a.actor, a.reason, a.target_record) for a in takedowns] == [
        ("steward-1", _REASON, rid)
    ]


def test_a_refused_moderation_action_records_nothing(server: tuple[Archive, str]) -> None:
    """No reason, no decision, no entry: the log holds only accountable acts."""
    archive, base = server
    _submit(base)
    rid = archive._all_records()[0].record_id
    status, _ = _req(
        base, f"/steward/records/{rid}/warn", data={"warning": "x", "reason": ""}, steward=True
    )
    assert status == 400
    assert moderation_actions(archive) == []


def test_review_publish_and_withhold_are_recorded_decisions(
    server: tuple[Archive, str],
) -> None:
    """Opening a submission is a moderation decision, and says so in the log."""
    archive, base = server
    _submit(base)
    rid = archive._all_records()[0].record_id
    status, _ = _req(
        base, f"/steward/submissions/{rid}/review", data={"action": "publish"}, steward=True
    )
    assert status == 303
    recorded = moderation_actions(archive)
    assert [(a.action, a.actor, a.target_record) for a in recorded] == [
        ("consent-change", "steward-1", rid)
    ]
    assert recorded[0].reason == "approved from the steward review queue"


def test_contributor_withdrawal_is_a_recorded_decision(server: tuple[Archive, str]) -> None:
    """A contributor's own withdrawal is accountable too — actor: the contributor."""
    archive, base = server
    _submit(base)
    rid = archive._all_records()[0].record_id
    token = consent.issue_claim_token(rid, _CLAIM_KEY.encode("utf-8"))
    status, body = _req(base, "/withdraw", data={"ref": rid, "claim": token})
    assert status == 200 and "Withdrawn" in body
    recorded = moderation_actions(archive)
    assert [(a.action, a.actor, a.target_record) for a in recorded] == [
        ("takedown", "contributor", rid)
    ]
    assert recorded[0].reason == "contributor withdrawal before publication"
    assert _SENTINEL not in json.dumps([a.to_dict() for a in recorded])


# --- the read surfaces ------------------------------------------------------


def test_audit_page_shows_the_rationale_to_a_steward_only(server: tuple[Archive, str]) -> None:
    """``/steward/audit`` renders the why; a non-steward gets a neutral 404."""
    archive, base = server
    _submit(base)
    rid = archive._all_records()[0].record_id
    _req(
        base,
        f"/steward/records/{rid}/warn",
        data={"warning": "medical", "reason": _REASON},
        steward=True,
    )

    status, body = _req(base, "/steward/audit", steward=True)
    assert status == 200
    assert "<h2>Moderation decisions</h2>" in body
    assert _REASON in body
    assert "<caption>Moderation decisions, newest first</caption>" in body
    assert 'scope="col"' in body  # the accessible table shape the page already uses
    assert "Chain verified" in body
    assert _SENTINEL not in body

    assert _req(base, "/steward/audit")[0] == 404


def test_audit_page_says_so_when_there_is_nothing_to_show(server: tuple[Archive, str]) -> None:
    """An archive with no decisions says that plainly, rather than omitting the section."""
    _archive, base = server
    _status, body = _req(base, "/steward/audit", steward=True)
    assert "<h2>Moderation decisions</h2>" in body
    assert "No moderation decisions recorded yet." in body


def test_audit_page_reports_a_broken_chain(server: tuple[Archive, str]) -> None:
    """A tampered log is reported *on the page*, not silently rendered as fine."""
    archive, base = server
    _submit(base)
    rid = archive._all_records()[0].record_id
    _req(
        base, f"/steward/records/{rid}/warn", data={"warning": "a", "reason": "first"}, steward=True
    )
    _req(
        base,
        f"/steward/records/{rid}/warn",
        data={"warning": "b", "reason": "second"},
        steward=True,
    )

    path = archive.moderation_log_path
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["entries"][0]["reason"] = "a reason nobody gave"
    path.write_text(json.dumps(doc), encoding="utf-8")

    _status, body = _req(base, "/steward/audit", steward=True)
    assert "Chain verification FAILED" in body
    assert "Chain verified" not in body


def test_audit_page_reports_an_unreadable_log_as_unreadable(server: tuple[Archive, str]) -> None:
    """Not readable is never rendered as not present (failure transparency)."""
    archive, base = server
    archive.moderation_log_path.parent.mkdir(parents=True, exist_ok=True)
    archive.moderation_log_path.write_text("{not valid json", encoding="utf-8")
    status, body = _req(base, "/steward/audit", steward=True)
    assert status == 200
    assert "The moderation log could not be read" in body
    assert "No moderation decisions recorded yet." not in body


def test_audit_moderation_section_is_localized(server: tuple[Archive, str]) -> None:
    """The section renders in the steward's language, like the rest of the console."""
    _archive, base = server
    _status, body = _req(base, "/steward/audit?lang=es", steward=True)
    assert "Decisiones de moderación" in body
    assert "Moderation decisions" not in body


def test_cli_moderation_list_prints_the_four_audit_facts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LEDGER_VAULT_KEY", _VAULT_KEY)
    root = tmp_path / "arc"
    rid = _ingest_via_cli(root, capsys)
    cli.main(
        [
            "cw",
            "--root",
            str(root),
            "--id",
            rid,
            "--warning",
            "medical",
            "--actor",
            "steward-1",
            "--reason",
            _REASON,
        ]
    )
    capsys.readouterr()

    assert cli.main(["moderation", "list", "--root", str(root)]) == 0
    out = capsys.readouterr().out
    assert "warn" in out and "steward-1" in out and _REASON in out and rid in out
    assert "(1 recorded decision(s))" in out

    assert cli.main(["moderation", "list", "--root", str(root), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["reason"] == _REASON
    assert payload[0]["action"] == "warn"


def test_cli_moderation_verify_exits_zero_intact_and_two_when_tampered(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scheduled check can branch on the exit code (operability)."""
    monkeypatch.setenv("LEDGER_VAULT_KEY", _VAULT_KEY)
    root = tmp_path / "arc"
    rid = _ingest_via_cli(root, capsys)
    for reason in ("first reason", "second reason"):
        cli.main(
            [
                "cw",
                "--root",
                str(root),
                "--id",
                rid,
                "--warning",
                reason[:5],
                "--actor",
                "steward-1",
                "--reason",
                reason,
            ]
        )
    capsys.readouterr()

    assert cli.main(["moderation", "verify", "--root", str(root)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["entries"] == 2 and report["chain_verified"] is True

    archive = Archive(Config.load(root / "store" / "config.json"))
    path = archive.moderation_log_path
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["entries"][0]["reason"] = "a reason nobody gave"
    path.write_text(json.dumps(doc), encoding="utf-8")

    assert cli.main(["moderation", "verify", "--root", str(root)]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["chain_verified"] is False and report["broken_at"] == 1


# --- no-outing --------------------------------------------------------------


@pytest.mark.disclosure
def test_the_rationale_never_reaches_a_public_surface(server: tuple[Archive, str]) -> None:
    """A steward's prose is steward-only: every ungated surface must be free of it.

    ``reason`` is the one field on the log a human types, so it is the one field
    that could carry something a public reader must not see. The control is not
    that a steward cannot type it -- it is that no ungated surface renders it.
    """
    archive, base = server
    _submit(base)
    rid = archive._all_records()[0].record_id
    _req(
        base,
        f"/steward/records/{rid}/warn",
        data={"warning": "medical", "reason": _REASON},
        steward=True,
    )
    assert _REASON in _req(base, "/steward/audit", steward=True)[1]  # the gate is real

    public_surfaces = (
        "/",
        f"/record/{rid}",
        "/search?q=gathering",
        "/api/records",
        f"/api/record/{rid}",
        "/feed.atom",
        "/oai?verb=ListRecords&metadataPrefix=oai_dc",
        "/transparency",
        "/proof",
        "/overview",
        "/timeline",
        "/places",
    )
    for path in public_surfaces:
        _status, body = _req(base, path)
        assert _REASON not in body, f"the steward's rationale leaked to {path}"
        assert _SENTINEL not in body


@pytest.mark.disclosure
def test_the_log_on_disk_carries_no_contributor_identity(server: tuple[Archive, str]) -> None:
    """The persisted file holds decision metadata only — never an identity."""
    archive, base = server
    _submit(base)
    rid = archive._all_records()[0].record_id
    _req(base, f"/steward/records/{rid}/takedown", data={"reason": _REASON}, steward=True)

    on_disk = archive.moderation_log_path.read_text(encoding="utf-8")
    assert _SENTINEL not in on_disk
    entries = json.loads(on_disk)["entries"]
    assert {k for entry in entries for k in entry} <= {
        "action",
        "action_id",
        "actor",
        "at",
        "prevHash",
        "reason",
        "target_record",
        "appeal_of",
    }
