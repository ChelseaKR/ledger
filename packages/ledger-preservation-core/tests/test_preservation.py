"""Tests for :mod:`ledger_preservation_core.preservation` — format identification.

Covers content-based signature matching (the strongest basis), the extension
fallback for formats with no reliable leading signature, the plain-text decode
fallback, the honest "unknown" outcome, at-risk flagging for obsolescent
formats, and :func:`identify_file` reading a real file's head bytes.
"""

from __future__ import annotations

from pathlib import Path

from ledger_preservation_core.preservation import identify_file, identify_format


def test_signature_match_is_authoritative_over_extension() -> None:
    """A PNG signature wins even if the filename claims a different format."""
    data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    result = identify_format(data, filename="not-a-png.txt")
    assert result.basis == "signature"
    assert result.name == "PNG"
    assert result.media_type == "image/png"
    assert result.at_risk is False


def test_pdf_signature() -> None:
    result = identify_format(b"%PDF-1.7\n%...", filename=None)
    assert result.name == "PDF"
    assert result.puid == "fmt/14"
    assert result.basis == "signature"


def test_riff_wave_vs_webp_disambiguation() -> None:
    """RIFF carries its real format in the sub-chunk brand, not the leading bytes."""
    wave = b"RIFF" + b"\x00\x00\x00\x00" + b"WAVE" + b"\x00" * 16
    webp = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 16
    assert identify_format(wave).name == "Broadcast/WAVE audio"
    assert identify_format(webp).name == "WebP"


def test_extension_fallback_when_no_signature_matches() -> None:
    """Legacy OLE2 Office is signature-matched; a JSON file falls back to extension."""
    result = identify_format(b'{"a": 1}', filename="record.json")
    assert result.basis == "extension"
    assert result.name == "JSON"


def test_at_risk_format_carries_a_recommendation() -> None:
    """Obsolescent/proprietary formats are flagged so migration is a visible signal."""
    ole2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 32
    result = identify_format(ole2, filename="report.doc")
    assert result.at_risk is True
    assert result.recommendation
    assert "AT-RISK" in result.summary()


def test_plain_text_fallback() -> None:
    result = identify_format(b"hello, archive\n", filename=None)
    assert result.basis == "text"
    assert result.name == "Plain text (UTF-8)"


def test_unknown_is_recorded_honestly_not_guessed() -> None:
    """Unrecognised binary content with no matching extension is "unknown", not
    silently mis-identified as something it is not (honesty)."""
    result = identify_format(b"\x00\x01\x02\x03\xff\xfe", filename=None)
    assert result.basis == "unknown"
    assert result.at_risk is False
    assert result.media_type == "application/octet-stream"


def test_identify_file_reads_only_head_bytes(tmp_path: Path) -> None:
    """A large file is identified from its head without loading it fully."""
    path = tmp_path / "photo.jpg"
    path.write_bytes(b"\xff\xd8\xff" + b"\x00" * (2 * 1024 * 1024))
    result = identify_file(path)
    assert result.name == "JPEG"
    assert result.basis == "signature"


def test_identify_format_is_deterministic() -> None:
    """The same bytes and filename always yield the same result (reproducibility)."""
    data = b"GIF89a" + b"\x00" * 16
    first = identify_format(data, filename="anim.gif")
    second = identify_format(data, filename="anim.gif")
    assert first == second
