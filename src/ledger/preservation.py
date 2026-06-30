"""Format identification and preservation planning (OAIS Preservation Planning).

ledger proves that bytes are *unchanged* (fixity), but bit-fixity alone does not
keep a record *usable*: a file in an obsolete or proprietary format can verify
perfectly and still be unreadable in a decade. OAIS (ISO 14721) names a distinct
**Preservation Planning** functional entity for exactly this risk, and the
[NDSA Levels of Digital Preservation](https://www.ndsa.org/publications/levels-of-digital-preservation/)
and the [DPC Handbook](https://www.dpconline.org/handbook) treat *format
identification* as a core preservation activity alongside checksums. This module
closes that gap with a small, dependency-free, PRONOM/DROID-style identifier.

What it does:

* **Identify a format from its bytes**, not only its extension — a content-based
  signature ("magic number") is authoritative, the filename extension is a
  fallback, and a UTF-8 decode catches plain text. Each result records *how* it
  was reached (``basis``) so a steward can tell a confident content match from a
  guess (inspectability).
* **Flag at-risk material.** Obsolescent or proprietary formats that real
  community archives actually hold (legacy Office, Flash, RealMedia, WordPerfect,
  proprietary RAR) are marked ``at_risk`` with a plain-language migration
  recommendation, so the preservation risk is surfaced at ingest rather than
  discovered when the last reader stops working.
* **Carry a PRONOM PUID** where one is well known, so the identification is
  interoperable with DROID/PRONOM-based preservation tooling (standards).

Design qualities, kept consistent with the rest of ledger:

* **No new dependency, runs on one cheap box.** Pure standard library; the
  signature registry is a literal table, so identification needs no network and
  no PRONOM download (affordability/minimal computing).
* **Determinism.** Identification is a pure function of the bytes and the
  filename — no clock, no locale, no global state — so the same input always
  yields the same :class:`FormatId` (reproducibility).
* **No-outing rule.** This module reads only the *head bytes* of a file to sniff
  its format and never logs, returns, or embeds a contributor identity or a
  payload's content; a :class:`FormatId` is pure format metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# How many leading bytes are enough to recognise every signature in the registry
# and to make a confident UTF-8 text decision, without reading a whole large file
# into memory (efficiency, minimal computing).
_HEAD_BYTES = 65536


@dataclass(frozen=True)
class FormatInfo:
    """What is known about one file format (a registry row).

    ``puid`` is the PRONOM persistent identifier where one is well known (else
    ``None``); ``media_type`` is the IANA type; ``at_risk`` marks an obsolescent
    or proprietary format whose continued usability is in doubt; ``recommendation``
    is the plain-language preservation-planning action (normalize/migrate target).
    """

    name: str
    media_type: str
    puid: str | None = None
    at_risk: bool = False
    recommendation: str = ""


@dataclass(frozen=True)
class FormatId:
    """The outcome of identifying one file: a :class:`FormatInfo` plus the basis.

    ``basis`` records *how* the format was determined — ``"signature"`` (a
    content-based magic-number match, the strongest), ``"extension"`` (filename
    only), ``"text"`` (decoded cleanly as UTF-8), or ``"unknown"`` (none of the
    above). A steward reading a preservation report can tell a confident
    identification from a guess (inspectability, honesty).
    """

    name: str
    media_type: str
    puid: str | None
    at_risk: bool
    recommendation: str
    basis: str

    @classmethod
    def of(cls, info: FormatInfo, *, basis: str) -> FormatId:
        """Build a :class:`FormatId` from a registry :class:`FormatInfo`."""
        return cls(
            name=info.name,
            media_type=info.media_type,
            puid=info.puid,
            at_risk=info.at_risk,
            recommendation=info.recommendation,
            basis=basis,
        )

    def summary(self) -> str:
        """A one-line, no-outing-safe description for a PREMIS event detail."""
        puid = self.puid or "no-puid"
        line = f"identified as {self.name} [{puid}] via {self.basis}; media-type {self.media_type}"
        if self.at_risk:
            line += f"; AT-RISK — {self.recommendation}"
        return line


# --- registry ---------------------------------------------------------------
#
# A curated, standards-grounded subset of PRONOM, biased toward the formats a
# queer-history / mutual-aid community archive actually holds: photos and scans,
# audio and video oral histories, documents, and the legacy/proprietary formats
# that genuinely endanger such collections (LHA's "outdated formats", QZAP's
# un-digitized media, an elder narrator's tapes). Extending it is a one-line edit.

# Open, well-supported formats — preferred preservation or access targets.
_PDF = FormatInfo(
    "PDF", "application/pdf", "fmt/14", False, "Consider PDF/A (ISO 19005) for archival masters."
)
_PNG = FormatInfo("PNG", "image/png", "fmt/13", False, "")
_JPEG = FormatInfo("JPEG", "image/jpeg", "fmt/43", False, "")
_GIF = FormatInfo("GIF", "image/gif", "fmt/4", False, "")
_TIFF = FormatInfo("TIFF", "image/tiff", "fmt/353", False, "")
_BMP = FormatInfo("Windows Bitmap", "image/bmp", "fmt/116", False, "")
_WEBP = FormatInfo("WebP", "image/webp", "fmt/565", False, "")
_WAV = FormatInfo("Broadcast/WAVE audio", "audio/x-wav", "fmt/141", False, "")
_FLAC = FormatInfo("FLAC", "audio/flac", "fmt/279", False, "")
_OGG = FormatInfo("Ogg", "application/ogg", "fmt/203", False, "")
_MP3 = FormatInfo(
    "MP3", "audio/mpeg", "fmt/134", False, "Lossy; keep any lossless master (e.g. FLAC/WAV)."
)
_MP4 = FormatInfo("MPEG-4 / QuickTime (ISO BMFF)", "video/mp4", "fmt/199", False, "")
_MKV = FormatInfo("Matroska / WebM", "video/x-matroska", "fmt/641", False, "")
_ZIP = FormatInfo("ZIP", "application/zip", "x-fmt/263", False, "")
_GZIP = FormatInfo("GZIP", "application/gzip", "x-fmt/266", False, "")
_SEVENZIP = FormatInfo("7-Zip", "application/x-7z-compressed", "fmt/484", False, "")
_TEXT = FormatInfo("Plain text (UTF-8)", "text/plain", "x-fmt/111", False, "")
_XML = FormatInfo("XML", "application/xml", "fmt/101", False, "")
_HTML = FormatInfo("HTML", "text/html", "fmt/471", False, "")

# At-risk: obsolescent or proprietary formats whose long-term usability is in
# doubt. NDSA/DPC treat migration of these as a core preservation activity.
_OLE2_OFFICE = FormatInfo(
    "Microsoft OLE2 (legacy Office 97-2003: .doc/.xls/.ppt)",
    "application/x-ole-storage",
    "fmt/111",
    True,
    "Migrate to OOXML/ODF or PDF/A; the legacy binary format is obsolescent.",
)
_SWF = FormatInfo(
    "Adobe Flash (SWF)",
    "application/x-shockwave-flash",
    "fmt/507",
    True,
    "Obsolete: no maintained runtime. Migrate to video or emulate, then capture.",
)
_REALMEDIA = FormatInfo(
    "RealMedia",
    "application/vnd.rn-realmedia",
    "fmt/202",
    True,
    "Proprietary and obsolescent. Transcode to an open format (e.g. MP4/Matroska).",
)
_WORDPERFECT = FormatInfo(
    "WordPerfect document",
    "application/vnd.wordperfect",
    "x-fmt/44",
    True,
    "Obsolescent. Migrate to ODF/OOXML or PDF/A.",
)
_RAR = FormatInfo(
    "RAR archive",
    "application/vnd.rar",
    "x-fmt/264",
    True,
    "Proprietary container. Repackage as ZIP or tar for preservation.",
)

# Unidentified content. Recorded honestly (an unrecognised format is itself a
# preservation-planning signal) but not flagged at_risk, which is reserved for
# *known* obsolescent formats so the at-risk advisory stays precise.
_UNKNOWN = FormatInfo(
    "Unidentified",
    "application/octet-stream",
    None,
    False,
    "Unrecognised format — identify and document it before relying on it.",
)

# Fixed-offset magic-number signatures, longest/most specific first. RIFF, ISO
# BMFF (ftyp), and OLE2 need a secondary check and are handled in code below.
_SIGNATURES: tuple[tuple[int, bytes, FormatInfo], ...] = (
    (0, b"\x89PNG\r\n\x1a\n", _PNG),
    (0, b"%PDF-", _PDF),
    (0, b"\xff\xd8\xff", _JPEG),
    (0, b"GIF87a", _GIF),
    (0, b"GIF89a", _GIF),
    (0, b"II*\x00", _TIFF),
    (0, b"MM\x00*", _TIFF),
    (0, b"BM", _BMP),
    (0, b"fLaC", _FLAC),
    (0, b"OggS", _OGG),
    (0, b"\x1aE\xdf\xa3", _MKV),
    (0, b"ID3", _MP3),
    (0, b"\xff\xfb", _MP3),
    (0, b"\xff\xf3", _MP3),
    (0, b"\xff\xf2", _MP3),
    (0, b"PK\x03\x04", _ZIP),
    (0, b"\x1f\x8b", _GZIP),
    (0, b"7z\xbc\xaf\x27\x1c", _SEVENZIP),
    (0, b"Rar!\x1a\x07", _RAR),
    (0, b"FWS", _SWF),
    (0, b"CWS", _SWF),
    (0, b"ZWS", _SWF),
    (0, b".RMF", _REALMEDIA),
    (0, b"\xffWPC", _WORDPERFECT),
)

_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

# Extension fallback for formats with no reliable leading signature (mostly text-
# based or container formats), used only when no content signature matched.
_EXTENSION_MAP: dict[str, FormatInfo] = {
    "txt": _TEXT,
    "md": _TEXT,
    "csv": FormatInfo("CSV", "text/csv", "x-fmt/18", False, ""),
    "json": FormatInfo("JSON", "application/json", "fmt/817", False, ""),
    "xml": _XML,
    "html": _HTML,
    "htm": _HTML,
    "svg": FormatInfo("SVG", "image/svg+xml", "fmt/91", False, ""),
    "doc": _OLE2_OFFICE,
    "xls": _OLE2_OFFICE,
    "ppt": _OLE2_OFFICE,
    "wpd": _WORDPERFECT,
    "rm": _REALMEDIA,
    "ram": _REALMEDIA,
    "swf": _SWF,
    "rar": _RAR,
}


def _match_signature(data: bytes) -> FormatInfo | None:
    """Return the :class:`FormatInfo` whose magic number ``data`` starts with.

    Content-based identification is authoritative: a file's bytes do not lie about
    their format the way an extension can. RIFF, ISO Base Media (``ftyp``), and
    OLE2 carry a brand in a secondary position and are disambiguated here.
    """
    if data.startswith(_OLE2_MAGIC):
        return _OLE2_OFFICE
    if data.startswith(b"RIFF") and len(data) >= 12:
        brand = data[8:12]
        if brand == b"WAVE":
            return _WAV
        if brand == b"WEBP":
            return _WEBP
        if brand == b"AVI ":
            return FormatInfo(
                "Audio Video Interleave (AVI)",
                "video/x-msvideo",
                "fmt/5",
                False,
                "Ageing container; consider Matroska/FFV1 or MP4 for access.",
            )
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return _MP4
    for offset, magic, info in _SIGNATURES:
        if data[offset : offset + len(magic)] == magic:
            return info
    return None


def _looks_like_text(data: bytes) -> bool:
    """Whether ``data`` decodes as UTF-8 with no NUL and no C0 control noise.

    A clean UTF-8 decode (tolerating a trailing multi-byte sequence cut by the
    head-bytes window) with only ordinary whitespace among the control codes is
    treated as plain text — the safe, conservative default for the many text-based
    formats an archive holds (robustness).
    """
    if not data:
        return False
    if b"\x00" in data:
        return False
    sample = data
    # Tolerate a multi-byte UTF-8 sequence truncated by the read window.
    for _ in range(3):
        try:
            text = sample.decode("utf-8")
            break
        except UnicodeDecodeError:
            sample = sample[:-1]
            if not sample:
                return False
    else:
        return False
    allowed_controls = {"\t", "\n", "\r", "\f", "\v"}
    return all(ch >= " " or ch in allowed_controls for ch in text)


def _extension(filename: str) -> str:
    """The lower-cased extension of ``filename`` without the dot (``""`` if none)."""
    suffix = Path(filename).suffix
    return suffix[1:].lower() if suffix else ""


def identify_format(data: bytes, *, filename: str | None = None) -> FormatId:
    """Identify the format of ``data`` (optionally aided by ``filename``).

    Resolution order, strongest first (each step records its ``basis``):

    1. **signature** — a content-based magic-number match (authoritative);
    2. **extension** — the filename's extension, for formats with no reliable
       leading signature;
    3. **text** — a clean UTF-8 decode (plain text);
    4. **unknown** — none of the above; recorded honestly as ``application/
       octet-stream`` with a recommendation to identify it.

    Pure and deterministic: the same bytes and filename always yield the same
    :class:`FormatId` (reproducibility). No identity or content is logged or
    returned (no-outing rule)."""
    info = _match_signature(data)
    if info is not None:
        return FormatId.of(info, basis="signature")
    if filename:
        ext_info = _EXTENSION_MAP.get(_extension(filename))
        if ext_info is not None:
            return FormatId.of(ext_info, basis="extension")
    if _looks_like_text(data):
        return FormatId.of(_TEXT, basis="text")
    return FormatId.of(_UNKNOWN, basis="unknown")


def identify_file(path: Path) -> FormatId:
    """Identify the format of the file at ``path`` by reading only its head bytes.

    Reads at most :data:`_HEAD_BYTES` so identifying a large oral-history video
    does not pull it all into memory (efficiency, minimal computing). The
    filename aids the extension fallback. Never reads or returns the file's
    content beyond the bytes needed to sniff its format (no-outing rule)."""
    path = Path(path)
    with open(path, "rb") as handle:
        head = handle.read(_HEAD_BYTES)
    return identify_format(head, filename=path.name)
