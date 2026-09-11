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

from ledger.arrangement import Arrangement
from ledger.errors import AccessDenied
from ledger.models import (
    PUBLIC_GRANT,
    AccessPolicy,
    ArchivalContainer,
    DisclosedContainer,
    DisclosedRecord,
    Grant,
    PayloadFile,
    PlacementStep,
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

    if policy is AccessPolicy.PUBLIC:
        return True
    if policy is AccessPolicy.COMMUNITY:
        return effective.is_steward or AccessPolicy.COMMUNITY in effective.levels
    if policy is AccessPolicy.STEWARDS:
        return effective.is_steward
    if policy is AccessPolicy.SEALED_UNTIL:
        if unseal_at is not None:
            # A temporal seal is a promise to time and binds every viewer tier.
            return _unseal_reached(now, unseal_at)
        return effective.is_steward
    if policy is AccessPolicy.SEALED_CONDITIONAL:
        return effective.is_steward or (
            unseal_condition is not None and unseal_condition in conditions_met
        )
    # SEALED and malformed runtime values are both denied by default.
    return False


# --- arrangement: the chain, and the one rule it obeys ----------------------
#
# #202. A record can now sit inside a collection, and a collection inside
# nothing (a series sits inside a collection). Description and policy flow
# downward, and **the flow can only ever narrow**. That is enforced here, in the
# same module as every other disclosure decision, and it is enforced as a
# logical AND rather than as a comparison between policies:
#
#     visible(record) == is_visible(record's own policy)
#                        AND is_visible(ancestor 1's records_policy)
#                        AND is_visible(ancestor 2's records_policy)
#
# An AND has no ordering to get wrong. There is no "which of PUBLIC and
# SEALED_CONDITIONAL is narrower" question to answer, no lattice, and no case
# where a broad container makes a narrow record more visible — the sentinel
# #202 asks for is satisfied by the shape of the expression, not by a table.
#
# Everything fails closed. A caller that does not supply an arrangement cannot
# apply a placed record's ceiling, so a placed record is invisible to it; an
# unresolvable chain (a missing container, a corrupt parent link) is invisible
# to everyone. Both are deliberate: a read path that has not been taught about
# arrangement must show nothing rather than show it unclamped.


def container_is_visible(
    container: ArchivalContainer,
    grant: Grant,
    now: str,
    *,
    arrangement: Arrangement | None = None,
    conditions_met: frozenset[str] = frozenset(),
) -> bool:
    """Whether ``grant`` may know this container's *own description* exists.

    A container's title and scope note are their own disclosure: "2019 raid
    testimony, deposited by Casa Abierta" outs by aggregation even when every
    record inside it is sealed. So this reads ``container.policy``, which is
    separate from the ``records_policy`` ceiling the container puts over what it
    holds.

    Narrowing applies here too: a container is visible only if every container
    above it is. Without that, a public series under a sealed collection would
    be a hole in the chain, and the scope note a series inherits could come from
    an ancestor this viewer may not see.

    With ``arrangement`` omitted the container is resolved against *itself*
    alone, which answers correctly for a collection (it has no ancestors) and
    denies a series (its parent is not there to consult). Same rule as
    :func:`arrangement_permits`: a ceiling that cannot be applied is not
    assumed away.
    """
    graph = arrangement if arrangement is not None else Arrangement.from_containers([container])
    chain = graph.chain(container.container_id)
    if chain is None:
        return False
    return all(
        _own_description_visible(node, grant, now, conditions_met=conditions_met) for node in chain
    )


def _own_description_visible(
    container: ArchivalContainer,
    grant: Grant,
    now: str,
    *,
    conditions_met: frozenset[str],
) -> bool:
    """One container's own-description policy, resolved through :func:`is_visible`."""
    return is_visible(
        container.policy,
        grant,
        now,
        unseal_at=container.unseal_at,
        unseal_condition=container.unseal_condition,
        conditions_met=conditions_met,
    )


def _records_permitted(
    container: ArchivalContainer,
    grant: Grant,
    now: str,
    *,
    conditions_met: frozenset[str],
) -> bool:
    """One container's ceiling over the records placed in it or below it."""
    return is_visible(
        container.records_policy,
        grant,
        now,
        unseal_at=container.records_unseal_at,
        unseal_condition=container.records_unseal_condition,
        conditions_met=conditions_met,
    )


def arrangement_permits(
    placement: str | None,
    grant: Grant,
    now: str,
    *,
    arrangement: Arrangement | None = None,
    conditions_met: frozenset[str] = frozenset(),
) -> bool:
    """Whether every container above a record lets ``grant`` see it — a pure AND.

    * An **unplaced** record (``placement is None``) has an empty chain, and an
      empty AND is ``True``: it behaves exactly as it did before #202, which is
      what lets an existing flat archive keep working untouched.
    * A **placed** record with no ``arrangement`` supplied is ``False``. The
      caller cannot apply a ceiling it was not given, and serving the record
      unclamped is the one outcome that widens.
    * An **unresolvable** chain is ``False`` for the same reason: a dangling
      placement is a ceiling of unknown height.

    This function can only ever remove visibility. It never consults the
    record's own policy, so it cannot grant anything the record did not already
    permit (provability).
    """
    if placement is None:
        return True
    if arrangement is None:
        return False
    chain = arrangement.chain(placement)
    if chain is None:
        return False
    return all(
        _records_permitted(node, grant, now, conditions_met=conditions_met) for node in chain
    )


def visible_placement(
    placement: str | None,
    grant: Grant,
    now: str,
    *,
    arrangement: Arrangement | None = None,
    conditions_met: frozenset[str] = frozenset(),
) -> tuple[PlacementStep, ...]:
    """The root-first arrangement chain, trimmed to what ``grant`` may describe.

    Separate from :func:`arrangement_permits` on purpose, and asymmetric with
    it. Being allowed to *see a record* is not being allowed to *name the
    container it is in*: a public flyer can sit in a collection whose title is
    the thing that would out its depositor. So a record whose chain permits it
    through can still come back with an empty placement, and a reader is told
    nothing — not that a container exists, not that one is withheld.

    Because container visibility narrows down the chain
    (:func:`container_is_visible`), the visible part is always a prefix: there
    is no case where a viewer is shown a series but not the collection holding
    it.
    """
    if placement is None or arrangement is None:
        return ()
    chain = arrangement.chain(placement)
    if chain is None:
        return ()
    steps: list[PlacementStep] = []
    for node in chain:
        if not _own_description_visible(node, grant, now, conditions_met=conditions_met):
            break
        steps.append(PlacementStep(node.container_id, node.title, node.level))
    return tuple(steps)


def disclose_container(
    container: ArchivalContainer,
    grant: Grant,
    now: str,
    *,
    arrangement: Arrangement | None = None,
    conditions_met: frozenset[str] = frozenset(),
) -> DisclosedContainer:
    """Project ``container`` to the only shape a read path may emit.

    The container analogue of :func:`disclose`, and the sole constructor of
    :class:`~ledger.models.DisclosedContainer`. Raises
    :class:`~ledger.errors.AccessDenied` naming only the container id when the
    viewer may not know it exists, so a hidden container and an absent one are
    the same answer (#202's "empty versus withheld has to be indistinguishable
    from outside").

    A container with no scope note of its own inherits the nearest **visible**
    ancestor's, and says so (``inherited_scope``). Inheritance walks only the
    visible chain: an invisible collection's prose never arrives through a
    visible series. No policy value and no count of holdings is carried out.
    """
    if not container_is_visible(
        container, grant, now, arrangement=arrangement, conditions_met=conditions_met
    ):
        raise AccessDenied(container.container_id)
    ancestors = visible_placement(
        container.parent_id, grant, now, arrangement=arrangement, conditions_met=conditions_met
    )
    scope = container.scope_and_content
    inherited = False
    if not scope and arrangement is not None:
        chain = arrangement.chain(container.container_id) or ()
        for node in reversed(chain[:-1]):
            if node.scope_and_content and _own_description_visible(
                node, grant, now, conditions_met=conditions_met
            ):
                scope = node.scope_and_content
                inherited = True
                break
    return DisclosedContainer(
        container_id=container.container_id,
        title=container.title,
        level=container.level,
        scope_and_content=scope,
        extent=container.extent,
        dates=container.dates,
        inherited_scope=inherited,
        ancestors=ancestors,
    )


def is_listable(
    record: Record,
    grant: Grant,
    now: str,
    *,
    conditions_met: frozenset[str] = frozenset(),
    arrangement: Arrangement | None = None,
) -> bool:
    """Decide whether ``record`` may appear in a listing for ``grant``.

    Listability is the record's *default* policy resolved through
    :func:`is_visible`, **and** every ceiling above it resolved through
    :func:`arrangement_permits`. A record whose very existence is sealed — by
    its own policy or by the collection it sits in — is not listed: there is no
    padded list with locked rows betraying that something is there
    (confidentiality — the absence of a row leaks nothing).

    A record carries no record-level unseal date or condition, so a record whose
    default policy is sealed is listable only to a steward (deny by default).
    """
    return is_visible(
        record.default_policy,
        grant,
        now,
        conditions_met=conditions_met,
    ) and arrangement_permits(
        record.placement,
        grant,
        now,
        arrangement=arrangement,
        conditions_met=conditions_met,
    )


def disclose(
    record: Record,
    grant: Grant,
    now: str,
    *,
    conditions_met: frozenset[str] = frozenset(),
    arrangement: Arrangement | None = None,
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
    * Carry the record's arrangement chain only as far as this viewer may
      describe it (:func:`visible_placement`). Being shown a record is not being
      shown the name of the collection it is in, and a record placed in a
      container the viewer cannot see comes back indistinguishable from an
      unarranged one (#202).
    """
    if not is_listable(record, grant, now, conditions_met=conditions_met, arrangement=arrangement):
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
        placement=visible_placement(
            record.placement,
            grant,
            now,
            arrangement=arrangement,
            conditions_met=conditions_met,
        ),
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
    if policy is AccessPolicy.COMMUNITY:
        return "shared with community members"
    if policy is AccessPolicy.STEWARDS:
        return "restricted to stewards"
    if policy is AccessPolicy.SEALED_UNTIL:
        if unseal_at:
            countdown = _embargo_countdown(now, unseal_at) if now else ""
            return f"sealed until {unseal_at[:10]}{countdown}"
        return "sealed (no opening date set)"
    if policy is AccessPolicy.SEALED_CONDITIONAL:
        return "sealed until a condition is met"
    if policy is AccessPolicy.SEALED:
        return "sealed from everyone, including stewards"
    return "restricted"
