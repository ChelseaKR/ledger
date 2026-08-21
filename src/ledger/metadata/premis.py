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
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as _sax_escape

from ledger.chain import GENESIS_HASH, ChainVerification, build_chain, chain_head
from ledger.chain import verify_chain as _verify_chain
from ledger.errors import PremisContradictionError
from ledger.models import (
    OBJECT_TYPE_CONTENT_ADDRESS,
    PremisEvent,
    PremisEventType,
    PremisRights,
    canonical_json,
)

__all__ = ["IdentificationContradiction", "PremisLog", "to_premis_xml"]

# Schema history:
#   1 — a bare JSON array of event dicts (no chaining).
#   2 — {"schemaVersion": 2, "entries": [...]}, each entry an event dict plus a
#       "prevHash" chain-link field (FIX-06: tamper-evident hash-chained logs).
_SCHEMA_VERSION = 2


# Characters XML 1.0 forbids even when escaped. PREMIS detail/agent text can carry
# arbitrary operator-supplied content, so strip these before escaping to keep the
# emitted XML well-formed (standards compliance, interoperability, robustness).
def _xml_text(value: str) -> str:
    """Return only XML 1.0 legal characters without a risky giant regex range."""
    return "".join(
        char
        for char in value
        if (code := ord(char)) in (0x9, 0xA, 0xD)
        or 0x20 <= code <= 0xD7FF
        or 0xE000 <= code <= 0xFFFD
        or 0x10000 <= code <= 0x10FFFF
    )


def escape(value: str) -> str:
    """XML-escape ``value`` after removing characters XML 1.0 disallows."""
    return _sax_escape(_xml_text(value))


_PREMIS_NS = "http://www.loc.gov/premis/v3"


def _rights_from_dict(data: dict[str, Any]) -> PremisRights:
    """Rebuild a :class:`PremisRights` from its ``to_dict`` form.

    Kept private and mirrors :meth:`PremisRights.to_dict`, so a written rights
    statement round-trips exactly. Missing optional parts default to empty, so a
    minimally-populated statement (only ``rightsBasis``) still reads back.
    """
    acts = data.get("grantedActs", [])
    restrictions = data.get("restrictions", [])
    linked = data.get("linkingObjectIdentifier")
    return PremisRights(
        rights_basis=str(data.get("rightsBasis", "")),
        rights_note=str(data.get("rightsNote", "")),
        granted_acts=tuple(str(a) for a in acts) if isinstance(acts, list) else (),
        restrictions=(
            tuple(str(r) for r in restrictions) if isinstance(restrictions, list) else ()
        ),
        linked_object=str(linked) if linked is not None else None,
    )


def _event_from_dict(data: dict[str, Any]) -> PremisEvent:
    """Rebuild a :class:`PremisEvent` from its ``to_dict`` form.

    Kept private: the canonical on-disk shape is owned here, mirroring
    :meth:`PremisEvent.to_dict` so a written log round-trips exactly.
    """
    object_type = data.get("linkingObjectIdentifierType")
    content_address = data.get("linkingObjectContentAddress")
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
        # Absent on every event written before ADR 0012; ``None`` reads as "untyped",
        # which :attr:`PremisEvent.object_identifier_type` resolves conservatively.
        linked_object_type=str(object_type) if object_type is not None else None,
        linked_content_address=str(content_address) if content_address is not None else None,
    )


def _identification_key(event: PremisEvent) -> tuple[str | None, str | None]:
    """What makes two format-identification events be about the *same* thing.

    The object identifier and the bytes examined, together. Two events about one
    payload whose bytes differ (a revised deposit) are about different things and
    may legitimately disagree; two about the same payload and the same bytes may not
    (ADR 0012). Events written before the address travelled separately carry the
    address *as* the identifier and ``None`` here, so legacy events group by address
    — exactly the keying under which #149 was observed, and therefore the keying
    under which a legacy log's contradiction must still be reportable.
    """
    return (event.linked_object, event.linked_content_address)


def _verdict(event: PremisEvent) -> str:
    """The whole of what an identification event asserts, as one comparable string."""
    return f"{event.outcome}: {event.detail}"


@dataclass(frozen=True)
class IdentificationContradiction:
    """One object whose log asserts more than one format-identification verdict.

    Returned by :meth:`PremisLog.contradictions` rather than raised, because a log
    already on disk — a bag written before ADR 0012, or one doctored by hand — is a
    finding for the steward to see, never a crash for the reader (failure
    transparency). ``verdicts`` lists every distinct ``outcome: detail`` in the
    order recorded, so the steward sees all of them, not whichever came last; and
    ``object_type`` says how the object was keyed (``content-address`` for a legacy
    log, ``ledger-payload`` for a current one). No payload content, no identity.
    """

    object_id: str
    object_type: str | None
    content_address: str | None
    verdicts: tuple[str, ...]
    events: int


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
        *,
        rights: PremisRights | None = None,
    ) -> None:
        """Start a log, optionally seeded with prior events (defensively copied).

        ``prev_hashes`` is normally left to be derived: when omitted, a fresh
        chain is built from :data:`~ledger.chain.GENESIS_HASH` as if each event
        had been :meth:`record`-ed in order (also how a legacy, pre-chain log is
        adopted into the chained format on read — see :meth:`from_json`). Pass it
        explicitly only to preserve chain links read verbatim off disk, which is
        what makes tampering with an already-written entry detectable.

        An optional PREMIS :class:`~ledger.models.PremisRights` statement describes
        the terms under which the object may be used; it sits beside the event
        history rather than in it, since a rights statement is a *standing* fact
        about the object, not a point-in-time event (PREMIS v3 keeps them as
        separate top-level entities). Because it is replaceable by design
        (:meth:`set_rights`), it is *not* covered by the event hash chain — the
        chain proves the event history, not the standing rights statement.
        """
        self._events: list[PremisEvent] = list(events) if events is not None else []
        if prev_hashes is not None:
            if len(prev_hashes) != len(self._events):
                raise ValueError("prev_hashes must have the same length as events")
            self._prev_hashes: list[str] = list(prev_hashes)
        else:
            self._prev_hashes = build_chain([e.to_dict() for e in self._events])
        self._rights: PremisRights | None = rights

    def record(self, event: PremisEvent) -> None:
        """Append one event, chained to the current head. Append-only ->
        auditability/provability; chained -> tamper-evidence (FIX-06).

        A format-identification event is checked first: if the log already holds
        one for the same object and the same bytes with a *different* verdict, it is
        refused with :class:`~ledger.errors.PremisContradictionError` rather than
        appended (ADR 0012). Nothing is written before the check, so a refused event
        leaves the log exactly as it was. A second event that agrees is recorded —
        that is history, not contradiction — and a future re-identification that
        *means* to supersede an earlier verdict must say so through an explicit
        path; none exists yet, and this guard is what keeps one from appearing by
        accident.
        """
        if event.event_type is PremisEventType.FORMAT_IDENTIFICATION:
            self._refuse_contradiction(event)
        prev = self.head
        self._events.append(event)
        self._prev_hashes.append(prev)

    def _refuse_contradiction(self, event: PremisEvent) -> None:
        """Raise if ``event`` would be the second, different verdict for its object."""
        if event.linked_object is None:
            return
        key = _identification_key(event)
        for prior in self._events:
            if prior.event_type is not PremisEventType.FORMAT_IDENTIFICATION:
                continue
            if _identification_key(prior) != key:
                continue
            if _verdict(prior) != _verdict(event):
                raise PremisContradictionError(
                    f"refusing a format-identification event for {event.linked_object}: "
                    "the log already records a different verdict for the same object and "
                    "the same bytes, and a contradiction is never written silently "
                    "(ADR 0012)"
                )

    def contradictions(self) -> list[IdentificationContradiction]:
        """Every object this log asserts more than one identification verdict for.

        Empty for any log :meth:`record` built, because the guard refuses the second
        verdict. Non-empty only for a log read off disk that was written before ADR
        0012 (keyed by content address, where two byte-identical payloads under
        differently-identifying names collided — #149) or edited by hand. Either way
        the reader reports it; it does not average, pick the latest, or raise.
        """
        groups: dict[tuple[str | None, str | None], list[PremisEvent]] = {}
        for event in self._events:
            if event.event_type is not PremisEventType.FORMAT_IDENTIFICATION:
                continue
            if event.linked_object is None:
                continue
            groups.setdefault(_identification_key(event), []).append(event)
        found: list[IdentificationContradiction] = []
        for (object_id, address), events in groups.items():
            verdicts = tuple(dict.fromkeys(_verdict(e) for e in events))
            if len(verdicts) > 1:
                found.append(
                    IdentificationContradiction(
                        object_id=str(object_id),
                        object_type=events[0].object_identifier_type,
                        content_address=address,
                        verdicts=verdicts,
                        events=len(events),
                    )
                )
        return found

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

    @property
    def rights(self) -> PremisRights | None:
        """The standing rights statement for the object, or ``None`` if unset."""
        return self._rights

    def set_rights(self, rights: PremisRights | None) -> None:
        """Attach (or clear) the object's rights statement.

        A rights statement is a standing fact, not an append-only event, so it is
        replaced rather than accumulated: re-declaring rights supersedes the prior
        statement. The event history is untouched (auditability preserved), and the
        hash chain covers only the event history — a rights statement is mutable by
        design, so chaining it would make legitimate rights updates look like
        tampering.
        """
        self._rights = rights

    def to_json(self) -> str:
        """Serialize to canonical JSON: a schema-versioned envelope over each
        event's dict form plus its chain link, with the standing rights statement
        (when present) beside the entries.

        Determinism/reproducibility: canonical JSON gives a byte-identical string
        for identical content, so the log hashes the same on every machine.
        """
        entries = [
            {**event.to_dict(), "prevHash": prev}
            for event, prev in zip(self._events, self._prev_hashes, strict=True)
        ]
        envelope: dict[str, Any] = {"schemaVersion": _SCHEMA_VERSION, "entries": entries}
        if self._rights is not None:
            envelope["rights"] = self._rights.to_dict()
        return canonical_json(envelope)

    @classmethod
    def from_json(cls, text: str) -> PremisLog:
        """Reconstruct a log from :meth:`to_json` output, preserving order.

        Accepts every shape this log has ever written (robustness, round-trip):

        * a bare JSON list of events — the schema-1 form written before chaining
          and before rights existed;
        * ``{"events": [...], "rights": {...}}`` — the transitional pre-chain form
          written while the rights entity existed but chaining did not;
        * ``{"schemaVersion": 2, "entries": [...], "rights"?: {...}}`` — the
          current chained envelope, each entry carrying its ``prevHash`` link.

        Pre-chain logs have no ``prevHash`` on disk, so a fresh chain is built for
        them from :data:`~ledger.chain.GENESIS_HASH` forward (an in-memory
        migration — nothing is rewritten on disk until the caller next calls
        :meth:`write`). This adopts old logs into the chained format going forward;
        it cannot prove entries recorded before chaining existed were untampered
        (evolvability, with the documented migration risk).
        """
        raw: object = json.loads(text)
        if isinstance(raw, list):
            events = [_event_from_dict(item) for item in raw]
            return cls(events)
        if isinstance(raw, dict):
            raw_rights = raw.get("rights")
            rights = _rights_from_dict(raw_rights) if isinstance(raw_rights, dict) else None
            if "schemaVersion" not in raw:
                # Transitional pre-chain form: {"events": [...], "rights": {...}}.
                raw_events = raw.get("events", [])
                if not isinstance(raw_events, list):
                    raise ValueError("PREMIS log 'events' must be a list of events")
                events = [_event_from_dict(item) for item in raw_events]
                return cls(events, rights=rights)
            version = raw.get("schemaVersion")
            if version != _SCHEMA_VERSION:
                raise ValueError(f"unsupported PREMIS log schema_version: {version!r}")
            entries = raw.get("entries")
            if not isinstance(entries, list):
                raise ValueError("PREMIS log 'entries' must be a list")
            events = [_event_from_dict(item) for item in entries]
            prev_hashes = [str(item.get("prevHash", GENESIS_HASH)) for item in entries]
            return cls(events, prev_hashes=prev_hashes, rights=rights)
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
    # PREMIS v3 makes linkingObjectIdentifierType mandatory inside a linking object
    # identifier and lets the element repeat. The object itself comes first, typed
    # explicitly where the writer said (ADR 0012), by the safe inference otherwise,
    # and as "local" — the conventional repository-scoped type — when nothing can be
    # said. The bytes examined follow as a second identifier of their own type, so
    # a consumer can tell the object from its fixity instead of having to conflate
    # them (the conflation behind #149).
    if event.linked_object is not None:
        lines.extend(
            _linking_object_xml(inner, event.object_identifier_type or "local", event.linked_object)
        )
    if event.linked_content_address is not None:
        lines.extend(
            _linking_object_xml(inner, OBJECT_TYPE_CONTENT_ADDRESS, event.linked_content_address)
        )
    lines.append(f"{indent}</premis:event>")
    return lines


def _linking_object_xml(inner: str, identifier_type: str, value: str) -> list[str]:
    """One ``premis:linkingObjectIdentifier`` element with its type and value."""
    return [
        f"{inner}<premis:linkingObjectIdentifier>",
        f"{inner}  <premis:linkingObjectIdentifierType>{escape(identifier_type)}"
        "</premis:linkingObjectIdentifierType>",
        f"{inner}  <premis:linkingObjectIdentifierValue>{escape(value)}"
        "</premis:linkingObjectIdentifierValue>",
        f"{inner}</premis:linkingObjectIdentifier>",
    ]


def _rights_to_xml(rights: PremisRights, indent: str) -> list[str]:
    """Render a rights statement as a ``premis:rights``/``rightsStatement`` element.

    Emits the PREMIS v3 ``rights`` entity: a ``rightsStatement`` with a synthetic
    ``rightsStatementIdentifier`` (local, derived from the linked object), the
    ``rightsBasis``, and a ``rightsGranted`` block carrying each granted ``act`` and
    ``restriction``. Every value is XML-escaped.

    No-outing rule: a rights statement holds only the collection-level terms — a
    basis, a note, granted acts, and restrictions — never a ``rightsHolder`` name or
    any contributor identity, so nothing here can out a person.
    """
    inner = indent + "  "
    inner2 = inner + "  "
    lines = [f"{indent}<premis:rights>"]
    lines.append(f"{inner}<premis:rightsStatement>")
    lines.append(f"{inner2}<premis:rightsStatementIdentifier>")
    lines.append(
        f"{inner2}  <premis:rightsStatementIdentifierType>local"
        "</premis:rightsStatementIdentifierType>"
    )
    ident = rights.linked_object if rights.linked_object is not None else "rights"
    lines.append(
        f"{inner2}  <premis:rightsStatementIdentifierValue>{escape(ident)}"
        "</premis:rightsStatementIdentifierValue>"
    )
    lines.append(f"{inner2}</premis:rightsStatementIdentifier>")
    lines.append(f"{inner2}<premis:rightsBasis>{escape(rights.rights_basis)}</premis:rightsBasis>")
    if rights.rights_note:
        lines.append(f"{inner2}<premis:rightsNote>{escape(rights.rights_note)}</premis:rightsNote>")
    if rights.granted_acts or rights.restrictions:
        lines.append(f"{inner2}<premis:rightsGranted>")
        for act in rights.granted_acts:
            lines.append(f"{inner2}  <premis:act>{escape(act)}</premis:act>")
        for restriction in rights.restrictions:
            lines.append(
                f"{inner2}  <premis:restriction>{escape(restriction)}</premis:restriction>"
            )
        lines.append(f"{inner2}</premis:rightsGranted>")
    lines.append(f"{inner}</premis:rightsStatement>")
    lines.append(f"{indent}</premis:rights>")
    return lines


def to_premis_xml(events: Sequence[PremisEvent], rights: PremisRights | None = None) -> str:
    """Render ``events`` (and an optional ``rights`` statement) as PREMIS v3 XML.

    Interoperability/standards-compliance: the result is a ``premis:premis`` root
    in the PREMIS v3 namespace with one ``premis:event`` child per event and, when
    supplied, a ``premis:rights`` statement, so other preservation systems can
    ingest both our history and the terms of use. All text is XML-escaped.

    No-outing rule: only the safe, opaque fields of each event and the
    collection-level rights terms are emitted; there is no identity, rights-holder,
    or sealed value anywhere in the document.
    """
    lines = ['<?xml version="1.0" encoding="UTF-8"?>']
    lines.append(f'<premis:premis xmlns:premis="{_PREMIS_NS}" version="3.0">')
    for event in events:
        lines.extend(_event_to_xml(event, "  "))
    if rights is not None:
        lines.extend(_rights_to_xml(rights, "  "))
    lines.append("</premis:premis>")
    return "\n".join(lines)
