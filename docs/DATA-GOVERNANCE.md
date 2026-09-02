# Data governance — ledger

Last verified: 2026-08-22 · Recheck cadence: on a new data field/source, a
license or retention change, an exposure incident, or quarterly.

Ledger processes **L3 identity-sensitive data**. Its purpose is long-term
preservation, so time-limited deletion is not an honest universal default:
contributors choose what is preserved and may tighten consent or request takedown
at any time. This file defines the local policy values required by the shared Data
Governance Standard without restating that standard.

## Stores and classification

| Store | Tier | Contents | Retention | Deletion control |
|---|---|---|---|---|
| Archive bags / payloads | L3 where content could identify or endanger a person; otherwise L1/L2 | contributed records, identity-free preservation metadata, consent policy | contributor-directed; indefinite while consent remains | takedown tombstone + delete from primary and every reachable replica; retry offline replicas |
| Identity vault | L3 | optional name/contact/notes keyed by opaque `identity_ref` | only while the contributor permits identity recovery | explicit vault-entry deletion; key destruction makes the whole vault unrecoverable |
| Grants and revocations | L3 capability data | access capabilities, revocation state | while needed to enforce active access and audit revocation | revoke/delete grant; never publish token material |
| Consent/moderation/PREMIS logs | L2/L3 depending on context | decisions and events without contributor identity inline | retained with the related archival record for accountability | takedown entries remain as identity-free evidence; sensitive free text is prohibited |
| Backups | inherits the highest tier, L3 | encrypted snapshot of the archive and encrypted vault | at most one backup cycle beyond the operator's live-data policy; reference schedule keeps 14 nightly copies | `ledger backup --keep N` plus matching off-box rotation |

The baseline sensitive inventory is credentials, vault/backup keys, grant tokens,
direct contact or identity, precise location, sealed payload/field values, and any
combination that can reveal contributor identity. Those values must not enter
logs, filenames, public errors, metrics, or unencrypted backups. The no-outing
sentinel tests are the enforcement of that classification at output boundaries.

## Provenance and lineage

The human contribution source is documented in
[`docs/data/community-contributions.md`](data/community-contributions.md).
Each ingest receives a generated record identifier, ingest timestamp, fixity
manifests, BagIt metadata, and PREMIS events. The archive does not scrape or bundle
third-party civic datasets. A future external ingest source must add its own data
card before code using it merges.

## Retention, erasure, and replicas

Indefinite preservation is permitted only while the contributor's recorded
consent remains in force. A takedown creates a durable, opaque tombstone; removes
the primary copy; propagates to each configured replica; and records confirmation
receipts. An offline replica remains visibly pending and is deleted on the next
replication sweep. Operators treat full replica confirmation as completion, not
the initial primary deletion.

## Backup and recovery

[`BACKUP-RUNBOOK.md`](BACKUP-RUNBOOK.md) defines encrypted backups, separate
custody of the vault key and backup passphrase, nightly rotation, off-box copy,
and quarterly restore drills. Tests exercise encrypted backup, restore, and
fixity verification. The reference local-first objective is **RPO 24 hours** and
**RTO 48 hours**; adopters record stricter or looser values in their deployment
runbook and must not claim the reference values without operating the schedule.

## Third-party processors (AI layer, opt-in, ADR 0013)

Ledger has one optional third-party data processor: the model provider a
`ledger ai-describe`/`ledger ai-ask` call sends disclosed evidence to
(Anthropic's API or AWS Bedrock, per `LEDGER_AI_BACKEND`), when a steward has
set `config.ai.enabled = true`. This is a fundamentally different kind of
entry in this table than the rest of the file, and it is written up
separately rather than folded into the classification table above for a
reason: **whether it is acceptable at all is a DECISION NEEDED, not a closed
question this document can answer on the project's behalf.**

What travels: exactly what the requesting viewer's own `Grant` already
permits for the record(s) in scope — title, Dublin Core, visible field
values, visible payload metadata/transcripts, identity-free PREMIS event
summaries. Never a contributor identity, never anything above the requester's
tier (`docs/THREAT-MODEL.md` §4.8 and trust boundary 7). Never logged or
persisted by ledger itself beyond the in-process rate-limit counter
(`ledger.ai.limits`), which stores only a request count per day, not content.

What ledger does not and cannot control: the provider's own data handling and
retention policy for a request while it is processed. `docs/THREAT-MODEL.md`
states this as a genuine residual risk, not a closed guarantee.

**The open question a community must answer before enabling this feature:**
does the consent language a contributor agreed to when they gave their
account to this archive cover "an opt-in AI feature may send the disclosed
portions of your record to a third-party model provider for one request, at
a steward's discretion"? For most tools in this portfolio, sending data to a
reviewed vendor is an ordinary, already-covered operational decision. For
ledger it is not automatically that: `docs/GOVERNANCE.md`'s consent model
predates any third-party processor existing in this system at all, and the
people this archive serves are named throughout `docs/THREAT-MODEL.md` as
people for whom "a third party learned this" can itself be a safety event —
not merely a privacy inconvenience.

This is why the feature ships **off by default** and the ADR does not resolve
this question — it is deliberately left to each community's own governance,
informed by this section, `docs/THREAT-MODEL.md` §4.8, and ADR 0013. A
community wishing to enable it should update its consent language and
`docs/GOVERNANCE.md` first, and record that decision the same way any other
policy change is recorded (a PREMIS `access-policy change` event class exists
for exactly this kind of accountable decision).

## Breach review

An L2/L3 exposure follows [`INCIDENT-RESPONSE.md`](INCIDENT-RESPONSE.md). The
postmortem names the tier, affected stores/replicas/backups, retention-control
behavior, and whether contributor notification needs accountable-owner or legal
review.
