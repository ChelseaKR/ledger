# Cryptographic embargo design review — time-lock for `SEALED_UNTIL` (EXP-12)

## Status

**Research-first design exploration — not a proposal ready for implementation, and
not yet reviewed by an external cryptographer.** Per the EXP-12 ideation item's own
instruction ("research-first: a design doc exploring share-escrow among federated
instances vs. published timelock services"), this document's job is to lay the
design space out honestly, including the possibility that the answer is "not
safely buildable at ledger's scale today." Nothing here should be read as a
committed roadmap item. A build decision requires, at minimum: an external
cryptography SME sign-off (this item's "crypto SME gate absolute," matching
`docs/audits/crypto-design-review-sealing-layer.md`'s FIX-11 gate) *and* the
sociological judgment call in [§6](#6-the-sociological-question-this-is-not-only-cryptography)
made by real communities, not by this document.

- **Scope.** Whether and how `AccessPolicy.SEALED_UNTIL` (`src/ledger/models.py`)
  can be backed by a cryptographic time-lock — content that is *ciphertext* before
  its `unseal_at` date, not merely gated by a software check — so a seized disk
  yields nothing before the embargo date, the way absolute `SEALED` already yields
  nothing to anyone ever.
- **Out of scope.** RM1's threshold/split-knowledge *vault key* (protects the
  identity vault and `SEALED` content against a single compelled or compromised
  key-holder; a distribution question) and FIX-11's key-hierarchy/envelope
  proposal for the *existing* Fernet-based sealing layer (a construction
  question about content that is already encrypted). This document assumes both
  may eventually exist and asks whether a *third*, date-gated primitive should be
  built on top of or alongside them. It does not repeat their analysis.
- **Why now.** EXP-12 is tagged Speculative/XL in `docs/ideation/03-expansions.md`
  specifically because the honest first deliverable is this analysis, not code.
  Building the wrong cryptographic embargo — one that quietly fails to protect
  what it promises, or one whose failure mode is "the community's own history
  becomes permanently unrecoverable" — is worse than not building one, so the
  gate is deliberately upstream of any implementation.

## 1. What exists today (the thing being evaluated for replacement)

`SEALED_UNTIL` is ledger's temporal embargo: a field or record is withheld from
*every* viewer, including stewards, until `unseal_at` passes, then opens to
whatever tier it would otherwise be visible at
(`src/ledger/access/policy.py`, `can_view`, the `AccessPolicy.SEALED_UNTIL` case,
currently around lines 93–103). The enforcement is a pure function of
`(now, unseal_at)` — `_unseal_reached(now, unseal_at)` — checked at read time on
every access path (HTML reading room, JSON API, CSV export; see ADR 0007 for why
a denial is withheld rather than 403'd).

Critically, this is a **policy check on plaintext**, not an encryption boundary.
`ingest.py`'s at-rest encryption step (currently around lines 393–402) only
triggers `vault.encrypt_text` for fields whose policy is the *absolute*
`AccessPolicy.SEALED` — a `SEALED_UNTIL` field's value is written to the bag as
ordinary plaintext, exactly like a `PUBLIC` one. The embargo is entirely a
promise the software keeps by refusing to *render* the value before the date; it
is not a promise physics keeps. `docs/THREAT-MODEL.md` §4.1 (device seizure)
already states the general form of this gap for identities ("an attacker who
seizes both the vault file and the key reads every identity"); for `SEALED_UNTIL`
content the gap is starker still, because **no key is required at all** — a
seized disk, a compromised backup, or a hostile replica that ingested the bag
yields the embargoed value immediately, with zero decryption step, regardless of
whether the embargo date is ten days or ten years away. This is exactly the
asymmetry the EXP-12 ideation entry names: "today a seized disk yields
`SEALED_UNTIL` content immediately... an embargo is currently a promise the
software keeps, not one physics keeps."

Who this matters for: a contributor sealing testimony until a statute of
limitations passes, until named parties have died, until a movement's
principals are no longer at legal or physical risk, or simply until "later,
when it's safer to know" — the exact case `infra/seed_demo.py`'s oral-history
seed record and `tests/test_ingest_e2e.py::test_sealed_until_field_unseals_on_its_date`
exercise functionally, but not cryptographically.

## 2. What a cryptographic time-lock actually needs to guarantee

Precisely stated, so the two design branches in §3–§4 can be evaluated against
the same bar:

1. **Pre-date confidentiality against a fully offline, fully capable attacker.**
   Anyone holding the seized disk, *including every party who runs ledger
   software and every party who currently holds any ledger key*, must be unable
   to recover the plaintext before `unseal_at`, without access to some resource
   that is unavailable before that date.
2. **Post-date availability.** The plaintext must actually become recoverable
   after `unseal_at` — including in a world where the contributor has died, the
   instance has changed hands, or the original steward is gone. A time-lock
   whose failure mode is "the embargo silently becomes permanent" converts every
   protected memory into a loss, which `docs/CONTINUITY.md`'s whole framing
   treats as close to the worst outcome this project can produce for a community
   archive.
3. **No trusted party who can be compelled or bribed to open it early.** This is
   the entire point relative to today's software-only check — if there exists
   any single human or single-instance operator who *could* decrypt early if
   they chose to, EXP-12 has not actually improved on the status quo, only
   added complexity around the same trust boundary `docs/THREAT-MODEL.md` §4.2
   already documents for the vault key.
4. **The date itself must not be spoofable in either direction** — an attacker
   should not be able to force early release (defeats the embargo) or
   indefinitely block legitimate release after the date (defeats availability).

No known construction — including the two evaluated below — clears all four
simultaneously without a real trust or liveness assumption somewhere. The
purpose of §3–§4 is to say exactly where that assumption sits for each option so
a community can decide if it is one they can live with, rather than presenting
either as if it clears the bar for free.

## 3. Design branch A — threshold key-share escrow across federated instances ("social timelock")

**Mechanism.** At ingest, generate a random content key, encrypt the
`SEALED_UNTIL` value under it (an AEAD construction — see FIX-11's envelope
proposal for the shape this should reuse rather than reinvent), then split the
content key with Shamir's Secret Sharing (`k`-of-`n`) and distribute one share to
each of `n` independent, federated ledger instances (or trusted community
custodians) via EX2-style federation. Each share-holder is contractually/socially
bound to release its share only once `now >= unseal_at`, verified locally by
each holder's own clock/policy check before it transmits.

**What this actually buys.** No single instance operator — including the
instance that originated the record — can decrypt early alone; a seized disk
at the originating instance yields only ciphertext plus, at most, one share
(below the `k` threshold if `k` is chosen sensibly). This is a genuine
improvement over today's plaintext-on-disk status quo for the "one seized box"
case in §4.1 of the threat model.

**What it does not buy, and why the trust assumption just moved rather than
disappeared:**

- **It is not a time-lock; it is a threshold-access-control scheme with an
  honor-system date check.** Nothing cryptographic stops share-holder N from
  releasing its share to a colluding requester before `unseal_at` — the "only
  after the date" property is enforced by each holder's software and goodwill,
  which is the *same* category of guarantee `SEALED_UNTIL` already provides
  today, just replicated across `n` parties instead of one. §2's requirement 1
  ("unable to recover... without access to some resource unavailable before that
  date") is not met: the resource (a share) *is* available before the date, at
  every holder, to anyone who can persuade or compel `k` of them.
- **Compulsion resistance depends entirely on jurisdictional and organizational
  diversity of the `n` custodians**, which is a sociological property this
  document cannot design its way around (see §6). A federation of instances run
  by allied volunteer collectives in the same city, the same country, or under
  the same funder is not `k`-of-`n` independence against a legal-compulsion
  adversary (§4.2) — a single subpoena campaign or a single raid targeting the
  federation's organizing hub could plausibly reach `k` of them. `docs/GOVERNANCE.md`
  and `docs/CONTINUITY.md` describe ledger's actual deployment reality (small,
  volunteer-run, often geographically and socially clustered collectives) —
  precisely the profile least likely to have genuine `k`-of-`n` independence at
  the scale this scheme needs to matter.
- **Lost-shares risk compounds with `n` and with time.** Every additional
  custodian is an additional entity that can disband, lose its key material, get
  seized, or simply vanish (`docs/RESEARCH-ROADMAP.md`'s continuity evidence:
  the Queer Zine Archive Project, mutual-aid groups that routinely disband,
  volunteer-run archives generally). Shamir's scheme tolerates up to `n-k` losses
  gracefully, but an embargo with a ten-year horizon is a ten-year bet that fewer
  than `n-k+1` custodians disappear *and* that the surviving `k` can still be
  located and contacted when the date arrives. A failed reassembly does not fail
  safe toward "still embargoed" — it fails toward **permanent loss of the
  content**, which per §2.2 is close to the worst outcome available.
- **New network and coordination attack surface.** Share transport and holder
  authentication are themselves security-critical (a spoofed "release your
  share" request, a compromised custodian instance, a Sybil-federated set of
  instances that look independent but are not) — none of which exists in
  today's single-instance, no-network-dependency model, and all of which
  contradicts ADR 0005's stdlib-first, minimal-dependency, single-box ethos
  unless designed very carefully.
- **Depends on RM1's threshold-vault-key machinery and EX2's federation
  discovery existing and being trustworthy first** — this option is explicitly
  gated on both, per the ideation entry, and neither exists yet.

## 4. Design branch B — published/verifiable timelock services

**Mechanism.** Encrypt the `SEALED_UNTIL` value to a *future* round of a public
verifiable randomness/timelock beacon — for example the drand League of
Entropy's `tlock` construction, which uses identity-based encryption against a
BLS signature that the beacon network will not (by construction, assuming honest
threshold participation in the beacon itself) produce until the target round's
wall-clock time arrives. Anyone can encrypt to a future round without
coordination; decryption becomes possible for anyone, the moment the beacon
publishes that round's signature — no share-holder needs to be located or asked.

**What this actually buys over branch A:**

- **No custodian-liveness dependency for release.** Once a round is published
  by the beacon network (a large, ongoing, externally operated threshold
  network, not a set of ledger-affiliated custodians ledger would have to
  recruit and keep alive), *anyone* holding the ciphertext can decrypt, solving
  §3's lost-shares problem — the embargoed content does not require the
  originating community to still exist or be reachable at the target date.
- **Decentralization is real and pre-existing** rather than something ledger
  must bootstrap. drand's League of Entropy already spans multiple independent
  organizational operators; ledger does not need `n` community collectives to
  each run infrastructure indefinitely.

**What it does not buy, and why this is not a free win either:**

- **A new, non-stdlib, externally-operated dependency directly contradicts ADR
  0005** ("stdlib-first, single-dependency ethos," cited explicitly by EXP-13 as
  a constraint PQ migration must respect — the same constraint applies here with
  equal force). `tlock`/drand client libraries are not in the Python standard
  library, and the beacon itself is a live third-party network dependency: if
  the League of Entropy changes terms, forks, or a specific round's threshold
  participation degrades, ledger's embargo guarantee inherits that risk
  wholesale, for content a community trusted to a *decades*-horizon promise.
  This is a governance-availability risk ledger does not currently accept
  anywhere else in its design.
- **It moves the trust boundary from "the ledger federation's `k`-of-`n`" to
  "the beacon network's own threshold honesty,"** which is a real improvement in
  *size and independence* of the anonymity/trust set (dozens of large,
  diverse, professionally-run operators vs. a handful of allied volunteer
  collectives) but is not a *different kind* of guarantee — §2's requirement 3
  ("no trusted party who can be compelled") still ultimately rests on the
  beacon's own operators not colluding or being compelled *as a bloc*, which
  ledger has no visibility into and no ability to audit or influence.
- **Round-time selection is coarse and public.** Encrypting to "round
  corresponding to 2030-01-01" is itself a signal (an outside observer holding
  only the ciphertext learns the approximate unseal date even before it arrives,
  since round-to-time mapping is public) — a smaller but real information leak
  relative to a purely software-checked `unseal_at`, which today's `SEALED_UNTIL`
  keeps entirely server-side, undisclosed to anyone without at least a steward
  or list grant.
- **No revocation or early-legitimate-access path.** `docs/GOVERNANCE.md`'s
  objection/dispute process and any future "named subject requests earlier
  disclosure with consent" flow (RM12-adjacent) have no hook into a beacon-gated
  ciphertext — once encrypted to a future round, *nothing* short of the beacon
  reaching that round unlocks it, including a case where the contributor
  themselves later wants to unseal early. Today's software check can be
  overridden by an explicit, logged steward action (with the honesty costs ADR
  0007 already discusses); a true time-lock structurally cannot be, by design —
  which is the point, but also removes a consent-respecting escape hatch this
  project treats as important elsewhere (contributor autonomy is a named value
  throughout `docs/GOVERNANCE.md` and the identity vault's own docstring).

## 5. Branches considered and set aside without full write-ups

- **Trusted hardware / TEEs (e.g., a hardware security module with a real-time
  clock that refuses to release a key early).** Rejected from further analysis
  here: requires a specific, procured hardware dependency per deployment,
  contradicts the "run on one cheap box for a broke collective" minimal-computing
  constraint (`docs/RESEARCH-ROADMAP.md`), and trusted-hardware attestation has
  its own well-documented supply-chain and side-channel history that would need
  its own SME gate on top of the cryptography SME gate this document already
  requires.
- **Pure verifiable delay functions (VDFs) run by the instance itself, no
  network.** Rejected: a VDF proves that *sequential* computation time elapsed,
  not that wall-clock time elapsed relative to a real calendar date, and running
  one continuously for a decade-scale embargo on "one cheap box" is not a
  realistic operational story; also has no maturity comparable to drand-style
  beacon timelocks for this exact use case yet.
- **A simple dead-man's-switch / delayed-publication escrow with a single named
  institutional partner** (e.g., a library or archive that agrees to hold and
  publish a decryption key on a date). Not analyzed in depth as a *cryptographic*
  primitive because it is not one — it is a single trusted third party, which is
  a strictly weaker guarantee than today's steward-withholds-until-date model in
  the compulsion case (§4.2), since it adds a party without removing the
  original one. It is noted here only because it is the kind of "good enough,
  legible, no novel cryptography" fallback a community might reasonably prefer
  over either branch A or B, and is worth naming honestly as an alternative
  rather than omitting it because it is unglamorous.

## 6. The sociological question this is not only cryptography

Both real branches (§3, §4) ultimately convert "do you trust ledger's software
and your steward" into "do you trust an *additional*, larger, less legible set
of parties (a federation of allied collectives, or a public beacon network) to
behave honestly for as long as ten, twenty, or fifty years." Per the ideation
item's own framing, that is a judgment only a real community holding real
embargoed history can make, weighing:

- Whether their community's actual threat model (§4.1/§4.2 of the threat model)
  is dominated by "a single seized box" (branch A helps) or "compelled software
  behavior at the one instance that already holds the plaintext" (both
  branches help, but see below) or something a cryptographic time-lock cannot
  touch at all — e.g., a court order served on the *contributor* or the
  *subject* directly, which no client-side encryption scheme changes.
- Whether **irrecoverable loss** (a lost share quorum, a beacon operator
  changing terms, an unglamorous forgotten passphrase) is a risk they would
  rather accept than the status-quo risk of early disclosure. `docs/CONTINUITY.md`
  already treats "the archive disappears" as close to the worst case; a
  cryptographic embargo that trades "readable a bit too early under duress" for
  "unreadable forever" is not obviously the better failure mode for a community
  archive's actual mission, even though it is the *more cryptographically
  impressive* answer.
- Whether the added operational complexity (running federation share
  infrastructure, or taking a live dependency on a specific external beacon
  network) is sustainable for the single-maintainer, volunteer-run reality
  `docs/CONTINUITY.md` §1/§3 already names as ledger's central risk — adding a
  bus-factor-fragile *new* subsystem to protect against a bus-factor-fragile
  *existing* one is not free.

## 7. Recommendation

**Neither branch is ready to build, and this document's honest conclusion is
that EXP-12 should stay in the ideation/research backlog, not graduate to an
implementation item, until three preconditions are met — not because the
cryptography is unsound in the abstract, but because none of the
prerequisites this document depends on exist yet:**

1. **FIX-11's key-hierarchy/envelope review lands and is reviewed by an actual
   external cryptographer** (`docs/audits/crypto-design-review-sealing-layer.md`,
   currently open as PR #51, not yet merged or reviewed) — both branches above
   assume a sound content-encryption primitive to build on top of, and today's
   is explicitly under review, not settled.
2. **RM1's threshold/split-knowledge vault key ships and is exercised in
   production** (`docs/RESEARCH-ROADMAP.md` RM1, P0, not started) — branch A is
   architecturally the same machinery (Shamir threshold secret sharing) applied
   to a different secret; building it twice, independently, before the first
   instance is battle-tested is unnecessary risk and duplicated SME review cost.
3. **A specific community with real embargoed content is willing to be the
   design partner** for the §6 sociological trade-offs, so the choice between
   branch A, branch B, the dead-man's-switch fallback (§5), or "no cryptographic
   time-lock, keep hardening the software-only check" is made by people who bear
   the consequences, not decided in the abstract by this document.

If and when those three preconditions hold, this document's recommendation for
*which branch to prototype first* is **branch A (federated threshold
share-escrow)**, not branch B, specifically because it does not add a live
third-party network dependency that ADR 0005 exists to avoid, and because it
reuses RM1's threshold machinery rather than introducing an unrelated
externally-operated system into a project whose whole design philosophy is
minimal, auditable, single-box operation. Branch B (published timelock
services) should be kept as a documented alternative in this file, not pursued,
unless branch A's federation-independence assumption (§3) turns out to be
unachievable for real community deployments (too few genuinely independent
custodians in practice) — in which case branch B's larger, pre-existing
trust set may be the lesser evil, and this document's Review Record (§9)
should be revisited before restarting Design branch B work rather than treating
this recommendation as permanent.

**This "not yet, and here is exactly what would need to be true first" answer is
itself the deliverable EXP-12 asks for** — the ideation entry names "an honest
published analysis even if the conclusion is 'not safely buildable at our scale'"
as excellent output in its own right, and that is what this section states.

## 8. Open questions for the external reviewer(s)

For the **cryptography SME** (gate absolute, per EXP-12; this section should be
handed to them once FIX-11's review is scheduled, so both can potentially be
reviewed together):

1. Does §2's four-requirement bar correctly capture what "cryptographic
   time-lock" needs to mean for this threat model, or is a weaker/differently
   shaped guarantee more honest to promise (e.g., "resistant to a single-box
   seizure" without claiming resistance to compelled-federation collusion)?
2. Is Shamir's Secret Sharing (as opposed to a more specialized threshold
   time-lock construction, e.g. timed-release crypto per Rivest/Shamir/Wagner
   or a proactive/refreshable secret-sharing scheme that re-splits periodically
   to bound the compulsion window) the right primitive for branch A, and does a
   `k`-of-`n` threshold need to be per-record, per-community, or globally
   configured?
3. For branch B, is `tlock`/drand's IBE-over-pairing construction, and its
   round-to-time mapping, mature and audited enough for a decades-horizon
   confidentiality promise, and does its published-round-time leak (§4) matter
   for ledger's specific no-outing threat model?
4. Is there a hybrid worth designing — e.g., branch A's share-holders each
   independently *also* gate their share release behind a branch-B beacon
   round, so early release requires both compelling `k` custodians *and* the
   beacon publishing early — and is the added complexity worth the marginal
   guarantee?

For a **community design partner / sociologist** (per §6, this is not a
cryptography-only decision):

5. For a specific real community's actual custodian set, is genuine `k`-of-`n`
   jurisdictional/organizational independence achievable, or does every
   plausible federation partner share enough of the same legal, geographic, or
   funding exposure that branch A's headline guarantee would be theater?
6. Is "irrecoverable after the date if we fail" an acceptable trade against
   "recoverable early under duress," for embargoed content this specific
   community holds — and does the answer differ by content type (a diary entry
   naming a living person vs. an institutional wrongdoing record with a
   statute-of-limitations horizon)?

## 9. Review record

*(To be filled in only after an actual, named external cryptographer — and,
per §6, a community design partner — has reviewed this document. Nothing below
this line is true yet.)*

- **Cryptography reviewer:** _(name, affiliation, vetting — not yet
  commissioned; blocked on FIX-11's own review per §7)_
- **Community design partner:** _(not yet identified)_
- **Date reviewed:** _(not yet scheduled)_
- **Verdict:** _(prototype branch A / prototype branch B / prototype hybrid /
  do not build, harden software-only check instead — not yet decided)_
- **Preconditions from §7 satisfied:** _(none yet — FIX-11 unreviewed, RM1 not
  started, no design partner identified)_

## References

- `docs/ideation/03-expansions.md` — EXP-12 (this document's source item),
  EXP-13 (crypto-agility/PQ, cites the same ADR 0005 constraint).
- `docs/ideation/02-large-scale-fixes.md` — FIX-11 (crypto design review this
  document is gated on) and RM1 (threshold vault key, referenced throughout as
  `docs/RESEARCH-ROADMAP.md` RM1).
- `docs/audits/crypto-design-review-sealing-layer.md` — the FIX-11 design doc;
  this document deliberately reuses its envelope/status conventions and does
  not re-derive its own encryption primitive.
- `src/ledger/access/policy.py` — `can_view`'s `SEALED_UNTIL` case, the software
  check this document evaluates replacing or supplementing.
- `src/ledger/ingest.py` — the at-rest encryption step that today only applies
  to absolute `SEALED`, not `SEALED_UNTIL`.
- `src/ledger/models.py` — `AccessPolicy` enum.
- `docs/THREAT-MODEL.md` §4.1 (device seizure), §4.2 (subpoena/legal
  compulsion) — the adversaries this document's §2 requirements are stated
  against.
- `docs/CONTINUITY.md`, `docs/GOVERNANCE.md` — the volunteer-run, single/small
  -maintainer operational reality that shapes §6 and §7's recommendation.
- `docs/adr/0005-stdlib-first-single-dependency.md` — the dependency-minimalism
  constraint branch B is weighed against.
- `docs/adr/0007-withhold-not-403.md` — the existing pattern for how a denial is
  communicated, relevant to branch B's "no revocation path" trade-off in §4.
- [drand / League of Entropy](https://drand.love/) and
  [`tlock`](https://github.com/drand/tlock) — the published-timelock
  construction evaluated in branch B.
- Rivest, Shamir, Wagner, ["Time-lock puzzles and timed-release crypto"](https://people.csail.mit.edu/rivest/pubs/RSW96.pdf) (1996) —
  the foundational timed-release cryptography literature underlying both
  branches' vocabulary.
