"""Tests for :mod:`ledger.metadata` — PREMIS event log and Dublin Core.

Covers the PREMIS log JSON round-trip, its append-only / tamper-evident behaviour,
deterministic serialization and on-disk read/write, minimal PREMIS XML emission, and
the Dublin Core JSON and ``oai_dc`` XML round-trips. The deterministic checks lean on
``canonical_json`` so a record's metadata hashes the same on every machine.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ledger.metadata.dublincore import (
    from_json,
    read_sidecar,
    to_json,
    to_oai_dc_xml,
    write_sidecar,
)
from ledger.metadata.premis import PremisLog, to_premis_xml
from ledger.models import DublinCore, PremisEvent, PremisEventType


def _sample_events() -> list[PremisEvent]:
    """Two opaque, identity-free preservation events with fixed timestamps."""
    return [
        PremisEvent(
            event_type=PremisEventType.INGESTION,
            agent="ledger.ingest",
            outcome="success",
            detail="ingested record",
            linked_object="rec-0000000000000000",
            event_datetime="2026-01-01T00:00:00Z",
        ),
        PremisEvent(
            event_type=PremisEventType.FIXITY_CHECK,
            agent="ledger.fixity",
            outcome="success",
            detail="all files matched",
            linked_object="sha256:abc",
            event_datetime="2026-01-02T00:00:00Z",
        ),
    ]


@pytest.mark.preservation
def test_premis_log_json_round_trip() -> None:
    """A log serialized to JSON and back yields equal events in the same order."""
    log = PremisLog(_sample_events())
    restored = PremisLog.from_json(log.to_json())
    assert restored.events == log.events


@pytest.mark.preservation
def test_premis_log_is_deterministic() -> None:
    """Identical history serializes to a byte-identical JSON string."""
    a = PremisLog(_sample_events()).to_json()
    b = PremisLog(_sample_events()).to_json()
    assert a == b


@pytest.mark.preservation
def test_premis_log_is_append_only() -> None:
    """``record`` appends; ``events`` hands back a copy that cannot mutate the log.

    Auditability: there is no public mutator that edits or removes a past event, so
    the on-disk and in-memory history stays tamper-evident.
    """
    log = PremisLog()
    events = _sample_events()
    log.record(events[0])
    log.record(events[1])
    assert log.events == events

    snapshot = log.events
    snapshot.clear()  # mutating the returned copy must not touch the log
    snapshot.append(events[0])
    assert log.events == events


@pytest.mark.preservation
def test_premis_log_write_read_round_trip(tmp_path: Path) -> None:
    """A log written to disk reads back with identical events."""
    log = PremisLog(_sample_events())
    path = tmp_path / "events.json"
    log.write(path)
    restored = PremisLog.read(path)
    assert restored.events == log.events


@pytest.mark.preservation
def test_premis_log_optional_linked_object_round_trips() -> None:
    """An event with no ``linked_object`` survives the JSON round-trip as None."""
    event = PremisEvent(
        event_type=PremisEventType.VALIDATION,
        agent="ledger.bag",
        outcome="success",
        detail="bag validated",
        linked_object=None,
        event_datetime="2026-01-03T00:00:00Z",
    )
    restored = PremisLog.from_json(PremisLog([event]).to_json())
    assert restored.events[0].linked_object is None


@pytest.mark.preservation
def test_premis_xml_is_well_formed_and_namespaced() -> None:
    """``to_premis_xml`` emits parseable, PREMIS-namespaced markup with one event each."""
    import xml.etree.ElementTree as ET

    xml = to_premis_xml(_sample_events())
    root = ET.fromstring(xml)  # noqa: S314 - our own trusted, identity-free output
    ns = "{http://www.loc.gov/premis/v3}"
    assert root.tag == f"{ns}premis"
    assert len(root.findall(f"{ns}event")) == 2


@pytest.mark.preservation
def test_dublin_core_json_round_trip() -> None:
    """Dublin Core serialized to JSON and back preserves every populated element."""
    dc = DublinCore(
        title=["Pride march, 1987"],
        creator=["Community Archive Collective"],
        subject=["queer history", "mutual aid"],
        language=["en"],
        rights=["CC-BY-SA-4.0"],
    )
    restored = from_json(to_json(dc))
    assert restored == dc


@pytest.mark.preservation
def test_dublin_core_json_drops_empty_elements() -> None:
    """Empty DC elements are absent from the JSON (compact, deterministic sidecar)."""
    import json

    dc = DublinCore(title=["Only a title"])
    parsed = json.loads(to_json(dc))
    assert parsed == {"title": ["Only a title"]}


@pytest.mark.preservation
def test_dublin_core_sidecar_round_trip(tmp_path: Path) -> None:
    """A DC sidecar written to disk reads back equal."""
    dc = DublinCore(title=["A title"], subject=["history"])
    path = tmp_path / "dc.json"
    write_sidecar(dc, path)
    assert read_sidecar(path) == dc


@pytest.mark.preservation
def test_oai_dc_xml_is_well_formed_with_expected_elements() -> None:
    """``oai_dc`` XML parses and carries one ``dc:`` element per value."""
    import xml.etree.ElementTree as ET

    dc = DublinCore(title=["A title"], subject=["history", "mutual aid"])
    xml = to_oai_dc_xml(dc)
    root = ET.fromstring(xml)  # noqa: S314 - our own trusted, identity-free output
    dc_ns = "{http://purl.org/dc/elements/1.1/}"
    titles = root.findall(f"{dc_ns}title")
    subjects = root.findall(f"{dc_ns}subject")
    assert [e.text for e in titles] == ["A title"]
    assert [e.text for e in subjects] == ["history", "mutual aid"]


@pytest.mark.preservation
def test_dublin_core_from_json_rejects_non_list_element() -> None:
    """A scalar where a list is expected is rejected (robustness)."""
    with pytest.raises(ValueError, match="must be a list"):
        from_json('{"title": "not a list"}')
