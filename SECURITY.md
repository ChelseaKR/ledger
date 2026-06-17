# Security policy

ledger holds records for people for whom exposure can be dangerous. Security here is
not only about the software — it is about the safety of contributors. Please read the
reporting rules below; one of them is unusual and important.

## Supported versions

This is a pre-1.0 reference implementation. Security fixes land on `main` and the
latest tagged release. Pin a tag and watch releases for advisories.

| Version | Supported |
| ------- | --------- |
| `main` / latest tag | ✅ |
| older tags | ❌ |

## Reporting a vulnerability

**Use GitHub's private vulnerability reporting** ("Report a vulnerability" under the
repository's *Security* tab). It opens a private advisory only maintainers can see.
If you cannot use it, email **ckellyreif@gmail.com** with `ledger security` in the
subject. Expect an acknowledgement within a few days; this is a volunteer project, so
please be patient and do not disclose publicly until a fix is available.

### Redaction-safe reporting (please read)

When you report a bug or a security issue, **never paste a real sealed value, a real
contributor identity, or any record content that is not already public.** If a flaw
exposes a sealed field, describe the *shape* of the leak — "field X with policy
`sealed-until` rendered to an anonymous viewer on route Y" — and reproduce it with the
synthetic fixtures in `tests/fixtures/`, which carry sentinel values designed exactly
for this. A report that helps us fix a leak must not itself become a leak. Issue
templates and the bug-capture path are built to never ask you for sealed content.

## What we consider a vulnerability

In addition to the usual (RCE, auth bypass, injection, secret exposure), the
following are **first-class** security bugs in ledger, equal in severity to a memory
unsafety bug elsewhere:

- **Any path by which holding or operating an archive reveals who contributed a
  record** to a viewer without an explicit identity-unseal grant — via a view, the
  JSON API, an export, a filename, a log line, a metric label, an error message, a
  timing difference, or inference from what is *not* shown.
- **Any path by which a sealed record or field renders to a viewer whose grant does
  not permit it**, including after a takedown or consent change.
- **Any silent fixity outcome** — corruption accepted as valid, or a `fixity-failure`
  swallowed instead of quarantined.
- **Any consent or takedown that does not propagate** to all replicas.

See `docs/THREAT-MODEL.md` for the full model (seizure, subpoena, doxxing, a
malicious steward) and the guarantees the code is required to meet.

## Our commitments

- We fix no-outing and sealed-disclosure bugs with the highest priority.
- We credit reporters who want credit, and respect those who want anonymity.
- Releases are signed and dependencies are pinned and scanned (pip-audit, CodeQL,
  gitleaks in CI); see `CONTRIBUTING.md`.
