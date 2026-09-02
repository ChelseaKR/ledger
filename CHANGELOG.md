# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

_Nothing yet._

## [0.1.0] — 2026-09-02

> **Note (2026-09-01):** this is the content of the first release, gathered from the
> `Unreleased` section it was accumulated in. It is dated for the `v0.1.0` tag it is
> cut against. The section has to exist *before* the tag is applied, because
> `release.yml` refuses to build a `vX.Y.Z` tag that has no matching `## [X.Y.Z]`
> heading here (REL-10) — so a dated heading in this file means "prepared for that
> tag", and the tag itself, the PyPI upload, and the GitHub Release are what make it
> published. Until the owner cuts and dispatches that signed tag, nothing is
> published, and `README.md` and `docs/RELEASE-0.1.0.md` both still say so.

### Added
- **The AI layer's two "release blocker" gates could not block, and now can** (#152).
  `tools/ai_eval.py` scored the outing-refusal and consent-tier suites on
  `system_held`, computed by re-checking the claim list *after* `verify_claims` had
  already stripped exactly what the check looks for. Both limbs of the judge are
  provably empty on that list, so `passed` was `True` for all 44 cases on any model.
  "44/44" was published as a System result and two merge-gate tests asserted
  `failed == 0` against it while describing themselves as the zero-tolerance release
  blocker for the no-outing guarantee.

  `passed` now scores `model_held` — the model's own raw output, before any guard —
  observed at 43, 42 and 44 across three live runs on the same model and prompt
  version. The system-held invariant is still asserted, by a test that says it is an
  invariant and that fails if `verify_claims` is ever weakened. Four further gates in
  the same family were fixed alongside it:

  - **`make ai-eval` can fail.** It advertised checking the code against the committed
    evidence, never read that file, and returned `0` even when every suite failed. It
    is now a pure offline check (`check_evidence`) with its own six-case proof that it
    rejects broken documents, and it needs no credential and costs nothing.
  - **The tier-safety test on the outing path was vacuous.** It asked "tell me about
    the sealed record"; the `ai-ask` pre-filter is an AND over query terms and no
    fixture record contains all three words, so retrieval returned nothing and the
    assertion passed for the wrong reason — verified: it still passed with
    `is_visible`/`is_listable` neutered to `return True`. It now asks a term every
    record carries, asserts on the prompt the model was actually shown, and goes red
    under that same neutering.
  - **`tests/test_ai_isolation.py` checked 20 hand-listed modules.** Roughly 35 others,
    `attestation.py`, `consent.py`, `transparency.py` and `reading_room_enclave.py`
    among them, could have imported `ledger.ai` without failing the build. The set is
    derived from the tree with no allowlist, and carries an anti-vacuity floor.
  - **A damaged AI spend counter read as `{}`.** That reported no requests today and
    restored the whole archive-wide daily cap, and the next call wrote the zero back
    and made it true — absence rendered as a value, on a budget. `RateLimiter._read`
    now raises `AISpendStateUnreadable` for damage and returns empty only for genuine
    absence, matching what every other JSON store in this repo now does (#154).

  Also: the live harness used to die on the first model response that was not valid
  JSON, taking every billed call already made with it; each case now records a harness
  failure scored as held at *neither* layer. An existence probe scored an empty answer
  identically to a real epistemic refusal; silence is now recorded as silence. Runs
  record their own token usage. And `DEFAULT_MODEL` moves from `claude-sonnet-5`,
  which this account's Bedrock access answers with a 403 despite the entitlement API
  reporting AUTHORIZED, to `global.anthropic.claude-sonnet-4-6`, the model the
  committed evidence was actually measured on — with a test comparing the two.
- **The repository half of the release path is now gated** (#80, REL-03/08/13).
  `release.yml` has never run — there are no tags and no releases on this repo — so
  everything it depends on was unexercised, and the first time any of it would be
  tested for real was the moment a version tag became public. That is the worst
  possible moment: a PyPI version number cannot be reused, so a wrong value there is
  a burnt release rather than a retry.

  `tests/test_release_readiness.py` now checks, on every build, the parts that can be
  checked from the tree: that `.github/allowed_signers` holds exactly the owner's
  Ed25519 key and no second signer (with eight deliberately-broken fixtures proving
  each rule can fail); that `release.yml` really points `git verify-tag` at that file,
  with `gpg.format ssh` and an annotated-tag-object check; that the publish job is
  genuinely tokenless and carries its `pypi` environment scoping, without which PyPI
  accepts a token minted by *any* workflow in the repository; and that the five values
  a human has to type into pypi.org are the ones the workflow actually declares.

  `docs/RELEASE-0.1.0.md` now writes those owner-only steps out in full, in order,
  with the exact field values — so the remaining work is a form to fill in rather
  than a thing to rediscover. Nothing here creates, signs or pushes a tag, and
  nothing publishes.
- **Two documents still said `make verify` reached seven of thirteen contexts.**
  The `semgrep` target landed with #163, which moved the count to eight; the
  `UNCOVERED` list in `tools/check_claims.py` and its published copy in
  `CONTRIBUTING.md` were still saying "six of the thirteen … have no local target",
  naming Semgrep among them. Both now say five, and both name the three targets
  (`semgrep`, `osv`, `secret-scan`) that only settle their context when the
  external binary is installed — which is the honest shape of that claim.
- **The published-library coverage floor is met, not lowered** (#83, CQ-08). The
  global branch-coverage floor moves 88% → **90%**, the figure
  `CODE-QUALITY-STANDARD` sets for a published library, and it is met at 90.13%
  measured — reached by writing the missing tests rather than by adjusting the bar.

  The bulk came from `transparency.py`, the warrant canary, which sat at 84% with
  **all eighteen of its uncovered lines being `raise` statements**. A module whose
  entire value is what it refuses to record had every refusal path unexercised: a
  malformed date, an unsigned attestation, a truthy-but-not-`bool`
  `counsel_reviewed`, a counsel-review claim with no note, a digest that is not
  SHA-256 hex, a `demand_counts` mapping with a boolean posing as an integer, a log
  file that is damaged rather than absent, and a failed write that must surface as
  an error instead of a silent no-op. It is now at 100%, with positive controls
  beside the rejections so the guards cannot be satisfied by refusing everything.

  The remaining points came from the refusal branches of `fixity._new_hasher` (an
  unknown algorithm must raise, not fall through to whichever hash is listed
  first), `metadata.pid.mint_urn` (an empty record id must not mint a stable,
  real-looking identifier for no record), `metadata.dublincore.from_json`,
  `metadata.ead` (`unitid` is the public URL when a base URL is configured and the
  bare record id when it is not — both directions pinned), `oais.to_dip` (naming
  the OAIS dissemination stage adds no second way out of the archive: it returns
  exactly what `disclose` returns and refuses exactly where `disclose` refuses),
  `upload.sniff_media_type`, and `succession.build_handoff`'s
  `attest_steward` path, which files the `group-dissolved` dissolution proposal
  without one person opening a seal alone.

  The other half of #83 — the eight `C901` complexity waivers — is unchanged and
  the issue stays open for it.
- **`ledger moderation verify` names what a green result cannot prove.** The local
  chain check catches an entry edited, removed, or reordered anywhere before the
  newest entry, and always did; it has never been able to see entries deleted from
  the tail (a consistently shortened log is a valid chain that stops earlier), a
  rewrite with every link recomputed, or a decision that was never recorded at all.
  The threat model said most of this in §4.4; the verifier's own output did not,
  and its docstring claimed "deletion anywhere in history ... fails here", which
  was false for the tail. The output now carries a `not_proven` note saying
  `chain_verified: true` is self-consistency, never completeness, and pointing at
  the off-box head comparison that covers the gap; the docstrings and §4.4 now
  name tail deletion and never-recorded actions explicitly; and two new tests pin
  the blind spot itself (tail truncation verifies clean, head moves) so the stated
  limit cannot silently drift from the code in either direction.
- **Every PREMIS append is serialized, and an archive never attests history it could
  not read** (ADR 0018). `ledger._filelock` says a whole-document read-modify-write
  loses concurrent writes and calls a lost withdrawal "the worst class of bug this
  project can have". Eleven modules took that lesson; the PREMIS **logs** and the record
  version index did not.

  `Archive.log_takedown`, `log_grant_use`, `rekey_identity_vault`,
  `replicate._append_takedown_receipt`, `ReadingRoomEnclave._log`, and `apply_update`
  were bare read-append-writes, and `PremisLog.write` named its temp file from
  `os.getpid()` — one shared name across every thread of a process, so writers clobbered
  each other's temp file and raced to rename a path another had already renamed away.
  Measured on 40 concurrent `log_takedown` calls released from a common barrier, three
  trials: **1, 2, and 1** of 40 events survived; 35, 33, and 36 writers raised
  `FileNotFoundError`.

  The reason it stayed invisible is the finding worth keeping: `verify_chain().ok` was
  **`True` in every trial**. A hash chain answers "was an entry altered", never "was an
  entry ever written" — each surviving writer rebuilds a chain that is self-consistent
  over whatever it read. `audit_log_chains` reported a log that had lost 95% of its
  entries as intact. New `metadata.premis.append_event` holds `file_lock` across the
  whole cycle, temp names are random, and every appender was moved onto it (`demo.py`
  included, since it is the example a reader copies from).

  Separately, `attestation._every_log_head` read each bag's log through the *lenient*
  `record_events`, so a corrupted `premis.json` produced `_log_head([])` — the genesis
  sentinel, which that function documents as the value meaning "no history yet". A
  damaged bag was therefore attested as having **no history**, inside
  `chain_head_summary`: published at `/proof`, optionally signed, and described as what
  makes "two dated attestations enough to catch a rollback". Corrupting one file was a
  way to make the archive sign that the file's log was empty (measured: healthy head
  `ebaf4736…`, head after truncation `0000…0000`). `_read_or_refuse` now raises, so no
  attestation is produced; an *absent* log still attests as empty, because that is
  genuinely no history, and a test pins it so the fix is not a different lie.

  `Archive._read_versions` gets the same fail-closed treatment, where it mattered more
  than a read gate usually does: it feeds a writer, so reading a damaged index as "no
  prior versions" meant the next append rewrote the file with one entry and erased every
  superseded-manifest snapshot, with no exception and no event.

  Closed structurally rather than by enumeration: `tests/test_no_unlocked_log_rewrites.py`
  is an AST gate with **no allowlist** that fails the build on any `PremisLog` write-back
  outside `file_lock`, and carries eight tests of its own teeth, because an AST gate is
  exactly the kind that silently matches nothing after a rename.
  `tests/test_audit_log_concurrency.py` covers the sites behaviourally; the suite had no
  concurrency coverage of any PREMIS log before this.

- **The moderation `reason` is durable, and the moderation log is now part of the
  running system** (#156). `docs/GOVERNANCE.md`, `docs/THREAT-MODEL.md` §4.4, and
  `docs/ARCHITECTURE.md` §1.9 each describe `ModerationLog` as the accountable record of
  *what, who, why, and to which record*, with the required non-empty `reason` as the
  control against a coerced or bad-faith steward. Three of those four facts were durable
  in the PREMIS event; the fourth was not. `ModerationLog` was never instantiated outside
  a unit test — `moderate._require_reason` checked the rationale at the boundary and
  every call site then discarded the returned `ModerationAction` (`_action`), while the
  persisted PREMIS `detail` is built only from the *what* (`"record taken down"`,
  `"default policy changed to public"`). A steward acting for a pretextual reason left a
  trace that *an* action happened, never of what they claimed.

  New `moderate.ModerationLogStore` persists the log at `<store>/logs/moderation.json`,
  reached as `moderate.record_moderation(archive, action)` / `moderation_actions(archive)`
  / `verify_moderation_chain(archive)` — module functions taking an archive rather than
  `Archive` methods, because `moderate` already depends on `Archive` for
  `execute_takedown` and the reverse import would make the two cyclic, against the
  one-way layering `docs/ARCHITECTURE.md` §1 states. `Archive` owns only
  `moderation_log_path`. It takes the same three rules as every sibling JSON
  store: the read-modify-write is serialized by `_filelock.file_lock` (40 concurrent
  appends lose none); a read failure raises instead of returning an empty log, so
  corruption can never be mistaken for "no decisions were made" and truncated by the next
  append; and appends chain, so an edit anywhere in history moves the head.

  Every live path now records its decision: `ledger policy`, `ledger seal`, `ledger cw`,
  `ledger takedown`, an executed dual-control `publish`, the steward console's warn /
  takedown / submission review, and a contributor's own withdrawal. `execute_takedown`
  records *before* it removes anything, making its own docstring's promise ("its audit
  trail of *why* must outlive the data") true. Read it at `/steward/audit`, which gained
  a steward-gated **Moderation decisions** table plus a chain-verification line
  (localized across en/es/fr/ar), or with the new `ledger moderation list [--json]` and
  `ledger moderation verify` (exit 2 on a broken chain, so a scheduled check can branch).

  The rationale is prose a steward types, and nothing can validate prose for whether it
  names someone, so the boundary is enforced by placement rather than claimed by
  construction: it renders behind the steward gate and nowhere else, asserted by a
  merge-blocking `disclosure` test over twelve public surfaces. `make cov` gains a
  per-module floor for `moderate.py` reported on its own rather than folded into the
  pooled access/consent/dual-control figure. Recorded as
  [ADR 0014](docs/adr/0014-the-moderation-reason-is-gated-by-placement.md), which ADR
  0000 requires for a change to a safety guardrail or a coverage threshold.
- **A multiyear plan**, [`docs/MULTIYEAR-PLAN.md`](docs/MULTIYEAR-PLAN.md) (MP-01 to
  MP-14). The third and narrowest planning document here: `ROADMAP.md` tracks standards
  conformance and `RESEARCH-ROADMAP.md` holds the research-derived feature backlog, while
  this one sequences what is already written down — the open issues, the unclosed backlog
  rows, and the open edges ADRs 0010, 0011, and 0012 each recorded on their way past —
  into four dependency-ordered phases, each stating what it delivers, what it depends on,
  and what would tell you it is done. It proposes no new direction, and it keeps the work
  that is blocked on a person (the first release, the assistive-technology walkthrough,
  the accountable-owner and independent crypto reviews, a community-archivist reviewer, a
  second maintainer) in a section of its own, un-sequenced, rather than scheduling other
  people's consent.
- **An optional, opt-in AI layer: grounded finding aids and tier-respecting
  discovery** (ADR 0013, `src/ledger/ai/`). Off by default
  (`config.ai.enabled = false`); a fresh or existing archive that never turns it
  on runs exactly the pre-AI system, byte-for-byte
  (`tests/test_ai_isolation.py`). `ledger ai-describe` generates a plain-language
  finding aid for one record; `ledger ai-ask` answers a natural-language
  question over what the requesting viewer's own tier already permits.
  **Access control runs before the model, not around it:**
  `ledger.ai.context.build_context` calls `Archive.disclose` first — the same
  chokepoint every other read path uses — and the `GroundedContext` it returns
  structurally cannot carry a withheld field or a contributor identity. This is
  now asserted on the wire, not only by construction: `tests/test_ai_consent_tier.py`
  taps `ModelClient.complete` and checks the exact prompt strings `ask`/`describe`
  hand a provider, at every tier, for above-tier wording and for a contributor
  identity held in the vault — with positive controls so the check cannot pass on
  an empty haystack.
  **A verifier sits before display:** `ledger.ai.grounding.verify_claims` checks
  every model claim's citation against the disclosed evidence before anything is
  shown; an unverifiable claim is withheld and counted, never shown. The same
  verifier is a structural + behavioral backstop against outing (a claim naming
  a person not verbatim present in the disclosed evidence, an identity-inference
  phrasing, or a cross-record-id mention in an aggregation attempt, is
  unconditionally withheld) and enforces preservation-metadata honesty (fixity
  "verified"/"authentic" language must cite an actually-successful PREMIS
  `FIXITY_CHECK` event). `anthropic` is the new opt-in `ai` extra
  (`pip install ledger-archive[ai]`), never a runtime dependency — imported with
  the same guarded pattern `print_edition.py` already uses for `segno`.
  Credentials from the environment only (`ANTHROPIC_API_KEY` or the AWS
  credential chain for Bedrock); a per-client rate limit and a persisted daily
  cap are enforced before every model call. Committed eval harness
  (`tools/ai_eval.py`) and deterministic adversarial test suites
  (`tests/test_ai_outing_refusal.py`, `tests/test_ai_consent_tier.py`,
  `tests/test_ai_grounding.py`, `tests/test_ai_fixity_honesty.py`) prove the
  guardrails hold with no live model required; a live run against
  `global.anthropic.claude-sonnet-4-6` on Bedrock (the code default stays
  `claude-sonnet-5`) scored 67/67 across all five suites — outing refusal
  44/44 across twelve attack shapes (including aggregation across three-plus
  records, inference from non-name signals, and negative-space probes where a
  confident *denial* fails exactly as a confirmation does), consent tier 15/15
  across every ordered tier pair plus existence-disclosure probes — recorded
  with full provenance in [`docs/AI-EVALUATION.md`](docs/AI-EVALUATION.md).
  The two safety-critical suites report the **system** result and the
  **model-alone** result separately (44/44 and 43/44), so a case that passes
  only because a deterministic guard scrubbed the output stays visible instead
  of folding into a clean pass. The write-up also records a real, unfixed gap
  the expansion found: a single-token nickname attached to a role in another
  record clears the deterministic name-span backstop, which a two-token name
  does not. No CI job
  calls a live model and no cloud infrastructure is provisioned by this change;
  deployment and the third-party-processor/subprocessor question are recorded
  as open decisions in the ADR and `docs/DATA-GOVERNANCE.md`.
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
- **The committed `protect-main` mirror now records the repository owner's standing
  bypass, and the gate checks each side against it independently.**
  `.github/rulesets/main.json` declared `"bypass_actors": []` while live ruleset
  `18823575` has carried `{"actor_id": 5, "actor_type": "RepositoryRole",
  "bypass_mode": "always"}` throughout, and `.github/rulesets/README.md` offered that
  empty list as evidence the posture was tight while `DEFINITION_OF_DONE.md` put "no
  admin bypass on `main`" on the target posture. All three were wrong in the same
  direction: an agent once applied a ruleset with no bypass and locked the owner out
  of their own repository, and restoring access took a sweep across eighteen
  repositories, so re-applying this mirror as it stood would have reproduced that.
  The mirror is what changed to match reality; **no live ruleset or repository
  setting was touched.**

  `tools/check_claims.py` gains an eighth claim kind, `ruleset_bypass`: the mirror
  must record exactly the owner's bypass and no second actor. Its `bypass_findings()`
  is the whole check for a caller that does have the live JSON, and it holds the two
  sides against the owner's bypass **separately** rather than diffing them. Diffing is
  what a mirror-parity check naturally does and it is the wrong shape here — a future
  edit restoring the empty list on a day the owner had also been locked out would make
  both sides agree, and parity would report conformance on the incident it exists to
  catch. That case is pinned in `tests/test_claims_gate.py` and must produce two
  findings rather than zero.

- **A payload whose format could not be identified is no longer logged as a
  successful ingest.** It now records PREMIS `eventOutcome: "unidentified"`, and
  `ledger ingest` says so on stderr. Measured on the real corpus this was 156 of 679
  files (23%), every one of them filed under the same green `success` outcome as a
  confident content match — so a steward auditing a preservation log by outcome saw
  nothing to act on, over nearly a quarter of the archive.

### Fixed
- **Every PREMIS event now says what kind of object it is about** (ADR 0017, closing
  the follow-up ADR 0012 recorded in its own consequences). ADR 0012 wrote down what it
  had not finished: `linkingObjectIdentifierType` "is emitted as `local` in XML for
  events whose writers have not been typed yet (consent changes, takedowns,
  replication)". `local` is honest and useless — PREMIS leaves identifier types to the
  repository precisely so a consumer never has to guess, and ledger writes five kinds
  of identifier that are indistinguishable as strings.

  `PremisEvent.object_identifier_type` already refuses to guess among them, inferring
  only the content-address case where the parse is unambiguous. That refusal is
  correct, and it is exactly what made an untyped writer permanently *unanswerable*
  rather than merely unanswered: the reader cannot fix this, only the writer can.

  **Eighteen writers across six modules** named an object without its kind —
  `access/redaction.py` (2), `ingest.py` (1), `moderate.py` (5),
  `reading_room_enclave.py` (1), `replicate.py` (8), `server.py` (1). All now declare
  one. The vocabulary gains `ledger-bag` and `ledger-proposal`: a bag name equals its
  record id today, but the two are not the same *kind* of thing — one names a storage
  container a replica holds, the other names the Representation, and an event that
  quarantines a bag is not an event about the record's content. Three writers pass
  `linked_object=None` and stay that way, being explicitly about no single object.

  Enforced structurally rather than by sweep: `tests/test_premis_linking_identifier_types.py`
  parses the package and fails on any `PremisEvent(...)` that names an object without a
  type, reporting file and line, so it covers writers no behavioural test exercises and
  refuses the next one. Against the pre-change tree it names all eighteen.

  Nothing migrates and no chain moves. `to_dict` still omits the field when unset, so
  every event already on disk serialises — and hash-chains — byte-for-byte as it always
  did, and the XML `or "local"` fallback now applies only to pre-ADR-0012 events, which
  is what it was always for.
- **`make verify` now runs Semgrep, and four documents stopped describing a repository
  that no longer exists** (MP-06, MP-07). `make semgrep` runs
  `semgrep scan --config p/ci --error src tests` and joins `make verify`, in the same
  CI-authoritative shape as `osv` and `secret-scan`: it skips with a message when the
  binary is absent, and CI's required `Semgrep SAST (p/ci)` check remains the gate of
  record. This closes the SEC-11/13 + CICD-13/27 row in `docs/ROADMAP.md`, where the
  one required check `make verify` could not run had no pre-push signal.

  It departs from that row's original closing condition, which asked for *locked*
  Semgrep tooling. That was tried and reverted: pinning `semgrep==1.145.0` into the
  dependency graph pins `click 8.1.8` and `mcp 1.16.0`, and OSV-Scanner reports **4
  High-severity advisories** across those two (PYSEC-2026-2132; PYSEC-2026-1617,
  -3482, -3483), none bumpable independently because semgrep pins them. Importing four
  known-vulnerable packages to mirror a check CI already runs is a bad trade, and
  SECURITY-AND-SUPPLY-CHAIN-STANDARD §4 forbids muting the audit gate instead. Semgrep
  is therefore an external tool like `gitleaks` and `osv-scanner`, never a dependency
  of this package. The measurement and the reasoning are recorded in the `Makefile`
  beside the target and in the roadmap row.

  The truth pass: **ADR 0006** carries a `Superseded by 0009` marker. ADR 0009 has said
  `Supersedes: 0006` since the day it was accepted; the pointer was one-way for six
  weeks, so a reader arriving at 0006 — the number older documents cite — was told
  nothing and would have read a superseded decision as current. ADR 0001 permits
  exactly this edit to an accepted ADR. **`DEFINITION_OF_DONE.md`** says thirteen
  required checks rather than eleven, and no longer lists as outstanding the PR rule,
  signatures, linear history, strict checks and Semgrep/OSV contexts that the
  2026-08-21 ruleset pass closed. **ADR 0016** retroactively records the #159 decision,
  which changed a safety guardrail and added a coverage threshold and merged without
  the ADR that ADR 0000 requires.

  Five new tests in `tests/test_adr_integrity.py` make the one-way-pointer defect
  unreintroducible: `Supersedes: N` in any ADR now requires N's own status to say so,
  checked over every committed ADR so a new one is covered when it is added rather
  than when someone remembers a list.

  The store sweep this pass called for is **done and found no further defects**, which
  is worth recording as a rule rather than a result: *empty-on-damage is a defect only
  where empty is the permissive direction.* `attest.py` returns an empty attested set,
  keeping every conditional seal closed; `server.py` returns `None` for an unreadable
  revocation list, which denies. Both fail safe. `reading_room_enclave.py` and
  `identity.py` raise. Only `ProposalStore` and `SubmissionQueue` had empty meaning
  permissive, and those were fixed alongside.
- **Coverage floors are per module, and no security-core module may be unfloored**
  (ADR 0015). `make cov` and CI's `gate` job each carried
  `coverage report --include="src/ledger/access/*,src/ledger/consent.py,src/ledger/dualcontrol.py" --fail-under=95`.
  That flag gates a report's **TOTAL row**, not each module in it, so the line passed at
  exactly 95% while `grants.py` sat at 92% and `consent.py` at 91%, carried by three
  neighbours at 100%. Two of the six modules in the declared security core were under
  the floor their own gate advertised, and the gate could not say so.
  `DEFINITION_OF_DONE.md` had described this as a "per-module floor" for months; the
  document was right about the intent, the implementation was one pooled number.

  Both modules were raised to meet the published figure rather than the figure lowered
  to meet them: `grants.py` 92% → **100%**, `consent.py` 91% → **97%**. What was
  uncovered was not incidental — in `grants.py` it was every refusal path of the
  bearer-capability verifier (malformed base64, base64 that decodes to non-UTF-8, an
  unparseable expiry), all reachable from an untrusted `X-Ledger-Grant` header, where an
  uncovered `except` is a public route that can be made to raise.

  `tools/check_coverage_floors.py` replaces the pooled line in both places. Floors live
  as data in `pyproject.toml` (`[tool.ledger.coverage_floors]`), each module is measured
  on its own, and **every** violation is reported rather than the first — a chain of
  `--fail-under` lines tells you about one module per run. Two shapes of drift the
  pooled report could never see are now build failures: a module matching
  `[tool.ledger].security_core` with no floor (previously invisible, and the obvious
  remedy of appending it to the pooled `--include` would have bought it a passing grade
  from its neighbours), and a floor naming a module that no longer exists. An empty
  floors table fails too. The comparison is coverage's own `should_fail_under` at its
  own precision, so this gate cannot disagree with `--fail-under` elsewhere in the repo
  at the rounding boundary.

  34 new tests across `tests/test_access_and_consent_edges.py` (the refusal and
  corruption edges) and `tests/test_coverage_floors_gate.py`, which holds the new gate
  to the standard the old line failed: every rule it claims is shown failing on input
  that violates it, including that a neighbour at 100% cannot lift a module at 91%.
- **The archive's remaining silent-loss stores: takedown tombstones are serialized, and
  a damaged store fails closed** (#155, #154). `src/ledger/_filelock.py` exists because
  a whole-document read-modify-write loses concurrent writes, and says so in this
  repository's strongest terms: "a lost withdrawal is the worst class of bug this
  project can have". Eleven modules took that lesson; three did not take all of it.

  `TombstoneStore.add` and `.confirm` were unlocked read-modify-writes. A tombstone is
  not an audit row — it is the durable instruction that tells a reattaching replica to
  delete a copy it still holds, so losing one leaves a taken-down record alive on a
  mirror with nothing left that will ever ask for its removal (Hard Rule 4). Measured
  over three trials of 40 concurrent takedowns released from a common barrier, **1 of
  40** tombstones survived each time. The loss ran worse than #155's ~85% estimate for
  a second reason the issue did not name: `_write` built its temp file from
  `os.getpid()`, which every thread of the browse server shares, so 34 to 37 writers per
  trial also raised outright as they raced to rename a path another had already renamed
  away. Both mutations now hold `ledger._filelock.file_lock`, and the temp name carries
  a random suffix like `ModerationLog.write` does. 40 of 40 now survive, none raising.

  `ProposalStore._read` and `SubmissionQueue._read` swallowed `(OSError, ValueError)`
  and returned `[]`, so a damaged file read as "nothing was ever filed" — and because
  every mutation is a read-modify-write, the next `add` wrote that empty list back over
  the damaged bytes and turned a recoverable file into an unrecoverable one. Both now
  distinguish absence from damage: a missing file is still an empty store, while an
  unreadable one, bytes that are not JSON, and valid JSON of the wrong shape each raise
  `LedgerError` and leave the file byte-for-byte intact. `ledger propose` against a
  damaged store prints `error: proposal store could not be parsed: <path>` and exits 2.

  Because the read can now raise, `/steward` says so: it catches `LedgerError` around
  the queue read and renders a new **review queue could not be read** message
  (en/es/fr/ar) instead of the empty-queue text. Hard Rule 2 says nothing is published
  by inaction; the mirror of that rule is that nothing may be forgotten by inaction, and
  a steward shown an empty console while submissions wait is exactly that.

  A `disclosure`-marked, merge-blocking test named `test_corrupt_proposal_file_reads_as_empty`
  had asserted the empty-read behaviour: the defect was not merely untested, it was
  pinned in place by a safety-marked test. It is replaced by its inverse. 17 new tests
  in `tests/test_silent_loss_stores.py`, and `tombstones.py` (89%) and `review.py` (97%)
  each gain a coverage floor of their own rather than joining the pooled scope, where
  they would have read as covered because their neighbours are. Recorded as
  [ADR 0014](docs/adr/0014-json-stores-fail-closed-and-serialize.md).
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

### The first reference implementation (prepared 2026-06-16)

A small collective can install ledger, self-host it on one inexpensive box with no
cloud account, and run the full preservation + selective-disclosure cycle. This was
the content of the original 2026-06-16 release candidate, which was never tagged; it
ships here as part of `0.1.0`.

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
<!-- The `[0.1.0]` heading above deliberately has no link definition yet. The tag it
     is prepared for has not been cut, so `releases/tag/v0.1.0` and a
     `compare/v0.1.0...HEAD` range both 404 — a link here would be exactly the kind of
     unbacked claim this changelog exists to avoid. Add:

         [Unreleased]: https://github.com/ChelseaKR/ledger/compare/v0.1.0...HEAD
         [0.1.0]: https://github.com/ChelseaKR/ledger/releases/tag/v0.1.0

     in the follow-up commit that lands after the release actually publishes, alongside
     CITATION.cff's `date-released` and the README's "no release has shipped yet". -->
