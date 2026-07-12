# Data card — community contributions

Last verified: 2026-07-11 · Recheck cadence: on any ingest-schema, consent,
license, or retention-policy change.

| Field | Value |
|---|---|
| Source | A person or community archive operator submits content directly through the ledger CLI or local contribution surface; no third-party API or scraper |
| Responsible entity | The adopting community operating its own ledger instance; the software maintainer does not receive or host instance data |
| License | Contributor-supplied; ledger does not assume ownership or relicense content. Operators must record and honor the contributor's terms before public reuse |
| Fetch/refresh cadence | Event-driven at submission; not periodically fetched |
| Fetch timestamp | `created_at` / ingest-time PREMIS event and BagIt metadata, generated for every ingest |
| Classification | L3 when identity, contact, sealed content, precise location, or outing risk is present; otherwise L1/L2 as documented by the operator |
| Known limitations | Contributor-provided metadata may be incomplete or inaccurate; public prose can reveal identity through context even when direct identity is separated |
| Retention | Contributor-directed and indefinite while consent remains; tightening and takedown propagate under [`DATA-GOVERNANCE.md`](../DATA-GOVERNANCE.md) |
| Lineage | Submission → SIP → validated AIP/BagIt bag → content-addressed store → optional verified replicas; PREMIS records preservation and consent events |
| Dataset version | N/A — ledger does not publish contributions as a versioned dataset |

Schema and safety checks reject malformed records and prevent optional contributor
identity from entering the preservation record. License compatibility and authority
to contribute remain human review obligations for the adopting community.
