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

from datetime import UTC

from ledger.errors import AccessDenied
from ledger.models import (
    PUBLIC_GRANT,
    AccessPolicy,
    DisclosedRecord,
    Grant,
    PayloadFile,
    Record,
    Redaction,
    parse_iso,
)


def _unseal_reached(now: str, unseal_at: str) -> bool:
    """True iff the instant ``now`` has reached ``unseal_at``.

    Timestamps are parsed to timezone-aware datetimes and compared
    *chronologically*, never lexicographically: comparing ISO strings with ``>=``
    is correct only for identically-formatted UTC values, and a corrupted,
    date-only, or differently-offset ``unseal_at`` could otherwise make a sealed
    value spring open. Any parse or comparison failure fails CLOSED (returns
    ``False``), so bad data keeps a record sealed rather than exposing it
    (safety, fail-closed, robustness).
    """
    try:
        return parse_iso(now) >= parse_iso(unseal_at)
    except (ValueError, TypeError):
        return False


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
    * ``SEALED_UNTIL`` -- two cases. With an ``unseal_at`` date it is a *temporal
      embargo* that binds EVERY tier, including stewards, until ``now`` reaches the
      date, after which it opens to all; a steward does not bypass it (an embargo is
      a promise to time, not an access level). With no date it is an indefinite
      access-level seal that a steward may read.
    * ``SEALED_CONDITIONAL`` -- a steward, or ``unseal_condition`` is set and is
      present in ``conditions_met``.
    * ``SEALED`` -- absolute: visible to no one, not even a steward. There is no
      grant that satisfies it.

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
            if unseal_at is not None:
                # A *temporal* seal ("sealed until <date>") is an embargo: a promise
                # made to time, not an access level. It binds EVERY tier, including
                # stewards, until the date passes — then it opens to all. A steward
                # who must reach embargoed content before the date does so through an
                # explicit, logged mechanism, not by silently bypassing the seal
                #
                # NOTE: this is a policy check on plaintext, not an encryption
                # boundary — a seized disk yields the value immediately regardless
                # of how far away unseal_at is (unlike absolute SEALED, which is
                # encrypted at rest; see ingest.py). Whether a genuine
                # cryptographic time-lock could close that gap is explored,
                # research-first and not yet a build decision, in
                # docs/audits/crypto-design-review-embargo-timelock.md (EXP-12).
                # (fail-closed; honours the date the contributor was promised).
                return _unseal_reached(now, unseal_at)
            # An indefinite seal (no date) is an access-level seal a steward may
            # read, as the threat model documents.
            return effective.is_steward
        case AccessPolicy.SEALED_CONDITIONAL:
            if effective.is_steward:
                return True
            return unseal_condition is not None and unseal_condition in conditions_met
        case AccessPolicy.SEALED:
            # An ABSOLUTE seal: restricted from everyone, including stewards. No
            # grant satisfies it — there is no read path on which it is disclosed
            # (the "seal from everyone" tier; such values are encrypted at rest).
            return False
        case _:
            # Unknown enum-like values are never visible. This explicit fallback
            # preserves the deny-by-default contract for malformed runtime input.
            return False  # type: ignore[unreachable]


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
    withheld: list[Redaction] = []
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
            withheld.append(
                Redaction(
                    fld.name,
                    withheld_reason(fld.policy, fld.unseal_at, now=now),
                    fld.policy.value,
                )
            )

    payloads: list[PayloadFile] = []
    for payload in record.payloads:
        if is_visible(payload.policy, grant, now, conditions_met=conditions_met):
            payloads.append(payload)
        else:
            withheld.append(
                Redaction(
                    payload.filename, withheld_reason(payload.policy, None), payload.policy.value
                )
            )

    # `dublin_core` is collection-level descriptive metadata; pass it through but
    # never add identity (no-outing rule). `to_dict` already drops empty elements.
    return DisclosedRecord(
        record_id=record.record_id,
        title=record.title,
        dublin_core=record.dublin_core.to_dict(),
        fields=visible_fields,
        payloads=tuple(payloads),
        content_warnings=tuple(record.content_warnings),
        withheld=tuple(withheld),
    )


def _embargo_countdown(now: str, unseal_at: str) -> str:
    """A plain, honest " (opens …)" suffix for a temporal embargo, or "" if reached.

    Turns a bare "sealed until <date>" into a live promise a reader can act on
    (user research C2 — "an embargo should say how long, not just that it exists").
    Derived only from the already-shown embargo date and the public ``now``, so it
    leaks nothing new. Day-granular and inclusive of date-only ``unseal_at``.
    """
    try:
        # A date-only ``unseal_at`` parses naive while a ``…Z`` ``now`` parses aware;
        # normalize both to UTC so the subtraction is always valid.
        until = parse_iso(unseal_at)
        current = parse_iso(now)
        if until.tzinfo is None:
            until = until.replace(tzinfo=UTC)
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        days = (until - current).days
    except (ValueError, TypeError):
        return ""
    if days <= 0:
        return " (opens today)"
    if days == 1:
        return " (opens tomorrow)"
    return f" (opens in {days} days)"


def withheld_reason(policy: AccessPolicy, unseal_at: str | None, *, now: str | None = None) -> str:
    """A safe, human label for *why* a field/payload is withheld — never its value.

    The phrasing is plain (user research P1-3): a legitimate viewer should be able
    to tell "not for you yet" (community/steward) from "locked until a date" from
    "restricted from everyone", without the label leaking the content. A read path
    serving an outsider generalizes this to a count (P2-2). When ``now`` is given for
    a dated temporal seal, a live countdown ("opens in N days") is appended so the
    embargo is an honest promise to a time, not just a label (C2).
    """
    match policy:
        case AccessPolicy.COMMUNITY:
            return "shared with community members"
        case AccessPolicy.STEWARDS:
            return "restricted to stewards"
        case AccessPolicy.SEALED_UNTIL:
            if unseal_at:
                countdown = _embargo_countdown(now, unseal_at) if now else ""
                return f"sealed until {unseal_at[:10]}{countdown}"
            return "sealed (no opening date set)"
        case AccessPolicy.SEALED_CONDITIONAL:
            return "sealed until a condition is met"
        case AccessPolicy.SEALED:
            return "sealed from everyone, including stewards"
        case _:
            return "restricted"
