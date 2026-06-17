"""Tests for :mod:`ledger.oai` — the OAI-PMH provider and the sitemap.

These exercise the harvest surface (user research P2-3): that ``Identify`` and the
list/get verbs are well-formed and carry the public records they are given, that an
unknown verb or a missing identifier degrades to the documented OAI error codes
(returned with HTTP 200 per the OAI convention), and that the sitemap lists every
public record URL. Determinism: a fixed ``now`` is passed throughout so the emitted
XML is byte-stable and assertions hold across machines and runs.

No-outing: every record handed to :func:`oai_response` here is already a
:class:`~ledger.models.DisclosedRecord` (the public shape, which cannot carry an
``identity_ref``), mirroring the contract that the caller passes only the set it has
disclosed to the anonymous public grant.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from ledger.models import DisclosedRecord

NOW = "2026-06-16T00:00:00Z"
BASE_URL = "https://archive.example/oai"
ARCHIVE_NAME = "Community Archive"
ADMIN_EMAIL = "steward@archive.example"

_OAI_NS = "{http://www.openarchives.org/OAI/2.0/}"
_DC_NS = "{http://purl.org/dc/elements/1.1/}"
_SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


def _public_record(
    record_id: str = "rec-0000000000000000",
    title: str = "Pride march, 1987",
    date: str = "1987-06-28",
) -> DisclosedRecord:
    """A public :class:`DisclosedRecord` with collection-level Dublin Core only.

    It carries no ``identity_ref`` (the shape structurally forbids one) and no
    sealed value — exactly what a caller would pass after disclosing to the public.
    """
    return DisclosedRecord(
        record_id=record_id,
        title=title,
        dublin_core={
            "title": [title],
            "creator": ["Community Archive Collective"],
            "subject": ["queer history", "mutual aid"],
            "date": [date],
            "language": ["en"],
            "rights": ["CC-BY-SA-4.0"],
        },
        fields={"story": "the public account"},
        payloads=(),
        content_warnings=(),
        withheld=(),
    )


def _call(verb: str, params: dict[str, str], records: list[DisclosedRecord]) -> tuple[int, str]:
    """Invoke :func:`oai_response` with the shared fixed parameters."""
    from ledger.oai import oai_response

    return oai_response(
        verb,
        params,
        records=records,
        archive_name=ARCHIVE_NAME,
        base_url=BASE_URL,
        admin_email=ADMIN_EMAIL,
        now=NOW,
    )


def _parse(xml: str) -> ET.Element:
    """Parse trusted, identity-free provider output, failing the test if malformed."""
    return ET.fromstring(xml)  # noqa: S314 - our own trusted output


def test_identify_is_well_formed() -> None:
    """``Identify`` returns HTTP 200 and a parseable OAI envelope naming the archive."""
    status, xml = _call("Identify", {}, [])
    assert status == 200
    root = _parse(xml)
    assert root.tag == f"{_OAI_NS}OAI-PMH"
    identify = root.find(f"{_OAI_NS}Identify")
    assert identify is not None
    name = identify.find(f"{_OAI_NS}repositoryName")
    assert name is not None and name.text == ARCHIVE_NAME
    version = identify.find(f"{_OAI_NS}protocolVersion")
    assert version is not None and version.text == "2.0"


def test_list_metadata_formats_offers_oai_dc() -> None:
    """``ListMetadataFormats`` advertises exactly the ``oai_dc`` prefix."""
    status, xml = _call("ListMetadataFormats", {}, [])
    assert status == 200
    root = _parse(xml)
    prefixes = [e.text for e in root.iter(f"{_OAI_NS}metadataPrefix")]
    assert prefixes == ["oai_dc"]


def test_list_records_contains_known_id_and_title() -> None:
    """``ListRecords`` emits a record whose header id and ``dc:title`` are present."""
    record = _public_record()
    status, xml = _call("ListRecords", {"metadataPrefix": "oai_dc"}, [record])
    assert status == 200
    root = _parse(xml)

    identifiers = [e.text for e in root.iter(f"{_OAI_NS}identifier")]
    assert record.record_id in identifiers

    titles = [e.text for e in root.iter(f"{_DC_NS}title")]
    assert record.title in titles


def test_list_identifiers_emits_headers_only() -> None:
    """``ListIdentifiers`` lists each record's header but no ``<metadata>`` block."""
    records = [_public_record("rec-a"), _public_record("rec-b", title="Second")]
    status, xml = _call("ListIdentifiers", {"metadataPrefix": "oai_dc"}, records)
    assert status == 200
    root = _parse(xml)
    identifiers = sorted(e.text or "" for e in root.iter(f"{_OAI_NS}identifier"))
    assert identifiers == ["rec-a", "rec-b"]
    assert root.find(f".//{_OAI_NS}metadata") is None


def test_get_record_returns_requested_record() -> None:
    """``GetRecord`` for a present id returns that single record with its metadata."""
    record = _public_record()
    status, xml = _call(
        "GetRecord",
        {"metadataPrefix": "oai_dc", "identifier": record.record_id},
        [record],
    )
    assert status == 200
    root = _parse(xml)
    ids = [e.text for e in root.iter(f"{_OAI_NS}identifier")]
    assert ids == [record.record_id]
    titles = [e.text for e in root.iter(f"{_DC_NS}title")]
    assert titles == [record.title]


def test_get_record_missing_id_returns_id_does_not_exist() -> None:
    """``GetRecord`` for an absent id returns an ``idDoesNotExist`` error at HTTP 200."""
    status, xml = _call(
        "GetRecord",
        {"metadataPrefix": "oai_dc", "identifier": "does-not-exist"},
        [_public_record()],
    )
    assert status == 200
    root = _parse(xml)
    error = root.find(f"{_OAI_NS}error")
    assert error is not None
    assert error.get("code") == "idDoesNotExist"
    # No-outing: the error must not echo the requested identifier as a value.
    assert "does-not-exist" not in (error.text or "")


def test_get_record_without_identifier_is_bad_argument() -> None:
    """``GetRecord`` with no ``identifier`` argument is ``badArgument``."""
    status, xml = _call("GetRecord", {"metadataPrefix": "oai_dc"}, [_public_record()])
    assert status == 200
    error = _parse(xml).find(f"{_OAI_NS}error")
    assert error is not None and error.get("code") == "badArgument"


def test_bad_verb_returns_error_at_http_200() -> None:
    """An unknown verb returns a ``badVerb`` error document at HTTP 200."""
    status, xml = _call("Frobnicate", {}, [_public_record()])
    assert status == 200
    root = _parse(xml)
    error = root.find(f"{_OAI_NS}error")
    assert error is not None and error.get("code") == "badVerb"
    # A badVerb response must not echo the bad verb as a valid <request> attribute.
    request = root.find(f"{_OAI_NS}request")
    assert request is not None and request.get("verb") is None


def test_missing_verb_returns_bad_verb() -> None:
    """An empty verb is treated as ``badVerb`` (illegal or missing verb)."""
    status, xml = _call("", {}, [])
    assert status == 200
    error = _parse(xml).find(f"{_OAI_NS}error")
    assert error is not None and error.get("code") == "badVerb"


def test_list_records_wrong_prefix_is_cannot_disseminate_format() -> None:
    """A metadataPrefix other than ``oai_dc`` is ``cannotDisseminateFormat``."""
    status, xml = _call("ListRecords", {"metadataPrefix": "marcxml"}, [_public_record()])
    assert status == 200
    error = _parse(xml).find(f"{_OAI_NS}error")
    assert error is not None and error.get("code") == "cannotDisseminateFormat"


def test_record_without_date_falls_back_to_now() -> None:
    """A record with no Dublin Core ``date`` uses ``now`` as its datestamp."""
    record = DisclosedRecord(
        record_id="rec-nodate",
        title="No date",
        dublin_core={"title": ["No date"]},
        fields={},
        payloads=(),
        content_warnings=(),
        withheld=(),
    )
    status, xml = _call("ListIdentifiers", {"metadataPrefix": "oai_dc"}, [record])
    assert status == 200
    root = _parse(xml)
    datestamps = [e.text for e in root.iter(f"{_OAI_NS}datestamp")]
    assert datestamps == [NOW]


def test_special_characters_are_escaped_and_well_formed() -> None:
    """A title with XML metacharacters round-trips through escaping intact."""
    record = _public_record(title='Ada & Bell <they/them> "quote"')
    status, xml = _call("ListRecords", {"metadataPrefix": "oai_dc"}, [record])
    assert status == 200
    assert "<title>Ada & Bell" not in xml  # raw, unescaped form must not appear
    root = _parse(xml)
    titles = [e.text for e in root.iter(f"{_DC_NS}title")]
    assert titles == ['Ada & Bell <they/them> "quote"']


def test_sitemap_contains_record_urls() -> None:
    """The sitemap lists ``base_url + /record/ + id`` for each public record."""
    from ledger.oai import sitemap_xml

    ids = ["rec-a", "rec-b"]
    xml = sitemap_xml(ids, "https://archive.example/")
    root = _parse(xml)
    assert root.tag == f"{_SITEMAP_NS}urlset"
    locs = [e.text for e in root.iter(f"{_SITEMAP_NS}loc")]
    assert locs == [
        "https://archive.example/record/rec-a",
        "https://archive.example/record/rec-b",
    ]


def test_sitemap_is_well_formed_when_empty() -> None:
    """An empty sitemap is still a parseable, empty ``<urlset>``."""
    from ledger.oai import sitemap_xml

    root = _parse(sitemap_xml([], "https://archive.example"))
    assert root.tag == f"{_SITEMAP_NS}urlset"
    assert list(root) == []
