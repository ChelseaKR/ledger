# Project Scope

Last reviewed: 2026-07-08. Base branch: `main`.

This file is a plain-language map of the project as it exists on `main`. It does not replace the README, roadmap, audit docs, or source comments. It points to them so a reviewer can see the whole shape without reading every file first.

## What This Project Is

Ledger is a privacy-first community archive for queer history and mutual-aid knowledge. It combines preservation packaging, fixity checks, consent-based access, selective disclosure, and community governance.

Package metadata checked in this pass:

- Python package `ledger-archive` for Python `>=3.12`.

## Who It Serves

- Community archivists and mutual-aid organizers preserving sensitive records.
- Contributors who need control over what is public, community-only, or sealed.
- Maintainers building digital preservation tools where safety is part of the archive model.

## What It Covers

- Content-addressed ingest, BagIt packaging, PREMIS and Dublin Core metadata, and fixity checks.
- Access grants, disclosure policies, redaction, consent, and contributor identity separation.
- Replication, review, server, browse, and API surfaces.
- Docs for architecture, threat model, governance, adoption, accessibility, ADRs, and audits.
- Tests for access, ingest, bagging, consent, review, and server behavior.

## How It Is Put Together

- src/ledger/ contains ingest, bags, access, consent, dual control, review, server, and preservation logic.
- docs/ contains architecture, governance, audits, adoption docs, and ADRs.
- examples/ and tests/ provide small records and policy cases.
- The Makefile and workflows run local and CI checks.
- Security docs explain the pre-1.0 audit posture.

Observed source and operations surfaces:

- `Makefile`
- `infra/`
- `pyproject.toml`
- `src/`
- `tools/`
- `web/`

GitHub workflow files checked:

- `.github/workflows/ci.yml`
- `.github/workflows/codeql.yml`
- `.github/workflows/standards.yml`

## Trust Boundaries

- Contributor identity is treated as a separate, grant-gated secret rather than ordinary metadata.
- Fixity is checked with manifests and preservation events, not assumed.
- Community governance and consent rules are part of the product surface, not afterthought docs.

## Outside This Scope

- It has not had an independent security or cryptography audit.
- High-stakes archives still need a threat-model review by the community using it.
- It is not a hosted platform and does not remove the need for human stewardship.

## Docs And Evidence Checked

This pass checked 45 hand-authored doc or metadata files, 61 test files, and 3 workflow files on `main`. The count excludes vendored provider licenses, dependency folders, generated cache files, and large generated artifact history.

Primary docs checked:

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
- `docs/GOVERNANCE.md`
- `docs/I18N.md`
- `docs/MUTUAL-AID.md`
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

Representative test files checked:

- `tests/__init__.py`
- `tests/conftest.py`
- `tests/fixtures/README.md`
- `tests/fixtures/community.txt`
- `tests/fixtures/public.txt`
- `tests/fixtures/sealed_conditional.txt`
- `tests/fixtures/sealed_until.txt`
- `tests/fixtures/stewards.txt`
- `tests/test_accessibility_check.py`
- `tests/test_attest.py`
- `tests/test_audit_log.py`
- `tests/test_backup_restore.py`
- `tests/test_bag.py`
- `tests/test_browse_compose.py`
- `tests/test_cas.py`
- `tests/test_cli.py`
- `tests/test_config.py`
- `tests/test_consent.py`
- `tests/test_consent_status.py`
- `tests/test_contribute.py`
- `tests/test_deposit_bridge.py`
- `tests/test_disclosure_workflow.py`
- `tests/test_dualcontrol.py`
- `tests/test_edit.py`
- `tests/test_export.py`
- `tests/test_export_drive.py`
- `tests/test_feed.py`
- `tests/test_fixity.py`
- `tests/test_i18n.py`
- `tests/test_identity.py`
- `tests/test_ingest_concurrent.py`
- `tests/test_ingest_e2e.py`
- `tests/test_language_switch.py`
- `tests/test_metadata.py`
- `tests/test_no_outing.py`
- `tests/test_oai.py`
- `tests/test_object.py`
- `tests/test_oralhistory.py`
- `tests/test_overview.py`
- `tests/test_pagination.py`
- `tests/test_policy.py`
- `tests/test_preservation.py`
- `tests/test_print_edition.py`
- `tests/test_reading_room_enforcement.py`
- `tests/test_record_facets.py`
- Plus 16 more test files.

## Validation Notes

For this docs PR, validation means the scope file was generated from the clean `origin/main` worktree, reviewed against repo metadata and docs inventory, and checked with `git diff --check`. Project test suites are still the authority for code behavior, because this PR changes documentation only.
