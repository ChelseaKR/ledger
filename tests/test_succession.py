"""Tests for the group-continuity / succession hand-off (EX1).

Mutual-aid groups disband and lose their knowledge. These tests pin that a folding
collective can produce a no-outing-safe hand-off manifest a designated successor can
follow: it inventories records by opaque id with re-verified fixity, names the
encrypted vault as a file to copy (never its key), embeds a runbook, and never leaks
a contributor identity. They also pin that a designated-successor grant confers
stewardship but NOT the power to out contributors.
"""

from __future__ import annotations

import glob
import json
import os
from pathlib import Path

import pytest

from ledger import cli
from ledger.access.grants import designated_successor
from ledger.config import Config
from ledger.ingest import Archive
from ledger.models import AccessPolicy
from ledger.succession import build_handoff

_VAULT_KEY = "0123456789abcdef0123456789abcdef0123456789a="
_NOW = "2026-06-30T00:00:00Z"
_SENTINEL = "SENTINEL-SUCCESSION-DO-NOT-LEAK-44Z"


def test_designated_successor_grant_is_steward_without_unseal() -> None:
    """A successor inherits stewardship but no identity-unseal power (no-outing)."""
    grant = designated_successor("casa-abierta-2")
    assert grant.is_steward is True
    assert AccessPolicy.STEWARDS in grant.levels
    assert grant.identity_unseal == frozenset()  # inheriting is not outing


def test_designated_successor_grant_can_expire() -> None:
    """A temporary caretaker hand-off can be time-bounded."""
    grant = designated_successor("caretaker", expires_at="2027-01-01T00:00:00Z")
    assert grant.is_expired("2027-02-01T00:00:00Z") is True
    assert grant.is_expired("2026-12-01T00:00:00Z") is False


def _seed_archive(tmp_path: Path) -> tuple[Archive, list[str]]:
    """Stand up an archive with two ingested records and a sealed identity."""
    root = tmp_path / "arc"
    os.environ["LEDGER_VAULT_KEY"] = _VAULT_KEY
    try:
        assert cli.main(["init", "--root", str(root), "--name", "Casa Abierta"]) == 0
        for i in range(2):
            payload = tmp_path / f"doc{i}.txt"
            payload.write_text(f"synthetic record {i}\n", encoding="utf-8")
            argv = [
                "ingest",
                "--root",
                str(root),
                "--title",
                f"Record {i}",
                str(payload),
                "--actor",
                "s",
                "--now",
                _NOW,
            ]
            if i == 0:
                argv += ["--contributor-name", _SENTINEL]
            assert cli.main(argv) == 0
    finally:
        del os.environ["LEDGER_VAULT_KEY"]
    archive = Archive(Config.load(root / "store" / "config.json"))
    rids = [Path(p).stem for p in glob.glob(str(root / "store" / "records" / "*.json"))]
    return archive, rids


def test_build_handoff_inventories_records_with_fixity(tmp_path: Path) -> None:
    """The manifest inventories every record by id, all verifying intact."""
    archive, rids = _seed_archive(tmp_path)
    manifest = build_handoff(archive, now=_NOW, successor="Casa Abierta II")
    assert manifest.total_records == 2
    assert manifest.all_fixity_ok is True
    assert {r.record_id for r in manifest.records} == set(rids)
    assert all(r.fixity_ok for r in manifest.records)
    assert manifest.vault_present is True
    assert manifest.successor == "Casa Abierta II"


def test_handoff_runbook_warns_about_key_and_points_at_verify(tmp_path: Path) -> None:
    """The runbook tells the successor to move the key out-of-band and verify."""
    archive, _ = _seed_archive(tmp_path)
    runbook = build_handoff(archive, now=_NOW).runbook()
    assert "out-of-band" in runbook
    assert "verify-backup" in runbook
    assert "No successor named yet" in runbook  # no successor passed


def test_handoff_manifest_never_leaks_identity(tmp_path: Path) -> None:
    """No sealed contributor identity appears anywhere in the manifest (no-outing)."""
    archive, _ = _seed_archive(tmp_path)
    manifest = build_handoff(archive, now=_NOW, successor=_SENTINEL and "Successor")
    blob = manifest.to_json()
    assert _SENTINEL not in blob
    # The vault is referenced as a file to copy, but its key is never in the manifest.
    data = json.loads(blob)
    assert data["vault"]["present"] is True
    assert _VAULT_KEY not in blob


def test_handoff_is_deterministic(tmp_path: Path) -> None:
    """The same archive state and instant hand off to a byte-identical manifest."""
    archive, _ = _seed_archive(tmp_path)
    a = build_handoff(archive, now=_NOW, successor="S").to_json()
    b = build_handoff(archive, now=_NOW, successor="S").to_json()
    assert a == b


def test_handoff_cli_writes_manifest(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`ledger handoff --out` writes a JSON manifest and exits 0 on a healthy archive."""
    archive, _ = _seed_archive(tmp_path)
    root = archive.store_root.parent
    out = tmp_path / "handoff.json"
    capsys.readouterr()
    rc = cli.main(
        [
            "handoff",
            "--root",
            str(root),
            "--successor",
            "New Stewards",
            "--out",
            str(out),
            "--now",
            _NOW,
        ]
    )
    assert rc == 0
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["total_records"] == 2
    assert data["successor"] == "New Stewards"
    assert _SENTINEL not in out.read_text(encoding="utf-8")
