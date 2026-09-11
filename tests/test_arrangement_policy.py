"""The arrangement resolver: it may narrow, and it may never widen (#202).

`#202 <https://github.com/ChelseaKR/ledger/issues/202>`_ calls this "the
safety-critical part" and says it should land first and alone, because every
other surface depends on the answer being right. These are the tests that make
the answer checkable:

* **The narrowing law.** For every pair of (record policy, container ceiling)
  and every viewer, a record inside a container is visible only where it would
  have been visible unplaced *and* the ceiling permits. The whole truth table is
  asserted, including the row #202 names explicitly — a container broader than
  the record it holds, which must not widen it.
* **Fail-closed.** A dangling placement, a corrupt parent link, a hierarchy
  deeper than the vocabulary allows, and a read path that was never given the
  arrangement at all: each of those denies the record. A ceiling that cannot be
  applied is not assumed away.
* **A container's existence is its own disclosure.** A public record can sit in
  a collection whose *title* is the thing that would out a depositor, and #202
  requires that the reader be told nothing: not the title, not that a container
  exists, not that one is withheld. "Empty" and "everything inside is withheld"
  have to be the same answer from outside.

The truth table is written out rather than derived from the implementation, so
a change to the resolver fails here instead of quietly redefining what it is
being compared against.
"""

from __future__ import annotations

import pytest

from ledger.access.grants import anonymous, community_member, steward
from ledger.access.policy import (
    arrangement_permits,
    container_is_visible,
    disclose,
    disclose_container,
    is_listable,
    visible_placement,
)
from ledger.arrangement import Arrangement
from ledger.errors import AccessDenied
from ledger.models import (
    AccessPolicy,
    ArchivalContainer,
    ContainerLevel,
    DublinCore,
    Field,
    Grant,
    Record,
)

pytestmark = pytest.mark.disclosure

_NOW = "2026-06-16T00:00:00Z"
_PAST = "2020-01-01T00:00:00Z"
_FUTURE = "2099-01-01T00:00:00Z"


def _collection(
    cid: str = "col-1",
    *,
    policy: AccessPolicy = AccessPolicy.PUBLIC,
    records_policy: AccessPolicy = AccessPolicy.PUBLIC,
    records_unseal_at: str | None = None,
    records_unseal_condition: str | None = None,
    parent_id: str | None = None,
) -> ArchivalContainer:
    return ArchivalContainer(
        container_id=cid,
        title=f"Collection {cid}",
        level=ContainerLevel.COLLECTION,
        parent_id=parent_id,
        policy=policy,
        records_policy=records_policy,
        records_unseal_at=records_unseal_at,
        records_unseal_condition=records_unseal_condition,
        created_at=_NOW,
    )


def _series(
    cid: str = "ser-1",
    parent: str = "col-1",
    *,
    policy: AccessPolicy = AccessPolicy.PUBLIC,
    records_policy: AccessPolicy = AccessPolicy.PUBLIC,
) -> ArchivalContainer:
    return ArchivalContainer(
        container_id=cid,
        title=f"Series {cid}",
        level=ContainerLevel.SERIES,
        parent_id=parent,
        policy=policy,
        records_policy=records_policy,
        created_at=_NOW,
    )


def _record(
    *,
    policy: AccessPolicy = AccessPolicy.PUBLIC,
    placement: str | None = None,
) -> Record:
    return Record(
        title="A flyer",
        record_id="rec-1",
        default_policy=policy,
        dublin_core=DublinCore(title=["A flyer"], subject=["mutual aid"]),
        fields=[Field(name="story", value="We drove food.", policy=AccessPolicy.PUBLIC)],
        created_at=_NOW,
        placement=placement,
    )


_VIEWERS: tuple[tuple[str, Grant], ...] = (
    ("anonymous", anonymous()),
    ("community", community_member("m")),
    ("steward", steward("s")),
)

# Every policy a container ceiling or a record can carry, with the (unseal_at,
# unseal_condition) a caller would pair with it. Written out so the table below
# is exhaustive over the enum rather than over whichever members were convenient.
_LEVELS: tuple[tuple[str, AccessPolicy, str | None, str | None], ...] = (
    ("public", AccessPolicy.PUBLIC, None, None),
    ("community", AccessPolicy.COMMUNITY, None, None),
    ("stewards", AccessPolicy.STEWARDS, None, None),
    ("sealed-until-indefinite", AccessPolicy.SEALED_UNTIL, None, None),
    ("sealed-until-past", AccessPolicy.SEALED_UNTIL, _PAST, None),
    ("sealed-until-future", AccessPolicy.SEALED_UNTIL, _FUTURE, None),
    ("sealed-conditional", AccessPolicy.SEALED_CONDITIONAL, None, "court-order"),
    ("sealed", AccessPolicy.SEALED, None, None),
)

#: Which viewers each level admits, at ``_NOW``, with no conditions attested.
#: Hand-written from `docs/`'s stated rules, NOT read back out of `is_visible`.
_ADMITS: dict[str, frozenset[str]] = {
    "public": frozenset({"anonymous", "community", "steward"}),
    "community": frozenset({"community", "steward"}),
    "stewards": frozenset({"steward"}),
    # An indefinite seal is an access-level seal a steward may read.
    "sealed-until-indefinite": frozenset({"steward"}),
    # A temporal embargo binds every tier, including stewards, until it lifts.
    "sealed-until-past": frozenset({"anonymous", "community", "steward"}),
    "sealed-until-future": frozenset(),
    "sealed-conditional": frozenset({"steward"}),
    "sealed": frozenset(),
}


#: A record's own `default_policy` carries no unseal date and no condition (a
#: Record has no field for either), so its six enum values admit a *narrower*
#: set than the same policy on a container would. Written out separately rather
#: than reusing `_ADMITS`, because conflating the two is exactly the mistake
#: that would make the narrowing table below compare two wrong answers.
_RECORD_LEVELS: tuple[tuple[str, AccessPolicy], ...] = (
    ("public", AccessPolicy.PUBLIC),
    ("community", AccessPolicy.COMMUNITY),
    ("stewards", AccessPolicy.STEWARDS),
    ("sealed-until", AccessPolicy.SEALED_UNTIL),
    ("sealed-conditional", AccessPolicy.SEALED_CONDITIONAL),
    ("sealed", AccessPolicy.SEALED),
)

_RECORD_ADMITS: dict[str, frozenset[str]] = {
    "public": frozenset({"anonymous", "community", "steward"}),
    "community": frozenset({"community", "steward"}),
    "stewards": frozenset({"steward"}),
    "sealed-until": frozenset({"steward"}),
    "sealed-conditional": frozenset({"steward"}),
    "sealed": frozenset(),
}


def test_the_table_of_admitted_viewers_matches_the_decision_point() -> None:
    """The hand-written table above is what `is_visible` actually does.

    Everything else in this file compares the resolver against `_ADMITS`; if
    that table drifted from the decision point, every "narrowing" assertion
    below would be comparing two wrong answers to each other.
    """
    from ledger.access.policy import is_visible

    for name, policy, at, cond in _LEVELS:
        for viewer, grant in _VIEWERS:
            got = is_visible(policy, grant, _NOW, unseal_at=at, unseal_condition=cond)
            assert got is (viewer in _ADMITS[name]), (name, viewer)
    for rec_name, rec_policy in _RECORD_LEVELS:
        for viewer, grant in _VIEWERS:
            unplaced = _record(policy=rec_policy)
            assert is_listable(unplaced, grant, _NOW) is (viewer in _RECORD_ADMITS[rec_name]), (
                rec_name,
                viewer,
            )


def test_a_container_can_only_narrow_a_record_never_widen_it() -> None:
    """The law, over all 6x8x3 = 144 cells: placed visibility == AND of the two.

    This is #202's "the resolver must be a pure narrowing function". It includes
    the sentinel the issue asks for by name — the rows where the container's
    ceiling is broader than the record's own policy — because those are the ones
    where a resolver written as a comparison rather than a conjunction would
    hand back the container's answer.
    """
    checked = 0
    widened: list[tuple[str, str, str]] = []
    for rec_name, rec_policy in _RECORD_LEVELS:
        for con_name, con_policy, con_at, con_cond in _LEVELS:
            container = _collection(
                records_policy=con_policy,
                records_unseal_at=con_at,
                records_unseal_condition=con_cond,
            )
            graph = Arrangement.from_containers([container])
            placed = _record(policy=rec_policy, placement="col-1")
            unplaced = _record(policy=rec_policy)
            for viewer, grant in _VIEWERS:
                alone = is_listable(unplaced, grant, _NOW)
                expected = (viewer in _RECORD_ADMITS[rec_name]) and (viewer in _ADMITS[con_name])
                got = is_listable(placed, grant, _NOW, arrangement=graph)
                assert alone is (viewer in _RECORD_ADMITS[rec_name])
                assert got is expected, (rec_name, con_name, viewer)
                if got and not alone:
                    widened.append((rec_name, con_name, viewer))
                checked += 1
    assert checked == len(_RECORD_LEVELS) * len(_LEVELS) * len(_VIEWERS) == 144
    assert widened == [], widened


def test_two_levels_of_container_both_apply_and_the_narrower_one_wins() -> None:
    """A series under a collection: the record passes only if BOTH ceilings do.

    The 2x2 that matters, written out. The interesting cell is the last: a
    public series inside a community collection does not make its records
    public, because narrowing is over the whole chain and not just the parent.
    """
    cases = (
        (AccessPolicy.PUBLIC, AccessPolicy.PUBLIC, {"anonymous", "community", "steward"}),
        (AccessPolicy.COMMUNITY, AccessPolicy.PUBLIC, {"community", "steward"}),
        (AccessPolicy.PUBLIC, AccessPolicy.COMMUNITY, {"community", "steward"}),
        (AccessPolicy.COMMUNITY, AccessPolicy.STEWARDS, {"steward"}),
    )
    for collection_ceiling, series_ceiling, admitted in cases:
        graph = Arrangement.from_containers(
            [
                _collection(records_policy=collection_ceiling),
                _series(records_policy=series_ceiling),
            ]
        )
        record = _record(policy=AccessPolicy.PUBLIC, placement="ser-1")
        for viewer, grant in _VIEWERS:
            got = is_listable(record, grant, _NOW, arrangement=graph)
            assert got is (viewer in admitted), (collection_ceiling, series_ceiling, viewer)


def test_an_unarranged_record_resolves_exactly_as_it_did_before_202() -> None:
    """No placement means an empty chain, and an empty AND permits.

    This is the compatibility half of #202's third "decide first" item: an
    existing flat archive keeps working with no migration, because an absent
    placement short-circuits before the arrangement is consulted at all — so it
    does not even matter whether the caller supplied one.
    """
    record = _record(policy=AccessPolicy.PUBLIC)
    graph = Arrangement.from_containers([_collection(records_policy=AccessPolicy.SEALED)])
    for _, grant in _VIEWERS:
        assert is_listable(record, grant, _NOW) is True
        assert is_listable(record, grant, _NOW, arrangement=graph) is True
        assert arrangement_permits(None, grant, _NOW) is True
        assert arrangement_permits(None, grant, _NOW, arrangement=graph) is True


@pytest.mark.parametrize(
    ("label", "containers", "placement"),
    [
        ("placement names a container that is not there", [], "col-missing"),
        (
            "a series whose parent collection is gone",
            [_series("ser-1", "col-gone")],
            "ser-1",
        ),
        (
            "a collection that claims a parent",
            [_collection("col-1", parent_id="col-2"), _collection("col-2")],
            "col-1",
        ),
        (
            "a series whose parent is another series",
            [_collection("col-1"), _series("ser-1", "col-1"), _series("ser-2", "ser-1")],
            "ser-2",
        ),
        (
            "a series that is its own parent",
            [_collection("col-1"), _series("ser-1", "ser-1")],
            "ser-1",
        ),
        (
            "a two-node cycle",
            [_series("a", "b"), _series("b", "a")],
            "a",
        ),
    ],
)
def test_an_unresolvable_chain_denies_the_record_to_everyone_including_stewards(
    label: str, containers: list[ArchivalContainer], placement: str
) -> None:
    """A ceiling of unknown height is not assumed to be zero.

    Every one of these is data a hand-edited `containers/` directory can hold,
    and for every one of them the honest answer is "this record's disclosure
    cannot be computed". Denying is the only answer that cannot widen. A steward
    is denied too: the deny is not an access level, it is an unanswerable
    question, and `ledger arrange check` is the surface that reports it.
    """
    graph = Arrangement.from_containers(containers)
    record = _record(policy=AccessPolicy.PUBLIC, placement=placement)
    assert graph.chain(placement) is None, label
    for viewer, grant in _VIEWERS:
        assert is_listable(record, grant, _NOW, arrangement=graph) is False, (label, viewer)
        with pytest.raises(AccessDenied):
            disclose(record, grant, _NOW, arrangement=graph)


def test_a_read_path_that_was_never_taught_about_arrangement_shows_no_placed_record() -> None:
    """The compatibility default is deny, not "ignore the ceiling".

    `disclose(record, grant, now)` with no arrangement is every pre-#202 call
    site in and outside this repository. Given a placed record it cannot apply
    the ceiling, so it refuses. That is what makes it impossible for a surface
    to be *half* migrated: a read path that has not been updated serves nothing
    rather than serving it unclamped.
    """
    record = _record(policy=AccessPolicy.PUBLIC, placement="col-1")
    for _, grant in _VIEWERS:
        assert is_listable(record, grant, _NOW) is False
        assert arrangement_permits("col-1", grant, _NOW) is False
        with pytest.raises(AccessDenied):
            disclose(record, grant, _NOW)


# --- a container's existence is its own disclosure --------------------------


def test_a_visible_record_in_a_hidden_collection_names_no_container() -> None:
    """#202's second "decide first" item, as a test.

    A public flyer filed in "2019 raid testimony, deposited by Casa Abierta".
    The flyer is public and stays public; the collection's title is what would
    out the depositor, so an anonymous reader gets the flyer and *nothing* about
    where it is filed — identical to what they would get for a flyer that is
    filed nowhere.
    """
    collection = _collection(
        policy=AccessPolicy.STEWARDS,  # the container's own description is hidden
        records_policy=AccessPolicy.PUBLIC,  # what it holds is not
    )
    collection = ArchivalContainer(
        **{
            **collection.__dict__,
            "title": "2019 raid testimony, deposited by Casa Abierta",
            "scope_and_content": "Deposited by Casa Abierta after the March raid.",
        }
    )
    graph = Arrangement.from_containers([collection])
    record = _record(policy=AccessPolicy.PUBLIC, placement="col-1")

    public_view = disclose(record, anonymous(), _NOW, arrangement=graph)
    unarranged = disclose(_record(policy=AccessPolicy.PUBLIC), anonymous(), _NOW)
    assert public_view.placement == ()
    assert public_view.to_dict() == unarranged.to_dict() | {"placement": []}
    rendered = str(public_view.to_dict())
    assert "Casa Abierta" not in rendered
    assert "col-1" not in rendered

    steward_view = disclose(record, steward("s"), _NOW, arrangement=graph)
    assert [s.container_id for s in steward_view.placement] == ["col-1"]
    assert steward_view.placement[0].title == "2019 raid testimony, deposited by Casa Abierta"


def test_a_hidden_container_and_an_absent_one_are_the_same_answer() -> None:
    """Empty versus withheld, indistinguishable from outside.

    `disclose_container` raises the same `AccessDenied` naming the same id for a
    container this viewer may not describe as a caller would get for one that
    does not exist — so a reader probing ids learns nothing about which of them
    are real.
    """
    hidden = _collection("col-secret", policy=AccessPolicy.STEWARDS)
    graph = Arrangement.from_containers([hidden])

    with pytest.raises(AccessDenied) as denied:
        disclose_container(hidden, anonymous(), _NOW, arrangement=graph)
    assert str(denied.value) == "col-secret"

    absent = _collection("col-absent", policy=AccessPolicy.PUBLIC)
    with pytest.raises(AccessDenied) as missing:
        disclose_container(absent, anonymous(), _NOW, arrangement=graph)
    assert str(missing.value) == "col-absent"
    assert type(denied.value) is type(missing.value)


def test_container_visibility_narrows_down_the_chain_too() -> None:
    """A public series under a hidden collection is hidden.

    Without this, the chain would have a hole in it: a viewer could be shown a
    series and not the collection above it, and the scope note the series
    inherits could come from prose that viewer may not read.
    """
    graph = Arrangement.from_containers(
        [
            _collection("col-1", policy=AccessPolicy.STEWARDS),
            _series("ser-1", "col-1", policy=AccessPolicy.PUBLIC),
        ]
    )
    series = graph.require("ser-1")
    assert container_is_visible(series, steward("s"), _NOW, arrangement=graph) is True
    assert container_is_visible(series, anonymous(), _NOW, arrangement=graph) is False
    with pytest.raises(AccessDenied):
        disclose_container(series, anonymous(), _NOW, arrangement=graph)


def test_the_visible_placement_chain_is_always_a_prefix_from_the_root() -> None:
    """No viewer is ever shown a series without the collection holding it."""
    graph = Arrangement.from_containers(
        [
            _collection("col-1", policy=AccessPolicy.PUBLIC),
            _series("ser-1", "col-1", policy=AccessPolicy.COMMUNITY),
        ]
    )
    anon_steps = visible_placement("ser-1", anonymous(), _NOW, arrangement=graph)
    member_steps = visible_placement("ser-1", community_member("m"), _NOW, arrangement=graph)
    assert [s.container_id for s in anon_steps] == ["col-1"]
    assert [s.container_id for s in member_steps] == ["col-1", "ser-1"]
    assert [s.level for s in member_steps] == [ContainerLevel.COLLECTION, ContainerLevel.SERIES]


def test_a_series_inherits_only_a_visible_ancestors_scope_note() -> None:
    """Description flows downward, and it stops at the first thing you may not read."""
    graph = Arrangement.from_containers(
        [
            ArchivalContainer(
                container_id="col-1",
                title="Casa Abierta deposit",
                level=ContainerLevel.COLLECTION,
                scope_and_content="Four boxes left with us after the March raid.",
                policy=AccessPolicy.COMMUNITY,
                records_policy=AccessPolicy.PUBLIC,
                created_at=_NOW,
            ),
            ArchivalContainer(
                container_id="ser-1",
                title="Flyers",
                level=ContainerLevel.SERIES,
                parent_id="col-1",
                scope_and_content="",  # no note of its own: inherit
                policy=AccessPolicy.COMMUNITY,
                records_policy=AccessPolicy.PUBLIC,
                created_at=_NOW,
            ),
        ]
    )
    member = disclose_container(
        graph.require("ser-1"), community_member("m"), _NOW, arrangement=graph
    )
    assert member.scope_and_content == "Four boxes left with us after the March raid."
    assert member.inherited_scope is True

    # The collection is community-only, so an anonymous viewer cannot see the
    # series at all — which is also why they can never reach its inherited note.
    with pytest.raises(AccessDenied):
        disclose_container(graph.require("ser-1"), anonymous(), _NOW, arrangement=graph)


def test_a_container_with_its_own_note_does_not_inherit() -> None:
    graph = Arrangement.from_containers(
        [
            ArchivalContainer(
                container_id="col-1",
                title="Casa Abierta deposit",
                level=ContainerLevel.COLLECTION,
                scope_and_content="Collection-level note.",
                policy=AccessPolicy.PUBLIC,
                records_policy=AccessPolicy.PUBLIC,
                created_at=_NOW,
            ),
            ArchivalContainer(
                container_id="ser-1",
                title="Newsletter",
                level=ContainerLevel.SERIES,
                parent_id="col-1",
                scope_and_content="A complete run, 1987-1994.",
                policy=AccessPolicy.PUBLIC,
                records_policy=AccessPolicy.PUBLIC,
                created_at=_NOW,
            ),
        ]
    )
    shown = disclose_container(graph.require("ser-1"), anonymous(), _NOW, arrangement=graph)
    assert shown.scope_and_content == "A complete run, 1987-1994."
    assert shown.inherited_scope is False


def test_a_disclosed_container_carries_no_policy_and_no_holdings_count() -> None:
    """The shape itself cannot leak a ceiling or a number.

    A count of what a container holds would be an oracle about records the
    viewer cannot list, which is the whole reason `extent` is the archivist's
    free text ("4 boxes") and not something ledger derives.
    """
    graph = Arrangement.from_containers([_collection(records_policy=AccessPolicy.SEALED)])
    shown = disclose_container(graph.require("col-1"), anonymous(), _NOW, arrangement=graph)
    keys = set(shown.to_dict())
    assert keys == {
        "container_id",
        "title",
        "level",
        "scope_and_content",
        "inherited_scope",
        "extent",
        "dates",
        "ancestors",
    }
    assert not any("polic" in k or "count" in k or "seal" in k for k in keys)
    assert "sealed" not in str(shown.to_dict())


def test_a_temporal_ceiling_opens_for_everyone_at_once_and_not_before() -> None:
    """A container's embargo is a promise to time, exactly like a field's.

    Asserted across the clock rather than across viewers: the same steward who
    could read the record the instant before must still be refused, because a
    temporal seal binds every tier (`is_visible`'s documented rule) and the
    chain is that same function applied to the container.
    """
    graph = Arrangement.from_containers(
        [
            _collection(
                records_policy=AccessPolicy.SEALED_UNTIL,
                records_unseal_at="2030-01-01T00:00:00Z",
            )
        ]
    )
    record = _record(policy=AccessPolicy.PUBLIC, placement="col-1")
    for _, grant in _VIEWERS:
        assert is_listable(record, grant, "2029-12-31T23:59:59Z", arrangement=graph) is False
        assert is_listable(record, grant, "2030-01-01T00:00:00Z", arrangement=graph) is True


def test_an_expired_grant_is_downgraded_before_the_chain_is_consulted() -> None:
    """A stale credential does not out-live its trust through a container either."""
    graph = Arrangement.from_containers([_collection(records_policy=AccessPolicy.COMMUNITY)])
    record = _record(policy=AccessPolicy.PUBLIC, placement="col-1")
    expired = Grant(
        subject="m",
        levels=frozenset({AccessPolicy.PUBLIC, AccessPolicy.COMMUNITY}),
        expires_at=_PAST,
    )
    assert is_listable(record, expired, _NOW, arrangement=graph) is False
    assert is_listable(record, community_member("m"), _NOW, arrangement=graph) is True


def test_an_attested_condition_lifts_a_conditional_ceiling_for_a_non_steward() -> None:
    """`conditions_met` reaches the chain, not only the record's own fields."""
    graph = Arrangement.from_containers(
        [
            _collection(
                records_policy=AccessPolicy.SEALED_CONDITIONAL,
                records_unseal_condition="court-order",
            )
        ]
    )
    record = _record(policy=AccessPolicy.PUBLIC, placement="col-1")
    member = community_member("m")
    assert is_listable(record, member, _NOW, arrangement=graph) is False
    assert (
        is_listable(
            record, member, _NOW, arrangement=graph, conditions_met=frozenset({"court-order"})
        )
        is True
    )


def test_a_dangling_placement_names_no_container_either() -> None:
    """Not visible, and also not *nameable*: the two answers agree.

    `arrangement_permits` denies a record whose chain will not resolve, so a
    reader never reaches its placement — but `visible_placement` is a separate
    function and a caller could reach it another way. It returns nothing for the
    same input, so there is no path on which an unresolvable chain produces a
    container name.
    """
    graph = Arrangement.from_containers([_collection("col-1")])
    for grant in (anonymous(), community_member("m"), steward("s")):
        assert visible_placement("col-gone", grant, _NOW, arrangement=graph) == ()
        assert visible_placement("col-1", grant, _NOW) == ()


def test_a_hidden_collection_hides_its_series_so_no_note_can_be_inherited() -> None:
    """The invariant that makes the inheritance walk need no visibility check.

    `disclose_container` inherits a scope note from the nearest ancestor without
    re-checking whether that ancestor is visible, because it cannot be reached
    over an invisible one: container visibility ANDs down the chain, so a series
    under a hidden collection is itself hidden. This asserts that directly,
    across every viewer and both sealed shapes, rather than leaving an
    unreachable guard in the resolver to imply it.
    """
    for hiding in (AccessPolicy.STEWARDS, AccessPolicy.SEALED):
        graph = Arrangement.from_containers(
            [
                ArchivalContainer(
                    container_id="col-1",
                    title="Casa Abierta deposit",
                    level=ContainerLevel.COLLECTION,
                    scope_and_content="Deposited by Casa Abierta after the March raid.",
                    policy=hiding,
                    records_policy=AccessPolicy.PUBLIC,
                    created_at=_NOW,
                ),
                ArchivalContainer(
                    container_id="ser-1",
                    title="Flyers",
                    level=ContainerLevel.SERIES,
                    parent_id="col-1",
                    scope_and_content="",
                    policy=AccessPolicy.PUBLIC,
                    records_policy=AccessPolicy.PUBLIC,
                    created_at=_NOW,
                ),
            ]
        )
        for grant in (anonymous(), community_member("m")):
            assert container_is_visible(graph.require("ser-1"), grant, _NOW, arrangement=graph) is (
                False
            )
            with pytest.raises(AccessDenied):
                disclose_container(graph.require("ser-1"), grant, _NOW, arrangement=graph)


def test_a_series_with_no_note_under_a_collection_with_no_note_inherits_nothing() -> None:
    """The other side of the inheritance branch: there is nothing to inherit."""
    graph = Arrangement.from_containers([_collection("col-1"), _series("ser-1", "col-1")])
    shown = disclose_container(graph.require("ser-1"), anonymous(), _NOW, arrangement=graph)
    assert shown.scope_and_content == ""
    assert shown.inherited_scope is False


def test_the_scope_inheritance_walk_is_valid_only_while_the_chain_is_two_deep() -> None:
    """`disclose_container` reads `chain[-2]`, not the whole chain above it.

    That is correct for a two-level vocabulary and silently wrong for a deeper
    one: a three-level chain whose middle container has no note would stop
    inheriting instead of reaching the top. The index is there because a loop
    would carry an iteration no fixture can reach, so this is the assertion that
    makes the trade visible — grow the vocabulary and it fails, naming the line
    that has to change with it.
    """
    from ledger.arrangement import MAX_CHAIN_DEPTH

    assert MAX_CHAIN_DEPTH == 2
    assert set(ContainerLevel) == {ContainerLevel.COLLECTION, ContainerLevel.SERIES}
