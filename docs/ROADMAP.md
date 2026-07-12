# Roadmap and conformance gap tracker

Last verified: 2026-07-11 · Recheck cadence: per release and quarterly

This file has two jobs: the feature roadmap belongs in
[`docs/RESEARCH-ROADMAP.md`](RESEARCH-ROADMAP.md); this file is the **standards
conformance gap tracker** the README's
[`## Standards conformance`](../README.md#standards-conformance) table links to
(DOCUMENTATION-STANDARD DOC-13: every "gap" declaration must link something a
reader can actually open, not a bare assertion).

Each row below traces to a specific control in the portfolio's `STANDARDS/` and to
the remediation item that will close it. Where a row's status changes, update it
here rather than letting the README table go stale.

Each open row links a live GitHub issue, as DOC-13 requires. Closed controls link
their committed evidence; a roadmap sentence alone is not treated as a waiver.

## Open conformance gaps

| Standard | Control(s) | Gap | Status | Closes when |
|---|---|---|---|---|
| Security & Supply-Chain | SEC-04 | Harden-Runner is present on every job but still observes egress in `audit` mode rather than enforcing per-job allowlists | Open — [#78](https://github.com/ChelseaKR/ledger/issues/78) | Derive allowlists from real runs, switch every job to `block`, and rerun all workflow shapes |
| Security & Supply-Chain / CI/CD | SEC-11/13, CICD-27 | pip-audit is blocking, but OSV lockfile scanning and local Semgrep parity are not yet in `make verify` | Open — [#84](https://github.com/ChelseaKR/ledger/issues/84) | Add locked OSV/Semgrep tooling and preserve fail-closed local/CI parity |
| CI/CD | CICD-10–16, CQ-37–43, SEC-15 | Live `protect-main` ruleset blocks deletion/force-push and requires checks, but checks are non-strict and PR/review/CODEOWNER/signed-commit rules are absent; Dependabot security updates are disabled | Open — [#79](https://github.com/ChelseaKR/ledger/issues/79) | Owner selects a solo-maintainer-safe review model and commits/exports the effective ruleset |
| Release & Versioning | REL-03/08/17/20 | Release workflow exists, but signer identity, PyPI Trusted Publisher/environment, and first end-to-end release remain owner actions | Open — [#80](https://github.com/ChelseaKR/ledger/issues/80) | First signed tag publishes and verifies successfully with an approved signer |
| Accessibility / Quality | A11Y-02/03/09/11/12/16/18, QM-04 | Axe + Chromium keyboard traversal are live; Lighthouse, pa11y, 320px reflow, statement, and first real NVDA/VoiceOver evidence remain | Open — [#81](https://github.com/ChelseaKR/ledger/issues/81) | Automated additions pass and human AT rows are dated by actual reviewers |
| Responsible Tech | RTF-01/03/04/06, QM-09 | Ethics, bias, DPIA, crypto, and residual-risk artifacts are prepared but accountable-owner/independent sign-off cannot be automated | Open — [#82](https://github.com/ChelseaKR/ledger/issues/82) | Named humans review and sign the artifacts; no Production claim before then |
| Code Quality | CQ-05/08/34/35 | Published-library coverage remains 85% and existing complexity/lint/type suppressions have not all been removed or issue-linked | Open — [#83](https://github.com/ChelseaKR/ledger/issues/83) | Reach 90% branch coverage and eliminate or explicitly track each suppression |

## Closed in the 2026-07-11 conformance pass

- Official Tier-1 failures closed: `.python-version`, valid citation release date,
  canonical README declarations, ADR 0000, and discoverable packaged catalogs.
- Container Trivy scanning + digest-pinned base, pre-commit gitleaks/ruff/mypy,
  Semgrep, scheduled TruffleHog, CodeQL Actions analysis, zizmor, performance
  budgets, SBOM/cosign/SLSA release stages, and OpenSSF Scorecard are present.
- Private vulnerability reporting is enabled; `incident`, `sev1`–`sev4`, and
  `deploy-caused` labels exist.
- Incident-response, data-governance/data-card, and residual-risk artifacts are
  committed; human sign-off fields remain honest and issue-backed.

## Drafted conformance artifacts

| Standard | Control(s) | Gap | Closed | Artifact |
|---|---|---|---|---|
| Responsible Tech | RTF-01/03/04/06 | Review substance exists, but accountable-owner/independent review is still required | Pending — [#82](https://github.com/ChelseaKR/ledger/issues/82) | [`docs/audits/`](audits/) contains the review-ready artifacts, including the residual-risk register |

## Metrics (QUALITY-AND-METRICS-STANDARD, CICD-29)

| Metric | Value | Measured by | Date |
|---|---|---|---|
| Test suite | 980 passed | `make test` | 2026-07-11 |
| Branch coverage | 86.4% (floor: 85%, `fail_under` in `pyproject.toml`) | `make cov` | 2026-07-05 |
| Tier-1 mechanical score | 31/31 after remediation | `portfolio-standards/automation/conformance_check.py --repo . --strict` | 2026-07-11 |
| `make verify` portable gate | Green: lint, strict types, 980 tests, i18n, structural accessibility, dependency/secret scans, truthfulness, zizmor | `make verify` | 2026-07-11 |
| Mutation score, safety core (advisory, not a gate) | 76.5% (406/531 killed) across `access/`, `identity.py`, `fixity.py` | `make mutation` (mutmut); see `docs/MUTATION-TESTING.md` | 2026-07-07 |

DORA five-metric delivery-health review: established 2026-07-07, reviewed quarterly —
[`docs/DORA-DELIVERY-HEALTH-REVIEW.md`](DORA-DELIVERY-HEALTH-REVIEW.md) (QM-11). Deployment
Frequency and Change Lead Time have real numbers from merged-PR history; Change Fail Rate,
Failed-Deployment Recovery Time, and Deployment Rework Rate are recorded N/A pending the
tag-triggered release workflow (REL-08, P1-6) that gives them something to measure.
