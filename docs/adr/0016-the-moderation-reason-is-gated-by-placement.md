# 0016. The moderation reason is gated by placement, not by construction

Status: Accepted

Date: 2026-08-27

Records, after the fact, the decision merged in #159 (closing #156). Written as part
of the MP-07 documentation truth pass, because ADR 0000 requires an ADR for a change
to a safety guardrail or a coverage threshold and that change merged without one.

Numbering note: this ADR is later than 0014 and 0015 while describing an earlier
change. ADRs are dated records of decisions, not a chronology of merges, and
renumbering to interleave would break every citation. 0013 is claimed by the open
AI-layer branch (#152).

## Context

`docs/GOVERNANCE.md` ("How decisions are recorded"), `docs/THREAT-MODEL.md` §4.4, and
`docs/ARCHITECTURE.md` §1.9 each describe `ModerationLog` as the accountable record of
**what, who, why, and to which record**, and each names the required non-empty
`reason` as the control that makes the log a check on a coerced or bad-faith steward
rather than a bare activity feed.

Three of those four facts were durable. The fourth was not. `ModerationLog` was never
instantiated outside a unit test: `moderate._require_reason` validated the rationale at
the boundary, every call site then discarded the returned `ModerationAction` as
`_action`, and the PREMIS event persisted beside it builds its `detail` only from the
*what*. A steward acting for a pretextual reason left a trace that *an* action
happened, never of what they claimed.

Two decisions in the repair are hard to reverse, and ADR 0000 reaches both.

`GOVERNANCE.md` had described the whole log as identity-free **by construction**. That
was true of `actor` (a steward id), of `target_record` (an opaque record id), and of
`action` (a fixed vocabulary). It was never true of `reason`, which is prose a human
types. Making the reason durable is what forces the question: a field no code path
could persist could not leak, and now it can.

Separately, `make cov` gained a coverage threshold for `moderate.py`.

## Decision

### 1. The reason is persisted, and every live decision path writes it

`moderate.ModerationLogStore` keeps the log at `<store>/logs/moderation.json` under the
same three rules as every sibling JSON store: the read-modify-write is serialized by
`ledger._filelock.file_lock`; a read failure raises rather than returning an empty log;
appends chain, so an edit anywhere in history moves the head.

Every path that takes one of these decisions records it: `ledger policy`, `ledger
seal`, `ledger cw`, `ledger takedown`, an executed dual-control `publish`, the steward
console's warn / takedown / submission review, and a contributor's own withdrawal.
`execute_takedown` records *before* it removes anything, which makes its own docstring
("its audit trail of *why* must outlive the data") true for the first time.

### 2. The store is reached by module functions taking an archive, not by `Archive` methods

`moderate` already depends on `ingest.Archive` for `execute_takedown`. `Archive`
importing `moderate` back would make the two cyclic, against the one-way layering
`docs/ARCHITECTURE.md` §1 states. So `Archive` owns only `moderation_log_path`.

### 3. The rationale is gated by placement, and `GOVERNANCE.md` says so

Nothing can validate prose for whether it names someone. A steward can type a
contributor's name into a reason field and no type discipline will stop them. So the
control is stated as what it is: **no ungated surface renders it.** The rationale
appears on `/steward/audit` behind the steward gate and in `ledger moderation list`,
nowhere else, asserted by a merge-blocking `disclosure` test over twelve public
surfaces that first asserts the reason *is* present behind the gate, so it cannot pass
vacuously.

`GOVERNANCE.md` now scopes the by-construction claim to the three fields it was ever
true of.

### 4. `moderate.py` was floored on its own, off the pooled scope

At 90%, reported separately rather than appended to the pooled
`access/*` + `consent.py` + `dualcontrol.py` include list, because `--fail-under`
gates a report's TOTAL row and the module would have read as covered because its
neighbours were. ADR 0015 has since replaced that pooled report entirely and made this
reasoning a property of the gate rather than a judgement repeated by hand.

## Consequences

| | before | after |
| --- | --- | --- |
| moderation decisions persisting a rationale | 0 | every live path: 4 CLI, 1 dual-control, 3 console, 1 contributor |
| `ModerationLog` instantiations outside a unit test | 0 | 1 store, reached from `cli.py` and `server.py` |
| surfaces that can render a steward's prose | 0 (never persisted) | 2, both steward-gated, asserted against 12 public surfaces |
| what `GOVERNANCE.md` claims is identity-free by construction | all four facts | the three that are |

Costs and open edges, stated rather than left to be discovered:

- **A steward can still write an identity into a reason.** This ADR does not claim
  otherwise. It moves the guarantee from a false "by construction" to a true "on no
  ungated surface", and puts a test behind the true one. The residual is governance, as
  `THREAT-MODEL.md` §4.4 already says of a malicious steward generally.
- **The chain narrows §4.4's tamper residual rather than closing it.** An edit that
  does not recompute every following link is detected by `ledger moderation verify`. An
  attacker with raw write access who rewrites the whole chain produces a locally
  self-consistent history; only comparing the head against an off-box replica catches
  that.
- **`restore` and `appeal` are in the `ModerationAction` vocabulary but no path
  constructs them**, so there was nothing to wire.
- **This ADR is retroactive.** It documents a decision already merged and running. That
  is strictly better than leaving the rule unmet, and strictly worse than having been
  written with the change; the gap is itself the argument for the PR-template checkbox
  that asks for it.

### Alternatives considered

- **Fold `reason` into `PremisEvent.detail` at each call site.** Rejected: `detail` is
  free text inside a hash-chained log whose entries are already written, so the
  rationale would arrive with no schema and no way to query the *why* apart from the
  *what*. It would also put steward prose into the PREMIS log, which `ARCHITECTURE.md`
  describes as identity-free, and into the XML serialization.
- **Correct the documents instead, recording the gap as intentional.** Rejected:
  `GOVERNANCE.md` and `THREAT-MODEL.md` §4.4 rest the case against a coerced steward on
  this one fact, so deleting the claim would leave the threat model with no control in
  that row.
- **Validate the reason for identities and keep the by-construction wording.**
  Rejected: a validator over free prose either rejects legitimate reasons or passes an
  identity through, and there is no safe detector for "does this sentence name a
  person" to put in front of a steward acting under time pressure.
