"""An archive with nothing in it must not verify as a good backup.

``all([])`` is :data:`True`, and for as long as the fixity layer had only a
two-state ``ok`` that vacuity ran straight through every caller. The sharpest
consequence was in :mod:`ledger.lockdown`: ``verify_backup_location`` ended in
``all_ok = all(bag.ok for bag in bags)``, so an off-box replica holding
``store/config.json`` and an ``identity.vault`` but **none of the archive's
content** came back ``ok=True`` with an empty ``reason`` — and that is the gate
:func:`ledger.lockdown.execute_lockdown` consults before it irreversibly shreds
the local identity vault. A partial rsync or an emptied replica disk read as
"your archive survived" and authorised destroying the only real copy.

The same shape sat in ``ledger verify-backup`` (``PASS: 0 bag(s) verified, 0
failed`` on a content-free backup, exit 0, so a cron job whose whole job is to
alarm on a bad backup stayed quiet), in ``ledger audit``, and in
:func:`ledger.attestation.build_attestation`, whose ``fixity_ok`` a steward
signs and publishes at ``/proof``.

The fix is three states, not a flipped boolean. Reporting a *failure* for an
absence would be the same defect wearing the other mask: an archive that
genuinely holds nothing yet is not corrupt. So
:class:`ledger.fixity.FixityStatus` distinguishes ``verified`` (things were
checked and passed), ``failed`` (things were checked and one did not), and
``could-not-verify`` (nothing was checked) — the same three-state honesty
:class:`ledger.checkup.CheckStatus` and :class:`ledger.drill.DrillOutcome`
already use. These tests pin each of those three outcomes apart from the others,
because a third state that is merely a second name for failure fixes nothing.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from ledger.attestation import build_attestation
from ledger.backup import verify_backup
from ledger.config import Config
from ledger.errors import LedgerError
from ledger.fixity import AuditReport, FixityStatus, audit_files
from ledger.identity import ContributorIdentity
from ledger.ingest import Archive
from ledger.lockdown import LockdownConfig, execute_lockdown, is_locked_down, verify_backup_location
from ledger.models import AccessPolicy, DublinCore, Field, FixityResult, HashAlgo, Record

_VAULT_KEY = b"0123456789abcdef0123456789abcdef0123456789a="
_NOW = "2026-09-07T00:00:00Z"
_FIXTURES = Path(__file__).resolve().parent / "fixtures"


# --- fixtures ---------------------------------------------------------------


def _ingest(archive: Archive, title: str) -> str:
    """Put one real record, with a real payload file, into ``archive``."""
    payload = _FIXTURES / "public.txt"
    record = Record(
        title=title,
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(
            title=[title], publisher=["Test Archive"], type=["oral history"], language=["en"]
        ),
        fields=[Field(name="story", value="A public account.", policy=AccessPolicy.PUBLIC)],
    )
    archive.ingest(
        {payload.name: payload},
        record,
        identity=ContributorIdentity(name="Test Contributor"),
        vault_key=_VAULT_KEY,
        agent="test-steward",
        now=_NOW,
    )
    return record.record_id


def _archive_with_one_record(root: Path, *, replica: Path | None = None) -> tuple[Archive, str]:
    """An archive holding one record, optionally armed to shred against ``replica``."""
    config = Config.default("Nothing-Verified Archive", root)
    if replica is not None:
        config.lockdown = LockdownConfig(
            stop_disclosure=True,
            shred_vault=True,
            required_replica_locations=[str(replica)],
            min_verified_replicas=1,
        )
    archive = Archive.init(config)
    return archive, _ingest(archive, "Thursday gatherings")


def _hollow_out(replica: Path) -> None:
    """Strip a replica of the archive's content, leaving config + vault behind.

    This is what a partial rsync, a copy that stopped after the metadata, or an
    emptied replica disk leaves on the far end: the location is perfectly
    readable (``check_readiness`` passes — the store and ``records/`` exist), the
    vault is sitting there ready to restore from, and there is not one byte of
    the archive to check.
    """
    shutil.rmtree(replica / "store" / "bags")
    (replica / "store" / "bags").mkdir()
    for stale in (replica / "store" / "records").glob("*.json"):
        stale.unlink()
    shutil.rmtree(replica / "store" / "index", ignore_errors=True)


def _empty_a_bags_manifests_at(bag: Path) -> None:
    """Leave a structurally valid bag at ``bag`` that declares nothing to check.

    Truncating the payload manifest and removing the tag manifests is enough:
    ``validate_bag`` then finds no entries to verify and no tag files declared,
    and returns a report with zero results. Nothing is *missing* and nothing
    *mismatches*, so neither the structural check nor the ``records/``
    reconciliation from issue #121 sees it — this is the hole those left open.
    """
    for payload in sorted((bag / "data").rglob("*")):
        if payload.is_file():
            payload.unlink()
    for manifest in sorted(bag.glob("manifest-*.txt")):
        manifest.write_text("", encoding="utf-8")
    for tagmanifest in sorted(bag.glob("tagmanifest-*.txt")):
        tagmanifest.unlink()


def _corrupt_a_payload(replica: Path) -> None:
    """Flip one byte of one payload so the replica FAILS fixity (not merely absent)."""
    payloads = sorted((replica / "store" / "bags").glob("*/data/*"))
    assert payloads, "fixture built no payload to corrupt"
    raw = bytearray(payloads[0].read_bytes())
    raw[0] ^= 0x01
    payloads[0].write_bytes(bytes(raw))


# --- the primitive ----------------------------------------------------------


def test_an_empty_audit_report_is_could_not_verify_and_not_ok() -> None:
    """``all([])`` is True; an audit that checked nothing is not a passing audit."""
    report = AuditReport(results=[])

    assert report.status is FixityStatus.UNVERIFIED
    assert report.ok is False
    assert report.checked == 0


def test_the_three_fixity_states_are_distinguishable() -> None:
    """A third state that is only a second name for failure would fix nothing."""
    passing = FixityResult(path="a", algo=HashAlgo.SHA256, expected="x", actual="x")
    failing = FixityResult(path="b", algo=HashAlgo.SHA256, expected="x", actual="y")

    assert AuditReport(results=[passing]).status is FixityStatus.VERIFIED
    assert AuditReport(results=[passing, failing]).status is FixityStatus.FAILED
    assert AuditReport(results=[]).status is FixityStatus.UNVERIFIED
    assert len({FixityStatus.VERIFIED, FixityStatus.FAILED, FixityStatus.UNVERIFIED}) == 3


def test_an_empty_manifest_proves_nothing_about_the_directory(tmp_path: Path) -> None:
    """``audit_files`` over a manifest that declares no entries is not a pass."""
    (tmp_path / "anything.txt").write_text("unlisted\n", encoding="utf-8")

    report = audit_files(tmp_path, {}, HashAlgo.SHA256)

    assert report.status is FixityStatus.UNVERIFIED
    assert report.ok is False


# --- the replica / shred gate -----------------------------------------------


def test_a_replica_holding_no_content_does_not_verify(tmp_path: Path) -> None:
    """THE MEASURED BUG. Before the fix this returned ``ok=True, reason=''``."""
    root = tmp_path / "arc"
    _archive_with_one_record(root)
    replica = tmp_path / "replica"
    shutil.copytree(root, replica)
    _hollow_out(replica)

    result = verify_backup_location(replica)

    assert result.bags == (), "the fixture must genuinely present zero bags"
    assert result.ok is False
    assert result.status is FixityStatus.UNVERIFIED
    assert result.reason == "nothing-verified"
    assert result.verified_bags == 0
    assert result.files_checked == 0
    # The vault IS there — which is exactly what made this dangerous: the replica
    # looked like something worth shredding the local copy for.
    assert result.has_vault is True


def test_an_empty_replica_is_reported_apart_from_a_corrupt_one(tmp_path: Path) -> None:
    """ "Your replica is empty" and "your replica is corrupt" call for opposite
    responses — re-run the copy job, or restore from somewhere else. Collapsing
    the new state into ``fixity-failed`` would send a steward under duress to
    repair bytes that are perfectly fine."""
    root = tmp_path / "arc"
    _archive_with_one_record(root)

    empty = tmp_path / "empty-replica"
    shutil.copytree(root, empty)
    _hollow_out(empty)

    corrupt = tmp_path / "corrupt-replica"
    shutil.copytree(root, corrupt)
    _corrupt_a_payload(corrupt)

    empty_result = verify_backup_location(empty)
    corrupt_result = verify_backup_location(corrupt)

    assert (empty_result.status, empty_result.reason) == (
        FixityStatus.UNVERIFIED,
        "nothing-verified",
    )
    assert (corrupt_result.status, corrupt_result.reason) == (
        FixityStatus.FAILED,
        "fixity-failed",
    )
    assert corrupt_result.failures == 1
    assert empty_result.failures == 0, "an absence is not a failed file"


def test_one_good_bag_does_not_carry_a_replica_whose_other_bag_proved_nothing(
    tmp_path: Path,
) -> None:
    """The roll-up's own version of the same trap, caught while writing this fix.

    "Failure dominates, and otherwise at least one bag verified" reads as a
    reasonable rule and is not one: a replica with one intact bag and one that
    declares nothing to check has *not* been verified, though nothing in it
    failed. `verified_bags` would say 1 while a whole record sat unproven, and
    the shred gate would take that as a clean replica.
    """
    root = tmp_path / "arc"
    archive, _rid = _archive_with_one_record(root)
    second = _ingest(archive, "Second record")
    replica = tmp_path / "replica"
    shutil.copytree(root, replica)
    _empty_a_bags_manifests_at(replica / "store" / "bags" / second)

    result = verify_backup_location(replica)

    assert len(result.bags) == 2, "both bags must still be present to reach the roll-up"
    assert result.verified_bags == 1
    assert result.ok is False
    assert result.status is FixityStatus.UNVERIFIED
    assert result.reason == "nothing-verified"


def test_a_faithful_replica_still_verifies(tmp_path: Path) -> None:
    """The positive control. If the good path had broken, every refusal below
    would pass for the wrong reason."""
    root = tmp_path / "arc"
    _archive_with_one_record(root)
    replica = tmp_path / "replica"
    shutil.copytree(root, replica)

    result = verify_backup_location(replica)

    assert result.ok is True
    assert result.status is FixityStatus.VERIFIED
    assert result.reason == ""
    assert result.verified_bags == 1
    assert result.files_checked > 0


def test_lockdown_will_not_shred_the_vault_for_a_replica_that_proved_nothing(
    tmp_path: Path,
) -> None:
    """The consequence that makes this a safety defect rather than a wording one.

    The local identity vault is destroyed irreversibly, and the only thing
    standing between a duress trigger and that destruction is
    ``verify_backup_location``. A replica with no content in it must not be able
    to authorise it.
    """
    root = tmp_path / "arc"
    replica = tmp_path / "replica"
    archive, _rid = _archive_with_one_record(root, replica=replica)
    shutil.copytree(root, replica)
    _hollow_out(replica)
    assert archive.vault_path.exists()

    with pytest.raises(LedgerError) as excinfo:
        execute_lockdown(archive, actor="steward-2", now=_NOW)

    # The vault survived, and the refusal names WHY in a no-outing-safe code.
    assert archive.vault_path.exists(), "an empty replica authorised an irreversible shred"
    assert "nothing-verified" in str(excinfo.value)
    # Disclosure is still frozen: refusing the shred must not undo the cheap,
    # reversible half of the duress posture.
    assert is_locked_down(archive)


def test_lockdown_still_shreds_for_a_replica_that_really_verified(tmp_path: Path) -> None:
    """The positive control for the gate itself. Without this, the refusal above
    could be a lockdown path that refuses everything."""
    root = tmp_path / "arc"
    replica = tmp_path / "replica"
    archive, _rid = _archive_with_one_record(root, replica=replica)
    shutil.copytree(root, replica)
    assert archive.vault_path.exists()

    result = execute_lockdown(archive, actor="steward-2", now=_NOW)

    assert result.vault_shredded is True
    assert result.verified_replicas == 1
    assert not archive.vault_path.exists()


# --- verify-backup ----------------------------------------------------------


def test_verify_backup_reports_could_not_verify_for_a_content_free_backup(
    tmp_path: Path,
) -> None:
    """``PASS: 0 bag(s) verified, 0 failed`` was the old answer here, with exit 0."""
    root = tmp_path / "arc"
    _archive_with_one_record(root)
    backup = tmp_path / "backup"
    shutil.copytree(root, backup)
    _hollow_out(backup)

    report = verify_backup(backup)

    assert report.ok is False
    assert report.status is FixityStatus.UNVERIFIED
    assert report.reason == "nothing-verified"
    assert report.failures == 0
    assert report.verified_bags == 0


def test_verify_backup_cli_exits_non_zero_on_a_backup_that_proved_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """End to end through the command a cron job actually runs and alarms on."""
    from ledger import cli

    root = tmp_path / "arc"
    _archive_with_one_record(root)
    backup = tmp_path / "backup"
    shutil.copytree(root, backup)
    _hollow_out(backup)

    exit_code = cli.main(["verify-backup", "--backup", str(backup)])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "COULD NOT VERIFY" in captured.err
    assert "PASS" not in captured.out


def test_verify_backup_cli_still_passes_a_faithful_copy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Positive control for the CLI surface."""
    from ledger import cli

    root = tmp_path / "arc"
    _archive_with_one_record(root)
    backup = tmp_path / "backup"
    shutil.copytree(root, backup)

    exit_code = cli.main(["verify-backup", "--backup", str(backup)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "PASS: backup at" in captured.out
    assert "1 of 1 bag(s) verified" in captured.out


# --- audit + attestation ----------------------------------------------------


def test_audit_does_not_print_pass_over_an_archive_holding_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A fresh archive is a legitimate state, so this stays exit 0 — but it must
    not print the same word as an archive whose every bag was just re-hashed."""
    from ledger import cli

    root = tmp_path / "arc"
    Archive.init(Config.default("Fresh Archive", root))

    exit_code = cli.main(["audit", "--root", str(root)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "NOTHING AUDITED" in captured.out
    assert "PASS:" not in captured.out


def test_a_bag_that_declares_nothing_to_check_is_not_a_passing_bag(tmp_path: Path) -> None:
    """The per-bag half of the same vacuity, reached without deleting anything
    the existing reconciliation would catch."""
    archive, rid = _archive_with_one_record(tmp_path / "arc")
    _empty_a_bags_manifests_at(archive.bags_dir / rid)

    reports = dict(archive.audit_fixity())

    assert list(reports) == [rid], "the bag must still be found, not reported as missing"
    assert reports[rid].checked == 0
    assert reports[rid].status is FixityStatus.UNVERIFIED
    assert reports[rid].ok is False


def test_a_signed_attestation_will_not_call_an_unverifiable_bag_healthy(tmp_path: Path) -> None:
    """``fixity_ok`` is what a steward signs and publishes at ``/proof``. A bag
    whose manifests were emptied must not be attested as having passed."""
    archive, rid = _archive_with_one_record(tmp_path / "arc")

    assert build_attestation(archive, now=_NOW).fixity_ok is True  # positive control

    _empty_a_bags_manifests_at(archive.bags_dir / rid)

    assert build_attestation(archive, now=_NOW).fixity_ok is False
