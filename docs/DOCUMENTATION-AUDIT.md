# Documentation Audit

Last reviewed: 2026-07-08. Base branch: `main`.

This audit records the documentation sweep and remediation loop for this repository. It checks the docs as a system: entry points, root-level process and legal files, project scope, setup and validation notes, safety and privacy posture, architecture and planning docs, local links, and the places where code, tests, workflows, and docs meet.

## Audit Results

| Area | Result | Evidence |
| --- | --- | --- |
| Entry docs | pass | `README.md` present |
| Security/process docs | pass | CONTRIBUTING.md, SECURITY.md, CHANGELOG.md |
| Architecture/planning docs | pass | 9 architecture/interface docs; 5 planning/research docs |
| Safety/privacy/audit docs | pass | 6 safety/privacy/accessibility/audit docs |
| Validation surface | pass | 53 test files; 3 workflow files |
| Local doc links | pass | 193 authored-doc links checked; 0 unresolved |

## Root-Level Documentation Audit

This section covers hand-authored documentation at the repository root and root-adjacent GitHub templates. It is separate from the `docs/` inventory so README, process, legal, release, and project-specific root files do not get hidden inside the larger docs tree.

| Surface | Result | Evidence |
| --- | --- | --- |
| Root README | pass | Present: `README.md` |
| Root process docs | pass | Present: `CONTRIBUTING.md`, `SECURITY.md`, `CHANGELOG.md` |
| Root legal, citation, and conduct docs | pass | Present: `LICENSE`, `NOTICE`, `CITATION.cff`, `CODE_OF_CONDUCT.md` |
| Other root project docs | info | None found. |
| Root-adjacent GitHub templates | pass | `.github/PULL_REQUEST_TEMPLATE.md`, `.github/CODEOWNERS` |
| Root/template doc links | pass | 33 root-level/template links checked; 0 unresolved |

Root-level files checked:

- `CHANGELOG.md`
- `CITATION.cff`
- `CODE_OF_CONDUCT.md`
- `CONTRIBUTING.md`
- `LICENSE`
- `NOTICE`
- `README.md`
- `SECURITY.md`

Root-adjacent template files checked:

- `.github/PULL_REQUEST_TEMPLATE.md`
- `.github/CODEOWNERS`

## Remediation In This PR

- Added missing root-level remediation docs found by the audit loop, including legal, conduct, contribution, or security files where absent.
- Added `docs/PROJECT-SCOPE.md` as the plain-language project and boundary map.
- Added this audit record so future doc changes have a dated baseline.
- Added or refreshed the docs index so scope, audit, and primary docs are easy to find.

## Repo Surfaces Checked

Package and workspace metadata:

- Python package `ledger-archive` (>=3.12).

Source and operations surfaces seen at the repo root:

- `infra/`
- `Makefile`
- `pyproject.toml`
- `src/`
- `tests/`
- `tools/`
- `web/`

Workflow files checked:

- `.github/workflows/ci.yml`
- `.github/workflows/codeql.yml`
- `.github/workflows/standards.yml`

## Documentation Inventory

| Category | Count | Representative files |
| --- | ---: | --- |
| architecture and interfaces | 9 | `docs/ARCHITECTURE.md`, `docs/adr/0001-record-architecture-decisions.md`, `docs/adr/0002-agpl-3-0-license.md`, `docs/adr/0003-identity-separated-from-record.md`, `docs/adr/0004-bagit-premis-dublin-core-oais.md`, `docs/adr/0005-stdlib-first-single-dependency.md`, `docs/adr/0006-standards-applicability.md`, `docs/adr/0007-withhold-not-403.md`, plus 1 more |
| entry points and repo process | 10 | `.github/CODEOWNERS`, `.github/PULL_REQUEST_TEMPLATE.md`, `CHANGELOG.md`, `CITATION.cff`, `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `LICENSE`, `NOTICE`, plus 2 more |
| examples and guides | 6 | `tests/fixtures/README.md`, `tests/fixtures/community.txt`, `tests/fixtures/public.txt`, `tests/fixtures/sealed_conditional.txt`, `tests/fixtures/sealed_until.txt`, `tests/fixtures/stewards.txt` |
| other docs | 13 | `docs/ADOPTING.md`, `docs/CONTINUITY.md`, `docs/GOVERNANCE.md`, `docs/I18N.md`, `docs/MUTUAL-AID.md`, `docs/PROJECT-SCOPE.md`, `docs/README.md`, `docs/oral-history/README.md`, plus 5 more |
| planning and research | 5 | `docs/RESEARCH-ROADMAP.md`, `docs/ROADMAP.md`, `docs/USER-RESEARCH.md`, `docs/ideation/02-large-scale-fixes.md`, `docs/research/USER-RESEARCH.md` |
| safety, privacy, accessibility, and audits | 6 | `docs/ACCESSIBILITY.md`, `docs/DOCUMENTATION-AUDIT.md`, `docs/RESPONSIBLE-TECH-AUDITS.md`, `docs/THREAT-MODEL.md`, `docs/accessibility/ACR.md`, `docs/audits/crypto-agility-pq-posture.md` |

Full hand-authored doc inventory checked by this pass:

- `.github/CODEOWNERS`
- `.github/PULL_REQUEST_TEMPLATE.md`
- `CHANGELOG.md`
- `CITATION.cff`
- `CODE_OF_CONDUCT.md`
- `CONTRIBUTING.md`
- `LICENSE`
- `NOTICE`
- `README.md`
- `SECURITY.md`
- `docs/ACCESSIBILITY.md`
- `docs/ADOPTING.md`
- `docs/ARCHITECTURE.md`
- `docs/CONTINUITY.md`
- `docs/DOCUMENTATION-AUDIT.md`
- `docs/GOVERNANCE.md`
- `docs/I18N.md`
- `docs/MUTUAL-AID.md`
- `docs/PROJECT-SCOPE.md`
- `docs/README.md`
- `docs/RESEARCH-ROADMAP.md`
- `docs/RESPONSIBLE-TECH-AUDITS.md`
- `docs/ROADMAP.md`
- `docs/THREAT-MODEL.md`
- `docs/USER-RESEARCH.md`
- `docs/accessibility/ACR.md`
- `docs/adr/0001-record-architecture-decisions.md`
- `docs/adr/0002-agpl-3-0-license.md`
- `docs/adr/0003-identity-separated-from-record.md`
- `docs/adr/0004-bagit-premis-dublin-core-oais.md`
- `docs/adr/0005-stdlib-first-single-dependency.md`
- `docs/adr/0006-standards-applicability.md`
- `docs/adr/0007-withhold-not-403.md`
- `docs/adr/0008-segno-optional-print-dependency.md`
- `docs/audits/crypto-agility-pq-posture.md`
- `docs/ideation/02-large-scale-fixes.md`
- `docs/oral-history/README.md`
- `docs/oral-history/facilitator-script.md`
- `docs/oral-history/session-manifest-format.md`
- `docs/research/USER-RESEARCH.md`
- `infra/README.md`
- `infra/aws/README.md`
- `tests/fixtures/README.md`
- `tests/fixtures/community.txt`
- `tests/fixtures/public.txt`
- `tests/fixtures/sealed_conditional.txt`
- `tests/fixtures/sealed_until.txt`
- `tests/fixtures/stewards.txt`
- `web/README.md`

## Link Check

- Checked 193 local links in authored Markdown and MDX docs.
- Unresolved authored-doc links after remediation: 0.
- Root-level/template unresolved links after remediation: 0.

Audit scope notes:

- Generated sites, deployed app routes, raw third-party HTML captures, and golden fixture websites were inventoried as product or data surfaces but excluded from authored-doc link failure counts.

## Validation Notes

- The audit was generated from a clean worktree based on `origin/main` for this PR branch.
- Ran a local relative-link check over hand-authored Markdown and MDX docs.
- Ran an explicit root-level documentation presence and link check for README, process, legal, project, and template docs.
- Ran `git diff --check` across the PR worktrees after remediation.
- Product test suites remain the authority for runtime behavior; this PR changes documentation only.
