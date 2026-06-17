# Accessibility Conformance Report

## ledger — a privacy-first community archive

**Based on VPAT® Version 2.5 Rev — Revised Section 508 Edition**

This report describes the accessibility conformance of ledger's public browse/search surface against WCAG 2.x (Levels A and AA, including the WCAG 2.2 additions), the Revised Section 508 software and support-documentation requirements, and the Functional Performance Criteria.

ledger is a pre-1.0 reference implementation. This ACR is deliberately candid: where support is genuinely partial or aspirational it says "Partially Supports" with a specific remark, rather than overstating conformance. An honest report is more useful than a uniformly green one.

### Conformance Levels

- **Supports** — the functionality meets the criterion without known defects.
- **Partially Supports** — some functionality meets the criterion.
- **Does Not Support** — the majority of functionality does not meet the criterion.
- **Not Applicable** — the criterion does not apply to this product.

### Summary

- Supports: 42
- Partially Supports: 10
- Does Not Support: 0
- Not Applicable: 21

The automated accessibility gate (`ledger.accessibility_check`) enforces the structural floor behind many "Supports" rows on every commit; the "Partially Supports" rows name the specific work still owed before a full claim is warranted.

## Tables

### WCAG 2.x — Level A

Success criteria at conformance Level A.

| Criterion | Conformance Level | Remarks and Explanations |
| --- | --- | --- |
| 1.1.1 Non-text Content | Supports | The site ships no images; were one added, decorative images use alt="" and informative ones carry descriptive alt. The automated gate fails any <img> without an alt attribute. |
| 1.2.1 Audio-only and Video-only (Prerecorded) | Not Applicable | The reference site renders text records only; it serves no audio or video. A deployment that adds media must supply transcripts. |
| 1.2.2 Captions (Prerecorded) | Not Applicable | No prerecorded audio/video in the reference site. |
| 1.2.3 Audio Description or Media Alternative (Prerecorded) | Not Applicable | No prerecorded video in the reference site. |
| 1.3.1 Info and Relationships | Supports | Semantic landmarks (header/nav/main/footer), a single h1 with non-skipping heading order, real <label for> on the search input, and tables with <caption> and <th scope>. All are enforced by the automated gate. |
| 1.3.2 Meaningful Sequence | Supports | Reading and DOM order match the visual order; no CSS reordering changes meaning. |
| 1.3.3 Sensory Characteristics | Supports | Instructions never rely on shape, size, or position alone; the content-warning signal is the literal word, not an icon. |
| 1.4.1 Use of Color | Supports | Color is never the only signal: content warnings appear as text ("Content warning", "Yes/No") and a full text interstitial. |
| 1.4.2 Audio Control | Not Applicable | The site plays no audio. |
| 2.1.1 Keyboard | Supports | Every control is a native link, button, or input, so all functionality is keyboard-operable; there is no scripted widget. |
| 2.1.2 No Keyboard Trap | Supports | No scripted focus management exists, so focus cannot be trapped. |
| 2.1.4 Character Key Shortcuts | Not Applicable | The site defines no single-character key shortcuts. |
| 2.2.1 Timing Adjustable | Not Applicable | No time limits are imposed on any interaction. |
| 2.2.2 Pause, Stop, Hide | Not Applicable | No moving, blinking, or auto-updating content. |
| 2.3.1 Three Flashes or Below Threshold | Supports | Nothing flashes; the site has no animation that could flash. |
| 2.4.1 Bypass Blocks | Supports | A visible "Skip to main content" link is the first focusable element on every page and targets #main; enforced by the gate. |
| 2.4.2 Page Titled | Supports | Every page has a unique, descriptive <title> (e.g. the record title); the gate fails an empty title. |
| 2.4.3 Focus Order | Supports | Focus order follows source order; no positive tabindex is used, and the gate fails any tabindex greater than 0. |
| 2.4.4 Link Purpose (In Context) | Supports | Link text is always descriptive (a record title, "Proceed to the content", "Back to all records"); never "click here". |
| 2.5.1 Pointer Gestures | Not Applicable | No multipoint or path-based gestures are used. |
| 2.5.2 Pointer Cancellation | Supports | All actions fire on standard activation of native controls (up event), so a pointer-down can be aborted. |
| 2.5.3 Label in Name | Supports | Visible control labels match their accessible names (native elements with real labels). |
| 2.5.4 Motion Actuation | Not Applicable | No functionality is triggered by device motion. |
| 3.1.1 Language of Page | Supports | <html lang> carries the archive's configured primary language; the gate fails a missing or empty lang. |
| 3.2.1 On Focus | Supports | Focusing a control never triggers a change of context. |
| 3.2.2 On Input | Supports | The search form submits only on explicit activation, not on input. |
| 3.3.1 Error Identification | Partially Supports | The only input is free-text search, which cannot be "in error"; an empty query is handled gracefully. There is no rich form to validate yet, so this is partially exercised rather than fully demonstrated. |
| 3.3.2 Labels or Instructions | Supports | The search field has a visible, associated label. |
| 4.1.1 Parsing (obsolete in WCAG 2.2) | Supports | HTML is generated programmatically with a single escaping boundary, yielding well-formed markup with unique ids. |
| 4.1.2 Name, Role, Value | Supports | Only native HTML elements are used, so name/role/value are provided by the platform; no custom ARIA widgets to maintain. |

### WCAG 2.x — Level AA

Success criteria at conformance Level AA.

| Criterion | Conformance Level | Remarks and Explanations |
| --- | --- | --- |
| 1.2.4 Captions (Live) | Not Applicable | No live audio/video. |
| 1.2.5 Audio Description (Prerecorded) | Not Applicable | No prerecorded video. |
| 1.3.4 Orientation | Supports | The layout is responsive and locks to no orientation. |
| 1.3.5 Identify Input Purpose | Partially Supports | The single search field is not a personal-data field, so autocomplete tokens do not apply; broader input-purpose support is untested because there are no such fields yet. |
| 1.4.3 Contrast (Minimum) | Partially Supports | The stylesheet documents AA-passing contrast tokens against white (body 16.1:1, links 6.5:1, content-warning text 8.2:1), but these have not yet been verified by an independent automated contrast audit across every state, so this is reported partial pending that check. |
| 1.4.4 Resize Text | Supports | Type scales in rem/ch units and reflows to 200% zoom without loss. |
| 1.4.5 Images of Text | Supports | All text is real text; the site uses no images of text. |
| 1.4.10 Reflow | Supports | Mobile-first, fluid layout; content reflows to a single column and the table scrolls horizontally rather than overflowing at 320 CSS px. |
| 1.4.11 Non-text Contrast | Partially Supports | The focus outline and control borders are designed to clear 3:1, but, as with 1.4.3, this awaits an independent contrast audit before a full "Supports" claim. |
| 1.4.12 Text Spacing | Supports | No fixed line-height/letter-spacing prevents user text-spacing overrides; the layout tolerates them. |
| 1.4.13 Content on Hover or Focus | Not Applicable | No hover/focus-triggered overlays or tooltips. |
| 2.4.5 Multiple Ways | Supports | Records are reachable by browse (list and table views) and by search — two independent ways. |
| 2.4.6 Headings and Labels | Supports | Headings and labels are descriptive (e.g. "Records (table view)", "Content warnings", "Withheld"). |
| 2.4.7 Focus Visible | Supports | A strong :focus-visible outline marks the focused control on every page. |
| 3.1.2 Language of Parts | Not Applicable | Content is single-language per the configured page language; no inline language changes are produced by the renderer. |
| 3.2.3 Consistent Navigation | Supports | The same nav (Browse / Search / Status) appears in the same order on every page. |
| 3.2.4 Consistent Identification | Supports | Components with the same function are labelled identically across pages. |
| 3.3.3 Error Suggestion | Partially Supports | As with 3.3.1, the lone search field offers little to suggest; richer error suggestion is untested for want of a rich form. |
| 3.3.4 Error Prevention (Legal, Financial, Data) | Not Applicable | The public surface is read-only; it commits no legal, financial, or data transactions. |
| 4.1.3 Status Messages | Partially Supports | Result counts are rendered in the page text, but they are not yet announced via an aria-live region, so a dynamic status announcement is only partially supported. |

### WCAG 2.2 — New Criteria (A/AA)

Criteria introduced in WCAG 2.2 at Levels A and AA.

| Criterion | Conformance Level | Remarks and Explanations |
| --- | --- | --- |
| 2.4.11 Focus Not Obscured (Minimum) (AA) | Supports | The focused element is never covered by sticky/overlay content; there is no sticky UI. |
| 2.5.7 Dragging Movements (AA) | Not Applicable | No dragging interactions exist. |
| 2.5.8 Target Size (Minimum) (AA) | Supports | Interactive controls meet a 44px minimum tap target in the stylesheet. |
| 3.2.6 Consistent Help (A) | Not Applicable | The site exposes no help mechanism that must be consistently placed. |
| 3.3.7 Redundant Entry (A) | Not Applicable | The read-only site asks for no repeated data entry. |
| 3.3.8 Accessible Authentication (Minimum) (AA) | Not Applicable | The public site has no authentication step; grants are provisioned out of band via a header, never a cognitive test. |

### Revised Section 508 — Chapter 5: Software

| Criterion | Conformance Level | Remarks and Explanations |
| --- | --- | --- |
| 502 Interoperability with Assistive Technology | Supports | The public surface is standards-based HTML rendered in a browser, so it inherits the platform accessibility services AT relies on; ledger ships no custom GUI toolkit. |
| 502.2.1 User Control of Accessibility Features | Not Applicable | ledger is not a platform and disables no platform accessibility feature. |
| 503 Applications | Supports | The browse application uses native controls with correct names, roles, and values; user preferences (zoom, reduced motion) are honoured. |
| 503.4 User Controls for Captions and Audio Description | Not Applicable | No media player is provided. |
| 504 Authoring Tools | Partially Supports | The ingest CLI is the authoring path. It accepts structured, accessible metadata (titles, Dublin Core, content warnings) and the renderer produces conformant markup, but the CLI does not yet actively prompt an author to supply accessibility information (e.g. alt text for a future image payload), so authoring-tool support is partial. |

### Revised Section 508 — Chapter 6: Support Documentation and Services

| Criterion | Conformance Level | Remarks and Explanations |
| --- | --- | --- |
| 602.2 Accessibility and Compatibility Features | Supports | The web/README and this ACR document the site's accessibility features and how each WCAG requirement is met. |
| 602.3 Electronic Support Documentation (WCAG) | Partially Supports | Documentation is plain Markdown (README, ACR) that conforms to WCAG as text, but it has not been independently audited as a full electronic document, so it is reported partial. |
| 603 Support Services | Not Applicable | This pre-1.0 reference implementation offers no commercial support service; community support is via the public issue tracker. |

### Chapter 3: Functional Performance Criteria

| Criterion | Conformance Level | Remarks and Explanations |
| --- | --- | --- |
| 302.1 Without Vision | Supports | Semantic landmarks, headings, a skip link, labelled controls, and a captioned/scoped data table give a complete screen-reader path; the list and table views are equivalent. |
| 302.2 With Limited Vision | Partially Supports | Text resizes and reflows to 200%/320px, and contrast tokens are documented as AA-passing, but pending the independent contrast audit noted under 1.4.3/1.4.11 this is reported partial. |
| 302.3 Without Perception of Color | Supports | Color is never the sole signal; the content-warning state is always conveyed as text. |
| 302.4 Without Hearing | Supports | The site conveys no information by sound. |
| 302.5 With Limited Hearing | Supports | No audio is used, so limited hearing imposes no barrier. |
| 302.6 Without Speech | Supports | No interaction requires speech. |
| 302.7 With Limited Manipulation | Supports | All controls are keyboard-operable with large (44px) targets and no dragging or multipoint gestures. |
| 302.8 With Limited Reach and Strength | Supports | Native controls work with any single input method; nothing requires simultaneous actions or sustained effort. |
| 302.9 With Limited Language, Cognitive, and Learning Abilities | Partially Supports | Plain language, consistent navigation, a clear content-warning interstitial, and honest "Withheld" notes aid comprehension; however, no reading-level testing or simplified-view option has yet been performed, so this is candidly partial. |
