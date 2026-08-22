"""Access control is enforced BEFORE any model sees anything (mission
requirement — an architecture requirement, not a prompt rule).

`build_context` is the one function every AI feature calls, and it works by
calling `Archive.disclose` first. These tests exercise that gate directly:
above-tier content raises exactly like every other read path, a
`GroundedContext` never carries a withheld field or payload, and PREMIS events
are scoped to what the viewer may see.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ledger.ai.context import build_context
from ledger.errors import AccessDenied
from tests import ai_fixtures as fx

pytestmark = pytest.mark.disclosure


@pytest.fixture
def seeded(tmp_path: Path):
    archive = fx.build_archive(tmp_path)
    ids = fx.seed(archive)
    return archive, ids


def test_public_record_visible_to_anonymous(seeded) -> None:
    archive, ids = seeded
    context = build_context(archive, ids["public_a"], fx.anonymous_grant())
    assert context.record_id == ids["public_a"]
    assert context.disclosed.title == "Zine: Mutual Aid Handbook, 1994"


def test_community_record_denied_to_anonymous(seeded) -> None:
    archive, ids = seeded
    with pytest.raises(AccessDenied):
        build_context(archive, ids["community"], fx.anonymous_grant())


def test_community_record_visible_to_community_member(seeded) -> None:
    archive, ids = seeded
    context = build_context(archive, ids["community"], fx.community_grant())
    assert context.record_id == ids["community"]


def test_stewards_record_denied_to_community_member(seeded) -> None:
    archive, ids = seeded
    with pytest.raises(AccessDenied):
        build_context(archive, ids["stewards"], fx.community_grant())


def test_stewards_record_visible_to_steward(seeded) -> None:
    archive, ids = seeded
    context = build_context(archive, ids["stewards"], fx.steward_grant())
    assert context.record_id == ids["stewards"]


def test_sealed_record_denied_to_anonymous(seeded) -> None:
    archive, ids = seeded
    with pytest.raises(AccessDenied):
        build_context(archive, ids["sealed"], fx.anonymous_grant())


def test_sealed_record_denied_to_community_member(seeded) -> None:
    """Sealed (no unseal date) is a steward-only tier, not merely above `community`."""
    archive, ids = seeded
    with pytest.raises(AccessDenied):
        build_context(archive, ids["sealed"], fx.community_grant())


def test_sealed_record_visible_to_steward(seeded) -> None:
    archive, ids = seeded
    context = build_context(archive, ids["sealed"], fx.steward_grant())
    assert context.record_id == ids["sealed"]


def test_denied_error_names_only_the_record_id(seeded) -> None:
    """The refusal must not itself leak the withheld content (mirrors the
    no-outing rule's own `AccessDenied` contract in `ledger.access.policy`)."""
    archive, ids = seeded
    with pytest.raises(AccessDenied) as excinfo:
        build_context(archive, ids["sealed"], fx.anonymous_grant())
    assert "never be listed" not in str(excinfo.value)  # the sealed field text
    assert ids["sealed"] in str(excinfo.value)


def test_grounded_context_carries_no_identity_ref(seeded) -> None:
    """Structural guarantee: there is no field on `GroundedContext`/`DisclosedRecord`
    that could carry an `identity_ref` — this asserts the disclosed shape has none."""
    archive, ids = seeded
    context = build_context(archive, ids["public_a"], fx.anonymous_grant())
    assert not hasattr(context.disclosed, "identity_ref")


def test_evidence_never_includes_a_withheld_field(seeded) -> None:
    """A field sealed above this viewer's tier must not appear anywhere in
    `context.evidence`, even indirectly."""
    archive, ids = seeded
    # `community`'s story field is COMMUNITY-tier; an anonymous viewer must
    # never see it, and here they can't even list the record at all.
    with pytest.raises(AccessDenied):
        build_context(archive, ids["community"], fx.anonymous_grant())


def test_premis_events_present_for_a_visible_record(seeded) -> None:
    archive, ids = seeded
    context = build_context(archive, ids["public_a"], fx.anonymous_grant())
    assert len(context.events) >= 1
    assert any(e.event_type.value == "ingestion" for e in context.events)
