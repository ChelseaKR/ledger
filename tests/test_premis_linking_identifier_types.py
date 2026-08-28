"""Every PREMIS event says what kind of thing it is about (MP-08).

`docs/MULTIYEAR-PLAN.md` MP-08, closing the follow-up ADR 0012 recorded in its own
consequences:

    `linkingObjectIdentifierType` is emitted as `local` in XML for events whose
    writers have not been typed yet (consent changes, takedowns, replication). That
    is the conventional repository-scoped type and it is honest; typing those writers
    is a follow-up that changes nothing about what the events assert.

`local` is honest and useless. PREMIS leaves identifier types to the repository
precisely so a consumer never has to guess what an identifier names, and ledger writes
five kinds of identifier that look alike as strings: a record id, a bag name, a
proposal id, a payload id, and a content address. `PremisEvent.object_identifier_type`
already refuses to guess among them -- it infers only the content-address case, where
the parse is unambiguous -- so an untyped writer left the question permanently
unanswerable rather than merely unanswered.

The structural test below is the one that matters. It reads the source rather than any
event, so it covers writers that no test happens to exercise, and it fails the moment
someone adds an untyped one.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from ledger.models import (
    OBJECT_TYPE_BAG,
    OBJECT_TYPE_CONTENT_ADDRESS,
    OBJECT_TYPE_PAYLOAD,
    OBJECT_TYPE_PROPOSAL,
    OBJECT_TYPE_RECORD,
    OBJECT_TYPES,
    PremisEvent,
    PremisEventType,
)

_SRC = Path(__file__).resolve().parent.parent / "src" / "ledger"


def _premis_calls() -> list[tuple[Path, ast.Call]]:
    """Every `PremisEvent(...)` construction in the package."""
    found: list[tuple[Path, ast.Call]] = []
    for path in sorted(_SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "PremisEvent":
                found.append((path, node))
    return found


def test_there_are_premis_writers_to_check() -> None:
    """A vacuous pass would make the structural test below meaningless."""
    assert len(_premis_calls()) >= 15


def test_every_writer_that_names_an_object_says_what_kind_it_is() -> None:
    """No `PremisEvent` may set `linked_object` without `linked_object_type`.

    This is the ADR 0012 follow-up as an invariant rather than a one-time sweep.
    Against the pre-change tree it names 18 writers across six modules.
    """
    untyped: list[str] = []
    for path, node in _premis_calls():
        kwargs = {k.arg for k in node.keywords}
        linked = next((k.value for k in node.keywords if k.arg == "linked_object"), None)
        if linked is None:
            continue  # positional or absent: no object named, nothing to type
        if isinstance(linked, ast.Constant) and linked.value is None:
            continue  # explicitly about no object (a whole-archive validation, a rekey)
        if "linked_object_type" not in kwargs:
            untyped.append(f"{path.relative_to(_SRC.parent.parent)}:{node.lineno}")
    assert untyped == [], (
        "PREMIS writers name an object without saying what kind of identifier it is; "
        "they would serialise as the uninformative `local` (ADR 0012):\n  " + "\n  ".join(untyped)
    )


def test_every_declared_type_is_in_the_vocabulary() -> None:
    """A writer may not invent an identifier type outside `OBJECT_TYPES`."""
    known = {
        "OBJECT_TYPE_PAYLOAD",
        "OBJECT_TYPE_RECORD",
        "OBJECT_TYPE_CONTENT_ADDRESS",
        "OBJECT_TYPE_BAG",
        "OBJECT_TYPE_PROPOSAL",
    }
    strays: list[str] = []
    for path, node in _premis_calls():
        for kw in node.keywords:
            if kw.arg != "linked_object_type":
                continue
            src = ast.unparse(kw.value)
            if not isinstance(kw.value, (ast.Name, ast.Attribute)):
                continue  # a computed value (the PREMIS reader round-trips whatever it read)
            if src.split(".")[-1] not in known:
                strays.append(f"{path.name}:{node.lineno} -> {src}")
    assert strays == [], f"identifier types outside the vocabulary: {strays}"


def test_the_vocabulary_and_its_constants_agree() -> None:
    """`OBJECT_TYPES` is the whole set; a constant missing from it is a silent hole."""
    assert {
        OBJECT_TYPE_PAYLOAD,
        OBJECT_TYPE_RECORD,
        OBJECT_TYPE_CONTENT_ADDRESS,
        OBJECT_TYPE_BAG,
        OBJECT_TYPE_PROPOSAL,
    } == OBJECT_TYPES
    assert len(OBJECT_TYPES) == 5  # a duplicated value would collapse the set silently


@pytest.mark.parametrize(
    ("declared", "expected"),
    [
        (OBJECT_TYPE_RECORD, OBJECT_TYPE_RECORD),
        (OBJECT_TYPE_BAG, OBJECT_TYPE_BAG),
        (OBJECT_TYPE_PROPOSAL, OBJECT_TYPE_PROPOSAL),
    ],
)
def test_a_declared_type_is_reported_not_inferred(declared: str, expected: str) -> None:
    """An explicit type wins over inference, for values that look like nothing else."""
    event = PremisEvent(
        event_type=PremisEventType.REPLICATION,
        agent="a",
        outcome="success",
        linked_object="abc123",
        linked_object_type=declared,
    )
    assert event.object_identifier_type == expected
    assert event.to_dict()["linkingObjectIdentifierType"] == expected


def test_an_untyped_record_id_still_refuses_to_guess() -> None:
    """The refusal ADR 0012 built stays: an untyped, non-address id types as nothing.

    This is why typing the writers was the fix, rather than teaching the reader to
    infer. A record id, a bag name and a proposal id are indistinguishable strings.
    """
    event = PremisEvent(
        event_type=PremisEventType.TAKEDOWN, agent="a", outcome="success", linked_object="abc123"
    )
    assert event.object_identifier_type is None
    assert "linkingObjectIdentifierType" not in event.to_dict()


def test_a_content_address_is_still_inferred() -> None:
    """The one safe inference is unchanged (chain stability for pre-ADR-0012 events)."""
    event = PremisEvent(
        event_type=PremisEventType.FIXITY_CHECK,
        agent="a",
        outcome="success",
        linked_object="sha256:" + "ab" * 32,
    )
    assert event.object_identifier_type == OBJECT_TYPE_CONTENT_ADDRESS


def test_an_untyped_event_serialises_exactly_as_it_always_did() -> None:
    """Chain stability: adding the vocabulary must not change an old event's bytes."""
    event = PremisEvent(
        event_type=PremisEventType.TAKEDOWN,
        agent="a",
        outcome="success",
        linked_object="abc123",
        event_datetime="2026-01-01T00:00:00Z",
    )
    assert event.to_dict() == {
        "eventType": PremisEventType.TAKEDOWN.value,
        "eventDateTime": "2026-01-01T00:00:00Z",
        "eventDetail": "",
        "linkingAgentIdentifier": "a",
        "eventOutcome": "success",
        "linkingObjectIdentifier": "abc123",
    }
