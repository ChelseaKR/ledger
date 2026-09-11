"""Arrangement: the collections and series a record sits in, and nothing else.

#202. :mod:`ledger.models` says what a :class:`~ledger.models.ArchivalContainer`
*is*; this module is the graph over a set of them, the rules that refuse a
malformed one, and the on-disk store. It is deliberately the only place that
knows a container has a parent, so the shape of the hierarchy is decided once.

Three properties, each of which the disclosure layer relies on:

* **The chain is short and acyclic by construction.** A ``COLLECTION`` has no
  parent and a ``SERIES``'s parent must be a ``COLLECTION``, so the longest
  root-to-node path is two links and a cycle is not representable. Nothing here
  recurses; :meth:`Arrangement.chain` is a loop with a hard bound.
* **Everything fails closed.** :meth:`Arrangement.chain` returns ``None`` —
  never a partial chain — for an unknown id, a broken parent link, a shape the
  rules above forbid, or anything that would exceed the bound. A caller that
  cannot resolve a record's chain must deny the record, because it cannot know
  what ceiling the missing container carried
  (:func:`ledger.access.policy.arrangement_permits` does exactly that).
* **No disclosure decision lives here.** This module never looks at a
  :class:`~ledger.models.Grant`. Which containers a viewer may see, and which
  records a container's policy lets through, are decided in
  :mod:`ledger.access.policy`, the one place the archive audits for that.

Determinism: :func:`load_arrangement` sorts by ``(created_at, container_id)``
and :func:`serialize_container` is canonical JSON, so the same directory always
yields the same graph and the same bytes (reproducibility).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from ledger.errors import LedgerError, ObjectNotFound
from ledger.models import (
    AccessPolicy,
    ArchivalContainer,
    ContainerLevel,
    canonical_json,
)

__all__ = [
    "MAX_CHAIN_DEPTH",
    "Arrangement",
    "container_path",
    "deserialize_container",
    "load_arrangement",
    "read_container",
    "safe_container_component",
    "save_container",
    "serialize_container",
    "validate_container",
]

#: The longest root-to-node chain the two-level vocabulary can produce. A
#: resolution that would exceed it is corrupt data, not a deeper hierarchy, and
#: is refused rather than walked (fail closed, and no unbounded loop on a cycle
#: that a hand-edited file could still write into the store).
MAX_CHAIN_DEPTH = 2

#: A container id is one allow-listed path component, exactly like a record id:
#: it becomes a filename, an EAD ``id`` attribute, an OAI ``setSpec`` and a URL
#: segment, and every one of those is a place a crafted value could escape.
_ID_RE = re.compile(r"[A-Za-z0-9_-]+")

_MAX_ID_LEN = 64


def safe_container_component(container_id: str) -> str:
    """Return ``container_id`` as a single allow-listed path component, or refuse.

    Mirrors ``ledger.ingest._safe_record_component``: the same allow-list, the
    same refusal, so a container id can never be a traversal, an empty name, or
    anything a path join would reinterpret. The message names only the condition
    (no-outing rule).
    """
    component = Path(container_id).name
    if (
        component != container_id
        or component in {"", ".", ".."}
        or len(component) > _MAX_ID_LEN
        or _ID_RE.fullmatch(component) is None
    ):
        raise LedgerError("invalid container id")
    return component


# --- (de)serialization ------------------------------------------------------


def serialize_container(container: ArchivalContainer) -> str:
    """Serialize ``container`` to canonical JSON (sorted keys, compact).

    Determinism: the same container always serializes to byte-identical text, so
    a stored arrangement hashes the same on every machine (reproducibility).
    """
    return canonical_json(
        {
            "container_id": container.container_id,
            "title": container.title,
            "level": container.level.value,
            "parent_id": container.parent_id,
            "scope_and_content": container.scope_and_content,
            "extent": container.extent,
            "dates": container.dates,
            "policy": container.policy.value,
            "unseal_at": container.unseal_at,
            "unseal_condition": container.unseal_condition,
            "records_policy": container.records_policy.value,
            "records_unseal_at": container.records_unseal_at,
            "records_unseal_condition": container.records_unseal_condition,
            "created_at": container.created_at,
        }
    )


def _opt_str(value: object) -> str | None:
    return str(value) if value is not None else None


def deserialize_container(text: str) -> ArchivalContainer:
    """Rebuild an :class:`~ledger.models.ArchivalContainer` from stored JSON.

    The inverse of :func:`serialize_container`. Unknown keys are ignored so a
    file written by a newer ledger degrades gracefully (robustness), but an
    unrecognised ``level`` or ``policy`` is a hard refusal rather than a default:
    guessing a policy is how a seal becomes a disclosure (fail closed).
    """
    raw: object = json.loads(text)
    if not isinstance(raw, dict):
        raise LedgerError("container manifest must be a JSON object")
    data: dict[str, object] = raw
    try:
        level = ContainerLevel(str(data.get("level", ContainerLevel.COLLECTION.value)))
        policy = AccessPolicy(str(data.get("policy", AccessPolicy.SEALED_UNTIL.value)))
        records_policy = AccessPolicy(
            str(data.get("records_policy", AccessPolicy.SEALED_UNTIL.value))
        )
    except ValueError as exc:  # an unknown enum member, never a value we may guess
        raise LedgerError("container manifest carries an unrecognised level or policy") from exc
    return ArchivalContainer(
        container_id=str(data.get("container_id", "")),
        title=str(data.get("title", "")),
        level=level,
        parent_id=_opt_str(data.get("parent_id")),
        scope_and_content=str(data.get("scope_and_content", "")),
        extent=str(data.get("extent", "")),
        dates=str(data.get("dates", "")),
        policy=policy,
        unseal_at=_opt_str(data.get("unseal_at")),
        unseal_condition=_opt_str(data.get("unseal_condition")),
        records_policy=records_policy,
        records_unseal_at=_opt_str(data.get("records_unseal_at")),
        records_unseal_condition=_opt_str(data.get("records_unseal_condition")),
        created_at=str(data.get("created_at", "")),
    )


# --- the graph --------------------------------------------------------------


@dataclass(frozen=True)
class Arrangement:
    """Every container the archive holds, as a graph that can be walked upward.

    Constructed from a directory (:func:`load_arrangement`) or directly from a
    sequence (:meth:`from_containers`, used by tests and by callers that already
    hold the set). Immutable, and it consults no clock and no grant.
    """

    containers: Mapping[str, ArchivalContainer]

    @classmethod
    def from_containers(cls, containers: Iterable[ArchivalContainer]) -> Arrangement:
        """Build an arrangement from ``containers``, last one wins on a duplicate id."""
        return cls(containers={c.container_id: c for c in containers})

    @classmethod
    def empty(cls) -> Arrangement:
        """The arrangement of an archive that has none — every chain is empty."""
        return cls(containers={})

    def __bool__(self) -> bool:
        return bool(self.containers)

    def get(self, container_id: str) -> ArchivalContainer | None:
        """The container with this id, or ``None``. No disclosure decision."""
        return self.containers.get(container_id)

    def require(self, container_id: str) -> ArchivalContainer:
        """The container with this id, or :class:`~ledger.errors.ObjectNotFound`.

        Names only the id, never anything about what it holds (no-outing rule).
        """
        found = self.containers.get(container_id)
        if found is None:
            raise ObjectNotFound(container_id)
        return found

    def chain(self, container_id: str | None) -> tuple[ArchivalContainer, ...] | None:
        """The root-first chain from the top collection down to ``container_id``.

        ``()`` for ``None`` (an unarranged record has an empty chain, and an empty
        AND is permissive — that is the pre-#202 behaviour, unchanged).

        ``None`` — never a partial chain — whenever the arrangement cannot be
        resolved: an id that is not here, a series whose parent is missing or is
        not a collection, a collection that claims a parent, or a walk that would
        exceed :data:`MAX_CHAIN_DEPTH`. Every caller must read ``None`` as *deny*:
        a chain that cannot be resolved is a ceiling that cannot be applied, and
        an unapplied ceiling is a widening (fail closed).
        """
        if container_id is None:
            return ()
        walked: list[ArchivalContainer] = []
        seen: set[str] = set()
        current: str | None = container_id
        while current is not None:
            if current in seen or len(walked) >= MAX_CHAIN_DEPTH:
                return None
            seen.add(current)
            node = self.containers.get(current)
            if node is None:
                return None
            if node.level is ContainerLevel.COLLECTION and node.parent_id is not None:
                # A collection is a root by definition; one claiming a parent is
                # corrupt, and guessing which half to believe is how a ceiling
                # gets dropped.
                return None
            if node.level is ContainerLevel.SERIES and node.parent_id is None:
                return None
            walked.append(node)
            current = node.parent_id
        # The loop can only exit with ``current is None``, and the two shape
        # checks above mean the node that set it was a collection with no
        # parent. So the top of ``walked`` is always a collection here; a
        # further check would be a branch nothing can reach.
        return tuple(reversed(walked))

    def children(self, container_id: str) -> tuple[ArchivalContainer, ...]:
        """The containers whose parent is ``container_id``, in stable order."""
        return tuple(
            sorted(
                (c for c in self.containers.values() if c.parent_id == container_id),
                key=lambda c: (c.created_at, c.container_id),
            )
        )

    def roots(self) -> tuple[ArchivalContainer, ...]:
        """Every collection, in stable order. Series are reached through them."""
        return tuple(
            sorted(
                (
                    c
                    for c in self.containers.values()
                    if c.level is ContainerLevel.COLLECTION and c.parent_id is None
                ),
                key=lambda c: (c.created_at, c.container_id),
            )
        )

    def all_containers(self) -> tuple[ArchivalContainer, ...]:
        """Every container, in stable order, whatever its level or shape."""
        return tuple(sorted(self.containers.values(), key=lambda c: (c.created_at, c.container_id)))


# --- validation (the write path) --------------------------------------------


def validate_container(container: ArchivalContainer, arrangement: Arrangement) -> None:
    """Refuse a container that would make the graph unwalkable, before it is stored.

    :meth:`Arrangement.chain` already fails closed over corrupt data, but a store
    that accepts corrupt data has turned a steward's typo into 400 invisible
    records with no error. So the write path refuses, loudly, here:

    * the id must be one allow-listed path component;
    * a title is required — an untitled container is a row nobody can act on;
    * a collection may not name a parent, and a series must name one;
    * a series' parent must already exist and must be a collection (which is what
      bounds the depth at two and makes a cycle unrepresentable);
    * a container may not be its own parent.

    The messages name the condition and the ids involved, never a scope note or
    any other description (no-outing rule).
    """
    safe_container_component(container.container_id)
    if not container.title.strip():
        raise LedgerError("a container needs a title")
    if container.level is ContainerLevel.COLLECTION:
        if container.parent_id is not None:
            raise LedgerError("a collection is a top-level container and cannot have a parent")
        return
    if container.parent_id is None:
        raise LedgerError("a series must name the collection it belongs to")
    if container.parent_id == container.container_id:
        raise LedgerError("a container cannot be its own parent")
    parent = arrangement.get(container.parent_id)
    if parent is None:
        raise LedgerError(f"no such collection: {container.parent_id}")
    if parent.level is not ContainerLevel.COLLECTION:
        raise LedgerError("a series must belong to a collection, not to another series")


# --- the store --------------------------------------------------------------


def container_path(containers_dir: Path, container_id: str) -> Path:
    """The on-disk manifest path for ``container_id``, id validated first."""
    return containers_dir / f"{safe_container_component(container_id)}.json"


def save_container(containers_dir: Path, container: ArchivalContainer) -> Path:
    """Write ``container``'s manifest, creating the directory if needed.

    Writes through a temporary file in the same directory and replaces
    atomically, so a reader never sees a half-written manifest and a crash
    mid-write leaves the previous one intact (robustness) — the same discipline
    the record manifests are written with.
    """
    containers_dir.mkdir(parents=True, exist_ok=True)
    target = container_path(containers_dir, container.container_id)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(serialize_container(container), encoding="utf-8")
    tmp.replace(target)
    return target


def read_container(containers_dir: Path, container_id: str) -> ArchivalContainer:
    """Load one container's manifest, or raise
    :class:`~ledger.errors.ObjectNotFound` naming only the id."""
    path = container_path(containers_dir, container_id)
    if not path.is_file():
        raise ObjectNotFound(container_id)
    return deserialize_container(path.read_text(encoding="utf-8"))


def load_arrangement(containers_dir: Path) -> Arrangement:
    """Load every container manifest under ``containers_dir`` into a graph.

    A directory that does not exist is an archive with no arrangement, not an
    error: every chain is then empty and every record behaves exactly as it did
    before #202 (compatibility).

    An individual manifest that will not parse is **skipped**, deliberately and
    asymmetrically: a broken container file must not take down browse for the
    whole archive, and skipping it does not widen anything — every record placed
    in it now resolves to ``None`` in :meth:`Arrangement.chain` and is therefore
    denied (fail closed). ``ledger arrange check`` is what surfaces it.
    """
    if not containers_dir.is_dir():
        return Arrangement.empty()
    found: list[ArchivalContainer] = []
    for path in sorted(containers_dir.glob("*.json")):
        try:
            found.append(deserialize_container(path.read_text(encoding="utf-8")))
        except (LedgerError, ValueError, OSError):
            continue
    return Arrangement.from_containers(found)
