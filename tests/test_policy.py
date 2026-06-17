"""Disclosure truth-table tests for the single visibility decision point.

These tests pin down :func:`ledger.access.is_visible` and :func:`ledger.access.disclose`
across every :class:`~ledger.models.AccessPolicy` level and every viewer shape, because
this one function is the place the no-outing rule is decided. The matrix is exhaustive on
purpose: a quiet change to one ``case`` is the kind of regression that would silently widen
access, so every cell is asserted (provability, safety).

All tests are marked ``disclosure`` so the safety-critical suite can be run on its own.
"""

from __future__ import annotations

import pytest

from ledger.access import disclose, is_visible
from ledger.access.grants import anonymous, build_grant, community_member, steward
from ledger.errors import AccessDenied
from ledger.models import (
    AccessPolicy,
    ContentAddress,
    DublinCore,
    Field,
    Grant,
    PayloadFile,
    Record,
)

pytestmark = pytest.mark.disclosure

# A fixed clock so every decision is reproducible; the unseal date sits in between.
_NOW = "2026-06-16T00:00:00Z"
_PAST = "2020-01-01T00:00:00Z"
_FUTURE = "2099-01-01T00:00:00Z"


def _anon() -> Grant:
    return anonymous()


def _member() -> Grant:
    return community_member("member")


def _steward() -> Grant:
    return steward("steward")


# --- PUBLIC: visible to everyone -------------------------------------------


@pytest.mark.parametrize("grant", [_anon(), _member(), _steward()])
def test_public_is_visible_to_everyone(grant: Grant) -> None:
    """PUBLIC is visible to anonymous, community members, and stewards alike."""
    assert is_visible(AccessPolicy.PUBLIC, grant, _NOW) is True


# --- COMMUNITY: steward or a grant carrying COMMUNITY ------------------------


def test_community_hidden_from_anonymous() -> None:
    """COMMUNITY is not visible to the anonymous public (deny by default)."""
    assert is_visible(AccessPolicy.COMMUNITY, _anon(), _NOW) is False


def test_community_visible_to_member() -> None:
    """COMMUNITY is visible to a grant whose levels include COMMUNITY."""
    assert is_visible(AccessPolicy.COMMUNITY, _member(), _NOW) is True


def test_community_visible_to_steward() -> None:
    """A steward sees COMMUNITY material (stewards satisfy every level)."""
    assert is_visible(AccessPolicy.COMMUNITY, _steward(), _NOW) is True


# --- STEWARDS: stewards only ------------------------------------------------


def test_stewards_hidden_from_anonymous() -> None:
    """STEWARDS is not visible to the anonymous public."""
    assert is_visible(AccessPolicy.STEWARDS, _anon(), _NOW) is False


def test_stewards_hidden_from_member() -> None:
    """STEWARDS is not visible to a plain community member (deny by default)."""
    assert is_visible(AccessPolicy.STEWARDS, _member(), _NOW) is False


def test_stewards_visible_to_steward() -> None:
    """STEWARDS is visible only to a steward grant."""
    assert is_visible(AccessPolicy.STEWARDS, _steward(), _NOW) is True


# --- SEALED_UNTIL: steward, or now has reached unseal_at --------------------


def test_sealed_until_hidden_with_no_date() -> None:
    """A SEALED_UNTIL field with no date is sealed indefinitely for non-stewards."""
    assert is_visible(AccessPolicy.SEALED_UNTIL, _anon(), _NOW, unseal_at=None) is False


def test_sealed_until_hidden_before_date() -> None:
    """Before ``unseal_at``, a non-steward cannot see a SEALED_UNTIL field."""
    assert is_visible(AccessPolicy.SEALED_UNTIL, _member(), _NOW, unseal_at=_FUTURE) is False


def test_sealed_until_visible_after_date() -> None:
    """At or after ``unseal_at``, anyone may see a SEALED_UNTIL field (time release)."""
    assert is_visible(AccessPolicy.SEALED_UNTIL, _anon(), _NOW, unseal_at=_PAST) is True


def test_sealed_until_visible_exactly_at_date() -> None:
    """The unseal is inclusive: visibility flips on at exactly ``unseal_at``."""
    assert is_visible(AccessPolicy.SEALED_UNTIL, _anon(), _NOW, unseal_at=_NOW) is True


def test_sealed_until_visible_to_steward_before_date() -> None:
    """A steward sees a SEALED_UNTIL field even before its unseal date (administration)."""
    assert is_visible(AccessPolicy.SEALED_UNTIL, _steward(), _NOW, unseal_at=_FUTURE) is True


# --- SEALED_CONDITIONAL: steward, or condition met --------------------------


def test_sealed_conditional_hidden_when_condition_absent() -> None:
    """SEALED_CONDITIONAL stays sealed when the condition is not in conditions_met."""
    assert (
        is_visible(
            AccessPolicy.SEALED_CONDITIONAL,
            _member(),
            _NOW,
            unseal_condition="estate-cleared",
            conditions_met=frozenset(),
        )
        is False
    )


def test_sealed_conditional_hidden_when_no_condition_named() -> None:
    """With no ``unseal_condition`` set, the field is sealed for non-stewards (deny by default)."""
    assert (
        is_visible(
            AccessPolicy.SEALED_CONDITIONAL,
            _member(),
            _NOW,
            unseal_condition=None,
            conditions_met=frozenset({"estate-cleared"}),
        )
        is False
    )


def test_sealed_conditional_visible_when_condition_met() -> None:
    """SEALED_CONDITIONAL opens once its condition appears in conditions_met."""
    assert (
        is_visible(
            AccessPolicy.SEALED_CONDITIONAL,
            _member(),
            _NOW,
            unseal_condition="estate-cleared",
            conditions_met=frozenset({"estate-cleared"}),
        )
        is True
    )


def test_sealed_conditional_visible_to_steward() -> None:
    """A steward sees a SEALED_CONDITIONAL field regardless of conditions met."""
    assert (
        is_visible(
            AccessPolicy.SEALED_CONDITIONAL,
            _steward(),
            _NOW,
            unseal_condition="estate-cleared",
            conditions_met=frozenset(),
        )
        is True
    )


# --- expired grant downgrades to anonymous ----------------------------------


def test_expired_grant_loses_community_access() -> None:
    """An expired community grant is downgraded to anonymous before deciding."""
    expired = build_grant(
        "former-member",
        levels=(AccessPolicy.PUBLIC, AccessPolicy.COMMUNITY),
        expires_at=_PAST,
    )
    assert is_visible(AccessPolicy.COMMUNITY, expired, _NOW) is False
    # PUBLIC still resolves true even for an expired grant (it is public to all).
    assert is_visible(AccessPolicy.PUBLIC, expired, _NOW) is True


def test_expired_steward_grant_loses_steward_access() -> None:
    """An expired steward grant no longer satisfies STEWARDS or sealed levels."""
    expired = build_grant(
        "former-steward",
        levels=(AccessPolicy.PUBLIC, AccessPolicy.COMMUNITY, AccessPolicy.STEWARDS),
        is_steward=True,
        expires_at=_PAST,
    )
    assert is_visible(AccessPolicy.STEWARDS, expired, _NOW) is False
    assert is_visible(AccessPolicy.SEALED_UNTIL, expired, _NOW, unseal_at=_FUTURE) is False
    assert is_visible(AccessPolicy.SEALED_CONDITIONAL, expired, _NOW, unseal_condition="x") is False


def test_unexpired_grant_keeps_access() -> None:
    """A grant whose expiry is in the future retains its levels."""
    valid = build_grant(
        "member",
        levels=(AccessPolicy.PUBLIC, AccessPolicy.COMMUNITY),
        expires_at=_FUTURE,
    )
    assert is_visible(AccessPolicy.COMMUNITY, valid, _NOW) is True


# --- deny-by-default: a grant gets nothing it was not explicitly given -------


def test_deny_by_default_empty_levels_grant() -> None:
    """A grant with no levels at all sees nothing but PUBLIC (deny by default)."""
    bare = Grant(subject="nobody", levels=frozenset())
    assert is_visible(AccessPolicy.PUBLIC, bare, _NOW) is True
    assert is_visible(AccessPolicy.COMMUNITY, bare, _NOW) is False
    assert is_visible(AccessPolicy.STEWARDS, bare, _NOW) is False
    assert is_visible(AccessPolicy.SEALED_UNTIL, bare, _NOW, unseal_at=None) is False
    assert is_visible(AccessPolicy.SEALED_CONDITIONAL, bare, _NOW, unseal_condition=None) is False


def test_member_is_not_a_steward() -> None:
    """Holding COMMUNITY never implies stewardship: a member cannot see sealed material."""
    member = _member()
    assert is_visible(AccessPolicy.STEWARDS, member, _NOW) is False
    assert is_visible(AccessPolicy.SEALED_UNTIL, member, _NOW, unseal_at=_FUTURE) is False


# --- disclose() over a multi-policy record ----------------------------------


def _mixed_record() -> Record:
    """A record whose default is PUBLIC but whose fields span several policies."""
    return Record(
        title="Oral history: a strike kitchen",
        record_id="rec-mixed",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(
            title=["Oral history: a strike kitchen"],
            description=["A community account of mutual aid during a 1980s strike."],
        ),
        fields=[
            Field(name="story", value="We cooked for four hundred.", policy=AccessPolicy.PUBLIC),
            Field(name="venue", value="The union hall", policy=AccessPolicy.COMMUNITY),
            Field(name="contact_log", value="steward notes", policy=AccessPolicy.STEWARDS),
            Field(
                name="diary",
                value="opens later",
                policy=AccessPolicy.SEALED_UNTIL,
                unseal_at=_FUTURE,
            ),
        ],
        payloads=[
            PayloadFile(
                filename="photo.jpg",
                address=ContentAddress.parse("sha256:" + "a" * 64),
                policy=AccessPolicy.COMMUNITY,
            ),
        ],
        content_warnings=["incarceration"],
    )


def test_disclose_anonymous_sees_only_public_field() -> None:
    """An anonymous viewer of a mixed record sees only the PUBLIC field; the rest are listed."""
    dr = disclose(_mixed_record(), _anon(), _NOW)
    assert dr.fields == {"story": "We cooked for four hundred."}
    # Every withheld field/payload is named, never valued.
    assert set(dr.redactions) == {"venue", "contact_log", "diary", "photo.jpg"}
    assert "steward notes" not in dr.redactions
    assert "opens later" not in dr.redactions


def test_disclose_member_sees_public_and_community() -> None:
    """A community member sees PUBLIC + COMMUNITY fields and the community payload."""
    dr = disclose(_mixed_record(), _member(), _NOW)
    assert dr.fields == {"story": "We cooked for four hundred.", "venue": "The union hall"}
    assert [p.filename for p in dr.payloads] == ["photo.jpg"]
    assert set(dr.redactions) == {"contact_log", "diary"}


def test_disclose_steward_sees_all_fields() -> None:
    """A steward sees every field, including the sealed diary, with nothing withheld."""
    dr = disclose(_mixed_record(), _steward(), _NOW)
    assert dr.fields == {
        "story": "We cooked for four hundred.",
        "venue": "The union hall",
        "contact_log": "steward notes",
        "diary": "opens later",
    }
    assert dr.redactions == ()


def test_disclose_always_includes_title_and_warnings() -> None:
    """``title`` and ``content_warnings`` surface for every grant, even the narrowest."""
    for grant in (_anon(), _member(), _steward()):
        dr = disclose(_mixed_record(), grant, _NOW)
        assert dr.title == "Oral history: a strike kitchen"
        assert dr.content_warnings == ("incarceration",)


def test_disclose_carries_no_identity_ref_attribute() -> None:
    """The disclosed shape structurally has no identity_ref to leak (no-outing rule)."""
    dr = disclose(_mixed_record(), _steward(), _NOW)
    assert not hasattr(dr, "identity_ref")


def test_disclose_denies_anonymous_a_fully_sealed_record() -> None:
    """A record whose default policy is sealed is not even listable to anonymous."""
    sealed = Record(
        title="Sealed testimony",
        record_id="rec-sealed",
        default_policy=AccessPolicy.SEALED_UNTIL,
        fields=[Field(name="body", value="protected", policy=AccessPolicy.SEALED_UNTIL)],
    )
    with pytest.raises(AccessDenied) as excinfo:
        disclose(sealed, _anon(), _NOW)
    # The error names only the record id, never the protected value (threat model).
    assert "rec-sealed" in str(excinfo.value)
    assert "protected" not in str(excinfo.value)


def test_disclose_sealed_record_visible_to_steward() -> None:
    """The same sealed record IS disclosable to a steward (selective, not absolute)."""
    sealed = Record(
        title="Sealed testimony",
        record_id="rec-sealed",
        default_policy=AccessPolicy.SEALED_UNTIL,
        fields=[Field(name="body", value="protected", policy=AccessPolicy.STEWARDS)],
    )
    dr = disclose(sealed, _steward(), _NOW)
    assert dr.fields == {"body": "protected"}
