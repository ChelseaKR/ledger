# DORA delivery-health review — ledger

> **Reviewed: 2026-07-07 · Recheck cadence: quarterly** (or sooner on a material
> change to how the repo deploys), matching `docs/ROADMAP.md`'s cadence note
> (DOC-15) and `QUALITY-AND-METRICS-STANDARD.md`'s DORA section. Instantiates
> QM-11 for ledger; the companion `DEFINITION_OF_DONE.md` at repo root
> instantiates QM-18.

This is a **portfolio-level delivery-health signal**, not a per-PR gate, per
`QUALITY-AND-METRICS-STANDARD.md` — measured from CI/CD events and reviewed on
a cadence, not tracked by hand PR-by-PR. It reports what the five-metric 2024
DORA model actually says about ledger today, including where the metric
doesn't apply yet and why, rather than filling in a table with invented
numbers.

## Why this repo's DORA shape is unusual

The reference implementation (`dora-team/fourkeys`) assumes a service the
maintainer deploys to production and can instrument with deploy + incident
events. Ledger doesn't fit that shape: it is **self-hosted archive software** a
community runs on its own infrastructure (a member's drive, a community
server, an off-site mirror — see `README.md`). There is no ledger-operated
production environment, no deploy pipeline pushing to one, and therefore no
real "deployment" event to measure Change Fail Rate, Failed-Deployment
Recovery Time, or Deployment Rework Rate against.

`QUALITY-AND-METRICS-STANDARD.md` §"DORA" anticipates this: *"library/CLI
repos report DF/LT only."* Ledger is closer to that end of the spectrum than
to a hosted service, even though it ships a web server component, because the
server is something operators self-host rather than something this repo
operates. This review therefore:

- Reports **Deployment Frequency** and **Change Lead Time** using **merge to
  `main`** as the deploy proxy (the point at which a change becomes available
  to anyone who builds or pulls from `main`), consistent with the standard's
  guidance for library/CLI-shaped repos.
- Records **Change Fail Rate**, **Failed-Deployment Recovery Time**, and
  **Deployment Rework Rate** as **N/A today, with the exact trigger that
  retires the N/A**: the tag-triggered release workflow tracked at
  `docs/ROADMAP.md` (REL-08, P1-6). Once ledger cuts real, versioned releases
  installers pull from, a "failed deployment" becomes meaningful (a release
  that gets yanked or hotfixed) and these three metrics get real numbers
  instead of a placeholder.
- Declares this explicitly rather than leaving the row blank, per the
  standard's "declare N/A, never silently skip" rule.

## Method

Source data: `gh pr list --repo ChelseaKR/ledger --state merged --json
number,title,createdAt,mergedAt` (GitHub's PR API, not hand-tracked) plus `git
log` on `main`, both queried 2026-07-07. All 13 PRs merged to `main` are
counted; `main` currently has no tags/releases (`git tag` is empty — the
release-workflow gap tracked as REL-08/P1-6), so lead time is measured PR-open
to PR-merge rather than commit-to-deploy.

## Results (2026-07-07, covers the repo's full history: 2026-06-17 to
## 2026-07-07, 13 merged PRs)

| DORA metric | Portfolio floor | Ledger, this window | Gate |
|---|---|---|---|
| Deployment Frequency | ≥ weekly per active repo | **13 merges to `main` in 17 days** (~5.4/week) — above floor | health signal |
| Change Lead Time (PR open → merge) | P90 < 1 day | **All PRs: P90 ≈ 327 h** (13.6 d), pulled up entirely by 2 Dependabot PRs (#1, #3) that sat unmerged for ~13.6 days before being batched-merged with other work, not by review friction. **Excluding Dependabot bumps (10 authored PRs): P90 ≈ 23.2 h, median ≈ 0.7 h** — inside the floor. | health signal — see note below |
| Change Fail Rate | < 15% (alert > 10%) | **N/A** — no deploy/release events to compute a failure rate against (see "unusual shape" above); 0 revert commits and 0 GitHub issues opened against `main` in this window is the closest proxy, and it is clean, but it is not the metric | health signal, N/A pending REL-08 |
| Failed-Deployment Recovery Time | < 1 day (alert if any incident > 4h) | **N/A** — no incidents recorded (no GitHub issues labelled `incident`, none opened at all in this window); no release to recover from a bad state of | health signal, N/A pending REL-08 |
| Deployment Rework Rate *(2024 metric)* | < 10% (alert > 5%) | **N/A** — no deploy-tracking to classify a merge as planned vs. unplanned-fix against | health signal, N/A pending REL-08 |

**Note on the Dependabot outlier.** #1 and #3 (`actions/upload-artifact`,
`actions/setup-python` version bumps) were opened 2026-06-17 and sat until
2026-06-30, when they were merged in the same batch as several hand-authored
PRs. That's a real gap worth naming rather than averaging away: Dependabot PRs
are not being triaged on their own cadence, they're riding along with
unrelated work. It doesn't reflect review latency on authored changes (the
10-PR figure above is the honest read of that), but it does mean a security-
relevant Actions-pin bump can sit unreviewed for two weeks. Action: `renovate.json`
(added 2026-07-05, `1dc9880`) now handles Actions digest pinning on a
schedule — the next review should check whether Renovate PRs are merging
faster than the Dependabot PRs they replace.

## 2024/2025 findings applied

Per `QUALITY-AND-METRICS-STANDARD.md`: AI-adoption research shows throughput
gains paired with stability risk unless safety nets are prerequisite
infrastructure, not optional hygiene. Ledger's safety nets (branch coverage
floor with `--cov-fail-under=85`, mypy `--strict`, the no-outing audit gate,
pip-audit blocking, gitleaks, CodeQL) are already AUTO-GATE per
`DEFINITION_OF_DONE.md`, which is the portfolio's answer to "throughput
without stability" for this repo. The remaining Security gaps (Semgrep,
zizmor, SBOM/cosign/SLSA — `docs/ROADMAP.md` P1-3/P1-6) are the concrete next
steps this finding argues for, and are already tracked, not new discoveries
from this review.

The **DORA 2025 AI Capabilities Model** governance checklist (7 gates before
expanding AI tooling scope) does not apply: ledger has no AI/LLM component
(`docs/adr/0006-standards-applicability.md` declares AI-Evaluation N/A).

## What retires each N/A

| Metric | Retires when |
|---|---|
| Change Fail Rate | Tag-triggered release workflow ships (REL-08, P1-6) and at least one release has either succeeded cleanly or needed a follow-up fix, giving a real numerator/denominator |
| Failed-Deployment Recovery Time | Same as above, plus the first incident (a yanked release, a hotfix tag) to time recovery against |
| Deployment Rework Rate | Same as above, plus enough releases (2024 metric wants a 30-day rolling window) to classify planned vs. unplanned-fix releases |

## Accountable owner

Chelsea Kelly-Reif. Next review due 2026-10 (quarterly) or immediately after
the release workflow (P1-6) lands, whichever is sooner — that's the point
this review's three N/A rows get real numbers for the first time.
