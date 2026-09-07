"""`ledger drill`: the rehearsal, and the ways a rehearsal can lie.

The scenarios themselves are the easy half. The tests that matter here are the
ones about a drill that cannot fail: an injection that silently no-ops, a fault
the archive's own check does not see, a scenario this archive's shape cannot
exercise, and a drill that writes to the archive it is supposed to be rehearsing.
Each of those has to end in a different word from "recovered".
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from ledger import drill
from ledger.cli import main as cli_main
from ledger.config import Config, LockdownConfig, StorageLocation
from ledger.drill import DrillOutcome
from ledger.ingest import Archive
from ledger.models import AccessPolicy, Record
from ledger.replicate import heal, replicate_bag
from ledger.tombstones import TombstoneStore

NOW = "2026-01-01T00:00:00Z"
DRILL_NOW = "2026-01-02T00:00:00Z"


def _build(root: Path, payload_dir: Path, *, mirrors: int = 1) -> tuple[Archive, str]:
    """An archive holding one public record, replicated to ``mirrors`` mirrors."""
    config = Config.default("Drill Test Archive", root)
    for index in range(mirrors):
        mirror = payload_dir / f"mirror{index}"
        mirror.mkdir(parents=True, exist_ok=True)
        config.locations.append(
            StorageLocation(name=f"offsite{index}", path=str(mirror), kind="mirror")
        )
    archive = Archive.init(config)
    payload = payload_dir / "note.txt"
    payload.write_text("hello archive", encoding="utf-8")
    record = Record(title="Public test", default_policy=AccessPolicy.PUBLIC)
    archive.ingest({"note.txt": payload}, record, now=NOW)
    for location in config.locations[1:]:
        replicate_bag(archive.bags_dir / record.record_id, location, agent="test", now=NOW)
    return archive, record.record_id


def _arm_lockdown(root: Path, replica: Path) -> None:
    """Copy the archive root to ``replica`` and configure it as the off-box replica.

    An off-box replica in ``lockdown.required_replica_locations`` is a whole archive
    root -- ``store/config.json``, ``store/bags/``, ``identity.vault`` -- not a bag
    mirror, because :func:`ledger.lockdown.verify_backup_location` loads the
    replica's own config and audits its bags. The mirrors ``_build`` makes are bag
    mirrors, so an archive can have three of them and still have nothing a lockdown
    would accept, which is exactly the state ``seized-primary`` reports rather than
    rehearses.
    """
    shutil.copytree(root, replica)
    config = Config.load(root / "store" / "config.json")
    config.lockdown = LockdownConfig(
        stop_disclosure=True,
        shred_vault=True,
        required_replica_locations=[str(replica)],
        min_verified_replicas=1,
    )
    config.save(root / "store" / "config.json")


#: A syntactically valid vault key. `Archive.init` seeds `identity.vault` only when
#: one is available, and `seized-primary` needs the vault *file* to exist -- it
#: shreds it and restores it, and never reads a byte of it, so the key's value is
#: irrelevant beyond being accepted.
_VAULT_KEY = "0123456789abcdef0123456789abcdef0123456789a="


@pytest.fixture
def archive_with_mirror(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("LEDGER_VAULT_KEY", _VAULT_KEY)
    _build(tmp_path / "arc", tmp_path / "data")
    _arm_lockdown(tmp_path / "arc", tmp_path / "offbox")
    return tmp_path / "arc"


@pytest.fixture
def archive_without_lockdown_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The same archive with no duress posture: `shred_vault` is off by default."""
    monkeypatch.setenv("LEDGER_VAULT_KEY", _VAULT_KEY)
    _build(tmp_path / "arc", tmp_path / "data")
    return tmp_path / "arc"


def _result(report: drill.DrillReport, name: str) -> drill.ScenarioResult:
    return next(r for r in report.results if r.scenario == name)


# --- the scenarios do what they say -------------------------------------------------


@pytest.mark.parametrize("scenario", sorted(drill.SCENARIOS))
def test_each_scenario_recovers_a_mirrored_archive(
    archive_with_mirror: Path, tmp_path: Path, scenario: str
) -> None:
    report = drill.run_drill(
        archive_with_mirror, tmp_path / "work", scenarios=[scenario], now=DRILL_NOW
    )
    result = _result(report, scenario)
    assert result.outcome is DrillOutcome.RECOVERED
    assert result.fault_landed, "a recovery credited without a confirmed fault is not one"
    assert result.fault_detected, "a recovery credited without detection is not one"
    assert result.failing_step is None
    assert result.bags_touched == 1
    assert report.exit_code == 0


def test_the_registry_is_closed(archive_with_mirror: Path, tmp_path: Path) -> None:
    """An unknown scenario is refused, not silently rehearsed as nothing."""
    with pytest.raises(drill.DrillError, match="unknown scenario"):
        drill.run_drill(archive_with_mirror, tmp_path / "work", scenarios=["earthquake"])


def test_each_scenario_gets_its_own_copy(archive_with_mirror: Path, tmp_path: Path) -> None:
    """One scenario's damage must not become the next one's starting point."""
    report = drill.run_drill(archive_with_mirror, tmp_path / "work", now=DRILL_NOW)
    assert len(report.results) == len(drill.SCENARIOS)
    assert all(r.outcome is DrillOutcome.RECOVERED for r in report.results)
    staged = sorted(p.name for p in (tmp_path / "work").iterdir() if p.is_dir())
    assert staged == sorted(drill.SCENARIOS)


# --- a scenario this archive cannot exercise ----------------------------------------


def test_a_single_location_archive_reports_not_applicable_never_recovered(
    tmp_path: Path,
) -> None:
    """The acceptance criterion, and the reason the outcome has three states."""
    root = tmp_path / "arc"
    archive = Archive.init(Config.default("Single Box", root))
    payload = tmp_path / "note.txt"
    payload.write_text("x", encoding="utf-8")
    archive.ingest(
        {"note.txt": payload},
        Record(title="t", default_policy=AccessPolicy.PUBLIC),
        now=NOW,
    )

    report = drill.run_drill(root, tmp_path / "work", now=DRILL_NOW)
    for result in report.results:
        assert result.outcome is DrillOutcome.NOT_APPLICABLE
        assert result.outcome is not DrillOutcome.RECOVERED
        assert not result.fault_landed
        assert result.detail, f"{result.scenario} was skipped without saying why"
    # Each scenario names the reason *it* cannot run, rather than one generic
    # sentence: this archive has no mirror, and it also has no duress posture, and
    # a steward reading the report has a different next step for each.
    assert "no mirror location" in _result(report, "bit-rot").detail
    assert "not configured to shred" in _result(report, "seized-primary").detail
    # Not-applicable is reported, not failed: an archive with one box genuinely
    # cannot rehearse losing a mirror, and reddening for it would train a steward
    # to ignore the command.
    assert report.exit_code == 0


def test_an_archive_with_no_bags_reports_not_applicable(tmp_path: Path) -> None:
    root = tmp_path / "arc"
    config = Config.default("Empty", root)
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    config.locations.append(StorageLocation(name="offsite", path=str(mirror), kind="mirror"))
    Archive.init(config)

    report = drill.run_drill(root, tmp_path / "work", now=DRILL_NOW)
    for result in report.results:
        assert result.outcome is DrillOutcome.NOT_APPLICABLE
        assert result.detail, f"{result.scenario} was skipped without saying why"
    assert "no bags" in _result(report, "bit-rot").detail
    # This archive has no duress posture either, and that reason is reached first.
    assert "not configured to shred" in _result(report, "seized-primary").detail


# --- a drill that cannot fail is worse than no drill --------------------------------


def _scenario_with(
    base: drill.Scenario,
    *,
    inject: Callable[[drill.StagedArchive], drill.Injection] | None = None,
    detect: Callable[[drill.StagedArchive], bool] | None = None,
) -> drill.Scenario:
    return drill.Scenario(
        name=base.name,
        fault=base.fault,
        recovering_command=base.recovering_command,
        applicability=base.applicability,
        inject=inject or base.inject,
        detect=detect or base.detect,
        recover=base.recover,
        verify=base.verify,
    )


def test_an_injection_that_no_ops_fails_rather_than_reporting_a_recovery(
    archive_with_mirror: Path, tmp_path: Path
) -> None:
    """The sabotage that silently does nothing is the one that reads as a pass.

    Recovery would trivially "succeed" over an undamaged archive, so the runner
    must stop before it, at the injection step, and say so.
    """
    staged = drill.stage(archive_with_mirror, tmp_path / "work")
    no_op = _scenario_with(
        drill.SCENARIOS["bit-rot"],
        inject=lambda _: drill.Injection(
            description="nothing was changed", landed=False, bags_touched=0, reason="no-op"
        ),
    )
    result = drill.run_scenario(no_op, staged)
    assert result.outcome is DrillOutcome.FAILED
    assert result.failing_step == "inject"
    assert not result.fault_landed
    assert "nothing was rehearsed" in result.detail


def test_a_fault_the_archive_cannot_see_fails_before_recovery_is_attempted(
    archive_with_mirror: Path, tmp_path: Path
) -> None:
    """The most interesting failure a drill can find.

    The damage is real; the check a steward runs does not report it. Healing might
    still make the archive whole, and crediting that would be a lie: nothing would
    ever have called the repair.
    """
    staged = drill.stage(archive_with_mirror, tmp_path / "work")
    blind = _scenario_with(drill.SCENARIOS["bit-rot"], detect=lambda _: False)
    result = drill.run_scenario(blind, staged)
    assert result.outcome is DrillOutcome.FAILED
    assert result.failing_step == "detect"
    assert result.fault_landed and not result.fault_detected
    assert "did not report it" in result.detail


def test_the_real_detector_sees_each_real_injection(
    archive_with_mirror: Path, tmp_path: Path
) -> None:
    """The complement of the two tests above: with no sabotage, detection is real.

    Without this, a `detect` that returned True unconditionally would satisfy every
    other assertion in this file.
    """
    for name, scenario in drill.SCENARIOS.items():
        staged = drill.stage(archive_with_mirror, tmp_path / f"work-{name}")
        assert not scenario.detect(staged), f"{name}: the undamaged copy already looks broken"
        injection = scenario.inject(staged)
        assert injection.landed, f"{name}: the injection did not land"
        assert scenario.detect(staged), f"{name}: the real fault went undetected"


def test_a_recovery_that_raises_is_a_failure_naming_the_step(
    archive_with_mirror: Path, tmp_path: Path
) -> None:
    staged = drill.stage(archive_with_mirror, tmp_path / "work")

    def explode(_: drill.StagedArchive) -> None:
        raise RuntimeError("no verified replica")

    base = drill.SCENARIOS["bit-rot"]
    broken = drill.Scenario(
        name=base.name,
        fault=base.fault,
        recovering_command=base.recovering_command,
        applicability=base.applicability,
        inject=base.inject,
        detect=base.detect,
        recover=explode,
        verify=base.verify,
    )
    result = drill.run_scenario(broken, staged)
    assert result.outcome is DrillOutcome.FAILED
    assert result.failing_step == "recover"
    assert "RuntimeError" in result.detail


# --- the live archive ---------------------------------------------------------------


def test_the_live_archive_is_byte_identical_before_and_after(
    archive_with_mirror: Path, tmp_path: Path
) -> None:
    """The acceptance criterion, and the one thing this command must never do."""
    before = drill.tree_digest(archive_with_mirror)
    report = drill.run_drill(archive_with_mirror, tmp_path / "work", now=DRILL_NOW)
    after = drill.tree_digest(archive_with_mirror)
    assert before == after
    assert report.live_digest_before == before
    assert report.live_digest_after == after
    assert report.live_archive_untouched


def test_a_drill_that_moved_the_live_archive_exits_nonzero_however_well_it_went() -> None:
    """Constructed directly: every scenario recovered, and it still fails.

    A drill whose own damage escaped the scratch copy has invalidated its own
    findings, so the report says so and the exit code follows the digest rather
    than the scenarios.
    """
    good = drill.ScenarioResult(
        scenario="bit-rot",
        outcome=DrillOutcome.RECOVERED,
        fault="f",
        fault_landed=True,
        fault_detected=True,
        bags_touched=1,
        recovering_command="ledger heal",
        detail="",
    )
    report = drill.DrillReport(
        generated_date=DRILL_NOW,
        archive_name="a",
        results=(good,),
        live_digest_before="aaa",
        live_digest_after="bbb",
    )
    assert not report.live_archive_untouched
    assert report.exit_code == 1
    assert "The live archive changed during this drill" in report.to_markdown()


def test_tree_digest_notices_a_rename_not_only_a_content_change(tmp_path: Path) -> None:
    """Paths are hashed too, so a deletion or rename cannot slip past."""
    root = tmp_path / "t"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "a").write_bytes(b"same")
    first = drill.tree_digest(root)
    (root / "sub" / "a").rename(root / "sub" / "b")
    assert drill.tree_digest(root) != first
    (root / "sub" / "b").rename(root / "sub" / "a")
    assert drill.tree_digest(root) == first


def test_tree_digest_of_an_absent_tree_is_a_word_not_an_empty_hash(tmp_path: Path) -> None:
    """An absent archive must not digest to the same value as an empty one."""
    assert drill.tree_digest(tmp_path / "nope") == "absent"
    (tmp_path / "empty").mkdir()
    assert drill.tree_digest(tmp_path / "empty") != "absent"


# --- staging ------------------------------------------------------------------------


def test_a_location_inside_the_archive_is_remapped_not_duplicated(
    archive_with_mirror: Path, tmp_path: Path
) -> None:
    """`primary` points at ``store/bags``. It must follow the staged store.

    Copying it out to its own directory instead would mean a heal repaired a copy
    nothing reads, and the scenario would credit a recovery of the authoritative
    bags that never happened.
    """
    staged = drill.stage(archive_with_mirror, tmp_path / "work")
    primary = next(loc for loc in staged.locations if loc.name == "primary")
    assert Path(primary.path) == staged.store_root / "bags"
    assert staged.store_root.is_relative_to(tmp_path / "work")


def test_staging_an_absent_mirror_yields_an_empty_directory_not_a_crash(
    tmp_path: Path,
) -> None:
    root = tmp_path / "arc"
    config = Config.default("Gone", root)
    config.locations.append(
        StorageLocation(name="offsite", path=str(tmp_path / "never-made"), kind="mirror")
    )
    Archive.init(config)
    staged = drill.stage(root, tmp_path / "work")
    mirror = next(loc for loc in staged.locations if loc.name == "offsite")
    assert Path(mirror.path).is_dir()
    assert not any(Path(mirror.path).iterdir())


def test_a_directory_that_is_not_an_archive_is_refused(tmp_path: Path) -> None:
    (tmp_path / "not-an-archive").mkdir()
    with pytest.raises(drill.DrillError, match="no ledger archive"):
        drill.run_drill(tmp_path / "not-an-archive", tmp_path / "work")


# --- the report ---------------------------------------------------------------------


def test_the_report_carries_no_identity_or_payload(
    archive_with_mirror: Path, tmp_path: Path
) -> None:
    """No-outing: names, counts, digests and command names only."""
    report = drill.run_drill(archive_with_mirror, tmp_path / "work", now=DRILL_NOW)
    text = report.to_markdown() + json.dumps(report.to_dict())
    assert "hello archive" not in text
    assert "Public test" not in text


def test_the_report_states_landed_and_detected_per_scenario(
    archive_with_mirror: Path, tmp_path: Path
) -> None:
    report = drill.run_drill(archive_with_mirror, tmp_path / "work", now=DRILL_NOW)
    markdown = report.to_markdown()
    assert "| Scenario | Fault | Landed | Detected | Bags | Recovered by | Outcome |" in markdown
    for name in drill.SCENARIOS:
        assert f"`{name}`" in markdown
    assert "The drill wrote nothing to the live archive." in markdown


def test_write_report_puts_a_dated_copy_in_audits(
    archive_with_mirror: Path, tmp_path: Path
) -> None:
    report = drill.run_drill(archive_with_mirror, tmp_path / "work", now=DRILL_NOW)
    path = drill.write_report(report, archive_with_mirror / "store" / "audits")
    assert path.name == "recovery-drill-2026-01-02.md"
    assert path.read_text(encoding="utf-8") == report.to_markdown()
    assert report.report_path == path


# --- the CLI ------------------------------------------------------------------------


def test_cli_drill_reports_and_exits_zero(
    archive_with_mirror: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli_main(
        [
            "drill",
            "--root",
            str(archive_with_mirror),
            "--workdir",
            str(tmp_path / "work"),
            "--now",
            DRILL_NOW,
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "ledger recovery drill" in out
    assert "[RECOVERED" in out
    assert "the live archive was not written" in out
    audits = archive_with_mirror / "store" / "audits"
    assert (audits / "recovery-drill-2026-01-02.md").is_file()
    assert (audits / "recovery-drill-2026-01-02.json").is_file()


def test_cli_drill_json_is_machine_readable(
    archive_with_mirror: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli_main(
        [
            "drill",
            "--root",
            str(archive_with_mirror),
            "--workdir",
            str(tmp_path / "work"),
            "--scenario",
            "bit-rot",
            "--json",
            "--no-report",
            "--now",
            DRILL_NOW,
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["live_archive_untouched"] is True
    assert [s["scenario"] for s in payload["scenarios"]] == ["bit-rot"]
    assert payload["scenarios"][0]["outcome"] == "recovered"
    assert not (archive_with_mirror / "store" / "audits").exists()


def test_cli_drill_refuses_an_unknown_scenario(
    archive_with_mirror: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli_main(
        [
            "drill",
            "--root",
            str(archive_with_mirror),
            "--workdir",
            str(tmp_path / "work"),
            "--scenario",
            "earthquake",
        ]
    )
    assert code == 2
    assert "unknown scenario" in capsys.readouterr().err


# --- checkup cites the last drill ---------------------------------------------------


def test_checkup_cannot_verify_recovery_before_any_drill_has_run(
    archive_with_mirror: Path,
) -> None:
    """No recorded drill is `could-not-verify`, never a pass.

    An archive nobody has rehearsed is not one whose recovery paths are known to
    work. It is also not one whose paths are known to be broken, so this must not
    be a fail either.
    """
    from ledger.checkup import CheckStatus, run_checkup

    archive = Archive(Config.load(archive_with_mirror / "store" / "config.json"))
    report = run_checkup(archive, env={}, platform="linux", now=NOW, write_report=False)
    result = next(r for r in report.results if r.check_id == "recovery-drill")
    assert result.status is CheckStatus.UNVERIFIED
    assert "No recovery drill is recorded" in result.explanation


def test_checkup_reads_the_last_drill_back_and_passes(
    archive_with_mirror: Path, tmp_path: Path
) -> None:
    from ledger.checkup import CheckStatus, run_checkup

    report = drill.run_drill(archive_with_mirror, tmp_path / "work", now=DRILL_NOW)
    drill.write_report(report, archive_with_mirror / "store" / "audits")

    archive = Archive(Config.load(archive_with_mirror / "store" / "config.json"))
    readiness = run_checkup(archive, env={}, platform="linux", now=NOW, write_report=False)
    result = next(r for r in readiness.results if r.check_id == "recovery-drill")
    assert result.status is CheckStatus.PASS
    # Derived, not written down: a literal here would have to be edited every time
    # the registry grows, and until somebody did the assertion would be checking a
    # stale number rather than the sentence checkup actually renders.
    assert f"recovered from {len(drill.SCENARIOS)} injected fault(s)" in result.explanation
    for name in drill.SCENARIOS:
        assert name in result.explanation


def test_checkup_fails_when_the_last_drill_recorded_a_failure(
    archive_with_mirror: Path,
) -> None:
    from ledger.checkup import CheckStatus, run_checkup

    failed = drill.ScenarioResult(
        scenario="bit-rot",
        outcome=DrillOutcome.FAILED,
        fault="f",
        fault_landed=True,
        fault_detected=True,
        bags_touched=1,
        recovering_command="ledger heal",
        detail="",
        failing_step="verify",
    )
    report = drill.DrillReport(
        generated_date=DRILL_NOW,
        archive_name="a",
        results=(failed,),
        live_digest_before="x",
        live_digest_after="x",
    )
    drill.write_report(report, archive_with_mirror / "store" / "audits")

    archive = Archive(Config.load(archive_with_mirror / "store" / "config.json"))
    readiness = run_checkup(archive, env={}, platform="linux", now=NOW, write_report=False)
    result = next(r for r in readiness.results if r.check_id == "recovery-drill")
    assert result.status is CheckStatus.FAIL
    assert "could not recover from: bit-rot" in result.explanation


def test_checkup_does_not_read_not_applicable_as_a_recovery(
    archive_with_mirror: Path, tmp_path: Path
) -> None:
    """A drill in which nothing was exercised must not read as a clean bill.

    This test used to assert `CheckStatus.PASS` here, which contradicted its own
    first sentence. The explanation named the not-applicable scenarios, so
    "3 recovered" could never mean "0 recovered, 3 skipped"; but the *status*
    was a pass, and `CheckupReport.readiness` rolls a pass up to green.

    Measured on origin/main, 2026-09-06:

        every scenario not-applicable  ->  pass,             green,  exit 0
        no drill recorded at all       ->  could-not-verify, yellow, exit 0

    Running a drill that rehearsed nothing scored better than not running one.
    """
    from ledger.checkup import CheckStatus, Readiness, run_checkup

    skipped = drill.ScenarioResult(
        scenario="lost-location",
        outcome=DrillOutcome.NOT_APPLICABLE,
        fault="f",
        fault_landed=False,
        fault_detected=False,
        bags_touched=0,
        recovering_command="ledger heal",
        detail="one location",
        failing_step="applicability",
    )
    report = drill.DrillReport(
        generated_date=DRILL_NOW,
        archive_name="a",
        results=(skipped,),
        live_digest_before="x",
        live_digest_after="x",
    )
    drill.write_report(report, archive_with_mirror / "store" / "audits")

    archive = Archive(Config.load(archive_with_mirror / "store" / "config.json"))
    readiness = run_checkup(archive, env={}, platform="linux", now=NOW, write_report=False)
    result = next(r for r in readiness.results if r.check_id == "recovery-drill")
    assert result.status is CheckStatus.UNVERIFIED
    assert "recovered from no injected fault" in result.explanation
    assert "not-applicable, not as passes" in result.explanation
    assert readiness.readiness is not Readiness.GREEN


def test_checkup_fails_when_the_last_drill_touched_the_live_archive(
    archive_with_mirror: Path,
) -> None:
    from ledger.checkup import CheckStatus, run_checkup

    report = drill.DrillReport(
        generated_date=DRILL_NOW,
        archive_name="a",
        results=(),
        live_digest_before="x",
        live_digest_after="y",
    )
    drill.write_report(report, archive_with_mirror / "store" / "audits")

    archive = Archive(Config.load(archive_with_mirror / "store" / "config.json"))
    readiness = run_checkup(archive, env={}, platform="linux", now=NOW, write_report=False)
    result = next(r for r in readiness.results if r.check_id == "recovery-drill")
    assert result.status is CheckStatus.FAIL
    assert "cannot be trusted" in result.explanation


def test_latest_drill_of_an_unreadable_report_is_none_not_an_empty_success(
    tmp_path: Path,
) -> None:
    """A corrupt report is "no drill", which checkup renders as could-not-verify.

    Returning an empty summary would look like a drill in which nothing failed.
    """
    audits = tmp_path / "audits"
    audits.mkdir()
    (audits / f"{drill.REPORT_PREFIX}2026-01-02.json").write_text("{not json", encoding="utf-8")
    assert drill.latest_drill(audits) is None
    assert drill.latest_drill(tmp_path / "nowhere") is None


# --- stale-replica: the scenario whose recovery is a refusal --------------------------
#
# Every other scenario asks "did the archive come back". This one asks "did the
# archive refuse to bring something back", so the tests below are the inverse
# shape: they assert absence, and they assert that the machinery which produces
# the absence is load-bearing rather than incidental.


def _tombstoned_id(staged: drill.StagedArchive) -> str:
    tombstones = TombstoneStore(staged.store_root / "logs").all()
    assert len(tombstones) == 1, "the scenario takes exactly one record down"
    return tombstones[0].record_id


def test_stale_replica_ends_with_the_record_gone_from_every_location(
    archive_with_mirror: Path, tmp_path: Path
) -> None:
    """The acceptance criterion: the reattaching copy is deleted, and recorded as deleted."""
    report = drill.run_drill(
        archive_with_mirror, tmp_path / "work", scenarios=["stale-replica"], now=DRILL_NOW
    )
    result = _result(report, "stale-replica")
    assert result.outcome is DrillOutcome.RECOVERED
    assert result.fault_landed and result.fault_detected
    assert result.recovering_command == "ledger heal"

    staged_root = tmp_path / "work" / "stale-replica" / "archive"
    config = Config.load(staged_root / "store" / "config.json")
    store = TombstoneStore(Path(config.store_root) / "logs")
    tombstone = store.all()[0]
    for location in config.locations:
        assert not (Path(location.path) / tombstone.record_id).exists(), (
            f"location {location.name!r} still holds the taken-down record"
        )
        assert tombstone.is_confirmed_at(location.name), (
            f"location {location.name!r} deleted the copy but recorded no receipt, so the "
            "archive cannot say the removal was applied there"
        )
    # And the drill did not touch the archive it was rehearsing.
    assert report.live_archive_untouched
    assert report.exit_code == 0


def test_a_heal_without_the_tombstone_store_resurrects_the_taken_down_record(
    archive_with_mirror: Path, tmp_path: Path
) -> None:
    """Why the refusal is real: the same heal, minus the store, brings the record back.

    This is the control for the whole scenario. If `heal` removed a taken-down bag
    for some other reason, `stale-replica` would be crediting a refusal that owed
    nothing to the tombstone machinery, and passing the store to `_heal_the_first_bag`
    would be decoration. It is not: without it the stale mirror wins.
    """
    staged = drill.stage(archive_with_mirror, tmp_path / "stage")
    injection = drill._inject_stale_replica(staged)
    assert injection.landed, injection.reason
    record_id = _tombstoned_id(staged)
    bags = staged.store_root / "bags"
    assert not (bags / record_id).exists(), "the takedown removed the authoritative copy"

    heal(record_id, list(staged.locations), agent="test", now=NOW)  # no tombstones passed

    assert (bags / record_id).exists(), (
        "a tombstone-blind heal is expected to copy the stale mirror back over the "
        "authoritative store; if it no longer does, this scenario's premise has moved"
    )
    # ...and the scenario's own source check is what catches exactly that.
    assert not drill._no_tombstoned_bag_survives_in_the_store(staged)


def test_the_drills_heal_applies_pending_takedowns_like_the_cli_does(
    archive_with_mirror: Path, tmp_path: Path
) -> None:
    """`_heal_the_first_bag` is `ledger heal`, tombstones included, or it is a different command."""
    staged = drill.stage(archive_with_mirror, tmp_path / "stage")
    record_id = staged.bag_names()[0]
    TombstoneStore(staged.store_root / "logs").add(record_id, NOW)
    assert (staged.store_root / "bags" / record_id).exists()

    drill._heal_the_first_bag(staged)

    for location in staged.locations:
        assert not (Path(location.path) / record_id).exists(), (
            f"the drill's heal left a taken-down copy at {location.name!r}; the report "
            "names `ledger heal`, which would not have"
        )


def test_the_default_source_check_would_have_failed_a_correct_refusal(
    archive_with_mirror: Path, tmp_path: Path
) -> None:
    """Why `source_check` is a field on the scenario and not a fixed call in the runner."""
    staged = drill.stage(archive_with_mirror, tmp_path / "stage")
    result = drill.run_scenario(drill.SCENARIOS["stale-replica"], staged)
    assert result.outcome is DrillOutcome.RECOVERED

    assert drill.SCENARIOS["stale-replica"].source_check(staged) is True
    assert drill._default_source_check(staged) is False, (
        "the generic check reads 'the first bag still validates'; after a correct "
        "takedown there is no first bag, so running it here would call the refusal a failure"
    )


def test_a_takedown_already_on_record_is_not_credited_as_an_injection(
    archive_with_mirror: Path, tmp_path: Path
) -> None:
    """A drill must not report recovering from a fault that was there before it ran."""
    config = Config.load(archive_with_mirror / "store" / "config.json")
    store_root = Path(config.store_root)
    record_id = sorted(p.name for p in (store_root / "bags").iterdir())[0]
    TombstoneStore(store_root / "logs").add(record_id, NOW)

    report = drill.run_drill(
        archive_with_mirror, tmp_path / "work", scenarios=["stale-replica"], now=DRILL_NOW
    )
    result = _result(report, "stale-replica")
    assert result.outcome is DrillOutcome.FAILED
    assert result.failing_step == "inject"
    assert not result.fault_landed
    assert "already" in result.detail
    assert report.exit_code == 1


def test_stale_replica_needs_a_mirror_to_leave_a_copy_behind(tmp_path: Path) -> None:
    """One box cannot rehearse a copy outliving a takedown; that is not-applicable."""
    root = tmp_path / "arc"
    archive = Archive.init(Config.default("Single Box", root))
    payload = tmp_path / "note.txt"
    payload.write_text("x", encoding="utf-8")
    archive.ingest(
        {"note.txt": payload},
        Record(title="t", default_policy=AccessPolicy.PUBLIC),
        now=NOW,
    )
    report = drill.run_drill(root, tmp_path / "work", scenarios=["stale-replica"], now=DRILL_NOW)
    result = _result(report, "stale-replica")
    assert result.outcome is DrillOutcome.NOT_APPLICABLE
    assert "no mirror location" in result.detail
    assert not result.fault_landed
    assert report.exit_code == 0
    # The record is untouched: a not-applicable scenario rehearses nothing.
    assert (Path(archive.config.store_root) / "bags").iterdir()


def test_the_refusal_checks_refuse_to_pass_over_an_empty_store(
    archive_with_mirror: Path, tmp_path: Path
) -> None:
    """Neither half of the verdict may be satisfied by there being nothing to check.

    `all()` over an empty tombstone list is `True`, and both checks below are
    written as `all(...)`. Without their guards, an archive in which no takedown
    was ever recorded would satisfy "no location holds a taken-down record" and
    "no taken-down bag survives" — absence read as a clean result.
    """
    staged = drill.stage(archive_with_mirror, tmp_path / "stage")
    assert TombstoneStore(staged.store_root / "logs").all() == []
    assert drill._every_stale_copy_is_gone_and_recorded(staged) is False
    assert drill._no_tombstoned_bag_survives_in_the_store(staged) is False


def test_deleting_the_copies_without_a_receipt_is_not_a_recovery(
    archive_with_mirror: Path, tmp_path: Path
) -> None:
    """The removal has to be *recorded* at each location, not merely performed.

    A sweep that deleted the stale copies but wrote no confirmation leaves the
    archive unable to say the takedown was applied, so `/consent-status` reports it
    pending forever and the next reattach has nothing to compare against.
    """
    staged = drill.stage(archive_with_mirror, tmp_path / "stage")
    injection = drill._inject_stale_replica(staged)
    assert injection.landed, injection.reason
    record_id = _tombstoned_id(staged)

    for location in staged.locations:
        copy = Path(location.path) / record_id
        if copy.exists():
            shutil.rmtree(copy)

    assert drill._a_location_still_holds_a_taken_down_bag(staged) is False
    assert drill._every_stale_copy_is_gone_and_recorded(staged) is False, (
        "every copy is gone, but no location has confirmed the removal"
    )


# --- a drill that rehearsed nothing must not read as ready ---------------------------


def test_checkup_cannot_verify_a_drill_that_ran_no_scenario(
    archive_with_mirror: Path,
) -> None:
    """A recorded drill holding no scenario at all is the same fact as no drill."""
    from ledger.checkup import CheckStatus, Readiness, run_checkup

    report = drill.DrillReport(
        generated_date=DRILL_NOW,
        archive_name="a",
        results=(),
        live_digest_before="x",
        live_digest_after="x",
    )
    drill.write_report(report, archive_with_mirror / "store" / "audits")

    archive = Archive(Config.load(archive_with_mirror / "store" / "config.json"))
    readiness = run_checkup(archive, env={}, platform="linux", now=NOW, write_report=False)
    result = next(r for r in readiness.results if r.check_id == "recovery-drill")
    assert result.status is CheckStatus.UNVERIFIED
    assert readiness.readiness is not Readiness.GREEN


def test_latest_drill_of_a_document_with_no_scenarios_list_is_none(tmp_path: Path) -> None:
    """Some other JSON in audits/ is not a drill report with nothing in it.

    Reading it as one produced `live_archive_untouched=False` by default, and
    checkup rendered that as "the drill changed the live archive while it ran",
    a specific accusation about a run that was never read.
    """
    audits = tmp_path / "audits"
    audits.mkdir()
    (audits / f"{drill.REPORT_PREFIX}2026-01-02.json").write_text(
        json.dumps({"note": "some other json someone dropped in audits/"}), encoding="utf-8"
    )
    assert drill.latest_drill(audits) is None


def _write_summary(audits: Path, scenarios: list[dict[str, str]]) -> None:
    audits.mkdir(parents=True, exist_ok=True)
    (audits / f"{drill.REPORT_PREFIX}2026-01-02.json").write_text(
        json.dumps(
            {
                "generated_date": "2026-01-02T00:00:00Z",
                "live_archive_untouched": True,
                "scenarios": scenarios,
            }
        ),
        encoding="utf-8",
    )


def test_a_partial_drill_names_every_scenario_it_did_not_rehearse(tmp_path: Path) -> None:
    """`ledger drill --scenario <name>` runs a partial drill, and it said so nowhere.

    One recovered fault read exactly like a full rehearsal. The gap is a set
    difference against `drill.SCENARIOS` taken at call time, so a scenario added
    to the registry widens this sentence with nothing here to update. Asserted as
    a set rather than a count for the same reason: a count in this test would go
    stale the moment the registry grows, and would still pass.
    """
    from ledger.checkup import CheckStatus, _check_recovery_drill

    rehearsed = "bit-rot"
    assert rehearsed in drill.SCENARIOS
    expected_gap = set(drill.SCENARIOS) - {rehearsed}
    assert expected_gap, "a one-scenario registry cannot demonstrate a partial drill"

    _write_summary(tmp_path / "audits", [{"scenario": rehearsed, "outcome": "recovered"}])
    result = _check_recovery_drill(tmp_path)

    assert result.status is CheckStatus.PASS, "one real recovery is a real, partial result"
    for name in expected_gap:
        assert name in result.explanation, name
    assert "were not rehearsed at all" in result.explanation


def test_a_full_drill_claims_no_unrehearsed_scenario(tmp_path: Path) -> None:
    """The disclosure appears only when the set difference is non-empty."""
    from ledger.checkup import CheckStatus, _check_recovery_drill

    _write_summary(
        tmp_path / "audits",
        [{"scenario": name, "outcome": "recovered"} for name in drill.SCENARIOS],
    )
    result = _check_recovery_drill(tmp_path)

    assert result.status is CheckStatus.PASS
    assert "were not rehearsed at all" not in result.explanation


# --- the staged copy must not be able to reach the live archive ----------------------


def _strings(value: object) -> list[str]:
    """Every string anywhere in a nested config document."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def test_no_path_in_a_staged_config_points_outside_the_workdir(
    archive_with_mirror: Path, tmp_path: Path
) -> None:
    """`stage`'s promise, held over the whole config rather than three known fields.

    It rewrote `store_root`, `vault_path` and `locations`, and its docstring said
    "nothing in the staged archive can reach back to the real one" on the strength
    of those. `lockdown.required_replica_locations` was not among them, and it is
    the set `execute_lockdown` verifies before shredding a vault and
    `execute_stand_up` copies a vault back *from* — so a `seized-primary` rehearsal
    would have read the community's real off-box replica and copied real vault bytes
    into a scratch directory.

    Derived from the config document rather than a list of field names, so a path
    field added later is covered with nothing here to update. Every absolute path is
    checked, because a config value that starts with `/` on this fixture is a path.
    """
    workdir = tmp_path / "work"
    staged = drill.stage(archive_with_mirror, workdir)
    outside = [
        value
        for value in _strings(staged.config.to_dict())
        if value.startswith("/") and not Path(value).resolve().is_relative_to(workdir.resolve())
    ]
    assert outside == [], (
        f"the staged config still points outside {workdir}: {outside}. A scenario acting on "
        "one of these would act on the live archive's own storage."
    )


def test_the_staged_replica_is_the_same_copy_the_locations_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A replica configured both as a mirror and as the duress replica is one copy.

    Two copies would let a scenario heal one and verify the other, and credit a
    recovery that happened somewhere nobody reads.
    """
    monkeypatch.setenv("LEDGER_VAULT_KEY", _VAULT_KEY)
    root = tmp_path / "arc"
    _build(root, tmp_path / "data")
    shared = tmp_path / "shared"
    shutil.copytree(root, shared)
    config = Config.load(root / "store" / "config.json")
    config.locations.append(StorageLocation(name="dual", path=str(shared), kind="mirror"))
    config.lockdown = LockdownConfig(
        stop_disclosure=True,
        shred_vault=True,
        required_replica_locations=[str(shared)],
        min_verified_replicas=1,
    )
    config.save(root / "store" / "config.json")

    staged = drill.stage(root, tmp_path / "work")
    replica = staged.config.lockdown.required_replica_locations[0]
    dual = next(loc.path for loc in staged.locations if loc.name == "dual")
    assert Path(replica).resolve() == Path(dual).resolve()


def test_a_stewards_signing_key_is_never_carried_into_the_workdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cleared rather than copied: a rehearsal has no business holding a private key."""
    monkeypatch.setenv("LEDGER_VAULT_KEY", _VAULT_KEY)
    root = tmp_path / "arc"
    _build(root, tmp_path / "data")
    key = tmp_path / "signing-key"
    key.write_text("not a real key", encoding="utf-8")
    config = Config.load(root / "store" / "config.json")
    config.attestation_signing_key = str(key)
    config.save(root / "store" / "config.json")

    staged = drill.stage(root, tmp_path / "work")
    assert staged.config.attestation_signing_key == ""
    assert not (tmp_path / "work" / "signing-key").exists()


# --- seized-primary ------------------------------------------------------------------


def test_seized_primary_shreds_the_vault_and_stands_it_back_up(
    archive_with_mirror: Path, tmp_path: Path
) -> None:
    """The whole transition, asserted at both ends rather than only at the end.

    A stand-up that ran over an un-shredded vault would end with the vault present
    and pass, having rehearsed nothing. So the injection's landing check is read
    here too: the flag written *and* the vault file gone before recovery ran.
    """
    workdir = tmp_path / "work"
    report = drill.run_drill(
        archive_with_mirror, workdir, scenarios=["seized-primary"], now=DRILL_NOW
    )
    result = _result(report, "seized-primary")
    assert result.outcome is DrillOutcome.RECOVERED
    assert result.fault_landed and result.fault_detected
    assert result.recovering_command == "ledger stand-up"
    assert "shredded" in result.detail

    staged_config = Config.load(workdir / "seized-primary" / "archive" / "store" / "config.json")
    vault = Path(staged_config.vault_path)
    assert vault.is_file(), "the stand-up did not restore the vault"
    replica_vault = Path(staged_config.lockdown.required_replica_locations[0]) / "identity.vault"
    assert vault.read_bytes() == replica_vault.read_bytes()
    assert not (Path(staged_config.store_root) / "logs" / "lockdown.flag").exists()


def test_the_live_archive_and_its_off_box_replica_are_untouched(
    archive_with_mirror: Path, tmp_path: Path
) -> None:
    """The scenario destroys a vault. It must destroy a copy of one.

    **Measured, and stated because the name overpromises otherwise:** this test
    passes with the staging leak reinstated. `execute_lockdown` only *reads* the
    off-box replica (verification) and `execute_stand_up` only reads it (a copy
    *from* it), so the leak's damage was that the drill read the community's real
    replica and pulled real vault bytes into a scratch directory — not that it
    wrote to it. The path-containment test above is what catches the leak; this one
    guards the day some scenario writes to a replica, which is a real possibility
    (`heal` already writes to locations) and would be silent without it.
    """
    live_before = drill.tree_digest(archive_with_mirror)
    replica = tmp_path / "offbox"
    replica_before = drill.tree_digest(replica)

    report = drill.run_drill(
        archive_with_mirror, tmp_path / "work", scenarios=["seized-primary"], now=DRILL_NOW
    )

    assert report.live_archive_untouched
    assert drill.tree_digest(archive_with_mirror) == live_before
    assert drill.tree_digest(replica) == replica_before, (
        "the drill wrote to the live off-box replica"
    )


def test_seized_primary_is_not_applicable_when_the_replica_would_not_verify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A duress posture whose replica is empty: reported, never rehearsed, never failed.

    This is the finding a steward most needs from this scenario and it is the state
    the `all([])` defect used to render as a pass. It is not-applicable rather than
    failed because nothing about the *recovery path* was demonstrated broken — what
    is broken is the replica, and the reason says so with the code
    `verify_backup_location` returned.
    """
    monkeypatch.setenv("LEDGER_VAULT_KEY", _VAULT_KEY)
    root = tmp_path / "arc"
    _build(root, tmp_path / "data")
    # Genuinely empty, not missing-bags: `audit_fixity` reconciles `records/`
    # against `bags/` (#121), so a replica that kept its records and lost its bags
    # is `fixity-failed` — real damage. The state this test is about is the one the
    # `all([])` defect used to call a pass: readable, and nothing in it to check.
    empty = tmp_path / "offbox"
    empty.mkdir()
    shutil.copytree(root / "store", empty / "store")
    for name in ("bags", "records"):
        shutil.rmtree(empty / "store" / name)
        (empty / "store" / name).mkdir()
    shutil.copyfile(root / "identity.vault", empty / "identity.vault")
    config = Config.load(root / "store" / "config.json")
    config.lockdown = LockdownConfig(
        stop_disclosure=True,
        shred_vault=True,
        required_replica_locations=[str(empty)],
        min_verified_replicas=1,
    )
    config.save(root / "store" / "config.json")

    report = drill.run_drill(root, tmp_path / "work", scenarios=["seized-primary"], now=DRILL_NOW)
    result = _result(report, "seized-primary")
    assert result.outcome is DrillOutcome.NOT_APPLICABLE
    assert result.outcome is not DrillOutcome.RECOVERED
    assert "would refuse to shred" in result.detail
    assert "nothing-verified" in result.detail, (
        "the reason must name the replica's own verdict code, not just a count"
    )
    assert report.exit_code == 0


def test_an_archive_with_no_duress_posture_says_so_rather_than_failing(
    archive_without_lockdown_root: Path, tmp_path: Path
) -> None:
    """`lockdown.shred_vault` is off by default, so this is the common deployment."""
    report = drill.run_drill(
        archive_without_lockdown_root,
        tmp_path / "work",
        scenarios=["seized-primary"],
        now=DRILL_NOW,
    )
    result = _result(report, "seized-primary")
    assert result.outcome is DrillOutcome.NOT_APPLICABLE
    assert "not configured to shred its vault under duress" in result.detail


def test_a_stand_up_that_restored_the_wrong_bytes_is_not_a_recovery(
    archive_with_mirror: Path, tmp_path: Path
) -> None:
    """The verify compares digests, so an empty or truncated vault fails.

    Asserting the file merely exists would credit a stand-up that touched an empty
    file into place, which is the shape of every other defect in this module.
    """
    workdir = tmp_path / "work"
    staged = drill.stage(archive_with_mirror, workdir)

    def _restore_nothing(_staged: drill.StagedArchive) -> None:
        Path(_staged.config.vault_path).write_bytes(b"")
        (Path(_staged.config.store_root) / "logs" / "lockdown.flag").unlink()

    scenario = drill.SCENARIOS["seized-primary"]
    broken = drill.Scenario(
        name=scenario.name,
        fault=scenario.fault,
        recovering_command=scenario.recovering_command,
        applicability=scenario.applicability,
        inject=scenario.inject,
        detect=scenario.detect,
        recover=_restore_nothing,
        verify=scenario.verify,
    )
    result = drill.run_scenario(broken, staged)
    assert result.outcome is DrillOutcome.FAILED
    assert result.failing_step == "verify"


def test_a_lockdown_over_an_archive_with_no_vault_is_not_a_landed_injection(
    archive_with_mirror: Path, tmp_path: Path
) -> None:
    """The state a two-part landing check would have called a rehearsed seizure.

    `execute_lockdown` over an archive with no vault succeeds down the branch
    "local vault already absent; nothing to shred": the flag is written and the
    vault is absent, so "flag present and vault gone" holds — satisfied by an
    archive that had nothing to destroy. The stand-up afterwards restores nothing
    and the scenario would report `recovered`.

    Driven by removing the vault from the *staged* copy and calling the injection
    directly, because applicability refuses this state upstream. That is the point:
    the check must hold on its own rather than inherit correctness from a caller.
    """
    staged = drill.stage(archive_with_mirror, tmp_path / "work")
    Path(staged.config.vault_path).unlink()

    injection = drill._inject_seized_primary(staged)

    assert not injection.landed
    assert "no vault for a lockdown to shred" in injection.reason
