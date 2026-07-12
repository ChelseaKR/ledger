# Residual-risk register — ledger

- **Prepared:** 2026-07-11
- **Register owner:** Chelsea Kelly-Reif
- **Human sign-off:** pending; this remediation pass does not impersonate the accountable owner
- **Next review:** before the first tagged release, then quarterly

This register is the decision companion to [`docs/THREAT-MODEL.md`](../THREAT-MODEL.md).
It records risks the implementation reduces but cannot eliminate. “Accept” means
the bounded residual is stated honestly; it does not mean the risk is harmless.

| ID | Risk description | Likelihood | Impact | Owner | Mitigation in place | Decision | Review date |
|---|---|---|---|---|---|---|---|
| RR-01 | Seizure of both the identity vault and its key reveals contributor identity | Low | High | Project owner / adopter | encrypted, separate vault; key stays out of the archive and backups use a second secret | Track — threshold custody remains a design/review item; do not claim seizure resistance when key and vault co-reside | before first release |
| RR-02 | Public record prose or cross-record correlation re-identifies a contributor despite identity separation | Medium | High | Adopting community | minimal-disclosure defaults, field policies, preview, redaction guidance | Accept with explicit contributor warning; re-review after user research with a community design partner | quarterly |
| RR-03 | A steward with raw disk access reads sealed content or tampers with application-enforced logs | Medium | High | Adopting community | least-privilege grants, attributed events, fixity, off-box replicas, governance review | Track — deployment hardening and multi-steward governance are required operational controls | before production adoption |
| RR-04 | An offline or hostile replica retains a pre-takedown copy longer than intended | Medium | High | Adopting community | durable tombstones, pending-location state, retry-on-reconnect, per-location receipts | Accept only for explicitly trusted replica operators; completion requires confirmation from every configured replica | quarterly |
| RR-05 | Plain HTTP or traffic analysis reveals what a legitimate viewer accessed | Medium | Medium | Deployment operator | loopback bind by default, no third-party assets, CSP/no-referrer, reverse-proxy deployment guide | Track — TLS termination is mandatory for any networked deployment; access-pattern hiding remains out of scope | before production adoption |
| RR-06 | Release signer identity is not pinned even though signed-tag presence is enforced | Low | High | Project owner | release job rejects lightweight/unsigned tags; keyless artifact signing and SLSA provenance | Track — commit the approved signer identity before the first tag | before first release |
| RR-07 | Accountable-owner and independent cryptography/accessibility reviews have not occurred | High | High | Project owner | candid Beta status, review-ready artifacts, automated safety/accessibility gates | Track — no Production claim until the required human reviews are dated and committed | before Production status |

## Sign-off

- Accountable owner: pending
- Independent security/cryptography reviewer: pending
- Review notes: pending

Open rows are reviewed at the stated date and after any material change to
identity custody, consent/takedown propagation, replication, or network exposure.
