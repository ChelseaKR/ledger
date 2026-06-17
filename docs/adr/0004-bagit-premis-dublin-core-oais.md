# 4. Open preservation standards: BagIt, PREMIS, Dublin Core, OAIS

## Status

Accepted

## Context

ledger's purpose is to keep a community's records when the people and platforms
around them are fragile: a single laptop, a dead group, a hosted service that
changes its terms. For that to mean anything, the archive must outlive any one host,
drive, maintainer, or even ledger itself. A community must be able to walk away with
its archive and read it with other tools, hand a peer a self-contained copy, or
deposit it with an institution — without ledger present to interpret a proprietary
format.

This rules out a bespoke storage layout, a database-only representation, or any
custom packaging that requires ledger's own code to read. Survivability and
portability demand formats that are open, documented, widely implemented, and likely
to be readable decades from now by people who never met us. The established
digital-preservation playbook already provides exactly this; the question is whether
to adopt it or invent.

## Decision

We adopt the established **open preservation standards** and keep them as
first-class, distinct objects in the codebase.

- **OAIS (ISO 14721)** is the reference model for how content moves. ledger keeps the
  three OAIS information packages as distinct, typed objects (`src/ledger/oais.py`):
  the **SIP** (submission — the only shape that may transiently carry identity), the
  **AIP** (archival — what is stored, and which structurally cannot carry identity),
  and the **DIP** (dissemination — the safe read shape, built through the single
  disclosure point). Every ingest and access step is thus traceable to a standard
  stage.
- **BagIt (IETF RFC 8493)** is the packaging and hand-off format (`src/ledger/bag.py`).
  An AIP is a bag: payload under `data/`, dual manifests (`manifest-sha256.txt` and
  `manifest-blake2b.txt`), a tagmanifest, and `bag-info.txt`. Emission is
  deterministic (sorted manifest lines, fixed separator, stable tag ordering), so the
  same payload always produces a byte-identical bag and bags can be diffed,
  golden-tested, and fixity-compared across machines.
- **PREMIS (Library of Congress data dictionary)** is the preservation-event
  vocabulary (`src/ledger/metadata/premis.py`). ledger keeps an append-only log of
  events — ingestion, fixity check, replication, redaction, policy change, consent
  change, takedown, quarantine, validation, moderation — each with its agent and
  outcome, and serializes it to canonical JSON (durable, byte-stable for hashing) and
  to minimal PREMIS XML (for exchange).
- **Dublin Core (DCMI / ISO 15836)** is the descriptive-metadata vocabulary
  (`src/ledger/metadata/dublincore.py`). The fifteen-element set is serialized to a
  canonical JSON sidecar and exported as `oai_dc` XML harvestable by OAI-PMH
  aggregators.

Identity is explicitly excluded from all of these clear-text artifacts. `bag-info.txt`,
the Dublin Core sidecar, and the PREMIS log must never carry contributor identity;
`dc.creator`/`dc.contributor` describe the *collection or community*, never a possibly
closeted individual. Identity lives only in the encrypted vault (ADR 0003).

## Consequences

- **Portability and no lock-in.** A bag with sidecar PREMIS and Dublin Core is plain
  files any conformant preservation tool can validate and unpack without ledger.
  ledger is the steward, not the owner; a community can export the whole archive and
  host it elsewhere.
- **Survivability.** The archive outlives any one host or maintainer because the
  package is a standard a stranger can read in the future. The format, not the
  running service, is what persists.
- **Interoperability.** Bags, `oai_dc` XML, and PREMIS XML are readable by other
  preservation systems and harvestable by aggregators, so ledger can interchange with
  the institutions a community may partner with.
- **Tamper-evidence comes from the format.** Dual-algorithm manifests plus
  content addressing mean a single weakened hash cannot hide drift; fixity is checked
  against the manifest rather than assumed.
- **Credibility and reuse.** Building on recognised standards means a partnering
  library or archivist already understands the artifacts, and the BagIt/PREMIS
  packaging and the fixity auditor are usable on their own.
- **We inherit the standards' constraints and verbosity.** These formats are more
  verbose and more rigid than a bespoke layout would be, and we are bound to their
  vocabularies and structures. We accept that cost as the price of survivability and
  interoperability.
- **Standards are necessary but not sufficient for safety.** The playbook assumes
  bit-integrity is the goal; it has no native concept of a contributor whose safety
  must be preserved alongside the bits. ledger adds that threat model on top
  (ADR 0003) and keeps identity out of every standard artifact.

### Alternatives considered

- **A bespoke storage format and custom packaging.** Could be leaner and tailored to
  ledger, but would require ledger itself to read, defeating survivability and
  portability. Rejected.
- **A database as the system of record.** Convenient for querying, but a proprietary
  or service-bound representation that a community cannot trivially walk away with or
  hand to a peer. Rejected as the durable form; the durable form is plain files.
- **Adopting only some of the standards** (e.g. BagIt without PREMIS, or Dublin Core
  without OAIS staging). Rejected: the chain of custody (PREMIS), the description
  (Dublin Core), the packaging (BagIt), and the staging model (OAIS) are
  complementary, and a partial adoption loses interoperability or auditability for
  little saving.
