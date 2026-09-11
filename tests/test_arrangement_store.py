"""The arrangement graph and its store: shape rules, round-trips, refusals (#202).

`ledger.access.policy` decides *who may see* a container; this file is about the
layer under that — whether a container is a legal shape at all, whether a stored
manifest round-trips, and what happens to a directory somebody hand-edited.

The asymmetry worth noticing, and the reason it is safe: the write path
(:func:`~ledger.arrangement.validate_container`) is strict and loud, and the
read path (:meth:`~ledger.arrangement.Arrangement.chain`,
:func:`~ledger.arrangement.load_arrangement`) is forgiving and silent — but
forgiving only in the direction that *hides* material. A container file that
will not parse is skipped, and every record filed in it then resolves to no
chain at all and is denied. Nothing a corrupt file can say makes a record more
visible.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ledger.arrangement import (
    MAX_CHAIN_DEPTH,
    Arrangement,
    container_path,
    deserialize_container,
    load_arrangement,
    read_container,
    safe_container_component,
    save_container,
    serialize_container,
    validate_container,
)
from ledger.errors import LedgerError, ObjectNotFound
from ledger.models import AccessPolicy, ArchivalContainer, ContainerLevel

_NOW = "2026-06-16T00:00:00Z"


def _collection(
    cid: str = "col-1",
    *,
    parent_id: str | None = None,
    scope_and_content: str = "",
) -> ArchivalContainer:
    return ArchivalContainer(
        container_id=cid,
        title=f"Collection {cid}",
        level=ContainerLevel.COLLECTION,
        parent_id=parent_id,
        scope_and_content=scope_and_content,
        created_at=_NOW,
    )


def _series(cid: str = "ser-1", parent: str = "col-1") -> ArchivalContainer:
    return ArchivalContainer(
        container_id=cid,
        title=f"Series {cid}",
        level=ContainerLevel.SERIES,
        parent_id=parent,
        created_at=_NOW,
    )


# --- (de)serialization ------------------------------------------------------


def test_a_container_round_trips_through_its_manifest() -> None:
    original = ArchivalContainer(
        container_id="casa-abierta",
        title="Casa Abierta deposit",
        level=ContainerLevel.COLLECTION,
        scope_and_content="Four boxes left with us after the March raid.",
        extent="4 boxes (1.5 linear feet)",
        dates="1987-1994, bulk 1991",
        policy=AccessPolicy.COMMUNITY,
        unseal_at=None,
        unseal_condition=None,
        records_policy=AccessPolicy.SEALED_UNTIL,
        records_unseal_at="2030-01-01T00:00:00Z",
        records_unseal_condition=None,
        created_at=_NOW,
    )
    assert deserialize_container(serialize_container(original)) == original


def test_the_manifest_is_canonical_so_the_same_container_is_the_same_bytes() -> None:
    """Determinism: sorted keys, compact, no wall clock anywhere in the writer."""
    container = _collection()
    first = serialize_container(container)
    assert first == serialize_container(container)
    keys = list(json.loads(first))
    assert keys == sorted(keys)


def test_an_unrecognised_policy_is_refused_rather_than_defaulted() -> None:
    """A policy nobody can parse is not quietly read as the default.

    The default here would be `sealed-until`, which is *narrow*, so defaulting
    would not widen anything — but it would silently reinterpret a steward's
    stated intent, and the one thing worse than a refused container is a
    container that claims to mean something it does not.
    """
    text = serialize_container(_collection()).replace('"policy":"sealed-until"', '"policy":"open"')
    with pytest.raises(LedgerError, match="unrecognised level or policy"):
        deserialize_container(text)

    bad_level = serialize_container(_collection()).replace(
        '"level":"collection"', '"level":"fonds"'
    )
    with pytest.raises(LedgerError, match="unrecognised level or policy"):
        deserialize_container(bad_level)


def test_a_manifest_that_is_not_an_object_is_refused() -> None:
    with pytest.raises(LedgerError, match="must be a JSON object"):
        deserialize_container("[]")


def test_an_unknown_key_is_ignored_so_a_newer_ledger_degrades_gracefully() -> None:
    text = json.dumps(
        {**json.loads(serialize_container(_collection())), "appraisal_note": "later entity"}
    )
    assert deserialize_container(text).container_id == "col-1"


# --- ids --------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "",
        ".",
        "..",
        "../escape",
        "with/slash",
        "with space",
        "unicode-é",
        "x" * 65,
    ],
)
def test_a_container_id_that_could_escape_a_path_or_an_attribute_is_refused(bad: str) -> None:
    """One allow-listed path component, the same rule record ids follow.

    A container id becomes a filename, a URL segment, an EAD `id` attribute and
    an OAI `setSpec`; each of those is somewhere a crafted value could break
    out, so the allow-list is applied once, here, rather than escaped four
    times downstream.
    """
    with pytest.raises(LedgerError, match="invalid container id"):
        safe_container_component(bad)


def test_a_well_formed_id_is_returned_unchanged() -> None:
    assert safe_container_component("casa_abierta-1987") == "casa_abierta-1987"


# --- the shape rules (the write path) ---------------------------------------


def test_a_collection_with_a_parent_is_refused() -> None:
    graph = Arrangement.from_containers([_collection("col-2")])
    with pytest.raises(LedgerError, match="cannot have a parent"):
        validate_container(_collection("col-1", parent_id="col-2"), graph)


def test_a_series_with_no_parent_is_refused() -> None:
    with pytest.raises(LedgerError, match="must name the collection"):
        validate_container(
            ArchivalContainer(
                container_id="ser-1",
                title="Flyers",
                level=ContainerLevel.SERIES,
                created_at=_NOW,
            ),
            Arrangement.empty(),
        )


def test_a_series_under_a_series_is_refused_which_is_what_bounds_the_depth() -> None:
    graph = Arrangement.from_containers([_collection(), _series("ser-1", "col-1")])
    with pytest.raises(LedgerError, match="not to another series"):
        validate_container(_series("ser-2", "ser-1"), graph)


def test_a_series_whose_parent_does_not_exist_is_refused() -> None:
    with pytest.raises(LedgerError, match="no such collection: col-gone"):
        validate_container(_series("ser-1", "col-gone"), Arrangement.empty())


def test_a_container_that_is_its_own_parent_is_refused() -> None:
    with pytest.raises(LedgerError, match="cannot be its own parent"):
        validate_container(_series("ser-1", "ser-1"), Arrangement.empty())


def test_a_container_needs_a_title() -> None:
    with pytest.raises(LedgerError, match="needs a title"):
        validate_container(
            ArchivalContainer(container_id="col-1", title="   ", created_at=_NOW),
            Arrangement.empty(),
        )


def test_a_well_formed_collection_and_series_are_accepted() -> None:
    graph = Arrangement.from_containers([_collection()])
    validate_container(_collection("col-2"), graph)
    validate_container(_series("ser-1", "col-1"), graph)


# --- the graph --------------------------------------------------------------


def test_the_chain_is_root_first_and_at_most_two_links() -> None:
    graph = Arrangement.from_containers([_collection(), _series("ser-1", "col-1")])
    assert [c.container_id for c in graph.chain("col-1") or ()] == ["col-1"]
    assert [c.container_id for c in graph.chain("ser-1") or ()] == ["col-1", "ser-1"]
    assert len(graph.chain("ser-1") or ()) <= MAX_CHAIN_DEPTH


def test_no_placement_is_an_empty_chain_and_not_a_refusal() -> None:
    """`()` and `None` mean different things and the distinction is load-bearing.

    `()` is "there is nothing above this record", which permits. `None` is "I
    could not work out what is above this record", which denies. A resolver that
    returned `()` for the second would widen every dangling placement.
    """
    graph = Arrangement.from_containers([_collection()])
    assert graph.chain(None) == ()
    assert graph.chain("col-gone") is None


def test_children_and_roots_are_stably_ordered() -> None:
    graph = Arrangement.from_containers(
        [
            _collection("col-b"),
            _collection("col-a"),
            _series("ser-z", "col-a"),
            _series("ser-a", "col-a"),
        ]
    )
    assert [c.container_id for c in graph.roots()] == ["col-a", "col-b"]
    assert [c.container_id for c in graph.children("col-a")] == ["ser-a", "ser-z"]
    assert graph.children("col-b") == ()


def test_require_names_only_the_id_it_could_not_find() -> None:
    with pytest.raises(ObjectNotFound) as exc:
        Arrangement.empty().require("col-1")
    assert str(exc.value) == "col-1"


def test_an_empty_arrangement_is_falsey_and_a_populated_one_is_not() -> None:
    assert not Arrangement.empty()
    assert Arrangement.from_containers([_collection()])


def test_all_containers_returns_every_level_in_stable_order() -> None:
    graph = Arrangement.from_containers([_series("ser-1", "col-1"), _collection("col-1")])
    assert [c.container_id for c in graph.all_containers()] == ["col-1", "ser-1"]


# --- the store --------------------------------------------------------------


def test_a_saved_container_reads_back_identically(tmp_path: Path) -> None:
    containers = tmp_path / "containers"
    original = _collection(scope_and_content="Four boxes.")
    save_container(containers, original)
    assert read_container(containers, "col-1") == original
    assert container_path(containers, "col-1").name == "col-1.json"


def test_saving_leaves_no_temporary_file_behind(tmp_path: Path) -> None:
    containers = tmp_path / "containers"
    save_container(containers, _collection())
    assert sorted(p.name for p in containers.iterdir()) == ["col-1.json"]


def test_reading_a_container_that_is_not_there_names_only_the_id(tmp_path: Path) -> None:
    with pytest.raises(ObjectNotFound) as exc:
        read_container(tmp_path / "containers", "col-1")
    assert str(exc.value) == "col-1"


def test_an_archive_with_no_containers_directory_has_an_empty_arrangement(
    tmp_path: Path,
) -> None:
    """An existing flat archive is not an error state.

    #202's third "decide first" item asked whether every record should be
    migrated into an implicit "unarranged" container. It is not: an archive with
    no `containers/` directory loads an empty arrangement, every record's
    placement is `None`, and every chain is `()`. No bag is rewritten and no
    PREMIS event is invented for material nobody arranged.
    """
    assert load_arrangement(tmp_path / "containers").containers == {}


def test_a_container_file_that_will_not_parse_is_skipped_not_fatal(tmp_path: Path) -> None:
    """One broken manifest must not take down browse for the whole archive.

    And skipping it cannot widen anything: every record placed in the skipped
    container now resolves to `None` in `chain`, which
    `arrangement_permits` reads as deny.
    """
    containers = tmp_path / "containers"
    save_container(containers, _collection("col-good"))
    (containers / "col-broken.json").write_text("{not json", encoding="utf-8")
    graph = load_arrangement(containers)
    assert set(graph.containers) == {"col-good"}
    assert graph.chain("col-broken") is None


def test_load_is_deterministic_across_two_reads_of_the_same_directory(tmp_path: Path) -> None:
    containers = tmp_path / "containers"
    for cid in ("col-b", "col-a"):
        save_container(containers, _collection(cid))
    first = load_arrangement(containers)
    second = load_arrangement(containers)
    assert [c.container_id for c in first.all_containers()] == [
        c.container_id for c in second.all_containers()
    ]


def test_a_series_with_no_parent_on_disk_resolves_to_no_chain_at_all() -> None:
    """The write path refuses it; this is what the read path does if it is there.

    `validate_container` will not store a parentless series, but a hand-edited
    `containers/` directory can hold one. `chain` answers `None` — deny — rather
    than treating it as a root, which would drop whatever ceiling its real
    collection carried.
    """
    orphan = ArchivalContainer(
        container_id="ser-1",
        title="Flyers",
        level=ContainerLevel.SERIES,
        parent_id=None,
        created_at=_NOW,
    )
    graph = Arrangement.from_containers([orphan])
    assert graph.chain("ser-1") is None
