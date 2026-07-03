"""Tests for the adoption-readiness checkup (:mod:`ledger.checkup`, EX6/EXP-03).

These exercise the checkup the way a steward standing up ledger for real would run
it: over a temporary archive, asserting the verdict of each control against
``docs/ADOPTING.md`` and — critically — that a control the tool cannot positively
confirm is reported ``could-not-verify`` rather than faked green. The environment
and platform are injected (never the real process environment mutated), so every
branch is reachable deterministically on any host.

No-outing: the rendered report is asserted to carry no contributor identity — it
records only operational facts (paths, counts, a bound host).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ledger import checkup, cli
from ledger.checkup import CheckStatus, Readiness, run_checkup
from ledger.config import Config, StorageLocation
from ledger.ingest import Archive
from ledger.models import AccessPolicy, Record
from ledger.replicate import replicate_bag

# A loud, fake identity string. If any of it reached the readiness report, a
# no-outing regression would be unmistakable (safety).
_SENTINEL_IDENTITY = "SENTINEL-CHECKUP-DO-NOT-LEAK-Q7X@sentinel.invalid"

_NOW = "2026-07-02T09:00:00Z"

# The off-loopback bind address the TLS-exposure check must flag. Named once so the
# bandit "binding to all interfaces" advisory is silenced here, not at each use site.
_ALL_INTERFACES = "0.0.0.0"  # noqa: S104 -- deliberately testing the off-loopback path


def _fresh_archive(root: Path, name: str = "Test Collective Archive") -> Archive:
    """Stand up a fresh single-box archive under ``root`` (one local location, no mirror)."""
    return Archive.init(Config.default(name, root))


def _result(report: checkup.CheckupReport, check_id: str) -> checkup.CheckResult:
    """The single check with ``check_id`` (asserts exactly one exists)."""
    matches = [r for r in report.results if r.check_id == check_id]
    assert len(matches) == 1, f"expected one {check_id} check, found {len(matches)}"
    return matches[0]


# --- off-box replica topology ----------------------------------------------


def test_fresh_archive_is_red_with_no_off_box_replicas(tmp_path: Path) -> None:
    """A one-box archive fails the off-box replica control and rolls up to red."""
    archive = _fresh_archive(tmp_path / "arc")
    report = run_checkup(archive, env={}, platform="linux", now=_NOW)

    off_box = _result(report, "off-box-replicas")
    assert off_box.status is CheckStatus.FAIL
    assert report.readiness is Readiness.RED
    assert report.exit_code == 1


def test_configuring_a_mirror_without_verified_bytes_does_not_pass(tmp_path: Path) -> None:
    """A configured path is not evidence that a usable independent copy exists."""
    config = Config.default("Test Collective Archive", tmp_path / "arc")
    mirror = StorageLocation(name="offsite", path=str(tmp_path / "mirror"), kind="mirror")
    config.locations.append(mirror)
    archive = Archive.init(config)

    report = run_checkup(archive, env={}, platform="linux", now=_NOW)
    off_box = _result(report, "off-box-replicas")
    assert off_box.status is CheckStatus.UNVERIFIED
    assert "no bags" in off_box.explanation


def test_verified_and_attested_mirror_passes(tmp_path: Path) -> None:
    """A pass requires verified replica bytes plus explicit physical attestation."""
    mirror_path = tmp_path / "mirror"
    mirror = StorageLocation(name="offsite", path=str(mirror_path), kind="mirror")
    config = Config.default("Test Collective Archive", tmp_path / "arc")
    config.locations.append(mirror)
    archive = Archive.init(config)
    record = Record(title="Public test", default_policy=AccessPolicy.PUBLIC)
    archive.ingest({}, record, now=_NOW)
    mirror_path.mkdir()
    replicate_bag(archive.bags_dir / record.record_id, mirror, agent="test", now=_NOW)

    report = run_checkup(
        archive,
        env={"LEDGER_OFFBOX_REPLICA_ATTESTED": "true"},
        platform="linux",
        now=_NOW,
    )
    off_box = _result(report, "off-box-replicas")
    assert off_box.status is CheckStatus.PASS
    assert "Every bag verifies" in off_box.explanation
    assert report.exit_code == 0


# --- vault key provenance ---------------------------------------------------


def test_vault_key_file_on_same_disk_fails(tmp_path: Path) -> None:
    """A key file on the same device as the store is total-compromise material — fail."""
    root = tmp_path / "arc"
    archive = _fresh_archive(root)
    key_file = tmp_path / "vault.key"  # same tmp filesystem as the store
    key_file.write_text("0123456789abcdef0123456789abcdef0123456789a=", encoding="utf-8")

    report = run_checkup(
        archive,
        env={"LEDGER_VAULT_KEY_FILE": str(key_file)},
        platform="linux",
        now=_NOW,
    )
    provenance = _result(report, "vault-key-provenance")
    assert provenance.status is CheckStatus.FAIL
    assert "same disk" in provenance.explanation


def test_vault_key_in_env_passes(tmp_path: Path) -> None:
    """A key supplied via LEDGER_VAULT_KEY (in the environment, not on disk) passes."""
    archive = _fresh_archive(tmp_path / "arc")
    report = run_checkup(
        archive,
        env={"LEDGER_VAULT_KEY": "0123456789abcdef0123456789abcdef0123456789a="},
        platform="linux",
        now=_NOW,
    )
    assert _result(report, "vault-key-provenance").status is CheckStatus.PASS


def test_no_vault_key_is_could_not_verify(tmp_path: Path) -> None:
    """With no key visible at all, provenance is could-not-verify, never a fake pass."""
    archive = _fresh_archive(tmp_path / "arc")
    report = run_checkup(archive, env={}, platform="linux", now=_NOW)
    assert _result(report, "vault-key-provenance").status is CheckStatus.UNVERIFIED


# --- full-disk-encryption heuristic ----------------------------------------


def test_fde_could_not_verify_when_mount_parsing_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the mount table cannot be read, FDE is could-not-verify — not a fake pass."""

    def _boom(_platform: str) -> list[tuple[str, str]]:
        raise OSError("mount table unavailable")

    monkeypatch.setattr(checkup, "_read_mount_table", _boom)
    archive = _fresh_archive(tmp_path / "arc")
    report = run_checkup(archive, env={}, platform="linux", now=_NOW)

    fde = _result(report, "full-disk-encryption")
    assert fde.status is CheckStatus.UNVERIFIED


def test_fde_passes_on_a_dm_crypt_device(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A store on a device-mapper/LUKS volume is positive evidence of at-rest encryption."""
    root = tmp_path / "arc"
    archive = _fresh_archive(root)
    store_root = str(archive.store_root)

    def _mounts(_platform: str) -> list[tuple[str, str]]:
        return [("/dev/mapper/luks-vault", "/"), ("sysfs", "/sys")]

    monkeypatch.setattr(checkup, "_read_mount_table", _mounts)
    report = run_checkup(archive, env={}, platform="linux", now=_NOW)
    fde = _result(report, "full-disk-encryption")
    assert fde.status is CheckStatus.PASS
    assert store_root  # store path resolved without error


def test_fde_could_not_verify_on_plain_device(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plain device shows no encryption layer — reported could-not-verify, not fail."""
    archive = _fresh_archive(tmp_path / "arc")

    def _mounts(_platform: str) -> list[tuple[str, str]]:
        return [("/dev/sda1", "/")]

    monkeypatch.setattr(checkup, "_read_mount_table", _mounts)
    report = run_checkup(archive, env={}, platform="linux", now=_NOW)
    assert _result(report, "full-disk-encryption").status is CheckStatus.UNVERIFIED


# --- TLS exposure hint ------------------------------------------------------


def test_loopback_bind_passes_tls_check(tmp_path: Path) -> None:
    """The default loopback bind needs no TLS — nothing off-box can reach it."""
    archive = _fresh_archive(tmp_path / "arc")
    report = run_checkup(archive, env={}, platform="linux", now=_NOW)
    assert _result(report, "tls-exposure").status is CheckStatus.PASS


def test_off_loopback_without_tls_fails(tmp_path: Path) -> None:
    """Binding 0.0.0.0 with no declared TLS proxy is an exposure failure."""
    archive = _fresh_archive(tmp_path / "arc")
    report = run_checkup(
        archive,
        env={"LEDGER_SERVE_HOST": _ALL_INTERFACES},
        platform="linux",
        now=_NOW,
    )
    assert _result(report, "tls-exposure").status is CheckStatus.FAIL


def test_off_loopback_with_tls_proxy_passes(tmp_path: Path) -> None:
    """Binding off loopback is fine once a TLS-terminating proxy is declared."""
    archive = _fresh_archive(tmp_path / "arc")
    report = run_checkup(
        archive,
        env={"LEDGER_SERVE_HOST": _ALL_INTERFACES, "LEDGER_BEHIND_TLS_PROXY": "true"},
        platform="linux",
        now=_NOW,
    )
    assert _result(report, "tls-exposure").status is CheckStatus.PASS


# --- report writing + no-outing --------------------------------------------


def test_report_written_under_audits_and_is_identity_free(tmp_path: Path) -> None:
    """The dated report lands under audits/ and carries no contributor identity."""
    root = tmp_path / "arc"
    archive = _fresh_archive(root)
    report = run_checkup(archive, env={}, platform="linux", now=_NOW)

    expected = archive.store_root / "audits" / "readiness-2026-07-02.md"
    assert report.report_path == expected
    assert expected.is_file()

    text = expected.read_text(encoding="utf-8")
    assert "ledger readiness checkup" in text
    assert "2026-07-02" in text
    # No-outing: the loud sentinel identity never appears (nothing puts one here).
    assert _SENTINEL_IDENTITY not in text
    assert "@sentinel.invalid" not in text


def test_write_report_can_be_skipped(tmp_path: Path) -> None:
    """With write_report False no file is produced and report_path stays None."""
    archive = _fresh_archive(tmp_path / "arc")
    report = run_checkup(archive, env={}, platform="linux", now=_NOW, write_report=False)
    assert report.report_path is None
    assert not (archive.store_root / "audits").exists()


# --- CLI surface ------------------------------------------------------------


def test_cli_checkup_exit_code_and_report_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`ledger checkup` on a one-box archive exits red (1) and prints the report path."""
    root = tmp_path / "arc"
    assert cli.main(["init", "--root", str(root), "--name", "CLI Archive"]) == 0
    capsys.readouterr()

    code = cli.main(["checkup", "--root", str(root), "--now", _NOW])
    out = capsys.readouterr().out
    assert code == 1  # no off-box replica yet -> red
    assert "readiness checkup" in out
    assert "RED" in out
    assert str(root / "store" / "audits" / "readiness-2026-07-02.md") in out


def test_cli_checkup_green_yellow_after_mirror(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """After registering an off-box mirror the CLI no longer exits red."""
    root = tmp_path / "arc"
    assert cli.main(["init", "--root", str(root), "--name", "CLI Archive"]) == 0
    assert (
        cli.main(
            [
                "add-location",
                "--root",
                str(root),
                "--name",
                "offsite",
                "--path",
                str(tmp_path / "mirror"),
                "--kind",
                "mirror",
            ]
        )
        == 0
    )
    capsys.readouterr()

    code = cli.main(["checkup", "--root", str(root), "--now", _NOW])
    assert code == 0  # the only hard failure is resolved; yellow/green both exit 0
    capsys.readouterr()


def test_cli_checkup_json_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`--json` emits a machine-readable report with a readiness roll-up and checks."""
    root = tmp_path / "arc"
    assert cli.main(["init", "--root", str(root), "--name", "CLI Archive"]) == 0
    capsys.readouterr()

    cli.main(["checkup", "--root", str(root), "--json", "--now", _NOW])
    payload = json.loads(capsys.readouterr().out)
    assert payload["readiness"] in {"green", "yellow", "red"}
    assert payload["generated_date"] == "2026-07-02"
    check_ids = {c["id"] for c in payload["checks"]}
    assert "off-box-replicas" in check_ids
    assert "vault-key-provenance" in check_ids
