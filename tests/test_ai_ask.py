"""End-to-end `ask`/`contexts_for`, against a hand-written fake client.

Covers mission requirement #5 (grounded, tier-respecting discovery), #6
(honest refusal rather than a guess), and the aggregation-attack shape of
requirement #2 (a claim spanning two records still needs one real, disclosed
citation naming one real record).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ledger.ai.ask import ask, contexts_for
from ledger.ai.client import CompletionResult
from ledger.errors import AccessDenied
from tests import ai_fixtures as fx

pytestmark = pytest.mark.disclosure


class _FakeClient:
    def __init__(self, text: str) -> None:
        self._text = text
        self.calls: list[dict[str, object]] = []

    def complete(self, *, system: str, user: str, max_tokens: int) -> CompletionResult:
        self.calls.append({"system": system, "user": user})
        return CompletionResult(text=self._text, backend="fake", model="fake-model-v1")


@pytest.fixture
def seeded(tmp_path: Path):
    archive = fx.build_archive(tmp_path)
    ids = fx.seed(archive)
    return archive, ids


def test_contexts_for_includes_only_visible_records(seeded) -> None:
    archive, ids = seeded
    from ledger.models import DisclosedRecord

    # Simulate a caller that (incorrectly, or maliciously) hands in a record
    # the viewer may NOT see, alongside one they may.
    stub_sealed = DisclosedRecord(
        record_id=ids["sealed"],
        title="x",
        dublin_core={},
        fields={},
        payloads=(),
        content_warnings=(),
        withheld=(),
    )
    disclosed = [*archive.browse(fx.anonymous_grant()), stub_sealed]
    contexts = contexts_for(archive, disclosed, fx.anonymous_grant())
    assert ids["sealed"] not in contexts
    assert ids["public_a"] in contexts
    assert ids["public_b"] in contexts


def test_contexts_for_excludes_a_record_outside_the_grant_tier(seeded) -> None:
    archive, ids = seeded
    from ledger.models import DisclosedRecord

    stub_community = DisclosedRecord(
        record_id=ids["community"],
        title="x",
        dublin_core={},
        fields={},
        payloads=(),
        content_warnings=(),
        withheld=(),
    )
    contexts = contexts_for(archive, [stub_community], fx.anonymous_grant())
    assert contexts == {}


def test_well_formed_grounded_answer_is_verified(seeded) -> None:
    archive, ids = seeded
    contexts = contexts_for(archive, archive.browse(fx.anonymous_grant()), fx.anonymous_grant())
    payload = {
        "claims": [
            {
                "text": "One record is about mutual aid.",
                "citation": {"kind": "dublin_core", "ref": "subject", "record_id": ids["public_a"]},
            }
        ],
        "found_anything": True,
    }
    result = ask(
        "What is this archive about?", contexts, _FakeClient(json.dumps(payload)), commit="c1"
    )
    assert len(result.claims) == 1
    assert result.found_anything is True


def test_refuses_rather_than_guesses_when_nothing_matches(seeded) -> None:
    payload = {"claims": [], "found_anything": False}
    result = ask("anything", {}, _FakeClient(json.dumps(payload)), commit="c1")
    assert result.claims == ()
    assert result.found_anything is False
    assert result.withheld_count == 0


def test_found_anything_is_false_if_every_claim_fails_grounding(seeded) -> None:
    """The model SAYING it found something is not enough -- if every claim it
    offered fails verification, the honest answer is "found nothing"."""
    archive, _ids = seeded
    contexts = contexts_for(archive, archive.browse(fx.anonymous_grant()), fx.anonymous_grant())
    payload = {
        "claims": [{"text": "fabricated", "citation": {"kind": "field", "ref": "nope"}}],
        "found_anything": True,
    }
    result = ask("anything", contexts, _FakeClient(json.dumps(payload)), commit="c1")
    assert result.claims == ()
    assert result.found_anything is False
    assert result.withheld_count == 1


def test_aggregation_attack_claim_across_two_records_is_withheld(seeded) -> None:
    """`public_a` and `public_b` share an org name on purpose (see
    `ai_fixtures.seed`). A claim trying to link a PERSON across them must be
    withheld even though the org name itself is legitimately public in both."""
    archive, ids = seeded
    contexts = contexts_for(archive, archive.browse(fx.anonymous_grant()), fx.anonymous_grant())
    payload = {
        "claims": [
            {
                "text": "The same person wrote both the zine and the flyer.",
                "citation": {"kind": "field", "ref": "story", "record_id": ids["public_a"]},
            }
        ],
        "found_anything": True,
    }
    result = ask(
        "Is this the same person?", contexts, _FakeClient(json.dumps(payload)), commit="c1"
    )
    assert result.claims == ()
    assert result.found_anything is False


def test_legitimate_org_reference_across_two_records_is_not_falsely_blocked(seeded) -> None:
    """Both records legitimately, individually name the Community Health
    Collective -- an org-level claim grounded in one record's own text must
    still pass."""
    archive, ids = seeded
    contexts = contexts_for(archive, archive.browse(fx.anonymous_grant()), fx.anonymous_grant())
    payload = {
        "claims": [
            {
                "text": "The Community Health Collective is mentioned in this record.",
                "citation": {"kind": "field", "ref": "story", "record_id": ids["public_a"]},
            }
        ],
        "found_anything": True,
    }
    result = ask("Who is mentioned?", contexts, _FakeClient(json.dumps(payload)), commit="c1")
    assert len(result.claims) == 1


def test_malformed_json_raises(seeded) -> None:
    from ledger.ai.ask import AskError

    with pytest.raises(AskError):
        ask("anything", {}, _FakeClient("not json"), commit="c1")


def test_disclose_still_gates_even_if_a_record_was_revoked_between_browse_and_ask(seeded) -> None:
    """Belt-and-suspenders: `contexts_for` re-checks disclosure itself rather
    than trusting a caller's earlier `browse` result."""
    archive, ids = seeded
    disclosed = archive.browse(fx.steward_grant())
    # A steward's browse legitimately includes the sealed record; asking as
    # anonymous must still exclude it even though it's in the input list.
    contexts = contexts_for(archive, disclosed, fx.anonymous_grant())
    assert ids["sealed"] not in contexts
    assert ids["stewards"] not in contexts
    with pytest.raises(AccessDenied):
        from ledger.ai.context import build_context

        build_context(archive, ids["sealed"], fx.anonymous_grant())
