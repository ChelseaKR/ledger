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
> section, with a matching signed annotated git tag, once the first `vX.Y.Z` tag is
> actually pushed through the release workflow added below.

### Added
- `ledger ingest --description ...` sets a Dublin Core description at authoring
  time, and the ingest CLI now nudges (non-blocking) when a record has no
  description, alongside the existing missing-transcript advisory — moving the
  ACR 504 authoring-tool support forward (RM8).

- **Public evaluation path for the first release candidate (2026-07-18).** The
  README now begins with a five-minute, synthetic-data-only walkthrough instead of
  making a prospective adopter infer the first useful command from the architecture
  description. `docs/TRY-LEDGER.md` explains exactly what the executable demo proves
  and what it does not. `docs/reviews/` adds bounded packets for a community
  archivist pilot, an independent threat-model review, and a manual
  assistive-technology review; none represent completed human review. The new
  `docs/RELEASE-0.1.0.md` checklist separates repository-verifiable work from the
  owner-controlled PyPI and human-review prerequisites before a real `v0.1.0` tag.

- **Portfolio-standards conformance remediation (2026-07-11).** Closed all five
  current Tier-1 checker failures with a Python runtime pin, complete CFF metadata,
  canonical README applicability declarations, ADR 0000, and a discoverable
  packaged-catalog marker. Added OpenSSF Scorecard, incident-response conventions
  and GitHub labels, L3 data-governance/data-card documentation, and a dated
  residual-risk register. Enabled GitHub private vulnerability reporting and
  converted every remaining human/account-setting conformance blocker into a
  linked issue rather than an untracked roadmap assertion.

- **DORA delivery-health review + root `DEFINITION_OF_DONE.md` (2026-07-07).**
  `docs/DORA-DELIVERY-HEALTH-REVIEW.md` instantiates QM-11: Deployment Frequency and
  Change Lead Time computed from real merged-PR history (`gh pr list`), with Change
  Fail Rate, Failed-Deployment Recovery Time, and Deployment Rework Rate recorded
  explicitly N/A pending the tag-triggered release workflow (REL-08) that gives them
  something to measure, rather than filled in with invented numbers. Root
  `DEFINITION_OF_DONE.md` instantiates QM-18, tracing every AUTO/REVIEW/RELEASE-GATE
  item to what `ci.yml`/`Makefile` actually enforce today and to the `docs/ROADMAP.md`
  row tracking what doesn't exist yet.
- **Tag-triggered release workflow (`.github/workflows/release.yml`).** Pushing a
  `vX.Y.Z` tag now re-runs the full lint/type/test gate against the tagged commit,
  builds the sdist/wheel, fails closed if the tag doesn't match `pyproject.toml`'s
  version, generates a CycloneDX SBOM of the shipped dependency closure, records
  GitHub-native SLSA build-provenance and SBOM attestations, cosign-signs (keyless)
  every artifact, publishes to PyPI via Trusted Publishing (OIDC — no stored API
  token), and mirrors sdist/wheel/SBOM/signatures onto a GitHub Release. Registering
  the PyPI Trusted Publisher and the `pypi` GitHub Environment remains a one-time
  manual step for the project owner (documented in the workflow header); every other
  stage runs with no additional setup.
- **Hardened release gates (REL-08/10/14/16).** The release workflow's verify job
  now asserts the pushed tag is a *signed annotated* tag (a lightweight or unsigned
  tag fails closed; signature presence is checked — pinning the signer's identity
  awaits a committed allowed-signers file, tracked in `docs/ROADMAP.md`), requires a
  matching `## [X.Y.Z]` section in this file before anything builds, and runs the
  complete `make verify` merge gate (lint, type, test, i18n, accessibility,
  pip-audit, secret-scan, claims) from the locked dependency graph instead of a
  hand-picked subset. After publishing, a new `verify-published` job downloads every
  file PyPI serves for the version and fails the release unless each is sha256-identical
  to what this run built; the GitHub Release only publishes after that check passes.
- **Concurrency-safe workflow stores (FIX-05).** `ledger._filelock.file_lock`, a tiny
  single-host advisory lock (`fcntl.flock` on a sibling `.lock` file, no-op on
  non-POSIX), now guards the whole read-modify-write critical section of
  `ConsentRequestStore`, `SubjectTokenStore`, `SubmissionQueue`, and `ProposalStore`. Under the threaded
  browse server, two concurrent POSTs could previously each read the same JSON store,
  append/modify independently, and have the second atomic rename silently clobber the
  first — for consent this could mean a lost *withdrawal* request, the worst failure
  class this project has. `tests/test_filelock.py` hammers each store from many
  threads at once and asserts nothing is lost, plus unit tests of the lock primitive
  itself.
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
- **Takedown tombstones and per-location propagation receipts (FIX-08).** A takedown now
  persists a durable tombstone (`src/ledger/tombstones.py`, `logs/tombstones.json`)
  recording that an opaque record id was removed and which storage locations have
  confirmed it. `Archive.remove_all_copies` marks the primary store and every reachable
  replica confirmed; a mirror that was offline at takedown time is left pending. When it
  reattaches, the replication sweep (`replicate.apply_tombstones`, invoked from
  `verify_replicas`/`heal`) deletes the stale copy, writes a per-location PREMIS
  `TAKEDOWN` receipt to `logs/takedowns.premis.json`, and confirms the location — and
  `heal` refuses to re-copy a tombstoned bag back, so a removal can never be silently
  undone. `/consent-status` now reports honest per-location completion ("2 of 3 confirmed;
  mirror-c pending", localized EN/ES) and never overstates it. Tombstones hold only opaque
  ids, an action, and location names — never a title, field, or identity (no-outing).
- **Advisory mutation testing on the safety-critical core (CQ-47).** `make mutation`
  (mutmut, its own `.[mutation]` extra so the audited dependency surface is unchanged)
  scoped to `access/`, `identity.py`, and `fixity.py`, reusing the `disclosure`/
  `preservation` pytest markers as its kill oracle. Never a merge gate — advisory only,
  run weekly and on demand via `.github/workflows/mutation.yml`. The first run found
  `access/grants.py`'s `load_grants` (the function that reads subject → grant mappings
  from an on-disk JSON file) had zero existing tests; `tests/test_grants_load.py` closes
  that gap, raising `grants.py` from 55% to 91.3% mutation score. See
  `docs/MUTATION-TESTING.md` for the full baseline and how to read survivors.
- **Performance budgets in CI (QM-02).** A new `perf` CI job (`tools/perf_budget.py`,
  `make perf`) runs on every push and PR, asserting a time budget over the
  operations a steward actually waits on — content-addressed store put/get,
  streaming dual-algorithm fixity hashing, a full ingest, and a browse listing.
  Budgets are set with wide headroom over a locally-measured median so ordinary
  CI runner noise doesn't fail the build; a failure means a real, order-of-
  magnitude regression (e.g. an accidental linear scan or a dropped streaming
  read). Closes `docs/ROADMAP.md` QM-02.
- **Offline redaction assistant (EXP-07).** `ledger.redact_suggest` is a fully local,
  regex/wordlist detector for likely names, addresses, phone numbers, emails, handles,
  and dates. It runs over a contributor's account text on the contribute-form preview
  (`contribute.render_redaction_suggestions`) and from `ledger redact-suggest --file`,
  and only ever *suggests* — it never edits, drops, or applies anything. A steward or
  contributor who wants a flagged detail hidden still uses the existing per-field
  sealing (`ledger seal`/`ledger redact`) or edits their own text. No network call, no
  subprocess, no model download; recall on a small synthetic corpus is measured and
  asserted in-repo (`tests/test_redact_suggest.py`), and every surface carries the
  honest caveat that this finds *some* identifying detail, not all of it — addressing
  the residual self-disclosure risk noted in the threat model (§4.3).
- **Captions/transcripts with real segment/timing structure (RM6).** `ledger ingest
  --captions filename=path.vtt|.srt` parses an *already-transcribed* WebVTT (W3C) or
  SRT caption file into structured `TranscriptCue` segments (start, end, text, and a
  speaker label where the source format names one — WebVTT's `<v>` voice span; SRT has
  no standardized speaker syntax) and stores them on the payload alongside the existing
  flat `transcript` field, which is auto-backfilled from the cues so every existing
  plain-text consumer (search, the H3 transcript render, export) keeps working
  unchanged. The record page renders the structured cues as an ordered list of timed
  segments. Cues are disclosed under the *same* payload-level policy as everything
  else about the file — there is no separate, weaker disclosure path and no
  per-cue/per-segment consent policy (that granularity question is open; see
  `ledger.models.TranscriptCue`'s docstring). This is caption-file *ingest* only:
  ledger performs no speech-to-text.
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

### Fixed

- **Missing production stylesheet (2026-07-12).** Package `web/static/app.css`
  inside wheel and container installs so `/static/app.css` no longer returns 404
  when the server runs outside a source checkout.
- **Cloud-init secret tracing (SEV2, 2026-07-12).** Removed shell xtrace from
  AWS first-boot provisioning after the initial synthetic demo deploy revealed
  that expanded secret assignments reached the IAM-restricted EC2 console log.
  Both demo credentials were rotated, the synthetic archive was rebuilt, and a
  regression test now forbids xtrace in the secret-bearing template. See
  incident [#86](https://github.com/ChelseaKR/ledger/issues/86) and the committed
  postmortem under `docs/incidents/`.

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
