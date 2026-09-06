"""`ledger drill`: the rehearsal, and the ways a rehearsal can lie.

The scenarios themselves are the easy half. The tests that matter here are the
ones about a drill that cannot fail: an injection that silently no-ops, a fault
the archive's own check does not see, a scenario this archive's shape cannot
exercise, and a drill that writes to the archive it is supposed to be rehearsing.
Each of those has to end in a different word from "recovered".
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from ledger import drill
from ledger.cli import main as cli_main
from ledger.config import Config, StorageLocation
from ledger.drill import DrillOutcome
from ledger.ingest import Archive
from ledger.models import AccessPolicy, Record
from ledger.replicate import replicate_bag

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


@pytest.fixture
def archive_with_mirror(tmp_path: Path) -> Path:
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
        assert "no mirror location" in result.detail
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
        assert "no bags" in result.detail


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
    assert "recovered from 3 injected fault(s)" in result.explanation


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

    The pass line names the not-applicable scenarios rather than folding them into
    the recovered count, so "3 recovered" can never mean "0 recovered, 3 skipped".
    """
    from ledger.checkup import CheckStatus, run_checkup

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
    assert result.status is CheckStatus.PASS
    assert "recovered from 0 injected fault(s)" in result.explanation
    assert "not-applicable, not as passes" in result.explanation


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
