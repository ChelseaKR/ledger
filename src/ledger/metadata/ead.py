"""EAD finding aids — a partner-ready collection-level description.

Encoded Archival Description (EAD, Society of American Archivists / Library of
Congress) is the finding-aid language institutional partners already navigate: a
university library or a Portico-style preservation service browses an EAD
document's hierarchical ``<dsc>`` the same way it browses its own collections.
:func:`to_ead_xml` renders one finding aid per *collection* — a named group of
already-disclosed records — with:

* an ``eadheader`` naming the finding aid and the archive as its custodian;
* an ``archdesc`` collection-level description (title, extent, language, and —
  when the caller supplies one — a ``scopecontent`` note); and
* a ``dsc`` (description of subordinate components) carrying the archive's real
  arrangement (#202): a ``<c01 level="collection">`` per collection, a
  ``<c02 level="series">`` inside it, and the items under whichever of those
  they are filed in. A record nobody arranged stays a top-level
  ``<c01 level="item">``, which is exactly what every record was before #202,
  so a caller that passes no arrangement gets byte-identical output.

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

from ledger.models import DisclosedContainer, DisclosedRecord

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


def _tag(depth: int) -> str:
    """The EAD component element name at ``depth`` (1-based): ``c01``, ``c02``, ...

    EAD 2002 numbers its component elements rather than nesting one repeated
    element, so the depth is part of the tag. Clamped at ``c12``, the deepest
    EAD defines; the two-level container vocabulary plus items cannot reach it,
    and clamping means a future deeper arrangement produces valid EAD rather
    than a ``c13`` element no schema knows.
    """
    return f"c{min(depth, 12):02d}"


def _record_component(
    record: DisclosedRecord, *, base_url: str, indent: str, depth: int = 1
) -> list[str]:
    """Render one record as a ``<cNN level="item">`` component at ``depth``."""
    tag = _tag(depth)
    inner = indent + "  "
    did_indent = inner + "  "
    lines = [f'{indent}<{tag} level="item" id="c-{escape(record.record_id)}">']
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
    lines.append(f"{indent}</{tag}>")
    return lines


def _container_component(
    container: DisclosedContainer,
    children: Sequence[DisclosedContainer],
    records_by_parent: dict[str | None, list[DisclosedRecord]],
    *,
    base_url: str,
    indent: str,
    depth: int,
) -> list[str]:
    """Render one container and everything filed under it, recursively.

    The hierarchy element EAD exists for. ``children`` and ``records_by_parent``
    are already trimmed to what the viewer may see — this function reads no
    policy and makes no decision, exactly as :func:`_record_component` does not.
    """
    tag = _tag(depth)
    inner = indent + "  "
    did_indent = inner + "  "
    lines = [
        f'{indent}<{tag} level="{escape(container.level.value)}" '
        f'id="c-{escape(container.container_id)}">'
    ]
    lines.append(f"{inner}<did>")
    lines.append(f"{did_indent}<unittitle>{escape(container.title)}</unittitle>")
    lines.append(f"{did_indent}<unitid>{escape(container.container_id)}</unitid>")
    if container.dates:
        lines.append(f"{did_indent}<unitdate>{escape(container.dates)}</unitdate>")
    if container.extent:
        lines.append(
            f"{did_indent}<physdesc><extent>{escape(container.extent)}</extent></physdesc>"
        )
    lines.append(f"{inner}</did>")
    if container.scope_and_content:
        lines.append(f"{inner}<scopecontent><p>{escape(container.scope_and_content)}</p>")
        if container.inherited_scope:
            # Say where an inherited note came from rather than presenting it as
            # this container's own words (honesty; the same instinct as the
            # withheld-count note below).
            lines.append(f"{inner}  <p>Inherited from the collection above.</p>")
        lines.append(f"{inner}</scopecontent>")
    for record in records_by_parent.get(container.container_id, []):
        lines.extend(_record_component(record, base_url=base_url, indent=inner, depth=depth + 1))
    for child in children:
        lines.extend(
            _container_component(
                child,
                [],
                records_by_parent,
                base_url=base_url,
                indent=inner,
                depth=depth + 1,
            )
        )
    lines.append(f"{indent}</{tag}>")
    return lines


def _parent_of(container: DisclosedContainer) -> str | None:
    """The id of ``container``'s immediate visible parent, or ``None``.

    ``ancestors`` is the visible chain root-first and excludes the container
    itself, so its last member is the parent. A container whose parent this
    viewer may not see has an empty chain and reads as a root here, which is
    what keeps a hidden collection out of the finding aid entirely.
    """
    return container.ancestors[-1].container_id if container.ancestors else None


def _deepest_visible_placement(record: DisclosedRecord, known: set[str]) -> str | None:
    """The id of the deepest container in ``record``'s chain that is being rendered.

    A record whose container is not in this document (unarranged, or filed in
    something the viewer cannot see, or in a collection this finding aid is not
    about) belongs at the top level rather than nowhere — losing it would be the
    worse failure by far.
    """
    for step in reversed(record.placement):
        if step.container_id in known:
            return step.container_id
    return None


def to_ead_xml(
    collection_title: str,
    records: Sequence[DisclosedRecord],
    *,
    created: str,
    collection_id: str,
    base_url: str = "",
    repository: str = "ledger",
    arrangement: Sequence[DisclosedContainer] = (),
    scope_and_content: str = "",
    extent: str = "",
    dates: str = "",
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
    clock).

    ``arrangement`` is the set of :class:`~ledger.models.DisclosedContainer`
    objects this document should nest by (#202), produced by
    :func:`ledger.access.policy.disclose_container` exactly as ``records`` are
    produced by :func:`ledger.access.disclose`. Pass every container to render
    the whole archive's shape; pass one collection's series to render that
    collection's finding aid. Containers become ``<cNN>`` components at their
    real level and items nest under whichever container they are filed in.

    Pass nothing and the output is **byte-identical to the pre-#202 flat form**:
    every record is a top-level ``<c01 level="item">`` in the order given. The
    same is true of a record whose container is not in ``arrangement`` — it is
    rendered at the top level rather than dropped, because losing a record from
    a finding aid is by far the worse failure.

    ``scope_and_content``, ``extent`` and ``dates`` describe the unit the
    ``archdesc`` is about. ``extent`` falls back to the item count when the
    caller gives none, which is the pre-#202 behaviour; an archivist's own
    "4 boxes" replaces it when there is one.
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
    physdesc = extent if extent else f"{len(records)} item(s)"
    lines.append(f"      <physdesc><extent>{escape(physdesc)}</extent></physdesc>")
    if dates:
        lines.append(f"      <unitdate>{escape(dates)}</unitdate>")
    lines.append("    </did>")
    if scope_and_content:
        lines.append(f"    <scopecontent><p>{escape(scope_and_content)}</p></scopecontent>")

    # --- dsc (description of subordinate components) ------------------------
    lines.append("    <dsc>")
    lines.extend(_dsc_lines(records, arrangement, base_url=base_url, indent="      "))
    lines.append("    </dsc>")

    lines.append("  </archdesc>")
    lines.append("</ead>")
    return "\n".join(lines)


def _dsc_lines(
    records: Sequence[DisclosedRecord],
    arrangement: Sequence[DisclosedContainer],
    *,
    base_url: str,
    indent: str,
) -> list[str]:
    """The body of ``<dsc>``: the arrangement, then whatever is not in it.

    Two rules, and both are about not losing anything:

    * a record is filed under the **deepest** container of its chain that this
      document is rendering, and under nothing if that is none of them;
    * every record reaches the output exactly once, arranged or not.

    Nothing here reads a policy. ``records`` and ``arrangement`` have already
    been through :func:`ledger.access.disclose` and
    :func:`ledger.access.policy.disclose_container`, so a container the viewer
    may not see is simply absent from ``arrangement`` and the records under it
    fall back to the top level (or, far more often, are absent too — the same
    ceiling hid both).
    """
    known = {container.container_id for container in arrangement}
    by_parent: dict[str | None, list[DisclosedRecord]] = {}
    for record in records:
        by_parent.setdefault(_deepest_visible_placement(record, known), []).append(record)

    children: dict[str | None, list[DisclosedContainer]] = {}
    for container in arrangement:
        parent = _parent_of(container)
        children.setdefault(parent if parent in known else None, []).append(container)

    lines: list[str] = []
    for container in children.get(None, []):
        lines.extend(
            _container_component(
                container,
                children.get(container.container_id, []),
                by_parent,
                base_url=base_url,
                indent=indent,
                depth=1,
            )
        )
    for record in by_parent.get(None, []):
        lines.extend(_record_component(record, base_url=base_url, indent=indent, depth=1))
    return lines
