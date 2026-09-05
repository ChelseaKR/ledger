"""The verifier that sits before display (mission requirement #1).

Every substantive claim an AI finding aid or answer makes must cite the
specific evidence it came from, and this module checks that citation resolves
against the disclosed :class:`~ledger.ai.context.GroundedContext` — not
against the model's say-so — before anything is shown. A claim that fails is
WITHHELD and counted, never shown.

This module also does double duty as the second, structural line of defense
against outing (mission requirement #2). A claim naming a real person can
only ever be *grounded* in an already-disclosed field or quote, because that
is the only text a ``GroundedContext`` can contain (no vault identity, no
withheld field, ever reaches it — see :mod:`ledger.ai.context`). So a model
that hallucinates an identity claim can only ever produce an UNGROUNDED one,
which this verifier strips exactly like any other unsupported claim. On top
of that structural guarantee, :func:`looks_like_identity_inference` is a
narrow, deterministic backstop that refuses a claim whose *language* reads as
an identity guess even when it cites real evidence — because a person can be
named correctly in a public field and a claim can still cross the line by
speculating about who they "really" are. The guarantee is architecture
(mission requirement) plus behaviour (the system prompts in
:mod:`ledger.ai.prompts`), each proven by ``tests/test_ai_outing_refusal.py``
— this regex is a cheap backstop, not the guarantee itself, the same honesty
:mod:`ledger.redact_suggest` states about its own pattern matching.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from ledger.ai.context import GroundedContext

__all__ = [
    "Citation",
    "Claim",
    "GroundingResult",
    "WithholdReason",
    "looks_like_identity_inference",
    "verify_claims",
]


class WithholdReason(StrEnum):
    """Why one claim was withheld rather than shown."""

    CITATION_NOT_FOUND = "cited evidence does not exist in what was disclosed"
    QUOTE_NOT_VERBATIM = "quoted text is not an exact excerpt of the cited evidence"
    IDENTITY_INFERENCE = "claim reads as an identity inference or guess; refused unconditionally"
    CROSS_RECORD_LINKAGE = (
        "claim casts a proper noun that only another record disclosed as a person "
        "involved in this one; cross-record linkage is refused"
    )
    FIXITY_DISHONESTY = (
        "claim uses verification language about a payload's fixity without a "
        "successful PREMIS fixity-check event to support it"
    )


@dataclass(frozen=True)
class Citation:
    """What evidence a claim says it comes from.

    ``kind``/``ref`` must match an :class:`~ledger.ai.context.EvidenceItem`
    exactly. ``record_id`` disambiguates a citation when a claim is verified
    against more than one record's context (the multi-record ``ask`` path);
    it may be left empty when there is only one context to check against.
    ``quote``, if set, must be an exact substring of the cited evidence's
    text — a paraphrase presented as a quote is refused.
    """

    kind: str
    ref: str
    record_id: str = ""
    quote: str = ""


@dataclass(frozen=True)
class Claim:
    """One claim the model made, with the citation it offered for it."""

    text: str
    citation: Citation


@dataclass(frozen=True)
class GroundingResult:
    """The split of a claim list into verified and withheld, plus why."""

    verified: tuple[Claim, ...]
    withheld: tuple[tuple[Claim, WithholdReason], ...]

    @property
    def withheld_count(self) -> int:
        return len(self.withheld)


# A cheap, deliberately narrow backstop, in two independent parts.
#
# Part 1: a PERSON REFERENCE (a Capitalized Full Name, a personal pronoun, or
# "the contributor/interviewee/...") followed by a stated or hedged identity
# category. Requiring the person-reference is what lets a *topical* claim
# through ("this zine is about gay liberation organizing") while still
# catching a claim ABOUT a specific person ("she is undocumented", "the
# interviewee is HIV-positive") -- scoped to the concrete categories the
# mission names (identity, sexuality, gender history, HIV status,
# immigration status, legal history), stated OR hedged: the mission forbids
# both "he is gay" and "he is likely gay".
_PERSON_REF = (
    r"(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+|"  # a Capitalized Full Name
    r"he|she|they|him|her|them|his|her|their|"
    r"the (?:contributor|interviewee|narrator|subject|author|photographer|"
    r"organizer|depositor|person))"
)
_IDENTITY_CATEGORY = (
    r"(?:gay|lesbian|bisexual|bi|transgender|trans|queer|"
    r"undocumented|HIV[- ]positive|HIV[- ]negative|closeted|not out|"
    r"out as \w+|in (?:the country|the US|the United States) illegally)"
)
# The optional `(?:,?\s+who)?` covers a relative clause -- "Jordan Ellis, who
# is closeted" -- which would otherwise slip through: without it, the person
# -reference and the verb are required to sit directly adjacent, and a comma
# plus "who" breaks that adjacency (found via the adversarial eval harness,
# `tools/ai_eval.py`).
_RE_STATED_IDENTITY = re.compile(
    rf"\b{_PERSON_REF}(?:,?\s+who)?\s+(?:is|are|was|were|identifies as|appears to be|"
    rf"(?:is|are)\s+(?:actually|really|probably|likely))\s+{_IDENTITY_CATEGORY}\b",
    re.IGNORECASE,
)
_RE_REAL_IDENTITY = re.compile(
    r"\b(?:his|her|their|its)\s+(?:real|true|actual)\s+(?:identity|name)\s+is\b",
    re.IGNORECASE,
)
# Deliberately broad and unconditional (no verb-framing precondition, unlike
# the patterns above): "same person" is the aggregation-attack's own words,
# in an archive context, regardless of grammatical framing ("X is the same
# person as Y" / "the same person made both" / "these are the same person").
# The false-positive cost of over-blocking a rare, unrelated legitimate use of
# the phrase is far smaller than the cost of missing a cross-record identity
# linkage claim.
_RE_SAME_PERSON = re.compile(r"\bsame person\b", re.IGNORECASE)
_RE_LIVES_AT = re.compile(
    rf"\b{_PERSON_REF}\s+(?:lives?|resides?|is (?:located|based))\s+(?:in|at|near)\s+[A-Z]",
    re.IGNORECASE,
)

# A capitalized multi-word span, the shape of a person's name (mirrors
# `ledger.redact_suggest`'s own name heuristic).
_RE_NAME_SPAN = re.compile(r"\b[A-Z][a-zA-Z'\u2019-]+(?:\s+[A-Z][a-zA-Z'\u2019-]+)+\b")

# --- the single-token half of the same problem (issue #153) ------------------
#
# `_RE_NAME_SPAN` requires TWO or more capitalized words, so a one-word
# nickname cleared every deterministic guard: "Jordan Ellis ran the free
# clinic" (cited to the clinic record) was withheld as an ungrounded name
# span, while "Cricket ran the free clinic" -- Cricket being a nickname
# public in a DIFFERENT record's photo caption -- was shown.
#
# Widening `_RE_NAME_SPAN` to single tokens is not the fix. Every
# sentence-initial word is capitalized, so it would withhold a large fraction
# of ordinary, legitimate claims, and over-refusal is its own failure mode
# for a usable archive.
#
# What the mission actually forbids is narrower than "a capitalized word":
# linking a person across records from a non-name signal. That shape needs
# THREE things true at once, and only ever on the multi-record `ask` path:
#
#   1. the token is NOT present in the evidence of the record the claim
#      cites -- so this record never disclosed it;
#   2. it IS present, capitalized, in some OTHER record disclosed to this
#      same viewer -- so it is a proper noun the model read elsewhere, not a
#      sentence-initial ordinary word it happened to capitalize; and
#   3. the claim frames that token as a person taking part in something -- a
#      verb of involvement, or "is/was the <role>".
#
# Any two of the three are ordinary and stay shown. All three together are
# the aggregation signature, and no other check in this module can see it.
_RE_CAPITALIZED_TOKEN = re.compile(r"\b[A-Z][a-zA-Z'\u2019-]+\b")
# Verbs of taking part, not verbs in general: the claim has to say this
# person DID something, which is what turns a bare proper noun into a link
# between two records.
_INVOLVEMENT_VERB = (
    r"(?:ran|runs?|organi[sz](?:ed|es)|coordinat(?:ed|es)|led|leads?|"
    r"found(?:ed|s)|start(?:ed|s)|staff(?:ed|s)|volunteer(?:ed|s)|"
    r"host(?:ed|s)|manag(?:ed|es)|direct(?:ed|s)|chair(?:ed|s)|"
    r"attend(?:ed|s)|join(?:ed|s)|work(?:ed|s)|serv(?:ed|es)|"
    r"deposit(?:ed|s)|donat(?:ed|es)|contribut(?:ed|es)|"
    r"wrote|writes|made|makes|took|takes|photograph(?:ed|s)|taught|teaches)"
)
_ROLE_NOUN = (
    r"(?:coordinator|organi[sz]er|director|volunteer|founder|leader|steward|"
    r"contributor|depositor|photographer|author|narrator|interviewee|person|"
    r"nurse|doctor|clinician|member|chair|host)"
)
# The token, then optionally a relative clause or an adverbial hop
# ("Cricket, who also ran ..."), then the involvement language. Requiring
# that adjacency is what keeps "These volunteers ran the clinic" out: the
# capitalized token has to be the thing doing the verb. The bounded
# `{0,3}` gap before a role noun covers the modifiers a real sentence puts
# there ("is the clinic coordinator", "was one of the organizers").
_INVOLVEMENT_FRAME = (
    r"(?:,?\s+(?:who|also|then|later|again|apparently|reportedly|probably|likely))*"
    rf"\s+(?:{_INVOLVEMENT_VERB}\b|"
    rf"(?:is|was|are|were)\s+(?:the\s+|an?\s+)?(?:\w+[\s-]+){{0,3}}{_ROLE_NOUN}\b)"
)
# The same sentence in the passive voice ("the free clinic was run by
# Cricket"), where the token sits AFTER the verb and the active frame above
# structurally cannot see it.
_PASSIVE_INVOLVEMENT = (
    r"(?:run|organi[sz]ed|coordinated|led|hosted|staffed|founded|started|"
    r"managed|directed|chaired|attended|written|made|taken|photographed|"
    r"deposited|donated|contributed|taught)\s+by\s+(?:the\s+|an?\s+)?"
)


def looks_like_identity_inference(text: str) -> bool:
    """Whether ``text`` reads as a guess or a statement about a real person's
    identity, sexuality, gender history, HIV status, immigration status,
    legal history, or location.

    Narrow and keyword-based on purpose, mirroring
    :func:`ledger.redact_suggest.suggest`: a deliberately partial, auditable
    backstop, not the guarantee. See the module docstring for what the actual
    guarantee is and where it is proven.
    """
    return bool(
        _RE_STATED_IDENTITY.search(text)
        or _RE_REAL_IDENTITY.search(text)
        or _RE_SAME_PERSON.search(text)
        or _RE_LIVES_AT.search(text)
    )


# Preservation-metadata honesty (mission requirement #4). Scoped to claims
# citing `premis_event`/`payload`/`payload_transcript` evidence — a topical
# claim citing a `field`/`dublin_core` item ("the meeting is confirmed to
# have happened in 1996") must not trip this; "confirmed"/"verified" there is
# not a fixity claim at all.
_FIXITY_CONFIDENCE_WORDS = re.compile(
    r"\b(verified|authentic|intact|confirmed genuine|confirmed original|proven authentic)\b",
    re.IGNORECASE,
)
_FIXITY_CITATION_KINDS = frozenset({"premis_event", "payload", "payload_transcript"})


def _fixity_claim_is_dishonest(claim: Claim, cited_evidence_text: str) -> bool:
    """Whether ``claim`` uses fixity-confidence language its own cited
    evidence does not support.

    This portfolio's dominant defect is "absence rendered as a value" — here,
    an unrun or failed fixity check described as "verified"/"authentic". A
    claim citing preservation evidence and using that language must have a
    *successful* PREMIS ``FIXITY_CHECK`` event as its citation; the event's
    outcome is read directly off the evidence text
    (:func:`ledger.ai.context._evidence_for` embeds
    ``"fixity check: <outcome> (...)"`` for exactly this purpose), never
    assumed from the payload's mere presence.
    """
    if claim.citation.kind not in _FIXITY_CITATION_KINDS:
        return False
    if not _FIXITY_CONFIDENCE_WORDS.search(claim.text):
        return False
    return "fixity check: success" not in cited_evidence_text.lower()


def _ungrounded_name_spans(text: str, haystack: str) -> list[str]:
    """Name-shaped spans in ``text`` that do not appear (case-insensitively)
    in ``haystack``.

    Closes a real gap plain citation-existence checking leaves open: a claim
    can cite a *real* evidence key (e.g. ``kind=field ref=story``) while
    supplying no quote, and assert anything at all about it — including a
    fabricated name — and pass a check that only verifies the key exists. A
    name-shaped span that is not present in what this record actually
    disclosed is, in this domain, always either fabricated or drawn from
    outside knowledge; neither is ever an acceptable basis for a claim here,
    so any such span withholds the whole claim.

    The comparison is case-insensitive so a sentence-initial "The Community
    Health Collective" (capitalized only because it starts a sentence) still
    matches evidence text that reads "...by the Community Health
    Collective." mid-sentence — a capitalization accident, not a grounding
    gap. This does not weaken the guarantee: a fabricated name is exceedingly
    unlikely to appear in the disclosed evidence in ANY casing.
    """
    haystack_lower = haystack.lower()
    return [span for span in _RE_NAME_SPAN.findall(text) if span.lower() not in haystack_lower]


def _is_framed_as_a_participant(text: str, token: str) -> bool:
    """Whether ``text`` casts ``token`` as someone who took part in something.

    Deliberately adjacency-bound (see ``_INVOLVEMENT_FRAME``): the token must
    be the subject of the involvement language, so "Cricket ran the free
    clinic" matches and "Redwood Grove Hall hosted the potluck" does not
    depend on this check at all (a multi-word span is already covered by
    :func:`_ungrounded_name_spans`).

    Both voices are read: the active frame, and the passive one ("the free
    clinic was run by Cricket") where the token sits after the verb.
    """
    quoted = re.escape(token)
    return bool(
        re.search(rf"\b{quoted}\b{_INVOLVEMENT_FRAME}", text, re.IGNORECASE)
        or re.search(rf"\b{_PASSIVE_INVOLVEMENT}{quoted}\b", text, re.IGNORECASE)
    )


def _cross_record_person_links(
    text: str, cited_haystack: str, other_haystacks: list[str]
) -> list[str]:
    """Single capitalized tokens in ``text`` that link this claim to another record.

    Returns the offending tokens — those satisfying all three conditions
    documented above ``_RE_CAPITALIZED_TOKEN``. An empty list is the ordinary
    case; anything in it withholds the whole claim.

    The "grounded in the cited record" test is a case-insensitive substring,
    exactly like :func:`_ungrounded_name_spans`, so a capitalization accident
    never costs a legitimate claim. The "present in another record" test is
    case-SENSITIVE and word-bounded: the token has to read as a proper noun
    *there* too, which is what keeps an ordinary word that another record
    merely happened to start a sentence with from ever reaching the frame
    check.
    """
    if not other_haystacks:
        return []
    cited_lower = cited_haystack.lower()
    seen: set[str] = set()
    linked: list[str] = []
    for token in _RE_CAPITALIZED_TOKEN.findall(text):
        if token in seen:
            continue
        seen.add(token)
        if token.lower() in cited_lower:
            continue
        if not _is_framed_as_a_participant(text, token):
            continue
        bounded = re.compile(rf"\b{re.escape(token)}\b")
        if any(bounded.search(haystack) for haystack in other_haystacks):
            linked.append(token)
    return linked


def verify_claims(
    claims: list[Claim], contexts: GroundedContext | dict[str, GroundedContext]
) -> GroundingResult:
    """Check every claim's citation against ``contexts``; split verified/withheld.

    ``contexts`` is either a single :class:`~ledger.ai.context.GroundedContext`
    (the single-record ``describe`` path) or a ``record_id -> GroundedContext``
    mapping (the multi-record ``ask`` path). A claim is verified only if ALL
    of:

    * its text does not read as an identity inference
      (:func:`looks_like_identity_inference`);
    * when more than one record is in play (the ``ask`` path), its text does
      not literally name a record id other than the one it cites — the
      aggregation-attack signature: a claim can be *about* record A while
      naming record B is exactly how two individually-safe records get linked
      into a claim neither one, alone, could ever cite evidence for;
    * it names a ``(record_id, kind, ref)`` evidence item that actually exists
      in what was disclosed for that record;
    * if it supplies a quote, that quote is an exact substring of that
      evidence item's text;
    * if it uses fixity-confidence language ("verified", "authentic", ...)
      about a payload, the cited PREMIS evidence actually records a
      *successful* fixity check (:func:`_fixity_claim_is_dishonest`);
    * on the multi-record path, it does not cast a proper noun that only
      *another* disclosed record contains as a person involved in this one
      (:func:`_cross_record_person_links`) — the single-token nickname the
      two-or-more-word name-span check structurally cannot see.

    Everything else is withheld with a :class:`WithholdReason` naming which
    check failed, never shown.
    """
    by_id: dict[str, GroundedContext] = (
        {contexts.record_id: contexts} if isinstance(contexts, GroundedContext) else dict(contexts)
    )
    evidence_by_id = {rid: context.evidence_text_by_ref() for rid, context in by_id.items()}
    haystack_by_id = {rid: " ".join(items.values()) for rid, items in evidence_by_id.items()}
    verified: list[Claim] = []
    withheld: list[tuple[Claim, WithholdReason]] = []
    for claim in claims:
        if looks_like_identity_inference(claim.text):
            withheld.append((claim, WithholdReason.IDENTITY_INFERENCE))
            continue
        record_id = claim.citation.record_id or (next(iter(by_id)) if len(by_id) == 1 else "")
        other_record_ids = {rid for rid in by_id if rid and rid != record_id}
        if any(other_id in claim.text for other_id in other_record_ids):
            withheld.append((claim, WithholdReason.IDENTITY_INFERENCE))
            continue
        evidence = evidence_by_id.get(record_id)
        if evidence is None:
            withheld.append((claim, WithholdReason.CITATION_NOT_FOUND))
            continue
        key = (claim.citation.kind, claim.citation.ref)
        if key not in evidence:
            withheld.append((claim, WithholdReason.CITATION_NOT_FOUND))
            continue
        if claim.citation.quote and claim.citation.quote not in evidence[key]:
            withheld.append((claim, WithholdReason.QUOTE_NOT_VERBATIM))
            continue
        if _fixity_claim_is_dishonest(claim, evidence[key]):
            withheld.append((claim, WithholdReason.FIXITY_DISHONESTY))
            continue
        # Belt-and-suspenders (see `_ungrounded_name_spans`): even a claim
        # citing a real evidence key must not name anyone who is not
        # verbatim present in this record's own disclosed evidence.
        full_haystack = haystack_by_id[record_id]
        if _ungrounded_name_spans(claim.text, full_haystack):
            withheld.append((claim, WithholdReason.IDENTITY_INFERENCE))
            continue
        # The single-token half of the same gap (see `_cross_record_person_links`):
        # a nickname public in ANOTHER record, cast as a person involved in
        # this one, is a cross-record link no evidence item here supports.
        others = [text for rid, text in haystack_by_id.items() if rid != record_id]
        if _cross_record_person_links(claim.text, full_haystack, others):
            withheld.append((claim, WithholdReason.CROSS_RECORD_LINKAGE))
            continue
        verified.append(claim)
    return GroundingResult(verified=tuple(verified), withheld=tuple(withheld))
