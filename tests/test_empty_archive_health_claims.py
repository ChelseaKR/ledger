"""An archive with nothing in it must not read as an archive that passed (#208).

``all(report.ok for _name, report in reports)`` and ``failed == 0`` are both
vacuously true over an empty sweep, so every surface that summarised a fixity
audit told its reader that everything was fine when nothing had been looked at.
:class:`ledger.fixity.FixityStatus` closed that hole for the files inside one bag
in #206; these pin it closed for the *set* of bags, on the three surfaces a person
actually reads:

* ``GET /status``, the page a non-technical reader is sent to when they want to
  know whether the archive is all right;
* ``HandoffManifest.runbook()``, read by a non-ops volunteer inheriting an
  archive — the reader least equipped to notice a sentence is empty;
* ``ledger handoff``'s summary line, read by the operator running the hand-off.

**What is deliberately NOT here.** ``/healthz``'s ``all_verified`` and the
manifest's ``all_fixity_ok`` are unchanged, and so is ``ledger handoff``'s exit
code. Those are machine contracts a monitor alerts on and a versioned document a
third party parses; changing them is the owner's call, recorded in #208 and #205.
Every fix below is to a *sentence*, built from fields that were already published.

These also pin that the /status page goes through the gettext seam at all. Its
sentences were English literals in ``server.py``, so an Arabic reader got an
English health report next to a correctly translated footer — the page-shell
defect of #216, one route further in.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from ledger import cli, fixity
from ledger.access.grants import issue_grant_token
from ledger.config import Config
from ledger.fixity import AuditReport, FixityStatus
from ledger.ingest import Archive
from ledger.models import AccessPolicy, DublinCore, Field, FixityResult, HashAlgo, Record
from ledger.server import make_server
from ledger.succession import build_handoff

_VAULT_KEY = "0123456789abcdef0123456789abcdef0123456789a="
_GRANT_SECRET = b"empty-archive-test-grant-secret"
_NOW = "2026-06-16T12:00:00Z"


def _result(*, ok: bool) -> FixityResult:
    """One file result, matching or not, with no file system behind it."""
    return FixityResult(
        path="data/x.txt", algo=HashAlgo.SHA256, expected="a" * 64, actual=("a" if ok else "b") * 64
    )


# --- the fold itself --------------------------------------------------------


def test_an_empty_sweep_is_unverified_not_verified() -> None:
    """No bags at all is the case the whole issue is about."""
    assert fixity.overall_status([]) is FixityStatus.UNVERIFIED


def test_every_bag_passing_is_verified() -> None:
    assert (
        fixity.overall_status([AuditReport(results=[_result(ok=True)])] * 3)
        is FixityStatus.VERIFIED
    )


def test_one_failing_bag_dominates_the_verdict() -> None:
    """Damage is the fact a reader must act on first."""
    reports = [AuditReport(results=[_result(ok=True)]), AuditReport(results=[_result(ok=False)])]
    assert fixity.overall_status(reports) is FixityStatus.FAILED


def test_an_unverifiable_bag_among_passing_ones_is_never_a_pass() -> None:
    """A bag declaring no files to check is the empty case one level down."""
    reports = [AuditReport(results=[_result(ok=True)]), AuditReport(results=[])]
    assert fixity.overall_status(reports) is FixityStatus.UNVERIFIED


def test_the_whole_iterable_is_consumed_even_when_it_fails() -> None:
    """A caller may pass a generator that is also doing the audit's I/O."""
    seen: list[int] = []

    def audited() -> Iterator[AuditReport]:
        for index, ok in enumerate([False, True, True]):
            seen.append(index)
            yield AuditReport(results=[_result(ok=ok)])

    assert fixity.overall_status(audited()) is FixityStatus.FAILED
    assert seen == [0, 1, 2], "short-circuiting would leave later bags unaudited"


# --- /status ----------------------------------------------------------------


def _archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, records: int, name: str = "arc"
) -> Archive:
    monkeypatch.setenv("LEDGER_VAULT_KEY", _VAULT_KEY)
    monkeypatch.setenv("LEDGER_GRANT_SECRET", _GRANT_SECRET.decode())
    root = tmp_path / name
    archive = Archive.init(Config.default("Empty Archive Test", root))
    for index in range(records):
        payload = root.parent / f"{name}-doc{index}.txt"
        payload.write_text(f"synthetic record {index}\n", encoding="utf-8")
        archive.ingest(
            {payload.name: payload},
            Record(
                title=f"Record {index}",
                default_policy=AccessPolicy.PUBLIC,
                dublin_core=DublinCore(title=[f"Record {index}"], type=["flyer"]),
                fields=[Field("text", "public", AccessPolicy.PUBLIC)],
            ),
            now=_NOW,
        )
    return archive


def _serve(archive: Archive, tmp_path: Path, name: str) -> Iterator[str]:
    grants = tmp_path / f"{name}-grants.json"
    grants.write_text(
        json.dumps({"warden": {"levels": ["public", "community", "stewards"], "is_steward": True}}),
        encoding="utf-8",
    )
    httpd = make_server(archive, host="127.0.0.1", port=0, grants_path=grants)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        thread.join(timeout=5)
        httpd.server_close()


def _get(base: str, path: str, *, lang: str | None = None, steward: bool = False) -> str:
    request = urllib.request.Request(f"{base}{path}")  # noqa: S310 - loopback
    if lang:
        request.add_header("Accept-Language", lang)
    if steward:
        request.add_header("X-Ledger-Grant", issue_grant_token("warden", _GRANT_SECRET))
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - loopback URL we constructed for the in-process test server
            return str(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:  # pragma: no cover - /status is always 200
        return str(error.read().decode("utf-8"))


@pytest.fixture
def empty_site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    yield from _serve(_archive(tmp_path, monkeypatch, records=0, name="empty"), tmp_path, "empty")


@pytest.fixture
def seeded_site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    yield from _serve(_archive(tmp_path, monkeypatch, records=2, name="seeded"), tmp_path, "seeded")


def test_status_over_an_empty_archive_does_not_claim_health(empty_site: str) -> None:
    """The live defect: 0 bags rendered "Everything is healthy.\""""
    body = _get(empty_site, "/status")
    assert "Archive status" in body, "the status page did not render; this proves nothing"
    assert "This archive holds nothing yet." in body
    assert "no integrity check has run" in body
    assert "Everything is healthy." not in body
    assert "Every stored record passed its most recent integrity check." not in body


def test_status_over_an_empty_archive_does_not_claim_damage_either(empty_site: str) -> None:
    """The mirror image: an archive that holds nothing yet is not corrupt."""
    body = _get(empty_site, "/status")
    assert "Some records need a steward's attention." not in body
    assert "did not pass their integrity check" not in body


def test_status_over_a_seeded_archive_still_reports_health(seeded_site: str) -> None:
    """The passing branch is unchanged — this is a fix to the empty case only."""
    body = _get(seeded_site, "/status")
    assert "Everything is healthy." in body
    assert "Every stored record passed its most recent integrity check." in body


def test_a_steward_sees_counts_only_when_there_are_bags_to_count(
    empty_site: str, seeded_site: str
) -> None:
    """ "0 of 0 passed every integrity check" is the same vacuous claim in numerals."""
    seeded = _get(seeded_site, "/status", steward=True)
    assert "2 of 2 record package(s) passed every integrity check" in seeded
    empty = _get(empty_site, "/status", steward=True)
    assert "0 of 0" not in empty
    assert "This archive holds nothing yet." in empty


@pytest.mark.parametrize(
    ("lang", "heading", "headline"),
    [
        ("es", "Estado del archivo", "Todo está en buen estado."),
        # \u2019 is the typographic apostrophe the French catalog uses; spelled as an
        # escape because ruff's RUF001 rejects the literal character in source.
        ("fr", "\u00c9tat de l\u2019archive", "Tout est en bon \u00e9tat."),
        ("ar", "حالة الأرشيف", "كل شيء في حالة جيدة."),
    ],
)
def test_the_status_page_is_translated(
    seeded_site: str, lang: str, heading: str, headline: str
) -> None:
    """Every sentence on this page used to be an English literal in server.py."""
    body = _get(seeded_site, "/status", lang=lang)
    assert heading in body
    assert headline in body
    assert "Archive status" not in body
    assert "Everything is healthy." not in body


# --- the hand-off runbook and the CLI summary -------------------------------


def test_the_handoff_runbook_does_not_tell_a_successor_an_empty_archive_is_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The live defect: "Records: 0. All bags verified intact at hand-off time.\""""
    manifest = build_handoff(_archive(tmp_path, monkeypatch, records=0), now=_NOW)
    runbook = manifest.runbook()
    assert "Records: 0." in runbook, "the count line is missing; this proves nothing"
    assert "All bags verified intact at hand-off time." not in runbook
    assert "No bags were audited, so nothing was verified" in runbook
    # The published field is deliberately untouched: it is a versioned document a
    # third party parses, and redefining it is #208's owner decision, not this fix.
    assert manifest.all_fixity_ok is True
    assert json.loads(manifest.to_json())["all_fixity_ok"] is True


def test_the_handoff_runbook_still_reports_intact_when_bags_were_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = build_handoff(_archive(tmp_path, monkeypatch, records=2), now=_NOW)
    assert "All bags verified intact at hand-off time." in manifest.runbook()
    assert manifest.fixity_status is FixityStatus.VERIFIED


def test_the_manifest_schema_version_does_not_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`fixity_status` is a property, not a field: the document is byte-compatible."""
    manifest = build_handoff(_archive(tmp_path, monkeypatch, records=1), now=_NOW)
    document = json.loads(manifest.to_json())
    assert document["schema_version"] == 1
    assert "fixity_status" not in document


def test_ledger_handoff_does_not_print_all_bags_verified_over_no_bags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """And the exit code is deliberately unchanged: an empty archive is not corrupt."""
    _archive(tmp_path, monkeypatch, records=0)
    out = tmp_path / "handoff.json"
    code = cli.main(["handoff", "--root", str(tmp_path / "arc"), "--out", str(out), "--now", _NOW])
    captured = capsys.readouterr()
    assert "hand-off: 0 record(s);" in captured.err, "the summary line is missing"
    assert "all bags verified" not in captured.err
    assert "no bags to verify" in captured.err
    assert code == 0
