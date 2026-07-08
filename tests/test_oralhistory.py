"""Tests for :mod:`ledger.oralhistory` — the EXP-09 oral-history session kit.

Covers manifest (de)serialization round-tripping, the one hard rule (a disclosing
segment requires a spoken-consent timestamp), and the mapping onto a
:class:`~ledger.models.Record` (per-segment Field + stewards-only consent audit
field + pre-declared payload policy), plus an end-to-end ingest through the real
archive so the session kit is proven against the one ingest path, not just in
isolation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ledger.access.grants import anonymous, community_member, steward
from ledger.config import Config
from ledger.errors import LedgerError
from ledger.ingest import Archive
from ledger.models import AccessPolicy, DublinCore, Record
from ledger.oralhistory import (
    SessionManifest,
    SessionSegment,
    apply_session_manifest,
    parse_session_manifest,
    session_manifest_to_json,
    validate_session_manifest,
)

_NOW = "2026-07-02T18:00:00Z"


def _segment(**overrides: object) -> SessionSegment:
    defaults: dict[str, object] = {
        "segment_id": "seg-01",
        "label": "how the fridge started",
        "start_seconds": 0.0,
        "end_seconds": 120.0,
        "policy": AccessPolicy.PUBLIC,
        "spoken_consent_at": "2026-07-02T18:03:10Z",
        "consent_note": "narrator: public",
    }
    defaults.update(overrides)
    return SessionSegment(**defaults)  # type: ignore[arg-type]


def _manifest(*segments: SessionSegment, **overrides: object) -> SessionManifest:
    defaults: dict[str, object] = {
        "session_id": "2026-07-elders-circle-03",
        "recorded_at": _NOW,
        "facilitator": "community steward",
        "narrator_ref": "narrator",
        "segments": segments or (_segment(),),
    }
    defaults.update(overrides)
    return SessionManifest(**defaults)  # type: ignore[arg-type]


# --- SessionSegment / SessionManifest construction ---------------------------


def test_segment_requires_nonempty_id() -> None:
    with pytest.raises(LedgerError, match="segment_id"):
        _segment(segment_id="")


def test_segment_rejects_end_before_start() -> None:
    with pytest.raises(LedgerError, match="end_seconds"):
        _segment(start_seconds=100.0, end_seconds=10.0)


def test_segment_rejects_negative_start() -> None:
    with pytest.raises(LedgerError, match="negative"):
        _segment(start_seconds=-5.0, end_seconds=10.0)


def test_manifest_requires_at_least_one_segment() -> None:
    with pytest.raises(LedgerError, match="no segments"):
        SessionManifest(
            session_id="s1", recorded_at=_NOW, facilitator="steward", segments=()
        )


def test_manifest_rejects_duplicate_segment_ids() -> None:
    with pytest.raises(LedgerError, match="duplicate"):
        _manifest(_segment(segment_id="seg-01"), _segment(segment_id="seg-01"))


def test_ever_discloses() -> None:
    assert _segment(policy=AccessPolicy.PUBLIC).ever_discloses
    assert _segment(policy=AccessPolicy.COMMUNITY).ever_discloses
    assert _segment(policy=AccessPolicy.STEWARDS).ever_discloses
    assert not _segment(
        policy=AccessPolicy.SEALED_UNTIL, unseal_at=None, spoken_consent_at=None
    ).ever_discloses
    assert _segment(
        policy=AccessPolicy.SEALED_UNTIL, unseal_at="2046-01-01T00:00:00Z"
    ).ever_discloses
    assert not _segment(policy=AccessPolicy.SEALED, spoken_consent_at=None).ever_discloses


# --- JSON round-trip ----------------------------------------------------------


def test_manifest_round_trips_through_json() -> None:
    original = _manifest(
        _segment(),
        _segment(
            segment_id="seg-02",
            policy=AccessPolicy.SEALED_UNTIL,
            unseal_at="2046-07-02T00:00:00Z",
            payload_filename="seg-02.wav",
            transcript="a longer, sealed story",
        ),
    )
    text = session_manifest_to_json(original)
    rebuilt = parse_session_manifest(text)
    assert rebuilt.to_dict() == original.to_dict()


def test_parse_session_manifest_rejects_bad_json() -> None:
    with pytest.raises(LedgerError, match="not valid JSON"):
        parse_session_manifest("{not json")


def test_parse_session_manifest_rejects_non_object() -> None:
    with pytest.raises(LedgerError, match="JSON object"):
        parse_session_manifest("[1, 2, 3]")


def test_parse_session_manifest_matches_documented_example() -> None:
    """The example in docs/oral-history/session-manifest-format.md parses and
    validates cleanly — keeps the doc and the implementation from drifting apart.
    """
    example = {
        "session_id": "2026-07-elders-circle-03",
        "recorded_at": "2026-07-02T18:00:00Z",
        "facilitator": "community steward",
        "narrator_ref": "narrator",
        "segments": [
            {
                "segment_id": "seg-01",
                "label": "how the mutual-aid fridge started",
                "start_seconds": 0,
                "end_seconds": 240,
                "policy": "public",
                "spoken_consent_at": "2026-07-02T18:03:10Z",
                "consent_note": "narrator: public, this is the part I want people to hear",
                "payload_filename": "seg-01.wav",
            },
            {
                "segment_id": "seg-02",
                "label": "names of people still living who were involved",
                "start_seconds": 240,
                "end_seconds": 410,
                "policy": "sealed-until",
                "unseal_at": "2046-07-02T00:00:00Z",
                "spoken_consent_at": "2026-07-02T18:07:45Z",
                "consent_note": "narrator: seal this part twenty years, folks are still around",
                "payload_filename": "seg-02.wav",
            },
            {
                "segment_id": "seg-03",
                "label": "a detail the narrator asked never be shared",
                "start_seconds": 410,
                "end_seconds": 480,
                "policy": "sealed",
            },
        ],
    }
    manifest = parse_session_manifest(json.dumps(example))
    validate_session_manifest(manifest)  # must not raise
    assert len(manifest.segments) == 3


# --- validate_session_manifest: the one hard rule -----------------------------


def test_validate_rejects_disclosing_segment_with_no_consent_timestamp() -> None:
    manifest = _manifest(_segment(policy=AccessPolicy.PUBLIC, spoken_consent_at=None))
    with pytest.raises(LedgerError, match="spoken_consent_at"):
        validate_session_manifest(manifest)


def test_validate_allows_indefinitely_sealed_segment_with_no_consent_timestamp() -> None:
    manifest = _manifest(
        _segment(
            policy=AccessPolicy.SEALED_UNTIL, unseal_at=None, spoken_consent_at=None
        )
    )
    validate_session_manifest(manifest)  # must not raise


def test_validate_allows_absolute_sealed_segment_with_no_consent_timestamp() -> None:
    manifest = _manifest(_segment(policy=AccessPolicy.SEALED, spoken_consent_at=None))
    validate_session_manifest(manifest)  # must not raise


def test_validate_rejects_date_bound_sealed_until_with_no_consent_timestamp() -> None:
    manifest = _manifest(
        _segment(
            policy=AccessPolicy.SEALED_UNTIL,
            unseal_at="2046-01-01T00:00:00Z",
            spoken_consent_at=None,
        )
    )
    with pytest.raises(LedgerError, match="spoken_consent_at"):
        validate_session_manifest(manifest)


def test_validate_rejects_sealed_conditional_without_condition() -> None:
    manifest = _manifest(
        _segment(policy=AccessPolicy.SEALED_CONDITIONAL, unseal_condition=None)
    )
    with pytest.raises(LedgerError, match="unseal_condition"):
        validate_session_manifest(manifest)


def test_validate_error_names_segment_id_not_content() -> None:
    """The error names the segment id but never the label or consent note
    (no-outing-adjacent: a validation error is diagnostic, not a leak)."""
    manifest = _manifest(
        _segment(
            segment_id="seg-secret",
            label="SENTINEL-LABEL-DO-NOT-LEAK",
            consent_note="SENTINEL-NOTE-DO-NOT-LEAK",
            policy=AccessPolicy.PUBLIC,
            spoken_consent_at=None,
        )
    )
    with pytest.raises(LedgerError) as exc_info:
        validate_session_manifest(manifest)
    message = str(exc_info.value)
    assert "seg-secret" in message
    assert "SENTINEL-LABEL-DO-NOT-LEAK" not in message
    assert "SENTINEL-NOTE-DO-NOT-LEAK" not in message


# --- apply_session_manifest ----------------------------------------------------


def _base_record() -> Record:
    return Record(
        title="Elders' Circle, session 3",
        default_policy=AccessPolicy.SEALED_UNTIL,
        dublin_core=DublinCore(title=["Elders' Circle, session 3"]),
    )


def test_apply_is_pure_and_adds_two_fields_per_segment() -> None:
    record = _base_record()
    manifest = _manifest(_segment())
    updated = apply_session_manifest(record, manifest)

    assert record.fields == []  # original untouched (purity)
    names = [f.name for f in updated.fields]
    assert "segment:seg-01" in names
    assert "segment:seg-01:consent" in names


def test_apply_refuses_invalid_manifest() -> None:
    record = _base_record()
    manifest = _manifest(_segment(policy=AccessPolicy.PUBLIC, spoken_consent_at=None))
    with pytest.raises(LedgerError, match="spoken_consent_at"):
        apply_session_manifest(record, manifest)
    assert record.fields == []  # nothing partially applied


def test_apply_segment_field_carries_segments_own_policy() -> None:
    record = _base_record()
    manifest = _manifest(
        _segment(segment_id="seg-01", policy=AccessPolicy.PUBLIC),
        _segment(
            segment_id="seg-02",
            policy=AccessPolicy.SEALED_UNTIL,
            unseal_at="2046-07-02T00:00:00Z",
            spoken_consent_at="2026-07-02T18:07:45Z",
        ),
    )
    updated = apply_session_manifest(record, manifest)
    seg1 = updated.field_named("segment:seg-01")
    seg2 = updated.field_named("segment:seg-02")
    assert seg1 is not None and seg1.policy is AccessPolicy.PUBLIC
    assert seg2 is not None and seg2.policy is AccessPolicy.SEALED_UNTIL
    assert seg2.unseal_at == "2046-07-02T00:00:00Z"


def test_apply_consent_field_is_stewards_only_and_carries_timestamp() -> None:
    record = _base_record()
    manifest = _manifest(_segment(spoken_consent_at="2026-07-02T18:03:10Z"))
    updated = apply_session_manifest(record, manifest)
    consent_field = updated.field_named("segment:seg-01:consent")
    assert consent_field is not None
    assert consent_field.policy is AccessPolicy.STEWARDS
    assert "2026-07-02T18:03:10Z" in consent_field.value


def test_apply_predeclares_payload_for_segment_with_payload_filename() -> None:
    record = _base_record()
    manifest = _manifest(_segment(payload_filename="seg-01.wav"))
    updated = apply_session_manifest(record, manifest)
    filenames = [p.filename for p in updated.payloads]
    assert filenames == ["seg-01.wav"]
    assert updated.payloads[0].policy is AccessPolicy.PUBLIC


def test_apply_segment_without_payload_filename_adds_no_payload() -> None:
    record = _base_record()
    manifest = _manifest(_segment(payload_filename=None))
    updated = apply_session_manifest(record, manifest)
    assert updated.payloads == []


# --- end-to-end through the real archive/ingest path ---------------------------


def test_session_manifest_ingests_through_the_one_ingest_path(tmp_path: Path) -> None:
    """A validated manifest, applied to a record, ingests cleanly and the per-
    segment policies are honoured on disclosure — public segment visible to an
    anonymous grant, community segment only to a community grant, sealed segment
    to neither.
    """
    config = Config.default("Elders Circle Archive", tmp_path)
    archive = Archive.init(config)

    record = _base_record()
    manifest = _manifest(
        _segment(segment_id="seg-public", policy=AccessPolicy.PUBLIC),
        _segment(
            segment_id="seg-community",
            policy=AccessPolicy.COMMUNITY,
            spoken_consent_at="2026-07-02T18:05:00Z",
        ),
        _segment(
            segment_id="seg-sealed",
            policy=AccessPolicy.SEALED_UNTIL,
            unseal_at=None,
            spoken_consent_at=None,
        ),
    )
    record = apply_session_manifest(record, manifest)
    # Listability is gated on the record's own default_policy, separately from
    # each field's own policy (see ledger.access.policy.is_listable); make the
    # record itself PUBLIC-listable so all three grants below can even reach it,
    # while each segment field still gates disclosure on its own policy.
    record.default_policy = AccessPolicy.PUBLIC

    aip = archive.ingest({}, record, agent="test-facilitator", now=_NOW)

    anon_view = archive.disclose(record.record_id, anonymous(), now=_NOW)
    assert "segment:seg-public" in anon_view.fields
    assert "segment:seg-community" not in anon_view.fields
    assert "segment:seg-sealed" not in anon_view.fields
    assert "segment:seg-public:consent" not in anon_view.fields  # stewards-only

    community_view = archive.disclose(record.record_id, community_member("c1"), now=_NOW)
    assert "segment:seg-community" in community_view.fields
    assert "segment:seg-sealed" not in community_view.fields

    steward_view = archive.disclose(record.record_id, steward("s1"), now=_NOW)
    assert "segment:seg-public:consent" in steward_view.fields
    # An indefinite sealed-until (no unseal_at) is an access-level seal a steward
    # may read for administration (see ledger.access.policy.is_visible) — unlike
    # an absolute `sealed` field, which no grant ever satisfies.
    assert "segment:seg-sealed" in steward_view.fields

    assert aip.record.record_id == record.record_id
