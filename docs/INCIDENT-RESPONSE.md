# Incident response — ledger

Last verified: 2026-07-11 · Recheck cadence: after any SEV1/SEV2 incident or
quarterly, whichever is sooner.

This file supplies ledger-specific values for the shared Incident Response
Standard. Ledger is local-first, so service-availability incidents are normally
out of scope; data exposure, loss of integrity, consent/takedown failure, and
secret exposure apply in full.

## Severity and response targets

| Severity | Ledger trigger | Acknowledge | Contain / resolve |
|---|---|---:|---:|
| SEV1 | Contributor identity or sealed content exposed; public credential; no-outing/consent guard failure | 4 hours | contain within 24 hours |
| SEV2 | Secret caught before publication; core archive path unavailable or corrupt with recovery possible | 24 hours | 3 days |
| SEV3 | Disabled security gate or non-exposing defect affecting a subset of records | 3 days | 2 weeks |
| SEV4 | Near miss or process weakness with no user impact | best effort | tracked |

The repository owner is the responder. Raise severity freely as facts emerge;
lowering it requires a written rationale in the postmortem.

## Tracking convention

Every incident is a GitHub issue carrying exactly two classification labels:
`incident` and one of `sev1`, `sev2`, `sev3`, or `sev4`. Add `deploy-caused` when
the event occurred within 24 hours of a release or deployment, or record
`deploy-caused: no` in the issue once ruled out. The issue's open and close times
feed the quarterly DORA review.

Do not close a SEV1–3 incident until its postmortem is committed under
`docs/incidents/YYYY-MM-DD-<slug>.md` and references the issue. SEV1/SEV2
postmortems are due within seven days of resolution; SEV3 within fourteen.

## Secret or credential exposure

1. Generate a replacement credential immediately.
2. Revoke the exposed value at its issuer and verify revocation.
3. Review issuer/audit logs for use between exposure and revocation.
4. Decide whether history must be scrubbed. Default to no rewrite after a public
   push because rotation contains the risk; document either decision.
5. Close the entry point and prove the regression is caught by gitleaks.
6. Commit a postmortem using [`docs/incidents/README.md`](incidents/README.md).

Never place real identity, sealed content, credentials, or grant material in the
issue or postmortem. Use synthetic sentinel fixtures and describe the shape of an
exposure.

## Data-exposure interface

For L2/L3 exposure, the Impact section names the tier from
[`DATA-GOVERNANCE.md`](DATA-GOVERNANCE.md), whether retention and backup controls
held, whether replicas were affected, and whether contributor notification needs
human/legal review.
