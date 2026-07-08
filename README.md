# ledger — a privacy-first community archive for queer histories and mutual-aid knowledge

> A digital-preservation tool that lets a queer community keep its own records — oral histories,
> zines, protest ephemera, mutual-aid runbooks, the knowledge that gets lost when an organizer
> moves or a group folds — in content-addressed, checksummed, replicated storage with real
> preservation metadata. Access is consent-based and selective: a contributor decides what is
> public, what is community-only, and what stays sealed, and the system is built so that holding a
> record can never out the person who made it. Open standards throughout. Governed by the community
> that uses it, not by a platform or a funder.

**Status:** Beta (pre-1.0 reference implementation) · independent personal open-source project ·
AGPL-3.0 · unaffiliated with any employer or client; contains no proprietary or client material.
Built for and with community archivists and mutual-aid organizers, not for a government or
institutional customer.

> **Maturity & safety.** This is a **pre-1.0 reference implementation.** The confidentiality
> guarantees described below (no-outing, sealed disclosure, fixity) are enforced by merge-blocking
> tests, but ledger has **not yet had an independent security or cryptography audit.** Evaluate it
> against your own threat model before entrusting real, high-stakes records — see
> [`SECURITY.md`](./SECURITY.md) and [`docs/THREAT-MODEL.md`](./docs/THREAT-MODEL.md).

**Why this domain.** Queer history is disproportionately undocumented, and the documentation that
exists is fragile. It lives on a single laptop, a dead Facebook group, a hosted service that changes
its terms, a shoebox under someone's bed. The people best placed to keep it are often the people
least able to trust an institution with it, because the same record that preserves a life can also
expose someone who is not out, who is undocumented, who organizes in a place where any of that is
dangerous. The standard digital-preservation playbook (OAIS, BagIt, PREMIS, Dublin Core) is sound
and worth using. What it usually lacks is a threat model where the contributor's safety is a
first-class preservation requirement, equal to bit-integrity. ledger treats it that way.

---

## What it does

- **Ingests** a record (audio, image, PDF, video, text, or a folder of them) into a content-addressed
  store, computes fixity, writes a BagIt bag, and generates preservation metadata (PREMIS events and
  fixity) and descriptive metadata (Dublin Core), so the item is documented the moment it lands.
- **Replicates** each bag across the storage locations a community chooses — a member's drive, a
  community server, an off-site mirror — and re-verifies fixity on a schedule, so no single failure
  or seizure loses the record.
- **Discloses selectively.** Every record and every field carries an access policy: public,
  community-only, restricted to named stewards, or sealed until a date or a condition. A contributor
  can publish a story while keeping the names, the location, and their own identity sealed. The
  browse and search surfaces only show a viewer what their grant allows.
- **Protects contributors.** Redaction is a built-in step, not an afterthought; minimal-disclosure
  defaults mean a record reveals as little as it can while still being useful; and there is no
  feature anywhere that surfaces "who contributed this" to someone without an explicit grant.
- **Warns and moderates.** Content warnings are structured metadata shown before a record renders;
  community moderation is a documented, accountable workflow, not a single admin's discretion.
- **Stays usable by humans.** The archive browse is a real, accessible interface with an equivalent
  list and table view, not a developer's S3 bucket.

## Hard rules (enforced, not aspirational)

1. **Holding a record never outs its contributor.** Contributor identity is stored separately from
   the record, encrypted, and disclosed only by an explicit grant the contributor controls. No view,
   export, log line, filename, or error message reveals authorship to an unauthorized viewer. This is
   tested with sentinel identities injected at ingest and asserted absent from every public surface.
2. **Default to the narrowest disclosure.** New records default to sealed, not public. Fields default
   to the most restrictive policy that still lets the record exist. Opening a record up is a
   deliberate act with a confirmation; nothing is ever published by inaction.
3. **Fixity is checked, not assumed.** Every bag is verified against its manifest on ingest, on every
   replication, and on a recurring schedule. A checksum mismatch raises a documented preservation
   event and quarantines the copy; it is never silently overwritten by a "good" replica.
4. **Consent is revocable and recorded.** A contributor can tighten access or request takedown; the
   system honors it across replicas and records the change as a PREMIS event. Consent state travels
   with the record, so a downstream mirror cannot lawfully ignore it.
5. **Open standards, no lock-in.** Storage is content-addressed and the package is a plain BagIt bag
   with sidecar PREMIS/Dublin Core. A community can walk away with its archive and read it with other
   tools. ledger is the steward, not the owner.

---

## Architecture

```
ledger/
├── README.md
├── src/ledger/
│   ├── ingest.py                # accept item → fixity → BagIt bag → PREMIS/DC metadata → store
│   ├── cas.py                   # content-addressed store (BLAKE2b/SHA-256 addressing, dedupe)
│   ├── bag.py                   # BagIt write/validate; manifest + tagmanifest; bag-info
│   ├── fixity.py                # checksum compute/verify; scheduled audit; quarantine on mismatch
│   ├── metadata/                # premis.py (events, agents, fixity), dublincore.py, schema/
│   ├── access/                  # policy model, grants, selective disclosure, redaction.py
│   ├── identity.py              # contributor identity vault: separated, encrypted, grant-gated
│   ├── replicate.py             # push/pull bags to configured locations; re-verify on arrival
│   ├── moderate.py              # content-warning model + accountable moderation workflow
│   ├── oais.py                  # SIP → AIP → DIP packaging per the OAIS reference model
│   ├── server.py                # accessible archive browse/search + JSON API (read-gated)
│   └── config.py                # storage locations, policies, prompts as versioned files
├── web/                         # framework-free WCAG 2.2 AA archive UI (browse + list/table view)
├── infra/                       # optional self-host deploy (compose/Terraform); no hosted dependency
├── tests/
│   └── fixtures/                # tiny sample records (consented, synthetic) + one per access policy
└── docs/                        # ARCHITECTURE, THREAT-MODEL, GOVERNANCE, ACCESSIBILITY, ADRs,
                                  # ROADMAP (conformance gap tracker), accessibility/ (ACR)
```

Records move through the OAIS pipeline as Submission, Archival, and Dissemination Information
Packages. Ingest produces a SIP, normalizes it into an AIP (a BagIt bag with PREMIS preservation
metadata and Dublin Core description), and the browse surface serves a DIP shaped to the viewer's
access grant. The content-addressed store gives deduplication and tamper-evidence for free: a record
is named by its hash, so a changed byte is a different address and fixity drift is detectable rather
than silent. Contributor identity lives in a separate, encrypted vault keyed by grant, never inline
with the record, so the preservation copy and the safety boundary are structurally distinct. Access
policy is evaluated at the edge of every read path, so a record's visibility is a property of the
data, not of which page happened to query it.

## The preservation and disclosure engine (the actual product)

The core of ledger is the join between two things most tools keep apart: rigorous preservation and
contributor safety. A record is only as preserved as its fixity guarantees, and only as safe as its
disclosure model. ledger makes both explicit and tests both.

**Preservation.** Each AIP is a BagIt bag: a payload directory, `manifest-sha256.txt` and
`manifest-blake2b.txt`, a `tagmanifest`, and `bag-info.txt` carrying source organization, dates, and
external-identifier metadata. PREMIS records the events (ingest, fixity check, replication,
redaction, access-policy change, takedown) with the agent and outcome of each, so the chain of
custody is auditable. Dublin Core describes the record for discovery. Fixity is verified on a
schedule across every replica; a mismatch raises a `fixity-failure` PREMIS event, quarantines the
bad copy, and heals from a verified replica rather than trusting the divergent one.

**Disclosure.** Every record and field carries an access policy drawn from a small, documented set
(`public`, `community`, `stewards`, `sealed-until`, `sealed-conditional`). A grant maps a viewer to
what they may see. Selective disclosure means a single record can be simultaneously a public story, a
community-only audio track, and a sealed set of names. Redaction is a first-class transform with its
own PREMIS event, and the unredacted original is itself access-controlled. The disclosure decision is
made in one place, `access/`, and every read path goes through it, so there is no surface that can
accidentally leak a sealed field.

Both halves are exercised by an audit suite: fixture records with known policies and injected
sentinel identities, asserting that fixity drift is caught and quarantined, that sealed fields never
render to an ungranted viewer, and that contributor identity never appears in any public output,
log, filename, or error.

---

## Quality attributes (engineered for, not assumed)

This section works through the full system-quality-attribute list and ties each to a concrete
decision. Grouped for readability; every attribute is named. A safety-sensitive archive lives or dies
on confidentiality, integrity, and durability, so those clusters carry weight.

### Safety, confidentiality, consent
**Safety** — the design property is that holding a record cannot out its contributor; identity is
vaulted, separated, and grant-gated, and the no-outing rule is tested with sentinels. **Confidentiality**
— sealed records and fields never render without a grant; the identity vault is encrypted at rest.
**Securability** — secrets via env or a keystore, never committed; least-privilege grants; a
tag-triggered release pipeline (`.github/workflows/release.yml`) now cosign-signs and SLSA-attests
every release artifact and publishes to PyPI via Trusted Publishing (OIDC, no stored token) — **no
release has shipped yet** (no git tag cut; the PyPI Trusted Publisher registration is also a one-time
manual step only the project owner can do — tracked in [`docs/ROADMAP.md`](docs/ROADMAP.md)). **Integrity**
(data) — content addressing plus dual-algorithm BagIt manifests make tampering detectable, not
deniable. **Autonomy** — a contributor controls their own disclosure and can revoke; the system
enforces their decision, not a steward's preference. **Vulnerability** management — pip-audit,
gitleaks, CodeQL in CI, blocking with no muted gates; dependency pinning is a range today, a
committed hash-pinned lockfile is tracked in `docs/ROADMAP.md`. **Accountability** and
**auditability** — every preservation and access event is a PREMIS record with agent and outcome;
audit-as-artifact documents committed today are [`docs/THREAT-MODEL.md`](docs/THREAT-MODEL.md), the
[Accessibility Conformance Report](docs/accessibility/ACR.md), and the
[Data Protection Impact Assessment](docs/audits/dpia.md); the remaining `docs/audits/` items (bias
review, ethics-consequence scan, residual-risk register) are tracked in `docs/ROADMAP.md`.

### Preservation integrity, durability, fixity
**Durability** — replicated content-addressed copies across independent locations; no single point of
loss. **Redundancy** — every bag exists in N configured locations, re-verified on arrival. **Reliability**
and **dependability** — scheduled fixity audits catch bit rot before it spreads. **Recoverability** —
a quarantined copy heals from a verified replica; the store rebuilds from bags. **Survivability** — the
archive outlives any one host, drive, or maintainer because the package is plain BagIt anyone can read.
**Stability** — the bag layout and metadata schemas are versioned and stable across releases.
**Correctness** and **accuracy** — fixity is checked against the manifest, not assumed; PREMIS events
reflect what actually happened. **Precision** and **fidelity** — preservation copies are bit-exact;
redaction is recorded so the lossy view is never confused with the original. **Effectiveness** —
measured by the audit suite: corruption caught, sealed fields withheld, contributor identity never
leaked, rather than asserted.

### Standards, interoperability, openness
**Standards compliance** — BagIt, PREMIS, Dublin Core, the OAIS reference model, WCAG 2.2 AA, semver,
conventional commits, SPDX headers. **Interoperability** — bags and sidecar metadata are readable by
other preservation tools; the JSON API is documented. **Interchangeability** — storage backends swap
behind the CAS interface without touching records. **Compatibility** — runs on Linux/macOS, Python
3.12+, and commodity storage. **Composability** and **inspectability** — bags, manifests, and metadata
are plain files a person can open and verify. **Portability** and **distributability** — a community can export the
whole archive, hand a peer a self-contained set of bags, and host it elsewhere; nothing is trapped in
a proprietary format and replicas are distributable by design.

### Access control, disclosure, governance
**Determinability** and **predictability** — the same viewer and grant always resolve to the same
visible record. **Provability** — every disclosure decision is attributable to a policy and a grant in
the log. **Traceability** — a record traces from rendered view → access policy → grant → contributor
consent. **Transparency** — the governance model and moderation workflow are documented, not improvised.
**Credibility** — failures and takedowns are recorded honestly. **Relevance** — browse returns what the
viewer is allowed to see, not a padded list with locked rows hinting at sealed content.

### Usability, learnability, reach
**Accessibility** — WCAG 2.2 AA enforced as a merge gate (axe plus manual screen-reader review); the
map/collection browse has an equivalent list and table view. **Usability** and **convenience** — drop
in a file, answer a short disclosure prompt, done; no preservation jargon required to contribute.
**Learnability**, **familiarity**, **intuitiveness** — the contribute flow reads like filling out a
short form, not configuring a repository. **Understandability** — content warnings and access state are
shown plainly before a record renders. **Interactivity** and **responsiveness** — browse and search
respond quickly over the read-gated API. **Discoverability** — faceted browse over Dublin
Core; a clear "how to contribute" path. **Demonstrability** — `make demo` walks a scripted ingest,
seal, grant, and verified-replica cycle. **Seamlessness** — the same archive reads the same whether
self-hosted on a laptop or a community server. **Localizability** — all interface strings in per-language
bundles; adding a language is one module. **Mobility** and **ubiquity** — mobile-first browse;
contributors and stewards work from a phone. **Convenience** restated where it matters: redaction is one
guided step, not a separate tool.

### Performance, scale, cost
**Efficiency** — content addressing deduplicates identical media; fixity is incremental where possible.
**Scalability** and **elasticity** — the store grows with added locations; read surfaces are stateless
and scale horizontally. **Timeliness** — no automated performance budget exists yet; browse and
verification latency are not currently asserted in CI (tracked in `docs/ROADMAP.md`, P3-5). Until
then, treat responsiveness as observed in `make demo`, not as a tested guarantee. **Affordability** — self-hostable on a single inexpensive box or a member's drive; no hosted dependency,
so a broke collective can still run it. **Process capabilities** and **producibility** — `make verify`
reproduces the full gate; one command builds the artifact.

### Maintainability, evolvability, modularity
**Maintainability**, **modifiability**, **evolvability** — small modules behind interfaces; ruff + mypy
strict. **Extensibility** and **flexibility** — new storage backends and metadata profiles via adapters.
**Adaptability** — point ledger at a different set of storage locations and access policies via config.
**Modularity**, **composability**, **orthogonality** — ingest, store, metadata, access, replicate, and
moderate are independent layers. **Simplicity** — plain files and one disclosure-decision point; no
hidden coupling between preservation and access. **Reusability** — the BagIt/PREMIS packaging and the
fixity auditor are usable on their own. **Analyzability** — typed, documented, with a threat model and
architecture doc. **Configurability**, **customizability**, **tailorability** — one config controls
storage locations, default policies, and warnings. **Upgradability** — pinned deps with a documented
bump path; versioned metadata schemas with migrations.

### Operability, serviceability, sustainability
**Operability** and **manageability** — a steward's runbook (add a location, run an audit, process a
takedown); a health and fixity-status endpoint. **Administrability** — config-over-code; governance is
documented policy, not a hidden admin console. **Observability** — a scrubbed request log (method + status + query-stripped path only)
and identity-free PREMIS events for ingest, replication, and audits, scrubbed of
contributor identity by construction. **Debuggability** —
a record dumps its bag and event history under a steward flag, still access-checked. **Serviceability /
supportability** — issue templates and a redaction-safe bug-capture path that never asks for sealed
content. **Deployability** and **installability** — `pipx install`, a container image, one-command
self-host. **Repairability** — most preservation fixes are re-verify-and-heal, not code changes.
**Agility** — CI smoke suite on every PR. **Autonomy** (operational), **self-sustainability**,
**sustainability** — no paid dependency and a plain-format archive mean a community can keep it running,
and keep the records, without anyone's funding.

### Compatibility, verification, dependability detail
**Failure transparency** and **degradability** — a quarantined replica or an unreachable location is
surfaced as a labeled preservation event, never hidden. **Fault-tolerance**, **resilience**, **robustness**
— one bad replica or corrupt upload never takes down the store or loses a verified copy. **Availability**
— read surfaces are static-friendly and can run scale-to-zero; there is no always-on component a community
must keep paid up. **Repeatability** and **reproducibility** — bagging and metadata generation are
deterministic; identical input yields a byte-identical bag. **Testability**, **inspectability**,
**demonstrability** — fixture records, sentinel identities, golden bags, and `make demo` make both
preservation and the no-outing guarantee visible and verifiable.

---

## Accessibility and Section 508 conformance

ledger targets **WCAG 2.2 Level AA** and conformance with the **Revised Section 508 Standards**
(36 CFR Part 1194), which incorporate WCAG 2.0 A/AA by reference for web content and add the
functional performance criteria of Chapter 3. A community archive is not federal ICT, so 508 is not
legally required here. Building to it anyway is deliberate. Disabled people are part of every community
this serves, as contributors and as readers, and meeting the standard that institutions audit to makes
the archive usable by the widest set of people and gives a partnering library or campus a clean,
public artifact to point at.

- A committed **Accessibility Conformance Report (ACR)** using the **VPAT 2.5 (Rev 508)** template
  lives at `docs/accessibility/ACR.md`, with tables for the WCAG 2.x A/AA success criteria, the
  Revised 508 software (Chapter 5) and support-documentation (Chapter 6) criteria, and the
  **Functional Performance Criteria** (use without vision, with limited vision, without hearing, with
  limited reach and strength, with limited cognition).
- The archive browse, record view, content-warning interstitials, search, and any map pass automated
  checks (axe) **and** manual screen-reader review (NVDA, VoiceOver). Content warnings are programmatic,
  not color- or icon-only; access state is conveyed in text; media has captions or transcripts where
  the source allows and the gap is stated where it does not.
- A **non-visual equivalent** of every browse and map surface is provided as an accessible list and
  table view carrying the same records, facets, and access state, so nothing is reachable only by
  pointing at a map.
- Accessibility is a **merge-blocking CI gate**; a regression fails the build. The ACR is regenerated
  and re-committed on each release, the same audit-as-artifact discipline applied to fixity.

## Governance

ledger is **community-governed**. The repository ships a `docs/GOVERNANCE.md` describing how stewards
are chosen, how moderation and takedown decisions are made and appealed, and how disputes are resolved,
so authority is documented and accountable rather than vested in whoever holds the server. The
moderation workflow in `moderate.py` records who acted and why, with an appeal path, so a content-warning
or takedown decision is reviewable. The threat model in `docs/THREAT-MODEL.md` is written for hostile
contexts: seizure, subpoena, doxxing, and a malicious steward are explicit cases, and the no-outing
guarantee is stated as a requirement the code must meet.

## Standards conformance

This repo references a shared, private portfolio of engineering standards rather than restating
them; they are fetched at CI time (`.github/workflows/standards.yml`, pinned to a released tag) and
never vendored into this repository. Per-repo *values* (measured coverage, ASVS level, ACR rows,
DPIA status) live in [`docs/ROADMAP.md`](docs/ROADMAP.md) and
[`docs/RESPONSIBLE-TECH-AUDITS.md`](docs/RESPONSIBLE-TECH-AUDITS.md), not here. The applicability
reasoning and the one N/A decision below are recorded in
[`docs/adr/0006-standards-applicability.md`](docs/adr/0006-standards-applicability.md).

**Release-producing:** yes — intended as a PyPI package (`ledger-archive`, `pipx install`-able),
pre-1.0. A tag-triggered release workflow now exists (`.github/workflows/release.yml`: build, SBOM,
cosign signing, SLSA provenance, PyPI Trusted Publishing), but **no release has shipped yet** — no
tag has been cut, and the PyPI Trusted Publisher registration is a one-time manual step only the
project owner can do (tracked below).

| Standard | Applies | This repo's posture |
|---|---|---|
| Code Quality | Applies | `ruff` (incl. `C901` complexity, max 10) + `mypy --strict`; branch coverage floor 85% (measured 86%); src layout; CODEOWNERS; Python floor `>=3.12`; hash-locked `uv.lock` + PEP 735 `[dependency-groups]` — [`docs/ROADMAP.md`](docs/ROADMAP.md) |
| Security & Supply Chain | Applies — **ASVS L2** (touches PII/identity) | pip-audit + gitleaks + CodeQL all blocking in CI and in `make verify`, plus Semgrep (`p/ci`) blocking in CI and a weekly full-history TruffleHog scan (`secret-scan-scheduled.yml`, non-blocking), zero muted gates; SHA-pinned Actions with Renovate digest-pinning; `uv.lock` pins the full dependency graph and `uv sync --locked` fails the build on drift. **Gap:** no container scan, Harden-Runner, or SBOM/signing yet — [`docs/ROADMAP.md`](docs/ROADMAP.md) |
| CI/CD | Applies | Single `ci.yml`, least-privilege tokens, SHA-pinned actions, `make verify` now reproduces CI's full required-check set (lint, type, test, i18n, accessibility, audit, secret-scan). **Gap:** no committed branch-protection/ruleset artifact (server-side settings are unverifiable from the repo alone) — ⛔ see `docs/ROADMAP.md`, requires the repo owner |
| Release & Versioning | Applies — **mandatory** (published-library repo) | SemVer intent stated; Keep-a-Changelog `CHANGELOG.md`. Tag-triggered release workflow ships SBOM (CycloneDX), keyless cosign signatures, and GitHub-native SLSA build-provenance + SBOM attestations on every `v*` tag, then publishes to PyPI over Trusted Publishing. **Gap:** the PyPI Trusted Publisher itself still needs one-time manual registration by the project owner, and no tag has been cut yet — the CHANGELOG's `[0.1.0] — 2026-06-16` section describes prepared, not shipped, work — [`docs/ROADMAP.md`](docs/ROADMAP.md) |
| Accessibility | Applies | WCAG 2.2 AA target; merge-blocking structural gate (`ledger.accessibility_check`); committed, dated, candid VPAT 2.5 ACR (`docs/accessibility/ACR.md`, 46 Supports / 6 Partially / 21 N/A). **Gap:** axe-core/Lighthouse/pa11y/Playwright not run in CI yet; no dated screen-reader/keyboard walkthrough artifact — [`docs/ROADMAP.md`](docs/ROADMAP.md) |
| Observability | Applies — **Tier C** (library/CLI) | See `## Observability` below |
| Internationalization | Applies | Full gettext catalog pipeline (EN/ES), five merge-blocking gates (POT-current, BCP-47, key-parity, completeness, `msgfmt --check`) — the repo's strongest standard |
| AI Evaluation | **N/A** | No model inference in any user-facing or decision path (ingest, fixity, access policy, and disclosure are all deterministic). Reason and the re-trigger condition recorded in `docs/adr/0006-standards-applicability.md` |
| Quality & Metrics | Applies | 528 tests green; metrics ledger + conformance gap tracker in `docs/ROADMAP.md`; dated DORA delivery-health review (`docs/DORA-DELIVERY-HEALTH-REVIEW.md`, QM-11) and root `DEFINITION_OF_DONE.md` (QM-18). **Gap:** no performance budgets in CI yet — `docs/ROADMAP.md` |
| Documentation | Applies | This README + ADRs (`docs/adr/`) + `docs/ROADMAP.md` + CHANGELOG + CITATION.cff, kept current; dated currency stamps on THREAT-MODEL/ACCESSIBILITY/GOVERNANCE/ACR |
| Responsible Tech | Applies | The no-outing sentinel suite is this standard's own named exemplar for misuse-resistance testing (RTF-02); threat model with per-adversary residual risk (`docs/THREAT-MODEL.md`); review-ready drafts for the [ethics scan](docs/audits/ethics-consequence-scan.md) and [DPIA](docs/audits/dpia.md). **Gap:** accountable-owner review/sign-off and a bias/representational-harm review remain open in `docs/ROADMAP.md` |

No standard is a bare `N/A` — the one that is (AI Evaluation) carries its reason above and in the
linked ADR. Every "Gap" above is tracked, dated, and owned in
[`docs/ROADMAP.md`](docs/ROADMAP.md#open-conformance-gaps) rather than asserted and left to go
stale; re-verify this table against that tracker at the cadence stated there.

## Observability

**Observability: Tier C — OTel tracing out-of-scope (no network surface beyond the local browse
server). Opt-in `--log-format json` only.** ledger exceeds Tier C in one respect and falls short in
another, stated honestly rather than folded into a blanket claim: it already ships a `/healthz`
endpoint returning JSON with a degraded-503 path (`server.py`), which Tier C does not require; it
does **not** yet ship the opt-in `--log-format json` CLI flag the tier description names (tracked in
`docs/ROADMAP.md`, P3-6) — today's structured-logging story is the standard library `logging` module
with contributor-identity scrubbing enforced by construction and asserted by the no-outing test
suite (OBS-11, unconditional regardless of tier).

## Build plan

- **Phase 1 — preservation core.** Content-addressed store; BagIt write/validate; PREMIS + Dublin
  Core; scheduled fixity audit with quarantine-and-heal; labeled fixtures. Definition of done: one
  command ingests a record into a verified bag and detects an injected corruption.
- **Phase 2 — disclosure and identity.** Access-policy model, grants, selective disclosure, redaction,
  and the separated encrypted identity vault; the no-outing audit suite with sentinels wired into CI.
- **Phase 3 — replication and browse.** Multi-location replication with re-verification; the accessible
  archive browse with list/table equivalent; content warnings and the accountable moderation workflow;
  deployed behind a real URL with a "reference implementation" banner.
- **Phase 4 — generalize and govern.** A config so any community can define its own storage locations,
  default policies, and warnings; `docs/GOVERNANCE.md` finished; an "adopt this for your collective in
  an afternoon" guide.

## Engineering and open-source practices

pytest for every deterministic component (ingest, bagging, fixity, access, redaction); ruff + mypy
strict in CI; reproducible, content-hashed bags and audit runs; `make verify` reproduces the full gate
end to end (lint, type, test, i18n, accessibility, dependency audit, secret scan — CI runs the same
targets). The repo ships **LICENSE (AGPL-3.0)**, **NOTICE** (independence statement: a personal
open-source project, unaffiliated with any employer or client, containing no proprietary or client
material), **CODE_OF_CONDUCT**, **CONTRIBUTING**, **SECURITY**, **GOVERNANCE**, a versioned metadata
schema with a deprecation policy, **semver**, **ADRs**, and audit-as-artifact documents
(`docs/THREAT-MODEL.md`, `docs/accessibility/ACR.md`; a fuller `docs/audits/` set is tracked in
[`docs/ROADMAP.md`](docs/ROADMAP.md)). Conventional commits; pinned GitHub Actions; a tag-triggered
release workflow (`.github/workflows/release.yml`) that builds, SBOMs, cosign-signs, and
SLSA-attests every `v*` release before publishing to PyPI over Trusted Publishing — but **no release
has shipped yet**, since no tag has been cut and the PyPI Trusted Publisher still needs one-time
manual registration (see the Standards conformance table below and `docs/ROADMAP.md` for what's
tracked); Dependabot + Renovate.

**License choice.** **AGPL-3.0** is chosen deliberately over a permissive license. ledger handles
disclosure decisions and contributor safety, and the network-use clause means anyone who runs a
modified ledger as a hosted service for a community must share those modifications. That keeps a
fork from quietly weakening the no-outing guarantee or the consent model behind a SaaS wall while
still serving real users. A privacy- and safety-sensitive tool for vulnerable contributors is exactly
the case AGPL exists for.

## Definition of done

A small collective can `pipx install ledger-archive`, self-host it on one inexpensive box with no cloud
account, ingest an oral history into a verified, replicated BagIt bag with PREMIS and Dublin Core
metadata, seal the contributor's name and identity while publishing the story, confirm via the
committed audit suite that no public surface or log reveals who contributed it, browse the archive
through an accessible interface with a working list/table equivalent, and process a consent change
that propagates across replicas — with the ACR committed and every CI gate green.
