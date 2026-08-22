"""Preservation-metadata honesty (mission requirement #4).

This portfolio's dominant defect, named explicitly in the mission brief, is
"absence rendered as a value" — a missing or unrun check displayed as if it
were a positive result. In a digital-preservation context that is a claim
about *evidentiary integrity*, not a cosmetic gap, so this module is the
single deterministic source of truth an AI finding aid's fixity claims are
verified against — never derived from the model's own words.

:func:`payload_fixity_status` answers "what does the preservation record
actually say about this file's fixity", from PREMIS fixity-check events only,
and returns one of exactly three honest states. There is no fourth state
meaning "assume it's fine": a payload with no fixity-check event in the
context it was given returns :data:`NOT_YET_CHECKED`, never
:data:`VERIFIED`.
"""

from __future__ import annotations

from ledger.ai.context import GroundedContext
from ledger.models import PremisEventType, payload_object_id

__all__ = ["FAILED", "NOT_YET_CHECKED", "VERIFIED", "payload_fixity_status"]

#: The only honest states a fixity claim may assert. A model claim outside
#: this vocabulary -- "authentic", "confirmed original", "verified forever" --
#: fails grounding (see :mod:`ledger.ai.grounding`) because it will never
#: match text derived from one of these three strings.
NOT_YET_CHECKED = "fixity has not yet been checked for this file"
VERIFIED = "fixity was verified"
FAILED = "a fixity check failed for this file"


def payload_fixity_status(context: GroundedContext, filename: str) -> str:
    """The honest fixity state of one visible payload in ``context``.

    Derived ONLY from that payload's own PREMIS ``FIXITY_CHECK`` events —
    never assumed from the payload's mere presence in the record, and never
    upgraded by an unrelated event (ingest, replication, format
    identification). If ``filename`` is not a payload visible in ``context``
    at all, this returns :data:`NOT_YET_CHECKED` rather than raising: there is
    nothing to honestly assert either way, and "not yet checked" is the
    narrowest true statement.

    When more than one fixity-check event exists for the payload (e.g. a
    scheduled re-verification after the original ingest check), the most
    recent one in log order wins — a corrected outcome must not be masked by
    an earlier one.
    """
    payload = next((p for p in context.disclosed.payloads if p.filename == filename), None)
    if payload is None:
        return NOT_YET_CHECKED

    object_id = payload_object_id(context.record_id, filename)
    address = str(payload.address)
    relevant = [
        event
        for event in context.events
        if event.event_type is PremisEventType.FIXITY_CHECK
        and event.linked_object in (object_id, address, context.record_id)
    ]
    if not relevant:
        return NOT_YET_CHECKED

    latest = relevant[-1]
    if latest.outcome == "success":
        return f"{VERIFIED} on {latest.event_datetime}"
    return f"{FAILED} (last checked {latest.event_datetime})"
