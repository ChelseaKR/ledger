# What real archival files did to this pipeline

Every other proof in this repository runs on fixtures ledger wrote itself. That is a
closed loop: the fixtures encode the same assumptions as the code, so they can only
ever confirm them. `make demo` is honest about being synthetic, but "synthetic and
honest about it" still leaves the central preservation claim untested against the
thing it exists for — real files, which arrive wrapped, truncated, mislabelled,
obsolete, and named by someone who was not thinking about BagIt.

This is the write-up of running the whole ingest pipeline over a real corpus.
Reproduce it with `make real-corpus`.

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

**Not fixed — flagged.** Widening the registry to cover these is real work with a
real design question behind it (should `unknown` imply elevated risk?), and guessing
at it under this PR's scope would be worse than naming it. Filed.

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

## Defects found and deliberately not fixed here

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

**Not fixed here.** Fernet does not stream; doing this properly means a chunked
framing format for data at rest, which changes the on-disk encryption format and
belongs with the crypto review already tracked as FIX-11. Filed with this
measurement attached.

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

**Not fixed here.** The fix is small but it changes the serialisation of already-
written bags, so it needs a migration story for existing archives rather than a
drive-by edit in a PR about format identification. Filed.

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

**Not fixed here** — deciding which of the two should win is a product question about
what the browse UI owes a reader, not a bug with an obvious correct answer. Filed,
with the harness reporting the count on every run so it cannot drift unnoticed.

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

| basis | before | after |
| --- | --- | --- |
| `signature` | 385 (56.7%) | 408 (60.1%) |
| **`unknown`** | **156 (23.0%)** | **118 (17.4%)** |
| `extension` | 111 (16.3%) | 111 (16.3%) |
| `signature-offset` | — | 20 (2.9%) |
| `text` | 22 (3.2%) | 17 (2.5%) |
| `xml-declaration` | 5 (0.7%) | 5 (0.7%) |

38 real files that the pipeline previously could not name, it now names: 18 JPEG
2000, 20 displaced-header PDFs. 5 RTF files moved from a wrong answer (`text/plain`)
to a right one.

**17.4% is still unidentified, and that is the honest number.** Most of the remainder
is §2 — obsolete formats with no registry entry — and it is now visible in the PREMIS
log as `unidentified` instead of hiding inside `success`.

---

## Reproducing

```sh
make real-corpus
```

Fetches the pinned corpus into the gitignored `./real-corpus/` (~302 MB, verified
file-by-file against its git blob SHA-1), ingests it into a temporary archive,
validates every bag, proves byte-identity against the fetched originals, and prints
the tables above. It is deliberately **not** part of `make verify`: a merge gate must
not depend on the network.
