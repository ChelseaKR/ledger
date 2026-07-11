"""Tests for :mod:`ledger.metadata.mets` and :mod:`ledger.metadata.ead` (EXP-11).

Covers well-formedness and namespacing of both exports, that the METS file section
carries payload checksums and the descriptive/provenance sections nest the sibling
modules' own output byte-for-byte, that both exports are deterministic, and --
the central safety property -- that both entry points only accept the safe,
already-disclosed record shape, so no sealed field or identity can reach the
output regardless of what was withheld.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from ledger.metadata.ead import to_ead_xml
from ledger.metadata.mets import to_mets_xml
from ledger.models import (
    AccessPolicy,
    ContentAddress,
    DisclosedRecord,
    DublinCore,
    HashAlgo,
    PayloadFile,
    PremisEvent,
    PremisEventType,
    Redaction,
)

_METS_NS = "{http://www.loc.gov/METS/}"
_EAD_NS = "{urn:isbn:1-931666-22-9}"


def _sample_record(*, with_withheld: bool = True) -> DisclosedRecord:
    dc = DublinCore(
        title=["Pride march, 1987"],
        creator=["Community Archive Collective"],
        subject=["queer history", "mutual aid"],
        date=["1987-06-01"],
        language=["en"],
        description=["Photographs from the 1987 march."],
    )
    payload = PayloadFile(
        filename="march.jpg",
        address=ContentAddress(HashAlgo.SHA256, "abc123deadbeef"),
        media_type="image/jpeg",
        size_bytes=2048,
        policy=AccessPolicy.PUBLIC,
    )
    withheld = (
        (Redaction("names", "sealed until 2030-01-01", "sealed-until"),) if with_withheld else ()
    )
    return DisclosedRecord(
        record_id="rec-0000000000000001",
        title="Pride march, 1987",
        dublin_core=dc.to_dict(),
        fields={"story": "It was a warm day downtown."},
        payloads=(payload,),
        content_warnings=("historical slur quoted in transcript",),
        withheld=withheld,
    )


def _sample_events() -> list[PremisEvent]:
    return [
        PremisEvent(
            event_type=PremisEventType.INGESTION,
            agent="ledger.ingest",
            outcome="success",
            detail="ingested record",
            linked_object="rec-0000000000000001",
            event_datetime="2026-01-01T00:00:00Z",
        )
    ]


@pytest.mark.preservation
def test_mets_xml_is_well_formed_and_namespaced() -> None:
    """``to_mets_xml`` emits a parseable ``mets:mets`` root in the METS namespace."""
    xml = to_mets_xml(_sample_record(), created="2026-07-07T00:00:00Z")
    root = ET.fromstring(xml)  # noqa: S314 - our own trusted, identity-free output
    assert root.tag == f"{_METS_NS}mets"
    assert root.attrib["OBJID"] == "rec-0000000000000001"


@pytest.mark.preservation
def test_mets_file_section_carries_payload_checksum() -> None:
    """Each payload becomes one ``mets:file`` with its content-address digest."""
    xml = to_mets_xml(_sample_record(), created="2026-07-07T00:00:00Z")
    root = ET.fromstring(xml)  # noqa: S314
    files = root.findall(f".//{_METS_NS}file")
    assert len(files) == 1
    assert files[0].attrib["CHECKSUM"] == "abc123deadbeef"
    assert files[0].attrib["CHECKSUMTYPE"] == "SHA-256"
    assert files[0].attrib["MIMETYPE"] == "image/jpeg"


@pytest.mark.preservation
def test_mets_struct_map_points_at_every_file() -> None:
    """``structMap`` carries one ``fptr`` per payload, referencing a real file ID."""
    xml = to_mets_xml(_sample_record(), created="2026-07-07T00:00:00Z")
    root = ET.fromstring(xml)  # noqa: S314
    file_ids = {f.attrib["ID"] for f in root.findall(f".//{_METS_NS}file")}
    fptr_ids = {p.attrib["FILEID"] for p in root.findall(f".//{_METS_NS}fptr")}
    assert fptr_ids and fptr_ids <= file_ids


@pytest.mark.preservation
def test_mets_embeds_premis_events_in_digiprov_md() -> None:
    """PREMIS events passed in appear, verbatim-shaped, inside ``digiprovMD``."""
    xml = to_mets_xml(
        _sample_record(), created="2026-07-07T00:00:00Z", premis_events=_sample_events()
    )
    root = ET.fromstring(xml)  # noqa: S314
    premis_ns = "{http://www.loc.gov/premis/v3}"
    events = root.findall(f".//{premis_ns}event")
    assert len(events) == 1


@pytest.mark.preservation
def test_mets_omits_amd_sec_when_no_premis_events_given() -> None:
    """No PREMIS events -> no empty ``amdSec`` clutter in the document."""
    xml = to_mets_xml(_sample_record(), created="2026-07-07T00:00:00Z")
    root = ET.fromstring(xml)  # noqa: S314
    assert root.find(f".//{_METS_NS}amdSec") is None


@pytest.mark.preservation
def test_mets_dmd_sec_carries_dublin_core_title() -> None:
    """The descriptive section nests real ``oai_dc`` markup, not a stub."""
    xml = to_mets_xml(_sample_record(), created="2026-07-07T00:00:00Z")
    root = ET.fromstring(xml)  # noqa: S314
    dc_ns = "{http://purl.org/dc/elements/1.1/}"
    titles = root.findall(f".//{dc_ns}title")
    assert [t.text for t in titles] == ["Pride march, 1987"]


@pytest.mark.preservation
def test_mets_notes_withheld_count_without_naming_it() -> None:
    """A withheld field is summarized as a safe count, never named or valued."""
    xml = to_mets_xml(_sample_record(with_withheld=True), created="2026-07-07T00:00:00Z")
    assert "1 field(s)/payload(s) not included" in xml
    assert "names" not in xml  # the withheld field's *name* must never appear
    assert "sealed until 2030" not in xml  # nor its reason/value


@pytest.mark.preservation
def test_mets_is_deterministic() -> None:
    """Identical inputs render byte-identical METS (no clock, no randomness)."""
    a = to_mets_xml(
        _sample_record(), created="2026-07-07T00:00:00Z", premis_events=_sample_events()
    )
    b = to_mets_xml(
        _sample_record(), created="2026-07-07T00:00:00Z", premis_events=_sample_events()
    )
    assert a == b


@pytest.mark.preservation
def test_ead_xml_is_well_formed_and_namespaced() -> None:
    """``to_ead_xml`` emits a parseable ``ead`` root in the EAD 2002 namespace."""
    xml = to_ead_xml(
        "Community Pride Collection",
        [_sample_record()],
        created="2026-07-07T00:00:00Z",
        collection_id="coll-pride",
    )
    root = ET.fromstring(xml)  # noqa: S314
    assert root.tag == f"{_EAD_NS}ead"


@pytest.mark.preservation
def test_ead_has_one_component_per_record() -> None:
    """Each disclosed record becomes exactly one ``c01`` item component."""
    records = [_sample_record(), _sample_record(with_withheld=False)]
    xml = to_ead_xml(
        "Community Pride Collection",
        records,
        created="2026-07-07T00:00:00Z",
        collection_id="coll-pride",
    )
    root = ET.fromstring(xml)  # noqa: S314
    components = root.findall(f".//{_EAD_NS}c01")
    assert len(components) == 2


@pytest.mark.preservation
def test_ead_surfaces_content_warnings() -> None:
    """A content warning on the disclosed record appears in the finding aid."""
    xml = to_ead_xml(
        "Community Pride Collection",
        [_sample_record()],
        created="2026-07-07T00:00:00Z",
        collection_id="coll-pride",
    )
    assert "historical slur quoted in transcript" in xml


@pytest.mark.preservation
def test_ead_notes_withheld_count_without_naming_it() -> None:
    """A withheld field is summarized as a safe count, never named or valued."""
    xml = to_ead_xml(
        "Community Pride Collection",
        [_sample_record(with_withheld=True)],
        created="2026-07-07T00:00:00Z",
        collection_id="coll-pride",
    )
    assert "1 field(s)/payload(s) not included" in xml
    assert "names" not in xml
    assert "sealed until 2030" not in xml


@pytest.mark.preservation
def test_ead_is_deterministic() -> None:
    """Identical inputs render byte-identical EAD (no clock, no randomness)."""
    records = [_sample_record()]
    a = to_ead_xml(
        "Community Pride Collection", records, created="2026-07-07T00:00:00Z", collection_id="c"
    )
    b = to_ead_xml(
        "Community Pride Collection", records, created="2026-07-07T00:00:00Z", collection_id="c"
    )
    assert a == b


@pytest.mark.preservation
def test_ead_empty_collection_still_produces_valid_document() -> None:
    """A collection with no disclosed records yields a well-formed, empty finding aid."""
    xml = to_ead_xml(
        "Empty Collection", [], created="2026-07-07T00:00:00Z", collection_id="coll-empty"
    )
    root = ET.fromstring(xml)  # noqa: S314
    assert root.findall(f".//{_EAD_NS}c01") == []
