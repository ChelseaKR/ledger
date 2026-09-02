# 0017. Every PREMIS event says what kind of object it is about

Status: Accepted

Date: 2026-08-27

Extends ADR 0012, which introduced `linkingObjectIdentifierType` and recorded this as
a follow-up in its own consequences. Does not supersede it: ADR 0012's decisions all
still hold.

## Context

ADR 0012 made a PREMIS event's object explicit rather than inferred, and typed the two
kinds it needed at the time (`ledger-payload`, `content-address`, plus
`ledger-record`). It then wrote down what it had not finished:

> `linkingObjectIdentifierType` is emitted as `local` in XML for events whose writers
> have not been typed yet (consent changes, takedowns, replication). That is the
> conventional repository-scoped type and it is honest; typing those writers is a
> follow-up that changes nothing about what the events assert.

`local` is honest and useless. PREMIS leaves identifier types to the repository
precisely so a consumer never has to guess what an identifier names, and ledger writes
five kinds of identifier that are indistinguishable as strings: a record id, a bag
name, a proposal id, a payload id, and a content address.

`PremisEvent.object_identifier_type` already refuses to guess among them. It infers
only the content-address case, where the parse is unambiguous, and returns `None`
otherwise, with the reasoning stated in its docstring: "a record id, a bag name, and a
proposal id all look alike, and guessing among them would be the same defect this
field exists to prevent." That refusal is correct, and it is exactly what made an
untyped writer permanently unanswerable rather than merely unanswered. The reader
cannot fix this; only the writer can.

Eighteen writers across six modules named an object and did not say what kind it was:
`access/redaction.py` (2), `ingest.py` (1), `moderate.py` (5),
`reading_room_enclave.py` (1), `replicate.py` (8), `server.py` (1).

## Decision

### 1. The vocabulary gains the two kinds the untyped writers actually name

`ledger-bag` and `ledger-proposal` join `ledger-payload`, `ledger-record` and
`content-address`.

A bag name equals its record id today, and typing it `ledger-record` would have
avoided a vocabulary change. Rejected: the two are not the same *kind* of thing. One
names a storage container a replica holds; the other names the Representation. An
event that quarantines a bag is not an event about the record's content, and a
consumer reconciling replicas against a catalogue must be able to tell those apart
without knowing that the two identifiers happen to coincide in this implementation.

`ledger-proposal` is the dual-control authorization decision a reading-room query
event is about, which is not the records the query touched.

### 2. Every writer that names an object declares its type

All eighteen are typed. Three writers pass `linked_object=None` and stay that way:
they are explicitly about no single object (a whole-archive validation, a rekey, an
attestation policy change), and inventing an object for them would be worse than
naming none.

### 3. The invariant is enforced structurally, not by sweep

`tests/test_premis_linking_identifier_types.py` parses the package and fails on any
`PremisEvent(...)` that sets a non-`None` `linked_object` without a
`linked_object_type`. A one-time sweep would have left the next writer free to omit
it; this covers writers no behavioural test happens to exercise, and it names the file
and line.

`OBJECT_TYPES` is the closed set, and a second structural test refuses a hard-coded
type outside it.

### 4. The reader is unchanged, and so is every event already written

The `or "local"` fallback in the XML serializer stays. It now applies only to events
written before ADR 0012, which is what it was always for.

`to_dict` still omits the type when unset, so an event written before these fields
existed serialises -- and therefore hash-chains -- byte-for-byte as it always did.
Nothing migrates and no chain moves.

## Consequences

| | before | after |
| --- | --- | --- |
| writers naming an object without its kind | 18 across 6 modules | 0 |
| identifier types in the vocabulary | 3 | 5 |
| events serialising as `local` in XML | every consent change, takedown, redaction, replication, quarantine, correction, reading-room query | only pre-ADR-0012 events |
| what stops the next untyped writer | nothing | a structural test naming file and line |
| pre-existing events on disk | | unchanged, byte-for-byte |

Costs and open edges, stated rather than left to be discovered:

- **Newly written events serialise differently from their predecessors of the same
  kind.** A takedown written today carries `linkingObjectIdentifierType`; one written
  last week does not. Both are valid PREMIS and both verify in their chains, because
  the chain covers the bytes each event actually had. A consumer reading a mixed log
  must treat the field as optional, which it always was.
- **`ledger-bag` and `ledger-record` currently carry equal values.** This ADR asserts
  they are different kinds, not different strings. If bag naming ever diverges from
  record ids, the events already say which they meant.
- **The vocabulary is closed by `OBJECT_TYPES` and enforced only against hard-coded
  literals.** The PREMIS reader round-trips whatever type it read, including a type
  from another repository's log, which is correct for a reader and deliberately not
  checked.
- **Nothing yet consumes the new types.** The value is in the log being answerable,
  not in a feature reading it; the steward audit surfaces still render events without
  distinguishing kinds. That is not a gap this ADR creates.
- **The second half of MP-08 is not done here, and is not merely undone.** ADR 0010
  declined the browse `media_type_basis` label rather than deferring it: it "needs a
  new user-facing string in four locales including Arabic, and authoring translations
  that nobody can review is its own honesty problem." What unblocks it is a reviewer,
  not an implementation. This ADR does not reopen that.

### Alternatives considered

- **Teach `object_identifier_type` to infer more.** Rejected, and this is the crux: it
  is the defect ADR 0012 exists to prevent. A record id, a bag name and a proposal id
  are the same shape, so any inference among them is a guess that will be wrong
  silently.
- **Type the bag events as `ledger-record`.** Rejected in decision 1.
- **Leave `local` and document it as intentional.** Rejected: ADR 0012 already
  committed to the follow-up, and `local` conveys strictly less than the writer knew
  at the moment it wrote.
- **Migrate existing events to carry types.** Rejected: it would rewrite every
  hash-chained log to add information the original writer did not record, which is
  precisely the tampering shape the chain exists to detect.
