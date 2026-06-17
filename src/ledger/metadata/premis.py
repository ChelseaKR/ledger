"""PREMIS event log — the archive's append-only record of what happened.

PREMIS (PREservation Metadata: Implementation Strategies, the Library of Congress
Data Dictionary) is the standard vocabulary for preservation events. This module
keeps an *append-only* sequence of :class:`~ledger.models.PremisEvent` objects and
serializes it two ways:

* canonical JSON — the archive's own durable form, byte-stable for hashing;
* minimal PREMIS XML — for exchange with other preservation systems.

Quality attributes:

* **Auditability / accountability / provability.** The log only ever grows; there
  is no public mutation that edits or removes a past event, so the history of an
  object is a faithful, replayable account of every ingestion, fixity check,
  policy change, and takedown.
* **Interoperability / standards-compliance.** :func:`to_premis_xml` emits valid
  ``premis:premis`` markup so another repository can read our event history.

No-outing rule: a :class:`~ledger.models.PremisEvent` carries an *agent*, an
*outcome*, a *detail*, and an opaque *linked_object* (a content address, record
id, or bag id) — never a contributor identity or a sealed value. This module adds
nothing to that shape; it only orders, serializes, and persists it.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from ledger.models import PremisEvent, PremisEventType, canonical_json

__all__ = ["PremisLog", "to_premis_xml"]

_PREMIS_NS = "http://www.loc.gov/premis/v3"


def _event_from_dict(data: dict[str, Any]) -> PremisEvent:
    """Rebuild a :class:`PremisEvent` from its ``to_dict`` form.

    Kept private: the canonical on-disk shape is owned here, mirroring
    :meth:`PremisEvent.to_dict` so a written log round-trips exactly.
    """
    return PremisEvent(
        event_type=PremisEventType(data["eventType"]),
        agent=str(data["linkingAgentIdentifier"]),
        outcome=str(data["eventOutcome"]),
        detail=str(data.get("eventDetail", "")),
        linked_object=(
            str(data["linkingObjectIdentifier"])
            if data.get("linkingObjectIdentifier") is not None
            else None
        ),
        event_datetime=str(data["eventDateTime"]),
    )


class PremisLog:
    """An append-only log of preservation events.

    The list is never exposed by reference: :attr:`events` returns a copy and the
    only mutator is :meth:`record`, which appends. This keeps the history
    tamper-evident in code as well as on disk (auditability, accountability).
    """

    def __init__(self, events: list[PremisEvent] | None = None) -> None:
        """Start a log, optionally seeded with prior events (defensively copied)."""
        self._events: list[PremisEvent] = list(events) if events is not None else []

    def record(self, event: PremisEvent) -> None:
        """Append one event. Append-only -> auditability/provability."""
        self._events.append(event)

    @property
    def events(self) -> list[PremisEvent]:
        """A copy of the events in recorded order; mutating it cannot alter the log."""
        return list(self._events)

    def to_json(self) -> str:
        """Serialize to canonical JSON over each event's dict form.

        Determinism/reproducibility: canonical JSON gives a byte-identical string
        for identical history, so the log hashes the same on every machine.
        """
        return canonical_json([event.to_dict() for event in self._events])

    @classmethod
    def from_json(cls, text: str) -> PremisLog:
        """Reconstruct a log from :meth:`to_json` output, preserving order."""
        raw: object = json.loads(text)
        if not isinstance(raw, list):
            raise ValueError("PREMIS log JSON must be a list of events")
        events = [_event_from_dict(item) for item in raw]
        return cls(events)

    def write(self, path: Path) -> None:
        """Write the log to ``path`` atomically.

        Atomic write (temp file + ``os.replace``) -> integrity/fault-tolerance: a
        reader never observes a half-written log, and a crash mid-write leaves the
        previous good file intact.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        data = self.to_json().encode("utf-8")
        with open(tmp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)

    @classmethod
    def read(cls, path: Path) -> PremisLog:
        """Read a log written by :meth:`write`."""
        return cls.from_json(Path(path).read_text(encoding="utf-8"))


def _event_to_xml(event: PremisEvent, indent: str) -> list[str]:
    """Render one event as ``premis:event`` child elements (XML-escaped)."""
    lines = [f"{indent}<premis:event>"]
    inner = indent + "  "
    lines.append(f"{inner}<premis:eventType>{escape(event.event_type.value)}</premis:eventType>")
    lines.append(
        f"{inner}<premis:eventDateTime>{escape(event.event_datetime)}</premis:eventDateTime>"
    )
    if event.detail:
        lines.append(
            f"{inner}<premis:eventDetailInformation>"
            f"<premis:eventDetail>{escape(event.detail)}</premis:eventDetail>"
            "</premis:eventDetailInformation>"
        )
    lines.append(f"{inner}<premis:eventOutcomeInformation>")
    lines.append(f"{inner}  <premis:eventOutcome>{escape(event.outcome)}</premis:eventOutcome>")
    lines.append(f"{inner}</premis:eventOutcomeInformation>")
    lines.append(f"{inner}<premis:linkingAgentIdentifier>")
    lines.append(
        f"{inner}  <premis:linkingAgentIdentifierValue>{escape(event.agent)}"
        "</premis:linkingAgentIdentifierValue>"
    )
    lines.append(f"{inner}</premis:linkingAgentIdentifier>")
    if event.linked_object is not None:
        lines.append(f"{inner}<premis:linkingObjectIdentifier>")
        lines.append(
            f"{inner}  <premis:linkingObjectIdentifierValue>"
            f"{escape(event.linked_object)}</premis:linkingObjectIdentifierValue>"
        )
        lines.append(f"{inner}</premis:linkingObjectIdentifier>")
    lines.append(f"{indent}</premis:event>")
    return lines


def to_premis_xml(events: Sequence[PremisEvent]) -> str:
    """Render ``events`` as a minimal, valid PREMIS XML document.

    Interoperability/standards-compliance: the result is a ``premis:premis`` root
    in the PREMIS v3 namespace with one ``premis:event`` child per event, so other
    preservation systems can ingest our history. All text is XML-escaped.

    No-outing rule: only the safe, opaque fields of each event are emitted; there
    is no identity or sealed value anywhere in the document.
    """
    lines = ['<?xml version="1.0" encoding="UTF-8"?>']
    lines.append(f'<premis:premis xmlns:premis="{_PREMIS_NS}" version="3.0">')
    for event in events:
        lines.extend(_event_to_xml(event, "  "))
    lines.append("</premis:premis>")
    return "\n".join(lines)
