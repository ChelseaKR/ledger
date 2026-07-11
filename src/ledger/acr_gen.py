"""Generate ledger's Accessibility Conformance Report (VPAT 2.5 Rev 508).

The report is the *honest* companion to the automated accessibility gate
(:mod:`ledger.accessibility_check`): the gate proves the structural floor, and
this document is the candid, human-judged conformance picture across the full
WCAG 2.x A/AA set, the Revised Section 508 software (Chapter 5) and support-docs
(Chapter 6) requirements, and the five Functional Performance Criteria (Chapter
3).

The whole report is built from one in-code data structure, so it is a single
source of truth that regenerates deterministically (``make acr`` redirects
:func:`render` into ``docs/accessibility/ACR.md``) — reproducibility, and no drift
between code and document.

Credibility over green-washing: this is a pre-1.0 reference implementation, so
where support is genuinely partial or aspirational the conformance level says so
("Partially Supports") with a frank remark, rather than claiming a green
"Supports" the implementation has not earned. That honesty is the point: an ACR a
reader can trust is worth more than a uniformly green one they cannot.

No-outing rule: this module emits only a conformance document. It never reads an
archive, a record, or an identity.
"""

from __future__ import annotations

from dataclasses import dataclass

# Conformance vocabulary, per the VPAT 2.5 instructions. Used verbatim so the
# report's terms are the standard ones a procurement reviewer expects.
_SUPPORTS = "Supports"
_PARTIAL = "Partially Supports"
_DOES_NOT = "Does Not Support"
_NA = "Not Applicable"

# Currency stamp (DOCUMENTATION-STANDARD DOC-15): every regeneration re-dates the
# report so a reader can tell a fresh audit from a stale one at a glance, rather
# than a document that looks current forever. Bump `_LAST_VERIFIED` by hand each
# time the report is regenerated for a release (`make acr`); the cadence line
# documents when a re-check is expected regardless.
_LAST_VERIFIED = "2026-07-05"
_RECHECK_CADENCE = "per release"


@dataclass(frozen=True)
class Criterion:
    """One row of a conformance table: a criterion, its level, and a remark.

    ``ident`` is the criterion number/name, ``level`` is one of the four standard
    conformance terms, and ``remarks`` is the candid, specific note explaining the
    judgement (credibility — every non-"Supports" row earns its remark).
    """

    ident: str
    title: str
    level: str
    remarks: str


@dataclass(frozen=True)
class Section:
    """A titled group of criteria rendered as one Markdown table.

    ``note`` is an optional lead-in paragraph giving the section context (e.g. the
    WCAG level the table covers), so each table is self-explaining.
    """

    title: str
    criteria: tuple[Criterion, ...]
    note: str = ""


# --- WCAG 2.x Level A -------------------------------------------------------

_WCAG_A: tuple[Criterion, ...] = (
    Criterion(
        "1.1.1",
        "Non-text Content",
        _SUPPORTS,
        "The site ships no images; were one added, decorative images use "
        'alt="" and informative ones carry descriptive alt. The automated '
        "gate fails any <img> without an alt attribute.",
    ),
    Criterion(
        "1.2.1",
        "Audio-only and Video-only (Prerecorded)",
        _NA,
        "The reference site renders text records only; it serves no audio or "
        "video. A deployment that adds media must supply transcripts.",
    ),
    Criterion(
        "1.2.2", "Captions (Prerecorded)", _NA, "No prerecorded audio/video in the reference site."
    ),
    Criterion(
        "1.2.3",
        "Audio Description or Media Alternative (Prerecorded)",
        _NA,
        "No prerecorded video in the reference site.",
    ),
    Criterion(
        "1.3.1",
        "Info and Relationships",
        _SUPPORTS,
        "Semantic landmarks (header/nav/main/footer), a single h1 with "
        "non-skipping heading order, real <label for> on the search input, "
        "and tables with <caption> and <th scope>. All are enforced by the "
        "automated gate.",
    ),
    Criterion(
        "1.3.2",
        "Meaningful Sequence",
        _SUPPORTS,
        "Reading and DOM order match the visual order; no CSS reordering changes meaning.",
    ),
    Criterion(
        "1.3.3",
        "Sensory Characteristics",
        _SUPPORTS,
        "Instructions never rely on shape, size, or position alone; the "
        "content-warning signal is the literal word, not an icon.",
    ),
    Criterion(
        "1.4.1",
        "Use of Color",
        _SUPPORTS,
        "Color is never the only signal: content warnings appear as text "
        '("Content warning", "Yes/No") and a full text interstitial.',
    ),
    Criterion("1.4.2", "Audio Control", _NA, "The site plays no audio."),
    Criterion(
        "2.1.1",
        "Keyboard",
        _SUPPORTS,
        "Every control is a native link, button, or input, so all "
        "functionality is keyboard-operable; there is no scripted widget.",
    ),
    Criterion(
        "2.1.2",
        "No Keyboard Trap",
        _SUPPORTS,
        "No scripted focus management exists, so focus cannot be trapped.",
    ),
    Criterion(
        "2.1.4",
        "Character Key Shortcuts",
        _NA,
        "The site defines no single-character key shortcuts.",
    ),
    Criterion("2.2.1", "Timing Adjustable", _NA, "No time limits are imposed on any interaction."),
    Criterion("2.2.2", "Pause, Stop, Hide", _NA, "No moving, blinking, or auto-updating content."),
    Criterion(
        "2.3.1",
        "Three Flashes or Below Threshold",
        _SUPPORTS,
        "Nothing flashes; the site has no animation that could flash.",
    ),
    Criterion(
        "2.4.1",
        "Bypass Blocks",
        _SUPPORTS,
        'A visible "Skip to main content" link is the first focusable '
        "element on every page and targets #main; enforced by the gate.",
    ),
    Criterion(
        "2.4.2",
        "Page Titled",
        _SUPPORTS,
        "Every page has a unique, descriptive <title> (e.g. the record "
        "title); the gate fails an empty title.",
    ),
    Criterion(
        "2.4.3",
        "Focus Order",
        _SUPPORTS,
        "Focus order follows source order; no positive tabindex is used, and "
        "the gate fails any tabindex greater than 0.",
    ),
    Criterion(
        "2.4.4",
        "Link Purpose (In Context)",
        _SUPPORTS,
        'Link text is always descriptive (a record title, "Proceed to the '
        'content", "Back to all records"); never "click here".',
    ),
    Criterion("2.5.1", "Pointer Gestures", _NA, "No multipoint or path-based gestures are used."),
    Criterion(
        "2.5.2",
        "Pointer Cancellation",
        _SUPPORTS,
        "All actions fire on standard activation of native controls (up "
        "event), so a pointer-down can be aborted.",
    ),
    Criterion(
        "2.5.3",
        "Label in Name",
        _SUPPORTS,
        "Visible control labels match their accessible names (native elements with real labels).",
    ),
    Criterion("2.5.4", "Motion Actuation", _NA, "No functionality is triggered by device motion."),
    Criterion(
        "3.1.1",
        "Language of Page",
        _SUPPORTS,
        "<html lang> carries the archive's configured primary language; the "
        "gate fails a missing or empty lang.",
    ),
    Criterion(
        "3.2.1", "On Focus", _SUPPORTS, "Focusing a control never triggers a change of context."
    ),
    Criterion(
        "3.2.2",
        "On Input",
        _SUPPORTS,
        "The search form submits only on explicit activation, not on input.",
    ),
    Criterion(
        "3.3.1",
        "Error Identification",
        _PARTIAL,
        'The only input is free-text search, which cannot be "in error"; '
        "an empty query is handled gracefully. There is no rich form to "
        "validate yet, so this is partially exercised rather than fully "
        "demonstrated.",
    ),
    Criterion(
        "3.3.2",
        "Labels or Instructions",
        _SUPPORTS,
        "The search field has a visible, associated label.",
    ),
    Criterion(
        "4.1.1",
        "Parsing (obsolete in WCAG 2.2)",
        _SUPPORTS,
        "HTML is generated programmatically with a single escaping boundary, "
        "yielding well-formed markup with unique ids.",
    ),
    Criterion(
        "4.1.2",
        "Name, Role, Value",
        _SUPPORTS,
        "Only native HTML elements are used, so name/role/value are provided "
        "by the platform; no custom ARIA widgets to maintain.",
    ),
)


# --- WCAG 2.x Level AA ------------------------------------------------------

_WCAG_AA: tuple[Criterion, ...] = (
    Criterion("1.2.4", "Captions (Live)", _NA, "No live audio/video."),
    Criterion("1.2.5", "Audio Description (Prerecorded)", _NA, "No prerecorded video."),
    Criterion(
        "1.3.4", "Orientation", _SUPPORTS, "The layout is responsive and locks to no orientation."
    ),
    Criterion(
        "1.3.5",
        "Identify Input Purpose",
        _PARTIAL,
        "The single search field is not a personal-data field, so autocomplete "
        "tokens do not apply; broader input-purpose support is untested "
        "because there are no such fields yet.",
    ),
    Criterion(
        "1.4.3",
        "Contrast (Minimum)",
        _SUPPORTS,
        "Every text colour pair in the stylesheet is measured against the AA "
        "4.5:1 threshold by an automated audit (ledger.accessibility_check."
        "audit_css_contrast) that runs in the accessibility gate on every build "
        "and fails on any regression; all pairs pass with margin (body 17.4:1, "
        "links 6.7:1, content-warning text 9.7:1).",
    ),
    Criterion(
        "1.4.4",
        "Resize Text",
        _SUPPORTS,
        "Type scales in rem/ch units and reflows to 200% zoom without loss.",
    ),
    Criterion(
        "1.4.5",
        "Images of Text",
        _SUPPORTS,
        "All text is real text; the site uses no images of text.",
    ),
    Criterion(
        "1.4.10",
        "Reflow",
        _SUPPORTS,
        "Mobile-first, fluid layout; content reflows to a single column and "
        "the table scrolls horizontally rather than overflowing at 320 CSS px.",
    ),
    Criterion(
        "1.4.11",
        "Non-text Contrast",
        _SUPPORTS,
        "The focus outline and control borders are measured at >= 3:1 by the same "
        "automated contrast audit (border 4.5:1 on white), enforced in the gate.",
    ),
    Criterion(
        "1.4.12",
        "Text Spacing",
        _SUPPORTS,
        "No fixed line-height/letter-spacing prevents user text-spacing "
        "overrides; the layout tolerates them.",
    ),
    Criterion(
        "1.4.13", "Content on Hover or Focus", _NA, "No hover/focus-triggered overlays or tooltips."
    ),
    Criterion(
        "2.4.5",
        "Multiple Ways",
        _SUPPORTS,
        "Records are reachable by browse (list and table views) and by "
        "search — two independent ways.",
    ),
    Criterion(
        "2.4.6",
        "Headings and Labels",
        _SUPPORTS,
        'Headings and labels are descriptive (e.g. "Records (table view)", '
        '"Content warnings", "Withheld").',
    ),
    Criterion(
        "2.4.7",
        "Focus Visible",
        _SUPPORTS,
        "A strong :focus-visible outline marks the focused control on every page.",
    ),
    Criterion(
        "3.1.2",
        "Language of Parts",
        _NA,
        "Content is single-language per the configured page language; no "
        "inline language changes are produced by the renderer.",
    ),
    Criterion(
        "3.2.3",
        "Consistent Navigation",
        _SUPPORTS,
        "The same nav (Browse / Search / Status) appears in the same order on every page.",
    ),
    Criterion(
        "3.2.4",
        "Consistent Identification",
        _SUPPORTS,
        "Components with the same function are labelled identically across pages.",
    ),
    Criterion(
        "3.3.3",
        "Error Suggestion",
        _PARTIAL,
        "As with 3.3.1, the lone search field offers little to suggest; "
        "richer error suggestion is untested for want of a rich form.",
    ),
    Criterion(
        "3.3.4",
        "Error Prevention (Legal, Financial, Data)",
        _NA,
        "The public surface is read-only; it commits no legal, financial, or data transactions.",
    ),
    Criterion(
        "4.1.3",
        "Status Messages",
        _SUPPORTS,
        "The result count and empty-state message are rendered inside a polite "
        'live region (role="status", aria-live="polite") on the browse/search '
        "surface, so a screen reader announces how many records a search returned "
        "without a change of focus. A server test asserts the live region is present.",
    ),
)


# --- WCAG 2.2 additions (A/AA) ----------------------------------------------

_WCAG_22: tuple[Criterion, ...] = (
    Criterion(
        "2.4.11",
        "Focus Not Obscured (Minimum) (AA)",
        _SUPPORTS,
        "The focused element is never covered by sticky/overlay content; there is no sticky UI.",
    ),
    Criterion("2.5.7", "Dragging Movements (AA)", _NA, "No dragging interactions exist."),
    Criterion(
        "2.5.8",
        "Target Size (Minimum) (AA)",
        _SUPPORTS,
        "Interactive controls meet a 44px minimum tap target in the stylesheet.",
    ),
    Criterion(
        "3.2.6",
        "Consistent Help (A)",
        _NA,
        "The site exposes no help mechanism that must be consistently placed.",
    ),
    Criterion(
        "3.3.7", "Redundant Entry (A)", _NA, "The read-only site asks for no repeated data entry."
    ),
    Criterion(
        "3.3.8",
        "Accessible Authentication (Minimum) (AA)",
        _NA,
        "The public site has no authentication step; grants are provisioned "
        "out of band via a header, never a cognitive test.",
    ),
)


# --- Revised 508 Chapter 5 (Software) ---------------------------------------

_CH5: tuple[Criterion, ...] = (
    Criterion(
        "502",
        "Interoperability with Assistive Technology",
        _SUPPORTS,
        "The public surface is standards-based HTML rendered in a browser, "
        "so it inherits the platform accessibility services AT relies on; "
        "ledger ships no custom GUI toolkit.",
    ),
    Criterion(
        "502.2.1",
        "User Control of Accessibility Features",
        _NA,
        "ledger is not a platform and disables no platform accessibility feature.",
    ),
    Criterion(
        "503",
        "Applications",
        _SUPPORTS,
        "The browse application uses native controls with correct names, "
        "roles, and values; user preferences (zoom, reduced motion) are "
        "honoured.",
    ),
    Criterion(
        "503.4",
        "User Controls for Captions and Audio Description",
        _NA,
        "No media player is provided.",
    ),
    Criterion(
        "504",
        "Authoring Tools",
        _PARTIAL,
        "The ingest CLI is the authoring path. It accepts structured, "
        "accessible metadata (titles, Dublin Core, content warnings) and the "
        "renderer produces conformant markup, but the CLI does not yet "
        "actively prompt an author to supply accessibility information (e.g. "
        "alt text for a future image payload), so authoring-tool support is "
        "partial.",
    ),
)


# --- Revised 508 Chapter 6 (Support Documentation and Services) -------------

_CH6: tuple[Criterion, ...] = (
    Criterion(
        "602.2",
        "Accessibility and Compatibility Features",
        _SUPPORTS,
        "The web/README and this ACR document the site's accessibility "
        "features and how each WCAG requirement is met.",
    ),
    Criterion(
        "602.3",
        "Electronic Support Documentation (WCAG)",
        _PARTIAL,
        "Documentation is plain Markdown (README, ACR) that conforms to WCAG "
        "as text, but it has not been independently audited as a full "
        "electronic document, so it is reported partial.",
    ),
    Criterion(
        "603",
        "Support Services",
        _NA,
        "This pre-1.0 reference implementation offers no commercial support "
        "service; community support is via the public issue tracker.",
    ),
)


# --- Chapter 3: Functional Performance Criteria -----------------------------

_FPC: tuple[Criterion, ...] = (
    Criterion(
        "302.1",
        "Without Vision",
        _SUPPORTS,
        "Semantic landmarks, headings, a skip link, labelled controls, and a "
        "captioned/scoped data table give a complete screen-reader path; the "
        "list and table views are equivalent.",
    ),
    Criterion(
        "302.2",
        "With Limited Vision",
        _SUPPORTS,
        "Text resizes and reflows to 200%/320px, and every colour pair is "
        "measured at WCAG AA by the automated contrast audit enforced in the "
        "gate (see 1.4.3/1.4.11).",
    ),
    Criterion(
        "302.3",
        "Without Perception of Color",
        _SUPPORTS,
        "Color is never the sole signal; the content-warning state is always conveyed as text.",
    ),
    Criterion("302.4", "Without Hearing", _SUPPORTS, "The site conveys no information by sound."),
    Criterion(
        "302.5",
        "With Limited Hearing",
        _SUPPORTS,
        "No audio is used, so limited hearing imposes no barrier.",
    ),
    Criterion("302.6", "Without Speech", _SUPPORTS, "No interaction requires speech."),
    Criterion(
        "302.7",
        "With Limited Manipulation",
        _SUPPORTS,
        "All controls are keyboard-operable with large (44px) targets and no "
        "dragging or multipoint gestures.",
    ),
    Criterion(
        "302.8",
        "With Limited Reach and Strength",
        _SUPPORTS,
        "Native controls work with any single input method; nothing requires "
        "simultaneous actions or sustained effort.",
    ),
    Criterion(
        "302.9",
        "With Limited Language, Cognitive, and Learning Abilities",
        _PARTIAL,
        "Plain language, consistent navigation, a clear content-warning "
        'interstitial, and honest "Withheld" notes aid comprehension; '
        "however, no reading-level testing or simplified-view option has yet "
        "been performed, so this is candidly partial.",
    ),
)


_SECTIONS: tuple[Section, ...] = (
    Section("WCAG 2.x — Level A", _WCAG_A, note="Success criteria at conformance Level A."),
    Section("WCAG 2.x — Level AA", _WCAG_AA, note="Success criteria at conformance Level AA."),
    Section(
        "WCAG 2.2 — New Criteria (A/AA)",
        _WCAG_22,
        note="Criteria introduced in WCAG 2.2 at Levels A and AA.",
    ),
    Section("Revised Section 508 — Chapter 5: Software", _CH5),
    Section("Revised Section 508 — Chapter 6: Support Documentation and Services", _CH6),
    Section("Chapter 3: Functional Performance Criteria", _FPC),
)


def _render_table(section: Section) -> str:
    """Render one :class:`Section` as a Markdown conformance table.

    The three-column shape — Criterion, Conformance Level, Remarks — is the VPAT
    table layout a procurement reviewer expects (standards-compliance).
    """
    lines: list[str] = [f"### {section.title}", ""]
    if section.note:
        lines.append(section.note)
        lines.append("")
    lines.append("| Criterion | Conformance Level | Remarks and Explanations |")
    lines.append("| --- | --- | --- |")
    for crit in section.criteria:
        name = f"{crit.ident} {crit.title}".strip()
        remarks = crit.remarks.replace("|", "\\|")
        lines.append(f"| {name} | {crit.level} | {remarks} |")
    lines.append("")
    return "\n".join(lines)


def _summary_counts() -> dict[str, int]:
    """Tally conformance levels across every section (transparency at a glance)."""
    counts = {_SUPPORTS: 0, _PARTIAL: 0, _DOES_NOT: 0, _NA: 0}
    for section in _SECTIONS:
        for crit in section.criteria:
            counts[crit.level] = counts.get(crit.level, 0) + 1
    return counts


def render() -> str:
    """Return the full Accessibility Conformance Report as Markdown.

    Built entirely from the in-code criterion tables, so the document is a single
    source of truth that regenerates deterministically (reproducibility). The
    output is the VPAT 2.5 Rev 508 structure: a header, the conformance-term key,
    a candid summary tally, then one table per section.
    """
    counts = _summary_counts()
    lines: list[str] = [
        "# Accessibility Conformance Report",
        "",
        "## ledger — a privacy-first community archive",
        "",
        "**Based on VPAT® Version 2.5 Rev — Revised Section 508 Edition**",
        "",
        f"Last verified: {_LAST_VERIFIED} · Recheck cadence: {_RECHECK_CADENCE}",
        "",
        "This report describes the accessibility conformance of ledger's public "
        + "browse/search surface against WCAG 2.x (Levels A and AA, including the "
        + "WCAG 2.2 additions), the Revised Section 508 software and support-"
        + "documentation requirements, and the Functional Performance Criteria.",
        "",
        "ledger is a pre-1.0 reference implementation. This ACR is deliberately "
        + "candid: where support is genuinely partial or aspirational it says "
        + '"Partially Supports" with a specific remark, rather than overstating '
        + "conformance. An honest report is more useful than a uniformly green one.",
        "",
        "### Conformance Levels",
        "",
        "- **Supports** — the functionality meets the criterion without known defects.",
        "- **Partially Supports** — some functionality meets the criterion.",
        "- **Does Not Support** — the majority of functionality does not meet the criterion.",
        "- **Not Applicable** — the criterion does not apply to this product.",
        "",
        "### Summary",
        "",
        f"- Supports: {counts[_SUPPORTS]}",
        f"- Partially Supports: {counts[_PARTIAL]}",
        f"- Does Not Support: {counts[_DOES_NOT]}",
        f"- Not Applicable: {counts[_NA]}",
        "",
        "The automated accessibility gate (`ledger.accessibility_check`) enforces "
        + 'the structural floor behind many "Supports" rows on every commit; the '
        + '"Partially Supports" rows name the specific work still owed before a full '
        + "claim is warranted.",
        "",
        "### Evidence basis",
        "",
        "This report rests on two committed, recurring sources of evidence — neither "
        + "adds a runtime dependency, both run against the same canonical pages:",
        "",
        "- **Automated.** The stdlib static gate "
        + "(`python -m ledger.accessibility_check web`) runs on every commit, and a "
        + "browser-real **axe-core** job (the `accessibility-browser` CI job) drives "
        + "the served site in a headless Chromium under both the light and dark colour "
        + "schemes, asserting no WCAG-tagged axe violations.",
        "- **Manual.** A committed quarterly (and pre-release) **NVDA and VoiceOver** "
        + "review covers what no scan can judge — reading order, content-warning "
        + "announcement, `aria-live` status, and spoken form errors. Its cadence, "
        + "checklist, and results log live in "
        + "[`MANUAL-REVIEW-CADENCE.md`](./MANUAL-REVIEW-CADENCE.md); manual findings "
        + "are reflected back into the remarks below.",
        "",
        "## Tables",
        "",
    ]
    for section in _SECTIONS:
        lines.append(_render_table(section))
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    """Print the Accessibility Conformance Report to stdout; return ``0``.

    ``make acr`` redirects this into ``docs/accessibility/ACR.md`` so the checked-in
    report is always regenerable from code (reproducibility).
    """
    print(render(), end="")
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
