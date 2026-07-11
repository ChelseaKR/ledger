"""Offline, on-device assistant that highlights likely-identifying text in a
contributor's account — names, addresses, phone numbers, emails, handles, and
dates — at contribute/edit time.

Threat model §4.3 is explicit that self-disclosure in the free-text account is
the largest residual risk the archive's own disclosure machinery cannot police,
because a contributor can write an identifying detail into the *body* of their
story rather than a policy-gated field. This module is a harm-reduction
**assist**, never a gate: it flags candidate spans for a contributor (or a
steward reviewing a submission) to look at, and links each finding to the
existing per-field sealing workflow (``ledger seal`` / ``ledger redact``,
:mod:`ledger.moderate`) — it never edits, drops, blocks, or auto-applies a
redaction itself. Only a steward's or contributor's own explicit choice does
that; this module only *suggests*.

Everything here is regex plus a small bundled wordlist, evaluated in-process on
text already in memory: no model download, no subprocess, no socket opened by
this module, ever (:func:`suggest` is a pure function of its input — see
``tests/test_redact_suggest.py::test_no_network_import``). It deliberately
trades recall for auditability — every rule is a few lines any reviewer can
read — and callers must surface the honest caveat that this finds *some*, not
all, identifying detail (never phrase results as a completeness guarantee;
see :data:`CAVEAT`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

#: The one honest, unconditional caveat every UI surface showing suggestions
#: from this module must display verbatim or paraphrase without weakening —
#: false confidence in a "yes this is safe now" reading is the failure mode
#: (EXP-07 risk). Never present a suggestion count as a completeness proof.
CAVEAT = (
    "This automatic check looks for common patterns only — names, addresses, "
    "phone numbers, emails, handles, and dates. It finds some identifying "
    "details, not all of them. Please still read your account yourself before "
    "it is shared."
)


class SuggestionKind(StrEnum):
    """The category of identifying detail a span was flagged as."""

    NAME = "name"
    ADDRESS = "address"
    PHONE = "phone"
    EMAIL = "email"
    HANDLE = "handle"
    DATE = "date"


@dataclass(frozen=True)
class Suggestion:
    """One candidate identifying span found in a text.

    ``start``/``end`` are character offsets into the text that was scanned
    (Python slice semantics: ``text[start:end] == text``), so a caller can
    highlight the exact span without re-searching. ``text`` carries the
    matched substring for host-side display context; nothing in this module
    ever removes or alters it.
    """

    kind: SuggestionKind
    start: int
    end: int
    text: str


# --- wordlist ----------------------------------------------------------------
# A small, deliberately partial set of common given names (English plus a
# handful of common names from other widely-represented naming traditions),
# used only to raise confidence that a capitalized word pair is a *personal*
# name rather than any other proper-noun pair (a place, an org, a book title).
# Its incompleteness is exactly the "finds some, not all" honesty the caller's
# UI text must carry through — this is a regex/wordlist tier by design (EXP-07
# effort estimate "M"), not a NER model.
_COMMON_GIVEN_NAMES = frozenset(
    """
    james mary john patricia robert jennifer michael linda david elizabeth
    william barbara richard susan joseph jessica thomas sarah charles karen
    christopher nancy daniel lisa matthew betty anthony margaret mark sandra
    donald ashley steven kimberly andrew emily paul donna joshua michelle
    kenneth carol kevin amanda brian melissa george deborah edward stephanie
    ronald rebecca timothy sharon jason laura jeffrey cynthia ryan kathleen
    jacob amy gary angela nicholas shirley eric anna jonathan brenda stephen
    pamela larry emma justin nicole scott helen brandon samantha benjamin
    katherine samuel christine raymond debra gregory rachel alexander carolyn
    patrick janet jack maria dennis olivia jerry heather tyler diane aaron
    julie jose joyce henry victoria adam kelly douglas christina nathan
    lauren peter joan zachary evelyn kyle judith noah megan alan andrea juan
    cheryl carl hannah harold jacqueline jordan martha arthur gloria gerald
    teresa keith ann roger sara wei chen li wang zhang liu yang huang priya
    amit raj fatima ahmed muhammad aisha omar layla carlos sofia luis
    valentina diego camila javier mariana kwame amara chidi ngozi
    """.split()  # noqa: SIM905 - prose word list is far more maintainable/reviewable
    # than a one-per-line list literal for ~150 names.
)

_ADDRESS_SUFFIXES = (
    r"street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|"
    r"court|ct|place|pl|way|terrace|ter|circle|cir|highway|hwy|"
    r"parkway|pkwy|square|sq|trail|trl|loop|alley"
)

_RE_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RE_HANDLE = re.compile(r"(?<![\w@])@[A-Za-z0-9_]{2,30}\b")
_RE_PHONE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")
_RE_ADDRESS = re.compile(
    rf"\b\d{{1,5}}\s+(?:[A-Z][a-zA-Z'.-]*\s+){{1,4}}(?:{_ADDRESS_SUFFIXES})\b\.?",
    re.IGNORECASE,
)
_RE_DATE = re.compile(
    r"\b("
    r"\d{4}-\d{2}-\d{2}"  # ISO 8601
    r"|\d{1,2}/\d{1,2}/\d{2,4}"  # US slash form
    r"|(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
    r"Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}"
    r")\b",
    re.IGNORECASE,
)
_RE_CAP_WORD_PAIR = re.compile(r"\b([A-Z][a-z]+)\s+([A-Z][a-z]+)\b")


def _find(pattern: re.Pattern[str], kind: SuggestionKind, text: str) -> list[Suggestion]:
    return [Suggestion(kind, m.start(), m.end(), m.group(0)) for m in pattern.finditer(text)]


def _find_names(text: str) -> list[Suggestion]:
    """Capitalized ``First Last`` spans whose first word is a common given name.

    A narrow, low-noise heuristic, not full NER: it under-flags (many real
    names use a first name outside the bundled list) far more than it
    over-flags, which is the deliberately conservative trade-off for a
    suggestion tool that must never manufacture false confidence in the
    *other* direction (flooding a contributor with noise they learn to
    ignore is its own failure mode).
    """
    return [
        Suggestion(SuggestionKind.NAME, m.start(), m.end(), m.group(0))
        for m in _RE_CAP_WORD_PAIR.finditer(text)
        if m.group(1).lower() in _COMMON_GIVEN_NAMES
    ]


def suggest(text: str) -> list[Suggestion]:
    """Return candidate identifying spans in ``text``, ordered by position.

    Pure and entirely offline: a plain function over an in-memory string, safe
    to call on every contribute/edit preview render or from the CLI. Overlap
    between rules (e.g. a date inside an address-like span) is left as-is —
    callers render suggestions for a human to judge; this function never
    merges, edits, or removes anything from ``text``.
    """
    found = [
        *_find(_RE_EMAIL, SuggestionKind.EMAIL, text),
        *_find(_RE_HANDLE, SuggestionKind.HANDLE, text),
        *_find(_RE_PHONE, SuggestionKind.PHONE, text),
        *_find(_RE_ADDRESS, SuggestionKind.ADDRESS, text),
        *_find(_RE_DATE, SuggestionKind.DATE, text),
        *_find_names(text),
    ]
    found.sort(key=lambda s: (s.start, s.end))
    return found


def summary_counts(suggestions: list[Suggestion]) -> dict[str, int]:
    """Counts per :class:`SuggestionKind` value, for a one-line "found N" chrome."""
    counts: dict[str, int] = {}
    for s in suggestions:
        counts[s.kind.value] = counts.get(s.kind.value, 0) + 1
    return counts
