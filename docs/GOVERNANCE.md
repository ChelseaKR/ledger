# Governance

ledger is community-governed. The central principle, repeated everywhere because it is
load-bearing:

> **Authority is documented policy, not whoever holds the server.**

Running the box does not confer the right to decide. The right to decide comes from a
process a community wrote down, applied consistently, and recorded. A steward with root
access who acts outside this policy is acting without authority, and the record of that
action — kept in the append-only moderation log and the PREMIS event chain — is what
makes the overreach visible and reviewable. This document describes how stewards are
chosen and removed; how moderation, content-warning, and takedown decisions are made,
recorded, and appealed; how disputes are resolved; and how consent changes and takedowns
propagate across replicas.

This is a reference governance model. A community adopting ledger is expected to adapt
the specifics — quorum sizes, timelines, who counts as a member — to its own size and
needs. What should *not* change is the shape: decisions are attributed, justified,
recorded, and appealable, and no single person quietly holds the power to out a
contributor.

---

## 1. Roles

| Role | Who they are | What they can do | What they cannot do |
| --- | --- | --- | --- |
| **Contributor** | A person who has contributed a record. | Set and change the disclosure policy of their own record and fields; tighten access; request takedown; appeal a moderation decision affecting their record. | See others' sealed content; act as a steward. |
| **Community member** | A vetted member of the community the archive serves. | View `public` and `community` material; raise concerns and appeals; participate in steward selection per the community's process. | View `stewards`-only or sealed content; take moderation actions; resolve identities. |
| **Steward** | A trusted member granted administrative responsibility by the community. | View all disclosure levels (including sealed content) *for administration*; add content warnings; perform takedowns and restores; record consent changes; run fixity audits and replication; add storage locations. | Resolve a contributor's real identity (requires a separate `identity_unseal` grant they do not hold by default); act without recording a reason; rewrite the log. |
| **Identity-unsealer** | A capability, deliberately rare, granted for a specific `identity_ref` and a specific, documented reason. | Resolve the one contributor identity their grant names. | Anything else; the grant is scoped to named refs only. |
| **Maintainer** | A person who maintains the ledger *software*. | Merge code, cut releases, triage security reports. | Govern any particular community's archive or override its policy. |

Two separations in this table are the heart of the model and are enforced in code, not
just convention:

- **Steward is not identity-unsealer.** A steward grant carries `is_steward=True` but an
  empty `identity_unseal` set (`src/ledger/access/grants.py`). Administering the archive
  never grants the power to out a contributor. The identity-unsealer capability is
  separate, scoped to specific refs, and should be granted rarely, for a stated reason,
  and ideally never as a standing capability held by one person (see the threat model,
  §4.2 and §4.4).
- **Maintainer is not steward.** The people who write ledger do not govern the
  communities who run it. A maintainer has no special authority over any archive's
  records or decisions.

---

## 2. Choosing and removing stewards

Stewardship is a grant of trust, and it is revocable. The mechanism in code is a grant
file (`load_grants`, `src/ledger/access/grants.py`): a steward exists because a
`steward(...)` grant names them. Provisioning and de-provisioning a grant is the
technical act; the *authority* to do it comes from the process below.

### Becoming a steward

1. **Nomination.** A community member or an existing steward nominates a candidate, in
   writing, with a short statement of why.
2. **Community assent.** The community ratifies by whatever threshold it has adopted
   (e.g. consensus at a meeting, or a majority of active members over a fixed comment
   period — the default reference threshold is a simple majority of active members with a
   minimum 7-day comment window). Small collectives may use consensus; larger ones a
   vote. The threshold itself is recorded in the community's adopted policy.
3. **Provisioning.** An existing steward (or, for the first steward, the community at
   founding) adds the new steward grant to the grants file. The vault key and any
   `identity_unseal` capability are handled separately and are *not* automatically
   conferred (least privilege).
4. **Record.** The decision is recorded — minimally as a `consent-change`/administrative
   note in the moderation log or the community's minutes, with who, when, and the
   ratifying threshold.

### Removing a steward

A steward may be removed by **resignation**, by **the same threshold that appointed
them**, or **immediately by any other steward in an emergency** (credible compromise,
active abuse) subject to prompt ratification.

1. **Trigger.** Resignation, a removal motion, or an emergency suspension.
2. **De-provisioning.** The steward's grant is removed from the grants file; a deny-by-
   default system means a removed steward immediately falls back to their underlying
   membership level. If the removed steward held the vault key or an `identity_unseal`
   grant, **the key must be rotated and the grant revoked** — removal of the steward
   grant alone does not undo a key they have already seen. Rotation is a first-class,
   recorded operation: `ledger vault rekey` re-encrypts the vault under a new key and
   logs a `REKEY` PREMIS event (see the steward runbook in `infra/README.md`).
3. **Record and review.** The removal, its reason, and any emergency action are recorded
   and reviewed by the community at the next opportunity. An emergency suspension that the
   community does not ratify is reversed.

The reference default is a **minimum of two stewards** wherever the community can manage
it, so that no single person is both the only administrator and unaccountable, and so
that high-stakes actions can require a second steward.

---

## 3. Moderation, content warnings, and takedowns

Every consequential moderation action in ledger is **justified, attributed, recorded, and
contestable**. This is not a matter of steward etiquette; it is enforced by
`src/ledger/moderate.py`.

### How decisions are made

- **Content warnings** are structured metadata on the record, drawn from a controlled,
  community-editable vocabulary (`src/ledger/config.py`), and shown *before* the material
  renders (`src/ledger/server.py`). Adding a warning is a low-stakes, reversible
  steward action; the bias is toward warning generously. A contributor or member may
  request a warning be added.
- **Takedowns** remove a record from disclosure. A takedown is high-stakes and the
  reference policy requires either the contributor's request (always honored) or, for a
  steward-initiated takedown, review by a second steward where two exist. A takedown is a
  *decision record* plus an *effect*: `takedown(...)` records the accountable decision,
  and the caller then removes copies and propagates the takedown to replicas (see §5).
- **Consent changes** tighten or alter a record's default policy. A contributor's own
  consent change is honored on request; a steward recording a consent change on a
  contributor's behalf must state the reason and, where possible, the contributor's
  instruction.

A moderation decision **without a stated reason is rejected at the boundary.** Every
`ModerationAction` requires a non-empty `reason`, validated both at construction and again
when appended to the log (`_require_reason`, `src/ledger/moderate.py`). A decision nobody
will explain is not a decision the system will record.

### Whose consent governs a record that names several people

A record often describes or names people who are not its contributor. ledger resolves
the multi-party question deliberately, and **without an automatic veto**:

- **The contributor retains control** of their own record's policy and fields. Consent
  to *keep, tighten, or withdraw* the record is the contributor's, exercised with their
  claim token (`/record/{id}/consent`).
- **A named subject has a voice, not a switch.** Anyone named or described in a record
  they did not contribute may **object** (`/record/{id}/object`, no claim token needed).
  An objection is a first-class, recorded request (`kind="object"`) that a steward must
  weigh — it does **not** automatically restrict or remove the record. This deliberately
  avoids two failure modes: a heckler silently censoring a record by objecting, and a
  contributor's account of harm being erased by the person it names.
- **A steward adjudicates** each objection on the record, balancing the contributor's
  account, the subject's safety, and the community's interest, and records the decision
  with a reason like any moderation action. Where two stewards exist, a contested
  objection warrants a second steward's review (as for a steward-initiated takedown).
- **Safety still wins by construction.** None of this can out a contributor: a subject's
  objection, and a steward's handling of it, run through the same surfaces that carry no
  contributor identity (no-outing rule). And the narrowest-disclosure default means a
  record that names someone is sealed-pending until a steward publishes it, so the first
  review already considers who is named before anything is public.

This is a governance rule, enforced by the objection mechanism (`kind="object"`) plus a
recorded, contestable steward decision — not by code that lets one party silently
override another.

### How decisions are recorded — the `ModerationLog`

The moderation log (`ModerationLog`, `src/ledger/moderate.py`) is **append-only**:
actions are added, never edited or removed, so the history of who decided what, and why,
cannot be quietly rewritten. Each `ModerationAction` records the four facts an audit
needs:

- **what** — the action: `warn`, `takedown`, `restore`, `consent-change`, or `appeal`;
- **who** — the acting steward's id (`actor`);
- **why** — the required non-empty `reason`;
- **to which record** — the opaque `target_record` id.

The log carries no identity and no sealed value, by construction — actors are steward
ids, reasons describe the *decision*, and the target is an opaque record id (the
no-outing rule holds in the log as everywhere). Serialization is canonical and writes are
atomic, so the persisted log is byte-reproducible and a crash mid-write cannot truncate
it. In parallel, preservation-significant actions also emit PREMIS events
(`MODERATION`, `TAKEDOWN`, `CONSENT_CHANGE`, `REDACTION`) with agent and outcome
(`src/ledger/metadata/premis.py`), so the chain of custody is auditable alongside the
moderation record.

### How decisions are appealed

Every recorded action is contestable. An **appeal** is itself a first-class
`ModerationAction` (`appeal(...)`, `src/ledger/moderate.py`): it targets the same record
as the action it contests, carries its own actor and required reason, and links back to
the contested action via `appeal_of`. An appeal is therefore part of the same auditable
chain as the decision it challenges — never a separate, unlinked complaint. A contributor
whose record is affected, or any community member with standing under the community's
policy, may file an appeal.

An appeal is **reviewed by a steward who did not make the original decision** (and, for a
takedown, by the community or a steward quorum where the reference policy calls for it).
The reviewer either upholds the original action or reverses it — a reversal is recorded as
its own action (e.g. a `restore` undoing a `takedown`, or a `consent-change` re-opening
what was tightened). Nothing is silently undone; the trail shows the decision, the
appeal, and the resolution, in order.

---

## 4. Resolving disputes

Disputes that are not single moderation decisions — disagreements between stewards,
contested removals, conflicts over policy — are resolved by escalation, with the
documented policy (not the server-holder) as the final authority.

1. **Direct resolution.** The people involved try to resolve it directly, in good faith,
   under the community's code of conduct (`CODE_OF_CONDUCT.md`).
2. **Steward review.** If unresolved, the stewards (excluding any with a conflict of
   interest) review and decide by their adopted threshold.
3. **Community decision.** If still unresolved, or if the dispute concerns the stewards
   themselves, it goes to the full community by the same threshold used to appoint
   stewards. The community's decision is final and recorded.
4. **Recorded outcome.** Every escalation's outcome is written down — in the moderation
   log where it concerns a record, or in the community's minutes where it concerns policy
   or people — so the resolution is part of the same accountable history.

A conflict of interest (a steward deciding their own appeal, an appellant reviewing their
own action) disqualifies that person from the reviewing role at every stage. Where a
community is too small to assemble an uninvolved reviewer, that fact is itself recorded,
and the decision is revisited when the community grows or a neutral party is available.

---

## 5. How consent changes and takedowns propagate across replicas

Consent is revocable and recorded, and a downstream mirror may not lawfully ignore it
(README, Hard Rule 4). The technical model that makes this real:

- **Consent state travels with the record.** A record's default policy and its per-field
  policies are part of the record manifest inside the bag (`serialize_record`,
  `src/ledger/ingest.py`). When a consent change tightens a policy (`change_consent`,
  `src/ledger/moderate.py`), the updated record is what gets bagged and replicated, so a
  replica that receives the updated bag receives the tightened policy with it. A mirror
  serving from a current bag therefore serves the current consent state.
- **Identity revocation is honored at the storage layer.** Revoking consent to identity
  resolution deletes the vault mapping (`IdentityVault.revoke`, `src/ledger/identity.py`),
  which is idempotent so a takedown can be retried safely. Because the vault is not
  replicated with the bags, revoking it at the authoritative vault removes the ability to
  resolve that identity everywhere that resolves through the vault.
- **Takedown is a decision plus a propagated effect.** `takedown(...)` records the
  accountable decision and emits a PREMIS `TAKEDOWN` event; the steward then removes the
  record's copies and pushes the removal to every configured replica location. Separating
  the *decision record* from the *effect* keeps the audit trail complete even if
  propagation must be retried against a temporarily unreachable mirror.
- **A mirror cannot be allowed to be stale silently.** Replication re-verifies bags on
  arrival, and fixity audits run on a schedule across every location
  (`src/ledger/replicate.py`, `src/ledger/fixity.py`); an unreachable or divergent
  replica is surfaced as a labelled preservation event, not hidden. Propagation of a
  consent change or takedown to a location that was offline is retried until the location
  reflects the change, and the gap is visible in the meantime rather than silently
  ignored.

The honest limit: propagation is only as complete as the set of replicas the community
*knows about and controls*. A copy a hostile or unknown party made off the network is
outside the system's reach — this is the residual risk in the threat model (§4.5). The
governance answer is to mirror sealed or sensitive content only to hosts the community is
willing to trust, and to record the locations it replicates to so propagation can be
verified.

---

## 6. Decision and appeal flow

```
  Concern raised (contributor, member, or steward)
          │
          ▼
  Steward makes a decision ──────────────► recorded in ModerationLog
   (warn / takedown / consent-change)        • what  (action)
          │                                  • who   (actor / steward id)
          │                                  • why   (required reason)
          │                                  • which (target record id)
          │                                + PREMIS event (agent, outcome)
          ▼
  Affected party files an APPEAL ─────────► recorded as a linked action
          │                                  (appeal_of → original action)
          ▼
  Reviewed by a DIFFERENT steward
   (takedowns: + community / quorum)
          │
   ┌──────┴───────┐
   ▼              ▼
 Upheld        Reversed ──────────────────► recorded as its own action
 (recorded)     (e.g. restore / re-open)     (nothing is silently undone)
          │
          ▼
  Still contested, or dispute is about
  the stewards themselves?
          │
          ▼
  Escalate to the full COMMUNITY ─────────► decision is final and recorded
   (same threshold that appoints stewards)
```

Every box in this flow leaves a recorded, attributed, justified trace. That trace — not
the server, not any one person — is where authority lives. A decision that is not
recorded is not a governed decision, and a community can hold its stewards to exactly
that standard because the log and the PREMIS chain make every consequential action
visible after the fact.

---

## 7. Relationship to the threat model

Governance and the threat model are two halves of the same safety property. The threat
model (`docs/THREAT-MODEL.md`) describes what the *code* guarantees against specific
adversaries — including a malicious or compromised steward — and is honest about residual
risk. Governance describes the *human* controls that the code's guarantees make possible:
not concentrating the vault key and `identity_unseal` capability in one person; requiring
a second steward for high-stakes actions; recording and appealing every decision; and
removing and re-keying when trust is broken. The code makes ordinary stewardship safe;
governance keeps extraordinary capability accountable. Neither is sufficient alone.
