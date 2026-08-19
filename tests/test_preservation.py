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

# Byte prefixes taken verbatim from real files in the Open Preservation Foundation
# format-corpus (CC0), which `make real-corpus` runs the whole pipeline over. Every
# one of these was recorded as "Unidentified" before the corpus run exposed it.
_JP2 = b"\x00\x00\x00\x0cjP  \r\n\x87\n\x00\x00\x00\x14ftypjp2 " + b"\x00" * 8
_JPX = b"\x00\x00\x00\x0cjP  \r\n\x87\n\x00\x00\x00\x1cftypjpx " + b"\x00" * 8
_JPM = b"\x00\x00\x00\x0cjP  \r\n\x87\n\x00\x00\x00\x14ftypjpm " + b"\x00" * 8
_MJ2 = b"\x00\x00\x00\x0cjP  \r\n\x87\n\x00\x00\x00\x18ftypmjp2" + b"\x00" * 8
_J2C = b"\xff\x4f\xff\x51\x00\x2f\x00\x00" + b"\x00" * 16
_RTF = rb"{\rtf1\ansi\deff0 hello}"


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


@pytest.mark.parametrize(
    ("data", "name", "media_type", "puid"),
    [
        (_JP2, "JP2 (JPEG 2000 part 1)", "image/jp2", "x-fmt/392"),
        (_JPX, "JPX (JPEG 2000 part 2)", "image/jpx", "fmt/151"),
        (_JPM, "JPM (JPEG 2000 part 6)", "image/jpm", "fmt/463"),
        (_MJ2, "MJ2 (Motion JPEG 2000)", "video/mj2", "fmt/337"),
        (_J2C, "JPEG 2000 codestream", "image/jp2", "fmt/1794"),
    ],
)
def test_jpeg_2000_family_identified_by_signature(
    data: bytes, name: str, media_type: str, puid: str
) -> None:
    """JPEG 2000 — the preservation master format of most digitisation programmes.

    All four container flavours share one signature box and differ only in the
    ``ftyp`` brand at offset 20, so identifying them means reading past the magic
    number. Every one of these was ``Unidentified`` until a real corpus said so.
    """
    fmt = identify_format(data, filename="no-extension")
    assert fmt.name == name
    assert fmt.media_type == media_type
    assert fmt.puid == puid
    assert fmt.basis == "signature"


def test_unknown_jpeg_2000_brand_still_identified_as_jpeg_2000() -> None:
    """An unrecognised JP2 brand degrades to JP2, never to ``unknown``."""
    fmt = identify_format(
        b"\x00\x00\x00\x0cjP  \r\n\x87\n\x00\x00\x00\x14ftypXXXX" + b"\x00" * 8,
        filename="mystery",
    )
    assert fmt.puid == "x-fmt/392"
    assert fmt.basis == "signature"


def test_rtf_is_a_format_not_plain_text() -> None:
    """RTF is ASCII, so without a signature it silently degrades to text/plain."""
    fmt = identify_format(_RTF, filename="no-extension")
    assert fmt.name == "Rich Text Format"
    assert fmt.media_type == "application/rtf"
    assert fmt.puid == "fmt/45"
    assert fmt.basis == "signature"


@pytest.mark.parametrize("preamble", [b" ", b"17e500\r\n", b"\x00" * 128, b'{"datetime": 1}\n'])
def test_pdf_header_displaced_by_a_wrapper_is_still_identified(preamble: bytes) -> None:
    """Real PDFs carry preambles: chunked-transfer lengths, MacBinary, JSON, a space.

    The file opens fine in a reader, so recording it as ``Unidentified`` tells the
    steward nothing. It is identified, and the offset is reported, because a header
    off byte 0 is itself the preservation defect worth surfacing.
    """
    fmt = identify_format(preamble + _PDF, filename="scan.pdf")
    assert fmt.media_type == "application/pdf"
    assert fmt.basis == "signature-offset"
    assert fmt.header_offset == len(preamble)
    assert f"header at byte {len(preamble)}" in fmt.summary()


def test_undisplaced_pdf_keeps_the_plain_signature_basis() -> None:
    """A well-formed PDF is unaffected: basis stays ``signature``, offset stays 0."""
    fmt = identify_format(_PDF, filename="scan.pdf")
    assert fmt.basis == "signature"
    assert fmt.header_offset == 0
    assert "header at byte" not in fmt.summary()


def test_text_mentioning_a_pdf_header_is_still_plain_text() -> None:
    """The displaced-header scan runs last, so it cannot relabel ordinary prose."""
    fmt = identify_format(b"The zine was distributed as %PDF-1.4 files.\n", filename="notes")
    assert fmt.basis == "text"
    assert fmt.media_type == "text/plain"


def test_displaced_header_search_is_bounded() -> None:
    """A ``%PDF-`` far into the file is not a header, and is not treated as one."""
    fmt = identify_format(b"\x00" * 4096 + _PDF, filename="mystery")
    assert fmt.basis == "unknown"


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


def test_unidentified_payload_is_not_recorded_as_a_successful_ingest(tmp_path: Path) -> None:
    """A file the identifier could not name must not carry a green PREMIS outcome.

    "We do not know what this is" is the single most actionable preservation-planning
    signal an ingest produces. Filing it as ``success`` — the same outcome as a
    confident content match — hides it from any steward auditing the log by outcome.
    On a real archival corpus this was 17% of files, not a rounding error.
    """
    archive, rid = _ingest(tmp_path, "mystery.bin", b"\x00\x01\x02\x03\xff\xfe\x05" * 8)
    premis = PremisLog.read(archive.bags_dir / rid / "premis.json")
    fmt_events = [e for e in premis.events if e.event_type is PremisEventType.FORMAT_IDENTIFICATION]
    assert fmt_events and fmt_events[0].outcome == "unidentified"


def test_identified_payload_still_records_success(tmp_path: Path) -> None:
    """The honest-unknown outcome does not disturb an ordinary identified payload."""
    archive, rid = _ingest(tmp_path, "photo.png", _PNG)
    premis = PremisLog.read(archive.bags_dir / rid / "premis.json")
    fmt_events = [e for e in premis.events if e.event_type is PremisEventType.FORMAT_IDENTIFICATION]
    assert fmt_events and fmt_events[0].outcome == "success"


@pytest.mark.parametrize(
    ("data", "puid"),
    [
        (_WEBP, "fmt/566"),
        (b"\x1aE\xdf\xa3" + b"\x00" * 16, "fmt/569"),
        (_REALMEDIA, "x-fmt/190"),
    ],
)
def test_puids_match_pronom(data: bytes, puid: str) -> None:
    """A PUID is a claim about an external registry, so a wrong one misinforms.

    These three were each pointing at a different format in PRONOM entirely — WebP
    at Adobe Illustrator, Matroska at Epson Raw, RealMedia at Nikon camera raw —
    and were written into every PREMIS log as fact. Verified against the DROID
    signature file (V120), which is PRONOM's own published export.
    """
    assert identify_format(data, filename="payload.bin").puid == puid


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


def test_xml_is_identified_by_its_declaration_without_an_extension() -> None:
    """A renamed or extension-less PREMIS/METS file must not be recorded as text.

    Found by identifying real encoder-produced files rather than magic-byte
    stubs: every binary format resolved correctly, but XML content reached the
    identifier as ``text/plain`` (``x-fmt/111``) whenever the filename did not
    end in ``.xml``. The metadata standards this project writes are XML, so that
    is the wrong PUID on exactly the files whose format matters most.
    """
    premis = b'<?xml version="1.0" encoding="UTF-8"?>\n<premis:premis/>\n'

    no_name = identify_format(premis)
    assert no_name.media_type == "application/xml"
    assert no_name.puid == "fmt/101"
    assert no_name.basis == "xml-declaration"

    # A wrong extension must not win over the declaration either.
    mislabelled = identify_format(premis, filename="scan.png")
    assert mislabelled.media_type == "application/xml"


def test_xml_declaration_is_found_behind_a_byte_order_mark() -> None:
    """A BOM is legal in front of ``<?xml`` and must not hide it."""
    for bom in (b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff"):
        fmt = identify_format(bom + b'<?xml version="1.0"?>\n<mets/>\n')
        assert fmt.media_type == "application/xml", bom


def test_svg_still_resolves_by_extension_not_as_generic_xml() -> None:
    """The XML check runs after the extension step precisely to protect this.

    An SVG normally opens with an XML declaration, so treating ``<?xml`` as a
    step-one signature would relabel every ``.svg`` as generic XML and lose
    ``fmt/91``.
    """
    svg = b'<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg"/>\n'
    fmt = identify_format(svg, filename="poster.svg")
    assert fmt.media_type == "image/svg+xml"
    assert fmt.basis == "extension"


def test_plain_text_is_unaffected_by_the_xml_check() -> None:
    fmt = identify_format(b"an oral history transcript\n")
    assert fmt.media_type == "text/plain"
    assert fmt.basis == "text"
