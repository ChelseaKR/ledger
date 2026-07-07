# Deep dive — current state as read on 2026-07-01

Assessment from a full read of the source, tests, docs, and CI. Nothing here was
executed; where a claim depends on running the code, that is said.

## What ledger is

A privacy-first community archive for queer histories and mutual-aid knowledge: a
pre-1.0, AGPL-3.0 reference implementation (`pyproject.toml` v0.1.0) whose product is
the *join* of preservation rigor (BagIt/PREMIS/Dublin Core/OAIS) and a structural
no-outing guarantee for contributors for whom exposure is dangerous. Users are
contributors (possibly not out, undocumented, criminalized), community stewards,
readers, partner archivists, and self-host operators — the persona cast in
`docs/USER-RESEARCH.md`.

## Architecture summary (verified against code)

- **Contract layer:** `src/ledger/models.py` — behaviour-free value objects. The
  load-bearing invariant is typed: `Record` carries at most an opaque `identity_ref`;
  `DisclosedRecord` (frozen, no identity field) is the only shape read paths may emit.
- **Preservation floor:** `src/ledger/cas.py` (sharded, atomic, dedup content store),
  `src/ledger/fixity.py` (dual SHA-256+BLAKE2b, 1 MiB streaming hash),
  `src/ledger/bag.py` (deterministic RFC 8493 bags; tag manifests cover the in-bag
  `record.json`/`dublincore.json`/`premis.json`).
- **Disclosure:** `src/ledger/access/policy.py` — `is_visible` is the single pure,
  deny-by-default decision point; `disclose` is the sole `DisclosedRecord`
  constructor. Six policy tiers including the absolute `SEALED` (encrypted at rest via
  the vault, invisible even to stewards).
- **Identity:** `src/ledger/identity.py` — Fernet vault, CSPRNG refs, grant-gated
  `resolve`, atomic 0600 writes, `rekey`, plus `encrypt_text`/`encrypt_bytes` reused
  for SEALED-at-rest content.
- **Ingest:** `src/ledger/ingest.py` — `ingest_sip` is one fixed path (hash → store →
  format-ID → seal identity → bag → document → `_assert_identity_free` rescan, with
  orphan-identity rollback on failure). `Archive` is the facade; `records/*.json` is a
  fast-lookup mirror of the in-bag manifest.
- **Preservation planning:** `src/ledger/preservation.py` — dependency-free
  PRONOM-style signature registry with at-risk flags and PREMIS
  `FORMAT_IDENTIFICATION` events (shipped as RM4).
- **Workflows:** `src/ledger/consent.py` (HMAC claim tokens, request store),
  `src/ledger/dualcontrol.py` (2-of-N proposals for high-stakes actions),
  `src/ledger/review.py` (submission queue), `src/ledger/moderate.py` (attributed,
  appealable log), `src/ledger/succession.py` (EX1 hand-off manifest + runbook).
- **Surfaces:** `src/ledger/server.py` (2,089 lines; stdlib `ThreadingHTTPServer`;
  ~30 routes including contribute/withdraw/edit, steward console, OAI-PMH, Atom,
  CSV export, i18n EN/ES) and `src/ledger/cli.py` (950 lines, ~20 subcommands).
- **Quality machinery:** 48 test modules / 481 test functions (docs cite 521
  collected tests via parametrization — not re-run for this pass); CI runs lint,
  mypy strict, tests, a *separate named* no-outing gate (`pytest -m disclosure`),
  static a11y checks, gettext catalog gates, pip-audit (blocking), gitleaks, CodeQL,
  SHA-pinned actions. `make verify` mirrors CI. `infra/` ships compose and a
  Terraform EC2+S3 path.

Git history is healthy and recent (last commit 2026-06-30, i18n gettext migration
PR #17); working tree clean.

## What is genuinely strong

1. **The no-outing guarantee is architectural, not procedural.** The leak is a type
   error (`DisclosedRecord` has nowhere to put identity), then a single chokepoint,
   then a defense-in-depth rescan, then a dedicated CI gate. Few production systems
   are this disciplined.
2. **Fail-closed reflexes are everywhere.** `_unseal_reached` and `Grant.is_expired`
   treat malformed timestamps as *sealed/expired*; ingest revokes a sealed identity
   and removes the partial bag on any failure; `heal` refuses to act when no replica
   validates.
3. **Determinism is real.** Injected `now`, canonical JSON, sorted manifests —
   byte-identical bags are plausible as claimed, and golden-bag testing is possible.
4. **Honesty is a design habit.** The threat model's residual-risk lines, the ACR's
   `Partially Supports` rows, and `ADOPTING.md`'s "this is your responsibility"
   framing are unusually candid.

## Structural debt and gaps actually observed

These are the observations that drive `02-large-scale-fixes.md`; each cites code.

1. **Post-ingest updates silently break the bag's own tag-manifest guarantee.**
   `Archive.apply_update` (`src/ledger/ingest.py:641`) rewrites `record.json` and
   `premis.json` *inside the bag* without refreshing `tagmanifest-*.txt`, while
   `bag.validate_bag` verifies tag files against tag manifests. As read, any consent
   change, policy change, content warning, or review approval leaves the bag failing
   its own tag verification — legitimate change is indistinguishable from tampering
   at the next `audit_fixity`. No test combines `apply_update` with `audit_fixity` on
   the same bag (checked `tests/`), so the suite would not catch it. (Unverified by
   execution; the code paths are unambiguous.)
2. **The grant header is a guessable bearer credential.** `X-Ledger-Grant: <subject>`
   is looked up verbatim in the grants file (`server.py:_resolve_grant`); knowing a
   subject string *is* the credential. No secret, no revocation beyond editing the
   file, no rate limiting, no grant-use audit event. The docs' "the header confers
   nothing on its own" is only true if subjects are treated as high-entropy secrets,
   which nothing enforces.
3. **Large-media claims outrun the code.** `bag.write_bag` copies payloads via
   `dest.write_bytes(source.read_bytes())`; SEALED payloads round-trip whole through
   `vault.encrypt_bytes`; `server._handle_file` builds the entire response in memory
   with no Range support. The README's "large media stream rather than block" is
   currently true only of *hashing* (`fixity.py`).
4. **Every read is O(archive).** `Archive._all_records` re-globs and re-parses every
   `records/*.json` per browse/search/OAI/feed request; `search.py` is a linear scan
   over disclosed records. Fine at 100 records, hostile at 50,000.
5. **Concurrency is unguarded around the JSON side-stores.** The server is threaded,
   but `ConsentRequestStore`, `SubmissionQueue`, and `ProposalStore` all do
   whole-file read-modify-write with no locking (no `fcntl`/`threading.Lock` anywhere
   in `src/`): concurrent POSTs can lose updates.
6. **`SEALED_CONDITIONAL` has no condition machinery.** `conditions_met` is threaded
   through `access/policy.py` but nothing in `server.py` or `cli.py` ever supplies
   it; there is no vocabulary, attestation workflow, or PREMIS event for "condition
   met." The tier is effectively steward-only today.
7. **Docs drift against honesty-as-a-feature.** README twice cites "committed
   `docs/audits/`" — the directory does not exist; the architecture tree says
   "compose/CDK" where infra is Terraform; streaming (above). Small, but this repo's
   brand is that claims are tested.
8. **Append-only logs are only app-enforced.** The threat model (§4.4) names raw-disk
   log tampering honestly, but no RM item closes it: `PremisLog` and `ModerationLog`
   have no hash chaining or cross-replica anchoring.

## Strategic position in the portfolio

ledger is the portfolio's flagship *preservation + safety* system — the deepest
threat model, the strongest structural-guarantee story, the only OAIS/BagIt/PREMIS
implementation, and (with 48 test modules and five ADRs) among its most mature
codebases. Its `bag.py`/`fixity.py`/`cas.py`/`premis.py` core is genuinely reusable
by other repos (the README claims this; nothing yet packages it). Its biggest
portfolio-level risks are the ones its own docs name: single maintainer, no
independent security audit (RM9), and a reference implementation whose safe
deployment still depends on operator diligence (EX6). The ideas in this folder aim
past those already-tracked items at what the roadmaps do not yet see.
