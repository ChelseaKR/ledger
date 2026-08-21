# 0012. The PREMIS Object is the payload within a record, not the bytes

Status: Accepted

Date: 2026-08-21

Resolves: #149. Closes the question ADR 0010 left open in its consequences.

## Context

Format identification is a function of **the bytes and the filename**: a content
signature is read off the bytes, and when no signature matches, the registry consults
the extension. A PREMIS `format identification` event was linked to the payload's
**content address**, which is a function of the bytes alone — and the content store
deduplicates, so identical bytes under any number of names are one address.

Those two facts contradict each other the moment identical bytes arrive under two names
that identify differently. The real corpus supplied the instance:
`office/wordprocessing/IBM_DCA/testIBM_DCA.rft` is byte-identical to three
`testIBMDisplayWrite*.doc` files. Measured on the code as it stood before #148 (commit
`dc70b05`, the trial archive kept and re-read with today's detectors — see
[`../data/real-corpus/opf-format-corpus-366f068c.before-adr-0012.json`](../data/real-corpus/opf-format-corpus-366f068c.before-adr-0012.json)):

| on the real corpus, before | |
| --- | --- |
| format-identification events | 679 |
| distinct `linkingObjectIdentifier` values they were linked to | 625 |
| identifiers carrying more than one event | **16**, carrying 70 events |
| identifiers carrying more than one *verdict* | **1** — four events, two verdicts (`unidentified` and `at-risk` OLE2 `fmt/111`) |
| payloads with an event that is about *that payload* | **0 of 679** |

A consumer keyed by `linkingObjectIdentifier` — the correct way to read PREMIS — saw
whichever of the four was written last. #148 fixed the instance by fixing the registry
(all four now identify identically), which is why the collision count printed 0, but
`a.txt` and `a.md` with identical contents reproduce the class on any archive.

The question #149 asked was the right one: **what is ledger's PREMIS Object?**

- If it is the content-addressed blob, identification must be a pure function of the
  bytes, and the extension step — which names 106 of 679 real files, 15.6% — cannot
  contribute to an event linked this way. The record would then say one thing
  (`text/markdown` via extension) and the log another (`unidentified`), re-opening the
  divergence ADR 0010 had just closed.
- If it is the payload within a record, `linkingObjectIdentifier` was pointing at the
  wrong thing, the serialisation has to say what it points at, and existing bags need a
  reading.

## Decision

### 1. The Object is the payload within a record

ledger's PREMIS Object for format identification and fixity is the **File within the
Representation**: one payload, in one record. This is how PREMIS models it and how every
file-level preservation repository does it — two files with identical bytes are two
Objects that happen to share a fixity value. A content address is a *characteristic* of
the object (PREMIS `objectCharacteristics/fixity`), not its identity.

The identifier is `<record_id>/<filename>`, typed `ledger-payload`. A record id is a
single allow-listed path component, so the first `/` always splits it from the
bag-relative filename even when the filename has directories in it; `payload_object_id`
refuses a record id that could make the split ambiguous rather than producing an
identifier that might be misread.

### 2. An event says what kind of identifier it carries, and names the bytes it examined

`PremisEvent` gains `linked_object_type` (PREMIS `linkingObjectIdentifierType`, which
the PREMIS schema makes mandatory and ledger had been omitting) and
`linked_content_address` — a second link, to the bytes, so a fixity or identification
event stays bound to exactly what it checked even if the record it lives in is later
revised. The ingest event is typed `ledger-record`. The XML export emits both links as
proper, typed `linkingObjectIdentifier` elements; the JSON keeps the existing scalar
`linkingObjectIdentifier` and adds `linkingObjectIdentifierType` and
`linkingObjectContentAddress` beside it.

Both new keys are omitted when unset. That is not tidiness: every existing log is
hash-chained over its entries' dict form, so an event written before this ADR must
serialise byte-for-byte as it did, or every chain head in every existing bag and every
published attestation would move. A test pins the legacy shape and verifies a
pre-ADR chain against today's reader.

### 3. A contradiction is refused, never written

`PremisLog.record` refuses a `format identification` event whose object and bytes
already carry a *different* verdict in that log, with `PremisContradictionError`, before
anything is appended. A second event that agrees is history. A different verdict for the
same payload id but different bytes is a revised deposit, not a contradiction, and is
recorded. There is no supersession path today; a future re-identification feature must
add one explicitly, and this guard is what keeps one from appearing by accident.

### 4. A log that already holds a contradiction reports it

A bag written before this ADR is keyed by address and may carry the #149 shape. Reading
it must not raise — a steward needs to see the finding — so `PremisLog.contradictions()`
returns every object with more than one verdict, listing **all** the verdicts in recorded
order, typed by how the object was keyed (`content-address` for a legacy log,
`ledger-payload` for a current one). Legacy events are read with their identifier type
inferred only where the inference is safe: a value that parses as a content address is
one; a bare id is left untyped rather than guessed at.

No existing bag is rewritten. The chain forbids it, and nothing needs it: a legacy
event's meaning — "at this time, in this record, these bytes were identified as X" — is
unchanged, and the reader now says which keying it used.

### 5. The harness fails on the invariant and commits what it measured

`make real-corpus` now reads every bag's log through the same `contradictions()` reader a
steward's tooling would use, joins every payload to the one event that is *about it*,
checks that event's media type and basis against the record, and checks that no event
logged `success` over a file nothing identified. Any hit fails the run. The population
the class could fire on — content addresses shared by several payloads — is reported in
full, with the number of groups whose verdict differs by name, as a measured fact rather
than a surprise.

What the run measured is written to
[`../data/real-corpus/opf-format-corpus-366f068c.json`](../data/real-corpus/opf-format-corpus-366f068c.json):
one row per file (path, the corpus's git blob SHA-1, SHA-256, size, and the
identifier's verdict) plus the counts derived from those rows. Metadata and hashes only,
never the files. A later run that drifts from the committed evidence fails, and
`tests/test_real_corpus_evidence.py` re-derives every count from the rows and checks each
number the write-up states against it — so a number in the docs can only ever come from
that derivation.

## Consequences

Measured on the same 679 files, same pinned corpus commit, 679/679 payloads re-hashed
byte-identical to the fetched originals. "Before" is commit `dc70b05`; "after" is this
change.

| | before | after |
| --- | --- | --- |
| payloads with exactly one identification event about *that payload* | 0 / 679 | **679 / 679** |
| identifiers carrying more than one event | 16 (70 events) | **0** |
| objects whose log carries more than one verdict | 1 (4 events, 2 verdicts) | **0**, and refused at write |
| content addresses shared by more than one payload | 16 (70 payloads) | 16 (70 payloads) — a property of the bytes, now reported, not conflated |
| shared addresses whose verdict differs by payload name | 1 | 0 |
| format-identification events logged `success` while unidentified | 0 (156 before #140) | **0**, and the run fails on any |
| record media type contradicting the log | 0 since ADR 0010 | **0**, now checked against the log itself, not only against a re-identification |

Costs and open edges, stated rather than left to be discovered:

- **A new identifier scheme in the log.** `ledger-payload` values contain the filename.
  The PREMIS log of a bag is already beside that bag's `record.json`, which lists every
  payload filename, so nothing new is disclosed within a bag; and every surface that
  renders events — the steward audit page, the steward console — is steward-gated, so an
  anonymous viewer sees neither the old address nor the new identifier. Replicas receive
  whole bags and already carry the filenames.
- **Consumers keyed by content address must join through the record.** The address still
  travels on every fixity and identification event (`linkingObjectContentAddress`), so
  grouping by bytes remains a one-line operation; it is no longer the key.
- **Pre-ADR bags stay address-keyed.** Their events are readable, typed by inference, and
  their contradictions (if any) are reportable; a mixed archive's log is therefore read
  under two keyings, and `object_identifier_type` says which applies to each event.
- **`linkingObjectIdentifierType` is emitted as `local` in XML for events whose writers
  have not been typed yet** (consent changes, takedowns, replication). That is the
  conventional repository-scoped type and it is honest; typing those writers is a
  follow-up that changes nothing about what the events assert.
- **Re-identification does not exist.** When it does, it needs an explicit supersession
  shape, and the guard in decision 3 is the place it will have to be added.
