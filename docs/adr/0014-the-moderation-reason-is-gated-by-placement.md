# 0014. The moderation reason is durable, and gated by placement rather than by construction

Status: Accepted

Date: 2026-08-27

Resolves: #156.

Numbering: 0013 is claimed by the open AI-layer branch (#152,
`0013-ai-at-the-edges.md`). This ADR takes 0014 so two unmerged branches cannot land
the same number; the gap closes when #152 merges.

## Context

`docs/GOVERNANCE.md` ("How decisions are recorded"), `docs/THREAT-MODEL.md` §4.4, and
`docs/ARCHITECTURE.md` §1.9 each describe `ModerationLog` as the accountable record of
**what, who, why, and to which record**, and each names the required non-empty `reason`
as the control that makes the log a check on a coerced or bad-faith steward rather than
a bare activity feed.

Three of those four facts were durable. The fourth was not. `ModerationLog` was never
instantiated outside `tests/test_moderate_chain.py`. `moderate._require_reason`
validated the rationale at the boundary, every call site then discarded the returned
`ModerationAction` as `_action`, and the PREMIS event persisted beside it builds its
`detail` only from the *what* (`"record taken down"`, `"default policy changed to
public"`). A steward acting for a pretextual reason left a trace that *an* action
happened, never of what they claimed.

Two decisions in that repair are hard to reverse, and ADR 0000's rule ("any change to a
safety guardrail, including no-outing, consent, identity separation, workflow
permissions, or a coverage/security threshold, must link an ADR in its pull request")
reaches both.

The first is where the boundary around the rationale sits. `GOVERNANCE.md` had described
the whole log as identity-free **by construction**. That was true of `actor` (a steward
id), of `target_record` (an opaque record id), and of `action` (a fixed vocabulary). It
was never true of `reason`, which is prose a human types. Making the reason durable is
what forces the question: a field that no code path could previously persist could not
leak, and now it can.

The second is the coverage floor. `make cov`'s scoped report is
`coverage report --include="src/ledger/access/*,src/ledger/consent.py,src/ledger/dualcontrol.py" --fail-under=95`,
and `--fail-under` gates the **TOTAL row, not each module**. That line passes at 95%
while `grants.py` sits at 92% and `consent.py` at 91%. Adding a module to that include
list is therefore not a neutral act: it buys the new module a passing grade from its
neighbours.

## Decision

### 1. The reason is persisted, and every live decision path writes it

`moderate.ModerationLogStore` keeps the log at `<store>/logs/moderation.json`, under the
same three rules as every sibling JSON store in this package: the read-modify-write is
serialized by `ledger._filelock.file_lock`; a read failure raises rather than returning
an empty log; appends chain, so an edit anywhere in history moves the head.

Every path that takes one of these decisions records it: `ledger policy`, `ledger seal`,
`ledger cw`, `ledger takedown`, an executed dual-control `publish`, the steward console's
warn / takedown / submission review, and a contributor's own withdrawal.
`execute_takedown` records *before* it removes anything, which makes its own docstring
("durably persisted FIRST -- its audit trail of *why* must outlive the data") true for
the first time.

### 2. The store is reached by module functions taking an archive, not by `Archive` methods

`moderate` already depends on `ingest.Archive` for `execute_takedown`. `Archive`
importing `moderate` back would make the two cyclic, against the one-way layering
`docs/ARCHITECTURE.md` §1 states. So `Archive` owns only `moderation_log_path`, and the
store is reached as `moderate.record_moderation(archive, action)` /
`moderation_actions(archive)` / `verify_moderation_chain(archive)`, matching the shape
`execute_takedown` already had.

### 3. The rationale is gated by placement, and `GOVERNANCE.md` says so instead of claiming construction

Nothing can validate prose for whether it names someone. A steward can type a
contributor's name into a reason field, and no amount of type discipline will stop them.
So the control is stated as what it actually is: **no ungated surface renders it.** The
rationale appears on `/steward/audit` behind the steward gate and in the
steward-operated `ledger moderation list`, and nowhere else.

This is asserted, not asserted-and-hoped: a merge-blocking `disclosure`-marked test
walks twelve public surfaces (`/`, `/record/{id}`, `/search`, `/api/records`,
`/api/record/{id}`, `/feed.atom`, `/oai`, `/transparency`, `/proof`, `/overview`,
`/timeline`, `/places`) and fails if the reason appears on any of them, having first
asserted it *is* present behind the gate so the test cannot pass vacuously.

`GOVERNANCE.md` now scopes the by-construction claim to the three fields it was ever
true of, and states the placement rule for the fourth. Stewards are told, in the same
paragraph, to describe the decision rather than the people in the record.

### 4. `moderate.py` gets its own coverage floor, off the pooled scope

`moderate.py` is reported on its own at `--fail-under=90`, its measured value, rather
than being appended to the pooled include list. The pooled line and its 95% are
unchanged. Both the `Makefile` and `CONTRIBUTING.md` now state the pooling weakness in
place, so the next module to be floored is not quietly folded in either.

This is a floor as this repo defines one (`pyproject.toml`: "set at the level the suite
ALREADY clears"), and a ratchet: raise it when the number rises.

## Consequences

| | before | after |
| --- | --- | --- |
| moderation decisions persisting a rationale | 0 | every live path: 4 CLI, 1 dual-control, 3 console, 1 contributor |
| `ModerationLog` instantiations outside a unit test | 0 | 1 store, reached from `cli.py` and `server.py` |
| surfaces that can render a steward's prose | 0 (it was never persisted) | 2, both steward-gated, asserted against 12 public surfaces |
| what `GOVERNANCE.md` claims is identity-free by construction | all four facts | the three that are |
| modules with a coverage floor of their own | 0 (one pooled scope of 3) | 1 (`moderate.py`), pooled scope unchanged |

Costs and open edges, stated rather than left to be discovered:

- **A steward can still write an identity into a reason.** This ADR does not claim
  otherwise. It moves the guarantee from a false "by construction" to a true "on no
  ungated surface", and puts a test behind the true one. The residual is governance, as
  `docs/THREAT-MODEL.md` §4.4 already says of a malicious steward generally.
- **The chain narrows §4.4's tamper residual rather than closing it.** An edit that does
  not recompute every following link is now detected by `ledger moderation verify` and
  reported on `/steward/audit`. An attacker with raw write access who rewrites the whole
  chain produces a locally self-consistent history; only comparing the head against an
  off-box replica catches that.
- **The pooled floor is still pooled.** This ADR keeps `moderate.py` out of it and
  documents it in two places; it does not fix it. Per-module floors for the pooled scope
  are `MP-02` in [`../MULTIYEAR-PLAN.md`](../MULTIYEAR-PLAN.md), and that change lowers
  two published numbers to their true values, which is a decision of its own.
- **`restore` and `appeal` are recorded by the store the moment a path builds one.**
  Both are in the `ModerationAction` vocabulary and in `moderate.py`, but no CLI or
  server path constructs them today, so there was nothing to wire.
- **The moderation table has no filter or pagination.** It renders the most recent 200,
  like the PREMIS table beside it.

### Alternatives considered

- **Fold `reason` into the `PremisEvent.detail` string at each call site.** The lighter
  half of what #156 suggested. Rejected: `detail` is free text inside a hash-chained log
  whose entries are already written, so the rationale would arrive with no schema, no
  separate read surface, and no way to query the *why* apart from the *what*. It would
  also put steward prose into the PREMIS log, which `docs/ARCHITECTURE.md` describes as
  identity-free, and into the XML serialization, widening the surface this ADR narrows.
- **Correct the documents instead, and record the gap as intentional.** Also offered by
  #156. Rejected: `GOVERNANCE.md` and `THREAT-MODEL.md` §4.4 rest the case against a
  coerced steward on this one fact. Deleting the claim would leave the threat model with
  no control in that row, which is a worse honest state than the one being fixed.
- **Keep the by-construction wording and validate the reason for identities.** Rejected:
  a validator over free prose either rejects legitimate reasons or passes an identity
  through, and either outcome is worse than a boundary that is honest about being a
  boundary. There is no detector for "does this sentence name a person" that is safe to
  put in front of a steward acting under time pressure.
- **Append `moderate.py` to the pooled include list.** Rejected for the reason in
  decision 4: it would read as covered because `policy.py` and `dualcontrol.py` are at
  100%, which is the pooling weakness itself, repeated deliberately.
