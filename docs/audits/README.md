# docs/audits/

Committed home for dated fixity- and audit-run artifacts (auditability, accountability).

This directory holds point-in-time records of the archive's integrity and access checks,
committed so the audit trail is inspectable in the repository rather than living only in
ephemeral CI logs:

- **Fixity reports** — the outcome of a scheduled fixity sweep across replicas: which bags
  were verified against their `manifest-sha256.txt` / `manifest-blake2b.txt`, and any
  `fixity-failure` events raised (bad copy quarantined, healed from a verified replica).
- **Access / disclosure audits** — the no-outing audit suite's summary: sealed fields
  withheld from ungranted viewers, contributor identity absent from every public output,
  log, filename, and error.
- **Dependency / supply-chain audits** — periodic `pip-audit` and related scan snapshots.

Each artifact is dated (`YYYY-MM-DD-<kind>.md` or `.json`) and identity-free by
construction: an audit record carries agents and outcomes, never contributor identity
(the no-outing rule applies here as everywhere).

The presence and contents of this directory are asserted by the truthfulness gate
(`tools/check_claims.py`, run in `make verify`), which keeps the README's
"committed `docs/audits/`" claim honest.
