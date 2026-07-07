# Responsible-Tech Audits — ledger

> **Last verified: 2026-07-05 · Recheck cadence: per release**, matching
> `docs/ROADMAP.md`'s own cadence note (DOC-15). Re-date a section when its content
> is substantively re-reviewed, not on every unrelated repo edit.

Instantiates the portfolio's private `RESPONSIBLE-TECH-FRAMEWORK.md` (fetched at CI
time, never committed here) for ledger. Read this alongside
[`docs/THREAT-MODEL.md`](THREAT-MODEL.md) (the adversary-by-adversary detail this
file summarizes) and [`docs/GOVERNANCE.md`](GOVERNANCE.md) (who decides what).
Applicability decisions and the N/A for AI-Evaluation live in
[`docs/adr/0006-standards-applicability.md`](adr/0006-standards-applicability.md).

---

## A. Ethics & responsibility

- **Worst misuse:** an archive built to protect vulnerable contributors, used
  instead to *identify* them — a hostile steward, a subpoena, a doxxer, or a seized
  device turned into a tool that outs someone who is not out, undocumented, or
  organizing somewhere that is dangerous. This is the single worst-case ledger is
  designed around; it is not hypothetical for the communities this targets.
- **Mitigations:** identity separated from the record in an encrypted vault,
  grant-gated (ADR 0003); no view, export, log line, filename, or error surfaces
  authorship without an explicit grant; default-sealed records and fields; a
  malicious-steward case is explicitly modeled (`docs/THREAT-MODEL.md` §4.4) with
  the append-only moderation log and PREMIS event chain as the accountability
  backstop.
- **"Works as intended" harm:** even a fully consenting, correctly-configured
  archive can out someone if a downstream consumer of a *public* record (a partner
  organization, a researcher, an aggregator) cross-references it against other
  public data the archive does not control — an inference risk, not a bug, stated
  honestly in `docs/THREAT-MODEL.md` §4.7 rather than claimed away.
- **Non-goals:** no feature that surfaces "who contributed this" without a grant,
  ever; no analytics or engagement optimization; no government or institutional
  customer relationship (stated in the README and `NOTICE`).
- **Kill-switch:** stop the process; the archive is plain BagIt files a community
  can keep reading with other tools even if ledger itself is abandoned (ADR
  0004/0005) — there is no hosted dependency to fail closed *or* open.
- **Accountable owner:** Chelsea Kelly-Reif (RTF-01).
- **Signed off:** 2026-07-05 — Chelsea Kelly-Reif.
- **Gap:** this section, `docs/THREAT-MODEL.md`, and `docs/GOVERNANCE.md` are the
  substantive ethics review; a distinct, committed, dated `docs/audits/` artifact
  (RTF-01's letter) does not exist yet — tracked in
  [`docs/ROADMAP.md`](ROADMAP.md#open-conformance-gaps).

## B. Bias & fairness

- **Segments:** contributors, subjects of records, viewers at different grant
  levels (anonymous / community / steward), and stewards themselves. Bias risk here
  is less "model bias" (there is no model, see AI-Evaluation N/A) and more
  **whose histories get preserved and whose get sealed by default** — a moderation
  or steward decision, not an algorithm.
- **Risks:** a moderation workflow that is inconsistently applied across
  submissions could systematically under-preserve some communities' material or
  over-seal it relative to others; a content-warning taxonomy that reflects one
  community's norms poorly for another.
- **Tests:** the moderation workflow (`moderate.py`) records who acted and why with
  an appeal path (`docs/GOVERNANCE.md`), making a pattern of inconsistent decisions
  reviewable after the fact. There is no automated fairness test today because
  there is no automated *decision* — every disclosure/moderation call is a human
  steward action logged for accountability.
- **Commitment:** default to the most protective policy, never the most convenient
  one; moderation decisions are appealable and logged, not final and silent.
- **Gap:** no dated, committed bias / representational-harm review artifact (content-
  warning taxonomy audit, moderation-pattern review) exists yet — tracked in
  [`docs/ROADMAP.md`](ROADMAP.md#open-conformance-gaps) (RTF-03).
- **Signed off:** 2026-07-05 — Chelsea Kelly-Reif (substance reviewed; dated artifact owed).

## C. Privacy & data-protection

- **Data inventory:** contributor identity (name/contact/any identifying detail),
  record content and its descriptive/preservation metadata (Dublin Core, PREMIS),
  access-policy state per record/field, grant tokens, consent-request history, and
  moderation/takedown events. See `docs/THREAT-MODEL.md` §1 for the full asset
  list and §2 for trust boundaries.
- **Lawful basis / consent model:** consent-based and revocable by design — a
  contributor decides what is public, community, steward-only, or sealed, and can
  tighten access or request takedown at any time; the system honors it across
  replicas and records the change as a PREMIS event (README "Hard rules" §4). This
  is largely descriptive of a mechanism that already exists, which is exactly why a
  DPIA is inexpensive to write and correspondingly overdue.
- **Handling:** self-hosted, no hosted-service dependency; identity lives only in
  the encrypted vault, never inline with the record; redaction is a first-class,
  logged transform, and the unredacted original stays access-controlled.
- **Retention & takedown propagation:** a takedown is a PREMIS event that must
  propagate across every configured replica, not just the primary copy — this is
  read-path-independent of *how many* copies exist (`replicate.py` re-verifies on
  arrival). Data-subject rights (access, correction, erasure) are mechanically
  supported (a contributor's own consent actions) but not yet inventoried against a
  specific rights-request SLA.
- **Commitment:** no telemetry, no analytics, no third-party data sharing; the only
  network egress is what a deployer explicitly configures (replication targets).
- **Gap — the highest-priority open item in this repo:** ledger is an ASVS L2, PII-
  handling archive whose entire purpose is sensitive personal data, and it has **no
  DPIA** as a distinct, dated artifact (RTF-04). `docs/THREAT-MODEL.md` covers
  adversarial exposure thoroughly; a DPIA (lawful basis, data-subject rights,
  retention schedule, cross-replica takedown propagation, residual risk from a
  data-protection rather than an adversary lens) is different work and is tracked
  as the top item in [`docs/ROADMAP.md`](ROADMAP.md#open-conformance-gaps).
- **Signed off:** 2026-07-05 — Chelsea Kelly-Reif (mechanism reviewed; DPIA owed).

## D. Transparency & explainability

- **In the product:** access state and content warnings are shown in plain text
  before a record renders, never color/icon-only; a withheld record states
  honestly that something is sealed and, where applicable, when it opens
  (`docs/adr/0007-withhold-not-403.md`) rather than silently vanishing.
- **In the docs:** `docs/THREAT-MODEL.md`, `docs/GOVERNANCE.md`, and this file are
  the transparency surface for how the system behaves under adversarial and
  ordinary conditions; `Last verified` currency stamps mark which are current
  (DOC-15).
- **Commitment:** no dark patterns; every disclosure decision is attributable to a
  policy and a grant, and that chain is inspectable (`access/` is the single
  disclosure-decision point every read path calls through).
- **Signed off:** 2026-07-05 — Chelsea Kelly-Reif.

## E. Accessibility (WCAG 2.2 AA)

- **Surface:** the archive browse/search server-rendered HTML surface (`server.py`,
  `render.py`, `web/`) — landmarks, single-`h1` heading order, `label for`
  coverage, `<html lang>`, alt text, and design-token contrast are enforced by a
  merge-blocking structural gate (`src/ledger/accessibility_check.py`).
- **Status:** committed, dated, candid VPAT 2.5 ACR at `docs/accessibility/ACR.md`
  (46 Supports / 6 Partially Supports / 21 N/A) and prose commitment in
  `docs/ACCESSIBILITY.md`. Full detail and the tracked tool-conformance gaps (axe/
  Lighthouse/pa11y/Playwright, dated screen-reader and keyboard walkthroughs) are
  in the README's Standards Conformance table and
  [`docs/ROADMAP.md`](ROADMAP.md#open-conformance-gaps).
- **Signed off:** 2026-07-05 — Chelsea Kelly-Reif.

## F. Security

- **Threat model:** `docs/THREAT-MODEL.md` §4 covers seven adversary cases (device
  seizure, subpoena/legal compulsion, doxxing, a malicious or compromised steward, a
  hostile replica host, network surveillance, and inference attacks), each with the
  guarantee, the mechanism, and the residual risk stated plainly — including where
  ledger cannot defend (§5, "out of scope, stated honestly").
- **ASVS level:** **L2** — ledger touches PII/identity (the contributor-identity
  vault and every record's disclosure state), per SECURITY-AND-SUPPLY-CHAIN-STANDARD
  §1. Function- and object-level authorization are enforced server-side at one
  choke point (`access/policy.py`) with cross-principal integration tests; the
  design departs from a literal ASVS V4 `403` in favor of withhold-and-acknowledge
  (anti-enumeration), documented and justified in
  `docs/adr/0007-withhold-not-403.md`.
- **Container scanning:** not yet enabled. `infra/Dockerfile` exists; base image is
  pinned by tag, not digest; no Trivy/Grype job runs against it. Tracked in
  [`docs/ROADMAP.md`](ROADMAP.md#open-conformance-gaps) (SEC-28).
- **SBOM + signing:** not yet enabled — ledger **is** a release-producing repo
  (PyPI, `ledger-archive`, mandatory per RELEASE-AND-VERSIONING-STANDARD §1), so
  this is not `N/A`; it is the repo's largest tracked gap (no release workflow has
  shipped at all yet). Tracked in `docs/ROADMAP.md` (REL-13..20, SEC-27/29).
- **Secret-management policy:** secrets (the identity-vault key, any deploy
  credential) are supplied via environment variable or an external keystore, never
  committed; `.gitleaks.toml` allowlists only literal placeholder test fixtures by
  exact value, scoped narrowly enough that it cannot mask a real credential.
  Rotation/revocation is deployer-operated (self-hosted, no central ledger-run
  service to rotate centrally) and is documented for operators in
  `docs/ACCESSIBILITY.md`'s sibling ops docs and `infra/`. Reviewed at each release
  once releases exist; reviewed today at this audit's date.
- **VEX:** none needed — `pip-audit` reports zero known vulnerabilities in the
  resolved dependency set as of this audit (2026-07-05); there is currently no
  unfixable HIGH/CRITICAL finding to document an exception for.
- **Residual-risk register:** the per-adversary residual risk is stated inline in
  `docs/THREAT-MODEL.md` §4 (e.g., "a steward who never uses dual-control and is
  never audited can still act quietly for a time" for the malicious-steward case);
  a *distinct*, regenerated-per-release register extracted from those paragraphs
  with owner + date does not exist yet as its own artifact. Tracked in
  `docs/ROADMAP.md` (RTF-06, QM-09).
- **Signed off:** 2026-07-05 — Chelsea Kelly-Reif.

---

## Committed artifacts

- [`docs/THREAT-MODEL.md`](THREAT-MODEL.md) — adversary-by-adversary threat model with residual risk stated per case (dated 2026-07-05)
- [`docs/accessibility/ACR.md`](accessibility/ACR.md) — VPAT 2.5 ACR, regenerable via `make acr` (dated 2026-07-05)
- [`docs/GOVERNANCE.md`](GOVERNANCE.md) — stewardship, moderation, and dispute-resolution process (dated 2026-07-05)
- [`docs/adr/0006-standards-applicability.md`](adr/0006-standards-applicability.md), [`docs/adr/0007-withhold-not-403.md`](adr/0007-withhold-not-403.md) — the two decisions this audit required
- **Not yet created** (tracked in [`docs/ROADMAP.md`](ROADMAP.md#open-conformance-gaps)): `docs/audits/dpia.md`, `docs/audits/bias-representational-harm.md`, `docs/audits/ethics-consequence-scan.md`, `docs/audits/residual-risk-register.md`

No LLM or model inference exists anywhere in ledger (ingest, fixity, access policy,
and disclosure are deterministic), so AI-Evaluation is **N/A** — see
`docs/adr/0006-standards-applicability.md` for the reason and the re-trigger
condition (AIEV-01: any future LLM SDK import flips this to Applies before that
feature merges).
