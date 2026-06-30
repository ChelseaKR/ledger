"""Tests for format identification + preservation planning (RM4 / OAIS).

Bit-fixity proves bytes are unchanged; it does nothing about *format obsolescence*,
which is what defeats real volunteer archives. These tests pin that ledger now
identifies a payload's format from its bytes (PRONOM/DROID-style), flags
obsolescent formats as at-risk with a migration recommendation, records a PREMIS
``FORMAT_IDENTIFICATION`` event per payload at ingest, backfills Dublin Core
``format``, and never leaks identity through any of it.
"""

from __future__ import annotations

import glob
from pathlib import Path

import pytest

from ledger import cli
from ledger.config import Config
from ledger.ingest import Archive
from ledger.metadata.premis import PremisLog
from ledger.models import PremisEventType
from ledger.preservation import identify_file, identify_format

# Minimal but valid magic-number prefixes for representative formats.
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 8
_PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"
_GIF = b"GIF89a" + b"\x00" * 16
_TIFF = b"II*\x00" + b"\x00" * 16
_WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 8
_WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 8
_MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 8
_FLAC = b"fLaC\x00\x00\x00\x22" + b"\x00" * 8
_OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 16
_SWF = b"FWS\x09" + b"\x00" * 16
_RAR = b"Rar!\x1a\x07\x00" + b"\x00" * 16
_REALMEDIA = b".RMF\x00\x00\x00\x12" + b"\x00" * 8


def test_signature_identification_beats_extension() -> None:
    """A content signature is authoritative even when the extension lies."""
    fmt = identify_format(_PNG, filename="actually-a-png.txt")
    assert fmt.name == "PNG"
    assert fmt.media_type == "image/png"
    assert fmt.basis == "signature"
    assert fmt.at_risk is False


@pytest.mark.parametrize(
    ("data", "media_type"),
    [
        (_PNG, "image/png"),
        (_JPEG, "image/jpeg"),
        (_PDF, "application/pdf"),
        (_GIF, "image/gif"),
        (_TIFF, "image/tiff"),
        (_WAV, "audio/x-wav"),
        (_WEBP, "image/webp"),
        (_MP4, "video/mp4"),
        (_FLAC, "audio/flac"),
    ],
)
def test_open_formats_identified_and_not_at_risk(data: bytes, media_type: str) -> None:
    """Common open/well-supported formats are recognised and not flagged at-risk."""
    fmt = identify_format(data, filename="payload.bin")
    assert fmt.media_type == media_type
    assert fmt.basis == "signature"
    assert fmt.at_risk is False


@pytest.mark.parametrize(
    "data",
    [_OLE2, _SWF, _RAR, _REALMEDIA],
)
def test_obsolescent_formats_flagged_at_risk_with_recommendation(data: bytes) -> None:
    """Obsolescent/proprietary formats are flagged at-risk and carry a recommendation."""
    fmt = identify_format(data, filename="payload.bin")
    assert fmt.at_risk is True
    assert fmt.recommendation  # a non-empty migration recommendation
    assert "AT-RISK" in fmt.summary()


def test_plain_text_identified_by_decode() -> None:
    """Text with no signature and no helpful extension is recognised as plain text."""
    fmt = identify_format("a synthetic story\nwith unicode: café\n".encode(), filename="x")
    assert fmt.media_type == "text/plain"
    assert fmt.basis == "text"
    assert fmt.at_risk is False


def test_extension_fallback_flags_legacy_office() -> None:
    """With no usable signature, a legacy-Office extension still flags the risk."""
    fmt = identify_format(b"\x05\x06\x07arbitrary-binary-no-signature", filename="memo.doc")
    assert fmt.basis == "extension"
    assert fmt.at_risk is True


def test_unidentified_binary_is_honest_not_at_risk() -> None:
    """Unrecognised binary is octet-stream, basis 'unknown', not falsely 'at-risk'."""
    fmt = identify_format(b"\x00\x01\x02\x03\xff\xfe\x05", filename="mystery")
    assert fmt.media_type == "application/octet-stream"
    assert fmt.basis == "unknown"
    assert fmt.at_risk is False


def test_identification_is_deterministic() -> None:
    """The same bytes + filename always yield an equal FormatId (reproducibility)."""
    assert identify_format(_PDF, filename="a.pdf") == identify_format(_PDF, filename="a.pdf")


def test_identify_file_reads_only_head(tmp_path: Path) -> None:
    """identify_file recognises a format without depending on the whole large file."""
    big = tmp_path / "scan.png"
    big.write_bytes(_PNG + b"\x00" * (5 * 1024 * 1024))
    assert identify_file(big).media_type == "image/png"


def _ingest(tmp_path: Path, name: str, data: bytes) -> tuple[Archive, str]:
    root = tmp_path / "arc"
    assert cli.main(["init", "--root", str(root), "--name", "P"]) == 0
    payload = tmp_path / name
    payload.write_bytes(data)
    assert (
        cli.main(
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
            ]
        )
        == 0
    )
    archive = Archive(Config.load(root / "store" / "config.json"))
    rid = Path(glob.glob(str(root / "store" / "records" / "*.json"))[0]).stem
    return archive, rid


def test_ingest_records_format_identification_event(tmp_path: Path) -> None:
    """Ingest records a PREMIS FORMAT_IDENTIFICATION event and backfills dc:format."""
    archive, rid = _ingest(tmp_path, "photo.png", _PNG)
    premis = PremisLog.read(archive.bags_dir / rid / "premis.json")
    fmt_events = [e for e in premis.events if e.event_type is PremisEventType.FORMAT_IDENTIFICATION]
    assert len(fmt_events) == 1
    assert "PNG" in fmt_events[0].detail
    assert archive.get(rid).dublin_core.format == ["image/png"]


def test_ingest_at_risk_format_marks_event_and_warns(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An at-risk payload yields an 'at-risk' PREMIS outcome and a CLI advisory."""
    archive, rid = _ingest(tmp_path, "legacy.doc", _OLE2)
    err = capsys.readouterr().err
    assert "at-risk" in err.lower()
    premis = PremisLog.read(archive.bags_dir / rid / "premis.json")
    fmt_events = [e for e in premis.events if e.event_type is PremisEventType.FORMAT_IDENTIFICATION]
    assert fmt_events and fmt_events[0].outcome == "at-risk"


def test_format_identification_never_leaks_identity(tmp_path: Path) -> None:
    """A sealed identity never appears in a format-identification event (no-outing)."""
    sentinel = "SENTINEL-FORMAT-DO-NOT-LEAK"
    root = tmp_path / "arc"
    key = "0123456789abcdef0123456789abcdef0123456789a="
    import os

    os.environ["LEDGER_VAULT_KEY"] = key
    try:
        assert cli.main(["init", "--root", str(root), "--name", "P"]) == 0
        payload = tmp_path / "scan.png"
        payload.write_bytes(_PNG)
        assert (
            cli.main(
                [
                    "ingest",
                    "--root",
                    str(root),
                    "--title",
                    "Item",
                    str(payload),
                    "--contributor-name",
                    sentinel,
                    "--actor",
                    "s",
                    "--now",
                    "2026-01-01T00:00:00Z",
                ]
            )
            == 0
        )
    finally:
        del os.environ["LEDGER_VAULT_KEY"]
    archive = Archive(Config.load(root / "store" / "config.json"))
    rid = Path(glob.glob(str(root / "store" / "records" / "*.json"))[0]).stem
    premis_text = (archive.bags_dir / rid / "premis.json").read_text(encoding="utf-8")
    assert sentinel not in premis_text
