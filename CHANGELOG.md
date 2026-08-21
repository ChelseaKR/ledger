# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

> **Note (2026-07-05):** this project has **not yet shipped a tagged release** —
> `git tag` returns nothing. The `0.1.0` heading below was prepared on 2026-06-16 as
> a release candidate but the tag was never cut, so it is recorded here under
> Unreleased rather than as a released version (a changelog claiming a release the
> repo cannot produce is exactly the kind of unbacked claim this project's own
> conformance audit exists to catch). It will move to a real `## [0.1.0] — YYYY-MM-DD`
> section, with a matching signed annotated git tag, once the first `vX.Y.Z` tag is
> explicitly approved through the release workflow added below.

### Added
- **The `main` branch ruleset now holds the CI-CD-STANDARD §5.1 solo-maintainer
  profile** (#79). `pull_request` (0 required approvals — the sole code owner
  cannot self-approve their own PR, and §5.1 permits `require_code_owner_review:
  false` for exactly that reason), `required_signatures` (GitHub signs every
  squash-merge server-side), `required_linear_history`, and
  `strict_required_status_checks_policy: true` are all now enforced live, and the
  required-check set grew from eleven contexts to thirteen — `OSV lockfile scan
  (uv.lock)` and `Semgrep SAST (p/ci)` now block a merge instead of running
  fail-closed but ignorable. Dependabot security updates enabled the same day.
  Full rationale in [`.github/rulesets/README.md`](.github/rulesets/README.md).
- **The PREMIS Object is the payload within a record** (ADR 0012, #149). A
  format-identification or fixity event is now about *one payload in one record* —
  `linkingObjectIdentifier` is `<record_id>/<filename>`, typed `ledger-payload` via a new
  `linkingObjectIdentifierType` — and carries the bytes it examined as a second link,
  `linkingObjectContentAddress`. Identification is a function of the bytes *and the
  filename*, while the content store deduplicates bytes, so keying events by address let
  two byte-identical payloads under differently-identifying names write two
  contradictory verdicts against one identifier; a consumer reading PREMIS the correct
  way saw whichever was written last. Measured on the real corpus before the fix: 679
  events on 625 identifiers, 16 identifiers carrying 70 events, 1 carrying two verdicts,
  and 0 of 679 payloads with an event about *that payload*. After: **679 of 679**, 0,
  and 0. The PREMIS XML export now emits the type the schema makes mandatory, and a
  second typed `linkingObjectIdentifier` for the bytes. Both new JSON keys are omitted
  when unset, so every existing log hash-chains byte-for-byte as before (pinned by a
  test).
- **A contradictory identification is refused, and an existing one is reported.**
  `PremisLog.record` raises `PremisContradictionError` on a second, different verdict for
  the same object and the same bytes, before anything is appended; an agreeing repeat is
  history and different bytes under the same name are a revised deposit. A bag written
  before ADR 0012 — address-keyed, contradiction and all — reads without a crash, and
  `PremisLog.contradictions()` lists every verdict it carries, typed by how it was keyed.
- **What `make real-corpus` measures is committed as evidence and re-derived by a test.**
  [`docs/data/real-corpus/opf-format-corpus-366f068c.json`](docs/data/real-corpus/opf-format-corpus-366f068c.json)
  holds one row per corpus file (path, git blob SHA-1, SHA-256, size, the identifier's
  verdict — metadata and hashes only, never the files) plus the counts derived from them
  and what the run's PREMIS logs said; a
  [`…before-adr-0012.json`](docs/data/real-corpus/opf-format-corpus-366f068c.before-adr-0012.json)
  beside it holds what today's detectors found in the trial archive ledger `dc70b05`
  produced. `make real-corpus` fails when a fresh run drifts from the committed file
  (`make real-corpus-evidence` rewrites it, deliberately); `tests/test_real_corpus_evidence.py`,
  which needs no network and is in `make verify`, re-derives every count from the rows
  and checks every number `docs/REAL-CORPUS-REPORT.md`, ADR 0012, the README and this
  changelog state against it, so a figure in the docs can only ever come from the run.
- **The harness now fails on four invariants it used to report or not check.** Any
  object whose log carries more than one verdict; any payload without exactly one
  identification event about it; any event whose media type or basis disagrees with the
  record beside it; and any `success` logged over a file nothing identified (the 156-file
  lead defect of the first run, now gated, not only fixed). The 16 content addresses the
  corpus shares across 70 payloads are reported in full, with the number of groups whose
  verdict differs by name (0), as the measured population the class could fire on.
- **Preservation risk is now two signals, not one** (ADR 0010, #142). `at_risk` keeps
  its exact meaning — a positive finding about a *known* obsolescent format, with a
  named migration target — and `FormatId.unassessable` is added for a file nothing
  could identify. `at_risk=False` used to mean both "assessed, and fine" and "never
  assessed at all", and on a real corpus the second was 17% of files. Reported
  separately in the PREMIS `eventOutcome`, the identification summary, and the
  `ledger ingest` advisory. Making `unknown` simply imply `at_risk` was rejected on the
  measurement: of 118 unidentified corpus files, 52 are empty, OS metadata, damaged,
  disk images, or a live niche format, and "migrate to a modern format" is the wrong
  instruction for every one of them.
- **The dead 1990s desktop is identified** — Lotus 1-2-3 (`x-fmt/114`, `x-fmt/115`,
  `x-fmt/116`, `x-fmt/117`, `fmt/1452`), Quattro Pro (`x-fmt/121`, `x-fmt/122`,
  `fmt/834`, `fmt/835`), Windows Write (`x-fmt/4`, `x-fmt/12`), Microsoft Access
  (Jet 3 / Jet 4), Inmagic DB/TextWorks, IBM DisplayWrite/DCA (`x-fmt/148`), ARJ
  (`fmt/610`), Adobe InDesign, and the discontinued ebook formats — Microsoft Reader
  LIT (`fmt/867`), Sony BBeB LRF (`fmt/518`), Rocket eBook (`fmt/485`), Bambook SNB,
  Mobipocket and PalmDOC (`fmt/396`). Every signature was read off the corpus bytes and
  then checked against PRONOM's DROID signature file (V120). Where PRONOM assigns
  several byte-identical PUIDs to one head signature (Access, InDesign) or has no entry
  (DB/TextWorks, Bambook), no PUID is asserted. Recall over the corpus's 66
  unambiguously obsolete files went from **0 to 57 (86%)**, and `make real-corpus`
  prints that against a written-down ground truth on every run.
- **A zero-byte payload is reported as empty, not as unidentified** (`basis="empty"`,
  PREMIS `eventOutcome: "empty"`). 31 of the corpus's 118 "unidentified" files were
  simply empty; in a real deposit that is a failed transfer, and it is worth catching
  at ingest rather than at the next audit.
- **`PayloadFile.media_type_basis`** records where a record's media type came from —
  `signature`, `extension`, `text`, `xml-declaration`, `signature-offset`, `empty`,
  `unknown`, or `declared` — and is disclosed alongside the type, so a consumer that
  sees `application/pdf` can tell whether the bytes said so or the filename did.
- **`Config.sealed_payload_max_bytes`** (default 64 MiB) caps SEALED payloads, with
  `bag.migrate_manifest_encoding()` provided for archives that need the RFC 8493
  §2.1.3 manifest migration below.

- **The pipeline is now exercised against a real, openly-licensed archival corpus**
  (`make real-corpus`, `tools/real_corpus.py`, findings in
  `docs/REAL-CORPUS-REPORT.md`). Every other proof in this repository runs on fixtures
  ledger wrote itself — a closed loop that can only confirm its own assumptions. This
  runs the whole ingest path over 679 files (302 MB) of the Open Preservation
  Foundation `format-corpus` (CC0), pinned to one commit and verified file-by-file
  against its git blob SHA-1, and re-hashes every bagged payload against the fetched
  original so a plausible-looking report cannot be produced over data that never
  reached the code. The corpus is gitignored and never committed; the target is
  deliberately outside `make verify`, which must not depend on the network.
- **JPEG 2000 is identified** — JP2, JPX, JPM, MJ2, and bare codestreams (`x-fmt/392`,
  `fmt/151`, `fmt/463`, `fmt/337`, `fmt/1794`). This is the preservation *master*
  format of most digitisation programmes and 18 of them were previously recorded as
  `application/octet-stream`. The four container flavours share one signature box and
  differ only in the `ftyp` brand at offset 20, which a fixed-offset signature table
  could not express; an unrecognised brand degrades to JP2, never to unknown.
- **Rich Text Format is identified** (`fmt/45`). RTF is ASCII, so with no signature for
  it the UTF-8 fallback claimed it first and filed a structured word-processing
  document as `text/plain`.
- **The live `protect-main` ruleset is now mirrored in-tree at
  `.github/rulesets/main.json`** (CI-CD-STANDARD §5), with `.github/rulesets/README.md`
  itemizing the six §5/§5.1 floors that profile does not meet and saying plainly that
  it is evidence rather than a profile to copy. `CODEOWNERS` routes the new directory
  explicitly. The truthfulness gate gains a `ruleset_contexts` claim: every required
  status check in the mirror must name a job that exists in `.github/workflows/`, and
  an empty required-check list fails, because renaming a job is otherwise a silent way
  to leave a branch requiring a context nothing will ever report.

### Changed
- **A payload whose format could not be identified is no longer logged as a
  successful ingest.** It now records PREMIS `eventOutcome: "unidentified"`, and
  `ledger ingest` says so on stderr. Measured on the real corpus this was 156 of 679
  files (23%), every one of them filed under the same green `success` outcome as a
  confident content match — so a steward auditing a preservation log by outcome saw
  nothing to act on, over nearly a quarter of the archive.

### Fixed
- **BagIt manifests are percent-encoded per RFC 8493 §2.1.3** (#143). `%`, CR, and LF
  are encoded on write and decoded on read; ledger previously wrote a payload named
  `%` into the manifest raw, so the Library of Congress `bagit-python` reference
  implementation — which percent-decodes — resolved a ledger path containing `%` to
  the wrong file or to none, and a filename containing a newline broke the manifest
  grammar outright. ledger round-tripped its own bags either way, because both halves
  were consistently wrong. This contradicted `bag.py`'s stated reason for choosing
  BagIt at all: that any conformant tool can read a ledger bag without ledger. The
  decoder handles only the three escapes the RFC defines, so **bags written before this
  change keep validating untouched** — a general percent-decoder would have turned a
  pre-migration payload named `%41` into a lookup for `A`. `bag.migrate_manifest_encoding()`
  re-serialises manifests and reseals the tag manifests for an archive that wants
  unambiguous ones; it is idempotent and only matters to an archive holding `%`, CR, or
  LF in a payload name.
- **A SEALED payload larger than the cap is refused instead of OOM-killing the ingest**
  (ADR 0011, #141). Fernet has no streaming API, so sealing holds the whole file in
  memory: measured peak RSS is `33 MB + 7.4 × payload`, which is **1189 MB for a 157 MB
  file** against a flat ~38 MB on the streamed PUBLIC path. SEALED is what an at-risk
  contributor picks for their most sensitive material, so the archive was falling over
  precisely on the records it most needs to keep — and not with an error. The refusal
  happens in a pre-flight pass before anything is read, encrypted, or stored, and names
  the limit, the measured cost, the formula, and three ways forward. The real fix is
  chunked at-rest framing, which changes the on-disk encryption format and stays gated
  on the commissioned crypto review (FIX-11) rather than being invented on self-review.
- **The five "never held in RAM" claims are now true as written** (#141).
  `docs/ARCHITECTURE.md`, `fixity.py`, `bag.py`, `cas.py`, and `server.py` each claimed
  a payload is never held in RAM or costs constant memory. Each was true of the path it
  annotated and, read together, asserted an end-to-end property that SEALED broke. Each
  now names the path it is actually about and points at ADR 0011, and the truthfulness
  gate pins the pointer in all five files so it cannot silently revert.
- **A record's media type no longer contradicts its own preservation log** (ADR 0010,
  #144). `mimetypes.guess_type` — a pure filename guess — is removed from the ingest
  path; the record's media type is the identifier's verdict, except for a type the
  caller *declared*. 100 payloads previously advertised a confident media type while
  their own PREMIS log read `identified as Unidentified [no-puid] via unknown`.
  Divergence is now **0 of 679**, and `make real-corpus` fails the run on any hit
  rather than reporting a count.
- **The `.doc`/`.xls`/`.ppt` extension rows are removed** (ADR 0010). They could only
  ever fire on a file whose bytes are *not* OLE2 — a genuine legacy Office file matches
  the OLE2 signature two steps earlier — so by construction they were reachable only
  when wrong, and they wrote PUID `fmt/111` into the PREMIS log as fact. All five files
  the `.doc` row identified on the corpus were WordPerfect or IBM DisplayWrite
  documents, and none was Microsoft anything; PRONOM itself lists `.doc` under
  WordPerfect (`x-fmt/44`).
- **OLE2 is named for what its PUID actually covers.** `fmt/111` is "OLE2 Compound
  Document Format" in PRONOM, with no extensions attached; calling it "Microsoft OLE2
  (legacy Office)" overclaimed, and the corpus caught it with a Quattro Pro `.wb3` and
  a `.qpw`, which are OLE2 files and are not Microsoft anything.
- **Markdown is identified as `text/markdown` (`fmt/1149`)** rather than plain text.
  The registry is meant to be the better-informed source and on this row it was the
  worse one, which produced the largest single media-type divergence (73 files).
- **`CONTRIBUTING.md` pointed ADR authors at `docs/ADRs/`**, which does not exist; the
  directory is `docs/adr/`.
- **A `%PDF-` header displaced past byte 0 is identified instead of discarded.** Real
  PDFs arrive behind an HTTP chunked-transfer length, a MacBinary wrapper, a `data:`
  URI prefix, a JSON envelope, or just a stray leading space; 20 in the corpus did,
  and all 20 were recorded as `Unidentified`, which named neither the format nor the
  defect. A displaced header within 1024 bytes (Adobe's documented tolerance) now
  yields a distinct basis, `signature-offset`, and the PREMIS detail reports the
  offset and warns that strict validators (DROID, veraPDF) will not identify the file.
  The scan runs last, after the extension/XML/text steps, so it can only name files no
  earlier step could — prose mentioning `%PDF-` is still plain text.
- **Three PRONOM PUIDs identified entirely different formats** and were being written
  into every PREMIS log as fact: WebP claimed `fmt/565` (**Adobe Illustrator**),
  Matroska/WebM claimed `fmt/641` (**Epson Raw Image Format**), and RealMedia claimed
  `fmt/202` (**Nikon Digital SLR Camera Raw**). Corrected to `fmt/566`, `fmt/569`, and
  `x-fmt/190`, verified against PRONOM's published DROID signature file (V120) — which
  also confirmed the registry's other 25 PUIDs are right — and pinned by a regression
  test. A PUID exists for interoperability with DROID/PRONOM tooling, so a wrong one
  does not merely fail to help, it misinforms every downstream system that trusts it.
- **Two security gates were described as blocking while neither could block a merge.**
  The `osv` job and the `semgrep` workflow run fail-closed on every pull request and
  are absent from the live ruleset's eleven required status checks, so a red run on
  either is advisory. The README's "Blocking … Semgrep" row and its "a blocking OSV
  scan" sentence now say what is true today; `docs/ROADMAP.md` carried the same gap on
  a row pointing at the **closed** issue #84, which is repointed. Adding the two
  contexts to the live ruleset is a server-side change and stays open.
- **Three documents cited a coverage flag this repo does not pass, at a floor it does
  not enforce.** `DEFINITION_OF_DONE.md`, `CONTRIBUTING.md`, and
  `docs/DORA-DELIVERY-HEALTH-REVIEW.md` all said `--cov-fail-under=85`; the flag
  appears in no config or workflow, and `[tool.coverage.report] fail_under` has been
  88 since the ratchet in #83's first pass. A new `config_number` claim kind re-derives
  the number from `pyproject.toml` in all three files, so it fails both when the
  documented floor disagrees with the enforced one and when a file stops stating it.
- **Eight comments pointed at `ledger-REMEDIATION.md`, a file this repo does not
  ship** — `CODEOWNERS` plus the prose above six `# noqa: C901` waivers. Each now
  names issue #83, which is where the `noqa` lines themselves already pointed.
- **A new HTML route could be served with no accessibility coverage and no red test.**
  `tests/test_accessibility_route_coverage.py` checked that every inventoried route
  still exists in `server.py`, but nothing checked the reverse, so an added page would
  silently invalidate every count in `docs/accessibility/ROUTE-COVERAGE.md`. Every
  literal route in the `do_GET` dispatch table must now be classified as HTML-in-scope
  or explicitly out of scope.
- **`DEFINITION_OF_DONE.md` claimed `make verify` reproduces every AUTO-GATE item
  locally**, including the coverage floors, which it does not run. The three
  deliberate local/CI differences (coverage, tool-dependent scans, `perf`/`container`)
  are now written down instead of implied.
- **Five stale README/architecture statements corrected, and the truthfulness gate
  widened so this class of claim is inside it.** All five drifted in the same
  direction — describing work as still owed that had shipped, or behaviour the code
  had since tightened — and `tools/check_claims.py` was green throughout, because a
  claim it does not hold cannot fail it. Corrected: dependency pinning is not "a
  range today" (a hash-pinned `uv.lock` is committed, `uv sync --locked` installs
  from it, and a blocking OSV job scans it — the README's own standards table
  already said so 161 lines further down); the residual-risk register and the
  `docs/audits/` review set are committed, with only the human sign-off (#82) open;
  the README's observability section pointed at a roadmap item, `P3-6`, that exists
  nowhere in the repo; and `/healthz` has not "reported counts only" since the counts were
  gated behind a steward grant (P2-2) — an anonymous request gets `status`,
  `all_verified`, `ready`, and `chain_head`, so a reader auditing the threat model
  would have concluded the endpoint leaks archive size that the code no longer
  exposes. The gate gains three claim kinds — `required_string` (the evidence a
  correction rests on, since "the old phrase is gone" is also true of a deleted
  paragraph), `stated_count` (a number in the prose re-derived from the tree, which
  fails both when the count is wrong and when the sentence stating it disappears),
  and `reference_exists` (every "tracked in `docs/ROADMAP.md`, <ID>" pointer in every
  committed Markdown file must resolve there) — and it now prints the load-bearing
  claims it *cannot* check on every run, published for readers in `CONTRIBUTING.md`
  and kept in step by `tests/test_claims_gate.py`. The same sweep found two more dead
  roadmap pointers (`DEFINITION_OF_DONE.md`, `docs/DORA-DELIVERY-HEALTH-REVIEW.md`),
  now corrected — and a sixth home for the `/healthz` claim, in `infra/README.md`,
  which is the operator-facing copy and the worse of the two: it told someone
  standing up a server to point an uptime monitor at `/healthz` and read counts an
  anonymous request does not return.
- **The truthfulness gate now runs on pull requests.** It was documented as
  merge-blocking and listed in `make verify`, but `ci.yml` never invoked it: it ran
  only on a contributor's machine and at tag time in `release.yml`, so the one place
  it never ran was the pull request it exists to block.
- **`audit_fixity` no longer reports health it never checked when a bag has been
  deleted outside `remove_all_copies`.** It walked only `bags/`, so a missing bag
  was a directory that was not there to iterate — never a validation failure.
  Every caller computes `all(report.ok for _, report in reports)` (or the
  equivalent "0 failed"), and that predicate is vacuously true over a shrunken
  or empty list. Measured: deleting one bag from a 3-record archive dropped the
  audit to `2 audited, 0 failed`; deleting all three produced `PASS: 0 bag(s)
  audited, 0 failed`, exit 0, while `browse()` still listed all 3 records, and
  `build_attestation()` — the function a steward's `ledger attest-health` signs
  and publishes to `/proof` — reported `fixity_ok: true`. `audit_fixity` now
  reconciles the bags found against every record id known to `records/` (the
  fast-lookup manifest that always exists once a bag does, per `ingest`'s write
  order) and turns each unmatched record into its own failing entry, keyed by
  record id. A genuinely empty archive — no records, nothing to reconcile —
  still audits clean; this only closes the gap where evidence used to exist.
- **The static accessibility gate can no longer pass having checked zero
  documents.** `web/` ships no static `.html`, so the whole structural floor
  (`lang`, `<title>`, a single `<h1>`, `<main>`, the skip link, `alt`,
  `<label for>`, table `<caption>`/`<th scope>`, `tabindex`) rested entirely on
  `_render_sample_pages()`, which ended in a bare `except Exception: return {}`.
  A renderer that raised for any reason — an import error, a `render.py`
  signature drift, a real bug — made `check_dir`/`main(['web'])` print
  `accessibility check passed for web` and exit 0 having examined nothing.
  `_render_sample_pages()` now degrades silently only for `OSError` (a sandbox
  with no writable temp directory, an environment fact); any other exception
  propagates and fails the gate loudly. `check_dir` now asserts it examined at
  least one HTML document and reports zero as a problem, never a pass, and a
  passing run's own output states how many documents (and stylesheets) it
  checked and names each one — the log is self-evidencing instead of asking a
  reader to trust "passed" on faith. While diagnosing the route-coverage gap
  this bug could hide, `/overview`, `/withdraw`, and `/edit` turned out to
  already build their HTML through a pure function `server.py` calls
  unmodified, so the static gate now renders and checks all three too — no
  `server.py` change needed, and `/withdraw`/`/edit` were the two
  highest-priority gaps this issue named (the pages a contributor uses to
  retract or tighten their own consent). Of 21 HTML-emitting routes in
  `server.py`, 13 now have automated accessibility coverage from either engine;
  the other 8 — including the per-record consent form, the third of the
  three highest-priority routes — need a `server.py` extraction or new sample
  state this PR deliberately does not add, and are recorded, route by route and
  dated, in the new
  [`docs/accessibility/ROUTE-COVERAGE.md`](docs/accessibility/ROUTE-COVERAGE.md)
  rather than left an undocumented gap. Refs #122.
- **Release publication now has a trusted-main control plane.** The workflow
  accepts only an existing SSH-signed stable tag, verifies its signer and main
  ancestry before testing, builds the exact verified commit, and rechecks the
  immutable tag object immediately before both PyPI and checkout-free GitHub
  Release publication.

### Added
- **`ledger heal` and `ledger mutual-aid seal|attest|verify|recover` — the recovery
  capabilities the docs described now have operator entry points.** `replicate.heal`
  and the whole EXP-15 sealed-replica family were written, documented, and unit-tested
  with no subcommand, no route, and no call site in `src/` outside the module defining
  them: 38 references to the sealed family, all in `tests/`. The README states as a
  rule that a quarantined copy "heals from a verified replica", `docs/ARCHITECTURE.md`
  maps Recoverability straight at `replicate.heal`, and `docs/MUTUAL-AID.md` is an
  operator runbook whose steps 3-5 were Python calls against internal APIs — for an
  audience of community archivists and mutual-aid organizers, explicitly not
  developers. `ledger heal` always passes the archive's takedown tombstones, so a
  pending takedown is applied before any copying and a tombstoned bag is never
  resurrected from a stale replica; it prints heal's honest limit (fixity-aware, not
  revision-aware) on stderr where the steward acting on it will read it, and exits
  non-zero if a healed copy arrived torn. The mutual-aid group takes its pairing key
  only from `LEDGER_PAIRING_KEY` — there is no `--key` flag, because a key in argv is a
  key in shell history and in the process table — and `attest` needs neither the key
  nor an archive, so the *holding* partner can run it on a cron job over a directory of
  blobs they cannot read. `verify` and `recover` exit non-zero on a drifted copy or a
  failed drill, so a scheduled check fails loudly instead of reporting a recovery that
  would not have worked.
- **`Archive.bag_path`.** The one supported way for steward tooling to turn a record id
  into a bag directory, with the same path-component validation the rest of the module
  uses, so the CLI does not reimplement it by string concatenation.
- **`ledger --version`.** The CLI had no top-level version flag — `COMMAND` was a
  required positional, so there was no way to ask an installed `ledger` what
  version it was without opening `pyproject.toml`. Prints `ledger <version>` and
  exits 0, sourced from the same `ledger.__version__` (installed package
  metadata) every other version-truth path already uses (RELEASE-AND-VERSIONING-
  STANDARD REL-02), so it cannot drift from `pyproject.toml`.
- `ledger ingest --description ...` sets a Dublin Core description at authoring
  time, and the ingest CLI now nudges (non-blocking) when a record has no
  description, alongside the existing missing-transcript advisory — moving the
  ACR 504 authoring-tool support forward (RM8).

- **Public evaluation path for the first release candidate (2026-07-18).** The
  README now begins with a five-minute, synthetic-data-only walkthrough instead of
  making a prospective adopter infer the first useful command from the architecture
  description. `docs/TRY-LEDGER.md` explains exactly what the executable demo proves
  and what it does not. `docs/reviews/` adds bounded packets for a community
  archivist pilot, an independent threat-model review, and a manual
  assistive-technology review; none represent completed human review. The new
  `docs/RELEASE-0.1.0.md` checklist separates repository-verifiable work from the
  owner-controlled PyPI and human-review prerequisites before a real `v0.1.0` tag.

- **Portfolio-standards conformance remediation (2026-07-11).** Closed all five
  current Tier-1 checker failures with a Python runtime pin, complete CFF metadata,
  canonical README applicability declarations, ADR 0000, and a discoverable
  packaged-catalog marker. Added OpenSSF Scorecard, incident-response conventions
  and GitHub labels, L3 data-governance/data-card documentation, and a dated
  residual-risk register. Enabled GitHub private vulnerability reporting and
  converted every remaining human/account-setting conformance blocker into a
  linked issue rather than an untracked roadmap assertion.

- **DORA delivery-health review + root `DEFINITION_OF_DONE.md` (2026-07-07).**
  `docs/DORA-DELIVERY-HEALTH-REVIEW.md` instantiates QM-11: Deployment Frequency and
  Change Lead Time computed from real merged-PR history (`gh pr list`), with Change
  Fail Rate, Failed-Deployment Recovery Time, and Deployment Rework Rate recorded
  explicitly N/A pending the tag-triggered release workflow (REL-08) that gives them
  something to measure, rather than filled in with invented numbers. Root
  `DEFINITION_OF_DONE.md` instantiates QM-18, tracing every AUTO/REVIEW/RELEASE-GATE
  item to what `ci.yml`/`Makefile` actually enforce today and to the `docs/ROADMAP.md`
  row tracking what doesn't exist yet.
- **Tag-triggered release workflow (`.github/workflows/release.yml`).** Pushing a
  `vX.Y.Z` tag now re-runs the full lint/type/test gate against the tagged commit,
  builds the sdist/wheel, fails closed if the tag doesn't match `pyproject.toml`'s
  version, generates a CycloneDX SBOM of the shipped dependency closure, records
  GitHub-native SLSA build-provenance and SBOM attestations, cosign-signs (keyless)
  every artifact, publishes to PyPI via Trusted Publishing (OIDC — no stored API
  token), and mirrors sdist/wheel/SBOM/signatures onto a GitHub Release. Registering
  the PyPI Trusted Publisher and the `pypi` GitHub Environment remains a one-time
  manual step for the project owner (documented in the workflow header); every other
  stage runs with no additional setup.
- **Hardened release gates (REL-08/10/14/16).** The release workflow's verify job
  now asserts the pushed tag is a *signed annotated* tag (a lightweight or unsigned
  tag fails closed; signature presence is checked — pinning the signer's identity
  awaits a committed allowed-signers file, tracked in `docs/ROADMAP.md`), requires a
  matching `## [X.Y.Z]` section in this file before anything builds, and runs the
  complete `make verify` merge gate (lint, type, test, i18n, accessibility,
  pip-audit, secret-scan, claims) from the locked dependency graph instead of a
  hand-picked subset. After publishing, a new `verify-published` job downloads every
  file PyPI serves for the version and fails the release unless each is sha256-identical
  to what this run built; the GitHub Release only publishes after that check passes.
- **Concurrency-safe workflow stores (FIX-05).** `ledger._filelock.file_lock`, a tiny
  single-host advisory lock (`fcntl.flock` on a sibling `.lock` file, no-op on
  non-POSIX), now guards the whole read-modify-write critical section of
  `ConsentRequestStore`, `SubjectTokenStore`, `SubmissionQueue`, and `ProposalStore`. Under the threaded
  browse server, two concurrent POSTs could previously each read the same JSON store,
  append/modify independently, and have the second atomic rename silently clobber the
  first — for consent this could mean a lost *withdrawal* request, the worst failure
  class this project has. `tests/test_filelock.py` hammers each store from many
  threads at once and asserts nothing is lost, plus unit tests of the lock primitive
  itself.
- **Mutual preservation aid: encrypted replica exchange (EXP-15).** A second, opt-in
  transport in `ledger.replicate` for community instances to hold *each other's*
  bags as redundancy without either side trusting the other with plaintext:
  `seal_bag`/`unseal_bag` encrypt a whole bag with a Fernet key that never leaves
  the owning instance ("key stays home"); `replicate_sealed_bag` writes the
  ciphertext blob — never the bag — to a partner `StorageLocation` and verifies it
  landed intact by digest; `attest_sealed_replica`/`verify_sealed_attestation`
  implement the scheduled fixity attestation exchange, letting a partner prove
  which bytes it holds without ever decrypting them; `recover_sealed_bag` is the
  recovery drill, pulling a blob back, decrypting locally, and running the same
  `validate_bag` used by every other replica. Closes the threat-model residual that
  a hostile or compromised replica host can read what it stores. See
  [`docs/MUTUAL-AID.md`](docs/MUTUAL-AID.md) for the operational runbook.
- **Takedown tombstones and per-location propagation receipts (FIX-08).** A takedown now
  persists a durable tombstone (`src/ledger/tombstones.py`, `logs/tombstones.json`)
  recording that an opaque record id was removed and which storage locations have
  confirmed it. `Archive.remove_all_copies` marks the primary store and every reachable
  replica confirmed; a mirror that was offline at takedown time is left pending. When it
  reattaches, the replication sweep (`replicate.apply_tombstones`, invoked from
  `verify_replicas`/`heal`) deletes the stale copy, writes a per-location PREMIS
  `TAKEDOWN` receipt to `logs/takedowns.premis.json`, and confirms the location — and
  `heal` refuses to re-copy a tombstoned bag back, so a removal can never be silently
  undone. `/consent-status` now reports honest per-location completion ("2 of 3 confirmed;
  mirror-c pending", localized EN/ES) and never overstates it. Tombstones hold only opaque
  ids, an action, and location names — never a title, field, or identity (no-outing).
- **Advisory mutation testing on the safety-critical core (CQ-47).** `make mutation`
  (mutmut, its own `.[mutation]` extra so the audited dependency surface is unchanged)
  scoped to `access/`, `identity.py`, and `fixity.py`, reusing the `disclosure`/
  `preservation` pytest markers as its kill oracle. Never a merge gate — advisory only,
  run weekly and on demand via `.github/workflows/mutation.yml`. The first run found
  `access/grants.py`'s `load_grants` (the function that reads subject → grant mappings
  from an on-disk JSON file) had zero existing tests; `tests/test_grants_load.py` closes
  that gap, raising `grants.py` from 55% to 91.3% mutation score. See
  `docs/MUTATION-TESTING.md` for the full baseline and how to read survivors.
- **Performance budgets in CI (QM-02).** A new `perf` CI job (`tools/perf_budget.py`,
  `make perf`) runs on every push and PR, asserting a time budget over the
  operations a steward actually waits on — content-addressed store put/get,
  streaming dual-algorithm fixity hashing, a full ingest, and a browse listing.
  Budgets are set with wide headroom over a locally-measured median so ordinary
  CI runner noise doesn't fail the build; a failure means a real, order-of-
  magnitude regression (e.g. an accidental linear scan or a dropped streaming
  read). Closes `docs/ROADMAP.md` QM-02.
- **Offline redaction assistant (EXP-07).** `ledger.redact_suggest` is a fully local,
  regex/wordlist detector for likely names, addresses, phone numbers, emails, handles,
  and dates. It runs over a contributor's account text on the contribute-form preview
  (`contribute.render_redaction_suggestions`) and from `ledger redact-suggest --file`,
  and only ever *suggests* — it never edits, drops, or applies anything. A steward or
  contributor who wants a flagged detail hidden still uses the existing per-field
  sealing (`ledger seal`/`ledger redact`) or edits their own text. No network call, no
  subprocess, no model download; recall on a small synthetic corpus is measured and
  asserted in-repo (`tests/test_redact_suggest.py`), and every surface carries the
  honest caveat that this finds *some* identifying detail, not all of it — addressing
  the residual self-disclosure risk noted in the threat model (§4.3).
- **Captions/transcripts with real segment/timing structure (RM6).** `ledger ingest
  --captions filename=path.vtt|.srt` parses an *already-transcribed* WebVTT (W3C) or
  SRT caption file into structured `TranscriptCue` segments (start, end, text, and a
  speaker label where the source format names one — WebVTT's `<v>` voice span; SRT has
  no standardized speaker syntax) and stores them on the payload alongside the existing
  flat `transcript` field, which is auto-backfilled from the cues so every existing
  plain-text consumer (search, the H3 transcript render, export) keeps working
  unchanged. The record page renders the structured cues as an ordered list of timed
  segments. Cues are disclosed under the *same* payload-level policy as everything
  else about the file — there is no separate, weaker disclosure path and no
  per-cue/per-segment consent policy (that granularity question is open; see
  `ledger.models.TranscriptCue`'s docstring). This is caption-file *ingest* only:
  ledger performs no speech-to-text.
- **Disclosure-policy workflow.** First-class, accountable steward commands to set and
  apply a disclosure policy on an already-archived item, enforced by the core engine and
  honoured by the reading-room:
  - `ledger seal` sets the policy of a single field, a payload, or the record default —
    including a temporal embargo (`--field … --level sealed-until --until <date>`,
    time-gated release), a conditional seal (`--condition`), or an absolute seal. Backed
    by `moderate.set_field_policy` / `moderate.set_payload_policy`, each a recorded,
    non-mutating transform emitting a PREMIS `access-policy change` event.
  - `ledger redact` wires the existing `access.redaction` transform into a workflow:
    it destructively replaces a field value with `[redacted]` or drops a payload from the
    stored manifest, recording a PREMIS `redaction` event that names only the
    field/filename, never the removed value.
  - Both require a rationale (accountability) and persist through the one identity-refusing
    write path, so no policy change can leak a contributor identity or a sealed value.
- **Reading-room enforcement proof.** An end-to-end test that applies an embargo and a
  redaction through the workflow, then drives the live stdlib reading-room over loopback
  and asserts the embargoed, redacted, and sealed-identity sentinels appear on no
  anonymous surface (HTML, JSON record/list APIs, CSV export), while the withholding is
  still acknowledged honestly without exposing the embargo date to outsiders.

### Fixed

- **`cryptography` HIGH-severity CVE in the identity vault's encryption dependency
  (2026-08-04).** The pinned resolution had drifted to `cryptography==49.0.0`,
  which carries [CVE-2026-69247](https://avd.aquasec.com/nvd/cve-2026-69247)
  ([PYSEC-2026-3552](https://osv.dev/PYSEC-2026-3552)). Widened the version cap
  from `<50` to `<51` and re-locked to `50.0.0`, the fixed release; the identity
  vault, backup, and replication modules only use the long-stable `Fernet` and
  `Scrypt` APIs, and the full test suite (1020 tests) passes unchanged.
- **Missing production stylesheet (2026-07-12).** Package `web/static/app.css`
  inside wheel and container installs so `/static/app.css` no longer returns 404
  when the server runs outside a source checkout.
- **Cloud-init secret tracing (SEV2, 2026-07-12).** Removed shell xtrace from
  AWS first-boot provisioning after the initial synthetic demo deploy revealed
  that expanded secret assignments reached the IAM-restricted EC2 console log.
  Both demo credentials were rotated, the synthetic archive was rebuilt, and a
  regression test now forbids xtrace in the secret-bearing template. See
  incident [#86](https://github.com/ChelseaKR/ledger/issues/86) and the committed
  postmortem under `docs/incidents/`.

### Prepared as 0.1.0 (2026-06-16) — first reference implementation, not yet tagged

A small collective can install ledger, self-host it on one inexpensive box with no
cloud account, and run the full preservation + selective-disclosure cycle. This was
the intended `0.1.0` content; it ships as a real tagged release once the release
workflow lands.

- **Preservation core.** Content-addressed store (`cas`) with dual-algorithm
  fixity (`fixity`, SHA-256 + BLAKE2b); deterministic, byte-reproducible BagIt bags
  (`bag`, RFC 8493); PREMIS event log and Dublin Core description (`metadata`);
  OAIS SIP → AIP → DIP packaging (`oais`).
- **Disclosure core.** A single access-decision point (`access.policy.disclose`),
  deny-by-default across five policy levels; least-privilege grants (`access.grants`);
  redaction as a recorded, auditable transform (`access.redaction`).
- **Contributor-identity vault.** Separated, authenticated-encrypted store keyed by
  an opaque, content-independent token (`identity`); identity resolves only under an
  explicit unseal grant.
- **Replication** with verify-on-arrival and quarantine-and-heal (`replicate`).
- **Accountable moderation**: content warnings as structured metadata, consent
  changes, takedowns, and an appeal path, all recorded (`moderate`).
- **Accessible surfaces.** A framework-free, stdlib browse/search/API server
  (`server`) targeting WCAG 2.2 AA with a list/table non-visual equivalent and
  content-warning interstitials; a CLI (`cli`); a scripted end-to-end demo (`demo`).
- **Audit-as-artifact.** A no-outing audit suite with sentinel identities; an
  accessibility checker (`accessibility_check`) wired into CI; a generated VPAT 2.5
  Accessibility Conformance Report (`acr_gen`).
- **Project scaffolding.** AGPL-3.0 license, independence NOTICE, threat model,
  governance model, ADRs, Docker/Compose self-host infra, and a CI gate covering
  lint, strict typing, tests, the no-outing safety check, accessibility, CodeQL,
  `pip-audit`, and secret scanning.

[Unreleased]: https://github.com/ChelseaKR/ledger/commits/main
<!-- No v0.1.0 tag exists yet (see the note above), so there is no compare link or
     release link to give until one is actually cut — a placeholder link here would
     be exactly the kind of unbacked claim this changelog is now correcting. -->
