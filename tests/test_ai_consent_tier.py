"""THE CONSENT-TIER LEAKAGE SUITE (mission requirement #3).

The AI layer must operate strictly inside the requester's consent tier —
never describe, quote, summarize, or even acknowledge the existence of
material above that tier. Scored on any cross-tier disclosure, INCLUDING
existence disclosure (confirming a record exists is itself a leak, mirroring
`docs/THREAT-MODEL.md` section 4.7's "inference from what is not shown").

Real consent-tier fixtures (`tests/ai_fixtures.py`): PUBLIC, COMMUNITY,
STEWARDS, and an indefinitely SEALED record, exercised across every viewer
tier via the actual access-control gate (`ledger.ai.context.build_context`)
and the actual `ask`/`contexts_for` pipeline — not a mock of either.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ledger.ai.ask import contexts_for
from ledger.ai.context import build_context
from ledger.errors import AccessDenied
from tests import ai_fixtures as fx

pytestmark = pytest.mark.disclosure


@pytest.fixture
def seeded(tmp_path: Path):
    archive = fx.build_archive(tmp_path)
    ids = fx.seed(archive)
    return archive, ids


# --- direct build_context: the access-control gate itself ----------------


@pytest.mark.parametrize(
    "tier_name,grant_fn",
    [("anonymous", fx.anonymous_grant), ("community", fx.community_grant)],
)
def test_stewards_only_record_is_denied_below_steward_tier(seeded, tier_name, grant_fn) -> None:
    archive, ids = seeded
    with pytest.raises(AccessDenied):
        build_context(archive, ids["stewards"], grant_fn())


def test_community_record_is_denied_to_anonymous(seeded) -> None:
    archive, ids = seeded
    with pytest.raises(AccessDenied):
        build_context(archive, ids["community"], fx.anonymous_grant())


@pytest.mark.parametrize(
    "tier_name,grant_fn",
    [
        ("anonymous", fx.anonymous_grant),
        ("community", fx.community_grant),
    ],
)
def test_sealed_record_is_denied_below_steward_tier(seeded, tier_name, grant_fn) -> None:
    archive, ids = seeded
    with pytest.raises(AccessDenied):
        build_context(archive, ids["sealed"], grant_fn())


# --- existence disclosure: contexts_for must silently exclude, not refuse-with-a-name ---


def test_above_tier_record_is_silently_absent_not_named_in_a_refusal(seeded) -> None:
    """Mirrors `Archive.browse`'s own no-padded-listing guarantee
    (`docs/THREAT-MODEL.md` 4.7): a record above this viewer's tier is simply
    not in the returned map. There is no "1 record withheld" placeholder that
    would itself confirm something exists."""
    archive, ids = seeded
    from ledger.models import DisclosedRecord

    stub = DisclosedRecord(
        record_id=ids["stewards"],
        title="x",
        dublin_core={},
        fields={},
        payloads=(),
        content_warnings=(),
        withheld=(),
    )
    contexts = contexts_for(archive, [stub], fx.community_grant())
    assert contexts == {}
    assert ids["stewards"] not in contexts


def test_browse_at_each_tier_never_includes_an_above_tier_record(seeded) -> None:
    """The AI layer's input set (`Archive.browse`) is itself already tier-safe
    -- this is the "access control before the model sees anything" contract
    checked from the AI package's own entry point, not assumed from
    `ledger.access` alone."""
    archive, ids = seeded

    anon_ids = {r.record_id for r in archive.browse(fx.anonymous_grant())}
    assert ids["public_a"] in anon_ids
    assert ids["public_b"] in anon_ids
    assert ids["community"] not in anon_ids
    assert ids["stewards"] not in anon_ids
    assert ids["sealed"] not in anon_ids

    community_ids = {r.record_id for r in archive.browse(fx.community_grant())}
    assert ids["community"] in community_ids
    assert ids["stewards"] not in community_ids
    assert ids["sealed"] not in community_ids

    steward_ids = {r.record_id for r in archive.browse(fx.steward_grant())}
    assert ids["stewards"] in steward_ids
    assert ids["sealed"] in steward_ids


def test_contexts_for_over_full_corpus_respects_every_tier_simultaneously(seeded) -> None:
    """Feed every record in the archive (as a stewards would see it) through
    `contexts_for` under a NON-steward grant, and confirm only the in-tier
    subset survives -- the strongest single assertion that the AI layer
    cannot be tricked into processing above-tier content by a caller that
    passes too much in."""
    archive, ids = seeded
    everything = archive.browse(fx.steward_grant())  # includes sealed + stewards
    assert len(everything) == 5

    contexts = contexts_for(archive, everything, fx.anonymous_grant())
    assert set(contexts) == {ids["public_a"], ids["public_b"]}


# --- evidence content itself never carries an above-tier value -----------


def test_evidence_for_an_in_tier_record_contains_no_above_tier_string(seeded) -> None:
    """Defense in depth: even though the community/stewards/sealed records are
    excluded entirely, also assert their own distinctive text never leaks into
    an unrelated in-tier record's evidence."""
    archive, ids = seeded
    context = build_context(archive, ids["public_a"], fx.anonymous_grant())
    haystack = " ".join(item.text for item in context.evidence)
    assert "closed organizing meeting" not in haystack  # community record's text
    assert "sensitive intake" not in haystack  # stewards record's text
    assert "never be listed" not in haystack  # sealed record's text
