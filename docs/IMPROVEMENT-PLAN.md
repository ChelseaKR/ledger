# ledger — Improvement Plan

> A prioritized, evidence-based plan for the next phase of work, drawn from a full read
> of the codebase as of this branch. Each item names the file(s) involved, ties the
> rationale to ledger's own commitments (the **Hard rules** in [`../README.md`](../README.md),
> [`THREAT-MODEL.md`](./THREAT-MODEL.md), [`GOVERNANCE.md`](./GOVERNANCE.md), and the
> [`ACR`](./accessibility/ACR.md)), and states a concrete "done when".
>
> **Drafted: 2026-06-17.** Planning document, not a contract; sequence and scope are
> subject to maintainer judgement.
>
> **Execution status (2026-06-18).** P1-1 through P1-4 and P2-1 and P2-3 are
> implemented in this branch: the backup→restore disaster-recovery test (`make
> backup-test`), a recorded `ledger vault rekey` key-rotation path, a deeper
> `/healthz` readiness probe, replication interruption/partial-transfer tests, the
> WCAG 4.1.3 live-region for result counts (ACR row upgraded to *Supports*), and a
> concurrent-ingest safety test. **P2-2** (split `server.py`) and **P2-4** (refactor
> `demo.py`) are intentionally deferred — internal refactors with higher churn and
> lower user-visible value, best done as their own focused changes.

## How to read this

Items are graded **P0 → P2**:

- **P0 — promise-integrity.** A gap in one of the four Hard rules (no-outing, narrowest
  disclosure, fixity-checked, consent-revocable) or in a load-bearing safety gate.
- **P1 — operability & resilience.** Things a real steward will hit when running this for
  a community on a single box, where "lose the data" or "leak the key" are live risks.
- **P2 — depth & polish.** Strengthens the reference without changing its shape.

The codebase is exceptionally disciplined for a pre-1.0 reference: a single disclosure
chokepoint (`access.disclose()`), identity structurally separated and encrypted, a
dedicated no-outing CI audit, a merge-blocking WCAG 2.2 AA gate, stdlib-first with one
runtime dependency, and a clean tree (no `TODO`/`FIXME`). **No P0 integrity gaps were
found** — the safety-critical paths are tested and gated. This plan is therefore about
operational resilience and finishing candidly-flagged partials, not fixing broken
promises.

---

## P1 — Operability & resilience

### P1-1 · Add an automated backup → restore disaster-recovery test
**Files:** `Makefile` (new `backup-test` target), `tests/`, `infra/README.md`

The infra runbook tells a steward that their two backup obligations are the **data volume**
and the **vault key, kept apart** — and that ciphertext is useless without the key. That is
exactly the scenario most likely to be botched in a real, volunteer-run community archive,
and it is the one path with **no automated test**. `test_replicate.py` covers replica
verify-and-heal, but nothing exercises a full *restore from cold backup* cycle: take an
archive, snapshot the store + vault separately, wipe, restore, and assert that (a) every
bag re-validates against its manifest, (b) sealed identities still resolve with the right
grant, and (c) no sentinel leaks survived the round-trip.

For a tool whose reason to exist is "no single failure or seizure loses the record," a
tested restore path is the most valuable thing missing.

**Done when:** `make backup-test` runs an ingest → back-up → wipe → restore cycle and
asserts fixity re-validation, grant-gated identity resolution, and the no-outing invariant
post-restore; it runs in CI.

### P1-2 · Formalize vault-key rotation as a recorded operation
**Files:** `src/ledger/identity.py`, `src/ledger/metadata/premis.py`, `docs/GOVERNANCE.md`,
`infra/README.md`

The identity vault encrypts contributor identity with Fernet under a scrypt-derived key
(`identity.py`). The runbook treats the vault key as a long-lived secret, but there is no
defined rotation/rekey procedure — and key rotation is a *when*, not an *if* (steward
turnover, suspected exposure, compliance). Rolling the key is precisely the kind of
sensitive act ledger's own design says should be deliberate, attributed, and recorded.

Provide a `ledger vault rekey` path that re-encrypts the vault under a new key, emits a
PREMIS `policy-change`-class event (without ever logging key material or identities), and
is documented in the steward runbook and governance roles. Pair it with the principle
already in the codebase: the rekey must leave the no-outing audit green.

**Done when:** a documented, PREMIS-recorded rekey operation exists, is tested (old key
fails, new key resolves, no identity in logs), and is described in `GOVERNANCE.md` and the
infra runbook.

### P1-3 · Deepen the container health check beyond an HTTP 200
**Files:** `infra/Dockerfile` (`HEALTHCHECK` → `/healthz`), `src/ledger/server.py`

The Dockerfile health check curls `/healthz`. A liveness probe that only proves the HTTP
server is up can stay green while the vault is unreachable or the store path is missing —
i.e. while the archive is functionally broken in exactly the ways that matter. Make
`/healthz` assert the readiness invariants that are cheap and safe to check (store path
present and readable, vault openable *without* unsealing anything, config loaded) and
return non-200 when they fail — still emitting nothing identity-bearing.

**Done when:** `/healthz` fails when the store or vault is unavailable, a test covers both
the healthy and degraded responses, and neither response leaks protected values.

### P1-4 · Test replication interruption and partial-transfer edge cases
**Files:** `src/ledger/replicate.py`, `tests/test_replicate.py`

Replication is tested for the happy path and quarantine-and-heal, but not for the failure
shapes a real off-site mirror produces: a transfer interrupted mid-copy, a truncated bag
arriving, or a timeout during post-arrival validation. The module's correctness rests on
"never trust a divergent copy" — that guarantee deserves explicit tests for partial and
interrupted transfers so a future refactor can't quietly weaken it.

**Done when:** tests assert that interrupted/truncated replicas are quarantined (never
promoted) and that a healthy replica still heals them, with a PREMIS event recorded.

---

## P2 — Depth & polish

### P2-1 · Close the two substantive ACR "Partially Supports" rows
**Files:** `src/ledger/server.py`, `src/ledger/accessibility_check.py`,
`docs/accessibility/ACR.md`

The ACR is candid, and most "Partially Supports" rows are honestly partial only because
the surface is small (e.g. error-suggestion criteria with a single search field). Two are
real, finishable work:

- **4.1.3 Status Messages.** Search result counts are rendered in page text but not
  announced via an `aria-live` region. Add a polite live region for result-count and
  empty-state announcements, and extend `accessibility_check` to assert its presence.
- **504 Authoring Tools.** The ingest CLI accepts accessible metadata but does not prompt
  the author to supply it (e.g. alt text for image payloads). Add optional prompting /
  validation so the authoring path actively encourages accessible description.

Doing these upgrades the rows to "Supports" honestly (gate-enforced), rather than by
assertion.

**Done when:** the live-region announcement and authoring-prompt exist and are gate-checked;
the ACR rows are updated with the new evidence.

### P2-2 · Split `server.py` along its existing seams
**Files:** `src/ledger/server.py` (1,373 lines)

`server.py` is the largest module — routing, HTML rendering, JSON API, static serving, and
the scrubbed access log in one file. It is not *coupled* (the single `_esc` escaping
boundary and pure render functions are good), but its size makes the public surface harder
to audit, which matters because every response must pass through `disclose()`. Extract the
HTML rendering and the API/route handlers into sibling modules behind the same chokepoint,
leaving `server.py` as the thin wiring layer. Pure refactor; the no-outing audit is the
guardrail that it changed nothing observable.

**Done when:** rendering and routing live in focused modules, the no-outing and server
tests pass unchanged, and no response path bypasses `disclose()`.

### P2-3 · Add a concurrent-ingest safety test
**Files:** `src/ledger/cas.py`, `src/ledger/ingest.py`, `tests/`

Ingest is single-threaded in tests; the CAS uses atomic writes and content-addressing, so
concurrent ingest of identical or distinct payloads *should* be safe and idempotent — but
that's asserted by reasoning, not by a test. Add a stress test that runs simultaneous
ingests against one archive and verifies store integrity, dedup correctness, and that no
two writers corrupt a shard. Cheap insurance for the "single inexpensive box" deployment
model, where a steward may well script bulk ingest.

**Done when:** a test runs N concurrent ingests and asserts CAS integrity and idempotency.

### P2-4 · Refactor `demo.py` onto shared test helpers
**Files:** `src/ledger/demo.py` (~400 lines), `tests/`

The end-to-end demo duplicates a fair amount of setup/teardown that overlaps with the test
suite. Factoring the shared "build an archive, ingest, grant, replicate" steps into
reusable helpers shrinks the demo, keeps it honest (it exercises the same paths the tests
do), and lowers the cost of keeping it current as interfaces evolve pre-1.0.

**Done when:** `demo.py` is built from shared helpers and `make demo` still performs the
full ingest → seal → grant → verified-replica → no-outing walk.

---

## Explicitly out of scope (documented future work, not debt)

These are named in the docs as future directions and should stay on the backlog, not in
this plan: the **map/collection view** (the list + table remain the authoritative
non-visual equivalent until then; see [`ACCESSIBILITY.md`](./ACCESSIBILITY.md)), the naive
in-memory **search** (intentional for small-archive affordability), and **multi-site
federation**. Each is a deliberate trade-off recorded in the README's quality attributes,
not an oversight.

---

## Sequencing

1. **P1-1** first — a tested restore path is the highest-leverage resilience gap for a
   tool that exists to not lose records.
2. **P1-2 → P1-3** next: the key-rotation and health-check gaps a steward hits in real
   operation.
3. **P1-4** rounds out replication hardening.
4. **P2** items are depth/polish for the next minor version; **P2-1** is the most
   user-visible (it removes two honest caveats from the published ACR).

All P1 items add capability without breaking the pre-1.0 interface contract; P2-2 and P2-4
are internal refactors guarded by the existing no-outing and preservation gates.
