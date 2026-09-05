# Accessibility

Last verified: 2026-08-06 · Recheck cadence: per release

This document states ledger's accessibility commitment, what it covers, and how it
is enforced. It is the prose companion to two machine artifacts: the automated gate
in `src/ledger/accessibility_check.py` and the Accessibility Conformance Report
generated into `docs/accessibility/ACR.md`.

For readers rather than implementers, the short public version is
[`docs/accessibility/STATEMENT.md`](accessibility/STATEMENT.md): what has been
tested, what has **not**, and how to report a barrier.

## The commitment

ledger targets **WCAG 2.2 Level AA** and conformance with the **Revised Section 508
Standards** (36 CFR Part 1194). The Revised 508 standards incorporate WCAG 2.0
Level A and AA by reference for web content and add the **Functional Performance
Criteria** of Chapter 3 (use without vision, with limited vision, without
perception of colour, without hearing, with limited hearing, without speech, with
limited manipulation, with limited reach and strength, and with limited language,
cognitive, and learning abilities), the software requirements of Chapter 5, and the
support-documentation requirements of Chapter 6.

The public browse/search surface (`src/ledger/server.py`) is a framework-free,
standard-library `http.server` application that renders semantic HTML directly. It
uses only native HTML controls — links, buttons, and inputs — with no scripted
widgets and no custom ARIA, so name, role, and value come from the platform and
keyboard operability is inherent rather than reconstructed.

## Why an unfunded community archive builds to the standard institutions audit to

A community archive for queer histories and mutual-aid knowledge is not federal
information and communication technology, so Section 508 does not legally apply
here. Building to it anyway is deliberate.

- **Disabled people are part of every community this serves**, as contributors and
  as readers. The point of the archive is that a community keeps its own records;
  "the community" includes blind organizers, low-vision elders, deaf contributors,
  and people with cognitive and motor disabilities. An archive that is not usable by
  them is not the community's archive.
- **The contributor with the most to lose is often the one with the most barriers.**
  Safety and access are not separable concerns. The same person who needs the
  no-outing guarantee may also need a screen reader; a content warning that is
  conveyed by colour alone fails exactly the reader it is meant to protect.
- **Meeting the standard institutions audit to gives a partnering library or campus
  a clean, public artifact to point at.** A small collective that wants to deposit
  its archive with a university or apply for a grant can hand over a committed ACR
  on the recognised VPAT template instead of an informal assurance. Building to the
  institutional bar is what lets an unfunded project interoperate with funded ones
  on the funded ones' terms — without becoming dependent on them.
- **It costs least when it is structural.** The surface is plain semantic HTML with
  one stylesheet and no build step, so conformance is a property of how pages are
  generated, not an expensive retrofit. Affordability and accessibility reinforce
  each other here rather than compete.

## The list/table non-visual equivalent

Every browse and search surface presents the same records in two equally complete
forms, rendered side by side in the same `<main>`:

- a semantic **list view** (`_records_list_html`), each record a heading-linked item
  with its summary; and
- a **data table view** (`_records_table_html`) carrying the same titles, summaries,
  and content-warning state, with a `<caption>` describing the table's purpose and a
  `<th scope="col">` on every column header so assistive technology can associate
  each cell with its column.

Neither view is a degraded fallback: they carry the same records, facets, and
access state. The commitment is that **nothing in the archive is reachable only by
pointing at a map or a visual layout**. Where a map view is later added, the same
list and table remain the authoritative non-visual equivalent, so a screen-reader
user or a small-screen user gets the full content by a path that never depends on
sight or a pointer.

## Content warnings as programmatic text

Content warnings are structured metadata on the record (`Record.content_warnings`),
not styling. They are surfaced as **programmatic text**, never as colour or an icon
alone:

- In the table view, the content-warning column holds the literal word `Yes` or
  `No`, so the signal survives for colour-blind and text-only readers.
- In the list view, a record with warnings carries a textual `Content warning`
  badge in its heading.
- On a single record that carries warnings, the viewer first sees a **text
  interstitial** (`_record_main_html`): a `Content warnings` heading, the warnings
  listed as words, and an explicit link to proceed to the content. The underlying
  material is not rendered until the viewer chooses to proceed.
- After proceeding, the warnings are **restated as text** above the content, so the
  signal is never lost on the way to the material.

This serves both accessibility (the warning is perceivable without colour, sound,
or vision) and safety (the warning surfaces before any render of the underlying
material).

## The merge-blocking CI gate

Accessibility is a **merge-blocking gate**, not an aspiration: a regression fails
the build. The gate has two parts.

1. **Automated structural check (`make accessibility`).** The `accessibility` job in
   `.github/workflows/ci.yml` runs `python -m ledger.accessibility_check web` on
   every push and pull request. The checker is dependency-free and built on the
   standard-library `html.parser`. `web/` ships no static `.html` of its own, so
   the check scans **nine server-rendered sample pages** — browse, a record,
   places, timeline, the collection overview, contribute, withdraw, edit, and the
   transparency log (`ledger.accessibility_check._render_sample_pages`) — and
   fails the build (exit code 1) on any of the structural WCAG 2.x violations it
   can verify statically:
   - a missing or empty `lang` on `<html>` (3.1.1);
   - a missing or empty `<title>` (2.4.2);
   - zero or more than one `<h1>` (1.3.1);
   - a missing `<main>` landmark (1.3.1);
   - a missing skip-to-content link (2.4.1);
   - any `<img>` without an `alt` attribute (1.1.1);
   - any `<input>` without a programmatically associated `<label for>` (1.3.1, 4.1.2);
   - any `<table>` without a `<caption>` or without a `<th scope>` (1.3.1);
   - any positive `tabindex` (2.4.3);
   - any **status message rendered outside a live region**, and any **live
     region scoped wider than the message it carries** (4.1.3 — see below).

   This is the automatable **floor**, not a claim of full conformance. It catches
   the structural regressions a machine can catch, deterministically, on every
   commit.

   Because the nine rendered sample pages are the *only* structural coverage
   (`web/` has no static HTML of its own), a check that examined zero of them
   is treated as a failure, not a vacuous pass: `check_dir` refuses to report
   success having checked nothing, and a passing run's own output states how
   many documents it examined and names each one, so "9 checked, clean" and "0
   checked" can never print the same "accessibility check passed" line
   ([#122](https://github.com/ChelseaKR/ledger/issues/122)). The renderer degrades
   silently only for `OSError` — a sandbox with no writable temp directory, an
   environment fact — never for a renderer defect, which fails the gate loudly
   instead.

   The static gate and the browser job below do not reach every HTML-emitting
   route in `server.py`; which routes are covered by which mechanism, and which
   8 have no automated coverage from either today, is recorded in
   [`docs/accessibility/ROUTE-COVERAGE.md`](accessibility/ROUTE-COVERAGE.md).

   A second, **browser-real** automated job adds engine-backed depth on top of that
   static floor. The `accessibility-browser` job in `.github/workflows/ci.yml`
   seeds a throwaway demo archive, serves it with `ledger serve`, and drives the
   canonical pages in a headless Chromium running **axe-core** — under **both** the
   light and dark colour schemes — asserting no WCAG-tagged axe violations, plus
   a keyboard-only traversal of the contribute form. This catches what a
   standard-library HTML scan cannot: rendered colour contrast in each theme,
   computed accessible names, and focus order. It is **CI/dev-only** — Playwright,
   Node, and the browser live under `tools/a11y_browser/` and never enter the
   `ledger` runtime, so the stdlib-only, one-cheap-box promise still holds.

   The same job runs a **320 CSS px reflow** pass (`reflow.spec.ts`, SC 1.4.10) —
   320×256 is a 1280×1024 screen at 400% zoom, which is where the number in the
   criterion comes from. axe cannot do this: axe evaluates the DOM it is handed and
   has no opinion about the viewport that DOM was laid out in, so an axe-green page
   can still force a reader to pan sideways to read every line. The gate fails on
   any horizontal *page* scroll, and on any element whose content spills past the
   viewport even when its box does not — a long unbreakable URL inside a correctly
   sized paragraph, which looks fine in a layout inspector. Content that genuinely
   needs a second dimension is exempt under the SC, so an element inside its own
   `overflow-x: auto` scroller (the record table) is not flagged.

2. **Manual screen-reader review (NVDA / VoiceOver).** The criteria no scan — static
   or browser-automated — can judge (meaningful reading order, the quality of the
   interstitial flow, announcement of the content-warning state, `aria-live` status
   messages, the equivalence of the list and table views in practice) are verified
   by manual review with **NVDA** (Windows/Firefox) and **VoiceOver** (macOS/Safari).
   This review runs on a **committed cadence** — quarterly, and before a release or
   a change to the rendered surface — documented with its page list, checklist, and
   a results log in
   [`docs/accessibility/MANUAL-REVIEW-CADENCE.md`](accessibility/MANUAL-REVIEW-CADENCE.md).
   Its findings are reflected in the ACR's remarks.

A change that breaks the automated floor cannot merge because CI is red. A change
that would degrade the human-judged surface is caught by the manual review and
recorded honestly in the ACR rather than papered over. Together the automated axe
evidence and the manual cadence are the ACR's stated **evidence basis**.

## Status messages (WCAG 2.2 SC 4.1.3)

A status message tells you the outcome of what you just did: how many records the
search matched, that a filter matched nothing, that a submission was rejected, that
the steward queue could not be read. A sighted reader sees it appear. A screen-reader
user hears it only if it sits inside an **ARIA live region**, because nothing moved
focus to it — so a message rendered outside one is, for them, simply not there.

**Where the regions are.** Every status message the site renders is wrapped by
`render._status_region`, which emits one `<div class="results-status" role="status"
aria-live="polite">` around that message and nothing else:

| Surface | Message | Register |
|---|---|---|
| Browse / search | the result count, or "no records match" | polite |
| `/overview` | the collection total, or the empty state | polite |
| `/places`, `/timeline` | the empty state | polite |
| Steward console | an empty submission queue, an empty request queue | polite |
| `/consent-status` | a request's progress, or "no such reference" | polite |
| `/transparency` | a stale or counsel-unreviewed attestation | polite |
| Contribute / withdraw / edit | a rejected submission | **assertive** (`role="alert"`) |
| Steward console | a queue that could not be read | **assertive** (`role="alert"`) |
| Record view | the content-warning interstitial, whose `h1` *is* the warning | **assertive** (`role="alert"`) |

Polite is the default. The assertive register is reserved for the three cases where
the reader has to stop and act — a submission that was not accepted, a queue the
console cannot see, and a content warning standing between the reader and the
record. Nothing else interrupts.

**Why the gate checks scope too.** An over-broad live region is worse than no live
region at all: everything inside one is re-announced on every change, so a region
drawn around `<main>` or around the results list turns each navigation into a wall
of speech and teaches the reader to tune the region out — including the one message
that mattered. The static gate therefore fails the build on *both* failures: a
status message outside every live region, and a live region placed on page
structure (`<body>`, `<main>`, `<nav>`, `<header>`, `<footer>`, `<form>`) or grown
to swallow the site navigation or a form.

**How the rule knows what a status message is.** Nothing in HTML says which
paragraph is *about* an outcome, which is precisely why axe cannot judge this
criterion. The gate uses the one mechanical fact available: ledger renders every
status message with one of a fixed set of CSS classes (`count`, `empty`, `error`,
`status`, `warning`, `results-status`), listed in
`ledger.accessibility_check._STATUS_CLASSES`. Adding a class there is how a new kind
of status message opts into the rule. The list view's `view-empty` filler is
deliberately *not* in that set: it is the body of one of two equivalent views of the
same result set, and the live region above it has already announced the outcome —
announcing it again would say the same thing three times.

`tests/test_aria_live_status.py` drives every dynamic state (populated and empty,
clean and rejected, plus the two console surfaces that only exist behind a live
server) and carries negative controls: the same page with its live region removed,
and with the region widened over `<main>`, must both fail the checker, so a checker
that quietly stopped looking cannot read as a clean surface.

What none of this can establish is whether the announcement is actually *heard* —
that is a question for the NVDA/VoiceOver cadence above, and the ACR says so.

## The ACR (`docs/accessibility/ACR.md`)

The **Accessibility Conformance Report** is a committed artifact using the **VPAT
2.5 (Rev 508)** template. It is the honest, human-judged conformance picture across
the full WCAG 2.x A/AA set (including the WCAG 2.2 additions), the Revised 508
software (Chapter 5) and support-documentation (Chapter 6) requirements, and the
five-area Functional Performance Criteria (Chapter 3).

- **It is generated, not hand-maintained.** The whole report is built from one
  in-code data structure in `src/ledger/acr_gen.py`, so it is a single source of
  truth that regenerates deterministically with no drift between the code and the
  document. Run:

  ```
  make acr
  ```

  which runs `python -m ledger.acr_gen > docs/accessibility/ACR.md`.

  Being generated is not the same as being current, and for most of this report's
  life nothing checked the difference: `make acr` writes the file, `make acr` was not
  part of `make verify`, and no test opened it. An edit to `acr_gen.py` could ship a
  conformance level the committed document contradicted, with every gate green.
  `make verify` now composes **`make acr-check`**, which renders the report into
  memory and fails on any byte of difference, and `tests/test_acr_current.py` asserts
  the same thing from the suite. Both compare; neither regenerates into the working
  tree, because a check that repairs its own subject hides exactly the drift it exists
  to find.

  ```
  make acr-check
  ```

- **It is regenerated and re-committed on each release**, the same
  audit-as-artifact discipline ledger applies to fixity. The placeholder
  `docs/accessibility/.gitkeep` reserves the directory; `make acr` produces the
  report itself.

- **It is candid.** Where support is genuinely partial or aspirational, the report
  says `Partially Supports` with a specific remark naming the work still owed —
  rather than overstating a uniformly green `Supports`. The `Partially Supports`
  rows currently include the two form-error criteria (3.3.1, 3.3.3) and input-purpose
  identification (1.3.5), all three because the public surface's only input is a
  free-text search field with little to get wrong or to suggest; the authoring-tool
  criterion (504) because the ingest CLI nudges but does not yet actively prompt for
  every piece of accessibility information; support documentation (602.3); and
  302.9 (limited language/cognitive) pending testing with the readers it names. An
  ACR a reader can trust is worth more than one they cannot.

  Because the report is generated, this list is the *second* place these levels are
  written down, and a second place is a place that goes stale. Both criteria this
  paragraph named before — contrast (1.4.3/1.4.11) and status messages (4.1.3) —
  had reached `Supports` in `acr_gen.py` while this sentence still called them
  partial; `make acr-check` compares the generator with the committed ACR and had
  nothing to say about a prose summary elsewhere in the docs. Treat
  [`docs/accessibility/ACR.md`](accessibility/ACR.md) as authoritative and this
  paragraph as a pointer.

## Running the checks locally

```
make accessibility   # run the automated structural gate over the web/ surface
make acr             # regenerate docs/accessibility/ACR.md from src/ledger/acr_gen.py
make acr-check       # fail if the committed ACR has drifted from acr_gen (in `verify`)
```

The browser-real axe pass (the same one CI's `accessibility-browser` job runs) is
opt-in locally, since it needs Node and a browser:

```
cd tools/a11y_browser
npm ci
npx playwright install chromium
npx playwright test          # seeds + serves the demo, then runs axe + keyboard specs
```

`make accessibility` is the same command CI runs, so green locally means green in
CI. The full picture is: the static gate proves the structural floor on every
commit; the browser axe job adds rendered-contrast and focus-order depth in both
colour schemes; the committed NVDA/VoiceOver cadence covers what no machine can
judge; and the ACR records the candid, end-to-end conformance result for anyone who
needs it.
