"""Redaction-as-a-recorded-transform tests.

A redaction in ledger is not a quiet in-place mutation; it produces a lossy *copy*
plus a :class:`~ledger.models.PremisEvent` documenting the change, and it leaves the
original untouched. These tests pin both halves of that contract — the copy is
redacted, the audit event is correct, and crucially the event names only the field
or filename, never the withheld value (auditability, fidelity, the no-outing rule).
"""

from __future__ import annotations

import pytest

from ledger.access.redaction import redact_field, redact_payload
from ledger.models import (
    AccessPolicy,
    ContentAddress,
    Field,
    PayloadFile,
    PremisEventType,
    Record,
)

pytestmark = pytest.mark.disclosure

_NOW = "2026-06-16T12:00:00Z"
_SENSITIVE_VALUE = "Deadname McSurname, 555-0100"


def _record_with_sensitive_field() -> Record:
    return Record(
        title="Letter, undated",
        record_id="rec-letter",
        fields=[
            Field(name="transcript", value="the body of the letter", policy=AccessPolicy.PUBLIC),
            Field(name="signature", value=_SENSITIVE_VALUE, policy=AccessPolicy.STEWARDS),
        ],
    )


def _record_with_payload() -> Record:
    return Record(
        title="Audio interview",
        record_id="rec-audio",
        payloads=[
            PayloadFile(
                filename="interview.wav",
                address=ContentAddress.parse("sha256:" + "b" * 64),
                policy=AccessPolicy.COMMUNITY,
            ),
            PayloadFile(
                filename="raw-with-names.wav",
                address=ContentAddress.parse("sha256:" + "c" * 64),
                policy=AccessPolicy.STEWARDS,
            ),
        ],
    )


# --- redact_field -----------------------------------------------------------


def test_redact_field_returns_copy_and_event() -> None:
    """redact_field returns a (Record, PremisEvent) pair."""
    record = _record_with_sensitive_field()
    redacted, event = redact_field(record, "signature", agent="steward", now=_NOW)
    assert isinstance(redacted, Record)
    assert event.event_type is PremisEventType.REDACTION


def test_redact_field_replaces_value_in_copy() -> None:
    """The returned copy has the field's value replaced with the redaction marker."""
    record = _record_with_sensitive_field()
    redacted, _ = redact_field(record, "signature", agent="steward", now=_NOW)
    fld = redacted.field_named("signature")
    assert fld is not None
    assert fld.value == "[redacted]"
    assert _SENSITIVE_VALUE not in fld.value


def test_redact_field_leaves_original_unchanged() -> None:
    """The original record still carries the unredacted value (transform, not mutation)."""
    record = _record_with_sensitive_field()
    redact_field(record, "signature", agent="steward", now=_NOW)
    original = record.field_named("signature")
    assert original is not None
    assert original.value == _SENSITIVE_VALUE


def test_redact_field_preserves_other_fields() -> None:
    """Redacting one field does not touch the others in the copy."""
    record = _record_with_sensitive_field()
    redacted, _ = redact_field(record, "signature", agent="steward", now=_NOW)
    other = redacted.field_named("transcript")
    assert other is not None
    assert other.value == "the body of the letter"


def test_redact_field_event_names_field_never_value() -> None:
    """The audit event names the field, never echoes the withheld value (no-outing rule)."""
    record = _record_with_sensitive_field()
    _, event = redact_field(record, "signature", agent="steward", now=_NOW)
    assert "signature" in event.detail
    assert _SENSITIVE_VALUE not in event.detail
    assert event.linked_object == "rec-letter"
    assert event.agent == "steward"
    assert event.outcome == "success"


def test_redact_field_event_uses_injected_now() -> None:
    """The event timestamp is the injected ``now`` (determinism, no wall clock)."""
    record = _record_with_sensitive_field()
    _, event = redact_field(record, "signature", agent="steward", now=_NOW)
    assert event.event_datetime == _NOW


def test_redact_field_is_deterministic() -> None:
    """The same inputs produce an identical event (reproducibility)."""
    record = _record_with_sensitive_field()
    _, first = redact_field(record, "signature", agent="steward", now=_NOW)
    _, second = redact_field(record, "signature", agent="steward", now=_NOW)
    assert first.to_dict() == second.to_dict()


# --- redact_payload ---------------------------------------------------------


def test_redact_payload_drops_named_file_in_copy() -> None:
    """The named payload is removed from the copy's manifest; the rest remain."""
    record = _record_with_payload()
    redacted, _ = redact_payload(record, "raw-with-names.wav", agent="steward", now=_NOW)
    names = [p.filename for p in redacted.payloads]
    assert "raw-with-names.wav" not in names
    assert "interview.wav" in names


def test_redact_payload_leaves_original_unchanged() -> None:
    """The original record still lists the dropped payload (transform, not mutation)."""
    record = _record_with_payload()
    redact_payload(record, "raw-with-names.wav", agent="steward", now=_NOW)
    names = [p.filename for p in record.payloads]
    assert "raw-with-names.wav" in names


def test_redact_payload_event_names_filename_only() -> None:
    """The audit event names the filename and links the record id, nothing more."""
    record = _record_with_payload()
    _, event = redact_payload(record, "raw-with-names.wav", agent="steward", now=_NOW)
    assert event.event_type is PremisEventType.REDACTION
    assert "raw-with-names.wav" in event.detail
    assert event.linked_object == "rec-audio"
    assert event.event_datetime == _NOW
