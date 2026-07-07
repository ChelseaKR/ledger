# Expansions (EXP-01 … EXP-15) — 2026-07-01

Three horizons. **H1** deepens the core for today's users; **H2** reaches adjacent
capabilities, audiences, integrations; **H3** is transformative bets. Nothing here
restates EX1–EX12 from [`../RESEARCH-ROADMAP.md`](../RESEARCH-ROADMAP.md); where an
idea extends one, the EX ID is named and the idea goes beyond it.

---

## Horizon 1 — deepen the core

### EXP-01 — Public transparency attestations on `/proof`
**Pitch.** Periodically publish a signed, dated attestation (bag count, fixity
outcomes, log chain heads from FIX-06, software version) so *anyone* — a
contributor, a partner, a rival fork — can verify the archive's health without
trusting the steward. **Impact.** Converts "we audit" into "you can check";
directly serves personas E1/F1 and the credibility the threat model earns. Distinct
from EX8 (a per-deposit bundle for one partner): this is continuous, archive-wide,
and public. **Shape.** Extend `_handle_proof` (`src/ledger/server.py:1753`) to emit
a canonical-JSON attestation signed with a steward-held key (age/ssh-keygen
signature, no new runtime dep if using `ssh-keygen -Y`); `ledger attest-health`
cron target; verification doc a third party can follow. **Effort:** M.
**Risks/deps:** FIX-06 (chain heads), key management story shared with RM1
thinking. **Excellent:** a third party detects a rolled-back archive from two
consecutive attestations alone.

### EXP-02 — Lockdown mode (one-command duress posture)
**Pitch.** `ledger lockdown` flips the archive to a pre-declared duress posture in
seconds: server refuses non-PUBLIC disclosure (or stops), vault file is verified
off-box then locally shredded per policy, a PREMIS event records the action, and a
recovery runbook prints. **Impact.** The threat model is written for raids and
seizures (§4.1) but the tooling assumes calm; the moment the model is *for* has no
verb. Serves A2/B1 directly. **Shape.** A `lockdown` config block (what to stop,
what to remove locally, where the off-box copy must exist first — refuses to shred
without a verified replica, reusing `verify-backup` logic in `cli.py:432`); the
inverse `ledger stand-up` re-verifies from replicas. Pairs with, but is distinct
from, EX1 succession (planned hand-off vs. emergency posture). **Effort:** M.
**Risks/deps:** destructive primitive — dual-control (`dualcontrol.py`) and dry-run
default; real-user gate before promoting it as a safety feature. **Excellent:**
tabletop-exercised runbook; lockdown-then-stand-up round-trip test green; time from
command to safe posture under 60 s in the demo archive.

### EXP-03 — Adoption self-assessment wizard (threat-model profiler)
**Pitch.** An interactive `ledger checkup` that walks a non-ops steward through the
`ADOPTING.md` checklist against the *live* config — detects FDE absence, key on the
same disk as the vault, zero off-box locations, missing TLS proxy — and emits a
dated, committed readiness report with plain-language residual risks. **Impact.**
Goes beyond EX6 (installer sets safe defaults) to *verifying* an existing
deployment and keeping it legible over time; converts ADOPTING.md from prose into a
check. **Shape.** `Archive.check_readiness` (`src/ledger/ingest.py:875`) already
exists as a seed; extend with host checks (mount flags for FDE heuristics, env-var
provenance of `LEDGER_VAULT_KEY`, `locations` topology), output as Markdown into
the audits directory FIX-10 creates. **Effort:** M. **Risks/deps:** host checks are
heuristic — report honestly as "could not verify," never fake a pass. **Excellent:**
a fresh collective reaches a green (or honestly-yellow) report without a
sysadmin; report regenerated in the weekly audit cadence.

### EXP-04 — Multi-party consent at ingest (named-subject tokens)
**Pitch.** When a record names other people, capture that at ingest and issue each
named subject their own claim token, giving them the same consent-status and
objection surface contributors get. **Impact.** GOVERNANCE.md §3 ("whose consent
governs a record that names several people") is policy without plumbing, and RM12
only time-bounds objection *responses*. Persona B2 (Marisol) currently has no
standing in the system at all. **Shape.** An optional `named_subjects_count` at
ingest mints extra HMAC claim tokens via the existing
`consent.issue_claim_token` machinery, stored only as token hashes (no identities
— the no-outing rule extends to subjects); `/record/{id}/consent` already exists
as the acting surface; objections land in `ConsentRequestStore` typed
`subject-objection`. **Effort:** M. **Risks/deps:** token distribution is human
and out-of-band (like the vault key in `succession.py`); governance text must
define subject vs. contributor precedence — SME/community gate. **Excellent:** a
named subject can object and track resolution with zero account, zero identity
stored, and the contributor's own identity never inferable from the flow.

### EXP-05 — Extract `preservation-core` as a standalone library
**Pitch.** Package `bag.py`, `fixity.py`, `cas.py`, `metadata/premis.py`,
`metadata/dublincore.py`, and `preservation.py` as an independently installable,
independently tested library (`ledger-preservation-core`) with ledger as its first
consumer. **Impact.** README already claims "the BagIt/PREMIS packaging and the
fixity auditor are usable on their own" — make it true. Gives the other 20
portfolio repos preservation-grade export for free, and gives the digital-
preservation community a dependency-free BagIt+PREMIS toolkit — an adoption and
credibility channel independent of the archive product. **Shape.** A src-layout
split within this repo (workspace or subpackage published separately), no behavior
change, shared test fixtures; SemVer per the portfolio release standard.
**Effort:** L. **Risks/deps:** API freeze pressure arrives early; do after FIX-01
settles bag-revision semantics. **Excellent:** another portfolio repo writes valid,
externally-validated bags with ≤10 lines of integration; ledger's own suite runs
against the published package.

---

## Horizon 2 — adjacent capabilities, audiences, integrations

### EXP-06 — Client-side sealing (contributor-encrypted submissions)
**Pitch.** For SEALED material, encrypt in the contributor's browser before upload,
so the server, steward, and wire never see clear bytes at all. **Impact.** Today
`_post_contribute` receives clear multipart bytes and only then encrypts
(`ingest.py:316-325`); the server operator is inside the trust boundary for the one
tier that promises "sealed from everyone." Closes the gap between the SEALED
promise and its implementation for the most at-risk contributors (A1/A2). Distinct
from EX7 (mobile UX). **Shape.** A small, auditable, dependency-pinned WebCrypto
script served from `web/static` (the CSP already forbids third-party sources);
ciphertext ingested as an opaque payload with a declared envelope version;
threat-model section on what moves (XSS in the contribute page becomes the new
edge). **Effort:** L. **Risks/deps:** JS on a deliberately framework-free site;
key handling UX for later authorized recovery; crypto SME gate (with FIX-11).
**Excellent:** a network capture plus full server disk image yields zero plaintext
of a SEALED submission; the no-JS path still works with the honest server-side
fallback labeled as such.

### EXP-07 — Local redaction assistant (human-confirmed, offline)
**Pitch.** At contribute/edit time, run an *offline* detector (regex + wordlists +
optional small local NER model) that highlights names, addresses, phones, handles,
and dates in the story text and suggests — never applies — redactions. **Impact.**
Threat model §4.3 is explicit that self-disclosure is the residual ledger cannot
police; this is the largest harm-reduction lever available without policing.
Serves A1/A3 at the exact moment of risk. **Shape.** A `ledger.redact_suggest`
module callable from the CLI and the contribute preview
(`contribute.render_preview_panel`); on-device only, no network calls, degrades to
regex-only on minimal installs; suggestions link to the existing one-click
per-field sealing. **Effort:** M (regex tier) / L (local model tier).
**Risks/deps:** false confidence is the failure mode — the UI must say "this finds
some, not all"; real-user gate on the wording. **Excellent:** measured recall on a
synthetic corpus published honestly in-repo; zero network egress asserted by test;
suggestion→seal is one action.

### EXP-08 — Sneakernet replication + printable archive editions
**Pitch.** First-class offline distribution: `ledger export-drive` produces a
self-verifying USB courier package (bags + manifests + a static, no-server browse
page + verification script), and `ledger print-edition` renders selected PUBLIC
records as an accessible, zine-style PDF/HTML booklet with per-record fixity QR
codes. **Impact.** Minimal-computing communities (the roadmap's own design
constraint) often have no reliable connectivity; paper is the oldest preservation
tier and culturally native to zine communities. Extends portability beyond "walk
away with bags." **Shape.** Reuse `export.py`, `bag.validate_bag`, and the
list/table renderers in `render.py`; static browse is a build product, not a
server. **Effort:** M. **Risks/deps:** print output must pass the same CW and
disclosure rules (PUBLIC-only by construction); accessible PDF is genuinely hard —
scope to tagged HTML first. **Excellent:** a courier package verifies on a machine
with no ledger installed (plain `sha256sum` script); the booklet renders CWs
before content and passes the FIX-12 checks in its HTML form.

### EXP-09 — Oral-history session kit (beyond RM6's transcript step)
**Pitch.** A guided *recording-session* workflow: per-segment consent script
("this part public, this part sealed 20 years"), segment markers captured during
recording, and automatic per-segment `Field`/policy scaffolding at ingest.
**Impact.** RM6 makes transcripts first-class after the fact; this shapes consent
*during capture*, which is where elder narrators (A3) actually negotiate it — and
produces records whose selective disclosure matches what was said aloud.
**Shape.** A session manifest format (JSON: segments, policies, spoken-consent
timestamps) the ingest path maps onto `Field`s and payload policies; a printable
facilitator script from GOVERNANCE/consent language; no recording software is
built — the kit wraps whatever recorder the community has. **Effort:** M.
**Risks/deps:** consent-language review is a human/legal gate; RM6 transcripts
pair naturally. **Excellent:** a facilitator with one page of instructions
produces an ingest where each segment's policy provably matches the spoken
consent timestamps.

### EXP-10 — Warrant canary and legal-process transparency page
**Pitch.** An auto-dated statement ("as of <date> this archive has received N legal
demands of type X"), refreshed on a schedule, plus a committed legal-response
playbook for stewards. **Impact.** The subpoena case (§4.2) tells stewards to
"consult counsel" but gives communities no transparency instrument; canaries are
established practice in adjacent movement infrastructure. **Shape.** A
`/transparency` page rendered from a signed, dated statement file the steward
re-attests on a cadence (mechanism shared with EXP-01); staleness is displayed
honestly ("last attested 47 days ago"). **Effort:** S (code) — the substance is
the **legal gate**. **Risks/deps:** canary wording and its legal effect vary by
jurisdiction — *must not ship without counsel review*; per the portfolio ethos,
defer and say so in-repo rather than shipping unreviewed legal text.
**Excellent:** counsel-reviewed template committed with the review noted; the page
can never render a stale attestation as current.

### EXP-11 — Institutional deposit bridge (METS/EAD finding-aid export)
**Pitch.** Export a partner-ready descriptive layer — a METS wrapper per AIP and an
EAD finding aid per collection — so a university library or Portico-style service
can ingest ledger material into their existing systems without hand-mapping.
**Impact.** EX8 (signed deposit bundle) proves *integrity* to a partner; this
speaks their *catalog language*, which is what actually unlocks deposit agreements
(persona C1; the Digital Transgender Archive/Portico precedent in the research
basis). **Shape.** New `metadata/mets.py` + `metadata/ead.py` exporters over
existing PREMIS/DC sidecars (both are XML transforms of data already held; the
`to_premis_xml` pattern in `metadata/premis.py:` is the template); PUBLIC- and
partner-grant-scoped only, through `disclose`. **Effort:** L. **Risks/deps:** real
archivist (SME) validation against a real partner's ingest profile — synthetic
conformance alone is not success; builds on RM5's rights/PID work. **Excellent:**
a real partner system ingests a sample deposit without manual remediation, and the
export path provably cannot include non-granted material.

---

## Horizon 3 — transformative bets

### EXP-12 — Cryptographic embargo (time-lock for `SEALED_UNTIL`)
**Pitch.** Make dated seals hold *against the disk*, not only against the policy
engine: embargoed content encrypted such that no present-day key holder can read
it before the date, via threshold key shares escrowed across independent
community instances that release shares only after the date. **Impact.** Today a
seized disk yields `SEALED_UNTIL` content immediately (only absolute-SEALED is
encrypted at rest, `ingest.py:386-397`); an embargo is currently a promise the
software keeps, not one physics keeps. This would be a genuinely novel guarantee
for community archives. **Shape.** Research-first: a design doc exploring
share-escrow among federated instances (social timelock) vs. published timelock
services; prototype gated on FIX-11's key-hierarchy work and RM1's threshold
machinery. **Effort:** XL. **Risks/deps:** hard cryptography + hard sociology;
crypto SME gate absolute; failure mode (lost shares = lost history) must be
weighed by real communities. **Excellent:** an honest published analysis even if
the conclusion is "not safely buildable at our scale" — that finding is itself
portfolio-grade output.

### EXP-13 — Crypto-agility and post-quantum posture for the vault
**Pitch.** A versioned key-and-algorithm envelope for the identity vault and
sealed content, an explicit migration path (`rekey` already exists,
`identity.py:235`), and a documented harvest-now-decrypt-later analysis. **Impact.**
Identity confidentiality here has a *decades* horizon — precisely the
harvest-now-decrypt-later profile; a seized vault ciphertext today must still
protect someone in 2050. No RM item touches algorithm lifecycle. **Shape.**
Envelope versioning first (with FIX-11), then hybrid encryption
(current + PQ KEM) for new entries when `cryptography` exposes stable primitives;
a committed algorithm-lifecycle policy doc. **Effort:** L (agility) + tracking
(PQ). **Risks/deps:** crypto SME gate; avoid pre-standard PQ dependencies that
break the single-dependency ethos (`docs/adr/0005`). **Excellent:** rotating
algorithm (not just key) is a tested one-command migration; the policy doc states
review dates and triggers.

### EXP-14 — Reading-room enclave: aggregate research access to sealed corpora
**Pitch.** Let researchers ask steward-approved *aggregate* questions ("how many
records mention evictions by year") over material they cannot read, with every
query logged, dual-approved, and answered only above k-anonymity floors.
**Impact.** Historians (D1) want the sealed 90 % of the archive's evidentiary
value; contributors get scholarship without exposure. Builds on the shipped
reading-room enforcement work (`tests/test_reading_room_enforcement.py`) but is a
different capability class. **Shape.** A query-manifest workflow through
`dualcontrol.py`; execution against a steward-side index (FIX-04's machinery);
suppression below configurable k; results and refusals both PREMIS-logged.
Never interactive; always human-approved. **Effort:** XL. **Risks/deps:**
aggregation leaks are subtle (small cells, differencing attacks) — privacy SME
gate; community governance must opt in per-collection. **Excellent:** a
differencing-attack test suite that fails closed; a real historian gets a real
answer with a published audit trail and no record disclosed.

### EXP-15 — Mutual preservation-aid network (encrypted replica exchange)
**Pitch.** Community instances pair up and hold each other's *encrypted* replicas
— "I hold your bags, you hold mine" — with automated fixity attestations
exchanged on a schedule, so a raided or burned-out collective can recover from a
sibling it never had to fully trust. **Impact.** Turns the movement's existing
solidarity into durable redundancy; addresses the §4.5 residual (hostile replica
host reads what it holds) by making the exchanged unit ciphertext. Distinct from
EX2, which federates *discovery* of public records; this federates *custody*.
**Shape.** Storage-layer encryption of outbound replica sets (key stays home —
extends `replicate.py` with an encrypting transport), a pairing handshake, and
attestation exchange reusing EXP-01's format; recovery drill = `verify-backup`
against a returned set. **Effort:** XL. **Risks/deps:** RM2/FIX-11 must land the
at-rest encryption design first; key-loss doubles as archive-loss — runbooks and
succession (EX1) integration essential; real-community pilot gate. **Excellent:**
a full recovery drill from a partner's copy on commodity hardware, with the
partner provably unable to read any non-public content they hosted.
