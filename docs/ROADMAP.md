# Roadmap and conformance gap tracker

Last verified: 2026-07-11 · Recheck cadence: per release and quarterly

This file has two jobs: the feature roadmap belongs in
[`docs/RESEARCH-ROADMAP.md`](RESEARCH-ROADMAP.md), and the phased sequencing of both
belongs in [`docs/MULTIYEAR-PLAN.md`](MULTIYEAR-PLAN.md); this file is the **standards
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
| Security & Supply-Chain | SEC-04 | 16 of 24 Harden-Runner jobs now enforce `egress-policy: block` with allowlists derived from observed runs. The 8 `release.yml` jobs stay in `audit`: that workflow has never run, so there is nothing to derive an allowlist from, and a wrong guess fails a release *after* the tag is public | Open — [#78](https://github.com/ChelseaKR/ledger/issues/78) | The first real release runs in audit, its observed endpoints are read off that run, and `release.yml` flips to `block` (needs [#80](https://github.com/ChelseaKR/ledger/issues/80) first) |
| Security & Supply-Chain / CI/CD | SEC-11/13, CICD-13/27 | ~~Local Semgrep parity is absent from `make verify`~~ **Closed**: `make semgrep` runs `semgrep scan --config p/ci --error src tests` and is part of `make verify`, in the same CI-authoritative shape as `osv` and `secret-scan` (skips with a message when the binary is absent; CI's required `Semgrep SAST (p/ci)` check remains the gate of record). The row's original closing condition — *locked* Semgrep tooling — was tried and deliberately not met: pinning `semgrep==1.145.0` into the dependency graph pins `click 8.1.8` and `mcp 1.16.0`, which OSV-Scanner reports as carrying 4 High-severity advisories (PYSEC-2026-2132, PYSEC-2026-1617/-3482/-3483) that cannot be bumped independently. Semgrep is therefore an external tool like `gitleaks` and `osv-scanner`, not a dependency | Closed 2026-08-27 — `Makefile` `semgrep:` target, measured OSV output recorded in the commit | Closed |
| Release & Versioning | REL-03/08/17/20 | Release workflow exists, but signer identity, PyPI Trusted Publisher/environment, and first end-to-end release remain owner actions | Open — [#80](https://github.com/ChelseaKR/ledger/issues/80) | First signed tag publishes and verifies successfully with an approved signer |
| Accessibility / Quality | A11Y-02/03/09/11/12/16/18, QM-04 | Axe, Chromium keyboard traversal, and a **blocking 320px reflow gate** (`reflow.spec.ts`, SC 1.4.10) are live, and [`docs/accessibility/STATEMENT.md`](accessibility/STATEMENT.md) is published. A second scan engine (pa11y/Lighthouse) is an open dependency decision — Lighthouse's a11y category is axe-core, which already runs. The first real NVDA/VoiceOver evidence remains, and cannot be produced by any scan | Open — [#81](https://github.com/ChelseaKR/ledger/issues/81) | Human AT rows in `MANUAL-REVIEW-CADENCE.md` are dated by actual reviewers |
| Responsible Tech | RTF-01/03/04/06, QM-09 | Ethics, bias, DPIA, crypto, and residual-risk artifacts are prepared but accountable-owner/independent sign-off cannot be automated | Open — [#82](https://github.com/ChelseaKR/ledger/issues/82) | Named humans review and sign the artifacts; no Production claim before then |
| Code Quality | CQ-05/08/34/35 | Suppression hygiene is a blocking gate (`make hygiene`, `tools/check_hygiene.py`) and every suppression is coded, explained, and issue-linked where temporary; the 8 `C901` complexity waivers link #83. The pooled scoped floor is gone: `tools/check_coverage_floors.py` now gates each of the 8 security-core modules on its own measured value, and refuses both an unfloored security-core module and a floor naming a module that no longer exists (ADR 0015). The global branch-coverage floor is ratcheted 85% → 88% (measured 89.25%); CQ-08's published-library target of 90% needs ~0.8 more points of real tests, and the 8 complex functions — the public GET/POST route tables, the SIP ingest pipeline, BagIt validation, WCAG element/rule checks, CLI ingest option handling, and untrusted-form field validation — are still waived rather than refactored. Each is a dispatch table or an independent-branch sequence with a documented reason a mechanical split would make worse, not oversight; refactoring the ingest pipeline and the public route handlers with safety-preserving tests is real, non-mechanical work on the repo's most security-critical paths | Open — [#83](https://github.com/ChelseaKR/ledger/issues/83) | Branch coverage reaches 90% and the 8 `C901` waivers are refactored away rather than tracked |

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

## Closed in the 2026-08-21 ruleset pass

- **CI/CD, CICD-10–16/CQ-37–43/SEC-15 ([#79](https://github.com/ChelseaKR/ledger/issues/79)).**
  The live `protect-main` ruleset, mirrored in-tree at
  [`.github/rulesets/main.json`](../.github/rulesets/main.json), now holds the §5.1
  solo-maintainer profile: a `pull_request` rule (0 required approvals — the sole
  code owner cannot self-approve, and §5.1 permits `false` on
  `require_code_owner_review` for exactly this reason), `required_signatures`
  (GitHub signs every squash-merge server-side), `required_linear_history`, and
  `strict_required_status_checks_policy: true`. The required-check set grew from
  eleven contexts to thirteen — `OSV lockfile scan (uv.lock)` and `Semgrep SAST
  (p/ci)` now block a merge instead of only running fail-closed and being
  ignorable. Dependabot security updates were enabled the same day
  (`GET /repos/ChelseaKR/ledger/automated-security-fixes` had returned
  `{"enabled": false}` on 2026-08-15). Full rationale in
  [`.github/rulesets/README.md`](../.github/rulesets/README.md).

## Drafted conformance artifacts

| Standard | Control(s) | Gap | Closed | Artifact |
|---|---|---|---|---|
| Responsible Tech | RTF-01/03/04/06 | Review substance exists, but accountable-owner/independent review is still required | Pending — [#82](https://github.com/ChelseaKR/ledger/issues/82) | [`docs/audits/`](audits/) contains the review-ready artifacts, including the residual-risk register |

## Metrics (QUALITY-AND-METRICS-STANDARD, CICD-29)

| Metric | Value | Measured by | Date |
|---|---|---|---|
| Test suite | 1324 passed | `make test` | 2026-08-27 |
| Branch coverage | 89.25% (floor: 88%, `fail_under` in `pyproject.toml`); 8 security-core modules each gated on their own measured value by `tools/check_coverage_floors.py`, no pooled figure remaining: `grants.py` 100%, `policy.py` 100%, `redaction.py` 100%, `consent.py` 97%, `dualcontrol.py` 100%, `review.py` 97%, `moderate.py` 90%, `tombstones.py` 89% | `make cov` | 2026-08-27 |
| Tier-1 mechanical score | 31/31 after remediation | `portfolio-standards/automation/conformance_check.py --repo . --strict` | 2026-07-11 |
| `make verify` portable gate | Green: lint, strict types, 1324 tests, i18n, structural accessibility, dependency/secret scans, Semgrep SAST, truthfulness, suppression hygiene, zizmor | `make verify` | 2026-08-27 |
| Real-corpus invariants (network; not a merge gate) | 679/679 payloads with exactly one identification event about them; 0 contradictions; 0 success-while-unidentified; 0 record/log divergence; run matches the committed evidence | `make real-corpus` against [`docs/data/real-corpus/`](data/real-corpus/) | 2026-08-21 |
| Mutation score, safety core (advisory, not a gate) | 76.5% (406/531 killed) across `access/`, `identity.py`, `fixity.py` | `make mutation` (mutmut); see `docs/MUTATION-TESTING.md` | 2026-07-07 |

DORA five-metric delivery-health review: established 2026-07-07, reviewed quarterly —
[`docs/DORA-DELIVERY-HEALTH-REVIEW.md`](DORA-DELIVERY-HEALTH-REVIEW.md) (QM-11). Deployment
Frequency and Change Lead Time have real numbers from merged-PR history; Change Fail Rate,
Failed-Deployment Recovery Time, and Deployment Rework Rate are recorded N/A pending the
tag-triggered release workflow (REL-08, P1-6) that gives them something to measure.
