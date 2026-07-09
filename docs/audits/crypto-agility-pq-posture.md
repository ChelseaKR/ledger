# Crypto-agility and post-quantum posture — the identity vault and sealed content (EXP-13)

## Status

**Policy and analysis doc — in effect on commit for the questions it answers today
(harvest-now-decrypt-later exposure, algorithm-lifecycle review cadence and
triggers); a design proposal, not yet implemented, for the questions it does not
answer alone (the versioned envelope, hybrid post-quantum encryption).** No
cryptographic code changes ship with this document. Implementing a versioned
envelope or any hybrid post-quantum construction on
`IdentityVault`/`src/ledger/identity.py` still requires a named external
cryptography reviewer's sign-off — this repeats, and does not relax, the gate
already stated by the companion in-flight remediation item that proposes the
envelope's exact byte layout (tracked separately; see [§6](#6-relationship-to-the-companion-envelope-proposal)).
Hybrid post-quantum encryption additionally cannot ship until the `cryptography`
package (ledger's one runtime dependency, `docs/adr/0005-stdlib-first-single-dependency.md`)
exposes a stable, non-experimental post-quantum KEM API — see [§4](#4-what-would-actually-unblock-hybrid-pq).

- **Scope.** The confidentiality primitives protecting the identity vault and
  absolute-`SEALED` content in `src/ledger/identity.py`: the Fernet-encrypted
  identity/content ciphertext, the `scrypt` passphrase-derived key, and every
  future algorithm choice layered on top of them.
- **Out of scope.** The exact envelope byte layout and key-hierarchy split
  (subkeys per asset class) — that is a companion item's proposal, referenced but
  not restated wholesale here. RM1's threshold/multi-party key custody — a
  distribution question, not an algorithm-lifecycle question, though [§4](#4-what-would-actually-unblock-hybrid-pq)
  flags where it intersects this doc.
- **Why now.** Identity confidentiality in ledger has a *decades* horizon by
  design: a contributor sealed today may need protection in 2050, and a seized or
  exfiltrated vault ciphertext does not expire. That is precisely the
  harvest-now-decrypt-later (HNDL) threat profile, and no committed ledger
  document analyzes it or states when algorithm choices get revisited. This doc
  is that analysis and that policy.

## 1. Harvest-now-decrypt-later: what it is and why identity ciphertext is exposed to it

HNDL is not a new attack on today's cryptography — it is a statement about
*when* an existing attack pays off. An adversary who can already capture
ciphertext (device seizure, a hostile replica host, network interception) does
not need to break it today. They can store it and wait for either a
cryptanalytic break or, specifically, a cryptographically relevant quantum
computer (CRQC) capable of running Shor's or Grover's algorithm at the key
sizes in use. The attack is retroactive: confidentiality promised *today* is
only as good as the weakest algorithm break available *at any point during the
data's protection horizon*, not at the point of capture.

Two of ledger's own threat-model cases already assume an adversary obtains
vault ciphertext and stops there only because they lack the key
(`docs/THREAT-MODEL.md` §4.1 device seizure, §4.2 subpoena/legal compulsion).
HNDL is the missing third leg of that same scenario: an adversary who seizes
ciphertext they cannot yet decrypt has no reason to discard it, and identity
confidentiality's decades-long horizon means "not yet" is not "safe."

## 2. What is actually at risk today: an honest inventory

This section is deliberately specific about ledger's *current* construction
rather than treating "quantum" as one undifferentiated risk, because the two
cryptographic primitives in `identity.py` have very different quantum exposure:

- **Fernet (identity and sealed-content ciphertext).** Fernet is
  AES-128-CBC for confidentiality plus HMAC-SHA256 for authentication — both
  **symmetric** primitives. Symmetric ciphers are not broken by Shor's
  algorithm (which targets the structured hardness of integer factorization
  and discrete log — RSA, Diffie-Hellman, elliptic-curve cryptography). They
  are weakened by Grover's algorithm, which gives a quadratic speedup on
  unstructured search: a large, fault-tolerant CRQC would reduce AES-128's
  effective security margin from 128 bits to roughly 64 bits, not to zero.
  64 bits is not comfortable for a decades-long confidentiality horizon, but
  it is a materially different risk than "fully broken the moment a CRQC
  exists," and it is fixable **without any post-quantum algorithm at all** —
  moving to a wider-margin symmetric construction (e.g. AES-256-GCM) closes
  most of this gap on its own. This is the concrete reason [§3](#3-the-versioned-envelope-what-it-must-be-able-to-express)
  requires the envelope to express algorithm choice independently of whether a
  PQ KEM is involved.
- **scrypt (passphrase-derived key).** A memory-hard KDF over a symmetric
  output. Not vulnerable to Shor's algorithm (there is no public-key structure
  to attack) and only quadratically weakened by Grover in the same sense as
  above. Not a priority for PQ migration.
- **No asymmetric/public-key primitive exists in `identity.py` today.**
  `IdentityVault` uses exactly one shared secret (the root Fernet key),
  supplied directly or derived from a passphrase — there is no RSA, no
  Diffie-Hellman, no elliptic-curve key exchange or signature anywhere in this
  module. This matters because Shor's algorithm — the quantum threat that
  actually *breaks* rather than *weakens* a primitive — has nothing to attack
  here yet. **Ledger's current HNDL exposure is lower than a system doing
  public-key key-establishment**, and the pitch's call for "hybrid encryption
  (current + PQ KEM)" is explicitly a *forward-looking* mitigation for a
  primitive ledger does not yet have, not an emergency retrofit of one it does.
- **Where that changes.** The moment any future work introduces a public-key
  primitive touching vault key material or content confidentiality — most
  plausibly RM1's threshold/multi-party key custody, or any scheme that lets a
  steward provision or recover a vault key without an existing shared secret
  channel — that primitive becomes fully exposed to Shor's algorithm, and
  everything encrypted under an ECC/RSA-established key from that point
  forward inherits full HNDL exposure at the moment of capture, not a
  Grover-reduced one. **This is the trigger this document tracks** ([§5](#5-review-cadence-and-triggers)):
  any RM1 design that introduces public-key key-establishment must be
  designed hybrid (classical + PQ KEM) from the start, not retrofitted later,
  because retrofitting cannot protect ciphertext already captured under the
  classical-only version.

**Net assessment:** ledger's present-day HNDL exposure is real but moderate
(a Grover-reduced symmetric margin, not a fully broken primitive), and its
future exposure is contingent on a specific, trackable trigger (any future
public-key primitive) rather than diffuse. That asymmetry is why the shape
below (agility now, hybrid PQ gated on both a concrete trigger and a stable
library primitive) is the right sequencing rather than adopting a PQ KEM
today for a construction that has no public-key component to protect.

## 3. The versioned envelope: what it must be able to express

Whatever byte layout is eventually implemented (a decision this document
defers to a named external cryptography reviewer, consistent with
[§6](#6-relationship-to-the-companion-envelope-proposal)), it must be able to
express, independently of each other:

1. **Envelope version** — so a reader can tell which format rules apply to a
   given token at all, replacing today's ambiguous `"enc:"` string prefix
   (`src/ledger/identity.py`'s `encrypt_text`/`decrypt_text`), which carries no
   version and can coincidentally prefix a legitimate plaintext field value.
2. **Key class** — which asset class's key produced this token (identity vs.
   sealed-field vs. sealed-payload), so rotation and exposure accounting can
   eventually be reasoned about per asset class rather than only in aggregate.
3. **Algorithm identifier, decoupled from key class.** This is the piece
   [§2](#2-what-is-actually-at-risk-today-an-honest-inventory) makes necessary:
   the envelope must let two tokens of the *same* key class carry *different*
   algorithms, so a symmetric-margin upgrade (Fernet/AES-128 to AES-256-GCM)
   and a future hybrid-PQ upgrade are both expressible as new algorithm IDs
   under the same envelope version, without a second migration mechanism for
   each. A provisional, non-binding registry for discussion:

   | `algorithm_id` | Construction | Status |
   | --- | --- | --- |
   | `0x01` | Fernet (AES-128-CBC + HMAC-SHA256) | current default; every token in the field today, unversioned |
   | `0x02` | AES-256-GCM (reserved) | candidate symmetric-margin upgrade; closes most of the Grover-reduced gap in §2 without PQ |
   | `0x10`–`0x1F` (reserved block) | Hybrid classical + PQ KEM combinations | not assignable until a specific KEM is chosen per §4; reserved now so a later allocation does not collide with `0x02`-class additions |

   These identifiers are a *policy placeholder* for the reviewer to bless,
   amend, or renumber — not a commitment to ship `0x02` or reserve exactly
   this range. What is committed is the *shape*: version, key class, and
   algorithm as three independently-varying fields, never conflated into one
   opaque byte or string prefix again.

## 4. What would actually unblock hybrid PQ

The pitch's shape is explicit that hybrid encryption (current symmetric
construction plus a PQ KEM) lands "when `cryptography` exposes stable
primitives" — this document takes that condition seriously rather than
treating PQ adoption as a date-driven deadline. Concretely, before any hybrid
PQ construction should be considered for implementation, **all** of the
following must hold:

1. **A finalized, standardized KEM exists to adopt.** NIST finalized its
   first post-quantum standards (FIPS 203 — ML-KEM, the KEM most relevant
   here; FIPS 204 — ML-DSA; FIPS 205 — SLH-DSA) in August 2024. That
   standardization removes the main "don't build on a moving target" objection
   in principle. Whoever revisits this document should re-verify current
   status, since standards and library support continue to evolve after this
   document's authoring.
2. **The `cryptography` package ships a stable (non-experimental,
   non-provisional) ML-KEM implementation** in a release within ledger's
   pinned dependency range (`pyproject.toml`). Adopting a second, PQ-specific
   library to get this sooner would violate ADR 0005's single-runtime-dependency
   ethos and is explicitly rejected as a shortcut — the whole point of that ADR
   is that ledger's one non-standard dependency is a single, heavily-audited
   library, not a discretionary set. If `cryptography` support lags materially
   behind the standard, the correct response is to wait and re-review on the
   cadence in [§5](#5-review-cadence-and-triggers), not to add a second
   dependency.
3. **A concrete trigger exists to protect**, per [§2](#2-what-is-actually-at-risk-today-an-honest-inventory):
   either RM1 (or any other work) is about to introduce a public-key primitive
   into the vault's key-establishment path, or the symmetric-margin case alone
   (AES-128 under Grover) is judged, at review time, to no longer be an
   acceptable margin for the multi-decade horizon. Absent either trigger,
   hybrid PQ has nothing to protect today that the algorithm-agility envelope
   in [§3](#3-the-versioned-envelope-what-it-must-be-able-to-express) cannot
   already accommodate by allocating a new symmetric algorithm ID.
4. **An external cryptography reviewer signs off** on the specific hybrid
   construction (KEM combiner choice, how classical and PQ shares are combined
   so the scheme is no weaker than the classical-only construction alone, and
   how this interacts with the envelope's key-class/algorithm fields) — the
   same non-negotiable gate this document restates from the companion envelope
   proposal, because a self-reviewed hybrid PQ construction is exactly the kind
   of "well-known way to introduce subtle, dangerous bugs" ADR 0005 already
   warns against for hand-rolled cryptography in general.

Until all four hold, the correct posture is: **agility now, PQ tracked, PQ not
implemented.** This is not a stalling position — it is what makes the *later*
adoption fast and low-risk, because the envelope will already be able to carry
a new algorithm ID the day condition 1–4 are all satisfied, rather than
requiring a second migration project layered on top of a first one.

## 5. Review cadence and triggers

This document is reviewed on ledger's existing "per release" security-review
cadence (`docs/RESPONSIBLE-TECH-AUDITS.md` §F, `docs/THREAT-MODEL.md`'s own
"Recheck cadence: per release" convention), plus early, out-of-cycle review
triggered by any of:

- NIST or another major standards body issuing a deprecation timeline for
  AES-128 or SHA-256 sooner than currently expected.
- A public cryptanalytic advance against AES, SHA-256, or Fernet's specific
  construction (not merely PQ-related).
- `cryptography` shipping a stable ML-KEM (or successor standard) API —
  triggers re-evaluation of [§4](#4-what-would-actually-unblock-hybrid-pq)'s
  condition 2.
- Any design (RM1's threshold custody or otherwise) proposing to introduce a
  public-key primitive into vault key-establishment — triggers mandatory
  hybrid-PQ design from the start of that work, per
  [§2](#2-what-is-actually-at-risk-today-an-honest-inventory)'s "designed
  hybrid from the start, not retrofitted later" requirement.
- The companion envelope proposal's external review record being filled in —
  triggers reconciling this document's provisional algorithm-ID registry
  ([§3](#3-the-versioned-envelope-what-it-must-be-able-to-express)) against
  whatever byte layout that review actually blesses.

Each review updates this document's status line and, where a trigger fired,
records what changed and why — the same "recheck cadence, stated honestly"
discipline `docs/THREAT-MODEL.md` already uses.

## 6. Relationship to the companion envelope proposal

A separate, in-flight remediation item proposes the concrete envelope byte
layout and a per-asset-class HKDF key hierarchy for this same vault (replacing
the single Fernet key and the `"enc:"` string prefix with a versioned,
structurally-distinct format, and splitting identity ciphertext from
sealed-content ciphertext under independently-derived subkeys). That proposal
and this document are complementary, not competing:

- The companion proposal owns **the exact byte layout and key-hierarchy
  mechanics** (HKDF domain separation, subkey derivation, the
  version/key-class byte pair).
- This document owns **the algorithm-lifecycle policy layered on top of that
  layout** (the third, algorithm-identifier field in [§3](#3-the-versioned-envelope-what-it-must-be-able-to-express);
  the HNDL analysis in [§§1–2](#1-harvest-now-decrypt-later-what-it-is-and-why-identity-ciphertext-is-exposed-to-it);
  the PQ-readiness gate in [§4](#4-what-would-actually-unblock-hybrid-pq); the
  review cadence in [§5](#5-review-cadence-and-triggers)).

Neither should be implemented without the other being accounted for: an
envelope shipped without an algorithm-identifier field would need a second,
disruptive migration the first time an algorithm (not just a key) needs to
rotate — precisely the gap this document exists to close. Both proposals
remain unimplemented pending the same external-cryptographer sign-off; neither
author self-reviews the other's half.

## 7. Open questions for the eventual external reviewer

In addition to the companion proposal's own open questions (envelope
soundness, HKDF construction details, scrypt cost parameters, Fernet's
all-or-nothing framing for large payloads):

1. Is the three-field (version, key class, algorithm) envelope shape in
   [§3](#3-the-versioned-envelope-what-it-must-be-able-to-express) sufficient,
   or does a real hybrid PQ construction need additional envelope fields (e.g.
   a KEM ciphertext length, since PQ KEM ciphertexts are typically much larger
   and non-uniform in size compared to a symmetric key wrap)?
2. Should the AES-256-GCM symmetric-margin upgrade (`algorithm_id 0x02`,
   provisional) ship on its own, ahead of any PQ work, as a low-risk
   Grover-margin improvement — or does bundling it with the larger envelope
   migration reduce total migration events for deployers at acceptable cost?
3. For a future hybrid construction, what combiner is appropriate (e.g. a KDF
   over the concatenation of classical and PQ shared secrets) so that the
   combined scheme is provably no weaker than the classical-only scheme alone,
   even if the PQ component is later found to be flawed?
4. Does RM1's threshold/multi-party custody design, once drafted, in fact
   introduce a public-key primitive as [§2](#2-what-is-actually-at-risk-today-an-honest-inventory)
   anticipates, or can it be built entirely from symmetric secret-sharing
   (e.g. Shamir over the existing root key) with no HNDL implications at all?
   This should be answered before RM1 design work starts, not after.

## References

- `docs/ideation/03-expansions.md` — EXP-13 (this document's source item).
- `docs/THREAT-MODEL.md` §1 (assets, decades-horizon identity confidentiality),
  §4.1 (device seizure), §4.2 (subpoena/legal compulsion) — the capture
  scenarios that make HNDL concrete rather than theoretical for this vault.
- `docs/adr/0005-stdlib-first-single-dependency.md` — the single-dependency
  ethos that rules out a second, pre-standard PQ library and requires waiting
  for `cryptography` itself to expose stable PQ primitives.
- `docs/RESPONSIBLE-TECH-AUDITS.md` §F (Security) — the per-release review
  cadence this document's own review cadence follows.
- `src/ledger/identity.py` — the code this document's algorithm-lifecycle
  policy will eventually apply to (`IdentityVault`, `encrypt_text`/
  `decrypt_text`/`encrypt_bytes`/`decrypt_bytes`, `derive_key`, `rekey`).
  `rekey()` already provides the atomic, all-or-nothing re-encryption
  operation ("a *when*, not an *if*") that any future algorithm migration —
  not just a key rotation — should reuse rather than duplicate.
