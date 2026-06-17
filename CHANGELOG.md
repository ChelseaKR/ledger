# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] — 2026-06-16

First reference implementation. A small collective can install ledger, self-host it
on one inexpensive box with no cloud account, and run the full preservation +
selective-disclosure cycle.

### Added

- **Preservation core.** Content-addressed store (`cas`) with dual-algorithm
  fixity (`fixity`, SHA-256 + BLAKE2b); deterministic, byte-reproducible BagIt bags
  (`bag`, RFC 8493); PREMIS event log and Dublin Core description (`metadata`);
  OAIS SIP → AIP → DIP packaging (`oais`).
- **Disclosure core.** A single access-decision point (`access.policy.disclose`),
  deny-by-default across five policy levels; least-privilege grants (`access.grants`);
  redaction as a recorded, auditable transform (`access.redaction`).
- **Contributor-identity vault.** Separated, authenticated-encrypted store keyed by
  an opaque, content-independent token (`identity`); identity resolves only under an
  explicit unseal grant.
- **Replication** with verify-on-arrival and quarantine-and-heal (`replicate`).
- **Accountable moderation**: content warnings as structured metadata, consent
  changes, takedowns, and an appeal path, all recorded (`moderate`).
- **Accessible surfaces.** A framework-free, stdlib browse/search/API server
  (`server`) targeting WCAG 2.2 AA with a list/table non-visual equivalent and
  content-warning interstitials; a CLI (`cli`); a scripted end-to-end demo (`demo`).
- **Audit-as-artifact.** A no-outing audit suite with sentinel identities; an
  accessibility checker (`accessibility_check`) wired into CI; a generated VPAT 2.5
  Accessibility Conformance Report (`acr_gen`).
- **Project scaffolding.** AGPL-3.0 license, independence NOTICE, threat model,
  governance model, ADRs, Docker/Compose self-host infra, and a CI gate covering
  lint, strict typing, tests, the no-outing safety check, accessibility, CodeQL,
  `pip-audit`, and secret scanning.

[Unreleased]: https://github.com/ChelseaKR/ledger/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/ChelseaKR/ledger/releases/tag/v0.1.0
