"""Preservation-metadata honesty (mission requirement #4): a missing or
unrun fixity check must never be rendered as "verified"/"intact"/"authentic".

Pure-function tests: `GroundedContext` objects are built directly with 0, 1,
or 2 `PremisEvent`s so every branch of `payload_fixity_status` is exercised
without needing a real `Archive`/ingest pipeline.
"""

from __future__ import annotations

import pytest

from ledger.ai.context import GroundedContext
from ledger.ai.fixity_honesty import FAILED, NOT_YET_CHECKED, VERIFIED, payload_fixity_status
from ledger.models import (
    AccessPolicy,
    ContentAddress,
    DisclosedRecord,
    HashAlgo,
    PayloadFile,
    PremisEvent,
    PremisEventType,
    payload_object_id,
)

pytestmark = pytest.mark.disclosure

_RECORD_ID = "rec-fixity"
_FILENAME = "oral-history.mp3"
_ADDRESS = ContentAddress(HashAlgo.SHA256, "a" * 64)


def _payload() -> PayloadFile:
    return PayloadFile(
        filename=_FILENAME, address=_ADDRESS, media_type="audio/mpeg", policy=AccessPolicy.PUBLIC
    )


def _disclosed() -> DisclosedRecord:
    return DisclosedRecord(
        record_id=_RECORD_ID,
        title="An oral history",
        dublin_core={},
        fields={},
        payloads=(_payload(),),
        content_warnings=(),
        withheld=(),
    )


def _context(events: tuple[PremisEvent, ...]) -> GroundedContext:
    return GroundedContext(record_id=_RECORD_ID, disclosed=_disclosed(), events=events, evidence=())


def _fixity_event(outcome: str, when: str) -> PremisEvent:
    return PremisEvent(
        event_type=PremisEventType.FIXITY_CHECK,
        agent="test-agent",
        outcome=outcome,
        linked_object=payload_object_id(_RECORD_ID, _FILENAME),
        event_datetime=when,
    )


def test_no_fixity_event_means_not_yet_checked() -> None:
    context = _context(())
    status = payload_fixity_status(context, _FILENAME)
    assert status == NOT_YET_CHECKED


def test_unrelated_event_alone_does_not_count_as_a_fixity_check() -> None:
    """An INGESTION event must not be mistaken for a fixity check having run."""
    context = _context(
        (
            PremisEvent(
                event_type=PremisEventType.INGESTION,
                agent="test-agent",
                outcome="success",
                linked_object=_RECORD_ID,
                event_datetime="2026-01-01T00:00:00Z",
            ),
        )
    )
    assert payload_fixity_status(context, _FILENAME) == NOT_YET_CHECKED


def test_successful_fixity_check_is_reported_as_verified() -> None:
    context = _context((_fixity_event("success", "2026-01-01T00:00:00Z"),))
    status = payload_fixity_status(context, _FILENAME)
    assert status.startswith(VERIFIED)
    assert "2026-01-01T00:00:00Z" in status


def test_failed_fixity_check_is_reported_as_failed_never_as_verified() -> None:
    context = _context((_fixity_event("failure", "2026-02-01T00:00:00Z"),))
    status = payload_fixity_status(context, _FILENAME)
    assert status.startswith(FAILED)
    assert VERIFIED not in status


def test_the_most_recent_check_wins_over_an_earlier_success() -> None:
    """A later failure must not be masked by an earlier success -- this is
    exactly the "absence/staleness rendered as a positive value" defect class
    the mission names."""
    context = _context(
        (
            _fixity_event("success", "2026-01-01T00:00:00Z"),
            _fixity_event("failure", "2026-03-01T00:00:00Z"),
        )
    )
    status = payload_fixity_status(context, _FILENAME)
    assert status.startswith(FAILED)


def test_the_most_recent_check_wins_over_an_earlier_failure() -> None:
    """Symmetric: a corrected, later success must not stay masked by staleness."""
    context = _context(
        (
            _fixity_event("failure", "2026-01-01T00:00:00Z"),
            _fixity_event("success", "2026-03-01T00:00:00Z"),
        )
    )
    status = payload_fixity_status(context, _FILENAME)
    assert status.startswith(VERIFIED)


def test_a_nonexistent_payload_is_reported_as_not_yet_checked_not_an_error() -> None:
    context = _context((_fixity_event("success", "2026-01-01T00:00:00Z"),))
    assert payload_fixity_status(context, "does-not-exist.mp3") == NOT_YET_CHECKED


def test_a_fixity_event_for_a_different_payload_does_not_leak_across() -> None:
    """A fixity check on one payload must never be attributed to another."""
    other_object_id = payload_object_id(_RECORD_ID, "other-file.mp3")
    event = PremisEvent(
        event_type=PremisEventType.FIXITY_CHECK,
        agent="test-agent",
        outcome="success",
        linked_object=other_object_id,
        event_datetime="2026-01-01T00:00:00Z",
    )
    context = _context((event,))
    assert payload_fixity_status(context, _FILENAME) == NOT_YET_CHECKED


def test_only_three_honest_states_exist() -> None:
    """The vocabulary is closed: a model claim outside these three strings can
    never match grounded fixity evidence."""
    assert {NOT_YET_CHECKED, VERIFIED, FAILED} == {
        "fixity has not yet been checked for this file",
        "fixity was verified",
        "a fixity check failed for this file",
    }
