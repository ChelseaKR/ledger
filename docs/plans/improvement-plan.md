# Improvement plan — audit pass, 2026-08-28

Working notes for an uncommitted audit pass. **Nothing here is committed**; the
owner withheld commit permission for this session. Every change described below
lives in the working tree only.

## Standing constraints for this pass

- No commits, no pushes, no PR writes. Read-only git and `gh` only.
- Do not duplicate the in-flight stack: PRs #160 -> #161 -> #162 -> #163.
- Verification is always `make verify < /dev/null; echo "EXIT=$?"`, never piped
  through `tail`/`head` (that reports the pager's exit code, not make's).

## Baseline (measured, not assumed)

- `main` @ `198351b`, working tree clean at session start.
- `make verify` on `main`: **`verify: all gates green`, `EXIT=0`.**
- 9 open issues, 7 open PRs, CI green for the last 20 runs.

## Issue classification

| # | Title (short) | Class | Disposition |
| --- | --- | --- | --- |
| 155 | TombstoneStore has no file locking | Real defect | **In flight, PR #160.** Do not duplicate. |
| 154 | ProposalStore silently discards history | Real defect | **In flight, PR #160.** Do not duplicate. |
| 153 | AI grounding: single-token nickname | Real defect | **Blocked.** `src/ledger/ai/` is not on `main` (only a stale `__pycache__`); it arrives with PR #152, which is `CONFLICTING`. Cannot be fixed or tested from `main`. |
| 99 | Help wanted: community-archivist review | Aspiration (human-only) | Needs a volunteer archivist. Not agent-satisfiable. Stale branch link in body noted by a prior pass. |
| 83 | Remove code-quality waivers, raise coverage floor | Missing feature, partial | Two of three halves already done. Per-module floor is **in flight, PR #161**. |
| 82 | Accountable-owner + independent risk reviews | Aspiration (human-only) | Artifacts drafted and honestly marked pending sign-off. Correct as-is. |
| 81 | Manual + browser accessibility evidence | Aspiration (human-only) | Automated half done incl. blocking 320px reflow gate. NVDA/VoiceOver needs a human. |
| 80 | Exercise and finish the first trusted release | Aspiration (blocked) | Blocked on #82, #81, PyPI credentials, owner decisions. |
| 78 | Harden-Runner deny-by-default egress | Missing feature, partial | 16 of 24 jobs already `block`. The 8 `release.yml` jobs are blocked on #80 (no completed run to derive an allowlist from). |

## The finding this pass adds

**Archive-level PREMIS audit logs do an unlocked whole-document
read-modify-write.** This is the exact defect class of #155 (which is about the
tombstone *store*), sitting in the accountability *log* beside it, and no
in-flight PR touches it. Verified: the #160->#163 stack adds `file_lock` only to
`tombstones.py`.

Unprotected append sites on `main`:

| site | file written | reachable concurrently from |
| --- | --- | --- |
| `ingest.Archive.log_takedown` (ingest.py:1075) | `logs/takedowns.premis.json` | threaded browse server (server.py:2040), `moderate.py:664` |
| `replicate._append_takedown_receipt` (replicate.py:102) | the **same** `takedowns.premis.json` | a **separate process** (`apply_tombstones`) |
| `ingest.Archive.log_grant_use` (ingest.py:1089) | `logs/grant-uses.premis.json` | server.py:394 (holds a `threading.Lock`, not `file_lock`) |
| `reading_room_enclave.EnclaveStore._log` (reading_room_enclave.py:443) | enclave events log | sibling methods in the same class **do** take `file_lock` |
| `ingest.Archive.rekey_identity_vault` (ingest.py:1377) | `logs/key-rotations.premis.json` | low contention, same class |

Measured on `main`, 40 threads from a common barrier, three trials each:

```
log_takedown:  1/40 events on disk; 35 writers raised FileNotFoundError; chain_valid=True
log_takedown:  2/40 events on disk; 33 writers raised FileNotFoundError; chain_valid=True
log_takedown:  1/40 events on disk; 36 writers raised FileNotFoundError; chain_valid=True
```

Two compounding causes, the same pair #160 found in `tombstones.py`:
1. no lock around read-modify-write, so appends are lost; and
2. `PremisLog.write` builds its temp file as `f"{name}.{os.getpid()}.tmp"` — a
   name **shared by every thread in one process** — so writers clobber each
   other's in-flight temp file and then race to `os.replace` a path another
   thread already renamed away.

**The part that matters most:** `chain_valid=True` in every trial. The
hash-chain tamper-evidence check reports the log **intact** while 38 of 40
accountability events are gone, because each surviving write rebuilds a
self-consistent chain from what it happened to read. A gate that reports success
over a log that silently lost 95% of its entries is the portfolio's governing
defect shape, and it is guarding this repo's Hard Rule 4 evidence.

## Phases

- [x] **P0** Read repo docs, classify all 9 issues, review all 7 PRs, baseline `make verify`.
- [x] **P1** Find the defect class above; prove it on `main`; prove no PR covers it.
- [x] **P2** Quantify the suite's hole: does any test exercise concurrent audit-log append?
- [x] **P3** Fix: `file_lock` + unique temp name for every PREMIS append site. Test fails before, passes after.
- [x] **P4** Add a structural gate that makes the *class* unrepeatable (every whole-document rewriter is locked), and break/restore it.
- [x] **P5** Audit every existing gate for the cannot-fail shape; break each one, watch it fail, restore.
- [ ] **P6** Re-run `make verify`; record the result.

## Running log

- Baseline `make verify` on `main`: EXIT=0, all gates green.
- Repro of the PREMIS append race written and run: see table above.

### P2 result — the suite's hole, quantified

`tests/test_filelock.py` is the repo's dedicated concurrency suite. It covers
**six** things by name: the `file_lock` primitive, `ConsentRequestStore`,
`SubjectTokenStore`, `SubmissionQueue`, `ProposalStore`. It covers **no PREMIS
log at all**, and no `TombstoneStore` (that is #155). `tests/test_ingest_concurrent.py`
covers only CAS blob writes. The enumeration *is* the gate, so anything nobody
remembered to add is uncovered and green.

Two findings make the green hollow rather than merely thin:

1. `tests/test_security_critical_paths.py::test_corrupt_proposal_file_reads_as_empty`
   **asserted the #154 defect as intended behaviour.** CI was green *because* a
   test pinned the silent-data-loss path as correct. (In flight, PR #160/#162.)
2. `tests/test_audit_chain.py::test_audit_log_chains_covers_archive_level_logs`
   calls `log_takedown` **sequentially, twice**, then asserts the chain verifies
   and that tampering is caught. Lost appends are invisible to tamper-evidence,
   so this test passes at full marks over a log that lost 95% of its entries.

### P3 result — fail before, pass after

`tests/test_audit_log_concurrency.py`, 6 tests. Against unmodified `main`:
**6 failed**. Headline failure was not even a wrong count, it was
`FileNotFoundError` x 23 of 24 writers out of the shared temp-file race. After
the fix: **6 passed**. The standalone repro moved from 1-2 of 40 events surviving
to **40/40, zero exceptions**.

### P4 result — the class closed, and the gate broken on purpose

`tests/test_no_unlocked_log_rewrites.py` is an AST gate with **no allowlist**: any
function that writes a `PremisLog` back outside `file_lock` fails the build.

- **Broke it:** reverted `Archive.log_takedown` to the unlocked read-modify-write.
  Gate failed, naming `src/ledger/ingest.py:1133 in log_takedown()`.
- **Restored:** passes.

It also carries 8 tests of its own teeth, because an AST gate is exactly the kind
that silently matches nothing after a rename: the detector is asserted to fire on
the defect, stay quiet on the fix, not be fooled by a non-`file_lock` `with`, not
credit an enclosing function's lock to a nested one, and to have actually scanned
a non-empty corpus (>40 files, >=5 real `PremisLog` users).

### P5 result — every gate broken on purpose, then restored

| gate | how it was broken | result | restored |
| --- | --- | --- | --- |
| `tools/check_claims.py` (forbidden direction) | appended the retired overclaim `structured logs and metrics` to README | **EXIT=1**, named `no-metrics` | EXIT=0 |
| `tools/check_claims.py` (deletion direction) | removed `--locked` from the Makefile install target | **EXIT=1**, named `install-refuses-to-re-resolve` | EXIT=0 |
| `tools/check_hygiene.py` | added a bare `# noqa: S310` with no reason | **EXIT=1**, named file and line | EXIT=0 |
| `ledger.accessibility_check web` | lightened `--muted` to `#bbbbbb` | **EXIT=1**, reported 1.92:1 and 1.75:1 vs AA 4.5:1 | EXIT=0 |
| `make i18n` | added a new gettext string in source without re-extracting | **EXIT=2**, showed the `+msgid` diff | EXIT=0 |
| `tests/test_no_unlocked_log_rewrites.py` (added this pass) | reverted `log_takedown` to the unlocked cycle | **failed**, named `ingest.py:1133 in log_takedown()` | passes |

Two notes on method, both cases where a first attempt proved nothing:

- My first `check_claims` break edited a string that was not in the README, and my
  first a11y break landed inside a docstring rather than rendered markup. Both
  produced a green gate that looked like a finding and was not. **A break test that
  does not verify the break landed is itself a check that cannot fail**, so each was
  re-run with the mutation confirmed present first.
- My first `make i18n` break edited the committed POT directly and the gate passed.
  That is correct: the target regenerates the POT before diffing, so editing it is
  not its threat model. Re-broken against the real threat (a source string with no
  re-extraction) it failed with EXIT=2.
- I also read one exit code through `tail` early on and got `tail`'s `0`. Re-run
  without the pipe it was `1`. Recorded because it is exactly the trap in the brief.

Gates checked and found sound without needing repair: `-m disclosure` selects 308 of
1273 tests and `-m preservation` 185 (neither marker is vacuous);
`tests/test_real_corpus_evidence.py` re-derives aggregates from the committed rows
and binds prose both directions, so it is not a self-regenerating baseline.

### The second defect this pass found — a signed statement the archive did not compute

`attestation._every_log_head` read each bag's log through `Archive.record_events`,
which swallows a damaged log and returns no events (correct for the browse surface it
was written for). `_log_head([])` returns the **genesis sentinel**, which that
function's own docstring defines as the value "distinguishing 'no history yet' from
any real history".

So a bag with a corrupt `premis.json` was attested as having no history at all, inside
`chain_head_summary` -- the field the module docstring says exists so that "two dated
attestations are enough to catch a rollback", published at `/proof` and optionally
**signed**. Measured on `main`:

```
healthy head for the bag:            ebaf4736cc34ac2e ...
head after corrupting premis.json:   0000000000000000 ...
IS THE GENESIS SENTINEL:             True
```

Corrupting one file was a way to make the archive sign the claim that the file's log
was empty. This is the domain rule exactly: a value the system did not compute,
published as though it were measured.

Fixed by `_read_or_refuse`: an *absent* log still attests as empty (it genuinely is),
a *present but unreadable* one raises and no attestation is produced.
`tests/test_attestation.py` gains both cases -- the refusal, and the check that a
legitimately empty bag still attests cleanly, so the fix is not just a different lie.
Fail-before: 1 failed / 17 passed. Pass-after: 18 passed.

## Files changed in this pass (all uncommitted, working tree only)

| file | change |
| --- | --- |
| `src/ledger/metadata/premis.py` | new `append_event(path, *events)`, the one locked appender; `PremisLog.write` uses a random temp suffix and cleans up a failed write |
| `src/ledger/ingest.py` | `log_takedown`, `log_grant_use`, `rekey_identity_vault` moved onto `append_event`; `apply_update` serialized on the record manifest; `_read_versions` fails closed; `_append_version` locked |
| `src/ledger/replicate.py` | `_append_takedown_receipt` moved onto `append_event` (the cross-process case) |
| `src/ledger/reading_room_enclave.py` | `_log` moved onto `append_event`, matching its own siblings |
| `src/ledger/bag.py` | `atomic_write_text` random temp suffix and temp cleanup on failure |
| `src/ledger/demo.py` | hand-rolled read-record-write replaced with `append_event` |
| `src/ledger/attestation.py` | `_read_or_refuse`; `_every_log_head` fails closed on a present-but-unreadable log |
| `tests/test_audit_log_concurrency.py` | **new**, 6 behavioural tests |
| `tests/test_no_unlocked_log_rewrites.py` | **new**, the AST gate plus 8 tests of its own teeth |
| `tests/test_attestation.py` | 2 tests: refusal on a damaged log, clean attestation on a legitimately empty bag |
| `docs/adr/0018-...md` | **new**, the decision (0014-0017 are claimed by the in-flight stack) |
| `CHANGELOG.md` | Unreleased entry |
| `docs/plans/improvement-plan.md` | this file |

## Blocked, and why

- **#153** (AI grounding, single-token nickname). `src/ledger/ai/` is not on `main`;
  it arrives with PR #152, which is `CONFLICTING`. Nothing to fix or test from here.
- **#99, #82, #81 (human half), #80.** Each needs a named human: a volunteer community
  archivist, an accountable owner's signature, an assistive-technology user's
  walkthrough, PyPI credentials and owner decisions. Every artifact is already drafted
  and honestly marked pending. Automation must not fill these in.
- **#78.** The 8 `release.yml` Harden-Runner jobs stay in audit mode because
  `release.yml` has never run, so there is no observed traffic to derive an allowlist
  from. Genuinely blocked on #80.
- **#83, #155, #154, and the pooled coverage floor.** In flight on PRs #160/#161/#162.
  Not duplicated.
- **Surfacing skipped logs on the steward audit page.** `Archive.audit_events` silently
  `continue`s past an unreadable log, while its neighbour `audit_fixity` turns the same
  failure into a visible failing result. Making that visible needs a new user-facing
  string in `en`/`es`/`fr`/`ar` (the i18n gate enforces completeness parity across all
  four). Machine-fabricating a safety-critical warning in three languages the author
  cannot check is the wrong call, so this is left stated rather than done. The signed
  path — the one that publishes a claim — **is** fixed.

## Final state

`make verify < /dev/null; echo "EXIT=$?"` -> **`verify: all gates green`, `EXIT=0`**,
**1273 tests passed, 0 failed**. Baseline at `HEAD` was 1257; this pass adds 16 tests
(6 behavioural, 8 for the structural gate and its own teeth, 2 for the attestation
refusal). `make demo` also re-run end to end: `EXIT=0`, no-outing proof intact,
`PREMIS now contains a CONSENT_CHANGE event: True`.

Working tree only. Nothing committed, nothing pushed, no PR touched.
