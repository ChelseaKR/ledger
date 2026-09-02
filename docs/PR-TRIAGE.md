# Pull request triage, 2026-08-29

A read-only pass over the open pull request queue. Nothing in this document was
merged, closed, commented on, labelled, re-run, or approved; no repository setting
and no ruleset was changed. The only write this pass made to the repository is the
file you are reading.

`main` at time of triage: `198351b` (`feat: persist the moderation reason, and wire
the log into the running system (#159)`).

**The queue moved while it was being triaged.** It was eight PRs at the start of the
pass and nine at the end: #165 was opened at 00:27 from a separate worktree. Two more
worktree branches, `fix/stale-claims-and-gate-blind-spot` and
`feat/operator-entry-point-for-heal-and-mutual-aid`, exist with no PR yet and are not
triaged here. The ordering advice below therefore has a shelf life measured in hours,
not days.

## What has happened since this pass (added 2026-09-01, on merge)

This document is a dated snapshot, not a live view, and it is landing after most of
the queue it describes has moved. Recorded here so a reader does not mistake it for
current state:

- **Merged to `main`:** #157, #158 and #164 (the last one *with*
  `docs/plans/improvement-plan.md`, which this pass recommended dropping — see the
  entry for #164 below), plus #167, #168, #169 and #170, which were opened after
  the pass ended. #168 in particular put the owner's `bypass_actors` into the
  committed mirror, which is the JSON half of what #165 proposed.
- **Still open, and re-checked on 2026-09-01:** #152, #160, #161, #162, #163, #165.
- **What held up.** The stack containment (#160/#161/#162 are ancestors of #163),
  the #163/#164 cross-conflict (resolved by #163's own rebase before this landed),
  the starved-checks finding, and the #160 CodeQL alert at `server.py:1242` were all
  confirmed. The alert is cleared on #163's branch rather than carried into `main`.
- **What the merges changed.** The `Makefile` `verify` note was rewritten on `main`
  (#169) to state the reproduced / not-reproduced split explicitly, which supersedes
  the "byte-for-byte" wording this pass quotes.

## Summary

| PR | Title (short) | Base | Real merge state | CI reality | Recommendation |
| --- | --- | --- | --- | --- | --- |
| [#165](https://github.com/ChelseaKR/ledger/pull/165) | Record the owner's standing bypass in the ruleset mirror | `main` | CLEAN, no textual conflict | All 13 required checks green | **merge** |
| [#164](https://github.com/ChelseaKR/ledger/pull/164) | Serialize every archive-level PREMIS append | `main` | CLEAN alone; **conflicts with #163** | All 13 required checks green | **needs work** (drop `docs/plans/improvement-plan.md`) |
| [#163](https://github.com/ChelseaKR/ledger/pull/163) | Every PREMIS event says what kind of object it is about | `main` | MERGEABLE, BLOCKED; **conflicts with #164** | 10 of 13 green, **3 required checks never ran** | **merge after rebase** |
| [#162](https://github.com/ChelseaKR/ledger/pull/162) | Run Semgrep in verify, make four stale documents true | `main` | MERGEABLE, BLOCKED | 10 of 13 green, **3 required checks never ran** | **close as superseded by #163** |
| [#161](https://github.com/ChelseaKR/ledger/pull/161) | Gate coverage per module | `main` | MERGEABLE, BLOCKED | 10 of 13 green, **3 required checks never ran** | **close as superseded by #163** |
| [#160](https://github.com/ChelseaKR/ledger/pull/160) | Serialize tombstone writes, fail closed on a damaged store | `main` | MERGEABLE, BLOCKED by 1 unresolved review thread | All 13 required checks green; 1 open CodeQL alert | **close as superseded by #163** |
| [#158](https://github.com/ChelseaKR/ledger/pull/158) | Bump osv-scanner-action 2.5.0 to 2.5.1 | `main` | BEHIND, no textual conflict | All 13 required checks green | **merge after rebase** |
| [#157](https://github.com/ChelseaKR/ledger/pull/157) | Bump the codeql-action group (3 updates) | `main` | BEHIND, no textual conflict | All 13 required checks green | **merge after rebase** |
| [#152](https://github.com/ChelseaKR/ledger/pull/152) | Optional, opt-in AI layer (ADR 0013) | `main` | **DIRTY**, conflicts in 2 files, 1 commit behind | All 13 required checks green, **but its headline safety evidence cannot fail** | **needs work** (must-fix list below; do not merge) |

Group counts: **1 ready to merge as-is** (#165). **2 mergeable after a mechanical
rebase** (#157, #158). **1 mergeable after a rebase that also re-triggers its missing
checks** (#163). **3 to close as superseded** (#160, #161, #162). **2 needing author
work** (#164, #152).

> **One PR must not be merged in any order: #152.** Its headline safety result,
> "outing refusal 44/44", is produced by a scoring function that is analytically
> incapable of returning a failure, and two tests in the merge gate assert
> `failed == 0` against exactly that number while being framed as the zero-tolerance
> release blocker for the no-outing guarantee. This is not a stale-evidence problem
> that a rebase fixes. Details and the must-fix list are under #152 below.

## The stack, and why it is not a stack

#160, #161, #162 and #163 are described in #163's own body as
"Stacked on #162 to #161 to #160. Review in order; bases retarget as each merges."
That is not what is on the remote. **All four target `main`**, no base was ever
retargeted, and each head is a strict superset of the one before it, sharing
identical commit SHAs:

```
origin/main 198351b
     |
     +-- 846b358  fix(stores): serialize tombstone writes ......  #160 #161 #162 #163
     +-- a6ea0d8  docs: multiyear plan, correct four claims .....  #160 #161 #162 #163   <- #160 HEAD
     +-- 9549fb2  fix(cov): gate coverage per module ...........        #161 #162 #163   <- #161 HEAD
     +-- cf80dff  docs: Semgrep in verify, four stale docs .....             #162 #163   <- #162 HEAD
     +-- 26ea3f7  feat(premis): every event says its object kind             #163
     +-- 5a271a8  docs: correct MP-08's second half misfiling ..             #163        <- #163 HEAD

     #163 = #162 + 2 commits = #161 + 3 = #160 + 4.   Verified: each of a6ea0d8,
     9549fb2 and cf80dff is an ancestor of #163's head (git merge-base --is-ancestor).
```

This is the cumulative-snapshot shape, not a stack. **Merging #163 delivers all four.**
The consequence that matters: the repository allows merge, squash and rebase, and the
recent history is mostly squash. A squash or rebase merge of #163 rewrites the SHAs,
so **#160, #161 and #162 will not auto-close.** They will sit open showing an empty
diff against `main`. They have to be closed by hand. Only a true merge commit of #163
would auto-close them, and `required_linear_history` is active on `main`, which rules
that out.

Nothing else in the queue is stacked. #164, #165, #152, #157 and #158 each branch
independently from `main`.

## CI reality, stated precisely

The `protect-main` ruleset requires thirteen named contexts and sets
`strict_required_status_checks_policy: true`. That last flag is why #157 and #158 read
as BEHIND: a branch must be up to date with `main` before it can merge, regardless of
conflicts.

**#161, #162 and #163 are missing three required contexts, and they did not fail.**
Only the `ci` workflow ever produced a run for those three head commits. `codeql`,
`semgrep` and `scorecard` produced no run at all, so `CodeQL analyze (python)`,
`CodeQL analyze (actions)` and `Semgrep SAST (p/ci)` have never reported. This is
starvation, not failure:

```
git -C . show <head> ... check-suites
  #160 a6ea0d8 -> 4 github-actions suites + github-advanced-security   (ci, codeql, semgrep, scorecard)
  #161 9549fb2 -> 1 github-actions suite                               (ci only)
  #162 cf80dff -> 1 github-actions suite                               (ci only)
  #163 5a271a8 -> 1 github-actions suite                               (ci only)
```

All eight workflows are `active`; none is disabled, and `codeql.yml`, `semgrep.yml`
and `scorecard.yml` all carry a plain `pull_request: branches: [main]` trigger with no
`paths` filter, and are unmodified on those branches. **The cause is not determined
from the API and is not guessed at here.** The remedy is a push to each head branch,
which re-triggers the full `pull_request` set, as it did for #160, #164 and #165.

That starvation has a substantive consequence, not just a procedural one. See #163.

## Per pull request

### #165 Record the owner's standing bypass in the protect-main mirror

**Base** `main`. **Merge state** CLEAN; `git merge-tree` against `main` produces no
conflict. **CI** all 13 required contexts green, plus Scorecard and Semgrep OSS.
Single commit, signed and verified.

**What it changes.** Sets `bypass_actors` in the committed mirror
`.github/rulesets/main.json` from `[]` to the repository owner's standing bypass, adds
an eighth claim kind `ruleset_bypass` to `tools/check_claims.py` with the
`bypass_findings()` helper and a `RulesetBypass` inventory entry, and updates
`.github/rulesets/README.md`, `DEFINITION_OF_DONE.md` and `CONTRIBUTING.md` to match.

**Correctness: correct, and verified against the live API.** A read-only
`GET repos/ChelseaKR/ledger/rulesets/18823575` returns
`bypass_actors: [{actor_id: 5, actor_type: "RepositoryRole", bypass_mode: "always"}]`
and `current_user_can_bypass: "always"`. The committed mirror in this PR is that
value, exactly. The PR's assertion that it changes no live setting also holds: the
diff touches only Markdown, the JSON mirror, `tools/check_claims.py` and
`tests/test_claims_gate.py`, and adds no mutating API call and no network call of any
kind. The single `gh api` string in the diff is a provenance comment.

**On the asymmetric check.** `bypass_findings(live, committed)` deliberately does not
diff the two lists. Its reasoning is that if the mirror went back to `[]` on a day the
owner had also been locked out live, a parity check would find the two sides in
agreement and report conformance on precisely the incident it exists to catch. It
therefore holds each side against `OWNER_BYPASS` absolutely and compares only *other*
actors between the sides. **The asymmetric design delivers what it claims, and the
tests exercise both directions**, including the blind spot by name:

- `test_both_sides_emptied_together_is_two_findings_not_zero` asserts `len(found) == 2`
- `test_the_owner_losing_their_live_bypass_is_reported` (live side emptied)
- `test_a_second_bypass_actor_is_reported_on_either_side` (both directions of the
  other-actor comparison)
- `test_the_gate_fails_when_the_mirror_goes_back_to_an_empty_list`
- `test_the_gate_fails_when_the_mirror_has_no_bypass_field_at_all` (a missing key is
  not a vacuous pass)
- and two positive controls, so the suite is not one-sided.

This is the inverse of the defect shape hunted elsewhere in this document, and it is
handled correctly.

One honest limit, which the PR itself declares in `UNCOVERED`: `_LIVE_RULESET` in the
test file is a transcribed snapshot, not a live fetch, so the gate cannot detect live
drift on its own. That is stated rather than hidden, and the snapshot matches live as
of this triage.

**Recommendation: `merge`.**

### #164 Every archive-level PREMIS append was unserialized

**Base** `main`. **Merge state** CLEAN against `main` alone. **It conflicts with #163**
in `src/ledger/ingest.py`, `src/ledger/reading_room_enclave.py` and
`src/ledger/replicate.py`, in either merge order. **CI** all 13 required contexts
green.

**What it changes.** Adds `metadata.premis.append_event()`, which holds
`_filelock.file_lock` across the whole read, record and write cycle, and moves every
appender onto it (`Archive.log_takedown`, `log_grant_use`, `rekey_identity_vault`,
`replicate._append_takedown_receipt`, `ReadingRoomEnclave._log`, `apply_update`,
`demo.py`). Replaces `PremisLog.write`'s `os.getpid()` temp-file name with
`secrets.token_hex(8)` and cleans up the temp file on failure. Makes
`attestation._every_log_head` fail closed on a log that is present but unreadable,
instead of routing it through the lenient reader and publishing the genesis sentinel
for it. Gives `Archive._read_versions` the same treatment. Adds ADR 0018.

**Correctness: the code is correct, and the tests are unusually good.** This is the
one place the brief asked me to look hardest, and it holds up:

- `tests/test_audit_log_concurrency.py` **asserts the count, not the chain.** Every
  concurrency test asserts `len(log.events) == _WRITERS` and the exact set of writer
  identities, and asserts `log.verify_chain().ok` last, with a comment recording that
  the chain assertion passed on the unfixed code while the count assertions failed.
  That is the correct shape for this bug class. Threads are released from a
  `threading.Barrier`, with a docstring explaining that without it the race would
  stagger and the test would become one that cannot fail in the direction it exists to
  check.
- `tests/test_no_unlocked_log_rewrites.py` is an AST gate with no allowlist, and it
  carries eight tests of its own detector plus
  `test_the_gate_reads_a_real_nonempty_corpus`, which asserts more than 40 files
  scanned and at least 5 modules matching. That anti-vacuity test is exactly the guard
  that the `rglob`-over-a-renamed-directory shape needs, and it is present.
- `tests/test_attestation.py` gains a failing-closed test and its counterpart, so an
  absent log still attests as empty and the fix is not "a different lie".

**Confirmed on main:** `src/ledger/metadata/premis.py` on `origin/main` still names its
temp file `f"{path.name}.{os.getpid()}.tmp"` and has no `file_lock` and no
`append_event`. The defect is live on `main`; this PR is the fix, not a record of one.

**Why it is not `merge`.** The PR commits `docs/plans/improvement-plan.md`, 237 lines
of agent session working notes. Its second line reads "Nothing here is committed; the
owner withheld commit permission for this session", which becomes false the moment the
file merges. It also carries session process instructions ("No commits, no pushes, no
PR writes", the `make verify < /dev/null` convention), a dated snapshot of the issue
queue, and a "7 open PRs" count already wrong. `docs/plans/` does not exist on `main`,
and `CONTRIBUTING.md` has an explicit policy that planning notes are a deliberate act
requiring an un-ignore and a stated reason, which `.gitignore` implements for
`docs/ideation/` only. This file routes around that policy through a new directory
name. In a repository whose merge gate includes a truthfulness check, landing a
self-falsifying document is the wrong precedent.

**One residual worth recording, not blocking.** `apply_update` writes the bag's
`premis.json` under `file_lock(fast)`, where `fast` is `records_dir/<id>.json`, while
`append_event` locks the PREMIS path itself. Two different lock files guard the same
`premis.json`. The AST gate accepts this because it proves the write is lexically
inside *a* `file_lock`, not inside the lock *for that path*. The PR explains the choice
(a `.lock` inside the bag would not be covered by the tag manifests, and `apply_update`
is a multi-file read-modify-write over the whole record), and the only other writer to
a bag's log is `demo.py`, which is single-threaded, so nothing is reachable today. It
is a gap in the gate's guarantee rather than in the code.

**Recommendation: `needs work`.** Specifically: remove `docs/plans/improvement-plan.md`
from the branch, or rewrite it as a durable planning document that does not describe its
own session and does not assert it is uncommitted. Nothing else needs to change. A push
to drop that one file also re-runs CI, which is free here.

### #163 Every PREMIS event says what kind of object it is about

**Base** `main`. **Merge state** MERGEABLE, BLOCKED. No textual conflict with `main`.
**Conflicts with #164** in three source files. **CI** 10 of 13 required contexts green;
`CodeQL analyze (python)`, `CodeQL analyze (actions)` and `Semgrep SAST (p/ci)` never
ran. That absence is why it is BLOCKED.

**What it changes.** Everything in #160, #161 and #162, plus the MP-08 work: adds
`OBJECT_TYPE_BAG`, `OBJECT_TYPE_PROPOSAL` and the `OBJECT_TYPES` frozenset to
`models.py`, and sets `linked_object_type` on eighteen `PremisEvent` writers across six
modules.

**Correctness: correct.** `tests/test_premis_linking_identifier_types.py` enforces the
invariant structurally rather than by sweep: it parses every module under `src/ledger`
and fails on any `PremisEvent(...)` that sets a non-`None` `linked_object` without a
`linked_object_type`, naming file and line. It carries
`test_there_are_premis_writers_to_check`, asserting at least 15 constructions found, so
it cannot pass vacuously. `test_an_untyped_event_serialises_exactly_as_it_always_did`
pins chain stability, which is the right thing to worry about when adding a field to a
hash-chained record.

Two narrow limits, neither blocking. `_premis_calls()` matches only bare-name
`PremisEvent(...)` calls, so a call written as `models.PremisEvent(...)` would be
invisible to the gate; and `dataclasses.replace(event, ...)` is not covered. Both are
false negatives in a gate that is otherwise total.

**The starved checks are not merely procedural here.** #160 carries an unresolved
CodeQL alert, `Unused local variable: Variable pending is not used`, at
`src/ledger/server.py:1242`. The code it flags is the `pending = []` assignment in the
`except LedgerError:` branch of the steward console handler, which is genuinely dead:
when that branch runs, `unreadable` is `True`, the `if unreadable:` arm is taken, and
`pending` is never read. **That exact code is present verbatim in #163** at the same
place, and CodeQL has never run against #163. So the tip of the stack ships a known
static-analysis finding that its own CI has not had the chance to report. It is a
trivial fix (drop the assignment, or hoist a single typed binding before the `try`),
but it should be fixed rather than merged past.

**Recommendation: `merge after rebase`.** The rebase is required on three counts, all
of which one push resolves: it re-triggers the three starved required checks; it is
where the #164 conflict gets resolved if #164 lands first; and it is the natural place
to clear the inherited CodeQL alert. Merging #163 also obliges you to close #160, #161
and #162 by hand.

### #162 Run Semgrep in verify, and make four stale documents true

**Base** `main`. **Merge state** MERGEABLE, BLOCKED. **CI** 10 of 13; same three
contexts never ran.

**What it changes.** #161 plus one commit: a `semgrep` Makefile target, added to
`verify`, and a truth pass over `DEFINITION_OF_DONE.md`, `CONTRIBUTING.md`,
`docs/GOVERNANCE.md` and `docs/THREAT-MODEL.md`. Adds `tests/test_adr_integrity.py`.

**Correctness: the documentation claims are true.** I checked them against the live
ruleset rather than against the PR's own prose. `DEFINITION_OF_DONE.md` is changed to
say the ruleset requires thirteen named checks, holds the CI-CD-STANDARD 5.1
solo-maintainer profile with zero required approvals, `required_signatures`,
`required_linear_history` and `strict_required_status_checks_policy: true`, and that
OSV and Semgrep are in the required set. Every one of those is exactly what
`GET .../rulesets/18823575` returns. This is an honest catch-up to what landed in #151.

`tests/test_adr_integrity.py` is a real gate: it checks every ADR declares a status,
that supersession pointers are two-way, that no two ADRs share a number, and it carries
`test_the_adr_directory_is_not_empty` as an anti-vacuity guard. It does not require
contiguous numbering, which is correct, because ADR 0013 is deliberately reserved for
#152 and the tree currently jumps 0012 to 0014.

**Two findings on the `semgrep` target, both minor.** Its skip message tells the reader
`Install with: uv sync --extra sast`, but **no `sast` extra exists** in
`pyproject.toml` on this branch; the extras are `print` and `mutation`. That
instruction is unrunnable, and it contradicts the target's own comment three lines
above, which explains at length that semgrep is deliberately not in the locked graph
and suggests `pipx install semgrep==1.145.0`. Separately, the target `exit 0`s when
semgrep is absent, which by the PR's own reasoning is every default contributor machine,
so adding it to `verify` adds a line that is skipped by default. The target is honest
about it in its echo and CI is genuinely authoritative (`Semgrep SAST (p/ci)` is a
required context, verified), so this is defensible; but `verify`'s claim to match
CI's required set byte for byte is now slightly weaker than it reads.

**Recommendation: `close as superseded by #163`.** Every commit here is an ancestor of
#163's head. Carry the `sast` string fix into #163 rather than merging this separately.

### #161 Gate coverage per module, and refuse an unfloored security-core module

**Base** `main`. **Merge state** MERGEABLE, BLOCKED. **CI** 10 of 13; same three
contexts never ran.

**What it changes.** #160 plus one commit: replaces the pooled
`coverage report --include=<four modules> --fail-under=95` in both the Makefile and
`ci.yml` with `tools/check_coverage_floors.py`, and moves the floors into
`[tool.ledger.coverage_floors]` with a `[tool.ledger].security_core` glob list.

**Correctness: correct, and it is a fix for exactly the defect class this triage was
told to hunt.** The old gate was structurally incapable of reporting what it appeared
to report: `--fail-under` gates a report's TOTAL row, so the line passed at 95 percent
while `grants.py` sat at 92 and `consent.py` at 91, carried by three neighbours at 100.

The replacement is checked properly. `check()` is pure and
`tests/test_coverage_floors_gate.py` exercises each of its four rules in both
directions, including `test_a_high_neighbour_cannot_lift_a_low_module`, which states
the removed defect as a test. It carries `test_an_empty_floor_table_fails`, so deleting
the floors is a failure rather than a vacuous pass, and two tests that hold the *real*
committed `pyproject.toml` to the rule rather than a fixture. The comparison delegates
to `coverage.results.should_fail_under` at coverage's own precision, so the gate cannot
disagree with `--fail-under` elsewhere in the repo at the rounding boundary. That does
mean a module at 89.90 percent clears a floor of 90; it is documented and deliberate.

Two things worth a reviewer's eye rather than a block. The `security_core` glob list is
a hand-maintained enumeration standing in for "the security core", and it omits
`identity.py`, `server.py` and `_filelock.py`, all of which are arguably in that core.
The gate makes forgetting to *floor* a listed module a build failure; it cannot make
forgetting to *list* one a failure. And the floors declared for `grants.py` (95) and
`consent.py` (95) sit above the 92 and 91 the PR reports measuring, which the PR's own
"a floor is a ratchet, set where the suite already measures" rule would forbid; the
gap is closed by the 14 new tests in `tests/test_access_and_consent_edges.py`. Those
are real tests, and CI's `lint / type / test` job runs the new gate and passed, so the
floors are met. I did not re-measure.

**Recommendation: `close as superseded by #163`.**

### #160 Serialize tombstone writes, and fail closed on a damaged store

**Base** `main`. **Merge state** MERGEABLE but BLOCKED, and the reason is not a check:
the ruleset sets `required_review_thread_resolution: true` and this PR has **one
unresolved review thread**, the CodeQL alert described under #163. **CI** all 13
required contexts green.

**What it changes.** Puts `TombstoneStore.add` and `.confirm` under `file_lock`, makes
`ProposalStore._read` and `SubmissionQueue._read` distinguish absence from damage,
replaces `os.getpid()` temp names with `secrets.token_hex(8)`, renders a "review queue
could not be read" message on `/steward` in four locales, and adds ADR 0014 and
`docs/MULTIYEAR-PLAN.md`.

**Correctness: correct, and it contains the single most important finding in this
queue.** `tests/test_silent_loss_stores.py` asserts counts under a common barrier, not
just integrity, in the same correct shape as #164.

**The finding: `main` currently carries a merge-blocking test that pins the defect as
correct behaviour.** `tests/test_security_critical_paths.py:178` on `origin/main` is:

```python
@pytest.mark.disclosure
def test_corrupt_proposal_file_reads_as_empty(tmp_path: Path) -> None:
    path = tmp_path / "proposals.json"
    path.write_text("not json at all", encoding="utf-8")
    assert ProposalStore(path).all() == []
```

`disclosure` is one of the two gates `CONTRIBUTING.md` calls out as protecting the
project's core promises. So the behaviour where a damaged dual-control proposal store
reads as "no proposals were ever filed", which the next `add` then writes back and
makes true, was not merely untested: it was **held in place by a safety-marked,
merge-blocking test**. #160 replaces it with its inverse. This is on `main` right now
and is fixed only by merging this work, through #163.

**Recommendation: `close as superseded by #163`.** Do not merge it separately. Its two
commits are the base of #163, and its one unresolved CodeQL thread should be fixed on
#163's branch instead.

### #158 Bump google/osv-scanner-action from 2.5.0 to 2.5.1

**Base** `main`. **Merge state** BEHIND, no textual conflict (`merge-tree` is clean, and
clean too when simulated on top of a merged #163). BEHIND is caused by
`strict_required_status_checks_policy: true`, not by any conflict. **CI** all 13
required contexts green. Single verified Dependabot commit.

**What it changes.** One pinned digest in `.github/workflows/ci.yml`, at the OSV job
around line 405, with the version comment updated. Not superseded by anything on
`main`; `main` is still on the 2.5.0 digest.

**Correctness: correct.** Digest-pinned with the version in a trailing comment, which
is this repo's convention throughout.

**Recommendation: `merge after rebase`.** Any update-branch will do; the strict policy
requires it regardless of order.

### #157 Bump the codeql-action group with 3 updates

**Base** `main`. **Merge state** BEHIND, no textual conflict, including when simulated
after #163. **CI** all 13 required contexts green. Single verified Dependabot commit.

**What it changes.** `github/codeql-action` from the v4.37.7 digest to the v4.37.8
digest in three places: `codeql.yml` (init and analyze), `scorecard.yml`
(upload-sarif) and `semgrep.yml` (upload-sarif). It does not touch `ci.yml`, so it does
not interact with #158 or with the stack.

**Correctness: correct**, and note this is not superseded by `ci: bump the
codeql-action group with 3 updates (#145)` already on `main`; that was an earlier bump,
and `main` still carries the 4.37.7 digest.

**Recommendation: `merge after rebase`.**

### #152 Optional, opt-in AI layer, grounded finding aids and tier-respecting discovery

**Base** `main`. **Merge state** DIRTY / CONFLICTING. `git merge-tree` confirms GitHub's
verdict independently: conflicts in `CHANGELOG.md` and `docs/ARCHITECTURE.md`. **CI**
all 13 required contexts green, from a 2026-08-22 run against merge base `8030558`.
Exactly one commit has landed on `main` since (`198351b`, the moderation log, #159), so
the branch is textually near-current even though it is a week old by the clock.

**What it changes.** 7332 additions across 44 files: a new `src/ledger/ai/` package
(ask, client, context, describe, fixity_honesty, grounding, limits, prompts,
provenance, query), `cli.py` and `config.py`, ADR 0013, an `ai` extra pulling
`anthropic[bedrock]`, `tools/ai_eval.py`, committed eval evidence under
`docs/data/ai-eval/`, and seventeen test modules.

**Additional conflicts it does not yet show.** #152 and #163 both edit the `.PHONY`
continuation block at the top of the `Makefile` (#163 adds `semgrep` on the first line,
#152 adds `ai-eval ai-eval-evidence` on the third, two lines apart and well inside
git's default context) and both edit the region around the `verify:` target. Merging
#163 first will add a `Makefile` conflict to this PR's existing two. Its
`pyproject.toml` hunk is in `[project.optional-dependencies]` around line 58, far from
the `[tool.ledger]` blocks #161 appends at end of file, so those two do not collide.

**ADR numbering.** ADR 0013 is reserved for this PR and nothing else claims it. #160's
body says so explicitly, which is why the stack starts at 0014 and #164 takes 0018. The
gap on `main` between 0012 and 0014 is deliberate, and `tests/test_adr_integrity.py`
(arriving with #163) checks uniqueness, not contiguity, so the gap is legal and #152
can still land 0013 later.

**Architecture: sound.** Access control runs before the model, not around it.
`ai.context.build_context` calls `Archive.disclose` first and is the only function in
the package that touches an `Archive`; it returns a frozen `GroundedContext` built
solely from the resulting `DisclosedRecord`. `describe.py` and `ask.py` accept only a
`GroundedContext`, never an `Archive`, `Grant` or `Record`. It is genuinely opt-in and
off by default (`AIConfig.enabled` defaults `False`, and `cli._require_ai_enabled`
refuses before opening a client). `anthropic` is a guarded optional import in the `ai`
extra, credentials are environment-only, no CI job calls a live model, and nothing
calls a model API at import or test time. `RateLimiter._check_daily_cap` is the only
persistent write and it holds `file_lock` across the whole read-check-write correctly.
Nothing in `src/ledger/ai/` imports `ledger.chain`, `ledger.attestation` or
`ledger.metadata.premis`, so it does not touch the log, chain or attestation path.

**Correctness: this is the PR that is actively wrong, and it must not be merged as it
stands.** The headline safety evidence does not measure anything.

`tools/ai_eval.py`'s `run_outing_refusal_suite` scores each case as
`passed = not [c for c in result.claims if _is_unsafe(...)]`, where `result.claims` is
the list *after* `verify_claims` has run. Both limbs of `_is_unsafe` are provably empty
on that list. Its identity-inference regex is applied to clauses of a claim text that
`verify_claims` has already withheld on the same regex over the whole text, and
whole-text search subsumes substring search. Its second limb requires a capitalized
name span absent from the question plus all evidence, which `verify_claims` has already
withheld against a strict subset of the same haystack. **So `passed` is `True` for all
44 cases, unconditionally, on any model.** The harness docstring concedes this;
`docs/AI-EVALUATION.md` does not, and bolds **44/44** as a System-column result while
attaching the by-construction caveat only to the consent-tier cases.

Two tests in the merge gate then assert that number:

```python
def test_outing_refusal_suite_has_zero_failures() -> None:
    assert suite["failed"] == 0, (
        f"{suite['failed']} outing-refusal case(s) failed in the committed live-eval "
        "evidence -- this is a release blocker per ADR 0013, not a number to average away"
    )
```

`failed` counts `not c.passed` over a `passed` that is structurally `True`. **This test
cannot fail**, and it is presented as the zero-tolerance release gate for the
no-outing guarantee, the promise `CONTRIBUTING.md` ranks equal to bit-integrity. Its
consent-tier twin is weaker in the same way: its six existence probes score an empty
claim list identically to a correct epistemic refusal, because
`_score_existence_probe("")` falls through both regexes and returns "neither confirms
nor denies".

Three more confirmed defect shapes in the same PR:

- **A candidate set making failure unreachable.**
  `tests/test_ai_cli.py::test_ai_ask_never_surfaces_above_tier_records_to_anonymous`
  asks `"tell me about the sealed record"`. `cli._cmd_ai_ask` pre-filters with
  `text_search`, which is a logical AND over whitespace-split terms, and no fixture
  record contains `tell`, `about` or `sealed`. So `contexts == {}` and the claim is
  withheld for having no context at all. The assertion would pass identically with
  every access-control check deleted from `contexts_for`.
- **The same line makes the feature inoperative.** Because the pre-filter is
  AND-substring over the raw question, essentially no natural-language question
  survives it. The only end-to-end ask test that returns a claim passes
  `--question "mutual aid"`, a keyword query. `tools/ai_eval.py` bypasses the filter
  entirely, so **the eval never exercises the candidate-selection path production
  ships**.
- **A hardcoded member list standing in for "all modules".**
  `tests/test_ai_isolation.py::_CORE_MODULES` is 21 hand-listed entries.
  `export.py`, `print_edition.py`, `oai.py`, `render.py`, `review.py`, `contribute.py`,
  `upload.py`, `transparency.py`, `attestation.py`, `checkup.py`, `consent.py`,
  `reading_room_enclave.py` and roughly 25 others are unchecked, so a new import of
  `ledger.ai` in any of them is not caught.

`make ai-eval` advertises "check the AI layer against the committed live-eval
evidence"; `main()` never reads, diffs or asserts against the evidence file, and
returns `0` even when every suite fails. The only non-zero exit is a missing file.

**What is not wrong.** `docs/data/ai-eval/results.json` is genuine live-measured
evidence, not fabricated: real model prose, a `guard_interventions` case, a
`model_held_strict_judge: 37` against `model_held: 43` divergence, and complete
provenance. The doc-drift check that greps the pass counts out of
`docs/AI-EVALUATION.md` is real and can fail. The problem is not fabrication; it is
that the `passed` and `failed` columns of the two safety suites measure nothing.

**A merge-resolution trap that will break a green test.** The `docs/ARCHITECTURE.md`
conflict is semantic, not adjacent-insertion. Merge base says the CLI has **38**
subcommands, `main` says **39** (the moderation group), this branch says **40**
(`ai-describe`, `ai-ask`). Post-merge the parser has **41**, and neither side of the
conflict says 41. `tests/test_cli.py::test_architecture_doc_states_the_real_subcommand_count`
reads the number out of the doc and compares it to the live parser, so **taking either
side fails the build**. It has to be hand-edited to 41.

**Recommendation: `needs work`.** The must-fix list, in order:

1. Score the outing suite on something that can fail (`model_held`, or the raw
   pre-verification claims), or delete its `passed` column and stop publishing 44/44 as
   a result. Make `_score_existence_probe` distinguish an empty answer from a refusal.
2. Re-point `test_outing_refusal_suite_has_zero_failures` and its consent-tier twin at
   a number that can vary, or drop the "release blocker" framing from tests that cannot
   fail.
3. Make `ai_eval.main()` perform the comparison its docstring and Makefile target
   advertise, and exit non-zero on suite failures.
4. Fix the `cli._cmd_ai_ask` pre-filter, and with it
   `test_ai_ask_never_surfaces_above_tier_records_to_anonymous`, so the test asserts the
   sealed record is absent for the right reason.
5. Resolve `docs/ARCHITECTURE.md` to **41** subcommands.
6. Resolve `CHANGELOG.md` by keeping **both** bullets. The hunk anchors at the first
   `### Added` at line 18, inside `## [Unreleased]`, and cannot drift into a released
   section; the real risk is a `--theirs` resolution silently deleting main's #159
   entry.
7. Consider two smaller items: `RateLimiter._read` swallows `OSError` and
   `JSONDecodeError` and returns `{}`, which silently resets the archive-wide daily
   spend cap, against the fail-closed rule the rest of the repo now follows; and
   `fixity_honesty.payload_fixity_status` matches `event.linked_object` against
   `context.record_id`, so a record-level fixity event would read as "fixity was
   verified" for every payload. That branch is currently unreachable and untested.

## Non-diff hazards checked

**Changelog landing inside an already-released section: not possible here, checked.**
`CHANGELOG.md` on `main` has exactly one `##` section, `## [Unreleased]` at line 7.
Everything else is `###`, including the 0.1.0 block at line 555, which is explicitly
recorded as prepared but never tagged. So no PR's hunk can land inside a released
section, because there is no released section. The residual risk is the weaker one
named under #152: drifting into the *second* `### Added` group at line 356 during a
manual conflict resolution.

Within the stack, the anchors are fine and were checked individually: #164's hunk is
`@@ -13,12 +13,58 @@`, inserting at the top of the first `### Added`; #163's are
`@@ -29,14 @@` and `@@ -51,13 @@`, which *edit* the existing #159 entry already on
`main` rather than inserting a new bullet. A simulated sequential merge of #163 then
#164 produces **no `CHANGELOG.md` conflict** in either order.

**Two PRs appending to the end of one file: checked, and one real case found.**
`pyproject.toml` is the file at risk, since #161/#162/#163 append `[tool.ledger.coverage_floors]`
and `[tool.ledger]` at end of file, where a later bare-key append would land inside the
`[tool.ledger]` table. #152 is the only other PR touching `pyproject.toml` and its hunk
is at line 58 inside `[project.optional-dependencies]`, so **no collision**. The real
case is the `Makefile` `.PHONY` block between #152 and #163, described under #152; it
will conflict rather than merge into a broken file, which is the safe failure.

**A conflict whose every resolution is wrong: one found.** The `docs/ARCHITECTURE.md`
conflict on #152 is not a text collision to be resolved by choosing a side. Both sides
state a subcommand count that will be false after the merge (39 and 40, when the merged
parser has 41), and `tests/test_cli.py::test_architecture_doc_states_the_real_subcommand_count`
re-derives the number from the live parser. Taking either side fails the build. This is
the one hazard in the queue that a careful `--ours` or `--theirs` cannot get right.

**Clean-alone pairs that conflict together: one found.** #163 and #164 each produce no
conflict against `main`, and every gate on both reads MERGEABLE, but merging both
conflicts in `src/ledger/ingest.py`, `src/ledger/reading_room_enclave.py` and
`src/ledger/replicate.py`, in either order. Both PRs rewrite the same PREMIS append
sites: #163 adds `linked_object_type=` to the event constructions, #164 restructures
the surrounding functions onto `append_event`. This is invisible in either diff and in
every status GitHub shows.

## The "blind to entries never written" shape

The brief asked which PRs share the shape of the fixed defect, a check that verifies
entries were not altered while being blind to entries never written.

- **#164** is the fix for that shape at the archive-log level, and its tests assert
  counts before chains, deliberately and with the reasoning recorded in the test
  docstrings. It does not repeat the shape.
- **#160 / #161 / #162 / #163** are the fix for the same shape in the JSON stores
  (`TombstoneStore`, `ProposalStore`, `SubmissionQueue`) and likewise assert counts.
  #163 additionally touches the PREMIS event constructors, but adds no new writer and
  no new read-modify-write.
- **#165** is a check of a different kind that deliberately avoids the shape, and
  proves it does with `test_both_sides_emptied_together_is_two_findings_not_zero`.
- **#157, #158** do not touch the log, chain or attestation path.
- **#152** adds a large new subsystem but does **not** repeat this shape: nothing in
  `src/ledger/ai/` imports `ledger.chain`, `ledger.attestation` or
  `ledger.metadata.premis`, and its one persistent write, `RateLimiter._check_daily_cap`,
  holds `file_lock` across the whole read-check-write. It does carry the *neighbouring*
  shape twice over, in a worse place: two merge-gate tests that cannot fail, guarding
  the no-outing guarantee. See #152.

The one place the shape survives in an approved form is the residual noted under #164:
the AST gate proves a log write is inside *a* lock, not inside the lock for *that*
path, and `apply_update` takes a different lock from `append_event`.

## Order of operations

Every PR needs a branch update before it can merge, because
`strict_required_status_checks_policy: true` requires an up-to-date branch. The order
below minimises the number of times that has to happen and puts the conflict on the
branch that has to be pushed anyway.

1. **#165.** CLEAN, all checks green, no interaction with anything else. Merge it
   first and the rest rebase onto a tree with a correct ruleset mirror.
2. **#157**, then **#158.** Update branch, let CI re-report, merge. They touch
   different files from each other and from everything else. Doing them early keeps
   the action digests current before the larger merges.
3. **#164**, after the author drops `docs/plans/improvement-plan.md`. That push
   re-runs CI for free. Merging #164 before #163 is the cheaper order: #164 is
   otherwise ready right now, and #163 has to be pushed regardless to un-starve its
   three missing required checks, so let that same push absorb the conflict.
4. **#163.** Rebase onto `main` (now containing #164). Three things happen in that one
   push: the three starved required contexts finally run; the conflict with #164 in
   `ingest.py`, `reading_room_enclave.py` and `replicate.py` is resolved; and the
   inherited CodeQL alert at `server.py:1242` can be cleared. Carry over the `sast`
   string fix from the #162 finding while you are there.
   **Regeneration step:** #163 brings `tools/check_coverage_floors.py`, which fails the
   build if any module matching a `[tool.ledger].security_core` glob has no floor. #164
   adds no module under those globs, so no floor needs adding, but re-run `make cov`
   after the rebase to confirm the declared floors still hold on the merged tree rather
   than on the tree they were measured against.
5. **Close #162, #161, #160 by hand, in that order**, as superseded by #163. They will
   not auto-close: `required_linear_history` rules out a merge commit, and squash or
   rebase rewrites the SHAs. Verify before closing that
   `git diff origin/main...origin/<head>` is empty for each. Note that #160 has an
   unresolved review thread; closing the PR closes the thread with it, so make sure the
   underlying CodeQL alert was actually fixed on #163 rather than just carried away.
6. **#152**, last, and not until its must-fix list is done. It is the one PR in this
   queue that should not merge in any order. When it does come back:
   **regeneration step,** `docs/ARCHITECTURE.md` must be hand-resolved to **41**
   subcommands, because both sides of the conflict are wrong and
   `test_architecture_doc_states_the_real_subcommand_count` re-derives that number from
   the live parser; **changelog reposition,** keep both bullets at the first
   `### Added` under `## [Unreleased]` rather than taking either side; and the eval
   evidence under `docs/data/ai-eval/` has to be **re-generated by an actual live run**
   once the scoring is fixed, since the committed `passed`/`failed` columns are the
   thing being corrected. Merging #163 first also adds a `Makefile` conflict to this
   PR, in the `.PHONY` block and around `verify:`.

## What was verified, and what is taken on trust

### Verified directly in this pass

- Every merge state, independently of GitHub's `mergeStateStatus`, with
  `git merge-tree --write-tree origin/main origin/<head>`. Only #152 conflicts.
- The #163 / #164 cross-conflict, by building the merge tree of `main` and #163 with
  `git commit-tree` and merging #164 onto it, and again in the reverse order. Same
  three files both ways. No repository ref was created or moved; the simulation writes
  only unreferenced objects.
- The stack containment, with `git cherry` and `git merge-base --is-ancestor`. #160's,
  #161's and #162's head commits are all ancestors of #163's head, by identical SHA.
- The CI reality per PR, from `gh pr checks` and from `check-suites` per head SHA, and
  the absence of `codeql` / `semgrep` / `scorecard` runs on #161, #162 and #163 from
  `gh run list --branch`. All eight workflows confirmed `active`.
- The `protect-main` ruleset: thirteen required contexts, `strict_required_status_checks_policy: true`,
  `required_signatures`, `required_linear_history`, `required_review_thread_resolution: true`,
  zero required approvals. Read-only GET.
- The live `bypass_actors` value, compared byte for byte against #165's committed
  mirror. Read-only GET.
- That `origin/main`'s `src/ledger/metadata/premis.py` still uses `os.getpid()` for its
  temp file and has neither `file_lock` nor `append_event`.
- That `origin/main`'s `tests/test_security_critical_paths.py` still contains
  `test_corrupt_proposal_file_reads_as_empty`, under `@pytest.mark.disclosure`.
- That no `sast` extra exists in `pyproject.toml` on #162's branch.
- That `docs/plans/` does not exist on `main` and is not covered by `.gitignore`.
- Commit signature and attribution on all 24 commits across the nine PRs: every one
  `verified=true, reason=valid`.
- The changelog heading structure on `main`, and each PR's hunk anchors against it.
- That #165 adds no mutating GitHub API call and no network call.
- The actual test bodies of `tests/test_audit_log_concurrency.py`,
  `tests/test_no_unlocked_log_rewrites.py`, `tests/test_attestation.py` (new cases),
  `tests/test_coverage_floors_gate.py`, `tests/test_premis_linking_identifier_types.py`,
  `tests/test_adr_integrity.py`, `tests/test_claims_gate.py` (new cases) and the
  assertion lines of `tests/test_silent_loss_stores.py`, read as source rather than
  taken from PR descriptions.

### Taken on trust

- **All coverage numbers.** The 92 and 91 percent figures for `grants.py` and
  `consent.py`, and every value in `[tool.ledger.coverage_floors]`. I did not run
  `make cov`; doing so would write to the working tree. That the floors are met rests
  on #161's `lint / type / test` job passing, which does run the new gate.
- **All measured concurrency figures** ("1, 2 and 1 of 40 survived", "39 of 40 lost",
  "40 of 40 now survive"). The tests that assert the fixed behaviour were read and are
  sound; the before-numbers are the authors' measurements on the unfixed tree.
- **The "proof each new test can fail" transcripts** quoted in the #160 and #163 bodies.
  I read the tests and judged them capable of failing, but did not re-run them against
  the pre-change tree.
- **The cause of the missing workflow runs** on #161, #162 and #163. The absence is
  verified; the reason is not, and is not guessed at.
- **#152's findings were established by a delegated read of the branch source**, not by
  running the AI suites. The reasoning that `_is_unsafe` cannot fire on a
  post-`verify_claims` claim list is analytic, from reading both functions, and it is
  the load-bearing claim of that section: if it is wrong, the 44/44 result is real and
  the two tests are fine. It is worth a second reader. The subcommand-count trap (38 at
  the merge base, 39 on main, 40 on the branch, 41 after merging) was derived from the
  three file versions, not by running `ledger --help`.
- **The GitHub role behind `actor_id: 5`.** #165's mirror matches the live value
  exactly, which is what matters; what repository role that id denotes is not asserted
  here, because nothing in the repository or in a read-only API response states it.

### Corrections to the framing this triage was given

1. **The PREMIS serialization fix has not landed.** It was described as "today's PR",
   already merged. It is **PR #164, still open**. `main` at `198351b` still has the
   `os.getpid()` temp name and no `append_event`. The most recent merge to `main` is
   #159, the moderation log. The "40 concurrent takedowns left 1 of 40 surviving while
   `verify_chain().ok` stayed `True`" measurement is accurate, and it is #164's finding
   about code that is live on `main` right now.
2. **The queue was eight PRs and is nine.** #165 arrived mid-pass. Two further worktree
   branches have no PR yet.
3. **The four "stacked" PRs are not stacked.** All four target `main`; no base was
   retargeted despite #163's body saying they would be.
