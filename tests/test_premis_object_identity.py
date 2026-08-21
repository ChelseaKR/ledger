"""The PREMIS Object is the payload within a record, not the bytes (ADR 0012, #149).

Format identification is a function of the bytes *and the filename*. A PREMIS
format-identification event used to be linked to the payload's content address,
which is a function of the bytes alone — and the content store deduplicates. So
when identical bytes arrived under two names that identify differently, one object
identifier carried two contradictory verdicts, and a consumer reading the log the
correct way (keyed by ``linkingObjectIdentifier``) saw whichever was written last.

The real corpus surfaced one instance (``testIBM_DCA.rft`` byte-identical to three
``testIBMDisplayWrite*.doc`` files); ``a.txt`` and ``a.md`` with identical contents
reproduce the class. These tests seed that class and prove, from both sides, that
it can no longer be recorded silently:

* two byte-identical payloads are two objects with two events, each about itself;
* a second, *different* verdict for the same object and bytes is refused;
* a log written before ADR 0012 — keyed by address, contradiction and all — is
  read without a crash and reports the contradiction instead of hiding it;
* the event shape every existing log hash-chains over is unchanged;
* the harness's detectors catch each seeded defect (break-the-gate), and pass on
  a clean archive (the gate is capable of passing).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tools.real_corpus import (
    _event_record_agreement,
    _format_events,
    _identification_contradictions,
    _shared_address_groups,
    _success_while_unidentified,
)

from ledger.chain import build_chain, entry_hash
from ledger.config import Config
from ledger.errors import PremisContradictionError
from ledger.ingest import Archive
from ledger.metadata.premis import PremisLog, to_premis_xml
from ledger.models import (
    OBJECT_TYPE_CONTENT_ADDRESS,
    OBJECT_TYPE_PAYLOAD,
    OBJECT_TYPE_RECORD,
    AccessPolicy,
    DublinCore,
    PremisEvent,
    PremisEventType,
    Record,
    payload_object_id,
)

pytestmark = pytest.mark.preservation

_NOW = "2026-01-01T00:00:00Z"
_BYTES = b"# Notes\n\nThe same bytes, filed twice under two names.\n"


def _ingest_same_bytes_twice(tmp_path: Path) -> tuple[Archive, Record]:
    """One record holding ``a.txt`` and ``a.md`` with byte-identical contents.

    ``txt`` and ``md`` are both registry extension rows, so the identifier returns
    ``text/plain`` for one and ``text/markdown`` for the other from the *same*
    bytes — the exact shape of #149.
    """
    incoming = tmp_path / "in"
    incoming.mkdir()
    (incoming / "a.txt").write_bytes(_BYTES)
    (incoming / "a.md").write_bytes(_BYTES)
    archive = Archive.init(Config.default("Identity archive", tmp_path / "archive"))
    record = Record(
        title="Two names, one byte stream",
        record_id="rec-same-bytes",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(title=["Two names, one byte stream"]),
    )
    archive.ingest({"a.txt": incoming / "a.txt", "a.md": incoming / "a.md"}, record, now=_NOW)
    return archive, record


def _identification_events(log: PremisLog) -> list[PremisEvent]:
    return [e for e in log.events if e.event_type is PremisEventType.FORMAT_IDENTIFICATION]


def _legacy_event(address: str, verdict: str) -> PremisEvent:
    """An identification event in the pre-ADR-0012 shape: keyed by content address."""
    return PremisEvent(
        event_type=PremisEventType.FORMAT_IDENTIFICATION,
        agent="legacy-ingest",
        outcome="success",
        detail=f"identified as Something [no-puid] via extension; media-type {verdict}",
        linked_object=address,
        event_datetime=_NOW,
    )


# --- two payloads, two objects ----------------------------------------------------


def test_byte_identical_payloads_are_two_objects_with_two_events(tmp_path: Path) -> None:
    """Same bytes, two names: two payload objects, each with exactly one verdict."""
    archive, record = _ingest_same_bytes_twice(tmp_path)
    stored = archive.get(record.record_id)
    addresses = {str(p.address) for p in stored.payloads}
    assert len(addresses) == 1, "the store deduplicates; both payloads share one address"

    log = PremisLog.read(archive.bags_dir / record.record_id / "premis.json")
    events = _identification_events(log)
    assert len(events) == 2
    assert {e.linked_object for e in events} == {
        payload_object_id(record.record_id, "a.txt"),
        payload_object_id(record.record_id, "a.md"),
    }
    assert all(e.linked_object_type == OBJECT_TYPE_PAYLOAD for e in events)
    assert all(e.object_identifier_type == OBJECT_TYPE_PAYLOAD for e in events)
    assert {e.linked_content_address for e in events} == addresses

    # The verdicts DO differ — that is the premise — and the log is still readable
    # without contradiction, because they are about different objects.
    assert {"text/plain", "text/markdown"} == {
        e.detail.split("media-type ")[1].split(";")[0] for e in events
    }
    assert log.contradictions() == []
    assert log.verify_chain().ok


def test_each_event_is_the_single_source_of_its_payloads_media_type(tmp_path: Path) -> None:
    """Record entry and log event come from one ``FormatId`` and say the same thing."""
    archive, record = _ingest_same_bytes_twice(tmp_path)
    stored = archive.get(record.record_id)
    log = PremisLog.read(archive.bags_dir / record.record_id / "premis.json")
    by_object = {e.linked_object: e for e in _identification_events(log)}
    for payload in stored.payloads:
        event = by_object[payload_object_id(record.record_id, payload.filename)]
        assert f"media-type {payload.media_type}" in event.detail
        assert f"via {payload.media_type_basis}" in event.detail
        assert event.linked_content_address == str(payload.address)


def test_the_ingestion_event_is_typed_as_a_record(tmp_path: Path) -> None:
    archive, record = _ingest_same_bytes_twice(tmp_path)
    log = PremisLog.read(archive.bags_dir / record.record_id / "premis.json")
    ingestion = [e for e in log.events if e.event_type is PremisEventType.INGESTION]
    assert len(ingestion) == 1
    assert ingestion[0].linked_object == record.record_id
    assert ingestion[0].linked_object_type == OBJECT_TYPE_RECORD


def test_fixity_events_are_about_the_payload_and_name_the_bytes(tmp_path: Path) -> None:
    archive, record = _ingest_same_bytes_twice(tmp_path)
    log = PremisLog.read(archive.bags_dir / record.record_id / "premis.json")
    fixity = [e for e in log.events if e.event_type is PremisEventType.FIXITY_CHECK]
    assert {e.linked_object for e in fixity} == {
        payload_object_id(record.record_id, "a.txt"),
        payload_object_id(record.record_id, "a.md"),
    }
    assert all(e.linked_object_type == OBJECT_TYPE_PAYLOAD for e in fixity)
    assert len({e.linked_content_address for e in fixity}) == 1


def test_payload_object_id_refuses_an_ambiguous_record_id() -> None:
    """A ``/`` in the record id would make the identifier unsplittable; fail closed."""
    assert payload_object_id("rec-1", "dir/a.md") == "rec-1/dir/a.md"
    with pytest.raises(ValueError, match="record id"):
        payload_object_id("rec/1", "a.md")
    with pytest.raises(ValueError, match="record id"):
        payload_object_id("", "a.md")


# --- the guard: a contradiction is refused, not written -----------------------------


def test_a_second_different_verdict_for_the_same_object_and_bytes_is_refused() -> None:
    log = PremisLog()
    first = PremisEvent(
        event_type=PremisEventType.FORMAT_IDENTIFICATION,
        agent="ingest",
        outcome="success",
        detail="identified as Markdown [fmt/1149] via extension; media-type text/markdown",
        linked_object="rec-1/a.md",
        linked_object_type=OBJECT_TYPE_PAYLOAD,
        linked_content_address="sha256:" + "ab" * 32,
        event_datetime=_NOW,
    )
    log.record(first)
    head_before = log.head

    contradicting = PremisEvent(
        event_type=PremisEventType.FORMAT_IDENTIFICATION,
        agent="ingest",
        outcome="success",
        detail="identified as Plain text (UTF-8) [x-fmt/111] via text; media-type text/plain",
        linked_object="rec-1/a.md",
        linked_object_type=OBJECT_TYPE_PAYLOAD,
        linked_content_address="sha256:" + "ab" * 32,
        event_datetime=_NOW,
    )
    with pytest.raises(PremisContradictionError, match=r"rec-1/a\.md"):
        log.record(contradicting)
    # Refused BEFORE anything was appended: the log is exactly as it was.
    assert len(log.events) == 1
    assert log.head == head_before
    assert log.contradictions() == []


def test_an_agreeing_repeat_is_history_and_different_bytes_are_a_different_object() -> None:
    """The guard is narrow: only *same object, same bytes, different verdict* is refused."""
    log = PremisLog()
    base = PremisEvent(
        event_type=PremisEventType.FORMAT_IDENTIFICATION,
        agent="ingest",
        outcome="success",
        detail="identified as Markdown [fmt/1149] via extension; media-type text/markdown",
        linked_object="rec-1/a.md",
        linked_object_type=OBJECT_TYPE_PAYLOAD,
        linked_content_address="sha256:" + "ab" * 32,
        event_datetime=_NOW,
    )
    log.record(base)
    # The same verdict again (a later re-check that agrees) is recorded.
    log.record(base)
    # A different verdict about the same payload id but DIFFERENT bytes is a revised
    # deposit, not a contradiction.
    revised = PremisEvent(
        event_type=PremisEventType.FORMAT_IDENTIFICATION,
        agent="ingest",
        outcome="unidentified",
        detail="identified as Unidentified [no-puid] via unknown; media-type application/octet-stream",
        linked_object="rec-1/a.md",
        linked_object_type=OBJECT_TYPE_PAYLOAD,
        linked_content_address="sha256:" + "cd" * 32,
        event_datetime=_NOW,
    )
    log.record(revised)
    assert len(log.events) == 3
    assert log.contradictions() == []


def test_the_guard_does_not_touch_other_event_types() -> None:
    """Two consent changes about one record are ordinary history."""
    log = PremisLog()
    for detail in ("tightened", "loosened"):
        log.record(
            PremisEvent(
                event_type=PremisEventType.CONSENT_CHANGE,
                agent="steward",
                outcome="success",
                detail=detail,
                linked_object="rec-1",
                event_datetime=_NOW,
            )
        )
    assert len(log.events) == 2


# --- legacy logs: readable, and the contradiction is reported ------------------------


def test_a_legacy_address_keyed_log_reports_its_contradiction_instead_of_hiding_it(
    tmp_path: Path,
) -> None:
    """The pre-ADR-0012 shape, contradiction included, seeded around the guard.

    The constructor takes events as already-recorded history (it is how a log is
    read off disk), so it is the one way to build what an old ingest wrote. Reading
    must not raise — a steward needs to see the finding — and the reader must
    return every verdict, not the last one.
    """
    address = "sha256:" + "29e717605f17" + "0" * 52
    legacy = PremisLog(
        [
            _legacy_event(address, "application/x-ole-storage"),
            _legacy_event(address, "application/x-ole-storage"),
            _legacy_event(address, "application/octet-stream"),
        ]
    )
    path = tmp_path / "premis.json"
    legacy.write(path)

    log = PremisLog.read(path)
    assert log.verify_chain().ok, "a contradictory log is intact history, not tampering"
    found = log.contradictions()
    assert len(found) == 1
    (contradiction,) = found
    assert contradiction.object_id == address
    assert contradiction.object_type == OBJECT_TYPE_CONTENT_ADDRESS
    assert contradiction.content_address is None
    assert contradiction.events == 3
    assert [v.split("media-type ")[1] for v in contradiction.verdicts] == [
        "application/x-ole-storage",
        "application/octet-stream",
    ]


def test_object_identifier_type_is_explicit_or_safely_inferred() -> None:
    typed = PremisEvent(
        event_type=PremisEventType.FORMAT_IDENTIFICATION,
        agent="a",
        outcome="success",
        linked_object="rec-1/a.md",
        linked_object_type=OBJECT_TYPE_PAYLOAD,
    )
    assert typed.object_identifier_type == OBJECT_TYPE_PAYLOAD
    legacy_address = PremisEvent(
        event_type=PremisEventType.FIXITY_CHECK,
        agent="a",
        outcome="success",
        linked_object="sha256:" + "00" * 32,
    )
    assert legacy_address.object_identifier_type == OBJECT_TYPE_CONTENT_ADDRESS
    record_id = PremisEvent(
        event_type=PremisEventType.INGESTION, agent="a", outcome="success", linked_object="rec-1"
    )
    assert record_id.object_identifier_type is None, "a bare id is not guessed at"
    unlinked = PremisEvent(event_type=PremisEventType.LOCKDOWN, agent="a", outcome="success")
    assert unlinked.object_identifier_type is None


# --- the on-disk shape existing logs hash-chain over is unchanged -------------------


def test_an_untyped_event_serialises_exactly_as_before_so_old_chains_still_verify() -> None:
    event = PremisEvent(
        event_type=PremisEventType.FIXITY_CHECK,
        agent="ledger",
        outcome="success",
        detail="sha256+blake2b verified (abcdef012345…)",
        linked_object="sha256:" + "11" * 32,
        event_datetime=_NOW,
    )
    assert set(event.to_dict()) == {
        "eventType",
        "eventDateTime",
        "linkingAgentIdentifier",
        "eventOutcome",
        "eventDetail",
        "linkingObjectIdentifier",
    }
    # A schema-2 log written before ADR 0012, with prevHash values computed over
    # exactly that dict shape, must verify against the reader of today.
    entries = [event.to_dict()]
    prev_hashes = build_chain(entries)
    on_disk = json.dumps(
        {"schemaVersion": 2, "entries": [{**entries[0], "prevHash": prev_hashes[0]}]}
    )
    log = PremisLog.from_json(on_disk)
    verification = log.verify_chain()
    assert verification.ok
    assert verification.head == entry_hash(entries[0], prev_hashes[0])


def test_typed_events_round_trip_through_json() -> None:
    log = PremisLog()
    log.record(
        PremisEvent(
            event_type=PremisEventType.FORMAT_IDENTIFICATION,
            agent="ingest",
            outcome="at-risk",
            detail="identified as Lotus 1-2-3 [x-fmt/114] via signature; media-type application/vnd.lotus-1-2-3",
            linked_object="rec-1/KSBASE.WK1",
            linked_object_type=OBJECT_TYPE_PAYLOAD,
            linked_content_address="sha256:" + "22" * 32,
            event_datetime=_NOW,
        )
    )
    again = PremisLog.from_json(log.to_json())
    assert again.events == log.events
    assert again.verify_chain().ok
    assert again.head == log.head


def test_premis_xml_types_both_linking_identifiers() -> None:
    event = PremisEvent(
        event_type=PremisEventType.FORMAT_IDENTIFICATION,
        agent="ingest",
        outcome="success",
        detail="identified as Markdown [fmt/1149] via extension; media-type text/markdown",
        linked_object="rec-1/a.md",
        linked_object_type=OBJECT_TYPE_PAYLOAD,
        linked_content_address="sha256:" + "33" * 32,
        event_datetime=_NOW,
    )
    xml = to_premis_xml([event])
    assert xml.count("<premis:linkingObjectIdentifier>") == 2
    assert (
        "<premis:linkingObjectIdentifierType>ledger-payload</premis:linkingObjectIdentifierType>"
        in xml
    )
    assert (
        "<premis:linkingObjectIdentifierValue>rec-1/a.md</premis:linkingObjectIdentifierValue>"
        in xml
    )
    assert (
        "<premis:linkingObjectIdentifierType>content-address</premis:linkingObjectIdentifierType>"
        in xml
    )
    legacy = to_premis_xml([_legacy_event("sha256:" + "44" * 32, "text/plain")])
    assert (
        "<premis:linkingObjectIdentifierType>content-address</premis:linkingObjectIdentifierType>"
        in legacy
    )
    bare = to_premis_xml(
        [
            PremisEvent(
                event_type=PremisEventType.INGESTION,
                agent="a",
                outcome="success",
                linked_object="rec-1",
            )
        ]
    )
    assert "<premis:linkingObjectIdentifierType>local</premis:linkingObjectIdentifierType>" in bare


# --- break the gate: each harness detector catches what it is for -----------------


def test_the_detectors_pass_on_a_clean_archive(tmp_path: Path) -> None:
    """The gate is capable of passing — otherwise it is a wall, not a gate."""
    archive, record = _ingest_same_bytes_twice(tmp_path)
    bags = [record.record_id]
    assert _identification_contradictions(archive, bags) == []
    assert _success_while_unidentified(_format_events(archive, bags)) == []
    checked, problems = _event_record_agreement(archive, bags)
    assert (checked, problems) == (2, [])
    groups = _shared_address_groups(archive, bags)
    assert len(groups) == 1
    assert sorted(groups[0]["verdicts"]) == [
        "text/markdown via extension",
        "text/plain via extension",
    ]


def test_a_seeded_contradiction_fails_the_contradiction_detector(tmp_path: Path) -> None:
    archive, record = _ingest_same_bytes_twice(tmp_path)
    premis_path = archive.bags_dir / record.record_id / "premis.json"
    address = "sha256:" + "55" * 32
    # Seeded around the guard, as a doctored or legacy log would be.
    PremisLog(
        [_legacy_event(address, "text/plain"), _legacy_event(address, "text/markdown")]
    ).write(premis_path)
    found = _identification_contradictions(archive, [record.record_id])
    assert len(found) == 1
    assert found[0][0] == record.record_id
    assert found[0][1].events == 2


def test_a_seeded_success_over_an_unidentified_file_fails_its_detector(tmp_path: Path) -> None:
    archive, record = _ingest_same_bytes_twice(tmp_path)
    premis_path = archive.bags_dir / record.record_id / "premis.json"
    PremisLog(
        [
            PremisEvent(
                event_type=PremisEventType.FORMAT_IDENTIFICATION,
                agent="ingest",
                outcome="success",
                detail=(
                    "identified as Unidentified [no-puid] via unknown; media-type "
                    "application/octet-stream; UNASSESSABLE — no format was identified"
                ),
                linked_object=payload_object_id(record.record_id, "a.md"),
                linked_object_type=OBJECT_TYPE_PAYLOAD,
                linked_content_address="sha256:" + "66" * 32,
                event_datetime=_NOW,
            )
        ]
    ).write(premis_path)
    assert len(_success_while_unidentified(_format_events(archive, [record.record_id]))) == 1


def test_a_record_edited_away_from_its_log_fails_the_agreement_detector(tmp_path: Path) -> None:
    """Flip one payload's recorded media type; the log no longer agrees."""
    archive, record = _ingest_same_bytes_twice(tmp_path)
    record_path = archive.bags_dir / record.record_id / "record.json"
    doc = json.loads(record_path.read_text(encoding="utf-8"))
    for payload in doc["payloads"]:
        if payload["filename"] == "a.md":
            payload["media_type"] = "application/pdf"
    record_path.write_text(json.dumps(doc, sort_keys=True), encoding="utf-8")
    checked, problems = _event_record_agreement(archive, [record.record_id])
    assert checked == 2
    assert len(problems) == 1
    assert "a.md" in problems[0]
    assert "application/pdf" in problems[0]


def test_a_payload_with_no_event_about_it_fails_the_agreement_detector(tmp_path: Path) -> None:
    """A legacy, address-keyed log has no event *about* any payload: 0 of 2 agree."""
    archive, record = _ingest_same_bytes_twice(tmp_path)
    premis_path = archive.bags_dir / record.record_id / "premis.json"
    stored = archive.get(record.record_id)
    address = str(stored.payloads[0].address)
    PremisLog([_legacy_event(address, "text/plain")]).write(premis_path)
    checked, problems = _event_record_agreement(archive, [record.record_id])
    assert checked == 2
    assert len(problems) == 2
    assert all("0 identification events" in problem for problem in problems)
