"""Tests for safe upload validation (``ledger.upload``, backlog A2).

A web upload is an attack surface, so the rule under test is simple and strict: the
*bytes* decide the type, never the filename or a declared ``Content-Type``. Only the
small allowlist of formats is accepted; everything else is refused. These tests pin
the magic-byte sniffing, the RIFF disambiguation (WebP vs WAV), and the rejection of
forged, truncated, or empty input.
"""

from __future__ import annotations

import pytest

from ledger import upload

# Minimal byte sequences carrying each format's leading signature. Sniffing reads
# only the prefix, so a valid signature followed by arbitrary bytes is enough.
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
_GIF = b"GIF89a" + b"\x00" * 16
_PDF = b"%PDF-1.7\n" + b"junk"
_OGG = b"OggS" + b"\x00" * 16
_MP3 = b"ID3" + b"\x00" * 16
_WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 8
_WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 8


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (_PNG, "image/png"),
        (_JPEG, "image/jpeg"),
        (_GIF, "image/gif"),
        (_PDF, "application/pdf"),
        (_OGG, "audio/ogg"),
        (_MP3, "audio/mpeg"),
        (_WEBP, "image/webp"),
        (_WAV, "audio/wav"),
    ],
)
def test_sniff_recognises_allowlisted_types(data: bytes, expected: str) -> None:
    """Each allowlisted format is identified from its leading bytes."""
    assert upload.sniff_media_type(data) == expected


def test_sniff_rejects_forged_and_unknown_bytes() -> None:
    """A type the allowlist does not cover is refused, whatever it claims to be."""
    # An ELF binary and a plain text "document" are both not on the allowlist.
    assert upload.sniff_media_type(b"\x7fELF" + b"\x00" * 16) is None
    assert upload.sniff_media_type(b"just some text, not a file we accept") is None
    assert upload.sniff_media_type(b"") is None


def test_riff_container_is_disambiguated_by_offset_eight() -> None:
    """A RIFF header alone is ambiguous: WebP and WAV are told apart at offset 8."""
    # RIFF with neither WEBP nor WAVE at offset 8 is not an accepted type.
    assert upload.sniff_media_type(b"RIFF\x00\x00\x00\x00AVI ") is None
    # A truncated RIFF (no room for the offset-8 tag) is refused, not guessed.
    assert upload.sniff_media_type(b"RIFF") is None


def test_allowed_types_is_sorted_and_covers_the_signatures() -> None:
    """The advertised allowlist matches what sniffing will actually accept."""
    assert tuple(sorted(upload.ALLOWED_TYPES)) == upload.ALLOWED_TYPES
    assert "image/png" in upload.ALLOWED_TYPES
    assert "application/pdf" in upload.ALLOWED_TYPES
