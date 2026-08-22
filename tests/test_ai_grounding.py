"""The verifier that sits before display (mission requirement #1), and its
second job as a structural backstop against outing (mission requirement #2).

This is a pure-function test suite: it builds `GroundedContext` objects
directly (no `Archive`, no model) and asserts `verify_claims` accepts a
correctly-cited claim and withholds every way a claim can fail to be one.
"""

from __future__ import annotations

import pytest

from ledger.ai.context import EvidenceItem, GroundedContext
from ledger.ai.grounding import (
    Citation,
    Claim,
    WithholdReason,
    looks_like_identity_inference,
    verify_claims,
)
from ledger.models import DisclosedRecord, PremisEvent, PremisEventType

pytestmark = pytest.mark.disclosure


def _disclosed(
    record_id: str = "rec-1", title: str = "Zine: Mutual Aid Handbook"
) -> DisclosedRecord:
    return DisclosedRecord(
        record_id=record_id,
        title=title,
        dublin_core={"subject": ["mutual aid"], "date": ["1994"]},
        fields={"story": "A guide distributed by the Community Health Collective."},
        payloads=(),
        content_warnings=(),
        withheld=(),
    )


def _context(record_id: str = "rec-1") -> GroundedContext:
    disclosed = _disclosed(record_id)
    evidence = (
        EvidenceItem("title", "title", disclosed.title),
        EvidenceItem("dublin_core", "subject", "mutual aid"),
        EvidenceItem("dublin_core", "date", "1994"),
        EvidenceItem("field", "story", disclosed.fields["story"]),
    )
    return GroundedContext(record_id=record_id, disclosed=disclosed, events=(), evidence=evidence)


# --- citation grounding -------------------------------------------------


def test_correctly_cited_claim_is_verified() -> None:
    context = _context()
    claim = Claim("This is a zine about mutual aid.", Citation("dublin_core", "subject"))
    result = verify_claims([claim], context)
    assert result.verified == (claim,)
    assert result.withheld_count == 0


def test_claim_with_verbatim_quote_is_verified() -> None:
    context = _context()
    claim = Claim(
        "It was distributed by the Community Health Collective.",
        Citation("field", "story", quote="Community Health Collective"),
    )
    result = verify_claims([claim], context)
    assert result.verified == (claim,)


def test_claim_citing_a_nonexistent_evidence_key_is_withheld() -> None:
    context = _context()
    claim = Claim("Fabricated fact.", Citation("field", "nonexistent"))
    result = verify_claims([claim], context)
    assert result.verified == ()
    assert result.withheld[0][1] is WithholdReason.CITATION_NOT_FOUND


def test_claim_with_a_paraphrase_presented_as_a_quote_is_withheld() -> None:
    context = _context()
    claim = Claim(
        "It says something else.",
        Citation("field", "story", quote="This text is not actually in the field"),
    )
    result = verify_claims([claim], context)
    assert result.withheld[0][1] is WithholdReason.QUOTE_NOT_VERBATIM


# --- preservation-metadata honesty (mission requirement #4) --------------


def _context_with_events(events: tuple[PremisEvent, ...]) -> GroundedContext:
    disclosed = _disclosed()
    events_evidence = tuple(
        EvidenceItem(
            "premis_event",
            str(i),
            f"{e.event_type.value}: {e.outcome} ({e.event_datetime})",
        )
        for i, e in enumerate(events)
    )
    evidence = (
        EvidenceItem("title", "title", disclosed.title),
        EvidenceItem("dublin_core", "subject", "mutual aid"),
        EvidenceItem("field", "story", disclosed.fields["story"]),
        *events_evidence,
    )
    return GroundedContext(record_id="rec-1", disclosed=disclosed, events=events, evidence=evidence)


def test_verified_language_over_a_successful_fixity_event_is_accepted() -> None:
    event = PremisEvent(
        event_type=PremisEventType.FIXITY_CHECK,
        agent="a",
        outcome="success",
        event_datetime="2026-01-01T00:00:00Z",
    )
    context = _context_with_events((event,))
    claim = Claim("The audio file's fixity was verified.", Citation("premis_event", "0"))
    result = verify_claims([claim], context)
    assert result.verified == (claim,)


def test_verified_language_over_a_failed_fixity_event_is_withheld() -> None:
    event = PremisEvent(
        event_type=PremisEventType.FIXITY_CHECK,
        agent="a",
        outcome="failure",
        event_datetime="2026-01-01T00:00:00Z",
    )
    context = _context_with_events((event,))
    claim = Claim("The audio file's fixity was verified.", Citation("premis_event", "0"))
    result = verify_claims([claim], context)
    assert result.verified == ()
    assert result.withheld[0][1] is WithholdReason.FIXITY_DISHONESTY


def test_authentic_language_over_an_ingestion_event_is_withheld() -> None:
    """Citing a real event that is not a successful fixity check at all --
    "authentic" claimed from a bare ingestion event -- is the same defect."""
    event = PremisEvent(
        event_type=PremisEventType.INGESTION,
        agent="a",
        outcome="success",
        event_datetime="2026-01-01T00:00:00Z",
    )
    context = _context_with_events((event,))
    claim = Claim("This file is authentic.", Citation("premis_event", "0"))
    result = verify_claims([claim], context)
    assert result.verified == ()
    assert result.withheld[0][1] is WithholdReason.FIXITY_DISHONESTY


def test_verified_language_about_an_unrelated_topical_claim_is_not_flagged() -> None:
    """ "Confirmed"/"verified" outside a payload/premis_event citation is an
    ordinary word, not a fixity claim -- must not trip this check."""
    context = _context()
    claim = Claim(
        "The meeting is confirmed to have happened in 1994.",
        Citation("dublin_core", "date"),
    )
    result = verify_claims([claim], context)
    assert result.verified == (claim,)


def test_a_mix_of_good_and_bad_claims_splits_correctly() -> None:
    context = _context()
    good = Claim("About mutual aid.", Citation("dublin_core", "subject"))
    bad = Claim("Fabricated.", Citation("field", "made_up"))
    result = verify_claims([good, bad], context)
    assert result.verified == (good,)
    assert result.withheld_count == 1


# --- the outing backstop: verbatim name grounding -----------------------


def test_fabricated_name_not_in_evidence_is_withheld_even_with_a_real_citation_key() -> None:
    """The gap this closes: a claim citing a REAL evidence key, with no quote
    to check, could otherwise assert anything -- including a fabricated
    person's name -- about that key."""
    context = _context()
    claim = Claim("The contributor is definitely Jane Doe.", Citation("field", "story"))
    result = verify_claims([claim], context)
    assert result.verified == ()
    assert result.withheld[0][1] is WithholdReason.IDENTITY_INFERENCE


def test_a_name_that_really_is_in_the_evidence_is_not_penalized() -> None:
    """The backstop must not over-fire on a real organization name that is
    genuinely present in the disclosed evidence."""
    context = _context()
    claim = Claim("It credits the Community Health Collective.", Citation("field", "story"))
    result = verify_claims([claim], context)
    assert result.verified == (claim,)


# --- looks_like_identity_inference: many phrasings -----------------------


@pytest.mark.parametrize(
    "text",
    [
        "She is undocumented based on the story.",
        "The interviewee is HIV-positive.",
        "The narrator is likely HIV-positive.",
        "He is gay.",
        "They are likely transgender.",
        "The contributor is closeted.",
        "The subject is not out to their family.",
        "This appears to be the same person as in another record.",
        "It is probably the same person.",
        "His real identity is someone else entirely.",
        "Her true name is different from what's written.",
        "The interviewee lives in San Francisco.",
        "Maria Gonzalez is likely undocumented.",
    ],
)
def test_identity_inference_language_is_detected(text: str) -> None:
    assert looks_like_identity_inference(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "This is a zine about gay liberation organizing.",
        "The collection covers mutual aid and health topics.",
        "This record documents a clinic night in 1995.",
        "The content warning is outing.",
        "This item is likely from the mid-1990s.",
        "The subject tags are mutual aid and health.",
    ],
)
def test_topical_description_is_not_flagged(text: str) -> None:
    """The backstop must not swallow ordinary, legitimate topical description
    of what a queer archive's records are ABOUT -- only claims about a
    specific person's identity."""
    assert looks_like_identity_inference(text) is False


# --- multi-record grounding (the `ask` aggregation-attack shape) ---------


def test_multi_record_citation_resolves_by_record_id() -> None:
    context_a = _context("rec-a")
    context_b = _context("rec-b")
    claim = Claim(
        "Record A is about mutual aid.",
        Citation("dublin_core", "subject", record_id="rec-a"),
    )
    result = verify_claims([claim], {"rec-a": context_a, "rec-b": context_b})
    assert result.verified == (claim,)


def test_multi_record_citation_to_the_wrong_record_id_is_withheld() -> None:
    context_a = _context("rec-a")
    context_b = _context("rec-b")
    claim = Claim("Claims record c exists.", Citation("dublin_core", "subject", record_id="rec-c"))
    result = verify_claims([claim], {"rec-a": context_a, "rec-b": context_b})
    assert result.verified == ()
    assert result.withheld[0][1] is WithholdReason.CITATION_NOT_FOUND


def test_cross_record_identity_claim_has_nothing_to_cite() -> None:
    """The aggregation-attack case: no single evidence item in EITHER record's
    context ever contains a cross-record identity claim for the model to cite
    -- so any such claim is necessarily either uncited (withheld) or reads as
    an inference (withheld), never both grounded AND accepted."""
    context_a = _context("rec-a")
    context_b = _context("rec-b")
    claim = Claim(
        "The same person made both records.",
        Citation("field", "story", record_id="rec-a"),
    )
    result = verify_claims([claim], {"rec-a": context_a, "rec-b": context_b})
    assert result.verified == ()
    assert result.withheld[0][1] is WithholdReason.IDENTITY_INFERENCE
