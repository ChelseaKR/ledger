# 0010. Format identification governs what a record asserts about a payload

Status: Accepted

Date: 2026-08-19

Resolves: #142, #144

## Context

Running the pipeline over 679 real files from the Open Preservation Foundation
`format-corpus` ([`../REAL-CORPUS-REPORT.md`](../REAL-CORPUS-REPORT.md)) produced two
findings that look unrelated and are the same defect on two surfaces: **the record
asserted more than the pipeline had actually determined.**

**The at-risk advisory (#142).** `at_risk` is a property of a *known* registry entry,
and `preservation.py` reserved it deliberately so the advisory would "stay precise".
Measured against a corpus of exactly the material it exists for, that reservation cost
almost all of the recall:

| | files |
| --- | --- |
| flagged `at_risk` | 25 |
| unambiguously obsolete files in the corpus | 66 |
| of those, flagged `at_risk` | **0** |

The formats most likely to be obsolete are exactly the formats a small curated
registry is least likely to know, so they landed in `unknown` — the one bucket that is
explicitly *not* at-risk. A steward auditing by risk saw nothing to act on for Lotus
1-2-3, Quattro Pro, Access, Windows Write, DB/TextWorks, and seven dead ebook formats.

The deeper problem is that `at_risk=False` was doing two incompatible jobs: "assessed,
and fine" and "never assessed at all". Only the first reading is reassuring, and the
second was 17% of a real archive.

**The record's media type (#144).** `ingest.py` took the record's media type from
`mimetypes.guess_type` — a pure filename guess — whenever identification did not reach
a content signature. So 100 payloads carried a confident media type in the record while
their own PREMIS log read `identified as Unidentified [no-puid] via unknown`. It also
inverted the intended precedence: for `memo.doc` the registry said
`application/x-ole-storage`, **at-risk**, with a migration recommendation, and the
record stored the stdlib's blander `application/msword`, carrying none of the risk
signal. The better-informed source was the one being overridden.

## Decision

### 1. The risk signal splits in two

`at_risk` keeps its exact current meaning — a *positive* finding about a known format,
with a named migration target — and a second, orthogonal signal is added:
`FormatId.unassessable`, true when nothing could identify the file. The two are
reported separately everywhere: in the PREMIS `eventOutcome`, in the identification
summary line, and in the `ledger ingest` advisory.

They are separate because **their remedies differ.** An at-risk file needs migrating to
a target we can name. An unassessable one needs *identifying*, and nothing can be
recommended until it is. Collapsing them — making `unknown` imply `at_risk`, the first
option the issue raised — would have told a steward to "migrate to a modern format" for
files where that is simply the wrong instruction. The corpus says exactly how often:

| the 52 unidentified files that are **not** obsolete | count | correct remedy |
| --- | --- | --- |
| zero-byte files | 31 | check the transfer — these are empty |
| `.DS_Store` (macOS Finder metadata) | 7 | exclude from the deposit |
| deliberately damaged PDFs | 4 | repair or re-acquire |
| raw disk images (`.img`) | 5 | characterise or mount |
| PCRaster maps (niche but maintained) | 5 | nothing; the format is alive |

Not one of those 52 wants a migration recommendation. Precision was worth protecting;
it was the *inference from its absence* that was wrong.

### 2. The registry widens, but is not expected to converge

The dead 1990s desktop is added — Lotus 1-2-3, Quattro Pro, Microsoft Access, Windows
Write, Inmagic DB/TextWorks, IBM DisplayWrite/DCA, ARJ, InDesign, and seven
discontinued ebook formats. Every signature was read off the corpus bytes and then
checked against PRONOM's published DROID signature file (V120); where PRONOM assigns
several byte-identical PUIDs to one head signature (Access, InDesign) or has no entry
at all (DB/TextWorks, Bambook), `puid` is left `None` rather than guessing. Asserting a
PUID on memory is what produced three wrong identifiers in the previous pass.

This is deliberately *not* a claim that the registry is now complete. The long tail of
dead formats has no end, which is why widening alone was rejected as the answer. What
decision 1 buys is that the registry **no longer has to converge**: a format it has
never heard of is now visibly unassessable instead of silently fine. Nine corpus files
(Statistica, MindManager, ConceptDraw, IBM FFT) are left unidentified on purpose, and
they demonstrate the design working rather than a gap in it.

### 3. The identifier's verdict is the record's media type

`mimetypes.guess_type` is removed from the ingest path. The record's media type is what
the identifier found, always — with one exception: a media type the caller *declared*
still wins, because that is a human assertion rather than a guess.

The objection to this was that it degrades the browse UI to `application/octet-stream`
for 17% of files. Decision 2 cut that to **4.9%**, and for those files
`application/octet-stream` is not a degradation: it is the correct IANA type for an
unrecognised byte stream, and it warns a reader that the damaged PDF they are about to
download will not open.

### 4. The record carries the provenance of its own media type

`PayloadFile.media_type_basis` records where the type came from — `signature` (matched
on content), `extension` (inferred by the registry from the filename), `text`,
`xml-declaration`, `signature-offset`, `empty`, `unknown`, or `declared`. It is
disclosed alongside the type in the record and the API, because a consumer that sees
`application/pdf` is entitled to know whether the bytes said so or the filename did.
It is empty on records written before this ADR, and nothing may read that as "verified".

### 5. An extension row that can only fire when wrong is deleted

The `doc`/`xls`/`ppt` rows in the extension map could only ever match a file whose bytes
are *not* OLE2, because a real legacy Office file matches the OLE2 signature two steps
earlier. By construction they were reachable only when wrong — and they wrote PUID
`fmt/111` into the PREMIS log as fact. On the corpus, all five files the `.doc` row
identified were WordPerfect or IBM DisplayWrite documents, and **none was Microsoft
anything**. PRONOM itself lists `.doc` under WordPerfect (`x-fmt/44`) as well as Word.

## Consequences

Measured over the same 679 files, same pinned corpus commit, same byte-provenance proof
(679/679 payloads re-hashed byte-identical to the fetched originals):

| | before | after |
| --- | --- | --- |
| identified by content signature | 408 (60.1%) | **468 (68.9%)** |
| unidentified | 118 (17.4%) | **33 (4.9%)** |
| reported as empty rather than unidentified | — | 30 |
| flagged `at_risk` | 25 (3.7%) | **80 (11.8%)** |
| **recall over the 66 known-obsolete files** | **0 / 66** | **57 / 66 (86%)** |
| record media type contradicting the log | 100 | **0** |

The recall number is now printed by `make real-corpus` on every run against a written-
down ground truth, so a regression shows up as a falling count rather than as silence.

Costs and open edges, stated rather than left to be discovered:

- **A zero-byte file is a new outcome.** `basis="empty"` and PREMIS
  `eventOutcome: "empty"` are new values a downstream consumer may not expect.
- **`media_type` changes for existing extension-identified files.** A `.md` payload
  ingested before this reads `text/markdown` from `mimetypes`; ingested after, it reads
  `text/markdown` from the registry (a new row) — but a `.opf` moves from
  `application/oebps-package+xml` to `application/xml`. Records are not rewritten; the
  change applies at ingest.
- **Nine obsolete formats remain unidentified**, by choice, and are surfaced as
  unassessable.
- **The browse UI is not yet labelled.** `media_type_basis` reaches the record and the
  API but is not rendered as "unverified" in the HTML, because that needs a new
  user-facing string in four locales including Arabic, and authoring translations that
  nobody can review is its own honesty problem. The data is there for the UI to use.
- **PREMIS events are linked by content address, which the store deduplicates.** Two
  identical files under names that identify differently produce contradictory events
  against one object identifier. The corpus surfaced this via `testIBM_DCA.rft`, which
  is byte-identical to three `testIBMDisplayWrite*.doc` files; fixing the `.doc` row
  resolved that instance, but the modelling question — what ledger's PREMIS Object
  *is* — is not resolved here. `make real-corpus` reports the collision count so it
  cannot recur unnoticed.
