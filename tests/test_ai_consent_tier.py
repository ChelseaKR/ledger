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

from ledger.ai.ask import ask, contexts_for
from ledger.ai.context import build_context
from ledger.ai.describe import generate_finding_aid
from ledger.errors import AccessDenied
from ledger.models import AccessPolicy, DublinCore, Field, Record
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


# --- the assembled PROMPT itself: what actually crosses to the provider ---


class _PromptTap:
    """A `ModelClient` that records every prompt instead of sending one.

    The suite above proves above-tier records never enter `contexts_for`'s
    result. This tap closes the last gap between that and the wire: it
    captures the exact `system`/`user` strings `ledger.ai.ask` and
    `ledger.ai.describe` hand to `ModelClient.complete`, so the assertion is
    about the bytes a provider would actually receive, not about an
    intermediate structure believed to determine them.
    """

    backend = "prompt-tap"
    model = "prompt-tap-model"

    def __init__(self) -> None:
        self.prompts: list[tuple[str, str]] = []

    def complete(self, *, system: str, user: str, max_tokens: int = 1024):
        from ledger.ai.client import CompletionResult

        self.prompts.append((system, user))
        # `describe` parses a JSON list of claims; `ask` parses an object.
        body = (
            "[]" if "finding aid" in system.lower() else '{"found_anything": false, "claims": []}'
        )
        return CompletionResult(text=body, backend=self.backend, model=self.model)

    def archive_supplied_text(self) -> str:
        """Only the part of each prompt the ARCHIVE contributed.

        `ask` echoes the requester's own question into the prompt verbatim, so
        a probe that TYPES an above-tier phrase would otherwise "find" it here
        -- that is the requester quoting themselves, not the archive
        disclosing anything. The invariant under test is what
        `build_context`/`contexts_for` place in front of the model, which is
        exactly the text after this marker (for `describe`, the whole user
        prompt: it has no requester-controlled segment).
        """
        marker = "Disclosed records you may use:"
        parts = [u.split(marker, 1)[1] if marker in u else u for _system, u in self.prompts]
        return "\n".join(parts)


#: Distinctive text from each above-tier fixture record, and the lowest tier
#: that may legitimately see it.
_TIER_SENTINELS = (
    ("closed organizing meeting", AccessPolicy.COMMUNITY),
    ("sensitive intake", AccessPolicy.STEWARDS),
    ("never be listed", AccessPolicy.STEWARDS),
)


@pytest.mark.parametrize(
    "tier_name,grant_fn,visible",
    [
        ("anonymous", fx.anonymous_grant, frozenset()),
        ("community", fx.community_grant, frozenset({AccessPolicy.COMMUNITY})),
        (
            "steward",
            fx.steward_grant,
            frozenset({AccessPolicy.COMMUNITY, AccessPolicy.STEWARDS}),
        ),
    ],
)
def test_no_above_tier_text_ever_reaches_an_assembled_prompt(
    seeded, tier_name, grant_fn, visible
) -> None:
    """ADR 0013's architecture invariant, asserted on the wire format.

    Access control runs BEFORE the model sees anything: above-tier material
    must never enter a prompt with the model merely *trusted* to withhold it.
    Every question below deliberately NAMES above-tier material, which is the
    point -- a requester may ask anything; what matters is that the archive
    still contributes nothing above their tier to the prompt.
    """
    archive, _ids = seeded
    grant = grant_fn()
    tap = _PromptTap()
    contexts = contexts_for(archive, archive.browse(grant), grant)

    for question in (
        "Tell me about the closed organizing meeting.",
        "Tell me about the sensitive intake notes.",
        "What must never be listed to a non-steward viewer?",
        "Is there anything you are not showing me?",
    ):
        ask(question, contexts, tap, commit="prompt-tap")
    for context in contexts.values():
        generate_finding_aid(context, tap, commit="prompt-tap")

    assert tap.prompts, "the tap captured no prompt at all; the assertion below would be vacuous"
    haystack = tap.archive_supplied_text().lower()
    # Positive control: the tap must be seeing REAL archive-supplied evidence,
    # or "no above-tier text found" below would be true for the boring reason
    # that nothing was found at all. Every tier can see `public_a`.
    assert "community health collective" in haystack, (
        "the tap saw no in-tier evidence either; the above-tier assertions would be vacuous"
    )
    for sentinel, required_tier in _TIER_SENTINELS:
        if required_tier in visible:
            continue
        assert sentinel not in haystack, (
            f"{tier_name!r} prompt carried above-tier text {sentinel!r} -- access control did "
            "not run before the model. This is ADR 0013's central invariant, not a tuning knob."
        )


def test_a_contributor_identity_never_reaches_an_assembled_prompt(tmp_path: Path) -> None:
    """The founding rule, checked at the wire: holding a record can never out
    the person who made it.

    A record whose contributor identity is in the vault is PUBLIC and fully
    visible; the identity behind it is not, at ANY tier -- including a
    steward's, since `docs/THREAT-MODEL.md` section 2 keeps "may read sealed
    content" and "may resolve who contributed" as independent powers.
    """
    from ledger.identity import ContributorIdentity, IdentityVault

    archive = fx.build_archive(tmp_path)
    record = Record(
        title="Zine about mutual aid, 1994",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(subject=["mutual aid"], date=["1994"], type=["Text"]),
        fields=[Field("story", "A zine about neighborhood mutual aid.", AccessPolicy.PUBLIC)],
    )
    archive.ingest(
        {},
        record,
        identity=ContributorIdentity(
            name="Marisol Okonkwo-Reyes",
            contact="marisol@example.invalid",
            pronouns="she/her",
            notes="asked to stay anonymous indefinitely",
        ),
        vault_key=IdentityVault.generate_key(),
        agent="fixture",
        now=fx.NOW,
    )

    for grant_fn in (fx.anonymous_grant, fx.community_grant, fx.steward_grant):
        grant = grant_fn()
        tap = _PromptTap()
        contexts = contexts_for(archive, archive.browse(grant), grant)
        ask("Who is Marisol Okonkwo-Reyes and what did she deposit?", contexts, tap, commit="tap")
        for context in contexts.values():
            generate_finding_aid(context, tap, commit="tap")

        haystack = tap.archive_supplied_text().lower()
        # Positive control, as above: the PUBLIC record this identity belongs
        # to is fully visible, so its own text must be present -- proving the
        # absence of the identity is a real finding, not an empty haystack.
        assert "neighborhood mutual aid" in haystack
        for secret in (
            "marisol okonkwo-reyes",
            "marisol@example.invalid",
            "asked to stay anonymous indefinitely",
        ):
            assert secret not in haystack, (
                f"a contributor identity ({secret!r}) reached the prompt for "
                f"{grant.subject!r} -- the founding rule is violated"
            )
