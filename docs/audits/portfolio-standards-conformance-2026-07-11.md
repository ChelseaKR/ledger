# Portfolio-standards conformance audit — 2026-07-11

**Target:** `ChelseaKR/ledger` working tree

**Standard source:** `ChelseaKR/portfolio-standards` main at
`8072c33b5c2fcb93ad2286f07f73acf388055c24`; ledger CI remains pinned to the
latest released standard, `v1.0.1`.

**Method:** official Tier-1 checker in strict per-repo mode, manual review of
applicable AUTO/REVIEW controls, live GitHub ruleset/security/label inspection,
and full local `make verify`.

## Outcome

The official mechanical score moved from **26/31 to 31/31**. The full local gate
passes with 980 tests. No human review was forged and no first release was cut.
Remaining work is limited to controls that require observed network allowlists,
an owner choice about single-maintainer review enforcement, external account
setup, additional coverage/refactoring/tooling, or actual human/AT review.

## Remediated

- Added `.python-version`, `.standards-version`, complete CFF metadata, the ADR
  seed, and repository-level catalog discovery.
- Replaced the stale/incomplete applicability table with all thirteen current
  standards and added ADR 0009 for Incident Response/Data Governance scope.
- Added OpenSSF Scorecard with SHA-pinned Actions and SARIF publication.
- Enabled GitHub private vulnerability reporting and created `incident`,
  `sev1`–`sev4`, and `deploy-caused` labels.
- Added the incident runbook/postmortem template, L3 data-governance policy,
  community-contribution data card, and residual-risk register.
- Expanded the pull-request Definition-of-Done checklist and CODEOWNER coverage.
- Removed stale claims about absent Trivy, digest pinning, Harden-Runner,
  pre-commit, branch rules, performance gates, SBOM/signing, and browser a11y.
- Converted every remaining conformance blocker into an open, linked GitHub
  issue (#78–#84) as required by DOC-13.

## Remaining review/account controls

| Area | Evidence / blocker |
|---|---|
| Deny-by-default workflow egress | [#78](https://github.com/ChelseaKR/ledger/issues/78) — derive real per-job allowlists before changing audit to block mode |
| Full branch/review/dependency-alert rules | [#79](https://github.com/ChelseaKR/ledger/issues/79) — owner decision needed to avoid locking out a solo maintainer |
| First trusted release | [#80](https://github.com/ChelseaKR/ledger/issues/80) — signer identity, PyPI environment/publisher, and first signed tag |
| Browser + human accessibility evidence | [#81](https://github.com/ChelseaKR/ledger/issues/81) — Lighthouse/pa11y/reflow/statement and real NVDA/VoiceOver passes |
| Accountable-owner / independent reviews | [#82](https://github.com/ChelseaKR/ledger/issues/82) — ethics, bias, DPIA, crypto, and residual-risk sign-off |
| Coverage, complexity, suppression hygiene | [#83](https://github.com/ChelseaKR/ledger/issues/83) — published-library 90% floor and waiver removal |
| OSV + local Semgrep parity | [#84](https://github.com/ChelseaKR/ledger/issues/84) — locked tooling and complete local/CI parity |

## Source-standard discrepancy

The shared `applicability.yml` currently marks ledger `html: false` and
Accessibility N/A, while ledger plainly renders a human-facing archive,
contribution, and steward interface. The local declaration remains
Accessibility Applies because an N/A claim would be false. ADR 0009 records the
discrepancy for upstream correction.

## Verification evidence

- `python portfolio-standards/automation/conformance_check.py --repo . --strict
  --network` → 31/31
- `make verify` → lint, formatting, strict mypy, 980 tests, i18n, structural
  accessibility, pip-audit, gitleaks, truthfulness checks, and zizmor green
- `git diff --check` → clean
- Workflow YAML parse + zizmor → green
