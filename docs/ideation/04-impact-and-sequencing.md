# Impact × effort and sequencing — 2026-07-01

Covers FIX-01…FIX-12 ([`02-large-scale-fixes.md`](02-large-scale-fixes.md)) and
EXP-01…EXP-15 ([`03-expansions.md`](03-expansions.md)). Impact is judged against
this repo's own hierarchy: contributor safety > consent integrity > preservation
integrity > adoption/reach > polish. Effort tiers are from the item sheets. These
are planning judgments, not measurements.

## Impact × effort matrix

| | **S / M effort** | **L / XL effort** |
|---|---|---|
| **Critical impact** (safety or integrity guarantee currently at risk) | FIX-01(a tactical form, M) · FIX-02 · FIX-05 · FIX-08 | FIX-01(b full revisioning) · FIX-11 (+SME gate) |
| **High impact** (core promise made true / major capability) | FIX-03 · FIX-06 · FIX-07 · EXP-02 · EXP-03 | FIX-04* · FIX-09 · EXP-05 · EXP-06 · EXP-11 · EXP-15 |
| **Medium impact** (durability of trust, reach) | FIX-10 · FIX-12 · EXP-01 · EXP-04 · EXP-07 · EXP-08 · EXP-09 | EXP-13 · EXP-14 |
| **Speculative / research value** | EXP-10 (code S; value gated on counsel) | EXP-12 |

\* FIX-04 straddles M/L; placed by its L-shaped interaction with FIX-01.

Reading the matrix: the top-left cell is unusual — four items where a *current
guarantee is quietly broken or unenforced* are fixable at S–M effort. That cell is
the whole argument for the "Now" tranche.

## Dependency notes

- **FIX-01 is upstream of the most things:** FIX-04 (index keys on revisions),
  FIX-06 (chain lives in `premis.json`), FIX-08 (tombstones interact with
  revisions), EXP-05 (don't freeze a public API around a bag layout about to
  change). Do it first or accept rework.
- **FIX-11 (crypto design review) gates** FIX-03's chunked sealing, EXP-06, EXP-12,
  EXP-13, EXP-15 — and shapes RM2 from the existing roadmap. Cheap to *prepare*
  (the design doc), external to *close*.
- **EXP-01 and FIX-06 are one system** (chained logs → publishable attestations);
  EXP-10 and EXP-15 reuse EXP-01's attestation format.
- **FIX-07 completes EX1:** succession events become attestable conditions that can
  unseal what was waiting for them.
- **FIX-10 creates the audits directory** that EXP-03's readiness reports and
  FIX-11's review artifact land in.
- **Existing-roadmap interactions:** FIX-02 complements RM1 (content-side vs.
  identity-side compulsion surface); FIX-12 complements RM11 (automated depth vs.
  manual cadence); EXP-11 builds on RM5; EXP-06/EXP-15 build on RM2's direction.

## Suggested sequence (beyond the existing RM/EX horizons)

The RESEARCH-ROADMAP's Horizon 1 (RM1, RM9, EX6 + shipped RM4/EX1) stands. This
sequence slots the net-new work around it without displacing those commitments.

**Now (next 2–4 weeks of effort):**
1. FIX-01 tactical form — stop the tag-manifest drift; add update-then-audit tests.
2. FIX-05 — locking on consent/review/proposal stores (a lost withdrawal is the
   worst small bug this repo can have).
3. FIX-02 — authenticated grant tokens.
4. FIX-10 — truthfulness gate + README corrections (cheap, on-brand, immediate).
5. FIX-11 prep — write the key-hierarchy/envelope design doc so the external review
   (with RM9's audit) has something concrete to bless.

**Next (the following quarter):**
6. FIX-01 full AIP revisioning, then FIX-06 chained logs, then EXP-01 attestations
   — one coherent integrity arc.
7. FIX-03 streaming media (plaintext paths; sealed chunking waits on FIX-11).
8. FIX-04 index + FIX-09 server decomposition (they touch the same request paths;
   sequence FIX-09's golden tests first).
9. FIX-07 condition attestation + EXP-04 multi-party consent — the consent-depth
   pair, alongside governance-text updates.
10. EXP-03 readiness wizard, feeding EX6's installer work from the existing roadmap.
11. FIX-12 browser-real a11y CI.

**Later (after the above and the RM-roadmap's Horizon 2):**
12. EXP-05 preservation-core extraction (post-FIX-01 API stability).
13. EXP-02 lockdown mode; EXP-08 sneakernet/print editions; EXP-07 redaction
    assistant — the field-conditions cluster, each with its real-user gate.
14. EXP-09 session kit; EXP-11 METS/EAD bridge (with a real partner).
15. H3 bets in research order: EXP-13 agility doc → EXP-14 enclave design →
    EXP-15 replica exchange → EXP-12 cryptographic embargo. Each begins as a
    published design/feasibility analysis, not code.

## Items requiring human / legal / SME / real-data gates

Per the portfolio ethos: these do **not** ship on internal judgment alone. Defer
and report the deferral honestly in-repo.

| Item | Gate | Why |
|---|---|---|
| FIX-11 | External cryptography review | Key reuse across asset classes; envelope design. Self-review is not evidence. |
| FIX-03 (sealed chunking only) | Same crypto review | Chunked AEAD is easy to get subtly wrong. |
| EXP-06 | Crypto SME + real at-risk contributor input | Client-side crypto moves the trust boundary; the UX *is* the security. |
| EXP-12, EXP-13, EXP-15 | Crypto SME; EXP-15 also a real-community pilot | Threshold escrow, PQ migration, cross-instance custody. |
| EXP-14 | Privacy SME (aggregation/differencing) + community governance opt-in | Aggregate leakage is a research-grade problem. |
| EXP-10 | Legal counsel, per jurisdiction | Canary wording has legal effect; unreviewed text is worse than none. |
| EXP-02 | Real-user tabletop with stewards facing actual risk | A duress feature that confuses people under duress is harm. |
| EXP-04, EXP-09 | Community/governance review of consent language | Consent wording is not an engineering artifact. |
| EXP-07 | Real-user gate on framing; honest recall reporting | Overtrust in a detector endangers exactly whom it should protect. |
| EXP-11 | Real partner institution + practicing archivist | Conformance to a spec ≠ ingestibility by a real system. |
| FIX-12 / RM11 | Real assistive-technology users (existing roadmap already says so) | Automated axe is a floor, not a verdict. |

Standing caveat, inherited from `USER-RESEARCH.md`: every priority here is derived
from documentation and code reading plus synthetic research. Real discovery with
real at-risk contributors precedes any safety-surface change. Nothing in this
folder overrides that.
