# Large-scale fixes (FIX-01 … FIX-12) — 2026-07-01

Deep structural fixes observed in the 2026-07-01 read. None restates an RM item from
[`../RESEARCH-ROADMAP.md`](../RESEARCH-ROADMAP.md); overlaps are called out and gone
beyond. Effort tiers: S (≤1 day) · M (days) · L (week+) · XL (multi-week).

---

## FIX-01 — AIP revisioning: make post-ingest change preservation-legal

**Pitch.** Stop mutating tag files inside a sealed bag; make every post-ingest change
a new, verifiable AIP revision.

**Why it matters.** `Archive.apply_update` (`src/ledger/ingest.py:641`) rewrites
`record.json`/`premis.json` in-bag without refreshing `tagmanifest-*.txt`, so after
any consent change, policy change, CW addition, or review approval, `validate_bag`
(`src/ledger/bag.py:245`) will report the bag's own tag files as failing — a
legitimate steward action becomes indistinguishable from tampering at the next
audit. This undermines the repo's central integrity claim for every archive that
ever changes a record, i.e. all of them. It also silently weakens quarantine/heal:
a "drifted" replica may simply be an older revision.

**Shape of the work.** Either (a) re-bag on update: `apply_update` routes through a
`bag.refresh_tag_manifests()` that recomputes tag manifests and records a PREMIS
`VALIDATION` event with old/new tag digests; or (b) adopt an explicit AIP-version
model: bags become immutable revisions (`bags/<id>/v0001/…`), the mutable truth
lives in `records/`, and PREMIS chains revisions. (b) is the archivally correct
answer (PREMIS relationships between versions; replicas converge on "latest verified
revision"); (a) is the tactical stop-gap. Update `replicate.verify_replicas`/`heal`
to be revision-aware, and add the missing test class: update-then-audit round trips
(`tests/test_audit_log.py` is the natural home).

**Effort:** L (option a: M). **Risks/deps:** touches replication semantics; must not
break `verify-backup` or golden bags; migration for existing archives needed.
**Excellent looks like:** property test — *any* sequence of ingest/policy/consent/CW
operations followed by `audit_fixity` is all-green, and a byte flipped by hand still
fails; replicas heal across revisions without ever blessing a stale copy.

---

## FIX-02 — Authenticated capability grants (retire the guessable subject header)

**Pitch.** Replace `X-Ledger-Grant: <subject>` name-lookup with unforgeable,
revocable, HMAC-signed grant tokens.

**Why it matters.** Today the subject string is the whole credential
(`server.py:_resolve_grant`): anyone who guesses or shoulder-surfs `devon-steward`
holds steward access to sealed *content*. For the archive's threat model (doxxing,
hostile observers) this is the sharpest gap the threat model does *not* currently
name. RM1 (threshold vault key) protects identity; this protects content and the
steward console.

**Shape of the work.** The pattern already exists in-repo:
`consent.issue_claim_token` (`src/ledger/consent.py:261`) does HMAC over a record id
with a server secret. Generalize: grants file gains a per-subject secret hash; the
header carries `subject:expiry:hmac`; `_resolve_grant` verifies with
`hmac.compare_digest`, honors `expires_at`, and consults a revocation list; each
authenticated request appends a scrubbed grant-use line (subject + route class only)
so a compromised grant is discoverable. CLI `ledger grant issue|revoke|list`
subcommands. Zero new dependencies.

**Effort:** M. **Risks/deps:** must stay usable by a curl-driven steward; document
migration from bare subjects; keep the anonymous default untouched.
**Excellent looks like:** a captured header replays only until expiry/revocation; a
wrong-MAC header is byte-for-byte the anonymous experience (no oracle); a new
`tests/test_grant_auth.py` covers forgery, expiry, revocation, and timing (via
`compare_digest`).

---

## FIX-03 — Memory-bounded media path end to end (make the streaming claim true)

**Pitch.** Stream payloads through ingest, bagging, sealing, and serving; add HTTP
Range.

**Why it matters.** A 4 GB oral-history video is the core use case, and today it is
read fully into RAM at three points: `bag.write_bag`
(`dest.write_bytes(source.read_bytes())`, `src/ledger/bag.py:167`),
`vault.encrypt_bytes` on SEALED payloads (`src/ledger/ingest.py:325`), and
`server._handle_file` (`src/ledger/server.py:1794`). That breaks the
"one-cheap-box" promise exactly for the elder-narrator media the mission centers
(persona A3), and contradicts README §Usability ("large media stream rather than
block").

**Shape of the work.** `shutil.copyfileobj` in `write_bag`; chunked serving with
`Content-Length` + `Range`/`206` support in `_handle_file` (still through
disclosure); for SEALED payloads, a chunked at-rest scheme (Fernet is
all-or-nothing — either chunk-wise Fernet frames with an index, or note the
limitation and cap SEALED payload size honestly until FIX-11's crypto review).
`cas.read_bytes` gains a streaming sibling `open_stream()`.

**Effort:** M (chunked sealing: +SME gate). **Risks/deps:** chunked encryption is
real cryptographic design — do not improvise; land the plaintext-path streaming
first. **Excellent looks like:** ingest and serve of a >2 GB fixture with RSS
bounded (<100 MB) in a marked perf test; seeking within served audio works in a
browser.

---

## FIX-04 — Indexed reads: browse/search/OAI stop being O(archive)

**Pitch.** A disclosure-safe catalog index so every request stops re-parsing every
record manifest.

**Why it matters.** `Archive._all_records` (`src/ledger/ingest.py:751`) globs and
JSON-parses every `records/*.json` on *each* browse, search, OAI, sitemap, Atom, and
CSV request; `search.py` then scans linearly. At a few thousand records on the
"one inexpensive box," every page becomes seconds of CPU — and the timing signal
grows, which touches the RM3 concern from the other side.

**Shape of the work.** A single sqlite3 (stdlib — no new dependency) or flat-file
index under `store/index/`, holding only *listable-safe* projections: record id,
created_at, default policy, DC facets, index text. Rebuilt deterministically from
`records/` (`ledger reindex`), updated by `ingest`/`apply_update`, and treated as a
cache: `disclose` remains the only gate, and the index stores nothing an anonymous
viewer couldn't derive — sealed-existence stays out of it by construction, mirroring
`is_listable`. Pagination pushes down (`pagination.py` already exists).

**Effort:** M/L. **Risks/deps:** the index must not become a second disclosure
surface (audit it with the sentinel suite in `tests/test_no_outing.py`); FIX-01
ordering matters (index keys on revisions). **Excellent looks like:** browse and
search latency flat from 10² to 10⁵ records in a perf test; `rm -rf store/index`
followed by `reindex` is byte-identical; sentinel audit extended over index files.

---

## FIX-05 — Concurrency-safe workflow stores

**Pitch.** Locking (or a single-writer queue) for the JSON stores behind consent,
review, and dual-control.

**Why it matters.** `ThreadingHTTPServer` (`server.py:2055`) means concurrent POSTs
are normal, but `ConsentRequestStore`, `SubmissionQueue`, and `ProposalStore`
(`src/ledger/consent.py`, `review.py`, `dualcontrol.py`) each do whole-file
read-modify-write with no lock (no `fcntl`/`Lock` in `src/`). Two simultaneous
consent requests can silently drop one — a lost *withdrawal request* is a consent
failure, the worst category this project has.

**Shape of the work.** A small shared `ledger/_filelock.py` (`fcntl.flock` on
POSIX; documented single-host scope) wrapped around each store's mutate path, or a
per-store `threading.Lock` plus O_EXCL journal appends. Add contention tests
mirroring `tests/test_ingest_concurrent.py`.

**Effort:** S/M. **Risks/deps:** none serious; NFS caveats documented in
`ADOPTING.md`. **Excellent looks like:** a 50-thread hammer test loses zero
requests; crash mid-write leaves the prior file intact (already true via atomic
rename — keep it).

---

## FIX-06 — Tamper-evident event logs (hash-chained PREMIS + moderation)

**Pitch.** Hash-chain every PREMIS and moderation entry and anchor chain heads
across replicas, so "append-only" survives a raw-disk attacker.

**Why it matters.** Threat model §4.4 admits the logs are append-only *only as
enforced by the application*; a malicious steward with disk access can rewrite
history. No RM item closes this. For an archive whose accountability story *is* the
log, tamper-evidence is the missing half of auditable.

**Shape of the work.** `PremisLog.record` (`src/ledger/metadata/premis.py`) adds
`prev_hash` = SHA-256 of the prior entry's canonical JSON; `ModerationLog` likewise.
`audit_fixity` verifies chains. Chain heads ride along in `replicate.py` transfers
and are compared in `verify_replicas` — divergent history surfaces exactly like
divergent bytes. Emit heads on `/proof` (page exists, `server.py:1753`) for
community cross-checking. Stdlib only.

**Effort:** M. **Risks/deps:** schema bump for `premis.json` (versioned migration,
pattern exists in `config._migrate`); interacts with FIX-01 revisions.
**Excellent looks like:** deleting or editing any historical entry is detected by
`ledger audit` and by any replica holder independently; documented verification
procedure a non-ledger tool can follow.

---

## FIX-07 — A real condition-attestation workflow for `SEALED_CONDITIONAL`

**Status: DONE.** Shipped a condition vocabulary (`config.DEFAULT_CONDITIONS` plus a
validated `conditions` field), a 2-of-N `ledger attest propose|approve|list` flow in
the new `ledger.attest` module (routed through `dualcontrol.ProposalStore`, action
kind `attest`), attested conditions persisted as a durable set and a PREMIS
`POLICY_CHANGE` event, and `Archive.disclose`/`browse` now thread the attested set as
`conditions_met` so every read path (CLI and `server.py`) opens a met seal uniformly.
`succession.build_handoff` can file a `group-dissolved` attestation proposal at
hand-off (`ledger handoff --attest-steward`). End-to-end coverage in
`tests/test_attest.py`.

**Pitch.** Give the "sealed until a condition is met" tier its missing machinery:
declared conditions, recorded attestations, dual-control unsealing.

**Why it matters.** `access/policy.py` accepts `conditions_met`, but nothing in
`server.py` or `cli.py` ever populates it — the tier silently degrades to
steward-only. Yet it is the natural home for the archive's most mission-defining
promises: "open after my death," "open when the group dissolves," "open when I say
so." EX1 (succession) shipped the *group-folding* event; this fix makes such events
able to *unseal what was waiting for them*.

**Shape of the work.** A small condition vocabulary in `config.py` (community-
defined strings, mirroring the CW vocabulary); `ledger attest <condition>` routed
through the existing `dualcontrol.ProposalStore` (2-of-N — one steward cannot
declare someone dead); attestation persisted with a PREMIS `POLICY_CHANGE` event;
`server.py` and `cli.py` pass the attested set as `conditions_met`. Succession
hand-off (`succession.build_handoff`) can emit a `group-dissolved` attestation
proposal.

**Effort:** M. **Risks/deps:** governance text must define who may attest what
(GOVERNANCE.md §3 extension); irreversibility of attestation needs an appeal path
via `moderate.appeal`. **Excellent looks like:** an end-to-end test: record sealed
on `death-of-contributor`, two stewards attest, the field discloses, the whole
chain is in PREMIS; a single steward attesting alone changes nothing.

---

## FIX-08 — Takedown tombstones and propagation receipts

**Pitch.** Make consent propagation across replicas provable, including replicas
that were offline when the takedown happened.

**Why it matters.** `Archive.remove_all_copies` (`src/ledger/ingest.py:680`) deletes
what it can reach *now*; an unreachable mirror keeps its copy with nothing queued to
fix that, and the contributor is told a count, not a completion. GOVERNANCE.md §5
describes propagation policy; the code has no memory of unfinished propagation.
Hard rule #4 ("honors it across replicas") deserves mechanism, not best effort.

**Shape of the work.** A `logs/tombstones.json` of (record_id, action, issued_at);
`replicate.verify_replicas` and `heal` consult it — a tombstoned bag found on any
location is removed and a per-location PREMIS `TAKEDOWN` receipt recorded;
`/consent-status` (route exists) shows per-location completion honestly
("2 of 3 locations confirmed; `mirror-b` last seen 2026-06-12"). Tombstones carry
only opaque ids (no-outing safe).

**Effort:** M. **Risks/deps:** tombstone retention is itself metadata about a
removal — document it; interacts with FIX-01 revisions and EX8-style exports.
**Excellent looks like:** takedown → mirror offline during it → mirror reattaches →
next verify pass removes the copy and records the receipt, no human in the loop;
the contributor-visible status never overstates completion.

---

## FIX-09 — Decompose `server.py` and fuzz the hand-rolled parsers

**Pitch.** Split the 2,089-line server into routed modules and property-test the
multipart/form/cookie parsing that guards the front door.

**Why it matters.** `server.py` mixes routing, HTML rendering, a hand-written
`_parse_multipart` (`server.py:458`), form decoding, grant resolution, and eight
workflows. Every new surface (steward console, consent, edit) has landed here; it is
the file a contributor is most likely to break and the least reviewable. The
multipart parser is security-critical (it receives anonymous uploads when
`--allow-contributions` is on) and currently has only example-based tests.

**Shape of the work.** Extract `server/routes_*.py` handlers behind a small route
table plus a `server/http.py` for send/escape/CSP helpers; keep `render.py` as the
one HTML layer. Add Hypothesis (dev-only dependency) property tests over
`_parse_multipart`, `_read_form_multi`, `_cookie_value`, and `_decode_id`:
malformed boundaries, oversized parts, NUL/UTF-8 edge cases, header injection.
Byte-identical golden responses for the top routes make the refactor provable.

**Effort:** L. **Risks/deps:** pure refactor risk — mitigate with golden-response
tests before moving anything. **Excellent looks like:** no module over ~500 lines;
fuzzers run in CI within budget; a crafted multipart body can at worst 400, never
500, hang, or over-read.

---

## FIX-10 — Truthfulness gate: README/doc claims checked against the code

**Pitch.** A CI check that the repo's factual claims about itself stay true.

**Why it matters.** Honesty is this repo's brand, and drift has already crept in:
README cites "committed `docs/audits/`" (directory absent), "compose/CDK" (infra is
Terraform), "large media stream rather than block" (see FIX-03), "structured logs
and metrics" (the server logs method+status only; there are no metrics). Each is
minor; together they erode exactly the credibility the threat model's honesty buys.

**Shape of the work.** A `tools/check_claims.py` with a small YAML inventory of
checkable claims (paths that must exist, strings that must not appear, features
mapped to test markers), run in `make verify`; plus a one-time README correction
pass. Not a linter for prose — a tripwire for factual claims.

**Effort:** S. **Risks/deps:** keep the inventory small or it becomes noise.
**Excellent looks like:** the four current drifts fixed; deleting `docs/adr/`
or re-introducing a dead claim fails CI with a message naming the claim.

---

## FIX-11 — Commissioned crypto design review of the sealing layer (scoped, not RM1)

**Pitch.** An external review of the *at-rest sealing* constructions specifically:
Fernet reuse of the identity-vault key for content, the `enc:` string-prefix
envelope, and scrypt parameters.

**Why it matters.** RM9 asks for a general security audit and RM1 for threshold
keys. Distinct from both: the SEALED tier reuses the identity vault's Fernet key
for content encryption (`identity.encrypt_text/encrypt_bytes`,
`src/ledger/identity.py:276-316`) — one key now protects two different asset
classes with different compulsion profiles, and revocation/rotation semantics
differ. The `enc:` prefix is an in-band type marker: a legitimate field value
beginning `enc:` would be misparsed on any future decrypt path. These are design
choices a cryptographer should bless or amend *before* RM2 broadens at-rest
encryption on top of them.

**Shape of the work.** A short design doc (key hierarchy: vault key vs. content
key(s), envelope format with explicit version byte instead of string prefix,
rotation story per key class), then the external review; implement the agreed
envelope with a `config` schema migration. Deliberately small surface.

**Effort:** M (internal prep) + external gate. **Risks/deps:** blocks/shapes RM2
and FIX-03's chunked sealing; **must not ship on self-review** — this is a named
SME gate in `04-impact-and-sequencing.md`. **Excellent looks like:** a committed,
dated review artifact (the audits directory FIX-10 makes real), separate content
keys rotatable without touching identities, versioned envelope bytes.

---

## FIX-12 — Browser-real accessibility CI (beyond the static checker)

**Pitch.** Run axe (and keyboard/reflow assertions) against the *served* site in CI,
not only the static HTML heuristics.

**Why it matters.** The merge gate today is `ledger.accessibility_check`
(`src/ledger/accessibility_check.py`) — a self-written static scan (landmarks,
labels, contrast tokens). It is a good floor, but the ACR's credibility and the
portfolio `ACCESSIBILITY-STANDARD` (axe/pa11y auto-gates) want engine-backed
checks over real rendered pages: browse, record view with CW interstitial, the
contribute form, the steward console. RM11 covers the *manual* cadence; this is the
automated depth between static and manual.

**Shape of the work.** A CI-only job (Playwright + axe-core, dev-dependency,
explicitly not a runtime dep — the one-cheap-box constraint applies to serving, not
to CI) that boots `ledger serve` against the demo archive (`demo.py` already builds
one), runs axe on ~8 canonical URLs in light and dark schemes, and asserts zero
serious/critical violations; keyboard-only traversal of the contribute flow.

**Effort:** M. **Risks/deps:** CI flakiness budget; keep the stdlib-only runtime
promise intact. **Excellent looks like:** axe-clean on all canonical pages both
themes; a seeded regression (e.g. dropping a label) fails the job; ACR rows cite
the automated evidence.
