# Manual assistive-technology review cadence

This document commits ledger to a **recurring manual screen-reader review** and
records its results over time. It is the human half of the accessibility evidence
basis; the machine half is the automated gate
(`python -m ledger.accessibility_check web`) plus the browser-real axe job
(`accessibility-browser` in `.github/workflows/ci.yml`).

A machine can prove the structural floor and, with a real browser, catch rendered
contrast and focus-order defects. It cannot judge whether the reading order *makes
sense*, whether the content-warning interstitial is *announced* before the material
is reached, whether an `aria-live` status is *heard*, or whether a form error is
*understandable* when spoken. Those are judgements only a person using the
technology can make. This cadence exists so that judgement happens on a schedule
and is written down, not left to memory or to the week before a release.

## Cadence

- **Quarterly**, and additionally **before any release** and **before merging a
  change that touches the rendered HTML surface** (`src/ledger/server.py`,
  `src/ledger/contribute.py`, `src/ledger/render.py`, or `web/static/app.css`).
- Each pass uses **two** assistive-technology + browser combinations:
  - **NVDA on Windows with Firefox** (the most-used free Windows screen reader), and
  - **VoiceOver on macOS with Safari** (the platform screen reader Apple ships).
- A pass covers the **same canonical pages** the automated axe job drives, so the
  manual and automated evidence describe the same surface:

  | # | Page | State to exercise |
  |---|------|-------------------|
  | 1 | `/` | Browse — list view and the equivalent data-table view |
  | 2 | `/search?q=…` | Search results, facet controls, and result count |
  | 3 | `/record/{id}` | **Content-warning interstitial** (before proceeding) |
  | 4 | `/record/{id}?proceed=1` | Record content with the warning restated |
  | 5 | `/contribute` | Contribution form (labels, hints, errors) |
  | 6 | `/steward` | Steward console (with a provisioned grant) |
  | 7 | `/about` | A representative static prose page |
  | 8 | `/how-it-works` | A representative explanatory page |

  Seed a local server exactly as CI does:

  ```
  cd tools/a11y_browser
  python -m serve_demo        # seeds ./local-archive, serves on http://127.0.0.1:8099
  ```

## Review checklist (per page, per AT/browser)

Judge the criteria a static or even a browser-automated scan cannot:

- **Landmarks & headings** — the skip link works; `<main>`, navigation, and the
  single `<h1>` are announced; heading levels form a sensible outline the reader
  can navigate by.
- **Reading order** — the spoken order matches the visual/logical order; nothing
  important is reached only by chance.
- **Content-warning interstitial** — the warning is **announced as text before**
  the underlying material is reachable, and the "proceed" control is clearly a
  choice, not an obstacle. After proceeding, the warning is restated.
- **`aria-live` / status messages** — dynamic state (e.g. a search result count,
  a validation summary) is **announced** when it changes, without moving focus
  unexpectedly.
- **Form errors** — a contribute-form validation error is announced, is associated
  with the field it concerns, and is understandable when spoken in isolation.
- **List/table equivalence** — the semantic list view and the data-table view
  convey the *same* records and access state to a non-visual reader.
- **Focus visibility & operability** — every control is reachable and operable by
  keyboard alone, and focus is always locatable.

## Recording results

After each pass, add a row to the log below and, if the pass changed the honest
conformance picture, update the corresponding rows in
[`ACR.md`](./ACR.md) via `src/ledger/acr_gen.py` (`make acr`) — the ACR is
generated, so edit the source data structure, not the rendered file. File any
concrete defect as an issue and link it in the **Findings** cell.

### Results log

| Date | Reviewer | AT / browser | Pages | Findings | ACR rows updated |
|------|----------|--------------|-------|----------|------------------|
| 2026-07-02 | (maintainer) | Cadence committed — first NVDA/VoiceOver pass due 2026-Q3 | — | Automated axe job (`accessibility-browser`) landed alongside this cadence; manual baseline pass not yet run | — |

> Add one row per AT/browser per pass. Keep the newest rows at the top under this
> line. An empty **Findings** cell is not allowed — write "no findings" explicitly
> so a blank is never mistaken for an unrun review.
