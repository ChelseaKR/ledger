# Multiyear plan (MP-01 … MP-14) — a two-to-three-year arc

> The third planning document in this repo, and the narrowest of the three.
> [`ROADMAP.md`](ROADMAP.md) tracks standards conformance gaps against the portfolio's
> `STANDARDS/`. [`RESEARCH-ROADMAP.md`](RESEARCH-ROADMAP.md) turns a synthetic persona
> panel into an evidence-tagged feature backlog. This file does neither. It **sequences
> what is already written down** — open issues, unclosed backlog rows, and the open
> edges the ADRs recorded on their way past — into phases, and says for each one what it
> delivers, what it depends on, and what would tell you it is done.
>
> It invents no direction. Every row cites the document or issue that asked for it. Work
> the record shows as rejected or out of scope is a decision with reasons attached and is
> not restated here as a gap; see *What this plan does not propose*.
>
> **Last assembled: 2026-08-27** · Recheck cadence: per release and quarterly.

## Framing — why this file exists and what bounds it

Three facts shape the whole arc, and none of them is a technical one.

**The maintainer count is one.** `CONTINUITY.md` §1 records it as the bus-factor finding
and says plainly that "a second maintainer is a goal, not a fact." Every engineering
phase below is sized for one person. That is why the arc is two to three years for a
body of work that a funded team would sequence in months, and why the co-maintainer item
sits in the people-blocked section rather than being assumed away and used to shorten it.

**Four of the ten open issues cannot be closed by engineering at all.** The first release
(#80) needs a PyPI account and an owner's decision on a signer identity; the manual
accessibility evidence (#81) needs an assistive-technology user; the risk reviews (#82)
need a named accountable owner and an independent cryptographer; the workflow review
(#99) needs a community archivist to volunteer. These are listed, dated, and left
un-sequenced, because a plan that schedules other people's consent is a plan that lies.

**Two named safety items are gated on the same external review.** ADR 0011 caps SEALED
payloads at 64 MiB and states the real fix is chunked at-rest AEAD framing, held because
"FIX-11 already records that the sealing layer's crypto **must not ship on self-review**."
RM1 (threshold vault key) is held by the same gate. Neither is scheduled against a date.

So the shape of the arc is: **finish everything that is genuinely unblocked first**,
because it is the only work whose completion date is under the project's own control;
then the depth work that leans on it; and hold the crypto-gated safety residuals in a
section that says why they are held rather than pretending they are next.

Every phase inherits the constraints the project already published: the runtime stays
stdlib-plus-`cryptography` (ADR 0005), it must still run on one cheap box, and the
no-outing audit, fixity audit, and accessibility gate stay green.

## The phases

Priority and effort use `RESEARCH-ROADMAP.md`'s scale: **P0** now · **P1** next ·
**P2** soon · **P3** opportunistic; **S** afternoon · **M** day or two · **L** week+.

| ID | Phase / item | Pri | Effort | Grounded in |
|---|---|---|---|---|
| MP-01 ✅ | **The recorded reason becomes durable.** `ModerationLogStore`, every live write path, two steward-gated read surfaces | P0 | L | [#156](https://github.com/ChelseaKR/ledger/issues/156); `GOVERNANCE.md`, `THREAT-MODEL.md` §4.4 · **merged as [#159](https://github.com/ChelseaKR/ledger/pull/159), without the ADR its own rules require — see MP-07** |
| MP-02 ✅ | **`TombstoneStore` gets the file lock every sibling store has.** Measured 39 of 40 tombstones lost at 40 concurrent takedowns; a lost tombstone means a reattaching replica is never told to delete its stale copy | P0 | S | [#155](https://github.com/ChelseaKR/ledger/issues/155); Hard Rule 4 · **[corroborates README Hard rules]** · **shipped, see below** |
| MP-03 ✅ | **`ProposalStore` and `SubmissionQueue` stop reading corruption as an empty history.** Both swallowed `(OSError, ValueError)` and returned `[]`, and the next `add()` then rewrote the file | P0 | S | [#154](https://github.com/ChelseaKR/ledger/issues/154) · **[corroborates ADR 0014 decision 1]** · **shipped, see below** |
| MP-04 | **Per-module coverage floors; retire the pooled figure.** `--fail-under` gates the TOTAL row, so the scoped 95% passes while `grants.py` (92%) and `consent.py` (91%) sit under it | P1 | M | `Makefile` `cov:`; ADR 0014 open edge · **[NET-NEW]** |
| MP-05 | **Retire the eight `C901` waivers and reach the 90% published-library floor.** The waived functions are the public GET/POST route tables, `ingest_sip`, `validate_bag`, WCAG element/rule checks, CLI ingest options, and untrusted-form validation | P1 | L | [#83](https://github.com/ChelseaKR/ledger/issues/83) · **[corroborates ROADMAP CQ-05/08]** |
| MP-06 | **Local Semgrep parity in `make verify`.** CI is the gate of record; a contributor gets no pre-push signal, unlike `osv` and `secret-scan` which both have local targets | P2 | S | `ROADMAP.md` open gaps, SEC-11/13 CICD-13/27, "no issue filed yet" |
| MP-07 | **Documentation truth pass, and the audit the ADRs imply.** ADR 0006 is superseded by 0009 but was never marked, against ADRs 0000/0001's own bidirectional rule; `DEFINITION_OF_DONE.md` still describes eleven required checks and calls Semgrep/OSV non-blocking, which the 2026-08-21 ruleset pass changed to thirteen and blocking; #99's body links a deleted branch; **#159 changed a safety guardrail and added a coverage threshold without the ADR ADR 0000 requires, and merged that way**; and the other JSON stores have not been swept for whether they too read damage as an empty collection | P1 | M | ADR 0000; `ROADMAP.md` 2026-08-21 pass; ADR 0014 open edges · **[NET-NEW]** |
| MP-08 | **Type the untyped PREMIS event writers, and label `media_type_basis` in browse.** Consent changes, takedowns, and replication still emit `linkingObjectIdentifierType: local`; the identification basis reaches the API but is not rendered, because it needs a user-facing string in four locales | P2 | M | ADR 0012 and ADR 0010, both "a follow-up" in their own consequences |
| MP-09 | **Format migration / normalization for at-risk media.** RM4 identifies and flags; nothing migrates. Pairs with the registry ADR 0010 deliberately left non-convergent | P2 | L | `RESEARCH-ROADMAP.md` EX12; OAIS Preservation Planning · **[corroborates RESEARCH-ROADMAP EX12]** |
| MP-10 | **Re-identification with an explicit PREMIS supersession shape.** ADR 0012's contradiction guard is the place it has to be added, and exists partly to stop one appearing by accident | P3 | L | ADR 0012 consequences, verbatim: "Re-identification does not exist" |
| MP-11 | **Community-authored graduated access labels, extended *through* `access/`.** The backlog's own warning is that this must not become a back door around the single disclosure decision point | P2 | L | `RESEARCH-ROADMAP.md` EX5 · **[corroborates RESEARCH-ROADMAP EX5]** |
| MP-12 | **Federation and the signed deposit bundle.** OAI-PMH harvest in/out over public records only; a bundle a partner institution can verify on receipt | P3 | L | `RESEARCH-ROADMAP.md` EX2, EX8 |
| MP-13 | **Chunked at-rest AEAD framing; raise or remove the 64 MiB SEALED cap.** Supersedes ADR 0011 at that point, not before | P0 when unblocked | L | ADR 0011; FIX-11 · **held, see below** |
| MP-14 | **Threshold / split-knowledge vault key.** The sharpest residual the threat model flags as unimplemented: "today the key is a single secret" | P0 when unblocked | L | `RESEARCH-ROADMAP.md` RM1; `THREAT-MODEL.md` §4.2/§4.4 · **held, see below** |

## Sequence

Four phases, each a coherent unit of review rather than a calendar quarter. Dates are
deliberately absent: a solo maintainer's throughput is not a schedulable quantity, and
the arc is two to three years at the cadence the commit history actually shows.

**Phase one — the documented control that did not run.** MP-01.
*Theme: the repository's central claim is that its guarantees are enforced rather than
described. A guardrail that runs, passes, and cannot produce the thing three documents
say it produces is the one defect class that undermines every other claim, so it goes
first.* **Delivered in this PR.**

**Phase two — the rest of the silent-loss class, and the gate that would have caught it.**
MP-02 ✅ · MP-03 ✅ · MP-04 · MP-07.
*Theme: MP-01 built one store correctly and its docstrings cite the two stores that are
still wrong. Fixing them is finishing a thought, not starting one. MP-04 belongs here
because a pooled coverage figure is the same defect in the measuring instrument: a number
that is green and structurally unable to report the thing it appears to report. MP-07 is
the prose equivalent.*
**Depends on:** nothing. Every item is reachable today.
**Done when:** 40 concurrent takedowns record 40 tombstones ✅; a truncated
`proposals.json` raises instead of reading as an empty history ✅; every module in the
security core reports against a floor of its own with no pooled `--include` remaining in
`make cov` (MP-04, not yet); ADR 0006 carries `Superseded by 0009` and 0009 points back
(MP-07, not yet); `DEFINITION_OF_DONE.md` names thirteen required checks (MP-07, not
yet).

**Phase three — the quality floor the standards actually ask for.** MP-05 · MP-06 · MP-08.
*Theme: with per-module floors in place, #83's remaining half stops being a pooled
average to chase and becomes eight named functions to refactor, each with a target of its
own. The two documentation follow-ups the ADRs recorded ride along, because both are
blocked on the same four-locale translation discipline that MP-08 has to establish
anyway.*
**Depends on:** MP-04. Refactoring the route tables without per-module floors means
losing coverage on `server.py` and learning about it from a pooled total that moved 0.3
points.
**Done when:** zero `C901` waivers remain, branch coverage clears 90% against
`pyproject.toml`'s `fail_under`, `make verify` runs Semgrep locally, and every PREMIS
event writer emits a typed linking identifier.

**Phase four — preservation depth and community-defined access.** MP-09 · MP-10 · MP-11 · MP-12.
*Theme: the mission work. RM4 taught the archive to say a format is at risk; nothing yet
acts on that. EX5 lets a community author its own access vocabulary instead of choosing
from ours. Both are large, both are hypotheses the research roadmap says to confirm with
real users before shipping, and neither should start while the floor beneath them is
still being repaired.*
**Depends on:** phases two and three, and — for anything that ships — the real-user
validation `RESEARCH-ROADMAP.md` requires: "Confirm before you build; ship nothing that
can endanger a contributor on the strength of a synthetic exercise alone."
**Done when:** an at-risk payload can be migrated with a PREMIS-legal supersession
record; a community can define an access label that resolves through `access/policy.py`
and nowhere else; a partner can verify a deposit bundle on receipt.

## Held, not scheduled: blocked on people rather than engineering

Nothing in this section has an engineering plan, because for each one the next step is a
human act that automation may prepare evidence for but must not perform. They are listed
so the arc above is not mistaken for the whole picture.

| What | Who it needs | Recorded where |
|---|---|---|
| First trusted release: PyPI Trusted Publisher, protected environment, approved signer identity, first signed tag | The project owner, plus a PyPI account. "I have no PyPI credentials and did not attempt this"; the signer is "Chelsea's decision to make and record, not mine to assert on her behalf" | [#80](https://github.com/ChelseaKR/ledger/issues/80); `docs/RELEASE-0.1.0.md` |
| Harden-Runner `egress-policy: block` on the 8 `release.yml` jobs | Nobody, but nothing to derive an allowlist from until #80 runs once: "writing one now would be a guess dressed up as evidence" | [#78](https://github.com/ChelseaKR/ledger/issues/78) |
| Dated NVDA+Firefox and VoiceOver+Safari walkthrough | An assistive-technology user. "What remains is exactly what no scan can produce" | [#81](https://github.com/ChelseaKR/ledger/issues/81); `docs/accessibility/MANUAL-REVIEW-CADENCE.md` |
| Accountable-owner and independent sign-off on the ethics, bias, DPIA, threat-model, and residual-risk artifacts | A named accountable owner; an independent cryptography/security reviewer. "Automation may prepare evidence but must not impersonate human sign-off" | [#82](https://github.com/ChelseaKR/ledger/issues/82); `docs/audits/residual-risk-register.md` RR-07 |
| Commissioned crypto design review of the sealing layer (FIX-11), which gates MP-13 and MP-14 | An external cryptographer | ADR 0011; `docs/audits/crypto-design-review-sealing-layer.md`, "Date reviewed: (not yet scheduled)" |
| Community-archivist review of the synthetic workflow | One community archivist or mutual-aid steward, 60 to 90 minutes | [#99](https://github.com/ChelseaKR/ledger/issues/99); `docs/reviews/community-archivist-pilot.md` |
| A second maintainer (the bus-factor finding; no MP id, because it is not work this project can schedule) | Someone who contributes, demonstrates the safety mindset, helps with review and triage, and is invited | `CONTINUITY.md` §3; `RESEARCH-ROADMAP.md` RM9 |
| Real-user validation before any P0 in phase four ships | At-risk contributors, a preservation librarian, a community archivist, a broke collective on real cheap hardware, screen-reader and multilingual readers | `RESEARCH-ROADMAP.md`, *Validate with real users* |

The dependency chain worth stating once: **#82 and #81 gate #80, which gates #78.** Four
of the six open standards-conformance rows are downstream of two human sign-offs.

## What this plan does not propose

The record shows these as decided, not missing. They are named here so a future reader
does not read their absence as an oversight and re-open them by accident.

- **An AI, LLM, or model component.** ADR 0009 declares AI Evaluation `N/A` and arms a
  trigger: the first model dependency reopens the decision, with the eval harness,
  red-team suite, and groundedness thresholds in place *before* that feature merges. An
  optional, opt-in AI layer is under review on its own branch with its own ADR (#152);
  this plan takes no position on it and does not assume it lands.
- **A funding rail — sponsorship, grants, a paid tier, an endowment.** `.github/FUNDING.yml`:
  "A funding rail introduces exactly the outside interest that governance model exists to
  exclude. The absence of a funder is a property of the design, not an oversight."
- **A hosted service or a maintainer-operated production deployment.** `README.md`'s
  "What this is not". ADR 0009 additionally requires re-evaluating Observability from
  Tier C to Tier A before any such launch.
- **A second runtime dependency.** ADR 0005. Adding one is a new ADR, weighed against the
  same forces and checked for AGPL compatibility.
- **OTel tracing, metrics, SLOs, or alerting** (Tier C ruling), and **the `--log-format json`
  flag**, which `README.md` explicitly records as not a gap.
- **A `--fix` mode or an OS-level installer for `ledger checkup`.** EX6 shipped advisory-only.
- **Per-cue disclosure granularity finer than the whole payload**, and **speech-to-text**.
  Both deliberately deferred in `RESEARCH-ROADMAP.md` with the open questions stated.
- **`403 Forbidden` on a sealed route.** ADR 0007: it leaks existence, "which is the
  oracle ledger is specifically built to deny."
- **A prose-drift scanner over the docs.** `CONTRIBUTING.md`: "a noisy tripwire trains
  reviewers to ignore it."
- **Maintainer authority over any deployed archive.** `CONTINUITY.md`: continuity of the
  code and governance of an archive are deliberately separate.

## Honest limits

This plan is a **sequencing instrument, not a commitment**, and it is assembled from
documents rather than from users. It inherits every caveat `RESEARCH-ROADMAP.md` states
about its own synthetic basis, and adds two of its own.

The first is that phases two and three are the only part of this arc whose completion
date is under the project's control, and even they are sized against a maintainer count
of one. The second is that the two items with the highest safety value — MP-13 and MP-14
— are the two this plan cannot schedule at all, which means the arc's most important
year may be the one in which an external cryptographer becomes available, and no amount
of sequencing brings that forward.

Read the phase boundaries as dependency statements, not as dates. Where a phase says
"done when", that sentence is meant to be falsifiable; if it is not, it is a defect in
this document.

## Implementation status — 2026-08-27

**MP-01 is merged upstream, not by this branch.** `ModerationLogStore` and its wiring
landed as [#159](https://github.com/ChelseaKR/ledger/pull/159) and closed
[#156](https://github.com/ChelseaKR/ledger/issues/156). It merged without an ADR, which
ADR 0000 requires for a change to a safety guardrail or a coverage threshold; that debt
is recorded in MP-07 rather than repaired here, because re-opening merged work is not
what this branch is for.

**MP-02 and MP-03 shipped here.** `TombstoneStore.add` and `.confirm` now hold
`ledger._filelock.file_lock`; `ProposalStore._read` and `SubmissionQueue._read` raise on
a damaged store instead of returning an empty list; `/steward` reports an unreadable
review queue in four languages rather than rendering it as an empty one. Recorded as
[ADR 0014](adr/0014-json-stores-fail-closed-and-serialize.md).

Measured before the change, three trials of 40 concurrent takedowns: **1 of 40**
tombstones survived each time, 34 to 37 writers raising. After: 40 of 40, no writer
raising. A `disclosure`-marked test asserting the old empty-read behaviour was replaced
by its inverse.

Verify: `make verify` green, 1274 tests; `make cov` total 89.05% against the 88% floor,
the pooled scope unchanged at 95%, and `moderate.py` (90%), `tombstones.py` (89%) and
`review.py` (97%) each against a floor of its own.

**MP-04 through MP-14 are planned only.** Nothing else in this document is built.
