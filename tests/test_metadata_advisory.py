"""RM8: the ingest CLI nudges an author to supply a description, and the new
``--description`` flag lets them provide it (minimal-metadata authoring support)."""

from __future__ import annotations

import glob
from pathlib import Path

import pytest

from ledger import cli
from ledger.config import Config
from ledger.ingest import Archive


def _init(root: Path) -> None:
    assert cli.main(["init", "--root", str(root), "--name", "P"]) == 0


def _ingest(root: Path, payload: Path, *extra: str) -> int:
    return cli.main(
        [
            "ingest",
            "--root",
            str(root),
            "--title",
            "Item",
            str(payload),
            "--actor",
            "s",
            "--now",
            "2026-01-01T00:00:00Z",
            *extra,
        ]
    )


def test_ingest_without_description_warns(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "arc"
    _init(root)
    payload = tmp_path / "note.txt"
    payload.write_text("hello")
    assert _ingest(root, payload) == 0
    assert "no description" in capsys.readouterr().err


def test_ingest_with_description_is_quiet_and_persists_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "arc"
    _init(root)
    payload = tmp_path / "note.txt"
    payload.write_text("hello")
    assert _ingest(root, payload, "--description", "A field recording from the 1994 march.") == 0
    assert "no description" not in capsys.readouterr().err

    archive = Archive(Config.load(root / "store" / "config.json"))
    rid = Path(glob.glob(str(root / "store" / "records" / "*.json"))[0]).stem
    assert archive.get(rid).dublin_core.description == ["A field recording from the 1994 march."]
