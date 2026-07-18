# Independent threat-model review packet

## Scope

This packet is for an independent security, privacy, or applied-cryptography
reviewer. It asks for scrutiny of ledger's stated guarantees and residual risks; it
does not ask a reviewer to certify the whole system or to handle real archive data.

The maintainer's current position is deliberately narrow: the repository has automated
tests for disclosure and integrity properties, but it has **not** had an independent
security or cryptography audit. A review may narrow, correct, or strengthen that
position; it must not be summarized as an audit until the reviewer says so in writing.

## Read first

- [Threat model](../THREAT-MODEL.md)
- [Residual-risk register](../audits/residual-risk-register.md)
- [Architecture](../ARCHITECTURE.md)
- [Adoption checklist](../ADOPTING.md)
- [Five-minute synthetic walkthrough](../TRY-LEDGER.md)

Relevant code boundaries include `src/ledger/access/`, `identity.py`, `consent.py`,
`dualcontrol.py`, `replicate.py`, `tombstones.py`, `server.py`, and the associated
`tests/` disclosure and preservation suites.

## Questions worth answering

1. Does the stated threat model distinguish identity secrecy, content secrecy,
   metadata leakage, traffic analysis, and raw-host compromise clearly enough?
2. Does the identity-vault design have an unaccounted-for access path, key-custody
   assumption, logging boundary, or recovery failure mode?
3. Can an access policy or grant be bypassed through a rendered route, API route,
   export, replica, backup, error, or timing signal that the model overlooks?
4. Are the consent, takedown, and replica-propagation claims accurate when a replica
   is unavailable or hostile?
5. Which residual risks should block a real-data pilot, and what narrower claims are
   justified today?

## Review method

- Work only with synthetic fixtures and a local checkout.
- Use `make demo` to observe the intended disclosure proof.
- Run focused tests where useful: `pytest -m disclosure` and `pytest -m preservation`.
- Report reproduction steps using synthetic values or neutral placeholders.
- Send a security vulnerability through the private channel in `SECURITY.md`; do not
  publish an exploitable proof of concept in a public issue.

## Deliverable

Ask for a short, dated memo with findings grouped as: critical / high / medium / low /
informational, affected claim or code boundary, synthetic reproduction or rationale,
recommended disposition, and whether the reviewer consents to being named. The
maintainer should log the result in the residual-risk register and update public
claims before describing the review externally.
