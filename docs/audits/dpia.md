# Data Protection Impact Assessment — ledger

Draft prepared: 2026-07-07 · Accountable owner review: pending · Recheck cadence
after approval: per release. This automated draft is not Chelsea Kelly-Reif's
assessment or legal sign-off until she explicitly records that decision here.

Prepares the artifact for RTF-04 (`docs/ROADMAP.md#open-conformance-gaps`), the repo's
artifact gap: an ASVS L2, PII-handling archive with no distinct, dated
data-protection-impact assessment. This document is deliberately different work from
[`docs/THREAT-MODEL.md`](../THREAT-MODEL.md), which is written adversary-by-adversary
("what could a hostile party do"). A DPIA is written data-subject-by-data-subject:
lawful basis, what is collected and why, how long it is kept, what rights a subject
has and how they exercise them, and what risk remains after mitigation. Where the two
documents describe the same mechanism they are cross-referenced rather than
duplicated; read `docs/THREAT-MODEL.md` alongside this file for the adversarial detail
this assessment does not restate.

This is a review-ready project self-assessment draft, not a legal opinion. ledger is
self-hosted software distributed to deployers, not a hosted service ledger's
maintainer operates — see [§7](#7-what-this-dpia-does-and-does-not-cover) for what
that means for who "the controller" is in practice.

---

## 1. Description of processing

**What ledger is.** Software for community-run archives holding oral histories,
zines, protest ephemera, and similar material contributed by people, some of whom
face real danger (legal, physical, or social) if their contribution is linked back to
them. See `README.md` and `docs/THREAT-MODEL.md` §0 for the full framing.

**Why a DPIA at all.** ledger's core purpose is processing personal data that is, for
many contributors, special-category or high-risk by nature (data revealing details
that intersect with legally protected or criminalized status in some jurisdictions —
immigration status, sexuality, gender identity, political or organizing activity).
Necessity and proportionality are addressed throughout this document rather than in
one section, because they shape every design choice described below.

**Processing operations in scope:**

1. **Ingest** — a contributor submits a record (content + optional identity) via a
   Submission Information Package (SIP); `ingest_sip` (`src/ledger/ingest.py`) computes
   fixity, seals any identity into the vault, and produces an Archival Information
   Package (AIP): a BagIt bag with dual-algorithm manifests, a Dublin Core sidecar, and
   a PREMIS event log.
2. **Storage** — the AIP is held in a content-addressed store; identity, if present,
   is held separately in an encrypted vault (`src/ledger/identity.py`).
3. **Disclosure** — browse, search, single-record view, and the JSON API all resolve
   through one function, `disclose` (`src/ledger/access/policy.py`), which reads
   consent/access policy and emits a `DisclosedRecord` that structurally cannot carry
   identity.
4. **Moderation** — a steward can warn, take down, record a consent change, or process
   an appeal, each an attributed, reasoned, append-only `ModerationLog` entry
   (`src/ledger/moderate.py`, `docs/GOVERNANCE.md` §3).
5. **Replication** — whole bags are copied to configured replica hosts and
   re-validated on arrival (`src/ledger/replicate.py`); the vault is never replicated
   with the bags.
6. **Identity resolution** — a grant-gated, per-`identity_ref` lookup
   (`IdentityVault.resolve`) used only by a holder of an explicit `identity_unseal`
   grant, almost always for takedown/consent administration, never for public
   disclosure.

---

## 2. Data inventory

| Category | Examples | Where it lives | Sensitivity |
| --- | --- | --- | --- |
| Contributor identity | Real name, contact, pronouns, free-text notes | Encrypted vault only (`identity.py`); never inline with a record | Highest — the single most dangerous datum in the system (`docs/THREAT-MODEL.md` §1) |
| Record content | Oral history text/audio/image, zine pages, protest ephemera, runbooks | Content-addressed store, inside the BagIt AIP | Varies by record; often itself sensitive (an account of organizing, a protest photo) |
| Descriptive/preservation metadata | Dublin Core sidecar, PREMIS event log | Inside the AIP alongside content | Low structurally (identity-free by construction and re-scanned for identity leakage at ingest — `_assert_identity_free`), but content-warning and subject-matter fields can be sensitive |
| Access-policy state | Per-record and per-field policy (public / community / steward / sealed) | Record manifest (`record.json`) inside the AIP | Low on its own; governs exposure of the above |
| Grant tokens | Viewer capabilities, `identity_unseal` sets | Grants file, provisioned by an operator (`access/grants.py`) | High — controls who can resolve identity |
| Consent-request / consent-change history | What a contributor agreed to disclose, and any tightening or takedown request | PREMIS `CONSENT_CHANGE`/`TAKEDOWN` events; `ModerationLog` | Medium — reveals that a consent action occurred, not the underlying identity |

This inventory matches `docs/THREAT-MODEL.md` §1's asset table; this document adds the
"where it lives" and "sensitivity" columns a threat model does not carry, because a
DPIA's job is to trace data, not adversaries.

**No special processing beyond the above.** There is no analytics, telemetry,
profiling, or automated decision-making anywhere in ledger — ingest, fixity, access
policy, and disclosure are all deterministic (`docs/adr/0006-standards-applicability.md`).
No inference is drawn about a contributor beyond what they explicitly submitted.

---

## 3. Lawful basis and necessity

**Basis: consent, explicit and revocable.** A contributor decides, at contribution
time and at any later time, what is public, community, steward-only, or sealed for
their own record and fields, and can tighten access or request takedown at any time
(README "Hard rules" §4; `docs/GOVERNANCE.md` §3, "Consent changes"). There is no
processing of a contributor's identity or record content on any basis other than
their own consent — no legitimate-interest or contractual-necessity basis is invoked
for identity, because identity capture is itself optional (a SIP need not carry one).

**Necessity of each category collected:**

- **Identity** is collected only when a contributor chooses to be identifiable to a
  future steward action (e.g. so a takedown request can later be authenticated) or a
  community's practice calls for it; it is never required to contribute. When
  collected it is immediately separated from the record (`ingest_sip` seals it into
  the vault before the record is finalized) and the record itself carries only an
  opaque `identity_ref` token generated by `secrets.token_urlsafe`
  (`docs/ARCHITECTURE.md` §1.1). This is the minimization the whole system is built
  around: the data that would out a contributor is architecturally the smallest
  possible surface, present nowhere it is not strictly needed.
- **Record content** is the archive's entire purpose; there is no lesser amount of it
  that would satisfy the purpose of preservation.
- **Grant tokens** are the minimum needed to implement least-privilege access
  (`build_grant` requires every capability to be named explicitly —
  `docs/THREAT-MODEL.md` §4.4).
- **Consent/moderation history** is retained because accountability requires it: an
  appeal or a dispute needs a record of what was decided, by whom, and why
  (`docs/GOVERNANCE.md` §4).

**Proportionality.** The identity/record separation (ADR 0003) means the highest-risk
data element — identity — is processed in the smallest possible way: written once at
ingest, read only by an explicit grant holder for an explicit `identity_ref`, and
never touched by any disclosure, browse, search, or export path
(`docs/THREAT-MODEL.md` §3, the no-outing requirement). No feature exists, or is
planned, that would touch identity more broadly than this — see the non-goals stated
in `docs/RESPONSIBLE-TECH-AUDITS.md` §A.

---

## 4. Data-subject rights

| Right | How it is exercised in ledger today | Mechanism |
| --- | --- | --- |
| **Access** (what is held about me) | A contributor can view their own record's current disclosure state and history via a steward, or directly if they hold a claim token for their record | `/record/{id}/consent` claim-token flow (`docs/GOVERNANCE.md` §"Whose consent governs") |
| **Rectification** | A contributor can request a correction; a steward records it as a `consent-change`/administrative decision with an outcome | `docs/GOVERNANCE.md` §2.4 |
| **Erasure / takedown** | A contributor can request takedown of their record at any time; a takedown is a PREMIS event plus an effect that removes the record from disclosure and propagates to every configured replica | `moderate.takedown`, `docs/GOVERNANCE.md` §5 |
| **Restriction (tighten access)** | A contributor can tighten their record's or a field's policy at any time without a full takedown | `change_consent`, `docs/THREAT-MODEL.md` §"Lawful basis / consent model" |
| **Objection / appeal** | A steward decision (including one affecting a contributor's own record) is appealable and logged, not final | `docs/GOVERNANCE.md` §4 |
| **Data portability** | Not implemented as a self-service export today; a contributor's record is retrievable by a steward on request in its BagIt form, which is a portable, standards-based package (RFC 8493) by construction, but there is no self-service "export my data" path a contributor can trigger unassisted | Gap — see §6 |

**No formal SLA exists yet for rights-request turnaround.** Mechanically every right
above is supported by an existing function; what is missing is a stated response-time
commitment (e.g. "N days") a deployer publishes to their contributors. This gap is
carried forward from `docs/RESPONSIBLE-TECH-AUDITS.md` §C and is not resolved by this
document — see §6.

---

## 5. Retention and cross-replica propagation

**No fixed retention period.** ledger is a preservation archive; the default posture
is to keep a record until a contributor requests otherwise (takedown) or a steward
acts under `docs/GOVERNANCE.md`'s moderation process. There is no automatic
expiry/deletion job. This is a deliberate design choice appropriate to an archive's
purpose, but it means the *only* retention control a data subject has is their own
request — stated here plainly so a deployer community can decide, with open eyes,
whether to layer a retention policy of their own on top (e.g. a community charter
committing to periodic consent re-confirmation).

**Propagation is not optional.** A consent change or takedown is not honored merely
at the authoritative copy — `docs/THREAT-MODEL.md`'s consent model requires it to
propagate to every configured replica, because a stale mirror serving withdrawn
consent is the same harm as never having honored the request at all
(`docs/GOVERNANCE.md` §5). The mechanism:

- The updated record (with its tightened policy) is what gets bagged and replicated,
  so a replica serving from a current bag serves current consent state.
- Identity revocation is honored at the storage layer: revoking consent to identity
  removes the vault entry, an idempotent operation safe to retry.
- A takedown propagation to a replica that was offline at the time is retried until
  the location confirms, rather than being silently dropped
  (`src/ledger/replicate.py`).

**Residual retention risk, stated honestly:** a replica host that is offline
indefinitely, or one an operator stops actively re-syncing, can retain a
pre-takedown copy for longer than the authoritative archive intends. This mirrors
`docs/THREAT-MODEL.md` §4.5's "a hostile replica host has whatever it received" —
here restated from a data-protection lens: propagation is retried, not guaranteed
within a bounded time, against a replica that never comes back online. The mitigation
is the same as the threat model's: mirror sealed content only to hosts a community is
willing to trust, and treat "replica confirmed" as part of a takedown's completion
check, not an afterthought.

**Vault-key destruction as a retention control.** Because identity is encrypted under
a single key held off the authoritative box (`docs/THREAT-MODEL.md` §4.1), a
community that wants to guarantee identity is permanently unrecoverable (e.g. winding
down a project) can destroy the key; the vault ciphertext then decrypts to nothing
recoverable, everywhere it has ever been copied, without needing to physically locate
and erase every replica of the ciphertext. This is a meaningful erasure primitive
this DPIA surfaces as a deployer-facing option, not previously named as such in
`docs/THREAT-MODEL.md` or `docs/GOVERNANCE.md`.

---

## 6. Residual risk and gaps (data-protection lens)

These are risks and gaps from a *data-protection* perspective — necessity,
proportionality, and data-subject rights — as distinct from
`docs/THREAT-MODEL.md`'s adversarial residual risks, which remain the authoritative
statement of what a hostile actor can still do. Where the two overlap, this section
cross-references rather than restates.

1. **No stated rights-request SLA.** Every right in §4 is mechanically supported, but
   no deployer-facing document commits to a response time. *Mitigation:* a deployer
   should publish one in their own community's governance charter; ledger's own docs
   should eventually recommend a default (tracked as a follow-up, not blocking this
   DPIA).
2. **No self-service data-portability export.** A contributor can request their
   record from a steward, but cannot trigger an export unassisted. *Mitigation:*
   low-effort future work — the BagIt AIP is already a portable format; what's
   missing is a contributor-facing trigger for it.
3. **Retention has no upper bound absent a request.** By design (§5) — appropriate
   for a preservation archive, but a deployer community should decide and publish
   its own retention posture rather than relying on ledger's defaults alone.
4. **Cross-replica takedown propagation is retry-based, not time-bounded**, against a
   replica that stays offline indefinitely (§5; overlaps `docs/THREAT-MODEL.md`
   §4.5). *Mitigation:* unchanged from the threat model — mirror sealed content only
   to trusted hosts, and treat replica confirmation as part of takedown completion.
5. **Compulsion of a key-and-grant holder** can still surface identity despite every
   architectural mitigation (`docs/THREAT-MODEL.md` §4.2) — a legal/data-protection
   risk as much as a security one. Not resolved by this document; named here so it is
   visible from the data-subject-rights angle too: a subject's erasure request is
   only as good as no one being compelled to have already copied the data elsewhere.
6. **Vault-key destruction as an erasure primitive (§5) is not yet documented for
   deployers** as a recommended practice. Low-effort follow-up: add a short section
   to deployer-facing ops docs.

None of these gaps changes the assessment in §7 (proceed); they are the concrete,
named work a deployer or a future ledger release should do next, in the same spirit
as `docs/THREAT-MODEL.md`'s residual-risk sections: stated plainly rather than
claimed away.

---

## 7. What this DPIA does and does not cover

**Controller/processor framing.** ledger is self-hosted software, not a hosted
service its maintainer operates on anyone's behalf (README, `NOTICE`). In data-
protection terms, a community that deploys ledger is the controller of the personal
data it processes; the ledger project is a software supplier, not a processor with
access to any deployer's live data. This DPIA assesses the *software's* design and
default behavior — the questions a controller would need answered to run their own
assessment — not any specific deployment's operational choices (key custody, TLS
termination, replica trust decisions), which remain that deployer's responsibility
and are named as such throughout `docs/THREAT-MODEL.md` and this document (e.g. §3's
key-off-box guidance, §5's replica-trust guidance). A deployer with jurisdiction-
specific obligations (GDPR, CCPA, or similar) should treat this document as a
starting point for their own assessment, not a substitute for one, and should consult
counsel where compulsion or cross-border transfer questions arise
(`docs/THREAT-MODEL.md` §4.2).

**Conclusion.** Having weighed necessity (§3), the data-subject-rights mechanisms
already in place (§4), retention and propagation behavior (§5), and the residual
gaps named honestly in §6, the processing is proportionate to ledger's stated
purpose — preserving contributor material while treating the risk of outing a
contributor as a first-class preservation requirement equal to bit-integrity
(`docs/THREAT-MODEL.md` §0). No high risk was identified that is not already named
and mitigated, structurally, by the identity/record separation this whole system is
built around. **Decision: proceed**, with the gaps in §6 tracked as follow-up work
rather than blockers.

---

## Related documents

- [`docs/THREAT-MODEL.md`](../THREAT-MODEL.md) — adversary-by-adversary detail this
  assessment does not restate.
- [`docs/GOVERNANCE.md`](../GOVERNANCE.md) — the moderation, consent, and takedown
  process this assessment relies on.
- [`docs/RESPONSIBLE-TECH-AUDITS.md`](../RESPONSIBLE-TECH-AUDITS.md) §C — the
  Responsible-Tech-Framework summary this document was tracked from (RTF-04).
- [`docs/ROADMAP.md`](../ROADMAP.md#open-conformance-gaps) — the conformance gap
  tracker this document closes.
