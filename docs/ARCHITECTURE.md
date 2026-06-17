# ledger — Architecture

This document describes how ledger is built and, more importantly, *why* it is built
that way. The central design claim is simple to state and hard to honour: a community
can keep rigorous, durable preservation of its records and, at the same time, a
structural guarantee that holding a record can never out the person who contributed
it. Most preservation tooling treats contributor safety as policy bolted onto a
storage system. ledger treats it as a property of the type system and the module
boundaries, so a leak is a build error or a failed audit rather than an oversight.

Read the [README](../README.md) first for voice, the hard rules, and the full
quality-attribute argument. This document is the layered design and the data flow.

---

## 1. Layered design

ledger is a small set of layers, each depending only on the ones beneath it. The
dependency direction is deliberate: the lower a layer sits, the less it knows about
disclosure, and the *contract* layer at the bottom knows nothing about behaviour at
all. Identity is not a layer in this stack — it is a sealed sidecar that the ingest
layer writes to and the disclosure layer (transitively) reads from, never inline with
a record. That separation is the subject of section 3.

```
                          cli.py        server.py            (entry surfaces)
                            │               │
              ┌─────────────┼───────────────┼──────────────┐
              │             ▼               ▼              │
          ingest.py / oais.py        moderate.py           (workflows + OAIS packaging)
              │   (Archive facade, SIP→AIP, to_dip)        │
              ├───────────────┬──────────────┬─────────────┤
              ▼               ▼              ▼             ▼
        access/ (disclose)  replicate.py   metadata/    identity.py
        the ONE read        verify+heal    premis/dc    encrypted vault
        decision point                                  (grant-gated)
              │               │              │
              └───────┬───────┴──────┬───────┘
                      ▼              ▼
                bag.py (RFC 8493)   cas.py + fixity.py
                BagIt packaging     content store + checksums
                      │              │
                      └──────┬───────┘
                             ▼
                config.py             models.py / errors.py
                config-as-data        the shared contract (no behaviour)
```

### 1.1 The contract: `models.py` and `errors.py`

`ledger.models` is deliberately behaviour-free. It defines the value objects every
other layer agrees on — `Record`, `Field`, `PayloadFile`, `DisclosedRecord`, `Grant`,
`ContentAddress`, `FixityResult`, `DublinCore`, `PremisEvent`, and the enums
`AccessPolicy`, `HashAlgo`, `PremisEventType` — so that ingest, storage, disclosure,
identity, replication, and the browse server each depend on one stable shape rather
than on each other's internals (this is what makes the layers swappable).

The single most important invariant in the whole system lives here, in the type
system:

- `Record` carries at most an opaque `identity_ref` — a random token — and *never* a
  contributor name. The module docstring states the rule and forbids adding a
  `contributor_name` field.
- `DisclosedRecord` is the only shape a read path may emit, and it has **no
  `identity_ref` field at all**. It is `frozen=True`, so a read path structurally
  cannot carry identity forward; there is nowhere to put it.

`AccessPolicy` is the small documented vocabulary (`PUBLIC`, `COMMUNITY`, `STEWARDS`,
`SEALED_UNTIL`, `SEALED_CONDITIONAL`). New records and fields default to
`SEALED_UNTIL` — the narrowest level that still lets a thing exist (a seal with no
unseal date is sealed indefinitely). Determinism helpers (`now_iso`, `parse_iso`,
`canonical_json`) live here too, so any layer that must be reproducible (golden bags,
audit records) sorts keys and stamps an injected time rather than reaching for the
wall clock.

`ledger.errors` is one exception hierarchy under `LedgerError`. Two rules hold across
it and are part of the threat model: no error message ever discloses an identity or a
sealed value (messages name the *object* — a content address, a record id, a bag path
— and the *condition*), and failures are surfaced, never swallowed. `FixityError`,
`QuarantineError`, `BagValidationError`, `ReplicationError`, `AccessDenied`,
`IdentityVaultError`, `ConsentError`, and `ModerationError` give each surface a family
to catch and a guaranteed-safe message.

### 1.2 Preservation core: `cas.py`, `fixity.py`, `bag.py`

These three modules are the integrity floor. They know nothing about access policy or
identity; they hash bytes, store bytes, and package bytes.

**`fixity.py`** is the primitive layer: `hash_bytes`, `hash_file`, and
`hash_file_multi` (one disk read feeds every requested hasher), plus `verify_file`,
`audit_files`, and the `AuditReport` aggregate. It supports SHA-256 *and* BLAKE2b on
purpose — a single weakened or backdoored algorithm cannot mask tampering when an
independent digest must agree. Hashing streams in 1 MiB windows so a multi-gigabyte
oral-history video is verified without being held in RAM. Nothing here ever reads or
emits file *contents* — only hex digests, paths, and pass/fail outcomes.

**`cas.py`** (`ContentStore`) names objects by the hash of their content under a fixed
`address_algo` (SHA-256 by default), so an object's name *is* its fixity check
(`ContentStore.verify` re-hashes the bytes and compares them to the address they are
filed under). `put_bytes`/`put_file` are idempotent — identical bytes map to one
address and are stored once (dedupe). Writes are atomic: a temp file in the same
directory followed by `os.replace`, so a reader never sees a half-written object and
an interrupted write leaves only an orphan temp file. A two-level hex shard
(`objects/<algo>/<aa>/<bb>/<digest>`) keeps any one directory small. `ObjectNotFound`
names only the address.

**`bag.py`** writes and validates BagIt bags per IETF RFC 8493 — the archive's
hand-off and replication unit. `write_bag` emits the payload under `data/`, a
`manifest-<algo>.txt` and `tagmanifest-<algo>.txt` for each of SHA-256 and BLAKE2b,
`bagit.txt`, and `bag-info.txt`. Emission is deterministic: manifest lines are sorted
by path with a fixed two-space separator and newlines are not platform-translated, so
the same payload yields byte-identical manifests on any machine (this is what makes
golden bags and cross-machine fixity comparison possible). `validate_bag` rejects a
missing `bagit.txt`, a missing manifest, a manifest entry absent on disk, **and a
payload file present on disk but absent from the manifest** (undeclared bytes are as
suspicious as missing ones), then returns a per-file `AuditReport`. The module
docstring carries the load-bearing warning: `bag-info.txt` travels in the clear and
must never carry an identity, contact, or sealed value.

### 1.3 Descriptive and preservation metadata: `metadata/`

`metadata/premis.py` keeps an append-only `PremisLog` of `PremisEvent`s and
serializes it two ways: canonical JSON (the archive's durable, byte-stable form) and a
minimal valid PREMIS v3 XML document (`to_premis_xml`) for exchange with other
preservation systems. The log only ever grows — `record` appends, `events` returns a
copy — so an object's history is a faithful, replayable account of every ingestion,
fixity check, replication, redaction, policy change, consent change, and takedown.
Writes are atomic. A `PremisEvent` carries an agent, an outcome, a detail, and an
opaque `linked_object`, never an identity or a sealed value.

`metadata/dublincore.py` serializes a `DublinCore` (the fifteen ISO 15836 elements)
to and from the canonical JSON sidecar and exports the standard `oai_dc:dc` XML for
OAI-PMH harvesters. The no-outing rule is explicit here too: `dc.creator` and
`dc.contributor` describe the *collection or community*, never the individual who
contributed an item.

The versioned record schema lives at `metadata/schema/record.schema.json`.

### 1.4 Access: the single disclosure decision point — `access/`

This package is ledger's safety heart, and it is intentionally tiny so the safety
boundary is small enough to audit in full. Everything a read path needs is re-exported
from `access/__init__.py`: `disclose`, `is_visible`, `is_listable`, `redact_field`,
`redact_payload`, and the grant builders.

- **`access/policy.py` — `is_visible(...)`** is the *one* function that answers "may
  this grant see this policy at this instant?" It is a pure function of
  `(policy, grant, now, unseal info)` — no clock, no randomness — so the same inputs
  always yield the same answer (predictability/determinability). It denies by default:
  any case not explicitly permitted returns `False`, and an expired grant is
  downgraded to `PUBLIC_GRANT` before deciding. There is exactly one place to audit
  and exactly one place that defaults to deny.

- **`disclose(...)`** is the *sole* constructor of `DisclosedRecord` used by read
  paths. It refuses to even list a record the grant may not see (raising
  `AccessDenied` naming only the record id, so a viewer cannot learn the record
  exists), then includes only fields and payloads whose own policy `is_visible`,
  records every withheld name in `redactions` so the lossy view is honest about being
  lossy, always surfaces `content_warnings`, and passes Dublin Core through without
  ever injecting identity. Because its return type has no `identity_ref`, the
  no-outing boundary is enforced *structurally* — there is no way to leak identity out
  of `disclose` because there is nowhere in the result to put it.

- **`is_listable(...)`** resolves a record's *default* policy through `is_visible`, so
  a record whose existence is sealed never appears in a listing — no padded list with
  locked rows betraying that something is there.

- **`access/grants.py`** builds grants under least privilege. The crucial separation:
  `identity_unseal` (the set of `identity_ref` tokens a grant may resolve to a real
  person) is independent of `is_steward`. `steward()` can see every disclosure level
  for administration but holds **no** `identity_unseal` tokens — stewardship is not an
  outing risk. `load_grants` rebuilds file-provisioned grants through the same
  `build_grant` path, so there is one construction path and no way to accrue privilege
  by omission.

- **`access/redaction.py`** makes redaction a first-class, recorded *transform*:
  `redact_field`/`redact_payload` return a lossy copy plus a PREMIS `REDACTION` event
  whose detail names only the field/filename, never its withheld value. The original
  stays access-controlled wherever the caller keeps it; the lossy view never
  masquerades as the original.

### 1.5 Identity vault: `identity.py`

`IdentityVault` is the structural guarantee behind the no-outing promise. It maps an
opaque `ref` to a `ContributorIdentity` (name, contact, pronouns, notes), stored
**only** here, encrypted with authenticated symmetric encryption (Fernet). Design
choices, each tied to a property:

- A separate, encrypted, grant-gated vault keeps identity out of every record and read
  path (safety, confidentiality). The mapping exists nowhere else.
- Refs come from `secrets.token_urlsafe` and are independent of the identity, so a
  record leaks no identifying signal even if its ref is observed (unlinkability).
- `resolve(ref, grant)` checks `ref in grant.identity_unseal` **before** any lookup,
  so the decision does not depend on whether the ref exists, and raises `AccessDenied`
  otherwise (least privilege).
- Fernet authentication detects tampering on read (integrity); `revoke` deletes a
  mapping so consent revocation and takedown are honoured at the storage layer.
- `__repr__`/`__str__` of both the identity and the vault are redacted, and no
  identity, ciphertext, or key is ever logged or placed in an exception message.

Keys are supplied as bytes (`generate_key`, or `derive_key` via scrypt from a
passphrase + salt) and travel via the `LEDGER_VAULT_KEY` environment variable so they
never land in config or on a command line.

### 1.6 OAIS packaging: `oais.py`

`oais.py` names the three OAIS (ISO 14721) information packages as distinct typed
objects so each step is traceable to a standard stage:

- **`SIP`** (Submission) — a `Record`, its raw `payload`, and *optionally* a
  `ContributorIdentity`. This is the **only** package permitted to carry an identity,
  and only in transit.
- **`AIP`** (Archival) — exactly what is stored: the `Bag` plus the on-disk paths of
  the record manifest, the Dublin Core sidecar, and the PREMIS log. An `AIP` has **no
  identity field by construction**.
- **`to_dip(...)`** (Dissemination) builds the safe read shape as a deliberately thin
  wrapper over `access.disclose` — naming the OAIS stage while routing every
  dissemination through the one audited boundary.

### 1.7 Ingest: `ingest.py` and the `Archive` facade

`ingest_sip(...)` is the *one* ingest path. Every item is processed in the same fixed
order, so an item can never be stored un-hashed, un-bagged, or un-documented:

1. **Fixity + store.** Each payload is hashed under both algorithms and `put_file`'d
   into the `ContentStore`; a `PayloadFile` is built carrying the content address,
   size, media type, and the file's intended policy (taken from the record if declared
   there, else `default_policy`). One `FIXITY_CHECK` PREMIS event per payload.
2. **Seal identity.** If the SIP carries an identity and a vault exists, the identity
   is `vault.add`'d and the returned opaque `identity_ref` is set on the record. The
   identity goes nowhere else.
3. **Bag.** A RFC 8493 bag is written. `bag-info.txt` names the *collection* as
   `Source-Organization`, never a person, plus the injected `Bagging-Date` and the
   record id as `External-Identifier`.
4. **Document.** The record manifest (`record.json`), Dublin Core sidecar
   (`dublincore.json`), and PREMIS log (`premis.json`, one `INGESTION` plus the
   per-payload `FIXITY_CHECK` events) are written as tag files *inside* the bag, so
   their integrity is covered by the bag's own tag manifest.

Defense in depth: `serialize_record` refuses to emit a record that still carries an
in-memory `ContributorIdentity`, and before returning, `ingest_sip` re-scans
`bag-info.txt`, the record manifest, the Dublin Core sidecar, and the PREMIS log via
`_assert_identity_free` for any identity value and raises loudly on a hit (the
exception names only *where*, never the value). All timestamps come from the injected
`now`, so a golden ingest is byte-reproducible.

`Archive` is the task-shaped facade over every subsystem — `init`, `ingest`, `get`,
`disclose`, `browse`, `resolve_identity`, `audit_fixity` — so a steward, the CLI, or
the server never wires the content store, vault, bagger, and access layer together by
hand. The vault is opened lazily (only when identity is genuinely in play). Reads go
only through `disclose`/`browse`; `browse` skips non-listable records silently so the
absence of a row leaks nothing.

### 1.8 Replication: `replicate.py`

`replicate_bag` copies a whole bag into a `StorageLocation` and **re-validates it at
the destination** (verify-on-arrival), so a transfer that arrived torn is caught at
write time. A copy that fails validation is moved to a sibling `quarantine/` directory
(kept, not deleted, for inspection) and a `QUARANTINE` PREMIS event with
`outcome="failure"` is attached to the `ReplicationError` that is then raised — the
failure is surfaced and auditable, never hidden. `verify_replicas` reports one
`ReplicaStatus` per location and degrades a missing or unreadable replica to
`ok=False` rather than raising, so one offline mirror cannot blind a steward to the
health of the others. `heal` rebuilds every failing or missing replica **only from a
replica that just passed full validation**, re-verifies the healed copy on arrival,
and refuses to act at all when no replica validates — a divergent copy can never
propagate. This module moves and validates opaque bag *directories*; it places only
bag names, location names, and paths into events and errors.

### 1.9 Moderation: `moderate.py`

`ModerationLog` is an append-only log of `ModerationAction`s. Every consequential
decision is justified (a non-empty `reason`, enforced at construction *and* on
record), attributed (a steward `actor`), and contestable (an `appeal` links via
`appeal_of` to the action it challenges). `add_content_warning`, `change_consent`,
`takedown`, and `appeal` each return the relevant PREMIS event so the decision and its
preservation record stay in lockstep. Content warnings are *structured metadata* on
the record, surfaced before the material renders. As everywhere, `actor` is a steward
id, `reason` describes the decision, and `target_record` is an opaque record id —
never an identity or sealed value.

### 1.10 Server: `server.py`

The accessible, framework-free browse/search site (standard-library `http.server`
only, so a community runs the whole public face on one inexpensive box with no
framework, build step, or paid service). Two qualities dominate every line:
accessibility and the no-outing rule.

Every record-bearing response is built from a `DisclosedRecord` produced by
`Archive.browse`/`Archive.disclose`, i.e. by `access.disclose`. There is **no** code
path that constructs a record view from anything but a `DisclosedRecord`, which
structurally cannot carry identity. Reinforcing guards: the access log is overridden
to emit method + status + a query-stripped path only (never a grant subject or a
search term); every interpolated string passes through one `_esc` HTML-escape
boundary (no XSS); the static handler resolves and bounds every path under
`web/static` (no path traversal); responses carry a strict `Content-Security-Policy`,
`nosniff`, and `no-referrer`. Grant resolution is deny-by-default — a request is
anonymous unless `X-Ledger-Grant` names a *pre-provisioned* subject in the grants
file, and the header confers nothing on its own. `/record/{id}` renders a textual
content-warning interstitial before the content, and a missing record and a
not-permitted record render the *same* neutral 404, so the response never reveals
whether a sealed record exists. `/healthz` reports counts only (bags audited, passed,
failed, files checked) — no path, digest, record id, or identity. The site binds to
`127.0.0.1` by default.

### 1.11 CLI: `cli.py` and `config.py`

`cli.py` is the one discoverable steward surface: `init`, `ingest`, `browse`, `show`,
`serve`, `audit`, `policy`, `takedown`, `replicas`, `add-location`, `demo`, `acr`.
Exit codes are meaningful (`audit` returns non-zero on any failing bag so cron/CI can
branch). It is held to the no-outing rule: a contributor name/contact is accepted only
as ingest *input*, sealed into the vault, and the CLI then prints *only* the opaque
`identity_ref` — never echoing the name. Every time-stamping command accepts `--now`
for reproducibility.

`config.py` is configuration-as-data: one versioned, declarative `Config` (archive
name, store root, vault path, replica `locations`, default policy, content-warning
vocabulary, languages) with a `schema_version` and a `_migrate` shim that upgrades
older files in memory and *refuses* a file from a newer ledger rather than misreading
it. `Config.default` produces secure single-box defaults — store and vault under one
root, `default_policy = SEALED_UNTIL`, one `local` location — and `save` writes
atomically. A config describes *where* the vault lives, never *what* is in it.

---

## 2. Data flow

### 2.1 Ingest: SIP → fixity → CAS → BagIt AIP → PREMIS/DC

```
   contributor input (CLI ingest / Archive.ingest)
   payload files + Record + optional ContributorIdentity
                          │
                          ▼
                ┌───────────────────┐
                │       SIP          │   oais.SIP — the ONLY package that may
                │ record + payload   │   carry an identity, and only in transit
                │ + identity?        │
                └─────────┬─────────┘
                          │  ingest.ingest_sip(...)  [one fixed path]
                          ▼
        ┌─────────────────────────────────────────────┐
   (1)  │ FIXITY  hash_file_multi(sha256, blake2b)     │  fixity.py
        │ STORE   ContentStore.put_file -> address     │  cas.py (dedupe, atomic)
        │ build PayloadFile(address, size, policy)     │  -> 1 FIXITY_CHECK event/payload
        └─────────────────────┬───────────────────────┘
                              │
   (2)  identity present? ──► vault.add(identity) ──► identity_ref ──► record
        │                     identity.py (Fernet, encrypted)    (record carries ONLY the ref)
        │                     identity goes NOWHERE else
        ▼
   (3)  BAG  bag.write_bag(...)  RFC 8493            bag.py
        data/<files>
        manifest-sha256.txt   manifest-blake2b.txt
        tagmanifest-sha256.txt tagmanifest-blake2b.txt
        bagit.txt   bag-info.txt  (Source-Organization = collection, never a person)
                              │
   (4)  DOCUMENT (tag files inside the bag, covered by the tag manifest)
        record.json      <- serialize_record (REFUSES in-memory identity)
        dublincore.json  <- metadata.dublincore.write_sidecar   (collection-level DC)
        premis.json      <- metadata.premis.PremisLog            (INGESTION + FIXITY_CHECK)
                              │
        ┌─────────────────────────────────────────────┐
        │ DEFENSE IN DEPTH: _assert_identity_free over │
        │ bag-info.txt, record.json, dublincore.json,  │  raise loudly on any hit
        │ premis.json                                  │  (message names only WHERE)
        └─────────────────────┬───────────────────────┘
                              ▼
                ┌───────────────────┐
                │       AIP          │   oais.AIP — what is stored on disk;
                │ bag + paths to     │   NO identity field by construction
                │ record/dc/premis   │
                └───────────────────┘
                              │  (a copy of record.json is mirrored under records/
                              ▼   for fast reads without unpacking the bag)
                  later: replicate_bag -> verify-on-arrival -> quarantine/heal
```

### 2.2 Read: request → grant → access.disclose → DIP

```
   viewer request
   (server GET /record/{id}   or   CLI show --id --as   or   /api/record/{id})
                          │
                          ▼
   GRANT RESOLUTION  (deny by default)
   server:  X-Ledger-Grant header -> load_grants lookup -> known subject? else anonymous()
   cli:     --as -> anonymous / steward / community_member
                          │  Grant{ levels, is_steward, identity_unseal, expires_at }
                          ▼
   LOAD  Archive.get(record_id) -> identity-free Record (records/ copy, else in-bag record.json)
                          │
                          ▼
   ┌──────────────────────────────────────────────────────────────┐
   │ access.disclose(record, grant, now)   THE SINGLE DECISION PT   │
   │   is_listable? ── no ──► raise AccessDenied (names only the id) │
   │        │ yes                                                    │
   │        ▼                                                        │
   │   for each field/payload: is_visible(policy, grant, now, …)?    │
   │        include if visible; else add name to `redactions`        │
   │   always include title + content_warnings                       │
   │   pass DC through; NEVER inject identity                        │
   └───────────────────────────┬──────────────────────────────────┘
                              ▼
                ┌───────────────────────────────┐
                │        DisclosedRecord (DIP)   │  frozen; HAS NO identity_ref field —
                │  only what the grant may see;  │  the no-outing boundary is structural
                │  redactions named for honesty  │  (oais.to_dip is a thin wrapper)
                └───────────────┬───────────────┘
                              ▼
   RENDER   server: _record_main_html (CW interstitial first; withheld stated in text;
            same neutral 404 for "not found" and "not permitted")
            cli:    show -> disclosed.to_dict() as JSON

   (Identity is reachable ONLY by a separate path: Archive.resolve_identity ->
    vault.resolve(ref, grant), gated by grant.identity_unseal. It NEVER flows
    through disclose, browse, the server, or any DisclosedRecord.)
```

---

## 3. Why preservation and identity are structurally separate

This is the design decision the rest of the system is arranged around, so it is worth
stating plainly *why* it is structural rather than procedural.

**The threat model demands it.** ledger serves communities where the same record that
preserves a life can also expose someone who is not out, who is undocumented, or who
organizes where any of that is dangerous. The realistic adversaries are not only bit
rot but seizure, subpoena, a hostile fork, and a malicious steward. A safety guarantee
that depends on every contributor of every read path *remembering* not to print a name
is not a guarantee; under those adversaries it will eventually fail. So the guarantee
is moved into places where forgetting is impossible:

1. **The contract makes identity unrepresentable on a read path.** `Record` holds only
   an opaque `identity_ref`; `DisclosedRecord` — the sole shape any read path emits —
   has *no identity field at all* and is frozen. `disclose` is the only constructor of
   it used by reads. A developer cannot leak identity through the browse, search, API,
   or export surface because the output type has nowhere to carry it. The leak is a
   type error, not a code review miss.

2. **The mapping lives in exactly one place, encrypted, behind a separate
   capability.** The `ref → person` mapping exists nowhere but the encrypted
   `IdentityVault`, and `resolve` is gated by `grant.identity_unseal`, which is
   independent of `is_steward`. Holding the entire preservation copy — every bag, every
   manifest, every replica — gives an adversary the records and *not* the identities,
   because the identities are not in the bags. Seizing the storage does not seize the
   people. Even a steward who can administer everything cannot resolve an identity
   without a token specifically granted for that ref.

3. **Refs carry no signal, so the two halves cannot be re-joined by inference.** Refs
   are random and independent of their identity, so observing a record's ref reveals
   nothing about who it points to (unlinkability). The preservation copy and the safety
   boundary are not merely stored apart; they are uncorrelated.

4. **The split lets the two halves keep their *own* invariants without compromise.**
   Preservation wants bit-exact, deterministic, replicable, openly readable artifacts —
   a plain BagIt bag anyone can validate decades from now without ledger. Safety wants
   minimal disclosure, encryption, and revocability. Those goals conflict if mixed: you
   cannot have a bag that is both openly readable by any RFC 8493 tool *and* safe to
   hand to an adversary if it contains a name. By keeping identity out of the bag
   entirely, the bag can be maximally open *and* maximally safe at the same time — the
   open package is exactly the safe package.

5. **Defense in depth catches the case where a layer above the contract slips.** Even
   though identity is supposed to flow only into the vault, `ingest_sip` re-scans every
   clear-text artifact (`bag-info.txt`, record manifest, Dublin Core sidecar, PREMIS
   log) for any identity value before returning the AIP, and `serialize_record` refuses
   to emit a record that still carries an in-memory identity. The structural guarantee
   is the wall; these scans are the alarm on the wall.

The same logic explains why **disclosure is one decision point** (`access.is_visible`
behind `access.disclose`) rather than a check sprinkled across pages: a safety property
is only as strong as its *weakest* enforcement site, so the design collapses the number
of enforcement sites to one and defaults that one to deny.

---

## 4. Quality attributes → where realized

Fifteen headline attributes from the README, each tied to the concrete module and
function that realizes it. This is a sample; the README works through the full list.

| Quality attribute            | Where realized in the code |
|------------------------------|----------------------------|
| **Safety** (no-outing)       | `models.DisclosedRecord` (no identity field); `identity.IdentityVault.resolve` (grant-gated); `ingest._assert_identity_free` (defense-in-depth rescan) |
| **Confidentiality**          | `access.policy.is_visible` (deny by default); `access.is_listable` (sealed records never listed); `identity.py` Fernet encryption at rest |
| **Integrity** (data)         | `cas.ContentStore.verify` (address *is* the checksum); `fixity.hash_file_multi` (dual SHA-256 + BLAKE2b manifests) |
| **Durability / Redundancy**  | `replicate.replicate_bag` (copy to N `StorageLocation`s, verify-on-arrival); `config.StorageLocation` (`mirror` targets) |
| **Recoverability**           | `replicate.heal` (rebuild only from a just-validated replica) and `replicate._quarantine` |
| **Failure transparency**     | `replicate.verify_replicas` (degrade, never raise); `QUARANTINE` event attached to `ReplicationError`; `errors.py` (failures surfaced, never swallowed) |
| **Auditability / Provability** | `metadata.premis.PremisLog` (append-only); `moderate.ModerationLog` (justified, attributed, contestable) |
| **Autonomy / Consent**       | `moderate.change_consent`; `identity.IdentityVault.revoke` (takedown honoured at storage) |
| **Determinism / Reproducibility** | `models.canonical_json`/`now_iso`; `bag.write_bag` (sorted manifests, byte-identical bags); injected `now` throughout |
| **Standards compliance**     | `bag.py` (RFC 8493 BagIt); `metadata.premis.to_premis_xml` (PREMIS v3); `metadata.dublincore.to_oai_dc_xml` (ISO 15836 / `oai_dc`); `oais.py` (ISO 14721 SIP/AIP/DIP) |
| **Interoperability / Portability** | `bag.validate_bag` (any RFC 8493 tool can read a bag); `metadata` XML exporters; `config.load` (JSON *or* TOML) |
| **Accessibility**            | `server._page` / `_records_list_html` / `_records_table_html` (WCAG 2.2 AA shell, equivalent list + table, textual content-warning interstitial) |
| **Securability**             | `server` (CSP, `nosniff`, path-traversal guard, deny-by-default grant, loopback bind); `access.grants` (least privilege, `identity_unseal` ≠ `is_steward`) |
| **Efficiency / Scalability** | `cas` content-addressed dedupe + streaming writes; `fixity` constant-memory chunked hashing; `fixity.hash_file_multi` (one read, all digests) |
| **Configurability / Upgradability** | `config.Config` (versioned config-as-data); `config._migrate` (forward migration, refuse newer schema) |

---

*ledger is a reference implementation and an independent personal open-source project,
AGPL-3.0, unaffiliated with any employer or client. See [NOTICE](../NOTICE) and the
[README](../README.md).*
