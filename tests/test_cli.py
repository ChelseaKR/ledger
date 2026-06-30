"""End-to-end tests for the ``ledger`` command-line surface (:mod:`ledger.cli`).

These exercise the CLI exactly as a steward or a script would: through
``cli.main([...])`` with a temporary ``--root``, asserting on the process exit
code and on captured stdout/stderr. The happy path walks the real lifecycle —
``init`` then ``ingest`` then ``show``/``browse`` then ``audit`` — over the
synthetic fixtures, and two safety-critical behaviours get their own tests:

* ``audit`` returns a non-zero exit code when a stored object is corrupted, so a
  CI or cron gate can branch on it (operability, failure transparency);
* ingesting with a contributor name prints **only** the opaque ``identity_ref``
  token — the sentinel name never appears on stdout or stderr (the no-outing
  rule, verified at the process boundary).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ledger import cli

# A loud, obviously-fake contributor identity. A leak of this string to any CLI
# output stream would be unmistakable (safety).
_SENTINEL_NAME = "SENTINEL-CLI-DO-NOT-LEAK-7Q4X"
_SENTINEL_CONTACT = "cli-leak-probe@sentinel.invalid"

# A valid 32-byte urlsafe-base64 Fernet key, fixed so the run is reproducible.
_VAULT_KEY = "0123456789abcdef0123456789abcdef0123456789a="

# Fixed timestamp so ingest is byte-reproducible (determinism).
_NOW = "2026-06-16T12:00:00Z"

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _init(root: Path, name: str = "Test Archive") -> int:
    """Run ``ledger init`` against ``root`` and return the exit code."""
    return cli.main(["init", "--root", str(root), "--name", name])


def _bags_dir(root: Path) -> Path:
    """The bags directory the archive writes under ``root`` (one bag per record)."""
    return root / "store" / "bags"


def _only_bag(root: Path) -> Path:
    """The single bag directory under ``root`` (the tests ingest exactly one)."""
    bags = [p for p in _bags_dir(root).iterdir() if p.is_dir()]
    assert len(bags) == 1, f"expected exactly one bag, found {len(bags)}"
    return bags[0]


# --- happy path -------------------------------------------------------------


def test_init_creates_archive_tree(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """``init`` stands up the store/bags/records/logs tree and a config file."""
    root = tmp_path / "arc"
    assert _init(root) == 0
    out = capsys.readouterr().out
    assert "initialized archive" in out
    assert (root / "store" / "config.json").is_file()
    for sub in ("bags", "records", "logs"):
        assert (root / "store" / sub).is_dir()


def test_init_ingest_show_browse_audit_happy_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The full lifecycle: init -> ingest -> show -> browse -> audit all succeed."""
    root = tmp_path / "arc"
    assert _init(root) == 0
    capsys.readouterr()  # drain init output

    # Ingest a PUBLIC fixture record so anonymous can list and show it.
    payload = _FIXTURES / "public.txt"
    rc = cli.main(
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
            str(payload),
        ]
    )
    assert rc == 0
    ingest_out = capsys.readouterr().out
    assert "record_id:" in ingest_out
    # Recover the record id the CLI printed so the later commands can address it.
    record_id = next(
        line.split("record_id:")[1].strip()
        for line in ingest_out.splitlines()
        if line.startswith("record_id:")
    )
    assert record_id

    # show: prints the disclosed (safe) JSON shape, including the public field.
    assert cli.main(["show", "--root", str(root), "--id", record_id, "--now", _NOW]) == 0
    show_out = capsys.readouterr().out
    shown = json.loads(show_out)
    assert shown["record_id"] == record_id
    assert shown["title"] == "Public sample"
    assert shown["fields"].get("story") == "a public account"

    # browse: lists the record for the anonymous public.
    assert cli.main(["browse", "--root", str(root), "--now", _NOW]) == 0
    browse_out = capsys.readouterr().out
    assert record_id in browse_out
    assert "Public sample" in browse_out
    assert "1 record(s) visible" in browse_out

    # audit: a freshly ingested, untampered bag passes with exit code 0.
    assert cli.main(["audit", "--root", str(root)]) == 0
    audit_out = capsys.readouterr().out
    assert "PASS" in audit_out
    assert "0 failed" in audit_out


# --- audit detects corruption ----------------------------------------------


def test_audit_returns_nonzero_when_object_corrupted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``audit`` exits non-zero after a stored payload byte is altered.

    A flipped byte in a bag's ``data/`` file must make fixity fail, and the exit
    code must be non-zero so a gate can detect drift (failure transparency).
    """
    root = tmp_path / "arc"
    assert _init(root) == 0
    payload = _FIXTURES / "public.txt"
    assert (
        cli.main(
            ["ingest", "--root", str(root), "--title", "Corrupt me", "--now", _NOW, str(payload)]
        )
        == 0
    )
    capsys.readouterr()  # drain

    # A clean audit first: the bag is valid as stored.
    assert cli.main(["audit", "--root", str(root)]) == 0
    capsys.readouterr()

    # Corrupt one payload byte in the single bag's data directory.
    bag = _only_bag(root)
    data_files = [p for p in (bag / "data").rglob("*") if p.is_file()]
    assert data_files, "expected at least one payload file in the bag"
    victim = data_files[0]
    original = victim.read_bytes()
    victim.write_bytes(original + b"\x00tampered")

    rc = cli.main(["audit", "--root", str(root)])
    assert rc != 0, "audit must report failure after corruption"
    out = capsys.readouterr().out
    assert "FAIL" in out


# --- the no-outing rule at the CLI boundary --------------------------------


def test_ingest_with_contributor_prints_only_opaque_ref(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Ingesting with a contributor name prints only the opaque ref, never the name.

    The contributor name and contact are sealed into the encrypted vault; the CLI
    must echo only the random ``identity_ref`` token. The sentinel name and contact
    must appear on neither stdout nor stderr (the no-outing rule).
    """
    root = tmp_path / "arc"
    # The vault key arrives only via the environment, never on a command line.
    monkeypatch.setenv("LEDGER_VAULT_KEY", _VAULT_KEY)
    assert _init(root) == 0
    capsys.readouterr()

    payload = _FIXTURES / "public.txt"
    rc = cli.main(
        [
            "ingest",
            "--root",
            str(root),
            "--title",
            "Sealed contributor",
            "--contributor-name",
            _SENTINEL_NAME,
            "--contributor-contact",
            _SENTINEL_CONTACT,
            "--now",
            _NOW,
            str(payload),
        ]
    )
    assert rc == 0
    captured = capsys.readouterr()
    combined = captured.out + captured.err

    # The opaque ref is printed...
    assert "identity_ref:" in captured.out
    ref = next(
        line.split("identity_ref:")[1].strip()
        for line in captured.out.splitlines()
        if line.startswith("identity_ref:")
    )
    assert ref, "an identity_ref token must be printed"

    # ...but the contributor name and contact appear NOWHERE in CLI output.
    assert _SENTINEL_NAME not in combined
    assert _SENTINEL_CONTACT not in combined
    # And the opaque ref itself is not the name (it is a random token).
    assert _SENTINEL_NAME not in ref


def test_ingest_seals_identity_out_of_on_disk_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The sentinel identity is absent from every clear-text on-disk artifact.

    Defense in depth for the CLI ingest path: the record manifest, the Dublin Core
    sidecar, the PREMIS log, and bag-info must never carry the contributor identity
    (it lives only as ciphertext in the vault).
    """
    root = tmp_path / "arc"
    monkeypatch.setenv("LEDGER_VAULT_KEY", _VAULT_KEY)
    assert _init(root) == 0
    payload = _FIXTURES / "public.txt"
    assert (
        cli.main(
            [
                "ingest",
                "--root",
                str(root),
                "--title",
                "Sealed contributor",
                "--contributor-name",
                _SENTINEL_NAME,
                "--contributor-contact",
                _SENTINEL_CONTACT,
                "--now",
                _NOW,
                str(payload),
            ]
        )
        == 0
    )
    capsys.readouterr()

    bag = _only_bag(root)
    clear_text_artifacts = [
        bag / "record.json",
        bag / "dublincore.json",
        bag / "premis.json",
        bag / "bag-info.txt",
    ]
    for artifact in clear_text_artifacts:
        text = artifact.read_text(encoding="utf-8")
        assert _SENTINEL_NAME not in text, f"name leaked into {artifact.name}"
        assert _SENTINEL_CONTACT not in text, f"contact leaked into {artifact.name}"

    # The fast-lookup records/ copy is identity-free too.
    for records_json in (root / "store" / "records").glob("*.json"):
        text = records_json.read_text(encoding="utf-8")
        assert _SENTINEL_NAME not in text
        assert _SENTINEL_CONTACT not in text


# --- error handling ---------------------------------------------------------


def test_show_missing_record_exits_nonzero_without_leaking(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Showing an unknown id fails cleanly with a non-zero code and a safe message."""
    root = tmp_path / "arc"
    assert _init(root) == 0
    capsys.readouterr()
    rc = cli.main(["show", "--root", str(root), "--id", "does-not-exist", "--now", _NOW])
    assert rc != 0
    err = capsys.readouterr().err
    assert "error:" in err


def test_ingest_rejects_malformed_field_pair(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A ``--public-field`` without ``=`` is rejected; the value is never echoed."""
    root = tmp_path / "arc"
    assert _init(root) == 0
    capsys.readouterr()
    rc = cli.main(
        [
            "ingest",
            "--root",
            str(root),
            "--title",
            "Bad field",
            "--public-field",
            "no-equals-here",
            "--now",
            _NOW,
        ]
    )
    assert rc != 0
    err = capsys.readouterr().err
    assert "name=value" in err


# --- seal / redact: the disclosure-policy workflow -------------------------

# A loud sealed-field sentinel: it is published at ingest, then sealed/redacted, and
# must disappear from the anonymous disclosed view once the workflow runs (safety).
_SEALED_FIELD_VALUE = "SENTINEL-CLI-SEALED-FIELD-7Q4X"


def _ingest_sealed_probe(root: Path, capsys: pytest.CaptureFixture[str]) -> str:
    """Ingest a PUBLIC record carrying a probe field and return its record id."""
    assert _init(root) == 0
    capsys.readouterr()
    rc = cli.main(
        [
            "ingest",
            "--root",
            str(root),
            "--title",
            "Workflow sample",
            "--public-field",
            f"venue={_SEALED_FIELD_VALUE}",
            "--now",
            _NOW,
            str(_FIXTURES / "public.txt"),
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    record_id = next(
        line.split("record_id:")[1].strip()
        for line in out.splitlines()
        if line.startswith("record_id:")
    )
    assert record_id
    return record_id


def test_seal_embargo_removes_field_from_anonymous_view(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``seal --field --until`` embargoes a field so anonymous no longer sees its value.

    The published probe value must be present before the seal and absent after, while
    the disclosed view honestly names the field as withheld (selective disclosure).
    """
    root = tmp_path / "arc"
    rid = _ingest_sealed_probe(root, capsys)

    # Visible before the seal.
    assert cli.main(["show", "--root", str(root), "--id", rid, "--now", _NOW]) == 0
    assert _SEALED_FIELD_VALUE in capsys.readouterr().out

    rc = cli.main(
        [
            "seal",
            "--root",
            str(root),
            "--id",
            rid,
            "--field",
            "venue",
            "--level",
            "sealed-until",
            "--until",
            "2099-01-01",
            "--actor",
            "steward",
            "--reason",
            "contributor asked to embargo",
            "--now",
            _NOW,
        ]
    )
    assert rc == 0
    assert "set to sealed-until" in capsys.readouterr().out

    # Gone from the anonymous disclosed view; the field is named as withheld instead.
    assert cli.main(["show", "--root", str(root), "--id", rid, "--now", _NOW]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert _SEALED_FIELD_VALUE not in json.dumps(shown)
    assert "venue" not in shown["fields"]
    assert any(w["name"] == "venue" for w in shown["withheld"])


def test_seal_rejects_until_without_field_target(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--until`` is a field-only concept; using it with ``--default`` is a clean error."""
    root = tmp_path / "arc"
    rid = _ingest_sealed_probe(root, capsys)
    rc = cli.main(
        [
            "seal",
            "--root",
            str(root),
            "--id",
            rid,
            "--default",
            "--level",
            "sealed-until",
            "--until",
            "2099-01-01",
            "--actor",
            "steward",
            "--reason",
            "r",
            "--now",
            _NOW,
        ]
    )
    assert rc != 0
    assert "error:" in capsys.readouterr().err


def test_redact_field_erases_value_in_stored_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``redact --field`` replaces the value on disk, so even a steward view cannot see it."""
    root = tmp_path / "arc"
    rid = _ingest_sealed_probe(root, capsys)
    rc = cli.main(
        [
            "redact",
            "--root",
            str(root),
            "--id",
            rid,
            "--field",
            "venue",
            "--actor",
            "steward",
            "--reason",
            "erase on request",
            "--now",
            _NOW,
        ]
    )
    assert rc == 0
    assert "redacted field 'venue'" in capsys.readouterr().out

    # The value is gone from the steward view too — redaction is destructive at rest.
    assert (
        cli.main(["show", "--root", str(root), "--id", rid, "--as", "steward", "--now", _NOW]) == 0
    )
    shown = capsys.readouterr().out
    assert _SEALED_FIELD_VALUE not in shown
    assert "[redacted]" in shown


def test_redact_requires_reason(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A redaction without a rationale is rejected (accountability)."""
    root = tmp_path / "arc"
    rid = _ingest_sealed_probe(root, capsys)
    rc = cli.main(
        [
            "redact",
            "--root",
            str(root),
            "--id",
            rid,
            "--field",
            "venue",
            "--actor",
            "steward",
            "--reason",
            "   ",
            "--now",
            _NOW,
        ]
    )
    assert rc != 0
    assert "error:" in capsys.readouterr().err


def test_redact_unknown_field_fails_without_leaking(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Redacting a non-existent field fails cleanly, naming only the field."""
    root = tmp_path / "arc"
    rid = _ingest_sealed_probe(root, capsys)
    rc = cli.main(
        [
            "redact",
            "--root",
            str(root),
            "--id",
            rid,
            "--field",
            "nope",
            "--actor",
            "steward",
            "--reason",
            "r",
            "--now",
            _NOW,
        ]
    )
    assert rc != 0
    err = capsys.readouterr().err
    assert "nope" in err
    assert _SEALED_FIELD_VALUE not in err
