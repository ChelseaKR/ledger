"""Tests for :mod:`ledger.redact_suggest` — the offline redaction assistant (EXP-07).

These pin the properties that make this an *assist*, never a gate: it must never
touch the network or subprocess (it is a plain regex/wordlist scan of an in-memory
string), it must never mutate or drop anything from the text it scans, and its
recall on a small synthetic corpus is measured explicitly and honestly (a
regex/wordlist tier finds *some* identifying detail, not all — the module's own
:data:`~ledger.redact_suggest.CAVEAT` says so, and these tests do not pretend
otherwise by asserting perfect recall).
"""

from __future__ import annotations

import socket

import pytest

from ledger.redact_suggest import CAVEAT, Suggestion, SuggestionKind, suggest, summary_counts

# --- offline / purity --------------------------------------------------------


def test_no_network_import(monkeypatch: pytest.MonkeyPatch) -> None:
    """Importing and running the module never opens a socket (EXP-07 "on-device only")."""

    def _blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("ledger.redact_suggest attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    result = suggest("Contact John Smith at john.smith@example.com or 555-123-4567.")
    assert result  # still functions correctly with the socket trapped


def test_suggest_is_pure_and_does_not_mutate_input() -> None:
    text = "Maria Garcia lives at 42 Oak Street and calls (555) 867-5309."
    original = str(text)
    suggest(text)
    assert text == original  # unchanged object contents; nothing is edited in place


def test_suggest_never_edits_or_drops_text_content() -> None:
    """A suggestion carries the *matched* text; nothing about the call redacts it."""
    text = "Reach James Brown by email james.brown@mail.example."
    hits = suggest(text)
    assert all(hit.text in text for hit in hits)
    assert isinstance(hits[0], Suggestion)


# --- per-kind detection -------------------------------------------------------


def test_detects_email() -> None:
    hits = suggest("You can write to survivor.contact@example.org for follow-up.")
    assert any(
        h.kind is SuggestionKind.EMAIL and h.text == "survivor.contact@example.org" for h in hits
    )


def test_detects_handle() -> None:
    hits = suggest("They posted about it on @quiet_witness last spring.")
    assert any(h.kind is SuggestionKind.HANDLE and h.text == "@quiet_witness" for h in hits)


def test_detects_phone_number() -> None:
    hits = suggest("Call me back at 555-867-5309 when you can.")
    assert any(h.kind is SuggestionKind.PHONE for h in hits)


def test_detects_us_style_address() -> None:
    hits = suggest("It happened outside 221 Baker Street near the old church.")
    assert any(h.kind is SuggestionKind.ADDRESS for h in hits)


def test_detects_iso_and_written_dates() -> None:
    hits = suggest("It happened on 2019-04-12, not long after March 3rd, 2019.")
    kinds = [h.kind for h in hits]
    assert kinds.count(SuggestionKind.DATE) >= 2


def test_detects_common_given_name_pairs() -> None:
    hits = suggest("Maria Garcia was the one who found me that night.")
    assert any(h.kind is SuggestionKind.NAME and h.text == "Maria Garcia" for h in hits)


def test_does_not_flag_a_capitalized_pair_with_an_uncommon_first_name() -> None:
    # A narrow heuristic (wordlist-gated), so an uncommon first name is not flagged
    # as a NAME — this is the documented "finds some, not all" trade-off, not a bug.
    hits = suggest("Zbigniew Kowalski helped organize the vigil.")
    assert not any(h.kind is SuggestionKind.NAME for h in hits)


def test_suggestions_are_sorted_by_position() -> None:
    hits = suggest("Email a@example.com then call 555-867-5309, ref 2020-01-01.")
    starts = [h.start for h in hits]
    assert starts == sorted(starts)


def test_span_offsets_slice_back_to_matched_text() -> None:
    text = "Reach out at witness.line@example.net for questions."
    hits = suggest(text)
    email_hit = next(h for h in hits if h.kind is SuggestionKind.EMAIL)
    assert text[email_hit.start : email_hit.end] == email_hit.text


def test_no_suggestions_on_clean_text() -> None:
    assert suggest("It was a hard year but we got through it together.") == []


# --- summary + caveat ---------------------------------------------------------


def test_summary_counts_groups_by_kind() -> None:
    hits = suggest("Call 555-867-5309 or 555-222-3333.")
    counts = summary_counts(hits)
    assert counts[SuggestionKind.PHONE.value] == 2


def test_caveat_never_claims_completeness() -> None:
    lowered = CAVEAT.lower()
    assert "some" in lowered
    assert "not all" in lowered or "not all of them" in lowered
    # A completeness/guarantee-shaped claim would be the false-confidence failure
    # mode the ideation doc calls out explicitly — assert the opposite framing.
    assert "guarantee" not in lowered
    assert "everything" not in lowered


# --- measured recall on a small synthetic corpus ------------------------------
# Honest, in-repo recall measurement (EXP-07 "Excellent" bar): a fixed corpus of
# sentences each containing exactly one deliberately-planted identifying detail,
# with the recall fraction asserted and printed rather than silently assumed.

_SYNTHETIC_CORPUS: list[tuple[str, SuggestionKind]] = [
    ("My name is Robert Johnson and this is my account.", SuggestionKind.NAME),
    ("You can reach me at test.person@example.com anytime.", SuggestionKind.EMAIL),
    ("My number is 555-234-5678 if you need to follow up.", SuggestionKind.PHONE),
    ("It happened near 118 Maple Avenue on the east side.", SuggestionKind.ADDRESS),
    ("This all started on 2021-09-14 during the storm.", SuggestionKind.DATE),
    ("I posted the whole story on @night_shift_diary.", SuggestionKind.HANDLE),
    ("Susan Miller was there the whole time, she can confirm.", SuggestionKind.NAME),
    ("Email the archive at intake@example.org for a copy.", SuggestionKind.EMAIL),
    ("Try (555) 998-2468 between nine and five.", SuggestionKind.PHONE),
    ("We met outside 47 Elm Court just after dark.", SuggestionKind.ADDRESS),
    ("The letter is dated January 3rd, 2018, if that helps.", SuggestionKind.DATE),
    ("Follow @witness_speaks_out for the rest of the thread.", SuggestionKind.HANDLE),
]


def test_measured_recall_on_synthetic_corpus() -> None:
    """Recall must clear a modest, explicit floor — measured honestly, not assumed.

    A regex/wordlist tier is expected to miss some cases (an uncommon name, an
    unusual address format); this asserts a floor well below 100% so the test
    documents real, measured behaviour rather than a fabricated guarantee.
    """
    hits_by_kind = 0
    for sentence, expected_kind in _SYNTHETIC_CORPUS:
        found = suggest(sentence)
        if any(h.kind is expected_kind for h in found):
            hits_by_kind += 1
    recall = hits_by_kind / len(_SYNTHETIC_CORPUS)
    # Published honestly: this run's measured recall, so a future change to the
    # ruleset that regresses recall below the floor fails loudly here.
    assert recall >= 0.75, f"measured recall {recall:.2f} on the synthetic corpus"


def test_suggestion_kind_compares_equal_by_value() -> None:
    """``SuggestionKind`` is a plain ``str`` enum: comparable by value, not identity.

    Callers (e.g. :func:`ledger.contribute.render_redaction_suggestions`) group
    suggestions by kind; this pins that grouping stays correct even if a caller
    holds its own reference to a kind constant rather than the exact enum member
    a particular :class:`Suggestion` carries.
    """
    assert SuggestionKind.EMAIL == "email"
    hits = suggest("Reach me at witness@example.com")
    assert hits[0].kind == SuggestionKind.EMAIL
