"""METS wrappers — a partner-ready descriptive shell around one archived item.

The Metadata Encoding and Transmission Standard (METS, Library of Congress) is the
catalog language institutional partners already speak: a university library or a
Portico-style preservation service ingests a METS document, not a bespoke ledger
manifest. :func:`to_mets_xml` renders one such document per AIP (Archival
Information Package), wrapping:

* a descriptive section (``dmdSec``) — the ``oai_dc`` metadata already produced by
  :mod:`ledger.metadata.dublincore`;
* an administrative section (``amdSec``/``digiprovMD``) — the PREMIS event history
  already produced by :mod:`ledger.metadata.premis`;
* a file section (``fileSec``) — one ``mets:file`` per payload, carrying its
  content-address digest as the METS ``CHECKSUM``; and
* a structural map (``structMap``) tying the item's single division to its files.

This module does not invent a new export boundary. EX8 (signed deposit bundle)
proves *integrity* to a partner; this speaks their *catalog language* over the
same disclosed data.

No-outing rule, enforced by type: :func:`to_mets_xml` takes a
:class:`~ledger.models.DisclosedRecord` — the ONLY record shape a read path may
emit, produced solely by :func:`ledger.access.disclose` — never a raw
:class:`~ledger.models.Record`. Because a ``DisclosedRecord`` structurally cannot
carry an ``identity_ref`` and already contains only the fields and payloads a
grant was permitted to see, the export path provably cannot include non-granted
material: there is no code path in this module that could reach a sealed field
even if it wanted to, because a sealed field was never withheld from disclosure
in the first place. Any field the caller withheld already appears only in
``withheld`` (with a safe reason), and :func:`to_mets_xml` never reads that
attribute's underlying values, only its redaction *labels* for the descriptive
note.

Determinism: every entry point takes ``created`` as a parameter and consults no
clock or random source, so the same disclosed record and PREMIS log always
produce byte-identical METS (reproducibility, so a partner's ingest checksum of
our export is itself stable and diffable across runs).
"""

from __future__ import annotations

from collections.abc import Sequence
from xml.sax.saxutils import escape as _sax_escape

from ledger.metadata.dublincore import to_oai_dc_xml
from ledger.metadata.premis import to_premis_xml
from ledger.models import DisclosedRecord, DublinCore, HashAlgo, PremisEvent

__all__ = ["to_mets_xml"]


# Characters XML 1.0 forbids even when escaped. Descriptive text and filenames can
# carry arbitrary operator- or contributor-supplied content, so strip these before
# escaping to keep the emitted XML well-formed (standards compliance,
# interoperability, robustness) -- the same rule the sibling metadata modules apply.
def _xml_text(value: str) -> str:
    return "".join(
        char
        for char in value
        if (code := ord(char)) in (0x9, 0xA, 0xD)
        or 0x20 <= code <= 0xD7FF
        or 0xE000 <= code <= 0xFFFD
        or 0x10000 <= code <= 0x10FFFF
    )


def escape(value: str) -> str:
    """XML-escape ``value`` after removing characters XML 1.0 disallows.

    Also escapes ``"`` and ``'`` (beyond ``xml.sax.saxutils.escape``'s default
    ``&``/``<``/``>``): this module interpolates escaped values directly into
    double-quoted attributes (``LABEL="..."``, ``xlink:href="..."``,
    ``xlink:title="..."``) built from contributor-suppliable text such as a
    record's title, so a literal quote in that text must not be able to break out
    of the attribute and produce malformed XML.
    """
    return _sax_escape(_xml_text(value), {'"': "&quot;", "'": "&apos;"})


_METS_NS = "http://www.loc.gov/METS/"
_XLINK_NS = "http://www.w3.org/1999/xlink"
_METS_SCHEMA = "http://www.loc.gov/METS/ http://www.loc.gov/standards/mets/mets.xsd"
_XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"

# METS CHECKSUMTYPE is a controlled vocabulary (the METS schema's enumerated
# ``mets:checksumType``). Our two fixity algorithms map onto it directly; any
# future algorithm not in the vocabulary is emitted uppercased as a best-effort
# label rather than raising, so a new algorithm degrades gracefully instead of
# breaking export (robustness).
_CHECKSUM_TYPE: dict[HashAlgo, str] = {
    HashAlgo.SHA256: "SHA-256",
    HashAlgo.BLAKE2B: "SHA-256",  # BLAKE2b has no METS vocabulary slot; SHA-256
    # (present on every payload as the addressing algorithm) is always available
    # as the METS-conformant checksum, so BLAKE2b payloads never need this branch.
}


def _file_id(index: int) -> str:
    """A stable, deterministic ``mets:file`` ID for the payload at ``index``."""
    return f"file-{index + 1}"


def _fptr_id(index: int) -> str:
    """The corresponding structural-map pointer ID."""
    return f"fptr-{index + 1}"


def _reindent(xml_fragment: str, indent: str) -> list[str]:
    """Drop an XML declaration line (if present) and indent the rest by ``indent``.

    Lets this module nest another module's already-correct XML (the ``oai_dc`` or
    PREMIS document) inside ``mets:xmlData`` without reparsing or duplicating its
    escaping logic -- reuse over reimplementation (simplicity, single source of
    truth for each sub-format).
    """
    lines = xml_fragment.splitlines()
    if lines and lines[0].startswith("<?xml"):
        lines = lines[1:]
    return [f"{indent}{line}" for line in lines]


def to_mets_xml(
    record: DisclosedRecord,
    *,
    created: str,
    premis_events: Sequence[PremisEvent] = (),
    base_url: str = "",
    package_id: str | None = None,
) -> str:
    """Render ``record`` (and its PREMIS history) as one partner-ready METS document.

    ``record`` MUST be a :class:`~ledger.models.DisclosedRecord` -- the safe,
    already-policy-filtered read shape -- never a raw
    :class:`~ledger.models.Record`; the type signature makes the no-outing
    boundary structural rather than conventional (safety). ``premis_events``
    should likewise be the opaque, identity-free events :mod:`ledger.metadata.premis`
    already handles; this function adds no vetting of its own beyond what
    :func:`~ledger.metadata.premis.to_premis_xml` already does.

    ``created`` is the caller-supplied timestamp for ``metsHdr/@CREATEDATE``
    (determinism -- no wall clock read here). ``base_url``, if given, is used to
    build an ``xlink:href`` for each payload pointing at the archive's own public
    file route; payloads are otherwise referenced by filename only.

    Withheld fields/payloads are summarized as a plain count in a ``note`` inside
    the descriptive section (honesty about lossiness, mirroring
    :func:`ledger.access.disclose`'s own ``withheld`` accounting) -- never by name
    or value, so the note itself cannot leak what was sealed.
    """
    obj_id = escape(package_id or record.record_id)
    lines = ['<?xml version="1.0" encoding="UTF-8"?>']
    lines.append(
        "<mets:mets "
        f'xmlns:mets="{_METS_NS}" '
        f'xmlns:xlink="{_XLINK_NS}" '
        f'xmlns:xsi="{_XSI_NS}" '
        f'xsi:schemaLocation="{_METS_SCHEMA}" '
        f'OBJID="{obj_id}" '
        'TYPE="AIP" '
        f'LABEL="{escape(record.title)}">'
    )

    # --- metsHdr ---------------------------------------------------------
    lines.append(f'  <mets:metsHdr CREATEDATE="{escape(created)}">')
    lines.append('    <mets:agent ROLE="ARCHIVIST" TYPE="ORGANIZATION">')
    lines.append("      <mets:name>ledger</mets:name>")
    lines.append("    </mets:agent>")
    lines.append("  </mets:metsHdr>")

    # --- dmdSec (descriptive metadata) ------------------------------------
    lines.append('  <mets:dmdSec ID="dmd-1">')
    lines.append('    <mets:mdWrap MDTYPE="DC">')
    lines.append("      <mets:xmlData>")
    lines.extend(_reindent(to_oai_dc_xml(_dublin_core_of(record)), "        "))
    lines.append("      </mets:xmlData>")
    lines.append("    </mets:mdWrap>")
    if record.withheld:
        lines.append(
            f"    <mets:note>{len(record.withheld)} field(s)/payload(s) not "
            "included in this export (withheld by access policy)</mets:note>"
        )
    lines.append("  </mets:dmdSec>")

    # --- amdSec (PREMIS provenance) ---------------------------------------
    if premis_events:
        lines.append('  <mets:amdSec ID="amd-1">')
        lines.append('    <mets:digiprovMD ID="digiprov-1">')
        lines.append('      <mets:mdWrap MDTYPE="PREMIS">')
        lines.append("        <mets:xmlData>")
        lines.extend(_reindent(to_premis_xml(premis_events), "          "))
        lines.append("        </mets:xmlData>")
        lines.append("      </mets:mdWrap>")
        lines.append("    </mets:digiprovMD>")
        lines.append("  </mets:amdSec>")

    # --- fileSec + structMap (payload) -------------------------------------
    root = base_url.rstrip("/")
    lines.append("  <mets:fileSec>")
    lines.append('    <mets:fileGrp USE="payload">')
    for index, payload in enumerate(record.payloads):
        checksum_type = _CHECKSUM_TYPE.get(payload.address.algo, payload.address.algo.value.upper())
        attrs = [
            f'ID="{_file_id(index)}"',
            f'MIMETYPE="{escape(payload.media_type)}"',
            f'SIZE="{payload.size_bytes}"',
            f'CHECKSUM="{escape(payload.address.digest)}"',
            f'CHECKSUMTYPE="{checksum_type}"',
        ]
        lines.append(f"      <mets:file {' '.join(attrs)}>")
        href = (
            f"{root}/record/{escape(record.record_id)}/{escape(payload.filename)}"
            if root
            else escape(payload.filename)
        )
        lines.append(
            f'        <mets:FLocat LOCTYPE="URL" xlink:href="{href}" '
            f'xlink:title="{escape(payload.filename)}"/>'
        )
        lines.append("      </mets:file>")
    lines.append("    </mets:fileGrp>")
    lines.append("  </mets:fileSec>")

    lines.append('  <mets:structMap TYPE="physical">')
    lines.append(f'    <mets:div TYPE="item" LABEL="{escape(record.title)}" DMDID="dmd-1">')
    for index in range(len(record.payloads)):
        lines.append(f'      <mets:fptr ID="{_fptr_id(index)}" FILEID="{_file_id(index)}"/>')
    lines.append("    </mets:div>")
    lines.append("  </mets:structMap>")

    lines.append("</mets:mets>")
    return "\n".join(lines)


def _dublin_core_of(record: DisclosedRecord) -> DublinCore:
    """Adapt a disclosed record's ``dublin_core`` dict to what ``to_oai_dc_xml`` needs.

    ``to_oai_dc_xml`` takes a :class:`~ledger.models.DublinCore`, but a
    ``DisclosedRecord`` stores the already-serialized dict form (its
    :meth:`DublinCore.to_dict` output). Rebuilding the dataclass here (rather than
    changing either module's public shape) keeps both sibling exporters free to
    evolve independently (modularity, orthogonality).
    """
    return DublinCore.from_dict(record.dublin_core)
