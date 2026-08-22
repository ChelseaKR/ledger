"""The access-control gate every AI feature must pass through.

:func:`build_context` is the ONE function in this package that touches an
:class:`~ledger.ingest.Archive`. It calls :meth:`Archive.disclose` FIRST — the
exact same single disclosure chokepoint (:mod:`ledger.access.policy`) every
other read path in this repo (browse, search, the JSON API, export) already
uses — and raises :class:`~ledger.errors.AccessDenied` under precisely the
condition every other read path does. Every AI module downstream of this file
(``describe.py``, ``ask.py``) only ever sees the :class:`GroundedContext` this
function returns; none of them accepts an ``Archive``, a ``Grant``, or a raw
:class:`~ledger.models.Record`.

This is deliberately the *same* discipline :class:`~ledger.models.DisclosedRecord`
uses for the no-outing guarantee: there is structurally no field on
``GroundedContext`` that could carry an ``identity_ref`` or a withheld value,
because the object it is built from (``DisclosedRecord``) does not carry one
either. A caller cannot construct a ``GroundedContext`` that skips
``Archive.disclose`` — there is no other constructor.

PREMIS events are identity-free by construction (see
:mod:`ledger.metadata.premis`), so including them here does not, on its own,
risk the no-outing guarantee. :func:`_filter_events_to_disclosed` still scopes
them to the record and to payloads the viewer may actually see, as
defense-in-depth against a withheld payload's fixity/format history reaching
the model even though the event log carries no protected *value* — mirroring
how :func:`ledger.access.policy.disclose` withholds a payload outright rather
than trusting the caller to ignore it.
"""

from __future__ import annotations

from dataclasses import dataclass

from ledger.ingest import Archive
from ledger.models import DisclosedRecord, Grant, PremisEvent, payload_object_id

__all__ = ["EvidenceItem", "GroundedContext", "build_context"]


@dataclass(frozen=True)
class EvidenceItem:
    """One piece of grounded evidence an AI claim may cite.

    ``kind``/``ref`` together are the citation key a claim names
    (:class:`ledger.ai.grounding.Citation`); ``text`` is the exact string a
    quoted claim must match a substring of.
    """

    kind: str
    ref: str
    text: str


@dataclass(frozen=True)
class GroundedContext:
    """Everything one AI call about one record is allowed to see.

    Built exclusively from a :class:`~ledger.models.DisclosedRecord` (already
    access-controlled for this viewer at this instant) plus that record's
    PREMIS events, filtered to what this viewer may see. No ``identity_ref``,
    no sealed field, no withheld payload's detail ever reaches here — there is
    no field to put one in.
    """

    record_id: str
    disclosed: DisclosedRecord
    events: tuple[PremisEvent, ...]
    evidence: tuple[EvidenceItem, ...]

    def evidence_text_by_ref(self) -> dict[tuple[str, str], str]:
        """A ``(kind, ref) -> text`` lookup, for the grounding verifier."""
        return {(item.kind, item.ref): item.text for item in self.evidence}


def _visible_payload_object_ids(disclosed: DisclosedRecord) -> set[str]:
    return {payload_object_id(disclosed.record_id, p.filename) for p in disclosed.payloads}


def _visible_payload_addresses(disclosed: DisclosedRecord) -> set[str]:
    return {str(p.address) for p in disclosed.payloads}


def _filter_events_to_disclosed(
    record_id: str, disclosed: DisclosedRecord, events: list[PremisEvent]
) -> list[PremisEvent]:
    """Keep only events about the record itself, or a payload this viewer may see."""
    visible_ids = _visible_payload_object_ids(disclosed)
    visible_addresses = _visible_payload_addresses(disclosed)
    kept: list[PremisEvent] = []
    for event in events:
        linked = event.linked_object
        if linked is None or linked == record_id:
            kept.append(event)
            continue
        if linked in visible_ids or linked in visible_addresses:
            kept.append(event)
            continue
        if (
            event.linked_content_address is not None
            and event.linked_content_address in visible_addresses
        ):
            kept.append(event)
    return kept


def _evidence_for(
    disclosed: DisclosedRecord, events: tuple[PremisEvent, ...]
) -> tuple[EvidenceItem, ...]:
    items: list[EvidenceItem] = [EvidenceItem("title", "title", disclosed.title)]
    for name, values in disclosed.dublin_core.items():
        for value in values:
            items.append(EvidenceItem("dublin_core", name, value))
    for name, value in disclosed.fields.items():
        items.append(EvidenceItem("field", name, value))
    for payload in disclosed.payloads:
        items.append(
            EvidenceItem(
                "payload",
                payload.filename,
                f"{payload.filename} ({payload.media_type}, "
                f"basis={payload.media_type_basis or 'unknown'})",
            )
        )
        if payload.transcript:
            items.append(EvidenceItem("payload_transcript", payload.filename, payload.transcript))
    for warning in disclosed.content_warnings:
        items.append(EvidenceItem("content_warning", warning, warning))
    for index, event in enumerate(events):
        detail = f" -- {event.detail}" if event.detail else ""
        items.append(
            EvidenceItem(
                "premis_event",
                str(index),
                f"{event.event_type.value}: {event.outcome} ({event.event_datetime}){detail}",
            )
        )
    return tuple(items)


def build_context(
    archive: Archive, record_id: str, grant: Grant, now: str | None = None
) -> GroundedContext:
    """Disclose ``record_id`` to ``grant``, THEN build the AI-visible context.

    Raises :class:`~ledger.errors.AccessDenied` under the exact same condition
    :meth:`Archive.disclose` does. This is the only way to obtain a
    :class:`GroundedContext`; there is no path that skips disclosure.
    """
    disclosed = archive.disclose(record_id, grant, now)
    events = tuple(
        _filter_events_to_disclosed(record_id, disclosed, archive.record_events(record_id))
    )
    evidence = _evidence_for(disclosed, events)
    return GroundedContext(
        record_id=record_id, disclosed=disclosed, events=events, evidence=evidence
    )
