# 0020. Arrangement narrows and never widens, and a container's existence is its own seal

Status: Accepted

Date: 2026-09-11

Relates to: [#202](https://github.com/ChelseaKR/ledger/issues/202) (collections and
series). Extends ADR 0007 (withhold, do not 403) and ADR 0003 (identity separated
from the record) to a second axis of description.

> Numbering note: 0019 is claimed by the in-flight #188 branch (physical holdings).
> This ADR takes 0020 so the numbers do not collide on merge. The two changes are
> independent: #188 adds no container and no placement, and this one adds no
> holding kind.

## Context

Before #202, `ledger` described *items* and nothing above them. `Record` had a
title, Dublin Core, fields and payloads, and no parent. Everywhere the word
"collection" appeared in the source it meant the whole archive:
`access/policy.py` calls Dublin Core "collection-level descriptive metadata",
`oai.py` says a harvester can "follow the collection as it grows", and
`metadata/ead.py` — a module whose entire reason to exist is EAD's hierarchical
`<dsc>` — emitted `<c01 level="item">` per record and nothing else. The hierarchy
element was present and always one level deep.

The material this project is for arrives as one organizer's four boxes: 380
flyers, a run of a newsletter, 12 cassettes, a folder of minutes. Flat, that is
400 items each needing its own title, its own subjects, and its own disclosure
decision, made one at a time by a tired volunteer. Arranged, it is one collection
with four series and a scope note that says whose it was and how it came in.

Adding arrangement to an archive for at-risk contributors raises two questions
that have to be answered together, because answering either one alone produces a
system that is worse than no arrangement at all.

**How does a container's policy combine with a record's?** If the answer is "the
broader of the two", a container is a hole in the disclosure model. If it is "an
ordering over `AccessPolicy`", somebody has to decide whether `SEALED_CONDITIONAL`
is narrower than an expired `SEALED_UNTIL`, and every future policy level
re-opens the question.

**Is a container's existence disclosable?** A collection titled *"2019 raid
testimony, deposited by Casa Abierta"* outs its depositor by aggregation even when
every record inside it is sealed. A model in which a container is merely a folder
that inherits its visibility from its contents cannot express that, and browse
would publish the title.

## Decision

**1. The resolution is a logical AND over the root-to-parent chain, not a
comparison between policies.**

```
visible(record) == is_visible(record's own policy)
                   AND is_visible(ancestor 1's records_policy)
                   AND is_visible(ancestor 2's records_policy)
```

An AND has no ordering to get wrong. There is no lattice over `AccessPolicy`, no
table of which level dominates which, and no cell in which a broad container
makes a narrow record more visible — the property holds because of the shape of
the expression, not because a table happens to be right.
`tests/test_arrangement_policy.py` asserts all 144 cells of (6 record policies ×
8 container ceilings × 3 viewers) against a hand-written truth table, including
the case #202 names by name: a container broader than the record it holds.

**2. A container carries two policies, because it is two things at once.**

`policy` governs the container's **own description** — its title, scope note,
extent, dates, and the fact that it exists. `records_policy` is the **ceiling**
over the records filed in it. They are independent: a collection can be
steward-only while what it holds is public (the Casa Abierta case above), or
public while what it holds is community-only until 2030 (the "one decision
instead of 400" case).

Container visibility narrows down the chain too, which makes the visible part of
a chain always a prefix: no viewer is ever shown a series without the collection
holding it, and a scope note is inherited only from an ancestor the viewer may
already read.

**3. Everything fails closed, including the compatibility default.**

`Arrangement.chain` returns `None` — never a partial chain — for an unknown id, a
broken parent link, a shape the two-level vocabulary forbids, or a walk that
would exceed its bound. Every caller reads `None` as deny, for stewards too: a
ceiling that cannot be applied is a ceiling of unknown height, and an unapplied
ceiling is a widening.

The same rule governs a read path that was never taught about arrangement.
`disclose(record, grant, now)` with no `arrangement=` cannot apply a placed
record's ceiling, so it refuses the record. That is what makes it impossible for
this change to be *half* shipped: a surface that has not been updated serves
nothing rather than serving it unclamped.

Failing closed is silent by construction, so `ledger arrange check` exists to say
what the resolver decided: it names every record denied to everyone because its
chain will not resolve, and exits non-zero.

**4. Two levels, and exactly one parent.**

A collection is a root and a series sits directly under one. That bounds every
chain at two links, makes a cycle unrepresentable rather than merely guarded
against, and keeps the resolver a loop a reviewer can read. Archival arrangement
says an item has one place; the many-to-many need is what subjects are for.

**5. Placement is optional, forever. Nothing is migrated.**

#202's third "decide first" item offered a choice: give every existing record an
implicit "unarranged" container, or let placement stay optional. The issue called
the first cleaner and said the second "means every read path carries a null
branch".

We took the second, and the premise turned out to be wrong: the null branch lives
in **one** function (`arrangement_permits` short-circuits on `placement is None`),
not in every read path, because no read path reads `placement` for itself. The
first would have meant rewriting every bag's manifest, and — the real objection —
giving every record in the archive a parent gives every record a new way to be
clamped. A migration that silently narrows an existing archive is the opposite of
what this feature is for.

`serialize_record` omits `placement` entirely when there is none, so every
manifest written before #202 serialises to exactly the bytes it always did: no
committed manifest, bag manifest or tag-file digest moves.

## Consequences

**A record cannot be made more visible than the container it is filed in.** That
is the point, and it has a cost worth stating: #202's own example — "this whole
box is community-only until 2030, except the three flyers already public" — is
not expressible as written. Those three flyers have to sit outside the
community-only container, or the container has to be public with the other 397
records narrowed individually. Making the exception expressible would mean an
override, and an override is exactly the hole rule 1 closes.

**An archive with no `containers/` directory behaves as it always did.** Every
chain is empty, an empty AND permits, and no gate, export or page changes.

**`/contribute` offers no container picker.** Which collections exist is itself
policy-gated, so a public form cannot list them without answering the question
the container policy exists to refuse. A steward files a submission after review
with `ledger arrange place`.

**The published record schema gains an optional property.** `placement` is the
one property in `record.schema.json` that is not required, because a record
written before this ADR does not carry it. `container.schema.json` is new and
every property in it is required.

**Membership is expressed in each format's own vocabulary, not repeated.**
OAI-PMH says it with `setSpec`; METS and Dublin Core say it with a `logical`
structMap and `dc:relation` (DCMI `isPartOf`, which has no element of its own in
`oai_dc`); EAD says it by nesting. None of them says it twice.

**Deferred, deliberately: accession.** #202's fourth "decide first" item asks
whether accession-level records (what came in, from whom, when, under what
agreement) belong here. They do not: an accession is a *custody* event with its
own dates, agreement and depositor, and modelling it as a third container level
would put a depositor's name into the arrangement — the one place the no-outing
rule most needs it absent. Nothing here forecloses it; a future `Accession`
entity can reference a `Collection` by id without either schema moving.
