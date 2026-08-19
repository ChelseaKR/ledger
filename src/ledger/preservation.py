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
  proprietary RAR, the dead 1990s desktop: Lotus 1-2-3, Quattro Pro, Windows
  Write, Access/Jet, and the discontinued ebook formats) are marked ``at_risk``
  with a plain-language migration recommendation, so the preservation risk is
  surfaced at ingest rather than discovered when the last reader stops working.
* **Say when no assessment was possible.** ``at_risk=False`` used to mean two
  incompatible things — "assessed, and fine" and "never assessed at all" — and a
  steward reading the advisory could not tell them apart. A file the identifier
  cannot name is now separately :attr:`~FormatId.unassessable`, because the
  absence of a risk finding is not a finding of safety (ADR 0010).
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
    only), ``"text"`` (decoded cleanly as UTF-8), ``"signature-offset"`` (a
    content signature found *after* byte 0, behind a wrapper or preamble),
    ``"empty"`` (the file has no bytes at all), or ``"unknown"`` (none of the
    above). A steward reading a preservation report can tell a confident
    identification from a guess (inspectability, honesty).

    ``header_offset`` is where the signature was found; it is ``0`` for every
    basis except ``"signature-offset"``.
    """

    name: str
    media_type: str
    puid: str | None
    at_risk: bool
    recommendation: str
    basis: str
    header_offset: int = 0

    @classmethod
    def of(cls, info: FormatInfo, *, basis: str, header_offset: int = 0) -> FormatId:
        """Build a :class:`FormatId` from a registry :class:`FormatInfo`."""
        return cls(
            name=info.name,
            media_type=info.media_type,
            puid=info.puid,
            at_risk=info.at_risk,
            recommendation=info.recommendation,
            basis=basis,
            header_offset=header_offset,
        )

    @property
    def unassessable(self) -> bool:
        """Whether no format was determined, so no risk assessment was possible.

        This is the *second* half of the preservation-risk signal, and it exists
        because the first half alone was misread. ``at_risk`` is a positive finding
        about a **known** format — it is obsolescent, and here is the migration
        target. Its complement was therefore doing double duty: ``at_risk=False``
        meant both "we assessed this and it is fine" and "we have no idea what this
        is", and only the first reading is reassuring.

        Measured on the OPF format-corpus (679 files), that conflation hid **66
        genuinely obsolete files** — Lotus 1-2-3, Quattro Pro, Access, Windows
        Write, DB/TextWorks, the dead ebook formats — inside the same quiet
        not-at-risk bucket as a healthy PNG. Splitting the signal is what lets
        ``at_risk`` stay precise (it never fires on a format nobody has assessed)
        while nothing silently passes as safe (ADR 0010).

        Deliberately *not* the same thing as ``at_risk``: the remedies differ. An
        at-risk file needs migrating to a named target; an unassessable one needs
        identifying first, and nothing can be recommended until it is.
        """
        return self.basis == "unknown"

    def summary(self) -> str:
        """A one-line, no-outing-safe description for a PREMIS event detail."""
        puid = self.puid or "no-puid"
        line = f"identified as {self.name} [{puid}] via {self.basis}; media-type {self.media_type}"
        if self.header_offset:
            # Say so explicitly: this is a preservation-planning signal in its own
            # right, because a strict identifier (DROID, veraPDF) anchors the header
            # at byte 0 and will not recognise the file at all.
            line += (
                f"; header at byte {self.header_offset}, not 0 — a wrapper or preamble "
                "precedes it, and strict validators will not identify this file"
            )
        if self.at_risk:
            line += f"; AT-RISK — {self.recommendation}"
        if self.unassessable:
            # Never let the log imply that "no risk recorded" means "no risk". This
            # is the same defect class as the outcome that used to read "success".
            line += (
                "; UNASSESSABLE — no format was identified, so no preservation-risk "
                "assessment was possible; this is not a finding that the file is safe"
            )
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
_WEBP = FormatInfo("WebP", "image/webp", "fmt/566", False, "")
_WAV = FormatInfo("Broadcast/WAVE audio", "audio/x-wav", "fmt/141", False, "")
_FLAC = FormatInfo("FLAC", "audio/flac", "fmt/279", False, "")
_OGG = FormatInfo("Ogg", "application/ogg", "fmt/203", False, "")
_MP3 = FormatInfo(
    "MP3", "audio/mpeg", "fmt/134", False, "Lossy; keep any lossless master (e.g. FLAC/WAV)."
)
_MP4 = FormatInfo("MPEG-4 / QuickTime (ISO BMFF)", "video/mp4", "fmt/199", False, "")
_MKV = FormatInfo("Matroska / WebM", "video/x-matroska", "fmt/569", False, "")
_ZIP = FormatInfo("ZIP", "application/zip", "x-fmt/263", False, "")
_GZIP = FormatInfo("GZIP", "application/gzip", "x-fmt/266", False, "")
_SEVENZIP = FormatInfo("7-Zip", "application/x-7z-compressed", "fmt/484", False, "")
_TEXT = FormatInfo("Plain text (UTF-8)", "text/plain", "x-fmt/111", False, "")
_XML = FormatInfo("XML", "application/xml", "fmt/101", False, "")
_HTML = FormatInfo("HTML", "text/html", "fmt/471", False, "")
# Markdown was mapped to plain text here while `mimetypes` knew it as text/markdown,
# so the record and the preservation log disagreed on 73 of the corpus's files — the
# largest single divergence measured (ADR 0010, issue #144). The registry is meant to
# be the better-informed source; on this row it was the worse one.
_MARKDOWN = FormatInfo("Markdown", "text/markdown", "fmt/1149", False, "")
_RTF = FormatInfo(
    "Rich Text Format",
    "application/rtf",
    "fmt/45",
    False,
    "Widely readable, but consider ODF or PDF/A for an archival master.",
)

# JPEG 2000 family. This is the preservation *master* format for most library and
# museum digitisation programmes (a scanned zine page, a photographed banner), so a
# preservation tool that cannot name it is blind to exactly the files an archive
# most cares about keeping. All four container flavours share one signature box and
# are told apart by the brand at offset 20; the raw codestream has its own marker.
_JP2 = FormatInfo("JP2 (JPEG 2000 part 1)", "image/jp2", "x-fmt/392", False, "")
_JPX = FormatInfo("JPX (JPEG 2000 part 2)", "image/jpx", "fmt/151", False, "")
_JPM = FormatInfo("JPM (JPEG 2000 part 6)", "image/jpm", "fmt/463", False, "")
_MJ2 = FormatInfo("MJ2 (Motion JPEG 2000)", "video/mj2", "fmt/337", False, "")
_J2C = FormatInfo(
    "JPEG 2000 codestream",
    "image/jp2",
    "fmt/1794",
    False,
    "A bare codestream carries no metadata; wrap it in a JP2 container for preservation.",
)

# At-risk: obsolescent or proprietary formats whose long-term usability is in
# doubt. NDSA/DPC treat migration of these as a core preservation activity.
_OLE2_OFFICE = FormatInfo(
    # PRONOM's own name for fmt/111 is "OLE2 Compound Document Format", with no
    # extensions attached, because OLE2 is a *container* many applications used.
    # Calling it "Microsoft Office" was an overclaim the corpus caught: a Quattro
    # Pro .wb3 and a .qpw are OLE2 files and are not Microsoft anything. The name
    # now matches what the PUID actually asserts; naming the inner application
    # would mean parsing the OLE2 directory, which this module deliberately does not.
    "OLE2 Compound Document (legacy Office .doc/.xls/.ppt and other OLE2 applications)",
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
    "x-fmt/190",
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

# --- the dead 1990s desktop ------------------------------------------------
#
# Added after the OPF format-corpus run measured this module catching 25 endangered
# files and missing 66 (ADR 0010). These are not corpus trivia: they are the formats
# a community archive inherits when someone donates the contents of an old hard
# drive, and every one of them is a spreadsheet, database, or manuscript nobody can
# open today. Every signature below was read off the corpus bytes and then checked
# against PRONOM's published DROID signature file (V120) — the same source that
# caught three wrong PUIDs in the previous pass — so no identifier here is asserted
# on memory.
#
# Where PRONOM has several PUIDs whose head signatures are byte-identical (Access,
# InDesign), ``puid`` is ``None``: naming one of them would be a guess dressed as an
# interoperable fact, which is precisely the defect that made this check necessary.

_LOTUS_RECOMMENDATION = (
    "Obsolete spreadsheet format with no maintained reader. Migrate to ODF/OOXML "
    "or CSV, and keep a PDF/A rendering of the formatted sheet."
)
_LOTUS_WK1 = FormatInfo(
    "Lotus 1-2-3 Worksheet (WK1)",
    "application/vnd.lotus-1-2-3",
    "x-fmt/114",
    True,
    _LOTUS_RECOMMENDATION,
)
_LOTUS_WKS = FormatInfo(
    "Lotus 1-2-3 Worksheet (WKS)",
    "application/vnd.lotus-1-2-3",
    "x-fmt/117",
    True,
    _LOTUS_RECOMMENDATION,
)
_LOTUS_WK3 = FormatInfo(
    "Lotus 1-2-3 Worksheet (WK3)",
    "application/vnd.lotus-1-2-3",
    "x-fmt/115",
    True,
    _LOTUS_RECOMMENDATION,
)
_LOTUS_WK4 = FormatInfo(
    "Lotus 1-2-3 Worksheet (WK4)",
    "application/vnd.lotus-1-2-3",
    "x-fmt/116",
    True,
    _LOTUS_RECOMMENDATION,
)
_LOTUS_123 = FormatInfo(
    "Lotus 1-2-3 Worksheet (123)",
    "application/vnd.lotus-1-2-3",
    "fmt/1452",
    True,
    _LOTUS_RECOMMENDATION,
)

_QPRO_RECOMMENDATION = (
    "Obsolete spreadsheet format; the last reader shipped with software that is no "
    "longer sold. Migrate to ODF/OOXML or CSV and keep a PDF/A rendering."
)
_QPRO_WQ1 = FormatInfo(
    "Quattro Pro for DOS (WQ1)", "application/x-quattropro", "x-fmt/121", True, _QPRO_RECOMMENDATION
)
_QPRO_WQ2 = FormatInfo(
    "Quattro Pro for DOS (WQ2)", "application/x-quattropro", "x-fmt/122", True, _QPRO_RECOMMENDATION
)
_QPRO_WB1 = FormatInfo(
    "Quattro Pro for Windows (WB1)",
    "application/x-quattropro",
    "fmt/834",
    True,
    _QPRO_RECOMMENDATION,
)
_QPRO_WB2 = FormatInfo(
    "Quattro Pro for Windows (WB2)",
    "application/x-quattropro",
    "fmt/835",
    True,
    _QPRO_RECOMMENDATION,
)

_WINWRITE_RECOMMENDATION = (
    "Windows Write was discontinued with Windows 95. Migrate to ODF/OOXML or PDF/A."
)
_WINWRITE = FormatInfo(
    "Write for Windows Document",
    "application/x-mswrite",
    "x-fmt/12",
    True,
    _WINWRITE_RECOMMENDATION,
)
_WINWRITE_ALT = FormatInfo(
    "Write for Windows Document", "application/x-mswrite", "x-fmt/4", True, _WINWRITE_RECOMMENDATION
)

_ACCESS_RECOMMENDATION = (
    "A proprietary database, not a document: the records are only readable through "
    "Microsoft Access. Export the tables to CSV and the schema to plain text now, "
    "and keep the original alongside them."
)
# PRONOM distinguishes Jet 3 from Jet 4 by the five bytes after the "Standard Jet DB"
# string, but assigns TWO byte-identical PUIDs to each (x-fmt/238 and x-fmt/239 for
# Jet 3; x-fmt/240 and x-fmt/241 for Jet 4), separated by metadata further inside the
# file than this module reads. So the *format* is named confidently and the PUID is
# left unset rather than picking one of a pair at random.
_ACCESS_JET3 = FormatInfo(
    "Microsoft Access database (Jet 3 — Access 95/97)",
    "application/x-msaccess",
    None,
    True,
    _ACCESS_RECOMMENDATION,
)
_ACCESS_JET4 = FormatInfo(
    "Microsoft Access database (Jet 4 — Access 2000-2003)",
    "application/x-msaccess",
    None,
    True,
    _ACCESS_RECOMMENDATION,
)

_EBOOK_RECOMMENDATION = (
    "A discontinued ebook format, usually DRM-bound to a reader that no longer "
    "exists. Migrate the text to EPUB and keep a plain-text or PDF/A rendering."
)
_LIT = FormatInfo(
    "Microsoft Reader eBook (LIT)",
    "application/x-ms-reader",
    "fmt/867",
    True,
    _EBOOK_RECOMMENDATION,
)
_LRF = FormatInfo(
    "Broad Band eBook (Sony BBeB/LRF)",
    "application/x-sony-bbeb",
    "fmt/518",
    True,
    _EBOOK_RECOMMENDATION,
)
_ROCKET = FormatInfo(
    "Rocket eBook", "application/x-rocketbook", "fmt/485", True, _EBOOK_RECOMMENDATION
)
# PRONOM has no entry for the Shanda Bambook format at V120 — an unassessable format
# even for the national registry, which is the whole argument for ADR 0010's split.
_SNB = FormatInfo(
    "Shanda Bambook eBook (SNB)", "application/x-snb", None, True, _EBOOK_RECOMMENDATION
)
# Both of these are Palm databases and PRONOM gives both byte sequences fmt/396. The
# .azw3 files in the corpus carry BOOKMOBI, not Amazon's `kindle:` marker (fmt/1937),
# so the bytes say Mobipocket whatever the extension claims — which is the entire
# reason content-based identification outranks the filename here.
_MOBIPOCKET = FormatInfo(
    "Mobipocket eBook (Palm database)",
    "application/x-mobipocket-ebook",
    "fmt/396",
    True,
    _EBOOK_RECOMMENDATION,
)
_PALMDOC = FormatInfo(
    "PalmDOC / AportisDoc (Palm database)",
    "application/vnd.palm",
    "fmt/396",
    True,
    _EBOOK_RECOMMENDATION,
)

_IBM_DCA = FormatInfo(
    "IBM DisplayWrite / DCA document",
    "application/x-ibm-dca",
    "x-fmt/148",
    True,
    "IBM's Document Content Architecture, from a word processor discontinued in the "
    "early 1990s. Migrate to ODF/OOXML or PDF/A; no current software opens it.",
)

_ARJ = FormatInfo(
    "ARJ archive",
    "application/x-arj",
    "fmt/610",
    True,
    "Obsolete DOS-era archive container. Unpack and repackage as ZIP or tar; a "
    "container nobody can open takes everything inside it down with it.",
)
# Every InDesign PUID from fmt/196 onward shares one head signature and is separated
# only by version metadata this module does not read, so no PUID is asserted.
_INDESIGN = FormatInfo(
    "Adobe InDesign document",
    "application/x-indesign",
    None,
    True,
    "Proprietary and version-locked — InDesign will not open a document more than a "
    "few major versions old. Export IDML plus a PDF/A rendering while a licence "
    "that can still open it exists.",
)
# Inmagic DB/TextWorks: a proprietary library/museum catalogue whose database is
# split across ten single-purpose files. PRONOM has no entry for any of them, which
# matters — this is a *cataloguing* system, so losing it loses the finding aid rather
# than one record. Named as a family; the three-letter tag says which component.
_DBTEXTWORKS_TAGS = frozenset(
    {"ACF", "BTX", "DBO", "DBR", "DBS", "IXL", "OCC", "SDO", "TBA", "TBU"}
)
_DBTEXTWORKS_RECOMMENDATION = (
    "A component of an Inmagic DB/TextWorks catalogue — proprietary, discontinued, "
    "and only meaningful alongside the other files of the same database. Export the "
    "catalogue to a delimited text or XML dump before the software is unavailable, "
    "and keep the whole file set together."
)

# Unidentified content. Recorded honestly (an unrecognised format is itself a
# preservation-planning signal) and NOT flagged at_risk, which stays reserved for
# *known* obsolescent formats so the at-risk advisory keeps its precision. The
# honesty now lives in a signal of its own — :attr:`FormatId.unassessable` — instead
# of being inferred from the absence of one (ADR 0010).
_UNKNOWN = FormatInfo(
    "Unidentified",
    "application/octet-stream",
    None,
    False,
    "Unrecognised format — identify and document it before relying on it.",
)

# A zero-byte file is not unidentifiable, it is *empty*, and saying "Unidentified"
# for one buries the more useful finding. 31 of the corpus's 118 unidentified files
# were simply empty; in a real deposit that is a failed transfer, and it is worth
# catching at ingest rather than at the next audit.
_EMPTY = FormatInfo(
    "Empty file (zero bytes)",
    "application/octet-stream",
    None,
    False,
    "A zero-byte payload is almost always a truncated transfer or a placeholder. "
    "Check the source before treating this record as preserved.",
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
    # RTF is ASCII, so without this it decodes cleanly and is filed as plain text —
    # losing the format of a word-processing document that carries real structure.
    (0, b"{\\rtf", _RTF),
    # A bare JPEG 2000 codestream (SOC + SIZ markers), as distinct from a JP2 container.
    (0, b"\xff\x4f\xff\x51", _J2C),
    # --- the dead 1990s desktop (see ADR 0010) ---------------------------------
    # Lotus and Quattro Pro share a BOF record whose first four bytes are the record
    # type and length; the two bytes after it are the version word, and that word is
    # the only thing telling a Lotus WK1 from a Quattro Pro WQ2. Each entry below is
    # DROID V120's own byte sequence for that PUID, so the discrimination is
    # PRONOM's, not a guess — and the longer sequences are listed before the shorter
    # ones they would otherwise shadow.
    (0, b"\x00\x00\x02\x00\x06\x04\x06\x00\x08\x00", _LOTUS_WK1),
    (0, b"\x00\x00\x02\x00\x04\x04", _LOTUS_WKS),
    (0, b"\x00\x00\x1a\x00\x00\x10\x04\x00", _LOTUS_WK3),
    (0, b"\x00\x00\x1a\x00\x02\x10\x04\x00", _LOTUS_WK4),
    (0, b"\x00\x00\x1a\x00\x03\x10\x04\x00", _LOTUS_123),
    (0, b"\x00\x00\x02\x00\x20\x51", _QPRO_WQ1),
    (0, b"\x00\x00\x02\x00\x21\x51", _QPRO_WQ2),
    (0, b"\x00\x00\x02\x00\x01\x10", _QPRO_WB1),
    (0, b"\x00\x00\x02\x00\x02\x10", _QPRO_WB2),
    (0, b"\x31\xbe\x00\x00\x00\xab\x00\x00\x00\x00\x00\x00\x00\x00", _WINWRITE),
    (0, b"\x32\xbe\x00\x00\x00\xab\x00\x00\x00\x00\x00\x00\x00\x00", _WINWRITE_ALT),
    # Jet 3 and Jet 4 differ only in the five bytes after the version string.
    (0, b"\x00\x01\x00\x00Standard Jet DB\x00\x01\x00\x00\x00", _ACCESS_JET4),
    (0, b"\x00\x01\x00\x00Standard Jet DB\x00\x00\x00\x00\x00", _ACCESS_JET3),
    (0, b"ITOLITLS", _LIT),
    (0, b"L\x00R\x00F\x00\x00\x00", _LRF),
    (0, b"\xb0\x0c\xb0\x0c", _ROCKET),
    (0, b"SNBP", _SNB),
    (0, b"\x60\xea", _ARJ),
    (0, b"\x00\x05\xe1\x03\x00\x00\x20\xe2\x05\x00\x01\x51\x01\x00", _IBM_DCA),
    (0, b"\x06\x06\xed\xf5\xd8\x1d\x46\xe5\xbd\x31\xef\xe7\xfe\x74\xb7\x1dDOCUMENT", _INDESIGN),
    # A Palm database opens with a 32-byte free-text name, so its real identity is the
    # type+creator pair at offset 60 — which is why these are offset entries and why
    # the `.azw3` files in the corpus resolve to Mobipocket rather than to Kindle.
    (60, b"BOOKMOBI", _MOBIPOCKET),
    (60, b"TEXtREAd", _PALMDOC),
)

_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

#: The JPEG 2000 signature box that opens every JP2-family container (ISO/IEC
#: 15444-1 Annex I). The flavour is then read from the ``ftyp`` brand at offset 20.
_JP2_SIGNATURE_BOX = b"\x00\x00\x00\x0cjP  \r\n\x87\n"
_JP2_BRANDS: dict[bytes, FormatInfo] = {
    b"jp2 ": _JP2,
    b"jpx ": _JPX,
    b"jpm ": _JPM,
    b"mjp2": _MJ2,
}

# Extension fallback for formats with no reliable leading signature (mostly text-
# based or container formats), used only when no content signature matched.
_EXTENSION_MAP: dict[str, FormatInfo] = {
    "txt": _TEXT,
    "md": _MARKDOWN,
    "markdown": _MARKDOWN,
    "csv": FormatInfo("CSV", "text/csv", "x-fmt/18", False, ""),
    "json": FormatInfo("JSON", "application/json", "fmt/817", False, ""),
    "xml": _XML,
    "html": _HTML,
    "htm": _HTML,
    "svg": FormatInfo("SVG", "image/svg+xml", "fmt/91", False, ""),
    "rtf": _RTF,
    "jp2": _JP2,
    "jpf": _JPX,
    "jpx": _JPX,
    "jpm": _JPM,
    "mj2": _MJ2,
    "mjp2": _MJ2,
    "j2c": _J2C,
    "j2k": _J2C,
    "jpc": _J2C,
    # NOTE: there is deliberately no "doc"/"xls"/"ppt" row here. Those rows could
    # only ever fire on a file whose bytes are *not* OLE2 — a real legacy Office file
    # matches the OLE2 signature two steps earlier — so by construction they were
    # reachable only when wrong, and they wrote PUID fmt/111 into the PREMIS log as
    # fact. Measured on the OPF corpus: all five files the ".doc" row identified were
    # WordPerfect or IBM DisplayWrite documents, and not one was Microsoft anything.
    # PRONOM itself lists ".doc" under WordPerfect (x-fmt/44) as well as under Word.
    # Losing the row costs nothing real and those files are now honestly unassessable
    # (ADR 0010) instead of confidently mislabelled.
    "wpd": _WORDPERFECT,
    "rm": _REALMEDIA,
    "ram": _REALMEDIA,
    "swf": _SWF,
    "rar": _RAR,
}


def _match_dbtextworks(data: bytes) -> FormatInfo | None:
    """Match an Inmagic DB/TextWorks component by its fixed 16-byte header.

    Every file of a DB/TextWorks database opens the same way: a three-letter
    component tag, a space, a three-digit format version, a space, and the version's
    release date as ``MM/DD/YY``. Checking that *shape* — rather than listing ten
    four-byte prefixes — is what keeps a plain-text file that happens to begin
    ``OCC `` from being claimed as a catalogue index.

    PRONOM has no PUID for any of these at V120, so the family is named from its own
    header and carries no identifier it cannot honour.
    """
    if len(data) < 16:
        return None
    tag = data[:3].decode("ascii", "replace")
    if tag not in _DBTEXTWORKS_TAGS:
        return None
    if data[3] != 0x20 or data[7] != 0x20:
        return None
    if not data[4:7].isdigit():
        return None
    stamp = data[8:16].decode("ascii", "replace")
    if len(stamp) != 8 or stamp[2] != "/" or stamp[5] != "/":
        return None
    if not (stamp[:2].isdigit() and stamp[3:5].isdigit() and stamp[6:].isdigit()):
        return None
    return FormatInfo(
        f"Inmagic DB/TextWorks database component ({tag})",
        "application/x-dbtextworks",
        None,
        True,
        _DBTEXTWORKS_RECOMMENDATION,
    )


_AVI = FormatInfo(
    "Audio Video Interleave (AVI)",
    "video/x-msvideo",
    "fmt/5",
    False,
    "Ageing container; consider Matroska/FFV1 or MP4 for access.",
)

#: RIFF is a container, not a format: WAVE, WebP, and AVI all open with ``RIFF`` and
#: are told apart only by the four-byte form type at offset 8.
_RIFF_FORMS: dict[bytes, FormatInfo] = {b"WAVE": _WAV, b"WEBP": _WEBP, b"AVI ": _AVI}


def _match_riff(data: bytes) -> FormatInfo | None:
    """Match a RIFF container by its form type, or ``None`` if this is not RIFF."""
    if not data.startswith(b"RIFF") or len(data) < 12:
        return None
    return _RIFF_FORMS.get(data[8:12])


def _match_signature(data: bytes) -> FormatInfo | None:
    """Return the :class:`FormatInfo` whose magic number ``data`` starts with.

    Content-based identification is authoritative: a file's bytes do not lie about
    their format the way an extension can. RIFF, ISO Base Media (``ftyp``), and
    OLE2 carry a brand in a secondary position and are disambiguated here.
    """
    if data.startswith(_OLE2_MAGIC):
        return _OLE2_OFFICE
    if data.startswith(_JP2_SIGNATURE_BOX):
        # Every JP2-family file opens with the same signature box; the brand in the
        # following ``ftyp`` box says which one. An unrecognised brand still means
        # "some JPEG 2000 container", so fall back to JP2 rather than to unknown.
        return _JP2_BRANDS.get(data[20:24], _JP2)
    riff = _match_riff(data)
    if riff is not None:
        return riff
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return _MP4
    dbtextworks = _match_dbtextworks(data)
    if dbtextworks is not None:
        return dbtextworks
    for offset, magic, info in _SIGNATURES:
        if data[offset : offset + len(magic)] == magic:
            return info
    return None


#: Byte-order marks stripped before sniffing an XML declaration. A BOM is legal
#: in front of ``<?xml`` and would otherwise hide it.
_BOMS: tuple[bytes, ...] = (b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")


def _looks_like_xml(data: bytes) -> bool:
    """Whether ``data`` opens with an XML declaration.

    The metadata standards this project writes are XML: PREMIS, METS, Dublin
    Core, and OAI-PMH responses. Without this check an XML file that reaches an
    archive without its ``.xml`` extension is recorded as ``text/plain``
    (``x-fmt/111``) instead of XML (``fmt/101``), which puts a wrong format in
    the preservation metadata for exactly the files whose format matters most.
    Files do arrive renamed or extension-less, which is why content-based
    identification exists at all.

    Deliberately *not* a step-one signature: an SVG normally opens with the same
    declaration, and SVG is identified by extension here, so promoting ``<?xml``
    to a signature would relabel every ``.svg`` as generic XML. Running after
    the extension step keeps ``.svg`` and ``.xml`` exactly as they were and only
    changes files no earlier step could name.
    """
    head = data
    for bom in _BOMS:
        if head.startswith(bom):
            head = head[len(bom) :]
            break
    return head.lstrip()[:5] == b"<?xml"


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


#: How far past byte 0 a displaced header is still looked for. 1024 is the
#: tolerance Adobe's own readers document for the ``%PDF-`` header, and it bounds
#: the scan so identification stays cheap and cannot wander into file content.
_MAX_HEADER_OFFSET = 1024


def _match_displaced_signature(data: bytes) -> tuple[FormatInfo, int] | None:
    """Find a ``%PDF-`` header that real-world damage has pushed past byte 0.

    Files do not reach an archive in the shape the standard describes. A PDF
    harvested over HTTP can carry the chunked-transfer length ahead of its header;
    one recovered from a Mac carries a MacBinary wrapper; one saved out of a web
    tool carries a ``data:`` URI prefix or a JSON envelope; plenty simply have a
    stray leading space. In every one of those cases the file is a PDF that opens
    fine in a reader, and ISO 32000-1 §7.5.2 wants the header first while Adobe's
    implementations accept it within the first 1024 bytes.

    Recording such a file as ``Unidentified`` is the worst of both worlds: the
    steward is told nothing, and the real defect — a wrapper that a strict
    validator will choke on — goes unnamed. So this identifies the format *and*
    reports the offset, which :meth:`FormatId.summary` puts in the PREMIS detail.

    Deliberately the *last* step before ``unknown``, and deliberately PDF-only.
    Running it after the extension, XML, and text steps means it can only change
    files that no earlier step could name, so a text file that merely mentions
    ``%PDF-`` is still plain text. Returns the format and the offset, or ``None``.
    """
    index = data.find(b"%PDF-", 1, _MAX_HEADER_OFFSET + len(b"%PDF-"))
    if index == -1:
        return None
    return _PDF, index


def _extension(filename: str) -> str:
    """The lower-cased extension of ``filename`` without the dot (``""`` if none)."""
    suffix = Path(filename).suffix
    return suffix[1:].lower() if suffix else ""


def identify_format(data: bytes, *, filename: str | None = None) -> FormatId:
    """Identify the format of ``data`` (optionally aided by ``filename``).

    Resolution order, strongest first (each step records its ``basis``):

    0. **empty** — the file has no bytes. Checked first because every step below
       would otherwise fall through to ``unknown``, and "we could not identify
       this" is a much weaker and more alarming statement than "this file is
       empty" — which in a real deposit usually means a truncated transfer. 31 of
       the corpus's 118 unidentified files were simply empty.
    1. **signature** — a content-based magic-number match (authoritative);
    2. **extension** — the filename's extension, for formats with no reliable
       leading signature;
    3. **xml-declaration** — an ``<?xml`` opening, so a renamed or
       extension-less PREMIS/METS/Dublin Core file is recorded as XML rather
       than as plain text (see :func:`_looks_like_xml` for why this runs here
       and not as a signature);
    4. **text** — a clean UTF-8 decode (plain text);
    5. **signature-offset** — a ``%PDF-`` header displaced past byte 0 by a
       wrapper or preamble, which real files carry often enough to matter (see
       :func:`_match_displaced_signature` for why this runs last);
    6. **unknown** — none of the above; recorded honestly as ``application/
       octet-stream`` with a recommendation to identify it.

    Pure and deterministic: the same bytes and filename always yield the same
    :class:`FormatId` (reproducibility). No identity or content is logged or
    returned (no-outing rule)."""
    if not data:
        return FormatId.of(_EMPTY, basis="empty")
    info = _match_signature(data)
    if info is not None:
        return FormatId.of(info, basis="signature")
    if filename:
        ext_info = _EXTENSION_MAP.get(_extension(filename))
        if ext_info is not None:
            return FormatId.of(ext_info, basis="extension")
    if _looks_like_xml(data):
        return FormatId.of(_XML, basis="xml-declaration")
    if _looks_like_text(data):
        return FormatId.of(_TEXT, basis="text")
    displaced = _match_displaced_signature(data)
    if displaced is not None:
        info, offset = displaced
        return FormatId.of(info, basis="signature-offset", header_offset=offset)
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
