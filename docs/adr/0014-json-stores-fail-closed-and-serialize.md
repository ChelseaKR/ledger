# 0014. The archive's JSON stores fail closed on damage and serialize their writes

Status: Accepted

Date: 2026-08-27

Resolves: #155, #154.

Numbering: 0013 is claimed by the open AI-layer branch (#152,
`0013-ai-at-the-edges.md`). This ADR takes 0014 so two unmerged branches cannot land
the same number; the gap closes when #152 merges.

## Context

Nine of this archive's small stores persist as one JSON document that a mutation
rewrites *whole*: read the list, modify it, write a temp file, rename it over the
target. `src/ledger/_filelock.py` was written because that shape has a failure mode,
and it states it in the strongest terms this repository uses anywhere:

> two concurrent POSTs can both read the same starting file, each append its own
> change, and the second rename clobbers the first -- silently dropping, for example,
> a consent *withdrawal* request. A lost withdrawal is the worst class of bug this
> project can have, so the read-modify-write critical section must be serialized.

Eleven modules took that lesson. Three did not take all of it, and the gap divides
into two halves that are the same defect seen from opposite ends.

**`TombstoneStore` never took the lock.** `add` and `confirm` are bare
read-modify-writes. A tombstone is not an audit row: it is the durable instruction
that tells a reattaching replica to delete a copy it still holds. Losing one means a
record a steward took down stays on a mirror with nothing left in the system that
will ever ask for its removal (#155). Hard Rule 4.

Measured on the code as it stood, 40 concurrent `add` calls released from a common
barrier, three trials:

| | trial 1 | trial 2 | trial 3 |
| --- | --- | --- | --- |
| tombstones on disk, of 40 | 1 | 1 | 1 |
| writers that raised `LedgerError` | 37 | 34 | 35 |
| tombstones lost | **39 (98%)** | **39 (98%)** | **39 (98%)** |

The loss is worse than #155's estimate of ~85%, and for a second reason the issue did
not name. `_write` built its temp file as `f"{name}.{os.getpid()}.tmp"`. Every thread
of the browse server shares one pid, so that is a *shared* name: concurrent writers
overwrite one another's temp file and then race to `os.replace` a path one of them has
already renamed away, which is where the 34 to 37 raised errors come from. The
unlocked read-modify-write loses tombstones silently; the shared temp name loses them
loudly, by failing the takedown outright.

**`ProposalStore` and `SubmissionQueue` took the lock but not the fail-closed read.**
Both swallowed `(OSError, ValueError)` in `_read` and returned `[]`. A damaged file
therefore read as "nothing was ever filed" -- and because every mutation is a
read-modify-write, the next `add` wrote that empty list back over the damaged bytes
and destroyed the history for real, converting a recoverable file into an
unrecoverable one (#154). `ModerationLogStore` was built against exactly this failure
mode and its docstring names these two stores as the ones still exhibiting it.

A `disclosure`-marked, merge-blocking test named
`test_corrupt_proposal_file_reads_as_empty` asserted the empty-read behaviour. The
defect was not merely untested; it was pinned in place by a safety-marked test.

## Decision

### 1. A damaged store raises; a missing store is still empty

`ProposalStore._read` and `SubmissionQueue._read` now distinguish absence from
damage. `FileNotFoundError` returns `[]`, because a fresh archive has filed nothing
and must need no setup (installability). Every other failure -- an `OSError` on read,
bytes that are not JSON, valid JSON of the wrong shape -- raises `LedgerError`.

Every failure mode leaves by the same door, so a caller catches one error family and
can never mistake corruption for an empty history. This is the rule
`ModerationLogStore` already applies, now applied to the two stores it was pointing
at.

The decisive property is not the raise but what the raise preserves: the damaged
bytes stay on disk for a human to recover, instead of being overwritten by the next
mutation.

### 2. `TombstoneStore` mutations are serialized, and its temp name is unshareable

`add` and `confirm` hold `ledger._filelock.file_lock` across the whole
read-modify-write, as every sibling JSON store in this package does. The temp file
carries `secrets.token_hex(8)` rather than the process id, matching
`ModerationLog.write`.

The lock alone would fix both mechanisms, since serialized writers never hold the temp
file at the same time. That is already this repository's stated position:
`IdentityVault.store` documents the identical hazard and answers it with the lock,
keeping its pid-named temp. The random suffix here is defence in depth on top of that,
matching `ModerationLog.write`, and it is applied to `dualcontrol.py` and `review.py`
too because both carried the same shared name.

It is **not** a repo-wide change. Eight other modules still build pid-named temp files
and are safe for the reason `identity.py` gives: they hold the lock. Nothing in this
ADR reopens that.

### 3. An unreadable review queue is reported, never rendered as an empty one

Making the read raise means the steward console must say something. It says what is
wrong: `/steward` catches `LedgerError` around the queue read and renders
`sw_queue_unreadable` (localized across en/es/fr/ar) instead of the empty-queue
message.

A 500 would be honest but useless, and the empty-queue message would be neither. Hard
Rule 2 says nothing is published by inaction; the mirror of that rule is that nothing
may be *forgotten* by inaction, and a steward shown an empty console while
submissions wait is exactly that. This follows the pattern
`_moderation_section_html` established: report the failure in place, never as
absence.

### 4. `tombstones.py` and `review.py` get their own coverage floors, off the pooled scope

Reported on their own at their measured values (89% and 97%), not appended to the
pooled `access/*` + `consent.py` + `dualcontrol.py` include list. `--fail-under`
gates the TOTAL row of a report, not each module in it, so a module added to that
list inherits its neighbours' average -- which is how `grants.py` (92%) and
`consent.py` (91%) sit under a line that passes at 95%. This follows the precedent
ADR-less `moderate.py` set and that the `Makefile` and `CONTRIBUTING.md` both
describe.

## Consequences

| | before | after |
| --- | --- | --- |
| tombstones surviving 40 concurrent takedowns | 1 of 40 | 40 of 40 |
| takedown writers raising on a shared temp name | 34 to 37 of 40 | 0 |
| a corrupt proposal or queue file | reads as `[]`, then is overwritten | raises; bytes preserved |
| a corrupt queue on `/steward` | renders as "no submissions awaiting review" | renders as unreadable, in four languages |
| the three stores in scope using a pid-named temp file | 3 | 0 (8 elsewhere unchanged, safe under their locks) |
| modules with a coverage floor of their own | 1 | 3 |
| tests asserting the empty-read behaviour | 1, `disclosure`-marked | 0; replaced by its inverse |

Costs and open edges, stated rather than left to be discovered:

- **A raise is a behaviour change for every caller of these two stores.** The steward
  console is handled above. On the CLI, `main` already renders a `LedgerError` as a
  one-line message and exits 2, so `ledger propose` against a damaged store now prints
  `error: proposal store could not be parsed: <path>`, exits 2, and leaves the file
  byte-for-byte intact, where it previously appended to an empty list and overwrote
  it. That is the intended change: acting on a wrongly-empty proposal list is how a
  dual-control threshold gets satisfied by a store that lost the other approval.
- **`file_lock` is single-host.** `flock` serializes threads and processes on one
  machine and does not coordinate across NFS or multiple hosts. This closes the
  contention the threaded browse server actually creates; a multi-writer, multi-host
  deployment must serialize another way, as `_filelock.py` and `ADOPTING.md` already
  say. Nothing here widens that claim.
- **Only the three stores named in #155 and #154 were changed.** Other JSON stores in
  this package hold the lock already, and no attempt was made here to audit whether
  each of them also fails closed on a damaged read rather than returning an empty
  collection. That sweep is `MP-07` in
  [`../MULTIYEAR-PLAN.md`](../MULTIYEAR-PLAN.md); this ADR does not claim it was
  done.
- **The pooled floor is still pooled.** This ADR keeps two more modules out of it and
  does not fix it. Per-module floors for the pooled scope are `MP-04`, and that change
  lowers two published numbers to their true values, which is a decision of its own.
- **A tombstone whose write fails still raises.** Fail-closed is deliberate: a
  takedown that cannot record its tombstone must not report success, because the
  propagation guarantee is the whole point of the record.

### Alternatives considered

- **Return `[]` but log a warning.** Rejected: the destructive step is the *next*
  write, not the read, and a warning does not stop it. The file would still be
  overwritten, and the only record of the loss would be in a log the archive does not
  durably keep.
- **Repair the damaged file automatically, keeping what parses.** Rejected: a
  partially-parsed proposal list is indistinguishable from a truncated one, so
  "repair" would silently ratify whatever the damage left behind. Refusing and
  preserving the bytes lets a human make that judgement with the evidence intact.
- **Fix the shared temp name only, and skip the lock.** Rejected: it would convert
  loud loss into silent loss, which is strictly worse. The measured 98% would fall to
  something like #155's ~85% and stop raising, which reads as an improvement while
  being a regression in detectability.
- **Add `tombstones.py` and `review.py` to the pooled include list.** Rejected for
  the reason in decision 4; at 89% and 97% against neighbours at 100%, both would read
  as covered because of the company they keep.
