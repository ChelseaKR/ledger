"""Tests for ``ledger verify-backup`` (backlog K1).

An untested backup is a hope, not a backup. This command points at a restored copy
of the archive root and proves it is intact — exit 0 when every bag passes fixity,
non-zero when a backed-up bag is corrupt — so a cron job can alarm on a bad backup.
"""

from __future__ import annotations

import glob
import shutil
from pathlib import Path

from ledger import cli

_NOW = "2026-06-17T00:00:00Z"


def _archive_with_one_record(tmp_path: Path) -> Path:
    root = tmp_path / "arc"
    assert cli.main(["init", "--root", str(root), "--name", "Backup"]) == 0
    payload = tmp_path / "doc.txt"
    payload.write_text("a community keeps its own history\n", encoding="utf-8")
    assert (
        cli.main(
            [
                "ingest",
                "--root",
                str(root),
                "--title",
                "Rec",
                str(payload),
                "--public-field",
                "s=x",
                "--actor",
                "s",
                "--now",
                _NOW,
            ]
        )
        == 0
    )
    return root


def test_verify_backup_passes_on_a_good_copy(tmp_path: Path) -> None:
    """A faithful copy of the archive root verifies and exits 0."""
    root = _archive_with_one_record(tmp_path)
    backup = tmp_path / "backup"
    shutil.copytree(root, backup)
    assert cli.main(["verify-backup", "--backup", str(backup)]) == 0


def test_verify_backup_fails_on_a_corrupt_copy(tmp_path: Path) -> None:
    """A backup with a tampered payload byte fails fixity and exits non-zero."""
    root = _archive_with_one_record(tmp_path)
    backup = tmp_path / "backup"
    shutil.copytree(root, backup)

    # Flip a byte in a backed-up bag's payload so its manifest no longer matches.
    data_files = glob.glob(str(backup / "store" / "bags" / "*" / "data" / "*"))
    assert data_files
    target = Path(data_files[0])
    raw = bytearray(target.read_bytes())
    raw[0] ^= 0x01
    target.write_bytes(bytes(raw))

    assert cli.main(["verify-backup", "--backup", str(backup)]) == 1
