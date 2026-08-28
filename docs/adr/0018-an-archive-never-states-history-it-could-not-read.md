# 0018. An archive never states history it could not read, and never loses an append

Status: Accepted

Date: 2026-08-28

Relates to: #155 (the same defect class, in the tombstone store). Extends the rule
`ledger._filelock` states, and ADR 0012's PREMIS discipline, to the event logs
themselves.

> Numbering note: 0014 through 0017 are claimed by the in-flight stack (#160, #161,
> #162, #163). This ADR takes 0018 so the numbers do not collide on merge.

## Context

`ledger._filelock`'s module docstring names the hazard and the stakes: a whole-document
read-modify-write loses concurrent writes, and "a lost withdrawal is the worst class of
bug this project can have, so the read-modify-write critical section must be
serialized." Eleven modules took that lesson. The PREMIS **logs** did not, and neither
did the record version index.

Two separate failures were measured on `main`.

**1. Appends were lost, and the guard that should have noticed could not.**

`Archive.log_takedown`, `Archive.log_grant_use`, `Archive.rekey_identity_vault`,
`replicate._append_takedown_receipt`, `ReadingRoomEnclave._log`, and
`Archive.apply_update` each read a whole PREMIS log, appended, and wrote it back with no
lock. `PremisLog.write` compounded it by naming its temp file from `os.getpid()`, which
is identical in every thread of one process, so concurrent writers truncated each
other's temp file and raced to rename a path another had already renamed away.

40 concurrent `log_takedown` calls from a common barrier, three trials: **1, 2, and 1**
of 40 events survived; 35, 33, and 36 writers raised `FileNotFoundError`.

The part that makes this an architectural decision rather than a bug fix is the third
column of that measurement: `verify_chain().ok` was **`True` every time**. A hash chain
answers "was an entry altered". It cannot answer "was an entry ever written", because
each surviving writer rebuilds a chain that is perfectly self-consistent over whatever
it happened to read. `Archive.audit_log_chains` therefore reported a log that had
silently lost 95% of its entries as intact. Tamper-evidence is not, and cannot be made
into, a completeness guarantee.

**2. An unreadable log was published as an empty one.**

`attestation._every_log_head` read each bag's log through `Archive.record_events`, which
swallows a damaged log and returns no events. That is right for `record_events`, which
feeds a browse surface where one bad bag must not blank the page. It is wrong here,
because `_log_head([])` returns the genesis sentinel — the value its own docstring
defines as what "distinguish[es] 'no history yet' from any real history".

So a bag with a corrupted `premis.json` was attested as having no history, inside
`chain_head_summary`: the field this project publishes at `/proof`, optionally **signs**,
and describes as the thing that makes "two dated attestations enough to catch a
rollback". Measured: a healthy bag's head `ebaf4736…`, and after truncating its
`premis.json`, `0000…0000` — the genesis sentinel exactly. Corrupting one file was a way
to get the archive to sign the statement that the file's log was empty.

## Decision

**An append to a log that already exists on disk goes through one locked writer.**
`ledger.metadata.premis.append_event` holds `file_lock` across read, record, and write.
`PremisLog.write` stays public for building a fresh log, and its temp file now carries a
random suffix rather than the process id. Every existing appender was moved onto it,
including `demo.py`, which is the worked example a reader copies from.

**A log that is absent means no history. A log that is present and unreadable means
unknown history, and unknown history is never stated.** `attestation._read_or_refuse`
raises, so no attestation is produced at all, rather than one asserting a genesis head.
The same fail-closed rule is applied to the record version index
(`Archive._read_versions`), where the lenient read was worse than elsewhere because it
fed a *writer*: the next append would have rewritten the file with one entry and erased
every prior snapshot with no error and no event.

**The class is closed structurally, not by enumeration.**
`tests/test_no_unlocked_log_rewrites.py` is an AST gate with no allowlist: any function
that writes a `PremisLog` back outside `file_lock` fails the build.

## Consequences

- `chain_head_summary`, `build_attestation`, and so `ledger attest-health` now **fail**
  on an archive holding a damaged PREMIS log, where before they produced a signed
  document. That is the intended trade: a steward is told to repair the log, rather than
  publishing a claim about history nothing read. A legitimately empty bag is unaffected,
  and a test pins that, so the fix is not merely a different false statement.
- `record_events` and `audit_events` stay lenient on purpose. They feed the browse and
  steward-audit surfaces, where degradability is the right property. The strict reader
  is scoped to the signed claim. The two readers are now visibly different things with
  different docstrings, instead of one reader used for both.
- `Archive.apply_update` holds a lock for the whole multi-file record mutation. The lock
  file is a sibling of the record manifest (`<id>.json.lock`), never a file inside a bag,
  because a bag holds only what its tag manifests cover. `_append_version` nests inside
  it; that is the only nesting in the package, so the order cannot deadlock.
- `server.py`'s `_GRANT_LOG_LOCK` (a `threading.Lock`) is now redundant rather than
  load-bearing: it covered one call site in one process, and could never have serialized
  `apply_tombstones`, which writes the same takedown log from a **separate process**. It
  is left in place; removing it is a follow-up, not a correctness need.
- This ADR deliberately does not claim the logs are now safe on multi-host storage.
  `flock` is single-host, as `ledger._filelock` already documents for adopters.
