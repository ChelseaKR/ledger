# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

> **Note (2026-07-05):** this project has **not yet shipped a tagged release** —
> `git tag` returns nothing. The `0.1.0` heading below was prepared on 2026-06-16 as
> a release candidate but the tag was never cut, so it is recorded here under
> Unreleased rather than as a released version (a changelog claiming a release the
> repo cannot produce is exactly the kind of unbacked claim this project's own
> conformance audit exists to catch). It will move to a real `## [0.1.0] — YYYY-MM-DD`
> section, with a matching signed annotated git tag, once the release workflow
> (`docs/ROADMAP.md`, tracked as this repo's largest open gap) actually ships it.

### Added

- **Mutual preservation aid: encrypted replica exchange (EXP-15).** A second, opt-in
  transport in `ledger.replicate` for community instances to hold *each other's*
  bags as redundancy without either side trusting the other with plaintext:
  `seal_bag`/`unseal_bag` encrypt a whole bag with a Fernet key that never leaves
  the owning instance ("key stays home"); `replicate_sealed_bag` writes the
  ciphertext blob — never the bag — to a partner `StorageLocation` and verifies it
  landed intact by digest; `attest_sealed_replica`/`verify_sealed_attestation`
  implement the scheduled fixity attestation exchange, letting a partner prove
  which bytes it holds without ever decrypting them; `recover_sealed_bag` is the
  recovery drill, pulling a blob back, decrypting locally, and running the same
  `validate_bag` used by every other replica. Closes the threat-model residual that
  a hostile or compromised replica host can read what it stores. See
  [`docs/MUTUAL-AID.md`](docs/MUTUAL-AID.md) for the operational runbook.
- **Disclosure-policy workflow.** First-class, accountable steward commands to set and
  apply a disclosure policy on an already-archived item, enforced by the core engine and
  honoured by the reading-room:
  - `ledger seal` sets the policy of a single field, a payload, or the record default —
    including a temporal embargo (`--field … --level sealed-until --until <date>`,
    time-gated release), a conditional seal (`--condition`), or an absolute seal. Backed
    by `moderate.set_field_policy` / `moderate.set_payload_policy`, each a recorded,
    non-mutating transform emitting a PREMIS `access-policy change` event.
  - `ledger redact` wires the existing `access.redaction` transform into a workflow:
    it destructively replaces a field value with `[redacted]` or drops a payload from the
    stored manifest, recording a PREMIS `redaction` event that names only the
    field/filename, never the removed value.
  - Both require a rationale (accountability) and persist through the one identity-refusing
    write path, so no policy change can leak a contributor identity or a sealed value.
- **Reading-room enforcement proof.** An end-to-end test that applies an embargo and a
  redaction through the workflow, then drives the live stdlib reading-room over loopback
  and asserts the embargoed, redacted, and sealed-identity sentinels appear on no
  anonymous surface (HTML, JSON record/list APIs, CSV export), while the withholding is
  still acknowledged honestly without exposing the embargo date to outsiders.

### Prepared as 0.1.0 (2026-06-16) — first reference implementation, not yet tagged

A small collective can install ledger, self-host it on one inexpensive box with no
cloud account, and run the full preservation + selective-disclosure cycle. This was
the intended `0.1.0` content; it ships as a real tagged release once the release
workflow lands.

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

[Unreleased]: https://github.com/ChelseaKR/ledger/commits/main
<!-- No v0.1.0 tag exists yet (see the note above), so there is no compare link or
     release link to give until one is actually cut — a placeholder link here would
     be exactly the kind of unbacked claim this changelog is now correcting. -->
