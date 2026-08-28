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
| MP-04 ✅ | **Per-module coverage floors; retire the pooled figure.** `--fail-under` gated the TOTAL row, so the scoped 95% passed while `grants.py` (92%) and `consent.py` (91%) sat under it. Both raised to 100% and 97% rather than the floor lowered to meet them | P1 | M | `Makefile` `cov:`; ADR 0014 open edge · **[NET-NEW]** · **shipped, see below** |
| MP-05 | **Retire the eight `C901` waivers and reach the 90% published-library floor.** The waived functions are the public GET/POST route tables, `ingest_sip`, `validate_bag`, WCAG element/rule checks, CLI ingest options, and untrusted-form validation | P1 | L | [#83](https://github.com/ChelseaKR/ledger/issues/83) · **[corroborates ROADMAP CQ-05/08]** |
| MP-06 ✅ | **Local Semgrep parity in `make verify`.** CI is the gate of record; a contributor got no pre-push signal, unlike `osv` and `secret-scan` which both have local targets. Shipped as a target, **not** as a locked dependency: pinning semgrep imports 4 High-severity advisories via `click` and `mcp` that it pins and that cannot be bumped independently | P2 | S | `ROADMAP.md` open gaps, SEC-11/13 CICD-13/27 · **shipped, see below** |
| MP-07 ✅ | **Documentation truth pass, and the audit the ADRs imply.** ADR 0006 is superseded by 0009 but was never marked, against ADRs 0000/0001's own bidirectional rule; `DEFINITION_OF_DONE.md` still describes eleven required checks and calls Semgrep/OSV non-blocking, which the 2026-08-21 ruleset pass changed to thirteen and blocking; #99's body links a deleted branch; **#159 changed a safety guardrail and added a coverage threshold without the ADR ADR 0000 requires, and merged that way**; and the other JSON stores have not been swept for whether they too read damage as an empty collection | P1 | M | ADR 0000; `ROADMAP.md` 2026-08-21 pass; ADR 0014 open edges · **[NET-NEW]** |
| MP-08 ◑ | **Type the untyped PREMIS event writers** ✅ **, and label `media_type_basis` in browse** (declined, not deferred). 18 writers across 6 modules named an object without saying what kind it was; all now do, and a structural test refuses the next one. The browse label is a *decision already taken against*, not a gap: see the correction below | P2 | M | ADR 0012's follow-up ✅; ADR 0010 §"The browse UI is not yet labelled" · **half shipped, see below** |
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
MP-02 ✅ · MP-03 ✅ · MP-04 ✅ · MP-07.
*Theme: MP-01 built one store correctly and its docstrings cite the two stores that are
still wrong. Fixing them is finishing a thought, not starting one. MP-04 belongs here
because a pooled coverage figure is the same defect in the measuring instrument: a number
that is green and structurally unable to report the thing it appears to report. MP-07 is
the prose equivalent.*
**Depends on:** nothing. Every item is reachable today.
**Done when:** 40 concurrent takedowns record 40 tombstones ✅; a truncated
`proposals.json` raises instead of reading as an empty history ✅; every module in the
security core reports against a floor of its own with no pooled `--include` remaining in
`make cov` ✅; ADR 0006 carries `Superseded by 0009` and 0009 points back ✅;
`DEFINITION_OF_DONE.md` names thirteen required checks ✅.

**Phase three — the quality floor the standards actually ask for.** MP-05 · MP-06 ✅ · MP-08 ◑.
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

**MP-04 shipped here too.** The pooled
`coverage report --include=<four modules> --fail-under=95` is gone from both `make cov`
and CI. `tools/check_coverage_floors.py` gates each of the 8 security-core modules on
its own measured value, reports every violation rather than stopping at the first, and
refuses two shapes of drift the pooled report could not see: a security-core module with
no floor, and a floor naming a module that no longer exists. `grants.py` (92% to 100%)
and `consent.py` (91% to 97%) were raised to meet the published 95% rather than the
published number lowered to meet them. Recorded as
[ADR 0015](adr/0015-per-module-coverage-floors.md).

Verify: `make verify` green, 1309 tests; `make cov` total 89.25% against the 88% floor,
and 8 modules each against a floor of its own with no pooled figure remaining.

**MP-08's first half shipped here.** Eighteen PREMIS writers across six modules named
an object without saying what kind of identifier it was, so every consent change,
takedown, redaction, replication, quarantine, correction and reading-room query
serialised as the uninformative `local` in XML. ADR 0012 recorded typing them as a
follow-up; this is it. The vocabulary gains `ledger-bag` and `ledger-proposal`, and a
structural test over the package refuses the next untyped writer by file and line.
Recorded as [ADR 0017](adr/0017-every-premis-event-is-typed.md). No event already on
disk changes, and no chain moves.

**MP-06 and MP-07 shipped here too.**

`make semgrep` runs `semgrep scan --config p/ci --error src tests` and joins
`make verify`, in the same CI-authoritative shape as `osv` and `secret-scan`. It is
deliberately **not** a locked dependency, which is where this departs from the closing
condition `ROADMAP.md` originally wrote for the row. Pinning `semgrep==1.145.0` was
tried and reverted: it pins `click 8.1.8` and `mcp 1.16.0`, and OSV-Scanner reports 4
High-severity advisories across those two (PYSEC-2026-2132; PYSEC-2026-1617 / -3482 /
-3483), none bumpable independently. Importing four known-vulnerable packages to
mirror a check CI already runs is a bad trade, and §4 forbids muting the audit gate
instead. Semgrep is therefore an external tool like `gitleaks` and `osv-scanner`.

The truth pass: ADR 0006 carries the `Superseded by 0009` marker ADR 0001 permits and
ADR 0000 implies (0009 had said `Supersedes: 0006` for six weeks, one-way);
`DEFINITION_OF_DONE.md` says thirteen required checks rather than eleven and no longer
lists as outstanding the approvals, signatures, linear history and Semgrep/OSV contexts
that the 2026-08-21 ruleset pass closed; and
[ADR 0016](adr/0016-the-moderation-reason-is-gated-by-placement.md) retroactively
records the #159 decision that merged without the ADR ADR 0000 requires. Five new
tests in `tests/test_adr_integrity.py` make the one-way-pointer defect
unreintroducible.

The store sweep MP-07 called for is **done, and found no further defects.** The other
four sites that read a store and return an empty collection on damage are all correct,
for a reason worth writing down: *empty on damage is a defect only where empty is the
permissive direction.* `attest.py` returns an empty attested set, which keeps every
conditional seal closed; `server.py` returns `None` for an unreadable revocation list,
which denies. Both fail safe. `reading_room_enclave.py` and `identity.py` raise. Only
`ProposalStore` and `SubmissionQueue` had empty meaning *permissive* -- a lost proposal,
a forgotten submission -- and those were fixed in MP-03.

## Phase status, stated exactly

Three different things are not built, and collapsing them into one word would be the
dishonest part. This table separates them.

| Item | Status | Why |
|---|---|---|
| MP-01 | **Built** | Merged upstream as #159 |
| MP-02 | **Built** | This stack |
| MP-03 | **Built** | This stack |
| MP-04 | **Built** | This stack |
| MP-06 | **Built** | This stack, with a documented departure from its original closing condition |
| MP-07 | **Built** | This stack |
| MP-05 | **Not built — tractable, and large** | Eight `C901` waivers over the public GET/POST route tables, `ingest_sip`, `validate_bag`, WCAG element/rule checks, CLI ingest options, and untrusted-form validation, plus ~0.8 points of coverage. `ROADMAP.md` calls it "real, non-mechanical work on the repo's most security-critical paths" and it is. Nothing blocks it but size; a partly-refactored route table is worse than an un-refactored one, so it is left whole |
| MP-08 | **Half built; the other half is gated on a person, not on size** | The writer typing is done (ADR 0017). The browse label is **not** tractable work left undone — ADR 0010 declined it, and this plan mis-filed it as an engineering follow-up. See *A correction to this plan* below. **Unblocked by:** a reviewer for the four locales |
| MP-09 | **Not built — tractable, and large** | A format migration pipeline is new subsystem work (OAIS Preservation Planning), sized **L** in the backlog it comes from |
| MP-10 | **Not built — tractable, and large** | Re-identification needs an explicit PREMIS supersession shape; ADR 0012 says so and put the guard where it will have to go |
| MP-12 | **Not built — tractable, and large** | Federation and a verifiable deposit bundle, both **L** |
| MP-11 | **Not built — gated on people** | `RESEARCH-ROADMAP.md`'s own rule: "Confirm before you build; ship nothing that can endanger a contributor on the strength of a synthetic exercise alone." A community-authored access vocabulary designed without the communities is the failure mode the item exists to avoid. **Unblocked by:** real-user validation, which needs #99's reviewer or equivalent |
| MP-13 | **Blocked** | Chunked at-rest AEAD framing supersedes ADR 0011's 64 MiB cap. ADR 0011: the sealing layer's crypto "must not ship on self-review" (FIX-11). **Unblocked by:** a commissioned external cryptography review, tracked as #82; `docs/audits/crypto-design-review-sealing-layer.md` records "Date reviewed: (not yet scheduled)" |
| MP-14 | **Blocked** | Threshold / split-knowledge vault key (RM1). Same review gate as MP-13. The backlog additionally records a risk this plan will not decide around: a 2-of-N key can *reduce* safety for a small collective, where losing one holder can mean losing the archive. That is a design decision for the owner and the communities, not an implementation detail. **Unblocked by:** #82, then an explicit decision on the N and the recovery story |

Four further items are owner or human actions that `DEFINITION_OF_DONE.md` says
automation must prepare evidence for but never impersonate. They are listed in *Held,
not scheduled* above and are **not** in this table, because they were never
engineering: #80 (first trusted release: PyPI credentials, signer identity), #81
(dated NVDA/VoiceOver walkthrough), #82 (accountable-owner and independent sign-off),
#99 (community-archivist review), and #78, which is blocked on #80 having run once so
an egress allowlist can be derived from observation rather than guessed.

### A correction to this plan

MP-08 was written here as one engineering row with two mechanical halves. That was
wrong about the second half, and the error is worth leaving visible rather than
quietly editing away, because it is the exact mistake this document's framing warns
against: *work the record shows as rejected is a decision with reasons attached, not a
gap.*

ADR 0010 did not defer the browse label. It declined it, and said why:

> **The browse UI is not yet labelled.** `media_type_basis` reaches the record and the
> API but is not rendered as "unverified" in the HTML, because that needs a new
> user-facing string in four locales including Arabic, and **authoring translations
> that nobody can review is its own honesty problem**. The data is there for the UI to
> use.

That is not a size problem. It belongs with MP-11 among the items gated on a person:
what unblocks it is a reviewer for es/fr/ar, not an afternoon. Building it would mean
shipping four unreviewed translations onto a public record page, which is the thing
the ADR refused.

**And this stack did exactly that once, in a smaller way.** MP-07 added
`sw_queue_unreadable` in en/es/fr/ar with no native review, to stop a damaged review
queue from rendering as an empty one. The reasoning was that a steward-gated failure
message is a narrower surface than a public record page, and that the alternative was
a silently wrong state rather than a missing label. That reasoning may be right, but
it is the same trade ADR 0010 declined, made without an ADR of its own, and it should
be reviewed rather than treated as settled. It is recorded here so a reviewer sees it.

One item the record showed as outstanding turned out to be **already done**:
`FIX-01` (AIP revisioning) landed in #50. `Archive.apply_update` calls
`refresh_tag_manifests` and records a PREMIS `VALIDATION` event carrying the digest
transition; a bag re-validates after a lawful update, verified by running one and
comparing every expected digest against its actual. It is not re-listed as a gap.
