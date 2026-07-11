"""Tests for :mod:`ledger.metadata` — PREMIS event log and Dublin Core.

Covers the PREMIS log JSON round-trip, its append-only / tamper-evident behaviour,
deterministic serialization and on-disk read/write, minimal PREMIS XML emission, and
the Dublin Core JSON and ``oai_dc`` XML round-trips. The deterministic checks lean on
``canonical_json`` so a record's metadata hashes the same on every machine.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ledger.chain import GENESIS_HASH
from ledger.metadata.dublincore import (
    from_json,
    read_sidecar,
    to_json,
    to_oai_dc_xml,
    write_sidecar,
)
from ledger.metadata.pid import ARK_PREFIX, is_ark, is_pid, mint_ark, mint_urn
from ledger.metadata.premis import PremisLog, to_premis_xml
from ledger.models import DublinCore, PremisEvent, PremisEventType, PremisRights


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


# --- FIX-06: hash-chained, tamper-evident PREMIS log ------------------------


@pytest.mark.preservation
def test_empty_premis_log_head_is_genesis() -> None:
    """A fresh log's head is the well-known genesis sentinel."""
    assert PremisLog().head == GENESIS_HASH


@pytest.mark.preservation
def test_premis_log_head_changes_as_events_are_recorded() -> None:
    """Each recorded event moves the chain head (it is not a static digest)."""
    log = PremisLog()
    heads = [log.head]
    for event in _sample_events():
        log.record(event)
        heads.append(log.head)
    assert len(set(heads)) == len(heads), "every head after a record() must be distinct"


@pytest.mark.preservation
def test_premis_log_verify_chain_ok_on_untouched_log() -> None:
    """A log built only through ``record`` verifies clean, end to end."""
    log = PremisLog()
    for event in _sample_events():
        log.record(event)
    result = log.verify_chain()
    assert result.ok
    assert result.broken_at is None
    assert result.head == log.head


@pytest.mark.preservation
def test_premis_log_chain_round_trips_through_json(tmp_path: Path) -> None:
    """Chain links survive a write/read round trip, and the head is unchanged."""
    log = PremisLog()
    for event in _sample_events():
        log.record(event)
    path = tmp_path / "premis.json"
    log.write(path)
    restored = PremisLog.read(path)
    assert restored.events == log.events
    assert restored.head == log.head
    assert restored.verify_chain().ok


@pytest.mark.preservation
def test_editing_a_premis_entry_on_disk_breaks_the_chain(tmp_path: Path) -> None:
    """Editing one recorded event's bytes directly on disk is caught on read.

    This is the raw-disk-attacker scenario FIX-06 exists for: nothing here goes
    through :meth:`PremisLog.record`, only a direct edit of the persisted JSON —
    exactly what "append-only" cannot itself prevent (threat model §4.4).
    """
    log = PremisLog()
    for event in _sample_events():
        log.record(event)
    path = tmp_path / "premis.json"
    log.write(path)

    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["entries"][0]["eventDetail"] = "rewritten after the fact"
    path.write_text(json.dumps(raw), encoding="utf-8")

    tampered = PremisLog.read(path)
    result = tampered.verify_chain()
    assert not result.ok
    assert result.broken_at is not None


@pytest.mark.preservation
def test_legacy_bare_array_premis_log_migrates_and_verifies() -> None:
    """A pre-FIX-06 log (a bare JSON array, no ``prevHash``) still loads and
    chain-verifies, adopted into the chained format from this read forward
    (evolvability — see the documented migration risk in the module docstring).
    """
    events = _sample_events()
    legacy_json = json.dumps([e.to_dict() for e in events])
    migrated = PremisLog.from_json(legacy_json)
    assert migrated.events == events
    assert migrated.verify_chain().ok
    assert migrated.head != GENESIS_HASH


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


# --- PREMIS rights entity (RM5) ---------------------------------------------


def _sample_rights() -> PremisRights:
    """A licence-basis rights statement with granted acts and a restriction."""
    return PremisRights(
        rights_basis="license",
        rights_note="CC-BY-SA-4.0",
        granted_acts=("disseminate", "replicate"),
        restrictions=("attribution required",),
        linked_object="rec-0000000000000000",
    )


@pytest.mark.preservation
def test_premis_rights_json_round_trips_in_log() -> None:
    """A log carrying a rights statement serializes and reads back with it intact."""
    log = PremisLog(_sample_events(), rights=_sample_rights())
    restored = PremisLog.from_json(log.to_json())
    assert restored.events == log.events
    assert restored.rights == _sample_rights()


@pytest.mark.preservation
def test_premis_log_without_rights_serializes_without_rights_key() -> None:
    """With no rights, the chained envelope (FIX-06) simply omits the rights key.

    Pre-chain logs were written as a bare JSON list; that shape (and the
    transitional ``{"events": ...}`` shape) is still *read* — see
    ``test_old_rightsless_log_still_reads_back`` — but everything written from
    FIX-06 on is the schema-versioned, hash-chained envelope.
    """
    log = PremisLog(_sample_events())
    parsed = json.loads(log.to_json())
    assert isinstance(parsed, dict)
    assert parsed["schemaVersion"] == 2
    assert "rights" not in parsed
    assert log.rights is None


@pytest.mark.preservation
def test_old_rightsless_log_still_reads_back() -> None:
    """An old log written as a bare event list reads back with no rights (round-trip)."""
    legacy = PremisLog(_sample_events()).to_json()  # the historical bare-list shape
    restored = PremisLog.from_json(legacy)
    assert restored.events == _sample_events()
    assert restored.rights is None


@pytest.mark.preservation
def test_set_rights_replaces_not_appends() -> None:
    """Rights are a standing fact: re-declaring supersedes rather than accumulating."""
    log = PremisLog(_sample_events())
    log.set_rights(PremisRights(rights_basis="other"))
    log.set_rights(_sample_rights())
    assert log.rights == _sample_rights()


@pytest.mark.preservation
def test_premis_xml_includes_rights_statement() -> None:
    """``to_premis_xml`` emits a PREMIS v3 rights statement with basis, act, restriction."""
    import xml.etree.ElementTree as ET

    xml = to_premis_xml(_sample_events(), _sample_rights())
    root = ET.fromstring(xml)  # noqa: S314 - our own trusted, identity-free output
    ns = "{http://www.loc.gov/premis/v3}"
    statements = root.findall(f"{ns}rights/{ns}rightsStatement")
    assert len(statements) == 1
    statement = statements[0]
    assert statement.findtext(f"{ns}rightsBasis") == "license"
    granted = statement.find(f"{ns}rightsGranted")
    assert granted is not None
    assert [a.text for a in granted.findall(f"{ns}act")] == ["disseminate", "replicate"]
    assert [r.text for r in granted.findall(f"{ns}restriction")] == ["attribution required"]


@pytest.mark.preservation
def test_premis_xml_without_rights_has_no_rights_element() -> None:
    """No rights argument means no ``premis:rights`` element (unchanged old output)."""
    import xml.etree.ElementTree as ET

    root = ET.fromstring(to_premis_xml(_sample_events()))  # noqa: S314 - trusted output
    ns = "{http://www.loc.gov/premis/v3}"
    assert root.findall(f"{ns}rights") == []


@pytest.mark.preservation
def test_premis_rights_carries_no_identity_fields() -> None:
    """No-outing: a rights statement has no rights-holder or agent field, only terms.

    Structural guarantee — the entity models the collection's *terms of use*, so any
    real person stays in the vault. If someone ever adds a ``rights_holder`` or
    ``agent`` field this test fails, flagging a no-outing regression.
    """
    fields = set(PremisRights.__dataclass_fields__)
    assert fields == {
        "rights_basis",
        "rights_note",
        "granted_acts",
        "restrictions",
        "linked_object",
    }


# --- persistent identifiers (RM5) -------------------------------------------


@pytest.mark.preservation
def test_mint_ark_is_deterministic_and_pure() -> None:
    """The same record id always mints the same ARK (offline, reproducible)."""
    assert mint_ark("rec-abc123") == mint_ark("rec-abc123")


@pytest.mark.preservation
def test_mint_ark_has_expected_form() -> None:
    """An ARK is ``ark:/<naan>/<shoulder><record_id>`` and passes :func:`is_ark`."""
    pid = mint_ark("deadbeef")
    assert pid.startswith(f"{ARK_PREFIX}/99999/")
    assert pid == "ark:/99999/ldeadbeef"
    assert is_ark(pid)


@pytest.mark.preservation
def test_mint_ark_distinct_ids_distinct_pids() -> None:
    """Distinct record ids mint distinct PIDs (no collisions for distinct inputs)."""
    assert mint_ark("rec-a") != mint_ark("rec-b")


@pytest.mark.preservation
def test_mint_ark_custom_naan_and_shoulder() -> None:
    """A deployment can supply its own registered NAAN and shoulder."""
    pid = mint_ark("x9", naan="12345", shoulder="k2")
    assert pid == "ark:/12345/k2x9"
    assert is_ark(pid)


@pytest.mark.preservation
def test_mint_ark_strips_unsafe_characters() -> None:
    """Reserved/whitespace characters in a crafted id cannot break the ARK structure."""
    pid = mint_ark("a/b c?d")
    assert pid == "ark:/99999/labcd"
    assert is_ark(pid)


@pytest.mark.preservation
def test_mint_ark_rejects_empty_id() -> None:
    """An empty (or all-unsafe) record id has no meaningful PID — fail closed."""
    with pytest.raises(ValueError, match="empty record id"):
        mint_ark("   ")


@pytest.mark.preservation
def test_is_ark_rejects_non_ark_identifiers() -> None:
    """A plain identifier (e.g. a bare record id or URL) is not mistaken for an ARK."""
    assert not is_ark("rec-abc123")
    assert not is_ark("https://example.org/record/1")
    assert not is_ark("ark:")


def test_mint_urn_is_a_real_uuid_pid() -> None:
    """The default identifier needs no placeholder naming authority or resolver."""
    pid = mint_urn("dbe112099f50481cbf7bc688a8305076")
    assert pid == "urn:uuid:dbe11209-9f50-481c-bf7b-c688a8305076"
    assert is_pid(pid)
    assert not is_pid("urn:uuid:not-a-uuid")


def test_mint_urn_maps_stable_imported_ids_deterministically() -> None:
    """Non-UUID imported ids receive a deterministic UUIDv5 URN."""
    assert mint_urn("rec-audit") == mint_urn("rec-audit")
    assert mint_urn("rec-audit") != mint_urn("rec-other")
    assert is_pid(mint_urn("rec-audit"))
