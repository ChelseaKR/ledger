# Ethics / consequence scan — ledger

> **Draft prepared: 2026-07-07 · Human reviewer: pending · Recheck cadence after
> approval: per release.** This automated draft must not be represented as Chelsea
> Kelly-Reif's review or sign-off until she explicitly records that decision here.

This is the committed, dated **draft** for the artifact that
[`docs/ROADMAP.md`](../ROADMAP.md#open-conformance-gaps) tracks as RTF-01. The
substance already existed, spread across three living documents; this file does
not duplicate that substance; it organizes it for human review so a future auditor
does not have to reconstruct
"was this actually reviewed, and when, and by whom" from prose scattered across
unrelated files.

Source material, read in full alongside this scan:

- [`README.md`](../../README.md) — what ledger is, who it is for, and the "Hard
  rules" it will not break (no un-consented disclosure, no telemetry, no
  government/institutional customer relationship).
- [`docs/THREAT-MODEL.md`](../THREAT-MODEL.md) — adversary-by-adversary analysis
  (device seizure, subpoena, doxxing, malicious steward, hostile replica host,
  network surveillance, inference attacks), each with guarantee, mechanism, and
  residual risk stated explicitly.
- [`docs/GOVERNANCE.md`](../GOVERNANCE.md) — stewardship, moderation, and
  dispute-resolution process; who decides, and what a contributor can appeal.
- [`docs/RESPONSIBLE-TECH-AUDITS.md`](../RESPONSIBLE-TECH-AUDITS.md) §A — the
  living ethics-and-responsibility section this scan formalizes.

## 1. Purpose and scope

ledger is a self-hosted archive for community-contributed records (oral
histories, ephemera, organizing material) for communities where being identified
as the source of a record can carry real risk — outing, harassment, legal
exposure, or worse. This scan asks the standard consequence-scan questions
against that specific threat profile: who could be harmed, by what use
(intended or not), and what stands between that use and an actual harm.

In scope: the archive's core disclosure model (access grants, redaction,
moderation, replication/takedown propagation) as implemented in this
repository. Out of scope: any deployer's operational choices (who they grant
steward access to, what content they choose to ingest) — those are governance
decisions the software enables and constrains but does not make; see
[`docs/GOVERNANCE.md`](../GOVERNANCE.md).

## 2. Worst-case misuse

**An archive built to protect vulnerable contributors is turned into a tool
that identifies them.** Concretely: a hostile steward, a subpoena or other
legal-compulsion request, a doxxer who gains partial access, or a seized
device, each turned into a vector that outs someone who is not out,
undocumented, or organizing somewhere that is dangerous to be visible. This is
the single worst case ledger's design is organized around, and it is not
hypothetical for the communities this project targets.

**Mitigations already in place:**

- Contributor identity is stored separately from the record content, in an
  encrypted vault, and is grant-gated (`docs/adr/0003*`, referenced from
  `THREAT-MODEL.md` §4).
- No view, export, log line, filename, or error message surfaces authorship
  without an explicit grant.
- Records and fields are sealed by default; disclosure is opt-in per field, not
  opt-out.
- The malicious-steward case is modeled explicitly (`THREAT-MODEL.md` §4.4),
  with dual-control (`dualcontrol.py`) and an append-only moderation log plus
  the PREMIS event chain as the accountability backstop — a hostile steward
  cannot act invisibly, only quietly for a time before the record trail
  surfaces it.

**Residual risk, stated plainly, not claimed away:** a steward who never
triggers dual-control and is never audited can still act for a period before
detection. This is tracked as an open item (a distinct, regenerated-per-release
residual-risk register — RTF-06/QM-09 in `docs/ROADMAP.md`) rather than
asserted as solved.

## 3. "Works as intended" harm

Even a fully consenting, correctly configured deployment can still cause harm
that is not a bug: a downstream consumer of a record the contributor marked
*public* (a partner organization, a researcher, a data aggregator) can
cross-reference it against other public data ledger does not control and does
not know exists, and re-identify someone the contributor never intended to be
identifiable outside the archive's own access model. `THREAT-MODEL.md` §4.7
states this as an inference risk ledger cannot fully close by design — it is
disclosed to deployers rather than implied away, and it is the reason the
default access level is the most protective one, not the most convenient one.

## 4. Non-goals (things ledger deliberately does not do)

- No feature that surfaces "who contributed this" without an explicit grant,
  under any circumstance, including to stewards by default.
- No analytics, telemetry, or engagement optimization of any kind (README
  "Hard rules"; `RESPONSIBLE-TECH-AUDITS.md` §C).
- No government or institutional customer relationship (README, `NOTICE`) —
  the communities this targets are frequently the ones such relationships put
  at risk.
- No silent takedown or disclosure: every state change is a logged,
  attributable PREMIS event a contributor or steward can trace
  (`docs/GOVERNANCE.md`).

## 5. Kill-switch and reversibility

If ledger the *project* is abandoned or a specific deployment must stop
immediately, the archive itself remains plain BagIt-format files a community
can keep reading with ordinary tools, independent of ledger's own code
continuing to run (ADR 0004/0005, `docs/CONTINUITY.md`). There is no hosted
dependency that must fail closed *or* stay open for the data to remain usable
— the software can disappear without the archive disappearing with it.

## 6. Accountability

- **Accountable owner:** Chelsea Kelly-Reif.
- **Escalation / dispute path:** `docs/GOVERNANCE.md` (moderation appeal
  process).
- **Re-trigger conditions** that require re-running this scan before the next
  scheduled per-release cadence: any change to the access-grant model
  (`src/ledger/access/`), the identity vault, replication/takedown propagation,
  or the addition of any analytics/telemetry/LLM-inference feature (the last of
  these would also flip AI-Evaluation from N/A — see
  `docs/adr/0006-standards-applicability.md`).

## 7. Sign-off

| Reviewer | Role | Date | Verdict |
|---|---|---|---|
| Pending | Accountable owner / maintainer | — | Human review and verdict required. This row must be completed by the reviewer, not by an automated contributor. |

This scan does not replace `docs/THREAT-MODEL.md` or `docs/GOVERNANCE.md` — it
is a review-ready draft. RTF-01 remains open until the accountable owner records a
dated verdict above.
