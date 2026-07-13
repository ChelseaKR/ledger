"""A minimal OAI-PMH 2.0 provider and a sitemap, for harvest/interoperability.

User research P2-3 asked for the archive to be *harvestable* by general-purpose
aggregators and search engines, so a community's history is discoverable beyond
the archive's own browse UI. This module answers that with two standard surfaces:

* an OAI-PMH 2.0 provider (:func:`oai_response`) supporting the ``oai_dc`` metadata
  format and the verbs ``Identify``, ``ListMetadataFormats``, ``ListIdentifiers``,
  ``ListRecords``, and ``GetRecord``; and
* an XML sitemap (:func:`sitemap_xml`) of public record URLs; and
* an Atom 1.0 feed (:func:`atom_feed_xml`) of the most recent public records, so a
  reader or aggregator can *follow* the collection as it grows.

The single most important property here is the *no-outing rule*. This module never
opens the vault, never resolves an ``identity_ref``, and never selects records. The
caller hands it a ``Sequence[DisclosedRecord]`` that it has *already* disclosed to
the anonymous public grant; this module only re-serializes those public records.
Because a :class:`~ledger.models.DisclosedRecord` structurally cannot carry an
``identity_ref`` (see :mod:`ledger.models`), and because the caller passes only the
public set, harvesting can never reveal sealed material or a contributor identity.

Standards-compliance/interoperability: the ``oai_dc`` payload and all XML escaping
are produced by :mod:`ledger.metadata.dublincore`, so the descriptive metadata in a
harvested record is byte-for-byte the same profile the rest of the archive emits.

Determinism: every entry point takes ``now`` (and the record ids) as parameters and
consults no clock or random source, so the same inputs yield byte-identical XML —
harvesters can compare ``responseDate``-free bodies and tests can assert on output.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime

from ledger.metadata.dublincore import escape, to_oai_dc_xml
from ledger.models import DisclosedRecord, DublinCore

__all__ = [
    "OAI_DC_PREFIX",
    "atom_feed_xml",
    "oai_response",
    "sitemap_xml",
]

# The one metadata format this provider supports. ``oai_dc`` is mandatory for any
# conforming OAI-PMH repository, so a single supported format is enough to be
# harvestable by any aggregator (standards-compliance).
OAI_DC_PREFIX = "oai_dc"

_OAI_PMH_NS = "http://www.openarchives.org/OAI/2.0/"
_XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
_OAI_PMH_SCHEMA = "http://www.openarchives.org/OAI/2.0/OAI-PMH.xsd"

_SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"

_OAI_DC_FORMAT_NS = "http://www.openarchives.org/OAI/2.0/oai_dc/"
_OAI_DC_SCHEMA = "http://www.openarchives.org/OAI/2.0/oai_dc.xsd"

# The verbs this provider implements. A verb outside this set is ``badVerb``.
_SUPPORTED_VERBS: frozenset[str] = frozenset(
    {
        "Identify",
        "ListMetadataFormats",
        "ListIdentifiers",
        "ListRecords",
        "GetRecord",
    }
)


def oai_response(
    verb: str,
    params: dict[str, str],
    *,
    records: Sequence[DisclosedRecord],
    archive_name: str,
    base_url: str,
    admin_email: str,
    now: str,
) -> tuple[int, str]:
    """Dispatch an OAI-PMH verb and return ``(http_status, xml_text)``.

    ``records`` is the set the caller has already disclosed to the anonymous public
    grant; this function never widens that set, so it cannot reveal sealed material
    (no-outing rule). ``now`` is the response timestamp *and* the fallback datestamp
    for a record without a Dublin Core ``date`` — passed in for determinism.

    OAI-PMH convention: even an *error* response is a well-formed OAI document
    returned with HTTP status ``200``; the failure is carried by an ``<error>``
    element whose ``code`` attribute the harvester reads (e.g. ``badVerb``,
    ``badArgument``, ``idDoesNotExist``). Only genuinely supported verbs produce a
    success body; everything else maps to one of those documented error codes.
    """
    request_attr = _request_attrs(verb, params)

    if verb not in _SUPPORTED_VERBS:
        # An empty/missing verb and an unrecognised verb are both ``badVerb`` per the
        # OAI-PMH spec; the request element omits the verb attribute in this case.
        return _error_response(
            base_url,
            now,
            {},
            "badVerb",
            "Illegal or missing OAI-PMH verb.",
        )

    if verb == "Identify":
        return _identify(base_url, now, request_attr, archive_name, admin_email)
    if verb == "ListMetadataFormats":
        return _list_metadata_formats(base_url, now, request_attr)
    if verb == "ListIdentifiers":
        return _list_identifiers(base_url, now, request_attr, params, records)
    if verb == "ListRecords":
        return _list_records(base_url, now, request_attr, params, records)
    # verb == "GetRecord" (the only remaining supported verb)
    return _get_record(base_url, now, request_attr, params, records)


def sitemap_xml(record_ids: Sequence[str], base_url: str) -> str:
    """Render a sitemap ``<urlset>`` of public record pages.

    Discoverability: the browse root (where the Atom feed is auto-discovered) plus
    one ``<url><loc>`` per public record, each ``base_url + "/record/" + id``, so a
    general-purpose crawler can find the collection and every publicly disclosed
    record. ``record_ids`` is the caller's public set; this function adds nothing of
    its own beyond the root. All locations are XML-escaped.
    """
    root = base_url.rstrip("/")
    lines = ['<?xml version="1.0" encoding="UTF-8"?>']
    lines.append(f'<urlset xmlns="{_SITEMAP_NS}">')
    lines.append(f"  <url><loc>{escape(root + '/')}</loc></url>")
    for record_id in record_ids:
        loc = escape(f"{root}/record/{record_id}")
        lines.append(f"  <url><loc>{loc}</loc></url>")
    lines.append("</urlset>")
    return "\n".join(lines)


_ATOM_NS = "http://www.w3.org/2005/Atom"


def _parse_atom_datetime(value: str) -> datetime | None:
    """Parse an accepted Dublin Core date shape into an aware UTC datetime."""
    text = value.strip()
    date_match = re.fullmatch(r"(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?", text)
    if date_match is not None:
        year, month, day = date_match.groups()
        try:
            return datetime(int(year), int(month or 1), int(day or 1), tzinfo=UTC)
        except ValueError:
            return None

    # ``datetime.fromisoformat`` validates calendar/time fields and understands
    # RFC 3339 offsets. A timezone-less timestamp is interpreted as UTC for
    # backward compatibility with contributor-entered ISO timestamps.
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _format_atom_datetime(value: datetime) -> str:
    """Normalize an aware datetime to a UTC RFC 3339 string."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _atom_datetime(value: str, fallback: str) -> datetime:
    """Coerce a Dublin Core date into the aware instant Atom ordering needs."""
    parsed = _parse_atom_datetime(value)
    if parsed is None:
        parsed = _parse_atom_datetime(fallback)
    # Callers supply a valid RFC 3339 generation timestamp. Keep malformed
    # programmatic inputs deterministic if both values are bad.
    return parsed if parsed is not None else datetime(1970, 1, 1, tzinfo=UTC)


def _atom_timestamp(value: str, fallback: str) -> str:
    """Coerce a Dublin Core date into an RFC 3339 instant Atom's ``<updated>`` needs.

    A record's ``dc:date`` may be a full timestamp, a ``YYYY-MM-DD``/``YYYY-MM``/
    ``YYYY`` (the minimum-metadata backfill yields a bare year), or free text. Atom
    requires a complete date-time, so a date-only value is widened to midnight UTC
    and anything unparseable falls back to ``fallback`` (the feed's generation time,
    already RFC 3339) rather than emitting an invalid feed."""
    return _format_atom_datetime(_atom_datetime(value, fallback))


def atom_feed_xml(
    records: Sequence[DisclosedRecord],
    *,
    archive_name: str,
    base_url: str,
    now: str,
    limit: int = 50,
) -> str:
    """Render an Atom 1.0 feed of the most recent public records.

    A feed lets a researcher or partner organisation *follow* a collection as it
    grows, instead of re-checking the browse page. Like the OAI provider and the
    sitemap, it re-serializes only the ``DisclosedRecord`` set the caller already
    disclosed to the anonymous public, so it can never surface sealed material or a
    contributor identity (no-outing rule). The only ``<author>`` is the *archive*
    itself (a collection, never a person), satisfying Atom without naming anyone.

    Entries are ordered newest first by their Dublin Core date (``record_id`` breaks
    ties for a deterministic feed) and capped at ``limit``. Every value is XML-escaped
    through the shared :func:`escape` boundary. ``now`` is the feed's generation time
    and the fallback timestamp, so output is deterministic for a given input.

    The sort key is the same aware instant :func:`_atom_datetime` widens each
    entry's date to for display (not the raw ``dc:date`` string): a ``dc:date`` is
    free text of varying granularity and padding (``"1994"``, ``"2021-5-1"``,
    ``"2021-12-01"``, ...), and comparing those as plain strings is lexicographic,
    not chronological — e.g. ``"2021-5-1" > "2021-12-01"`` as strings even though May
    precedes December. Parsing both as aware UTC datetimes before comparing keeps
    "most recent first" true for any date shape or fractional precision in use,
    and keeps the sort key consistent with what ``<updated>`` actually displays."""
    root = base_url.rstrip("/")
    ordered = sorted(
        records,
        key=lambda r: (_atom_datetime(_datestamp(r, now), now), r.record_id),
        reverse=True,
    )[: max(0, limit)]

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<feed xmlns="{_ATOM_NS}">',
        f"  <title>{escape(archive_name)}</title>",
        "  <subtitle>Recently published records</subtitle>",
        f"  <id>{escape(root + '/')}</id>",
        f'  <link rel="self" href="{escape(root + "/feed.atom")}"/>',
        f'  <link rel="alternate" type="text/html" href="{escape(root + "/")}"/>',
        f"  <updated>{escape(_atom_timestamp(now, now))}</updated>",
        f"  <author><name>{escape(archive_name)}</name></author>",
    ]
    for record in ordered:
        url = f"{root}/record/{record.record_id}"
        updated = escape(_atom_timestamp(_datestamp(record, now), now))
        lines.append("  <entry>")
        lines.append(f"    <title>{escape(record.title)}</title>")
        lines.append(f"    <id>{escape(url)}</id>")
        lines.append(f'    <link rel="alternate" type="text/html" href="{escape(url)}"/>')
        lines.append(f"    <updated>{updated}</updated>")
        descriptions = record.dublin_core.get("description") or []
        if descriptions and descriptions[0]:
            lines.append(f'    <summary type="text">{escape(descriptions[0])}</summary>')
        lines.append("  </entry>")
    lines.append("</feed>")
    return "\n".join(lines)


# --- verb handlers ----------------------------------------------------------


def _identify(
    base_url: str,
    now: str,
    request_attr: str,
    archive_name: str,
    admin_email: str,
) -> tuple[int, str]:
    """The ``Identify`` response: who this repository is and how to reach it."""
    body = [
        "  <Identify>",
        f"    <repositoryName>{escape(archive_name)}</repositoryName>",
        f"    <baseURL>{escape(base_url)}</baseURL>",
        "    <protocolVersion>2.0</protocolVersion>",
        f"    <adminEmail>{escape(admin_email)}</adminEmail>",
        f"    <earliestDatestamp>{escape(now)}</earliestDatestamp>",
        "    <deletedRecord>no</deletedRecord>",
        "    <granularity>YYYY-MM-DDThh:mm:ssZ</granularity>",
        "  </Identify>",
    ]
    return 200, _envelope(base_url, now, request_attr, body)


def _list_metadata_formats(
    base_url: str,
    now: str,
    request_attr: str,
) -> tuple[int, str]:
    """The ``ListMetadataFormats`` response: this provider offers only ``oai_dc``."""
    body = [
        "  <ListMetadataFormats>",
        "    <metadataFormat>",
        f"      <metadataPrefix>{OAI_DC_PREFIX}</metadataPrefix>",
        f"      <schema>{escape(_OAI_DC_SCHEMA)}</schema>",
        f"      <metadataNamespace>{escape(_OAI_DC_FORMAT_NS)}</metadataNamespace>",
        "    </metadataFormat>",
        "  </ListMetadataFormats>",
    ]
    return 200, _envelope(base_url, now, request_attr, body)


def _list_identifiers(
    base_url: str,
    now: str,
    request_attr: str,
    params: dict[str, str],
    records: Sequence[DisclosedRecord],
) -> tuple[int, str]:
    """The ``ListIdentifiers`` response: a header per public record."""
    prefix_error = _check_metadata_prefix(base_url, now, params)
    if prefix_error is not None:
        return prefix_error
    if not records:
        return _error_response(
            base_url, now, params, "noRecordsMatch", "No records are available to harvest."
        )
    lines = ["  <ListIdentifiers>"]
    for record in records:
        lines.extend(_header_lines(record, now, indent="    "))
    lines.append("  </ListIdentifiers>")
    return 200, _envelope(base_url, now, request_attr, lines)


def _list_records(
    base_url: str,
    now: str,
    request_attr: str,
    params: dict[str, str],
    records: Sequence[DisclosedRecord],
) -> tuple[int, str]:
    """The ``ListRecords`` response: a full ``<record>`` per public record."""
    prefix_error = _check_metadata_prefix(base_url, now, params)
    if prefix_error is not None:
        return prefix_error
    if not records:
        return _error_response(
            base_url, now, params, "noRecordsMatch", "No records are available to harvest."
        )
    lines = ["  <ListRecords>"]
    for record in records:
        lines.extend(_record_lines(record, now, indent="    "))
    lines.append("  </ListRecords>")
    return 200, _envelope(base_url, now, request_attr, lines)


def _get_record(
    base_url: str,
    now: str,
    request_attr: str,
    params: dict[str, str],
    records: Sequence[DisclosedRecord],
) -> tuple[int, str]:
    """The ``GetRecord`` response: one record selected by its ``identifier``."""
    prefix_error = _check_metadata_prefix(base_url, now, params)
    if prefix_error is not None:
        return prefix_error
    identifier = params.get("identifier")
    if not identifier:
        return _error_response(
            base_url,
            now,
            params,
            "badArgument",
            "GetRecord requires an 'identifier' argument.",
        )
    match = next((r for r in records if r.record_id == identifier), None)
    if match is None:
        # The id is not in the public set: it may be sealed, unknown, or simply not
        # disclosed. We never distinguish these — every miss is the same
        # ``idDoesNotExist`` so the response cannot confirm a sealed record exists
        # (no-outing rule). The error message names no value, only the verb.
        return _error_response(
            base_url,
            now,
            params,
            "idDoesNotExist",
            "No public record has the requested identifier.",
        )
    lines = ["  <GetRecord>"]
    lines.extend(_record_lines(match, now, indent="    "))
    lines.append("  </GetRecord>")
    return 200, _envelope(base_url, now, request_attr, lines)


# --- record rendering -------------------------------------------------------


def _header_lines(record: DisclosedRecord, now: str, *, indent: str) -> list[str]:
    """The ``<header>`` block for one record: identifier + datestamp."""
    identifier = escape(record.record_id)
    datestamp = escape(_datestamp(record, now))
    return [
        f"{indent}<header>",
        f"{indent}  <identifier>{identifier}</identifier>",
        f"{indent}  <datestamp>{datestamp}</datestamp>",
        f"{indent}</header>",
    ]


def _record_lines(record: DisclosedRecord, now: str, *, indent: str) -> list[str]:
    """A full ``<record>``: ``<header>`` then ``<metadata>`` carrying ``oai_dc``."""
    lines = [f"{indent}<record>"]
    lines.extend(_header_lines(record, now, indent=f"{indent}  "))
    lines.append(f"{indent}  <metadata>")
    lines.extend(_indent_block(_oai_dc_payload(record), f"{indent}    "))
    lines.append(f"{indent}  </metadata>")
    lines.append(f"{indent}</record>")
    return lines


def _oai_dc_payload(record: DisclosedRecord) -> str:
    """Render the record's collection-level Dublin Core as an ``oai_dc:dc`` element.

    The descriptive metadata is reused verbatim from
    :func:`ledger.metadata.dublincore.to_oai_dc_xml` (so the harvested profile and
    its escaping match the rest of the archive); only the leading XML declaration is
    dropped because this payload is embedded inside the OAI envelope, which already
    carries one. ``DisclosedRecord.dublin_core`` is already identity-free.
    """
    dc = DublinCore.from_dict(record.dublin_core)
    xml = to_oai_dc_xml(dc)
    body_lines = [line for line in xml.splitlines() if not line.startswith("<?xml")]
    return "\n".join(body_lines)


def _datestamp(record: DisclosedRecord, now: str) -> str:
    """The OAI datestamp for a record: its first Dublin Core ``date`` or ``now``.

    Falling back to ``now`` (rather than omitting the element) keeps every
    ``<header>`` schema-valid even for a record that carries no date.
    """
    dates = record.dublin_core.get("date") or []
    if dates and dates[0]:
        return dates[0]
    return now


# --- envelope & errors ------------------------------------------------------


def _check_metadata_prefix(
    base_url: str,
    now: str,
    params: dict[str, str],
) -> tuple[int, str] | None:
    """Validate the ``metadataPrefix`` argument for the list/get verbs.

    A missing prefix is ``badArgument``; a prefix other than ``oai_dc`` is
    ``cannotDisseminateFormat`` (both documented OAI-PMH error codes). Returns the
    ready error response, or ``None`` when the prefix is acceptable.
    """
    prefix = params.get("metadataPrefix")
    if prefix is None:
        return _error_response(
            base_url,
            now,
            params,
            "badArgument",
            "A 'metadataPrefix' argument is required.",
        )
    if prefix != OAI_DC_PREFIX:
        return _error_response(
            base_url,
            now,
            params,
            "cannotDisseminateFormat",
            f"The only supported metadataPrefix is '{OAI_DC_PREFIX}'.",
        )
    return None


def _error_response(
    base_url: str,
    now: str,
    params: dict[str, str],
    code: str,
    message: str,
) -> tuple[int, str]:
    """A well-formed OAI-PMH error document, returned with HTTP 200 (OAI convention).

    The ``<request>`` element echoes the supplied arguments (so a harvester can see
    what it sent), and the ``<error>`` carries the machine-readable ``code``. Neither
    the code nor the message ever names a sealed value or identity (no-outing rule).
    """
    request_attr = _request_attrs(None, params)
    body = [f'  <error code="{escape(code)}">{escape(message)}</error>']
    return 200, _envelope(base_url, now, request_attr, body)


def _request_attrs(verb: str | None, params: dict[str, str]) -> str:
    """Build the attribute string for the ``<request>`` element.

    OAI-PMH echoes the request's arguments as attributes on ``<request>``. The verb
    is included only when it is one this provider recognises (a ``badVerb`` response
    must not echo the bad verb as a valid attribute). Attribute order is sorted for
    deterministic output. All attribute values are XML-escaped.
    """
    attrs: dict[str, str] = {}
    if verb is not None and verb in _SUPPORTED_VERBS:
        attrs["verb"] = verb
    for key in ("metadataPrefix", "identifier"):
        value = params.get(key)
        if value:
            attrs[key] = value
    if not attrs:
        return ""
    rendered = " ".join(f'{name}="{escape(attrs[name])}"' for name in sorted(attrs))
    return f" {rendered}"


def _envelope(base_url: str, now: str, request_attr: str, body: list[str]) -> str:
    """Wrap ``body`` lines in the standard OAI-PMH document envelope.

    The envelope carries the protocol namespaces, the ``responseDate`` (``now``),
    and the echoed ``<request>``. Everything inside ``body`` is already escaped by
    its producer; ``base_url`` is escaped here as ``<request>`` text content.
    """
    lines = ['<?xml version="1.0" encoding="UTF-8"?>']
    lines.append(
        "<OAI-PMH "
        f'xmlns="{_OAI_PMH_NS}" '
        f'xmlns:xsi="{_XSI_NS}" '
        f'xsi:schemaLocation="{_OAI_PMH_NS} {_OAI_PMH_SCHEMA}">'
    )
    lines.append(f"  <responseDate>{escape(now)}</responseDate>")
    lines.append(f"  <request{request_attr}>{escape(base_url)}</request>")
    lines.extend(body)
    lines.append("</OAI-PMH>")
    return "\n".join(lines)


def _indent_block(text: str, indent: str) -> list[str]:
    """Prefix every non-empty line of ``text`` with ``indent`` (for nesting)."""
    return [f"{indent}{line}" if line else line for line in text.splitlines()]
