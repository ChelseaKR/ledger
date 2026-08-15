"""Operator entry points for recovery: ``ledger heal`` and ``ledger mutual-aid`` (#123).

`replicate.heal` and the whole EXP-15 sealed-replica family were written, documented,
and unit-tested, and nothing a steward could run reached any of them: no subcommand, no
route, no call site in `src/` outside the module that defines them. The docs said
otherwise — the README states as a rule that a quarantined copy "heals from a verified
replica", `docs/ARCHITECTURE.md` maps Recoverability straight at `replicate.heal`, and
`docs/MUTUAL-AID.md` is written as a five-step operator runbook whose steps 3-5 were
Python function calls. For the audience this project names — community archivists and
mutual-aid organizers, explicitly not developers — a documented runbook that cannot be
followed is a claim the interface does not honour.

So these tests are about *reachability* first and behaviour second:

* the documented capability answers to a command, asserted through the same surface an
  operator uses (`ledger heal --help` exits 0 or the command does not exist);
* the runbook contains no Python the reader is expected to write themselves — the
  absence of the thing that made it unfollowable, not merely the presence of commands;
* and then the commands actually recover data: a corrupted replica is healed, a sealed
  copy round-trips through a partner directory and validates on the way back.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ledger import cli
from ledger.bag import validate_bag

_NOW = "2026-06-16T12:00:00Z"
_FIXTURES = Path(__file__).resolve().parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parent.parent

# A valid Fernet key, fixed so the run is reproducible. A pairing key is exchanged
# out-of-band between two archives; it never travels through the archive or through
# argv, so the tests hand it over the same way the docs tell an operator to: the env.
_PAIRING_KEY = "0123456789abcdef0123456789abcdef0123456789a="


def _archive_with_mirror(tmp_path: Path) -> tuple[Path, Path, str]:
    """An archive holding one record, plus a registered (still empty) mirror location."""
    root = tmp_path / "arc"
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    assert cli.main(["init", "--root", str(root), "--name", "Recovery Test Archive"]) == 0
    assert (
        cli.main(
            [
                "ingest",
                "--root",
                str(root),
                "--title",
                "Public sample",
                "--public-field",
                "story=a public account",
                "--now",
                _NOW,
                str(_FIXTURES / "public.txt"),
            ]
        )
        == 0
    )
    assert (
        cli.main(
            [
                "add-location",
                "--root",
                str(root),
                "--name",
                "mirror-1",
                "--path",
                str(mirror),
                "--kind",
                "mirror",
            ]
        )
        == 0
    )
    bags = [p for p in (root / "store" / "bags").iterdir() if p.is_dir()]
    assert len(bags) == 1
    return root, mirror, bags[0].name


# --- reachability: the point of #123 -----------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        ["heal"],
        ["mutual-aid", "seal"],
        ["mutual-aid", "attest"],
        ["mutual-aid", "verify"],
        ["mutual-aid", "recover"],
    ],
)
def test_the_documented_recovery_capability_has_an_operator_entry_point(
    command: list[str],
) -> None:
    """Asserted through the operator's own surface: an unknown command exits 2, not 0."""
    with pytest.raises(SystemExit) as exc_info:
        cli.main([*command, "--help"])
    assert exc_info.value.code == 0, f"`ledger {' '.join(command)}` is not a command"


def test_the_pairing_runbook_asks_the_operator_for_no_python() -> None:
    """The absence of the unearned claim: a runbook of function signatures is not a runbook.

    `docs/MUTUAL-AID.md` is written for community archivists. Its "Setting up a pairing"
    steps were `replicate_sealed_bag(...)`, "a cron target calling `attest_sealed_replica`",
    and a `recover_sealed_bag(...)` drill — none runnable without writing Python against
    internal APIs and constructing `StorageLocation` objects by hand.
    """
    text = (REPO_ROOT / "docs/MUTUAL-AID.md").read_text(encoding="utf-8")
    section = text.split("## Setting up a pairing", 1)
    assert len(section) == 2, "the pairing runbook section is gone"
    steps = section[1].split("\n## ", 1)[0]
    for api_call in (
        "replicate_sealed_bag(",
        "attest_sealed_replica(",
        "recover_sealed_bag(",
        "verify_sealed_attestation(",
    ):
        assert api_call not in steps, (
            f"the pairing runbook still tells an operator to call {api_call}…) themselves"
        )


def test_every_ledger_command_the_runbook_names_exists() -> None:
    """Doc-derived, so a renamed subcommand cannot leave the runbook pointing at nothing."""
    text = (REPO_ROOT / "docs/MUTUAL-AID.md").read_text(encoding="utf-8")
    named = {match.group(1).strip() for match in re.finditer(r"`ledger ([a-z -]+?)[`\n]", text)}
    assert named, "the runbook names no ledger commands at all — it is prose again"
    for command in sorted(named):
        with pytest.raises(SystemExit) as exc_info:
            cli.main([*command.split(), "--help"])
        assert exc_info.value.code == 0, (
            f"the runbook names `ledger {command}`, which does not exist"
        )


def test_the_architecture_doc_points_recoverability_at_a_runnable_command() -> None:
    """`docs/ARCHITECTURE.md` mapped Recoverability at a function no operator could call."""
    text = (REPO_ROOT / "docs/ARCHITECTURE.md").read_text(encoding="utf-8")
    row = next(line for line in text.splitlines() if line.startswith("| **Recoverability**"))
    assert "ledger heal" in row, "the Recoverability row still names only the library function"


# --- heal, end to end --------------------------------------------------------


def test_heal_populates_a_registered_mirror_that_has_no_copy_yet(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, mirror, bag = _archive_with_mirror(tmp_path)
    assert not (mirror / bag).exists()

    rc = cli.main(["heal", "--root", str(root), "--id", bag, "--actor", "steward-1", "--now", _NOW])
    assert rc == 0
    out = capsys.readouterr().out
    assert "REPLICATION" in out or "replication" in out.lower()
    assert validate_bag(mirror / bag).ok


def test_heal_rebuilds_a_corrupted_replica_and_replicas_goes_green(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The README's rule, exercised the way a steward would: FAIL, heal, ok."""
    root, mirror, bag = _archive_with_mirror(tmp_path)
    assert cli.main(["heal", "--root", str(root), "--id", bag, "--actor", "s", "--now", _NOW]) == 0
    capsys.readouterr()

    payload = next((mirror / bag / "data").iterdir())
    payload.write_text("corrupted on the mirror's disk")
    assert cli.main(["replicas", "--root", str(root), "--id", bag]) == 0
    assert "FAIL" in capsys.readouterr().out

    assert cli.main(["heal", "--root", str(root), "--id", bag, "--actor", "s", "--now", _NOW]) == 0
    capsys.readouterr()
    assert cli.main(["replicas", "--root", str(root), "--id", bag]) == 0
    assert "FAIL" not in capsys.readouterr().out


def test_healing_an_already_healthy_fleet_is_a_no_op(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, _mirror, bag = _archive_with_mirror(tmp_path)
    assert cli.main(["heal", "--root", str(root), "--id", bag, "--actor", "s", "--now", _NOW]) == 0
    capsys.readouterr()
    assert cli.main(["heal", "--root", str(root), "--id", bag, "--actor", "s", "--now", _NOW]) == 0
    assert "nothing to do" in capsys.readouterr().out


def test_heal_refuses_when_no_replica_validates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Never bless a divergent copy: with nothing trustworthy to copy from, heal errors."""
    root, mirror, bag = _archive_with_mirror(tmp_path)
    assert cli.main(["heal", "--root", str(root), "--id", bag, "--actor", "s", "--now", _NOW]) == 0
    capsys.readouterr()
    for copy in ((root / "store" / "bags" / bag), (mirror / bag)):
        next((copy / "data").iterdir()).write_text("both copies are now divergent")

    assert cli.main(["heal", "--root", str(root), "--id", bag, "--actor", "s", "--now", _NOW]) == 2
    err = capsys.readouterr().err
    assert "no validating replica" in err


def test_heal_states_its_honest_limit_where_the_steward_will_read_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """heal is fixity-aware, not revision-aware — a steward acting on it should be told."""
    root, _mirror, bag = _archive_with_mirror(tmp_path)
    assert cli.main(["heal", "--root", str(root), "--id", bag, "--actor", "s", "--now", _NOW]) == 0
    assert "not necessarily the newest revision" in capsys.readouterr().err


def test_heal_does_not_resurrect_a_taken_down_bag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Takedown wins over healing, through the CLI as well as through the library.

    The dangerous sequence: a location is unreachable when a record is taken down, so
    its tombstone stays pending there; the location comes back holding a stale copy;
    the next heal must remove it, not copy it around.
    """
    root, mirror, bag = _archive_with_mirror(tmp_path)
    assert cli.main(["heal", "--root", str(root), "--id", bag, "--actor", "s", "--now", _NOW]) == 0
    capsys.readouterr()

    offline = tmp_path / "mirror-offline"
    mirror.rename(offline)  # the mirror is unreachable when the takedown lands
    assert (
        cli.main(
            [
                "takedown",
                "--root",
                str(root),
                "--id",
                bag,
                "--actor",
                "steward-1",
                "--reason",
                "contributor withdrew consent",
                "--now",
                _NOW,
            ]
        )
        == 0
    )
    capsys.readouterr()
    offline.rename(mirror)  # it reattaches, still holding the stale copy
    assert (mirror / bag).exists()

    assert cli.main(["heal", "--root", str(root), "--id", bag, "--actor", "s", "--now", _NOW]) == 0
    out = capsys.readouterr().out
    assert not (mirror / bag).exists(), "heal left a taken-down bag on a reattached mirror"
    assert "REPLICATION" not in out, "heal re-copied a taken-down bag"


# --- mutual aid, end to end --------------------------------------------------


def _seal(root: Path, partner: Path, bag: str) -> str:
    """Seal ``bag`` to ``partner`` through the CLI and return the printed digest."""
    rc = cli.main(
        [
            "mutual-aid",
            "seal",
            "--root",
            str(root),
            "--id",
            bag,
            "--path",
            str(partner),
            "--actor",
            "steward-1",
            "--now",
            _NOW,
        ]
    )
    assert rc == 0
    return ""


def test_the_pairing_round_trips_through_the_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Runbook steps 3-5, as commands: seal, attest, verify, and a recovery drill."""
    monkeypatch.setenv("LEDGER_PAIRING_KEY", _PAIRING_KEY)
    root, _mirror, bag = _archive_with_mirror(tmp_path)
    partner = tmp_path / "partner"
    partner.mkdir()

    _seal(root, partner, bag)
    seal_out = capsys.readouterr().out
    digest = seal_out.strip().splitlines()[-1].rsplit(": ", 1)[1]
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert (partner / f"{bag}.sealed").is_file()

    # The holding partner attests without the key — that is the whole point.
    monkeypatch.delenv("LEDGER_PAIRING_KEY")
    assert (
        cli.main(["mutual-aid", "attest", "--path", str(partner), "--bag", bag, "--now", _NOW]) == 0
    )
    attest_out = capsys.readouterr().out
    assert digest in attest_out

    # The owner checks that digest against what they kept from seal time.
    assert (
        cli.main(
            [
                "mutual-aid",
                "verify",
                "--path",
                str(partner),
                "--bag",
                bag,
                "--expect",
                digest,
                "--now",
                _NOW,
            ]
        )
        == 0
    )
    capsys.readouterr()

    # The drill: pull it back, decrypt with the key that never left home, validate.
    monkeypatch.setenv("LEDGER_PAIRING_KEY", _PAIRING_KEY)
    into = tmp_path / "recovered"
    rc = cli.main(
        ["mutual-aid", "recover", "--path", str(partner), "--bag", bag, "--into", str(into)]
    )
    assert rc == 0
    assert "ok" in capsys.readouterr().out
    assert validate_bag(into / bag).ok


def test_verify_fails_when_the_partners_copy_drifted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A cron-able exit code: a substituted or damaged copy must not exit 0."""
    monkeypatch.setenv("LEDGER_PAIRING_KEY", _PAIRING_KEY)
    root, _mirror, bag = _archive_with_mirror(tmp_path)
    partner = tmp_path / "partner"
    partner.mkdir()
    _seal(root, partner, bag)
    digest = capsys.readouterr().out.strip().splitlines()[-1].rsplit(": ", 1)[1]

    (partner / f"{bag}.sealed").write_bytes(b"substituted ciphertext")
    rc = cli.main(
        ["mutual-aid", "verify", "--path", str(partner), "--bag", bag, "--expect", digest]
    )
    assert rc == 1
    assert "MISMATCH" in capsys.readouterr().err


def test_attest_reports_a_missing_copy_rather_than_raising(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    partner = tmp_path / "partner"
    partner.mkdir()
    assert cli.main(["mutual-aid", "attest", "--path", str(partner), "--bag", "nothing-here"]) == 1
    assert "missing" in capsys.readouterr().out


def test_the_pairing_key_is_never_taken_from_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No `--key` flag exists, and without the env var the command refuses to run.

    A key in argv is a key in shell history and in the process table; this is the same
    rule `vault rekey` applies to the vault key.
    """
    monkeypatch.delenv("LEDGER_PAIRING_KEY", raising=False)
    root, _mirror, bag = _archive_with_mirror(tmp_path)
    partner = tmp_path / "partner"
    partner.mkdir()
    capsys.readouterr()

    rc = cli.main(
        [
            "mutual-aid",
            "seal",
            "--root",
            str(root),
            "--id",
            bag,
            "--path",
            str(partner),
            "--actor",
            "steward-1",
        ]
    )
    assert rc == 2
    assert "LEDGER_PAIRING_KEY" in capsys.readouterr().err

    with pytest.raises(SystemExit) as exc_info:
        cli.main(
            [
                "mutual-aid",
                "seal",
                "--root",
                str(root),
                "--id",
                bag,
                "--path",
                str(partner),
                "--actor",
                "steward-1",
                "--key",
                _PAIRING_KEY,
            ]
        )
    assert exc_info.value.code == 2, "a --key flag would put the key in the process table"


def test_no_command_ever_prints_the_pairing_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Confidentiality, checked at the process boundary the way the no-outing tests are."""
    monkeypatch.setenv("LEDGER_PAIRING_KEY", _PAIRING_KEY)
    root, _mirror, bag = _archive_with_mirror(tmp_path)
    partner = tmp_path / "partner"
    partner.mkdir()
    capsys.readouterr()

    _seal(root, partner, bag)
    into = tmp_path / "recovered"
    assert (
        cli.main(
            ["mutual-aid", "recover", "--path", str(partner), "--bag", bag, "--into", str(into)]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert _PAIRING_KEY not in captured.out
    assert _PAIRING_KEY not in captured.err


def test_a_location_name_that_is_not_configured_is_named_not_guessed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, _mirror, bag = _archive_with_mirror(tmp_path)
    capsys.readouterr()
    rc = cli.main(
        ["mutual-aid", "attest", "--root", str(root), "--location", "nowhere", "--bag", bag]
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "nowhere" in err
    assert "mirror-1" in err  # tells the operator what *is* configured
