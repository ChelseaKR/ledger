"""EAD finding aids — a partner-ready collection-level description.

Encoded Archival Description (EAD, Society of American Archivists / Library of
Congress) is the finding-aid language institutional partners already navigate: a
university library or a Portico-style preservation service browses an EAD
document's hierarchical ``<dsc>`` the same way it browses its own collections.
:func:`to_ead_xml` renders one finding aid per *collection* — a named group of
already-disclosed records — with:

* an ``eadheader`` naming the finding aid and the archive as its custodian;
* an ``archdesc`` collection-level description (title, extent, language); and
* a ``dsc`` (description of subordinate components) with one ``<c01 level="item">``
  per record, carrying its title, identifier, date, and a content-warning note.

EX8 (signed deposit bundle) proves *integrity* to a partner; EX11's sibling
:mod:`ledger.metadata.mets` speaks the partner's *item*-level catalog language.
This module speaks the *collection*-level one — the descriptive layer an archivist
actually browses before deciding to ingest.

No-outing rule, enforced by type: :func:`to_ead_xml` takes a
``Sequence[DisclosedRecord]`` — the ONLY record shape a read path may emit,
produced solely by :func:`ledger.access.disclose` — never raw
:class:`~ledger.models.Record` objects. This mirrors the same boundary
:mod:`ledger.oai` already draws for OAI-PMH harvest: the caller discloses first,
this module only re-serializes what was already deemed safe to show. Content
warnings are surfaced (they must precede any render of underlying content,
per :func:`ledger.access.disclose`'s own contract); withheld field/payload names
and values are never read by this module.

Determinism: :func:`to_ead_xml` consults no clock or random source; the caller
supplies ``created`` explicitly, so the same disclosed collection always produces
byte-identical EAD (reproducibility).
"""

from __future__ import annotations

from collections.abc import Sequence
from xml.sax.saxutils import escape as _sax_escape

from ledger.models import DisclosedRecord

__all__ = ["to_ead_xml"]


# Characters XML 1.0 forbids even when escaped -- same rule as the sibling
# metadata modules (standards compliance, interoperability, robustness).
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
    ``&``/``<``/``>``): this module interpolates escaped record ids directly into
    a double-quoted ``id="c-..."`` attribute, so a literal quote in that value
    must not be able to break out of the attribute and produce malformed XML.
    """
    return _sax_escape(_xml_text(value), {'"': "&quot;", "'": "&apos;"})


_EAD_NS = "urn:isbn:1-931666-22-9"  # the EAD 2002 namespace, per the LC schema
_XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
_EAD_SCHEMA = "urn:isbn:1-931666-22-9 http://www.loc.gov/ead/ead.xsd"


def _first(dc: dict[str, list[str]], element: str) -> str:
    """The first value of a Dublin Core element, or ``""`` if absent."""
    values = dc.get(element) or []
    return values[0] if values else ""


def _unit_id(record: DisclosedRecord, *, base_url: str) -> str:
    """The item's ``unitid``: a stable identifier, its public URL if one is known."""
    if base_url:
        return f"{base_url.rstrip('/')}/record/{record.record_id}"
    return record.record_id


def _record_component(record: DisclosedRecord, *, base_url: str, indent: str) -> list[str]:
    """Render one record as a ``<c01 level="item">`` component."""
    inner = indent + "  "
    did_indent = inner + "  "
    lines = [f'{indent}<c01 level="item" id="c-{escape(record.record_id)}">']
    lines.append(f"{inner}<did>")
    lines.append(f"{did_indent}<unittitle>{escape(record.title)}</unittitle>")
    lines.append(f"{did_indent}<unitid>{escape(_unit_id(record, base_url=base_url))}</unitid>")
    date = _first(record.dublin_core, "date")
    if date:
        lines.append(f"{did_indent}<unitdate>{escape(date)}</unitdate>")
    description = _first(record.dublin_core, "description")
    if description:
        lines.append(f"{did_indent}<abstract>{escape(description)}</abstract>")
    languages = record.dublin_core.get("language") or []
    if languages:
        lines.append(
            f"{did_indent}<langmaterial><language>{escape(', '.join(languages))}"
            "</language></langmaterial>"
        )
    lines.append(f"{inner}</did>")
    for warning in record.content_warnings:
        lines.append(f'{inner}<note type="content-warning"><p>{escape(warning)}</p></note>')
    if record.withheld:
        lines.append(
            f"{inner}<note><p>{len(record.withheld)} field(s)/payload(s) not included "
            "in this finding aid (withheld by access policy)</p></note>"
        )
    lines.append(f"{indent}</c01>")
    return lines


def to_ead_xml(
    collection_title: str,
    records: Sequence[DisclosedRecord],
    *,
    created: str,
    collection_id: str,
    base_url: str = "",
    repository: str = "ledger",
) -> str:
    """Render ``records`` (already disclosed) as one EAD 2002 finding aid.

    ``records`` MUST already be :class:`~ledger.models.DisclosedRecord` instances
    -- the caller discloses first (:func:`ledger.access.disclose` /
    :func:`ledger.oais.to_dip`), this function only re-serializes what was already
    deemed safe to show for the grant the caller used, exactly as
    :mod:`ledger.oai` does for OAI-PMH harvest. There is no code path here that
    reads a ``Record`` or resolves an ``identity_ref``.

    ``collection_id`` seeds the finding aid's own EAD identifier (``eadid``);
    ``created`` is the caller-supplied publication date (determinism -- no wall
    clock). Records are emitted as a flat sequence of ``<c01 level="item">``
    components in the order given; hierarchical (series/subseries) arrangement is
    left to a future revision when a real partner's ingest profile calls for it.
    """
    lines = ['<?xml version="1.0" encoding="UTF-8"?>']
    lines.append(
        f'<ead xmlns="{_EAD_NS}" xmlns:xsi="{_XSI_NS}" xsi:schemaLocation="{_EAD_SCHEMA}">'
    )

    # --- eadheader ---------------------------------------------------------
    lines.append("  <eadheader>")
    lines.append(f"    <eadid>{escape(collection_id)}</eadid>")
    lines.append("    <filedesc>")
    lines.append("      <titlestmt>")
    lines.append(f"        <titleproper>{escape(collection_title)}</titleproper>")
    lines.append(f"        <author>{escape(repository)}</author>")
    lines.append("      </titlestmt>")
    lines.append("      <publicationstmt>")
    lines.append(f"        <publisher>{escape(repository)}</publisher>")
    lines.append(f"        <date>{escape(created)}</date>")
    lines.append("      </publicationstmt>")
    lines.append("    </filedesc>")
    lines.append("  </eadheader>")

    # --- archdesc (collection-level description) ----------------------------
    lines.append('  <archdesc level="collection">')
    lines.append("    <did>")
    lines.append(f"      <unittitle>{escape(collection_title)}</unittitle>")
    lines.append(f"      <unitid>{escape(collection_id)}</unitid>")
    lines.append(f"      <repository>{escape(repository)}</repository>")
    lines.append(f"      <physdesc><extent>{len(records)} item(s)</extent></physdesc>")
    lines.append("    </did>")

    # --- dsc (description of subordinate components) ------------------------
    lines.append("    <dsc>")
    for record in records:
        lines.extend(_record_component(record, base_url=base_url, indent="      "))
    lines.append("    </dsc>")

    lines.append("  </archdesc>")
    lines.append("</ead>")
    return "\n".join(lines)
