"""Redaction as a first-class, recorded transform.

A redaction is not an in-place mutation that quietly destroys evidence; it is a
*transform* that produces a lossy copy and an accompanying
:class:`~ledger.models.PremisEvent`. The original stays access-controlled
wherever the caller keeps it, and the audit trail records exactly what was
removed, by whom, and when (auditability, fidelity — the lossy view never
masquerades as the original).

Like the rest of disclosure, these functions are deterministic: the caller passes
``agent`` and ``now`` explicitly so the emitted event is reproducible and never
reaches for the wall clock (determinism, traceability).
"""

from __future__ import annotations

from dataclasses import replace

from ledger.models import (
    PremisEvent,
    PremisEventType,
    Record,
    with_redaction,
)


def redact_field(
    record: Record,
    field_name: str,
    *,
    agent: str,
    now: str,
) -> tuple[Record, PremisEvent]:
    """Redact one descriptive field, returning a lossy copy and an audit event.

    The copy has ``field_name``'s value replaced via
    :func:`~ledger.models.with_redaction`; the original record is untouched and
    stays access-controlled elsewhere (the caller decides what to keep). The
    returned :class:`~ledger.models.PremisEvent` records the redaction against the
    record id so the change is provable after the fact (auditability).

    The event detail names only the *field*, never its withheld value, honouring
    the no-outing rule even in the audit trail (confidentiality).
    """
    redacted = with_redaction(record, field_name)
    event = PremisEvent(
        event_type=PremisEventType.REDACTION,
        agent=agent,
        outcome="success",
        detail=f"redacted field: {field_name}",
        linked_object=record.record_id,
        event_datetime=now,
    )
    return redacted, event


def redact_payload(
    record: Record,
    filename: str,
    *,
    agent: str,
    now: str,
) -> tuple[Record, PremisEvent]:
    """Drop one payload from a copy of the record, returning it and an audit event.

    The named payload is removed from the copy's manifest; the original record is
    untouched. As with :func:`redact_field`, the event names only the *filename*,
    never any sealed content, and links to the record id for a provable trail
    (auditability, fidelity, confidentiality).
    """
    kept = [payload for payload in record.payloads if payload.filename != filename]
    redacted = replace(record, payloads=kept)
    event = PremisEvent(
        event_type=PremisEventType.REDACTION,
        agent=agent,
        outcome="success",
        detail=f"redacted payload: {filename}",
        linked_object=record.record_id,
        event_datetime=now,
    )
    return redacted, event
