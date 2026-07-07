# Threat model

Last verified: 2026-07-05 · Recheck cadence: per release

This document is written for hostile contexts. ledger holds records for people for
whom exposure can be dangerous — someone not out, someone undocumented, someone who
organizes where any of that is criminalized. For those people, a leak is not an
embarrassment; it is a risk to safety, liberty, or life. So the threat model here
treats the contributor's safety as a first-class preservation requirement, equal to
bit-integrity, and it is honest about where the guarantees stop.

The structure of each adversary case below is deliberate and the same every time:

- **the guarantee** ledger makes,
- **the mechanism** that makes the guarantee real in the code, and
- **the residual risk** — stated plainly, including the cases ledger cannot defend.

A threat model that promised everything would be lying. The honest parts are the
point.

---

## 1. Assets — what is worth protecting

| Asset | What it is | Why it matters |
| --- | --- | --- |
| **Records** | The archived material itself: oral histories, zines, protest ephemera, runbooks — the bytes and the descriptive/access manifest around them. | The thing the archive exists to keep. Loss is permanent; corruption is silent betrayal. |
| **Contributor identities** | The real name, contact, pronouns, and notes of the person who contributed a record. | The single most dangerous datum in the system. Linking a record to a person can out, endanger, or expose them. |
| **Consent state** | Each record's and field's access policy, plus takedown and consent-change history. | Encodes what the contributor agreed to disclose. Ignoring it is the same harm as never having asked. |
| **Fixity** | The dual-algorithm checksums (`manifest-sha256.txt`, `manifest-blake2b.txt`) and the PREMIS chain of custody. | The evidence that a record is what it claims to be and has not been tampered with. Without it, every other guarantee is unverifiable. |
| **Grants and the vault key** | The mapping of viewers to capabilities, and the key that decrypts the identity vault. | Compromise of either collapses the disclosure boundary. The vault key is the crown jewel. |

These are ranked roughly by harm-on-compromise. A lost record is a tragedy; an outed
contributor can be a catastrophe. The design weights the second accordingly.

---

## 2. Trust boundaries

ledger draws hard lines between things most tools keep together. Each line is a place
where a property is enforced rather than assumed.

1. **Record ↔ identity.** The single most important boundary. A `Record`
   (`src/ledger/models.py`) carries *no* identity — at most an opaque, random
   `identity_ref`. The mapping from that ref to a real `ContributorIdentity` lives
   only inside the encrypted vault (`src/ledger/identity.py`). The preservation copy
   and the safety boundary are structurally distinct: you can hold every bag in the
   archive and learn nothing about who made any of them.

2. **Stored record ↔ disclosed record.** A read path may never emit a `Record`. It
   emits a `DisclosedRecord` (`src/ledger/models.py`), which has no `identity_ref`
   field at all, constructed solely by `ledger.access.disclose`
   (`src/ledger/access/policy.py`). The disclosure decision happens in exactly one
   function, `is_visible`, so there is one place to audit and one place that denies by
   default.

3. **Steward ↔ identity-unsealer.** Stewardship (`is_steward`) and the ability to
   resolve an identity (`identity_unseal`) are independent fields on a `Grant`
   (`src/ledger/access/grants.py`). A steward can administer the whole archive,
   including viewing sealed *content*, without holding a single `identity_unseal`
   token. Seeing a record is not the same as seeing who contributed it.

4. **Vault key ↔ everything else.** The Fernet key never lands in config, on a command
   line, in a log, or in an exception message. It travels by environment variable
   (`LEDGER_VAULT_KEY`) or a keystore and is held only to encrypt and decrypt
   (`src/ledger/ingest.py`, `src/ledger/identity.py`).

5. **Authoritative copy ↔ replica.** A replica host receives whole BagIt bags and is
   never trusted to be honest: bags are re-validated on arrival and a divergent copy is
   quarantined, never blessed (`src/ledger/replicate.py`).

6. **Local box ↔ network.** The browse server binds to `127.0.0.1` by default
   (`src/ledger/server.py`), so a freshly stood-up archive is reachable only locally
   until an operator deliberately exposes it behind a vetted reverse proxy.

---

## 3. The no-outing guarantee (a requirement the code must meet)

> **No-outing requirement.** No view, JSON response, export, filename, log line,
> metric label, error message, timing difference, or inference from what is *not*
> shown may reveal who contributed a record to any viewer who does not hold an
> explicit `identity_unseal` grant for that record's `identity_ref`.

This is stated as a *requirement on the implementation*, not an aspiration. It is the
top-priority class of security bug (see `SECURITY.md`), and any path that violates it
is a defect to be fixed at the highest priority, not a tuning knob.

The requirement is enforced in layers — structural, then defense-in-depth, then tested:

- **Structural.** Identity is not a field on `Record` or `DisclosedRecord`. There is no
  attribute to leak. `disclose` cannot copy identity into the output because the source
  type does not carry it.
- **Single chokepoint.** Every read path (`browse`, `search`, `/record/`,
  `/api/records`, `/api/record/`, export) flows through `disclose`. The server has no
  code path that builds a record view from anything but a `DisclosedRecord`.
- **Redacted representations.** `ContributorIdentity.__repr__` and `IdentityVault`
  string forms are redacted, so an accidental `print`/`log` of either cannot out anyone
  (`src/ledger/identity.py`).
- **Scrubbed surfaces.** The access log emits method + status + path-only, with the
  query string stripped and the `X-Ledger-Grant` header never logged. The `/healthz`
  endpoint reports counts only — no bag path, file name, digest, record id, or identity
  (`src/ledger/server.py`). Error messages name the *object and condition*, never the
  protected value (`src/ledger/errors.py`).
- **Defense in depth at ingest.** Before an AIP is returned, `ingest_sip` re-scans every
  clear-text artifact — `bag-info.txt`, the record manifest, the Dublin Core sidecar,
  the PREMIS log — for any non-empty value of the contributor's name, contact, pronouns,
  or notes, and fails loudly on a hit (`_assert_identity_free`,
  `src/ledger/ingest.py`). Dublin Core's `creator`/`contributor` describe the
  *collection*, never the person.

### The test of record

The requirement is asserted, not trusted, by **`tests/test_no_outing.py`**. That suite
injects *sentinel* identities at ingest — unique, searchable marker strings standing in
for a real name and contact — and then asserts those sentinels are **absent** from every
public surface: the browse HTML, the search results, the single-record view (before and
after proceeding past a content warning), the JSON API, every exported artifact, the
bag and its tag files, the PREMIS log, the request log, the health endpoint, and every
error page. A sentinel that surfaces anywhere is a failing test and a release blocker.
The sentinels are synthetic — never a real contributor's data — exactly so a test for a
leak can never itself become a leak (see the redaction-safe reporting rule in
`SECURITY.md`).

This suite is the executable form of the requirement. When a reviewer asks "does ledger
actually meet the no-outing guarantee?", the answer is `tests/test_no_outing.py`, green
in CI.

---

## 4. Adversaries and cases

Each case names a concrete adversary, the guarantee, the mechanism, and the residual
risk. The residual-risk lines are the most important part of this document.

### 4.1 Device seizure

*Adversary: someone who physically takes the disk, laptop, or drive holding the archive
— at a border, in a raid, after a theft.*

- **Guarantee.** Possession of the stored archive does not reveal any contributor's
  identity. The seized bags and record manifests are identity-free.
- **Mechanism.** Vault separation. Records carry only opaque `identity_ref` tokens
  generated from a CSPRNG (`secrets.token_urlsafe`, `src/ledger/identity.py`), with no
  structural relationship to the contents they point at. The mapping to real people
  exists only as authenticated (Fernet) ciphertext inside the vault file, encrypted
  under a key that is *not on the disk* if it is supplied by environment variable or
  keystore. A seized bag is a preservation copy and nothing more.
- **Residual risk.** **An attacker who seizes both the vault file *and* the key reads
  every identity.** If the deployment stores the key on the same disk (e.g. an
  unencrypted env file next to the vault), seizure is total compromise of identities.
  The vault key is the crown jewel; ledger cannot protect identities against an attacker
  who holds both halves. Mitigations are operational, not code: keep the key off the box
  (a passphrase entered at runtime via scrypt-derived key, an external keystore, or a
  hardware token), and use full-disk encryption so the vault ciphertext is not even
  reachable cold. Note also that `identity_ref` tokens themselves are visible in seized
  records; they are useless without the key, but they do confirm *that* a record has a
  sealed contributor.

### 4.2 Subpoena / legal compulsion

*Adversary: a court order, warrant, or other legal demand for records and contributor
information directed at a steward or a host.*

- **Guarantee.** ledger does not create a single party who can be compelled to produce
  what they do not technically hold. A steward, by default, cannot decrypt any identity;
  there is nothing for the steward to hand over even under compulsion.
- **Mechanism.** Deny-by-default disclosure plus the steward/unsealer split. A steward
  grant carries `is_steward=True` but an *empty* `identity_unseal` set
  (`src/ledger/access/grants.py`). Resolving an identity requires a grant that names the
  specific `identity_ref`, and the vault checks the grant before any lookup
  (`IdentityVault.resolve`). The community can choose to provision *no standing
  identity-unseal grant to anyone*, so the capability to out a contributor need not
  exist in any single person's hands.
- **Residual risk.** **Legal compulsion can reach whatever a person can actually do.**
  If any individual holds the vault key and an `identity_unseal` grant, a court can
  compel that individual to use them; the cryptography does not resist a lawful order
  against a key-holder. ledger reduces the attack surface to whoever holds those
  capabilities, but it cannot make a compelled key-holder refuse. The strongest posture
  — split-knowledge or threshold control of the vault key, so no one person can decrypt
  alone — is **not implemented**; today the key is a single secret. Compulsion can also
  reach the *content* the archive holds, which is a separate question governed by
  `docs/GOVERNANCE.md` and the takedown/consent machinery; this section concerns
  identity specifically. Stewards facing a demand should consult counsel; this document
  is a description of the software, not legal advice.

### 4.3 Doxxing

*Adversary: someone trying to assemble a public identification of a contributor by
probing the archive's public surfaces, combining fields, or cross-referencing.*

- **Guarantee.** No public surface attributes a record to a person, and no public field
  is published that the contributor did not consent to publish.
- **Mechanism.** No identity in any surface, plus per-field selective disclosure. The
  record view, browse, search, and JSON API all render only a `DisclosedRecord`, which
  cannot carry identity. A contributor can publish a story while sealing the names, the
  location, and their own identity, because every `Field` and `PayloadFile` carries its
  own policy and `disclose` includes only those `is_visible` to the viewer
  (`src/ledger/access/policy.py`). Withheld parts are named generically in a "Withheld"
  note (the field *name*, never its value), so the partial view is honest without
  leaking.
- **Residual risk.** **ledger cannot police the content of the fields a contributor
  chooses to publish.** If a published `story` field itself contains a name, a location,
  or an unmistakable detail, the system will render it faithfully — that is consent, not
  a leak. Redaction (`src/ledger/access/redaction.py`) is offered as a first-class,
  guided step precisely to help here, but the judgment is human. Correlation across
  *multiple* published records (same writing style, same rare event, same small
  community) can also re-identify a contributor; this is a limit of any archive that
  publishes anything, and ledger does not claim to defeat a determined human analyst
  working from published content alone.

### 4.4 A malicious or compromised steward

*Adversary: a steward who turns hostile, or whose account/credentials are stolen.*

- **Guarantee.** Ordinary stewardship is never an outing risk. A steward can administer
  the archive and view sealed *content* for moderation, but cannot resolve a
  contributor's identity, and cannot act on records invisibly.
- **Mechanism.** The steward/unsealer split (a steward grant holds no `identity_unseal`
  tokens), least-privilege grant construction (`build_grant` requires every capability
  to be named explicitly — privilege never accrues by omission), and an append-only,
  attributed moderation log (`ModerationLog`, `src/ledger/moderate.py`). Every
  consequential action — warn, takedown, consent-change, appeal — is recorded with the
  acting steward, a required non-empty reason, and the target record id, and the log can
  be appended to but not silently rewritten. Preservation actions are PREMIS events with
  agent and outcome (`src/ledger/metadata/premis.py`), so a steward's actions are
  attributable after the fact.
- **Residual risk.** **A steward who *also* holds the vault key and an `identity_unseal`
  grant can out the contributors whose refs that grant names.** This is exactly the
  capability the design tries to keep no one from holding by default, but if a community
  provisions it, that steward (or whoever compromises them) inherits it. Furthermore, a
  steward with filesystem access to the bags can read all *content* (sealed fields
  included) outside the application's read paths entirely — the application-layer
  disclosure gate does not constrain someone with raw disk access. The append-only log
  is append-only *as enforced by the application*; an attacker with raw write access to
  the log file on disk can tamper with it, which is why fixity and off-box replicas of
  the log matter. Governance (`docs/GOVERNANCE.md`) is the control for a malicious
  steward: removal, multi-steward review of high-stakes actions, and not concentrating
  the vault key and unseal grants in one person.

### 4.5 A hostile replica host

*Adversary: a mirror operator (a member's drive, an off-site host) who is careless,
compromised, or actively malicious — corrupting, truncating, or substituting bags.*

- **Guarantee.** A bad or tampered replica can never silently become the truth, and a
  hostile replica cannot quietly degrade the archive's integrity.
- **Mechanism.** Dual-manifest fixity plus verify-on-arrival and quarantine-and-heal.
  Every bag carries two independent manifests, `manifest-sha256.txt` and
  `manifest-blake2b.txt` (`src/ledger/bag.py`), so a single weakened or backdoored
  algorithm cannot mask tampering — an independent digest must also agree. On
  replication a bag is re-validated *at the destination*; a copy that arrives torn or
  drifted is moved into a sibling `quarantine/` directory and a labelled `QUARANTINE`
  PREMIS event is raised, never hidden (`src/ledger/replicate.py`). Healing only ever
  copies *from* a replica that just passed full RFC 8493 validation, and `heal` refuses
  to act when no replica validates — there is nothing trustworthy to copy from, so a
  divergent copy can never propagate.
- **Residual risk.** **A hostile replica host has whatever it received.** Replication
  ships whole bags; a mirror holds the identity-free record manifests and payloads it
  was given, so anything published or community-level on those bags is readable by the
  host (the bags are not themselves encrypted at rest — that is the operator's
  responsibility). The fixity guarantee is about *integrity and recovery*, not
  *confidentiality at the mirror*: a community should mirror sealed content only to hosts
  it is willing to trust with that content, or encrypt the bag at the storage layer. A
  host can also simply *delete* or refuse to serve its copy (an availability attack); the
  defense is redundancy (N independent locations), not preventing one host from dropping
  out. Identity is *not* exposed by mirroring, because the vault is not replicated with
  the bags.

### 4.6 Network surveillance

*Adversary: someone observing traffic between viewers and the archive, or between
replicas.*

- **Guarantee.** The application does not, by its own behavior, leak identity or sealed
  content onto the wire, and it does not bind itself to the world by default.
- **Mechanism.** No identity in any surface (so even an observer who reads a full
  response sees no identity), a deny-by-default grant header that confers nothing on its
  own (`X-Ledger-Grant` is only ever a lookup key into a pre-provisioned grants file,
  `src/ledger/server.py`), loopback-only binding by default, and a `no-referrer`
  referrer policy plus a strict `Content-Security-Policy` so the browser does not leak
  navigation context or load third-party resources.
- **Residual risk.** **Transport encryption is the operator's responsibility, not
  ledger's.** The stdlib server speaks plain HTTP; a real deployment must sit behind a
  TLS-terminating reverse proxy. Without it, a network observer reads everything a
  legitimate viewer would see at that grant level — which is never identity, but is the
  published/community content in clear text. Even with TLS, **traffic analysis remains**:
  an observer can see *that* a viewer fetched `/record/{id}`, the size and timing of
  responses, and patterns of access. ledger does not implement padding, mixing, or
  access-pattern hiding; it does not defend against an adversary who infers interest from
  metadata. Replication transport security (e.g. moving bags over an encrypted channel)
  is likewise an operational concern outside the application.

### 4.7 Inference attacks — what is *not* shown leaking information

*Adversary: someone who learns from absence — from the shape of a listing, a status
code, a count, or the existence of a locked row.*

This is the subtle one, and it is treated as a first-class leak in `SECURITY.md`
("inference from what is *not* shown").

- **Guarantee.** The absence of a record, a field, or a contributor leaks nothing about
  whether something sealed exists. ledger does not publish padded lists with locked rows,
  does not distinguish "not found" from "not permitted," and does not reveal whether a
  record has a sealed contributor through any public surface.
- **Mechanism.** Deny-by-default listability and uniform negative responses. A record
  whose default policy is sealed is simply *not listed* to an ungranted viewer —
  `is_listable` resolves through the same `is_visible` decision, and `browse` skips
  non-listable records silently rather than emitting a locked placeholder
  (`src/ledger/access/policy.py`, `src/ledger/ingest.py`). On the server, both "the
  record does not exist" and "you may not list this record" render the *same* neutral
  404 (`_handle_record`, `src/ledger/server.py`), so a probe cannot distinguish a sealed
  record from a nonexistent one. The "Withheld" note names the *field name* of something
  redacted but never its value, and only on records the viewer may already list. Counts
  on `/healthz` are aggregate (bags audited/passed/failed, files checked) with no
  per-record detail.
- **Residual risk.** **Some inference channels are inherent or out of scope.** A field's
  *name* appearing in the "Withheld" list does tell a permitted viewer that a field by
  that name exists and is sealed — this is a deliberate honesty/inference tradeoff
  (the partial view is labelled as partial), not an accidental leak, but it is
  information. **Timing side-channels are not fully closed:** the grant check in
  `IdentityVault.resolve` runs before any lookup so identity-unseal does not leak ref
  existence by timing, and the access decision in `is_visible` is a pure function of its
  arguments, but ledger does not make all read paths constant-time, and a determined
  attacker measuring response latency across many requests might infer the presence or
  size of sealed payloads. **Total counts can leak aggregate facts** — e.g. the number
  of records that exist at all — even when individual records are sealed; `/healthz`
  exposes archive-wide bag counts by design (it is an operational health endpoint).
  Finally, **the existence of an `identity_ref` on a seized or mirrored record manifest**
  confirms that *some* contributor is sealed behind that record, though not who; this is
  visible to anyone holding the bag (see §4.1, §4.5).

---

## 5. Out of scope (stated honestly)

ledger does not claim to defend against:

- **An attacker holding both the vault file and its key.** That is total identity
  compromise; the key must be protected operationally (off-box, encrypted disk, threshold
  control where the community can arrange it).
- **Raw filesystem access to the bags by a hostile party.** The disclosure gate is an
  *application-layer* control. Someone with the disk reads content directly; protect the
  disk (full-disk encryption, access control) and replicate the audit log off-box.
- **Compulsion of a person who genuinely holds a capability.** Cryptography does not make
  a key-holder refuse a lawful order. The defense is to not concentrate the capability.
- **A contributor publishing identifying content.** Consent to publish is consent;
  redaction is offered, but the judgment is human.
- **Traffic analysis and timing inference by a sophisticated network observer.** TLS is
  required operationally; access-pattern and timing hiding are not implemented.
- **Re-identification by correlation across published records or external data.** No
  archive that publishes content can fully prevent a determined analyst.
- **Supply-chain compromise of dependencies.** Mitigated, not eliminated, by pinned and
  hashed dependencies, pip-audit, CodeQL, and gitleaks in CI (see `CONTRIBUTING.md`).

The line ledger holds firmly is the one it can hold in code: **holding or operating the
archive does not out a contributor, and a corrupt copy never silently becomes the
truth.** Everything past that line is named here so a community can decide, with open
eyes, how to operate.

---

## 6. Summary table

| Adversary | Guarantee | Primary mechanism | Sharpest residual risk |
| --- | --- | --- | --- |
| Device seizure (§4.1) | Stored archive reveals no identity | Vault separation; key off-box | Attacker with vault file **and** key reads all identities |
| Subpoena / compulsion (§4.2) | No single party holds what they cannot technically produce | Deny-by-default; steward ≠ unsealer | A compelled holder of key + unseal grant can be forced to use them |
| Doxxing (§4.3) | No surface attributes a record to a person | No identity in any surface; per-field disclosure | Content a contributor chose to publish; cross-record correlation |
| Malicious/compromised steward (§4.4) | Stewardship is not an outing risk | Steward/unsealer split; append-only attributed log | A steward who also holds key + unseal grant + raw disk access |
| Hostile replica host (§4.5) | Bad copy never silently becomes truth | Dual-manifest fixity; verify-on-arrival; quarantine-and-heal | Host reads/deletes the content it was given; bags not encrypted at rest by ledger |
| Network surveillance (§4.6) | App leaks no identity/sealed content by behavior; loopback by default | No identity in surface; deny-by-default header; CSP/referrer | TLS is operator's job; traffic analysis and timing remain |
| Inference / what is not shown (§4.7) | Absence leaks nothing about sealed existence | Deny-by-default listability; uniform 404; aggregate-only health | Withheld field names; non-constant-time paths; aggregate counts |

The no-outing guarantee (§3) is the requirement that ties the table together, and
`tests/test_no_outing.py` is its enforcement.
