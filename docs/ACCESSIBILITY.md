# Accessibility

This document states ledger's accessibility commitment, what it covers, and how it
is enforced. It is the prose companion to two machine artifacts: the automated gate
in `src/ledger/accessibility_check.py` and the Accessibility Conformance Report
generated into `docs/accessibility/ACR.md`.

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
   standard-library `html.parser`. It scans the static HTML under `web/` **and**
   the server-rendered sample pages (browse and a single record), and fails the
   build (exit code 1) on any of the structural WCAG 2.x violations it can verify
   statically:
   - a missing or empty `lang` on `<html>` (3.1.1);
   - a missing or empty `<title>` (2.4.2);
   - zero or more than one `<h1>` (1.3.1);
   - a missing `<main>` landmark (1.3.1);
   - a missing skip-to-content link (2.4.1);
   - any `<img>` without an `alt` attribute (1.1.1);
   - any `<input>` without a programmatically associated `<label for>` (1.3.1, 4.1.2);
   - any `<table>` without a `<caption>` or without a `<th scope>` (1.3.1);
   - any positive `tabindex` (2.4.3).

   This is the automatable **floor**, not a claim of full conformance. It catches
   the structural regressions a machine can catch, deterministically, on every
   commit.

2. **Manual screen-reader review (NVDA / VoiceOver).** The criteria a static scan
   cannot judge — meaningful reading order, the quality of the interstitial flow,
   announcement of the content-warning state, the equivalence of the list and table
   views in practice — are verified by manual review with **NVDA** (Windows) and
   **VoiceOver** (macOS/iOS). This manual review is a required step before a release
   and before changes that touch the rendered surface; its findings are reflected in
   the ACR's remarks.

A change that breaks the automated floor cannot merge because CI is red. A change
that would degrade the human-judged surface is caught by the manual review and
recorded honestly in the ACR rather than papered over.

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

- **It is regenerated and re-committed on each release**, the same
  audit-as-artifact discipline ledger applies to fixity. The placeholder
  `docs/accessibility/.gitkeep` reserves the directory; `make acr` produces the
  report itself.

- **It is candid.** Where support is genuinely partial or aspirational, the report
  says `Partially Supports` with a specific remark naming the work still owed —
  rather than overstating a uniformly green `Supports`. The `Partially Supports`
  rows currently include the contrast criteria (1.4.3, 1.4.11) pending an
  independent automated contrast audit, the authoring-tool criterion (504) because
  the ingest CLI does not yet actively prompt for accessibility information, and the
  status-message criterion (4.1.3) because result counts are not yet announced via
  an `aria-live` region. An ACR a reader can trust is worth more than one they
  cannot.

## Running the checks locally

```
make accessibility   # run the automated structural gate over the web/ surface
make acr             # regenerate docs/accessibility/ACR.md from src/ledger/acr_gen.py
```

`make accessibility` is the same command CI runs, so green locally means green in
CI. The full picture is: the automated gate proves the structural floor on every
commit; the manual NVDA/VoiceOver review covers what a machine cannot judge; and the
ACR records the candid, end-to-end conformance result for anyone who needs it.
