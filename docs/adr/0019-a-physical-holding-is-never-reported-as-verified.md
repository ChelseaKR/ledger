# 0019. A physical holding is never reported as verified, and its custodian is on the no-outing path

Status: Accepted

Date: 2026-09-10

Implements: #188. Extends ADR 0003 (identity separated from record) from authorship to
possession, and ADR 0010's rule that a record must never assert more confidence than
the pipeline actually had.

## Context

The README's case is a shoebox under someone's bed full of zines, flyers, buttons,
photographs and cassettes. ledger could not take it. Every path assumed a payload, so
the only way to catalogue an undigitized collection was to ingest records with no
files — and that is where the trouble starts, because **a record with no payload is
exactly what a failed ingest also looks like.**

Two measurements decided the shape of this ADR.

**1. A physical record's bag verifies.** A ledger bag carries `record.json`,
`dublincore.json`, `premis.json`, `bagit.txt`, `bag-info.txt` and the manifests as
*tag* files, all covered by the tag manifests. With an empty `data/`, `validate_bag`
returns a report with several results, all of them matching. So `AuditReport.status`
is `VERIFIED`, `report.ok` is `True`, and before this change `ledger audit` printed:

```
PASS	<bag>	(7 file(s) checked)
PASS: 1 bag(s) audited, 0 failed
```

for a box of four hundred flyers in somebody's flat that nothing has ever looked at.
That is this project's own dominant defect — an absence rendered as a value — landing
on the one number a steward reads to decide whether a collection is safe. The same
sentence was reachable in `/healthz`, `/status`, `ledger replicas`, `ledger heal`,
`ledger verify-backup`, the hand-off runbook, the print edition and the courier
package.

**2. The custodian is the most exposed person in the chain.** The contributor of a
zine is protected by the vault. The person whose apartment holds the box is not
mentioned anywhere in the existing model, and the obvious design — a structured
`custody` block on `Record` with a policy of its own — would have created a *second*
place where a protected value can be disclosed, beside the one function
(`access.policy.is_visible`) whose singularity is this project's whole safety
argument.

## Decision

**The holding kind is declared, never derived.** `Record.holding_kind` is a
`HoldingKind` (`digital` / `physical` / `physical_with_surrogate`). Nothing infers it
from an empty payload list. `serialize_record` refuses a record whose declaration
disagrees with what it carries — a `physical` record with payloads, a
`physical_with_surrogate` with none, a `digital` record carrying a physical
description or a custody field — at the same chokepoint that refuses an in-memory
identity.

**The migration is an absence.** `holding_kind` and `physical` are omitted from the
serialized manifest at their defaults, exactly as `PremisEvent`'s optional links are,
so every record written before this feature serializes to the bytes it always did: no
stored digest moves, no bag needs a reseal, no data is rewritten. A manifest with no
`holding_kind` reads as `digital`. An *unreadable* one is refused rather than
defaulted — ADR 0018's rule, one layer down.

**Fixity gets a fourth state, and only two functions may return it.**
`FixityStatus.NOT_APPLICABLE` means "there was nothing to check, by declaration",
which is a different fact from `UNVERIFIED` ("nothing was checked"): the second is an
alarm a steward must act on, the first is the healthy resting state of a catalogue
entry. Folding them together would either alarm on every shoebox forever — which
retires the alarm — or silence it where it matters.

`AuditReport.status` and `overall_status` keep their meaning and their three states:
they answer "did the stored bytes match their manifest", which has no not-applicable
answer, and they have thirteen callers that were written for three states.
`holding_status(kind, report)` and `overall_holding_status(pairs)` are the new pair
that answer the *content* question, and are the only functions that return the fourth
state. A test asserts that separation rather than describing it.

**A failure always dominates the kind.** `holding_status` checks for `FAILED` before
it looks at the kind. Otherwise `not_applicable` becomes a place to hide damage: a
physical record whose `record.json` was altered would read as "nothing to check", and
an attacker with disk access could relabel a rotted digital record as `physical`. The
relabelling attack fails for a second reason too, and it is worth stating: the bag
still *declares* the payload in its payload manifest, so `validate_bag` still fails on
it.

**Custody is not a new field type. It is two sealed `Field`s.**
`custody.location` and `custody.custodian` are ordinary fields with ordinary
policies, defaulting to `SEALED_UNTIL` with no date like every other field. They
therefore inherit the one disclosure decision point, at-rest encryption for an
absolute `SEALED`, the redaction verb, the withheld-reason vocabulary, and every
existing no-outing sentinel — with **no new code on the read path**. This is the
central safety decision in the ADR: the cost is that custody is not structurally
typed and needs a reserved-name rule; the benefit is that there is still exactly one
place to audit.

**What a viewer is told about custody is a three-state word.**
`CustodyState` is `DISCLOSED` / `WITHHELD` / `NOT_RECORDED`, derived from what
disclosure actually did rather than passed in, so it cannot disagree with the values
beside it. All three are rendered to every viewer, including an anonymous one.
Collapsing `NOT_RECORDED` into `WITHHELD` would publish *somebody is looking after
this* over a record where nobody is — the same defect as reporting a physical holding
as verified, one field along. The word says whether a fact was recorded; it never
says what the fact is.

**Surrogates are ordinary digital content attached to a holding that stays
unverifiable.** `Archive.attach_surrogate` stores the bytes, adds them to the bag's
payload manifests (`bag.add_payload`, which refuses to overwrite), moves the record
to `physical_with_surrogate`, and writes a `digitization` PREMIS event linking the
record to the surrogate's content address. The surrogate gets a real fixity result.
The holding's verdict stays `not_applicable`, because *"the scan verified, so the
object verified"* is the single inference this whole feature exists to refuse.

**Three local PREMIS event types**, in the same way `lockdown`, `stand-up` and
`query` are already local: `custody transfer`, `condition check` and `digitization`.
`condition check` is deliberately not spelled `fixity check` — a person's eyes are
not a digest, and a consumer filtering for fixity checks must not pick up a human
judgement. Conversely, the standing statement that a record's fixity is not
applicable **is** a `fixity check` event, with outcome `not-applicable`, written once
per physical record at ingest to the bag's log and to a new archive-level
`logs/holdings.premis.json`. A consumer filtering the log for fixity checks has to
*see* the record and read the answer; an absent event and a not-applicable one look
identical to anything counting successes, and only one of them is a statement.

## Consequences

- `ledger audit` prints a third verdict word, `n/a`, and its summary line now carries
  three counts (`verified`, `not applicable (physical)`, `failed`). An archive whose
  records are all physical reports `NOTHING TO VERIFY` and exits 0. `UNVERIFIED` still
  counts against the exit code exactly as it did before.
- `/healthz`'s **anonymous** payload is unchanged, deliberately. Making it "honest"
  about a physical archive would hand an outsider a second emptiness oracle:
  `degraded` with no failing bag would say the archive holds only undigitized
  material. Every other route to `all_verified: false` also returns 503, so the honest
  verdict goes in the steward-gated block — the same place #219 put the three-state
  one — which gains `bags_verified` and `bags_not_applicable`.
- `succession`'s `fixity_status` will not say `VERIFIED` over an archive of nothing but
  physical holdings, and the runbook a non-ops volunteer reads names the count of
  records nothing can verify. `all_fixity_ok` and `HANDOFF_SCHEMA_VERSION` are
  untouched: the boolean still means "did the stored bytes verify", which for a
  catalogue entry is true and narrow, and a hand-off document for a digital-only
  archive is byte-identical to the one this code produced before.
- **`attestation.build_attestation` is deliberately unchanged**, and this is the
  residual. `fixity_ok` is computed from `AuditReport.status`, so a physical holding
  folds in as verified and `/proof` will say the archive "passed its most recent
  fixity check" over a shoebox catalogue. Changing it means either
  `ATTESTATION_SCHEMA_VERSION` 2 — breaking every third-party verifier — or redefining
  a published, signed field, and that trade is recorded as the owner's at #205. What
  this ADR does instead is state the *scope* of the claim on `/proof`, qualitatively
  and without a count (a count would disclose part of the archive's size): a fixity
  check covers files the archive stores, and no attestation says anything about
  objects it does not.
- `lockdown` is unchanged, on purpose. Its shred gate requires a verified replica
  before destroying the local vault; for a physical holding the replica holds the
  catalogue entry, which is everything ledger ever had for that record, so nothing is
  lost that the gate was protecting.
- `ledger replicas`, `ledger heal` and `ledger verify-backup` keep their booleans —
  the replica or backup of a physical record's bag really is complete — and each
  prints one sentence saying what was and was not covered. Replicating a description
  three times does not make the object it describes any safer, and a steward reading
  three green rows would otherwise conclude that it does.
- The controlled `PhysicalFormat` vocabulary is closed, with `other` as the honest
  escape. Its twenty labels are translated in all four catalogs: these are ledger's
  own words, not a steward's, so unlike a record's descriptive prose they can be
  translated, and leaving them English would put `audio-cassette` in the middle of an
  Arabic page — the defect #217 closed one route earlier.
