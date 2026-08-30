# What real archival files did to this pipeline

Every other proof in this repository runs on fixtures ledger wrote itself. That is a
closed loop: the fixtures encode the same assumptions as the code, so they can only
ever confirm them. `make demo` is honest about being synthetic, but "synthetic and
honest about it" still leaves the central preservation claim untested against the
thing it exists for — real files, which arrive wrapped, truncated, mislabelled,
obsolete, and named by someone who was not thinking about BagIt.

This is the write-up of running the whole ingest pipeline over a real corpus.
Reproduce it with `make real-corpus`.

Ten defects were found. Six were fixed in the corpus PR; the remaining four needed a
product decision, a migration story, a crypto review, or a data-model decision, were
filed as issues rather than guessed at, and have since been resolved by
[ADR 0010](adr/0010-identification-governs-what-a-record-asserts.md),
[ADR 0011](adr/0011-sealed-payloads-are-size-capped.md), and
[ADR 0012](adr/0012-the-premis-object-is-the-payload-within-a-record.md). Every number
here is measured on the same pinned corpus commit and re-measured on every run.

Two of the three are committed as [evidence](#evidence) a test re-derives on every
`make verify`: the pipeline as it stands today, and — since ADR 0012 — the state of it
at ledger `dc70b05`, which is the "after fixes 1-6" column below. **The third, the
original corpus run, has no committed evidence file.** It predates the convention and
cannot be regenerated from `main`, so its figures are checked the only two ways they
can be: every column of §10's basis table has to count the same 679 files, every
percentage has to be its own column's count over that total, and the four places
stating what the corpus run could not identify have to agree with each other. That is
weaker than the other two columns, and it is said here rather than left for a reader to
discover from the test.

**Lead finding: nothing crashed, and that was the problem.** All 22 collections
ingested, all 22 bags validated against RFC 8493, every payload byte survived. The
defects were all in what the pipeline *said* about the files afterwards — a
preservation log that reported confident success over material it had entirely
failed to understand.

---

## The corpus

| | |
| --- | --- |
| **Source** | [Open Preservation Foundation `format-corpus`](https://github.com/openpreserve/format-corpus) |
| **Licence** | CC0 unless otherwise stated (per the corpus README) |
| **Pinned at** | commit `366f068cec399d0cdfd61fa473de3ab6dc858098` |
| **Sample** | 679 files, 302 MB (every file ≤ 5 MB; the full corpus is ~800 MB) |
| **Provenance** | every file verified against its git blob SHA-1 at fetch |

Chosen because it is the digital-preservation community's own reference collection of
awkward files, assembled precisely to break format tools: `jhove-errors` (files that
trip JHOVE's validators), `pdfCabinetOfHorrors`, `govdocs1-error-pdfs` (real
government PDFs that broke real harvesters), `jp2k-test` (including deliberately
truncated JPEG 2000), legacy office and spreadsheet formats going back to Lotus
1-2-3, `disk-images`, and `filesys-trials` — a directory of deliberately hostile
filenames. It is small enough to fetch on demand and openly licensed enough to
redistribute, and it needs no partner or data-sharing agreement.

**Proof the data reached the code.** A plausible-looking report over data that never
actually flowed through the pipeline would be worse than no report. So the harness
re-hashes every bagged payload against the file that was fetched and reports the
count: **679/679 payload files byte-identical to the fetched originals**. The
identification numbers below are computed from those same bytes, and the PREMIS
counts are read out of the bags the run produced.

---

## Defects found

### 1. Every file the identifier could not name was logged as a success

The worst one, because it is the one that hides all the others.

Of 679 real files, **156 (23.0%) could not be identified at all**. Every single one
was written to the PREMIS preservation log with `eventOutcome: "success"` — the same
outcome as a confident content-signature match. A steward auditing an archive's
preservation log by outcome, which is the entire point of recording an outcome, would
have seen 679 successes and nothing to act on.

"We do not know what this is" is the most actionable preservation-planning signal an
ingest can produce. It was being discarded.

**Fixed.** An unidentified payload now records `eventOutcome: "unidentified"`, and
`ledger ingest` tells the operator at the moment they hand the file over — the moment
they are best placed to say what it is.

### 2. The at-risk advisory missed 66 of the 66 genuinely obsolete files

The whole preservation-planning feature exists to flag "obsolescent or proprietary
formats that real community archives actually hold." The corpus contains **66 files
in formats that are unambiguously obsolete** — Lotus 1-2-3 (`.wk1`, `.wk3`, `.wk4`,
`.123`, `.wks`), Quattro Pro (`.wq1`, `.wq2`, `.wb1`, `.wb2`), Microsoft Access
(`.mdb`), Windows Write (`.wri`), ARJ archives, InDesign, IBM DCA, DB/TextWorks, and
the whole dead-ebook family (`.lit`, `.mobi`, `.azw3`, `.lrf`, `.pdb`, `.rb`,
`.snb`).

**ledger flagged zero of them.** It flagged 25 files total as at-risk across the
corpus (legacy OLE2 Office and WordPerfect) — so it missed 2.6× more endangered
material than it caught.

This is structural, not a missing table row: `at_risk` is a property of a *known*
registry entry, and the registry deliberately reserves the flag for formats it
recognises. An obsolete format the registry has never heard of therefore lands in the
one bucket that is explicitly *not* at-risk. The formats most likely to be obsolete
are exactly the formats a small curated registry is least likely to know.

**Resolved** by [ADR 0010](adr/0010-identification-governs-what-a-record-asserts.md)
([#142](https://github.com/ChelseaKR/ledger/issues/142)), which answers the design
question rather than deferring it again. `at_risk` keeps its exact meaning — a
*positive* finding about a known format, with a named migration target — and a second,
orthogonal signal is added: `unassessable`, true when nothing could identify the file.

Making `unknown` imply `at_risk`, the obvious shortcut, was rejected on the
measurement. Of the 118 unidentified files, 66 are obsolete and **52 are not**: 31 are
zero-byte, 7 are `.DS_Store`, 4 are deliberately damaged PDFs, 5 are raw disk images,
and 5 are PCRaster maps in a format that is still maintained. Not one of those 52 wants
"migrate to a modern format" as its remedy. Precision was worth protecting; it was the
inference from its *absence* that was wrong.

The registry also widened, with every signature read off the corpus bytes and then
checked against PRONOM's published DROID signature file (V120). Where PRONOM assigns
several byte-identical PUIDs to one head signature (Access, InDesign) or has no entry
at all (DB/TextWorks, Bambook), `puid` stays `None` rather than guessing — asserting a
PUID on memory is what produced defect 5. Nine files (Statistica, MindManager,
ConceptDraw, IBM FFT) are left unidentified **on purpose**: the long tail of dead
formats has no end, and the point of the split is that the registry no longer has to
converge for the archive to stay honest.

Recall over the corpus's 66 known-obsolete files went from **0 to 57 (86%)**, and
`make real-corpus` now prints that number against a written-down ground truth on every
run, so a regression shows up as a falling count rather than as silence.

### 3. JPEG 2000 — the preservation master format — was unidentified

18 files. JP2, JPX, JPM, MJ2, and bare codestreams were all recorded as
`application/octet-stream`, basis `unknown`.

This is the format most library and museum digitisation programmes write their
*preservation masters* in. A digital-preservation tool that cannot name it is blind
to the files an archive most cares about keeping. `file(1)` identifies them without
difficulty ("JPEG 2000 Part 1 (JP2)").

The cause: all four container flavours open with the same signature box and differ
only in the `ftyp` brand at offset 20, so identifying them means reading *past* the
magic number — which the fixed-offset signature table could not express.

**Fixed.** All five now identified by signature, with PUIDs verified against the
DROID signature file (V120): `x-fmt/392`, `fmt/151`, `fmt/463`, `fmt/337`,
`fmt/1794`. An unrecognised brand degrades to JP2 rather than to `unknown`.

### 4. Twenty real PDFs were unidentified because their header was not at byte 0

Real PDFs do not start where the standard says. From this corpus:

| offset | what precedes the header | file |
| --- | --- | --- |
| 1 | a single space | 15 files in `pdf-handbuilt-test-corpus` |
| 8 | an HTTP chunked-transfer length (`17e500\r\n`) | `carp2010-2.pdf` |
| 28 | a `data:application/...` URI prefix | `emi15514-sup-0002-SupinfoS2.pdf` |
| 119 | a JSON envelope (`{"datetime": ...`) | `0b1b4c74-….pdf` |
| 128 | a MacBinary II wrapper | `reserve3.pdf`, `ESUjulyaugust2002.pdf` |

Each of these opens fine in a PDF reader; `file(1)` calls 15 of them "PDF document"
outright and decodes the MacBinary pair as PDFs too. ledger called all 20
`Unidentified`, which told the steward nothing at all — neither the format nor the
real defect.

Note that DROID is also strict here (its PDF signature anchors at byte 0), so simply
reporting these as ordinary PDFs would *diverge* from PRONOM and hide the wrapper.

**Fixed, honestly.** A displaced `%PDF-` header within 1024 bytes (the tolerance
Adobe's own readers document) now yields a distinct basis, `signature-offset`, and
the PREMIS detail names the offset:

> `identified as PDF [fmt/14] via signature-offset; media-type application/pdf; header at byte 128, not 0 — a wrapper or preamble precedes it, and strict validators will not identify this file`

The steward gets the format *and* the defect. The scan runs last, after the
extension, XML, and text steps, so it can only change files no earlier step could
name — prose mentioning `%PDF-` is still plain text, and a well-formed PDF keeps the
plain `signature` basis.

### 5. Three PRONOM PUIDs pointed at entirely different formats

Surfaced while verifying the new PUIDs against PRONOM's published DROID signature
file. Three identifiers already in the registry were being written into every PREMIS
log as fact, and all three were wrong:

| format | PUID claimed | what that PUID actually is in PRONOM |
| --- | --- | --- |
| WebP | `fmt/565` | **Adobe Illustrator** |
| Matroska / WebM | `fmt/641` | **Epson Raw Image Format** |
| RealMedia | `fmt/202` | **Nikon Digital SLR Camera Raw** |

A PUID's only purpose is interoperability with DROID/PRONOM tooling, so a wrong one
does not merely fail to help — it actively misinforms every downstream preservation
system that trusts it.

**Fixed** to `fmt/566`, `fmt/569`, and `x-fmt/190`, with a regression test that pins
all three. The other 25 PUIDs in the registry were checked against V120 and are
correct.

### 6. RTF was silently filed as plain text

5 files. RTF is ASCII, so with no signature for it the UTF-8 decode fallback claimed
it first and recorded `text/plain`. A word-processing document with real structure
was recorded as if it were a `.txt` file. **Fixed** (`fmt/45`).

---

## Defects deferred at the corpus run, and resolved since

### 7. A sealed 157 MB payload costs 1.1 GB of RAM

Five separate places in the docs and code claim payloads are never held in RAM —
`docs/ARCHITECTURE.md`, `fixity.py`, `bag.py`, `cas.py`, `server.py`, all invoking
"a multi-gigabyte oral-history video" and "the one inexpensive box the archive
targets."

Measured, on one 157 MB payload:

| ingest path | peak RSS |
| --- | --- |
| PUBLIC (streamed) | **38 MB** |
| SEALED (encrypted at rest) | **1178 MB** |

These two are the corpus run's own single-file probe. They are **not** the numbers ADR
0011 and the code carry: the re-measurement further down this section reads 38.9 MB and
1189.3 MB on a 157.3 MB payload, and that is the run every other document quotes. The
two peak figures are under 1% apart, and neither is a correction of the other — both are
true of the run that produced them. They are kept apart rather than reconciled into one,
because silently replacing a measurement with a later one is how a document stops being
a record of what was observed.

The streaming claim holds everywhere it was fixed under FIX-03 — and fails on the one
path that was not: `ingest.py` reads the entire file into memory to encrypt it
(`vault.encrypt_bytes(source.read_bytes())`). That is 7.5× the file size, and 31× the
streamed path.

This is the *worst* place for it. SEALED is the setting an at-risk contributor is
most likely to choose for the most sensitive material they have — so the archive
falls over precisely on the records it most needs to keep. A 1 GB sealed oral history
would need roughly 7 GB of RSS on a box the project advertises as inexpensive.

`docs/ideation/02-large-scale-fixes.md` names this exact site as one of three FIX-03
targets, fixes the other two, and offers "or note the limitation and cap SEALED
payload size honestly." Neither was done: there is no cap and no documented caveat.

**Resolved** by [ADR 0011](adr/0011-sealed-payloads-are-size-capped.md)
([#141](https://github.com/ChelseaKR/ledger/issues/141)). Fernet does not stream, and
doing this properly means a chunked framing format for data at rest — which changes
the on-disk encryption format for the tier protecting a contributor's most sensitive
material, and which FIX-11 already records must not ship on self-review. So the honest
interim shipped instead of an unreviewed AEAD framing:

- **SEALED payloads are capped** at `Config.sealed_payload_max_bytes` (default 64 MiB),
  refused in a pre-flight pass before anything is read, encrypted, or stored, with a
  message naming the limit, the measured cost, the formula, and three ways forward.
- **All five claims are qualified** to name the path they are actually about and to
  point at ADR 0011 for the exception, and the truthfulness gate pins the pointer in
  each of the five files so it cannot silently revert to an unqualified claim.

Re-measured across three sizes, one file each, peak RSS of the ingesting process:

| payload | PUBLIC (streamed) | SEALED |
| --- | --- | --- |
| 16.8 MB | 35.8 MB | 159.9 MB |
| 67.1 MB | 35.8 MB | 527.2 MB |
| 157.3 MB | 38.9 MB | 1189.3 MB |

The streamed path is flat; the sealed path is linear at `peak_mb ≈ 33 + 7.4 × payload_mb`.
64 MiB is the payload whose peak fits half of a 1 GB box — predicted at 506 MB and
**measured at 527 MB**, which is the number the docs carry, because a predicted figure
in a document about false claims would repeat the original mistake.

**Reproducing the measurement.** Ingest one file of a known size under
`AccessPolicy.PUBLIC` and again under `AccessPolicy.SEALED` in separate processes, and
read `resource.getrusage(RUSAGE_SELF).ru_maxrss` after each `Archive.ingest` returns.
Separate processes matter: peak RSS is a high-water mark, so a shared process reports
the largest run for all of them.

### 8. BagIt manifests are not percent-encoded (RFC 8493 §2.1.3)

The `filesys-trials` collection contains a file literally named `%`. It went into the
manifest raw:

```
<digest>  data/filesys-trials/a-bad-name/characters/%
```

RFC 8493 §2.1.3 requires `%`, CR, and LF in a manifest filepath to be percent-encoded
(`%25`, `%0D`, `%0A`). ledger neither encodes on write nor decodes on read, so it
round-trips its own bags fine and would mis-resolve anyone else's — and a conformant
reader that *does* decode (the Library of Congress `bagit-python` reference
implementation among them) will resolve a ledger path containing `%` to the wrong
file, or to no file.

This contradicts `bag.py`'s stated reason for choosing BagIt at all: "any conformant
tool — now or decades from now, run by people who never met us — can validate and
unpack a ledger bag without ledger itself."

**Resolved** ([#143](https://github.com/ChelseaKR/ledger/issues/143)). `%`, CR, and LF
are percent-encoded on write and decoded on read — and only those three, so a UTF-8
filename with spaces or accents stays legible to a human checking a manifest against a
directory listing.

**Migration path for existing bags.** Reading migrates itself: the decoder handles only
the three escapes the RFC defines and leaves every other `%` alone, so a bag written
before this change keeps validating untouched. A general percent-decoder would have
turned a pre-migration payload named `%41` into a lookup for `A` — corrupting the read
of every existing bag in order to fix the write of new ones. For an archive that wants
unambiguous manifests, `bag.migrate_manifest_encoding(bag_dir)` re-serialises the
payload manifests and reseals the tag manifests through the same path a lawful metadata
revision takes; it is idempotent, returns whether anything changed, and only an archive
holding `%`, CR, or LF in a payload name needs it at all. The one case that cannot be
disambiguated is a pre-migration bag holding a file literally named `%25`, which reads
back as `%`; that ambiguity is inherent to introducing an escape character after the
fact, and is documented rather than hidden.

Round-tripped in tests over the hostile filenames `filesys-trials` actually ships —
`!`, `#`, `$`, `%`, `(.)`, `{ (2).}`, backtick, `~`, `null` — plus `%41` and `a%20b`.

### 9. The record's media type contradicts the preservation log for 100 payloads

When content-based identification fails, `ingest.py` falls back to
`mimetypes.guess_type` — a pure filename guess. The result is that the *record* (what
the browse UI and Dublin Core show) can advertise a confident media type for a file
the *preservation log* says was never identified:

| the record says | the identifier found | files |
| --- | --- | --- |
| `image/jp2` | `application/octet-stream` | 12 *(before fix 3)* |
| `application/pdf` | `application/octet-stream` | 25 *(before fix 4)* |
| `application/x-msaccess` | `application/octet-stream` | 3 |
| `text/markdown` | `text/plain` | 73 |

Fixes 3 and 4 removed the two largest groups by making identification actually
succeed, but the underlying asymmetry remains: a curated `_EXTENSION_MAP` result is
also discarded in favour of stdlib `mimetypes`, so a `.doc` is recorded as
`application/msword` in the record while PREMIS calls it at-risk OLE2.

**Resolved** by [ADR 0010](adr/0010-identification-governs-what-a-record-asserts.md)
([#144](https://github.com/ChelseaKR/ledger/issues/144)). `mimetypes.guess_type` is
removed from the ingest path: the record's media type is the identifier's verdict,
always, except for a type the caller *declared* — a human assertion rather than a
guess, and labelled as such.

The objection to preferring the identifier was that it degrades the browse UI to
`application/octet-stream` for 17% of files. Widening the registry (defect 2) cut that
to **4.9%**, and for those files `application/octet-stream` is not a degradation: it is
the correct IANA type for an unrecognised byte stream, and it warns a reader that the
damaged PDF they are about to download will not open. `PayloadFile.media_type_basis`
now travels with the type in the record and the API, so a consumer that sees
`application/pdf` can tell whether the bytes said so or the filename did.

Divergence is **0 of 679**, and the harness now fails the run on any hit rather than
reporting a count: since ADR 0010 this is an invariant, not a statistic.

---

## 10. One content address carried two contradictory verdicts

PREMIS events were linked by content address, and the content store deduplicates — but
identification is a function of the bytes *and the filename*. So two byte-identical
payloads whose names identify differently wrote two contradictory
`format identification` events against one object identifier, and a consumer reading
PREMIS the correct way (keyed by `linkingObjectIdentifier`) saw whichever was written
last.

The corpus surfaced it: `office/wordprocessing/IBM_DCA/testIBM_DCA.rft` is
byte-identical to three `testIBMDisplayWrite*.doc` files. Measured on the code as it
stood before #148 (commit `dc70b05`, its trial archive kept and re-read with today's
detectors), the 679 identification events were linked to **625** distinct identifiers;
**16** identifiers carried more than one event (70 events between them — the 30
zero-byte files share one address, and so on); **1** identifier carried more than one
*verdict* — the IBM quartet, four events, `unidentified` against `at-risk` OLE2
`fmt/111`; and **0 of 679** payloads had an event that was about *that payload*.
Removing the `.doc` extension row and adding the IBM DisplayWrite/DCA signature (#148)
made all four identify the same way, so the collision count printed 0 — but that
resolved the instance, not the class. `a.txt` and `a.md` with identical contents is
enough to reproduce it.

**Resolved** by [ADR 0012](adr/0012-the-premis-object-is-the-payload-within-a-record.md)
([#149](https://github.com/ChelseaKR/ledger/issues/149)), which answers the question the
issue asked — what ledger's PREMIS *Object* is — the way PREMIS and every file-level
repository answer it: the Object is the **payload within a record**, identified as
`<record_id>/<filename>` and typed `ledger-payload`; the content address is its fixity,
carried on every event as a second link to the bytes examined, not its identity. Two
byte-identical payloads are two objects, each with one verdict, and a consumer keyed by
object identifier reads the log without contradiction.

Three things make the class impossible to record silently rather than merely absent
from this corpus:

- `PremisLog.record` **refuses** a second, different verdict for the same object and the
  same bytes (`PremisContradictionError`) before anything is appended; an agreeing repeat
  is history, and different bytes under the same name are a revised deposit.
- A bag written before the ADR — address-keyed, contradiction and all — is read without
  a crash, and `PremisLog.contradictions()` **reports** every verdict it carries, typed
  by how it was keyed, instead of surfacing whichever came last.
- `make real-corpus` reads every bag through that same reader, joins every payload to
  the one event about it, and **fails** on any contradiction, any payload without
  exactly one event, any event that disagrees with its record, and any `success` logged
  over an unidentified file.

The legacy on-disk shape is unchanged — both new keys are omitted when unset — so every
existing chain head and published attestation stands; a test pins that.

After, on the same 679 files: **679 / 679** payloads have exactly one identification
event about them, **0** identifiers carry more than one event, **0** objects carry more
than one verdict. The 16 shared addresses (70 payloads) are still there — identical bytes
are a property of the corpus, not a defect — and are now reported as what they are, with
**0** of them differing in verdict by name.

---

## What held up

Worth stating plainly, because it is the part fixtures could not have told us:

- **No crashes.** 679 real files across 22 collections, including deliberately
  malformed, truncated, and zero-byte files — not one unhandled exception.
- **Bag integrity is real.** All 22 bags validated against RFC 8493, and all 679
  payloads re-hashed byte-identical to their sources. The fixity core does what it
  says.
- **Hostile filenames survived.** `filesys-trials` contains files named `!`, `#`,
  `$`, `%`, `(.)`, `{ (2).}`, `` ` ``, `~`, and `null`. All ingested, bagged, and
  validated (see §8 for the interoperability caveat on `%`).
- **Zero-byte files are handled.** 34 of them, ingested and manifested without
  special-casing.
- **The streaming claim holds on the public path** — 157 MB in, 38 MB peak RSS.

---

## Measured effect of the fixes

Identification basis over the same 679 files, before and after:

| basis | at the corpus run | after fixes 1-6 | after §§2, 7-9 |
| --- | --- | --- | --- |
| `signature` | 385 (56.7%) | 408 (60.1%) | **468 (68.9%)** |
| **`unknown`** | **156 (23.0%)** | **118 (17.4%)** | **33 (4.9%)** |
| `extension` | 111 (16.3%) | 111 (16.3%) | 106 (15.6%) |
| `empty` | — | — | 30 (4.4%) |
| `signature-offset` | — | 20 (2.9%) | 20 (2.9%) |
| `text` | 22 (3.2%) | 17 (2.5%) | 17 (2.5%) |
| `xml-declaration` | 5 (0.7%) | 5 (0.7%) | 5 (0.7%) |

And the signals a steward actually acts on:

| | at the corpus run | now |
| --- | --- | --- |
| flagged `at_risk` | 25 (3.7%) | **80 (11.8%)** |
| reported `unassessable` | — | 33 (4.9%) |
| **recall over the 66 known-obsolete files** | **0 / 66** | **57 / 66 (86%)** |
| record media type contradicting the log | 100 | **0** |
| identification events logged `success` over an unidentified file | 156 | **0** |
| payloads whose identification event is about *that payload* (ADR 0012) | 0 / 679 | **679 / 679** |
| object identifiers carrying more than one event | 16 (70 events) | **0** |
| one object, contradictory verdicts | 1 | **0**, and refused at write |

123 real files that the pipeline previously could not name, it now names: 18 JPEG 2000,
20 displaced-header PDFs, 56 obsolete-format files, 30 correctly reported as empty
rather than unidentified. 5 RTF files moved from a wrong answer (`text/plain`) to a
right one, and 5 `.doc` files moved from a *confidently wrong* answer (asserted as
Microsoft OLE2 with PUID `fmt/111`, when they are WordPerfect and IBM DisplayWrite) to
an honest one.

**4.9% is still unidentified, and that is the honest number.** Nine of those are
obsolete formats left out of the registry deliberately (§2); the rest are damaged
files, raw disk images, OS metadata, and a live-but-niche scientific format. All of
them are now reported as `unassessable` — not as safe.

---

## Evidence

Every number in this report is derived from one committed file,
[`data/real-corpus/opf-format-corpus-366f068c.json`](data/real-corpus/opf-format-corpus-366f068c.json):
one row per corpus file — path, the corpus's own git blob SHA-1, SHA-256, size, and the
identifier's verdict — plus the counts derived from those rows and what the PREMIS logs
of the run said. Metadata and hashes only; never a byte of the corpus. `make real-corpus`
re-checks a fresh run against it and fails on drift, and
`tests/test_real_corpus_evidence.py` re-derives every count from the rows and checks the
table below, and every stated number above, against them. A number that stops matching
fails the suite; a row that is deleted from this table fails it too.

| measure | value |
| --- | --- |
| `files` | 679 |
| `bytes` | 301690480 |
| `distinct_addresses` | 625 |
| `basis:signature` | 468 |
| `basis:extension` | 106 |
| `basis:unknown` | 33 |
| `basis:empty` | 30 |
| `basis:signature-offset` | 20 |
| `basis:text` | 17 |
| `basis:xml-declaration` | 5 |
| `at_risk` | 80 |
| `unassessable` | 33 |
| `displaced_headers` | 20 |
| `obsolete` | 66 |
| `obsolete_flagged` | 57 |
| `shared_addresses` | 16 |
| `payloads_on_shared_addresses` | 70 |
| `bags:collections` | 22 |
| `bags:bags_valid` | 22 |
| `bags:payloads_proven` | 679 |
| `premis:format_events` | 679 |
| `premis:outcomes:success` | 536 |
| `premis:outcomes:at-risk` | 80 |
| `premis:outcomes:unidentified` | 33 |
| `premis:outcomes:empty` | 30 |
| `premis:success_while_unidentified` | 0 |
| `premis:record_log_divergence` | 0 |
| `premis:payloads_checked` | 679 |
| `premis:event_record_disagreements` | 0 |
| `premis:contradictions` | 0 |
| `premis:name_dependent_verdict_groups` | 0 |

The "before" column of §10 is measured too, not remembered:
[`data/real-corpus/opf-format-corpus-366f068c.before-adr-0012.json`](data/real-corpus/opf-format-corpus-366f068c.before-adr-0012.json)
holds what today's detectors found in the trial archive that ledger `dc70b05` — the
commit before #148 — produced from the same corpus, and the same test binds §10's
before-numbers to it.

## Reproducing

```sh
make real-corpus            # run, report, and check against the committed evidence
make real-corpus-evidence   # run and REWRITE the evidence (then update what cites it)
```

Fetches the pinned corpus into the gitignored `./real-corpus/` (~302 MB, verified
file-by-file against its git blob SHA-1), ingests it into a temporary archive,
validates every bag, proves byte-identity against the fetched originals, prints the
tables above, and compares the run to the committed evidence. It is deliberately
**not** part of `make verify`: a merge gate must not depend on the network. The
evidence test, which needs no network, *is* in `make verify`.
