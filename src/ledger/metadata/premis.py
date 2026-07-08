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
* **Tamper evidence.** "Append-only" above is an application-level promise, which
  a steward with raw disk access could otherwise defeat by editing ``premis.json``
  directly and re-sealing the bag's tag manifest to match. Every entry also
  carries ``prevHash`` — a :mod:`ledger.chain` hash chain over the log's own
  history — so silently rewriting any past entry changes the chain's head, and
  that head can be compared across replicas (:mod:`ledger.replicate`) or
  published (``/proof``) even when a single, locally-doctored copy still looks
  self-consistent.
* **Interoperability / standards-compliance.** :func:`to_premis_xml` emits valid
  ``premis:premis`` markup so another repository can read our event history.

No-outing rule: a :class:`~ledger.models.PremisEvent` carries an *agent*, an
*outcome*, a *detail*, and an opaque *linked_object* (a content address, record
id, or bag id) — never a contributor identity or a sealed value. This module adds
nothing to that shape; it only orders, serializes, chains, and persists it.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as _sax_escape

from ledger.chain import GENESIS_HASH, ChainVerification, build_chain, chain_head
from ledger.chain import verify_chain as _verify_chain
from ledger.models import PremisEvent, PremisEventType, canonical_json

__all__ = ["PremisLog", "to_premis_xml"]

# Schema history:
#   1 — a bare JSON array of event dicts (no chaining).
#   2 — {"schemaVersion": 2, "entries": [...]}, each entry an event dict plus a
#       "prevHash" chain-link field (FIX-06: tamper-evident hash-chained logs).
_SCHEMA_VERSION = 2

# Characters XML 1.0 forbids even when escaped. PREMIS detail/agent text can carry
# arbitrary operator-supplied content, so strip these before escaping to keep the
# emitted XML well-formed (standards compliance, interoperability, robustness).
_ILLEGAL_XML = re.compile("[^\x09\x0a\x0d\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")


def escape(value: str) -> str:
    """XML-escape ``value`` after removing characters XML 1.0 disallows."""
    return _sax_escape(_ILLEGAL_XML.sub("", value))


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
    """An append-only, hash-chained log of preservation events.

    The list is never exposed by reference: :attr:`events` returns a copy and the
    only mutator is :meth:`record`, which appends. This keeps the history
    tamper-evident in code as well as on disk (auditability, accountability).

    Each event also carries a chain link (a ``prevHash`` computed by
    :mod:`ledger.chain`) that folds in the entry before it, so rewriting any past
    entry — not just the latest one — changes :attr:`head`. :meth:`verify_chain`
    checks the stored links still match; :attr:`head` is the value to compare
    across replicas or publish for independent cross-checking (FIX-06).
    """

    def __init__(
        self,
        events: list[PremisEvent] | None = None,
        prev_hashes: list[str] | None = None,
    ) -> None:
        """Start a log, optionally seeded with prior events (defensively copied).

        ``prev_hashes`` is normally left to be derived: when omitted, a fresh
        chain is built from :data:`~ledger.chain.GENESIS_HASH` as if each event
        had been :meth:`record`-ed in order (also how a legacy, pre-chain log is
        adopted into the chained format on read — see :meth:`from_json`). Pass it
        explicitly only to preserve chain links read verbatim off disk, which is
        what makes tampering with an already-written entry detectable.
        """
        self._events: list[PremisEvent] = list(events) if events is not None else []
        if prev_hashes is not None:
            if len(prev_hashes) != len(self._events):
                raise ValueError("prev_hashes must have the same length as events")
            self._prev_hashes: list[str] = list(prev_hashes)
        else:
            self._prev_hashes = build_chain([e.to_dict() for e in self._events])

    def record(self, event: PremisEvent) -> None:
        """Append one event, chained to the current head. Append-only ->
        auditability/provability; chained -> tamper-evidence (FIX-06)."""
        prev = self.head
        self._events.append(event)
        self._prev_hashes.append(prev)

    @property
    def events(self) -> list[PremisEvent]:
        """A copy of the events in recorded order; mutating it cannot alter the log."""
        return list(self._events)

    @property
    def head(self) -> str:
        """The chain hash of the most recent entry, or :data:`GENESIS_HASH` if empty.

        Recomputed from the events' *content alone* (:func:`ledger.chain.chain_head`)
        — never from the stored ``prevHash`` values, which is what makes this
        sensitive to an edit anywhere in history, not only the latest entry.
        Editing entry *i* without also recomputing every stored ``prevHash`` after
        it is exactly what :meth:`verify_chain` catches; editing entry *i* while
        leaving every ``prevHash`` untouched (a naive disk edit that does not even
        try to stay self-consistent) still moves this value, because it is derived
        fresh each time rather than trusted off the last entry's own link. This is
        what the *next* recorded event will chain from, and the value an
        independent replica or the ``/proof`` page can compare to detect a history
        that was rewritten on this copy alone.
        """
        return chain_head([e.to_dict() for e in self._events])

    def verify_chain(self) -> ChainVerification:
        """Recompute the chain from the events and compare it to their stored links.

        Detects any entry whose content or chain link no longer matches what was
        originally recorded — the tamper-evidence half of an append-only log
        (accountability, provability). See :func:`ledger.chain.verify_chain`.
        """
        return _verify_chain([e.to_dict() for e in self._events], self._prev_hashes)

    def to_json(self) -> str:
        """Serialize to canonical JSON: a schema-versioned envelope over each
        event's dict form plus its chain link.

        Determinism/reproducibility: canonical JSON gives a byte-identical string
        for identical history, so the log hashes the same on every machine.
        """
        entries = [
            {**event.to_dict(), "prevHash": prev}
            for event, prev in zip(self._events, self._prev_hashes, strict=True)
        ]
        return canonical_json({"schemaVersion": _SCHEMA_VERSION, "entries": entries})

    @classmethod
    def from_json(cls, text: str) -> PremisLog:
        """Reconstruct a log from :meth:`to_json` output, preserving order.

        Also reads the legacy (schema 1) bare-array format written before chaining
        existed: those logs have no ``prevHash`` on disk, so a fresh chain is
        built for them from :data:`~ledger.chain.GENESIS_HASH` forward (an
        in-memory migration — nothing is rewritten on disk until the caller next
        calls :meth:`write`). This adopts old logs into the chained format going
        forward; it cannot prove entries recorded before chaining existed were
        untampered (evolvability, with the documented migration risk).
        """
        raw: object = json.loads(text)
        if isinstance(raw, list):
            events = [_event_from_dict(item) for item in raw]
            return cls(events)
        if isinstance(raw, dict):
            version = raw.get("schemaVersion")
            if version != _SCHEMA_VERSION:
                raise ValueError(f"unsupported PREMIS log schema_version: {version!r}")
            entries = raw.get("entries")
            if not isinstance(entries, list):
                raise ValueError("PREMIS log 'entries' must be a list")
            events = [_event_from_dict(item) for item in entries]
            prev_hashes = [str(item.get("prevHash", GENESIS_HASH)) for item in entries]
            return cls(events, prev_hashes=prev_hashes)
        raise ValueError("PREMIS log JSON must be a list (legacy) or a schema-versioned object")

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
