"""The single decision point every read path goes through — the safety heart.

Every visibility question in ledger funnels through :func:`is_visible`. Having one
function answer "may this grant see this policy at this instant?" makes the rule
*provable*: there is exactly one place to audit, exactly one place to deny by
default (simplicity, orthogonality, provability). :func:`disclose` is the sole
constructor of :class:`~ledger.models.DisclosedRecord` used by read paths, so the
no-outing boundary is enforced structurally rather than by convention
(safety, confidentiality).

The decision is a pure function of ``(policy, grant, now, unseal info)`` — no
wall clock, no randomness — so the same inputs always yield the same answer
(predictability, determinability).
"""

from __future__ import annotations

from ledger.errors import AccessDenied
from ledger.models import (
    PUBLIC_GRANT,
    AccessPolicy,
    DisclosedRecord,
    Grant,
    PayloadFile,
    Record,
)


def is_visible(
    policy: AccessPolicy,
    grant: Grant,
    now: str,
    *,
    unseal_at: str | None = None,
    unseal_condition: str | None = None,
    conditions_met: frozenset[str] = frozenset(),
) -> bool:
    """Decide whether ``grant`` may see something at ``policy`` at instant ``now``.

    Deny by default: any case not explicitly permitted below returns ``False``
    (safety, confidentiality). An expired grant is downgraded to the anonymous
    public grant before deciding, so a stale credential never out-lives its trust
    (least privilege).

    Rules:

    * ``PUBLIC`` -- visible to everyone.
    * ``COMMUNITY`` -- a steward, or a grant whose ``levels`` include COMMUNITY.
    * ``STEWARDS`` -- a steward only.
    * ``SEALED_UNTIL`` -- a steward, or ``now`` has reached ``unseal_at`` (which
      must be set); a seal with no date is sealed indefinitely.
    * ``SEALED_CONDITIONAL`` -- a steward, or ``unseal_condition`` is set and is
      present in ``conditions_met``.

    The decision is pure in its arguments (no clock, no randomness) for
    determinism/determinability.
    """
    effective = PUBLIC_GRANT if grant.is_expired(now) else grant

    match policy:
        case AccessPolicy.PUBLIC:
            return True
        case AccessPolicy.COMMUNITY:
            return effective.is_steward or AccessPolicy.COMMUNITY in effective.levels
        case AccessPolicy.STEWARDS:
            return effective.is_steward
        case AccessPolicy.SEALED_UNTIL:
            if effective.is_steward:
                return True
            return unseal_at is not None and now >= unseal_at
        case AccessPolicy.SEALED_CONDITIONAL:
            if effective.is_steward:
                return True
            return unseal_condition is not None and unseal_condition in conditions_met


def is_listable(
    record: Record,
    grant: Grant,
    now: str,
    *,
    conditions_met: frozenset[str] = frozenset(),
) -> bool:
    """Decide whether ``record`` may appear in a listing for ``grant``.

    Listability is the record's *default* policy resolved through
    :func:`is_visible`. A record whose very existence is sealed is not listed:
    there is no padded list with locked rows betraying that something is there
    (confidentiality — the absence of a row leaks nothing).

    A record carries no record-level unseal date or condition, so a record whose
    default policy is sealed is listable only to a steward (deny by default).
    """
    return is_visible(
        record.default_policy,
        grant,
        now,
        conditions_met=conditions_met,
    )


def disclose(
    record: Record,
    grant: Grant,
    now: str,
    *,
    conditions_met: frozenset[str] = frozenset(),
) -> DisclosedRecord:
    """Project ``record`` down to only what ``grant`` may see — the safe read shape.

    This is the ONLY constructor of :class:`~ledger.models.DisclosedRecord` used
    by read paths (browse, search, API, export). It enforces the no-outing rule
    structurally: the result type has no ``identity_ref``, and this function never
    copies identity into ``dublin_core`` or ``fields``.

    Behaviour:

    * If the record is not listable for this grant, raise
      :class:`~ledger.errors.AccessDenied` naming only the record id -- the viewer
      may not even learn the record exists (confidentiality).
    * Include only fields and payloads whose own policy :func:`is_visible` to this
      grant; record the names of everything withheld in ``redactions`` so the
      lossy view is honest about being lossy (honesty, fidelity).
    * Always include ``title`` and ``content_warnings`` -- warnings must surface
      before any render of the underlying content (safety).
    * Pass ``dublin_core`` through unchanged: descriptive metadata is
      collection-level, not per-field sealed -- but never inject identity.
    """
    if not is_listable(record, grant, now, conditions_met=conditions_met):
        # Name only the object, never the protected content (threat model).
        raise AccessDenied(record.record_id)

    visible_fields: dict[str, str] = {}
    withheld: list[str] = []
    for fld in record.fields:
        if is_visible(
            fld.policy,
            grant,
            now,
            unseal_at=fld.unseal_at,
            unseal_condition=fld.unseal_condition,
            conditions_met=conditions_met,
        ):
            visible_fields[fld.name] = fld.value
        else:
            withheld.append(fld.name)

    payloads: list[PayloadFile] = []
    for payload in record.payloads:
        if is_visible(payload.policy, grant, now, conditions_met=conditions_met):
            payloads.append(payload)
        else:
            withheld.append(payload.filename)

    # `dublin_core` is collection-level descriptive metadata; pass it through but
    # never add identity (no-outing rule). `to_dict` already drops empty elements.
    return DisclosedRecord(
        record_id=record.record_id,
        title=record.title,
        dublin_core=record.dublin_core.to_dict(),
        fields=visible_fields,
        payloads=tuple(payloads),
        content_warnings=tuple(record.content_warnings),
        redactions=tuple(withheld),
    )
