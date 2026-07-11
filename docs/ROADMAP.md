# Roadmap and conformance gap tracker

Last verified: 2026-07-05 · Recheck cadence: per release

This file has two jobs: the feature roadmap belongs in
[`docs/RESEARCH-ROADMAP.md`](RESEARCH-ROADMAP.md); this file is the **standards
conformance gap tracker** the README's
[`## Standards conformance`](../README.md#standards-conformance) table links to
(DOCUMENTATION-STANDARD DOC-13: every "gap" declaration must link something a
reader can actually open, not a bare assertion).

Each row below traces to a specific control in the portfolio's `STANDARDS/` and to
the remediation item that will close it. Where a row's status changes, update it
here rather than letting the README table go stale.

**A note on tracking mechanism.** The standard's own worked examples link a GitHub
issue per gap. This repo tracks gaps here instead: opening a batch of GitHub issues
is a live write against a real repository that this remediation pass deliberately
did not perform (see `ledger-REMEDIATION.md` Execution Log, 2026-07-05). If the
maintainer later opens issues for any of these, replace the row's link with the
issue URL; until then, this table is the source of truth for "is this actually
tracked."

## Open conformance gaps

| Standard | Control(s) | Gap | Status | Closes when |
|---|---|---|---|---|
| Security & Supply Chain | SEC-28, REL-18 | No container CVE scan; base image pinned by tag, not digest | Open | Trivy job + digest pin (P1-1) |
| Security & Supply Chain | SEC-07 | No Semgrep; SAST coverage today is CodeQL + ruff-S only | Open | P1-3 |
| Security & Supply Chain | SEC-19 | No scheduled full-history secret scan (TruffleHog) | Open | P1-3 |
| Security & Supply Chain | SEC-04 | No Harden-Runner egress policy on any workflow | Open | P1-7 |
| Security & Supply Chain | SEC-17 | No pre-commit hooks | Open | P1-4 |
| Security & Supply Chain | SEC-27, SEC-29 | ~~No SBOM, signing, or provenance workflow~~ Closed 2026-07-10: `release.yml` generates a CycloneDX SBOM, cosign-signs (keyless) every artifact, and records SLSA build-provenance + SBOM attestations on every tagged release | Closed | 2026-07-10 (`.github/workflows/release.yml`) |
| Security & Supply Chain | SEC-35, SEC-36, SEC-37 | No OpenSSF Scorecard workflow (§6.5 checks: Signed-Releases, Vulnerabilities, aggregate score) | Open | P1-6b |
| CI/CD | CICD-12, CQ-37/38/39/40/43, SEC-15 | No committed branch-protection/ruleset export; server-side settings unverifiable offline | Open | P2-4 — **⛔ requires the repo owner to export/enable via `gh api repos/ChelseaKR/ledger/rulesets` themselves** (write-effect GitHub API call, out of scope for an automated pass) |
| CI/CD | CICD-19, CICD-20 | No zizmor workflow-linter job; CodeQL doesn't analyze `language: actions` | Open | P1-3 |
| Release & Versioning | REL-08, REL-13–17, REL-20 | ~~No tag-triggered release workflow~~ Closed 2026-07-10: `release.yml` triggers on `v*` tags, re-runs lint/type/test at the tagged commit, verifies tag/version consistency, builds, generates SBOM+provenance, cosign-signs, publishes to PyPI via Trusted Publishing (OIDC), and mirrors artifacts to a GitHub Release. Registering the PyPI Trusted Publisher + `pypi` GitHub Environment is a one-time manual step for the project owner (workflow header); no `vX.Y.Z` tag has been pushed yet, so the pipeline is unexercised end-to-end (REL-03 below) | Closed (unexercised pending first tag) | 2026-07-10 (`.github/workflows/release.yml`) |
| Release & Versioning | REL-03 | CHANGELOG declares `0.1.0` "released" 2026-06-16; no matching git tag exists | Open — claim corrected in CHANGELOG.md pending real cut (P2-6) |  |
| Accessibility | A11Y-01–03, 07, 09 | axe-core / Lighthouse / pa11y / Playwright keyboard+reflow specs not run in CI (structural checker + manual review substitute today) | Open | P3-7 |
| Accessibility | A11Y-11, 12, 16, 18 | No dated screen-reader/keyboard walkthrough artifact or `docs/a11y/STATEMENT.md` | Open | P2-3 |
| Responsible Tech | RTF-03 | No dated bias / representational-harm review artifact | Open | P2-2 |
| Responsible Tech | RTF-04 | No DPIA — the highest-priority artifact gap in the repo (L2 PII archive with no data-protection impact assessment) | Open | P2-2 |
| Responsible Tech | RTF-01 | Ethics/consequence scan substance exists (README, THREAT-MODEL, GOVERNANCE) but no committed, dated, signed-off artifact | Open | P2-2 |
| Quality & Metrics | QM-02 | No performance budgets/benchmarks in CI (README claim already removed pending this — see CHANGELOG) | Open | P3-5 |
| Quality & Metrics | QM-11, QM-18 | No DORA delivery-health review artifact; no root `DEFINITION_OF_DONE.md` | Open | P2-5 |
| Code Quality | CQ-47 | No mutation testing on safety modules (`access/`, `identity.py`, `fixity.py`) | Open | P3-3 |
| Code Quality | CQ-05 (partial) | Complexity gate is now enforced (`ruff` C901, max 10); 7 pre-existing functions exceed it and are waived with dated `# noqa: C901` comments pending a deliberate, fully-retested split — not rushed under audit time pressure on safety-adjacent code | Open (waived) | See `# noqa: C901` sites in `accessibility_check.py`, `bag.py`, `cli.py`, `contribute.py`, `ingest.py`, `server.py` |

## Metrics (QUALITY-AND-METRICS-STANDARD, CICD-29)

| Metric | Value | Measured by | Date |
|---|---|---|---|
| Test suite | 528 passed | `make test` | 2026-07-05 |
| Branch coverage | 86.4% (floor: 85%, `fail_under` in `pyproject.toml`) | `make cov` | 2026-07-05 |
| Tier-1 mechanical score | 8/11 (pre-remediation); coverage floor + Makefile-mute items in this pass address 2 of the 3 failing checks | `STANDARDS/automation` Tier-1 check | 2026-07-05 |
| `make verify` == CI required checks | Yes, as of this pass (lint, type, test, i18n, accessibility, audit, secret-scan) | manual trace of `ci.yml` jobs to `Makefile` targets | 2026-07-05 |

DORA five-metric delivery-health review: not yet established (QM-11, tracked above).
