# 0015. Coverage floors are per module, and no module may be unfloored

Status: Accepted

Date: 2026-08-27

Supersedes the pooled scoped floor introduced alongside CQ-08; extends ADR 0014
decision 4, which deferred this.

## Context

`make cov` and CI's `gate` job each carried the same line:

```
coverage report --include="src/ledger/access/*,src/ledger/consent.py,src/ledger/dualcontrol.py" --fail-under=95
```

`--fail-under` gates the **TOTAL row** of a report, not each module in it. That line
passed at exactly 95% while `grants.py` sat at 92% and `consent.py` at 91%, carried by
`policy.py`, `redaction.py` and `dualcontrol.py` at 100%. Two of the six modules in the
repository's declared security core were below the floor their own gate advertised, and
the gate was structurally incapable of reporting it.

`DEFINITION_OF_DONE.md` had described this as "a ≥ 95% **per-module** floor on
`access/`/consent/dual-control" for months. The document was right about the intent; the
implementation was a single pooled number.

The pooling weakness was known and written down: the `Makefile` carried a `NOTE ON THE
POOL` paragraph, `CONTRIBUTING.md` repeated it, and ADR 0014 named it as an open edge
while keeping three further modules (`moderate.py`, `tombstones.py`, `review.py`) out of
the pool on their own scoped lines. Documenting a hole is not closing it, and the
workaround was accumulating: five ad-hoc `coverage report` invocations, each gating one
scope, each stopping the build at the first failure so a reviewer learned about one
module per run.

## Decision

### 1. Raise the two modules rather than lower the published number

The honest way to make a 95% claim true is to make it true. `grants.py` went 92% to
100% and `consent.py` 91% to 97%, by covering paths that were missing rather than by
restating the floor at the measured value.

What was uncovered was not incidental. In `grants.py` it was every refusal path of the
bearer-capability verifier: a token whose base64 is malformed, one that decodes to bytes
that are not UTF-8, one whose expiry is not a parseable timestamp. Those inputs arrive
in an untrusted `X-Ledger-Grant` header, so an uncovered `except` there is a public
route that can be made to raise. In `consent.py` it was the `SubjectTokenStore`'s
corruption handling, which is the same fail-closed family ADR 0014 addressed.

Lowering the floors to 92 and 91 would have been the other available honesty. It was
rejected: the floors were reachable, and a floor set to whatever the code happens to
score is a description, not a gate.

### 2. Floors are declared as data, one per module, and checked by a gate that reports every violation

`[tool.ledger.coverage_floors]` in `pyproject.toml` maps a module path to its floor.
`tools/check_coverage_floors.py` measures each module on its own and collects **all**
violations before failing, rather than stopping at the first as a chain of
`--fail-under` invocations does.

The comparison is `coverage.results.should_fail_under` at coverage's own precision. A
bespoke comparison would make this gate disagree with every other `--fail-under` in the
repo at the rounding boundary: `moderate.py` measures 89.90%, which `coverage report
--fail-under=90` passes, so this must pass it too or the same tree is green under one
gate and red under another. The precise figure is printed beside the rounded one, so the
rounding that decides the outcome is visible.

### 3. A security-core module without a floor is a build failure

This is the half the pooled `--include` never had, and the reason this is an ADR rather
than a refactor. `[tool.ledger].security_core` is a list of globs; every module matching
one must appear in the floors table.

Before, adding a safety-critical module and forgetting to floor it was invisible. Worse
than invisible: the obvious remedy, appending it to the pooled `--include`, would have
bought it a passing grade from its neighbours. That is exactly what ADR 0014 declined to
do for `moderate.py`, `tombstones.py` and `review.py`, and it had to be declined by
hand each time. It is now refused by the gate.

The inverse drift is refused too: a floor naming a module that no longer exists is a
dead config key, and a floor nobody is meeting reads as a floor somebody is.

## Consequences

| | before | after |
| --- | --- | --- |
| `grants.py` | 92% under a 95% line that passed | 100% against its own 95% floor |
| `consent.py` | 91% under the same line | 97% against its own 95% floor |
| modules gated on their own score | 0 (one pool of 3, plus 3 ad-hoc scopes) | 8 |
| violations reported per run | 1, then the next on the next run | all of them |
| a security-core module with no floor | silently ungated | build failure |
| a floor naming a deleted module | silently vacuous | build failure |
| an empty floors table | not possible to express | build failure |
| places the floor list is written | 2 (`Makefile`, `ci.yml`), able to drift | 1 (`pyproject.toml`) |

Costs and open edges, stated rather than left to be discovered:

- **Floors are a ratchet and nothing raises them automatically.** A module that
  improves keeps its old floor until someone edits the table. An automatic ratchet was
  considered and rejected: it would make an unrelated PR's incidental coverage gain into
  a permanent obligation on the next author, which is how a floor becomes something
  people route around.
- **`moderate.py` (90%), `tombstones.py` (89%) and `review.py` (97%) are floored below
  95.** They are not in the same tier as the access and consent core, and this ADR does
  not claim they are; it claims only that each is gated on its own number. Raising them
  is ordinary work, not a decision needing a record.
- **`security_core` is a curated list, not a derived one.** Nothing proves the globs
  name every module that deserves a floor; that judgement stays human. What the gate
  guarantees is that the list and the floors cannot drift apart.
- **The gate reads the coverage data `make cov` has already written.** It adds no second
  test run, and it fails with a clear message rather than a stale number if the data file
  is missing.
- **`make verify` still does not run coverage.** `verify` calls `test` (plain `pytest`)
  because the coverage run roughly doubles wall time; CI's `gate` job runs both. That
  split is unchanged and remains documented in `DEFINITION_OF_DONE.md`.

### Alternatives considered

- **Lower the pooled floor to 91%, the true minimum.** Rejected: it makes the gate
  weaker than the code, and the published claim in three documents would have to fall
  with it. The gap was closable with tests worth having on their own.
- **Keep the pool and add a second, per-module report beside it.** Rejected: two gates
  answering overlapping questions is how a reviewer learns to read one and ignore the
  other, and the pooled TOTAL would keep passing while a module failed.
- **Enforce per-module floors with N `coverage report --fail-under` lines.** This is
  what the `Makefile` was already growing toward, and it was rejected for the reasons in
  decision 2: it stops at the first failure, it duplicates the module list into two
  files that drift, and it cannot express "every security-core module must have a floor".
- **Derive `security_core` from the `disclosure`/`preservation` pytest markers**, as
  `[tool.mutmut]` does for its kill oracle. Attractive, and rejected for now: the markers
  select *tests*, and inverting them to a set of *modules* requires running the suite
  under coverage per marker, which is the second test run this gate deliberately avoids.
