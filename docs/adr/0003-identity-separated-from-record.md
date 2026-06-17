# 3. Identity separated from the record

## Status

Accepted

## Context

ledger's central, non-negotiable promise is that **holding a record can never out
the person who contributed it**. The records this archive holds — oral histories,
protest ephemera, mutual-aid runbooks — are contributed by people who may not be
out, who may be undocumented, or who organize where any of that is dangerous. The
same record that preserves a life can expose the person who made it. So contributor
safety has to be a first-class preservation requirement, equal to bit-integrity, and
it has to be enforced by the shape of the data rather than by the discipline of every
read path.

The naive design — a `contributor_name` (or contact, or pronouns) field on the
record — fails this requirement structurally. Once identity lives on the record, it
travels with every copy: into the BagIt bag that gets replicated to a member's drive
and an off-site mirror, into descriptive metadata, into logs, into error messages,
into the JSON API, into any export. Every read path then has to remember to strip it,
and the guarantee becomes only as strong as the least careful surface. A
preservation format is designed to be copied widely and read by other tools decades
from now; that is exactly the wrong place to put a secret.

We need a design where a record is safe to replicate and inspect *by construction*,
and where contributor identity is reachable only through a single, deliberate,
revocable act.

## Decision

We **store contributor identity in a separate, encrypted vault, referenced from the
record only by an opaque token**, and we make the safe shape the only shape a read
path can emit.

- A `Record` (`src/ledger/models.py`) carries **no identity** — at most an opaque
  `identity_ref: str | None`. The module documents this as the single most important
  invariant and explicitly forbids adding a `contributor_name` field.
- The mapping from `identity_ref` to a real `ContributorIdentity` lives nowhere but
  the **encrypted identity vault** (`src/ledger/identity.py`). On disk the vault is a
  JSON object mapping each ref to authenticated-symmetric-encryption ciphertext
  (Fernet), with the key derived from a passphrase via scrypt or generated from a
  CSPRNG. Writes are atomic (write-temp-then-rename).
- The **ref is random and independent of the identity** it points at
  (`secrets.token_urlsafe`, 32 bytes), so observing a ref reveals nothing about the
  person — refs are unlinkable to contents.
- Identity is returned **only through `IdentityVault.resolve(ref, grant)`**, and only
  when the grant's `identity_unseal` set names that ref. The grant check runs *before*
  any lookup, so the decision does not even depend on whether the ref exists (least
  privilege). `identity_unseal` is empty for almost everyone, including most stewards.
- The **only record shape a read path may emit is `DisclosedRecord`**, which has no
  `identity_ref` field at all and is constructed solely by the single disclosure
  chokepoint (`ledger.access.disclose`). The browse/search server builds every
  response from a `DisclosedRecord`, so no route, header, JSON field, log line,
  health summary, or error page can carry an identity.
- Identity may appear transiently on exactly one OAIS shape, the **SIP** (submission
  package), on its way into the vault; the ingest pipeline moves it into the vault
  and replaces it with an opaque ref before anything is written to disk. The **AIP**
  (what is stored) and the **DIP** (what is read) structurally cannot carry it.
- `ContributorIdentity` and `IdentityVault` have **redacted `__repr__`/`__str__`**,
  and no identity, sealed value, ciphertext, or key is ever placed in a log line or
  an exception message.
- `IdentityVault.revoke(ref)` deletes the mapping, so **consent is revocable** at the
  storage layer and a takedown is honoured even where replicas of the (identity-free)
  record persist.

## Consequences

- **The no-outing guarantee is structural, not procedural.** A record, a bag, a
  replica, and every read shape are safe to copy and inspect because none of them
  *contains* identity. There is no surface that has to "remember" to strip it.
- **The guarantee is testable.** Because identity flows through one module and one
  disclosure point, the audit suite can inject sentinel identities at ingest and
  assert their absence from every public surface, log, filename, and error — and the
  `no-outing-audit` CI job runs the disclosure tests on their own so a regression is
  unmistakable.
- **Disclosing identity is a deliberate, narrow, revocable act**, gated by a grant
  that names the specific ref, defaulting to denied, and undoable by deleting the
  mapping.
- **A new dependency is accepted for it.** Storing identity safely requires real
  cryptography, so `cryptography` (for Fernet and scrypt) is ledger's one runtime
  dependency. Rolling our own here would be the opposite of safety (see ADR 0005).
- **Key management becomes an operational responsibility.** If the vault key is lost,
  sealed identities are unrecoverable — which is the correct failure direction for a
  safety tool, but it means operators must manage the key with care (it is never
  committed, never logged).
- **A small indirection cost** is paid: resolving identity is an extra, gated step
  rather than a field read. This is the intended friction.

### Alternatives considered

- **Identity as a field on the record.** Simplest, and structurally unsafe: identity
  would propagate into every bag, replica, log, and export, making the guarantee only
  as strong as the most careless read path. Rejected.
- **Identity on the record but redacted at each read path.** Pushes correctness onto
  every surface and every future contributor; one missed path is an outing. Rejected
  in favour of a shape (`DisclosedRecord`) that *cannot* carry identity.
- **Encrypt the whole record.** Protects identity but defeats the preservation and
  discovery goals — a bag must be a plain, inspectable, broadly readable artifact, and
  most of a record is meant to be discoverable per its access policy. Rejected;
  encryption is scoped to the vault, where the secret actually is.
- **A deterministic or content-derived ref (e.g. a hash of the identity).** Would
  make the ref linkable back to the identity and enable correlation attacks. Rejected
  in favour of a random, independent token.
