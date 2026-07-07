# 6. Standards applicability declarations

## Status

Accepted

## Context

ledger participates in a personal portfolio governed by a shared set of engineering
standards (`STANDARDS/` — fetched at CI time by `.github/workflows/standards.yml`,
pinned to a released tag, never vendored). `STANDARDS/README.md` requires every
repo to state, in its own README, which standards apply and to give a one-line
reason for any that do not — "silent omission is a defect."

A conformance audit dated 2026-07-05 found that ledger's README made **no**
applicability declaration at all, while separately making several claims the repo
did not back (a committed `docs/audits/` directory, "signed releases," "CI
latency budgets"). This ADR is the record of the applicability decisions now
declared in the README's `## Standards conformance` table; it exists so the *why*
behind each decision — especially the one N/A — is durable and reviewable, per
CODE-QUALITY-STANDARD CQ-45 ("ADR required for declaring a standard N/A").

## Decision

Ten of the portfolio's eleven standards **apply** to ledger. The applicability
basis for each, and the one N/A, are:

| Standard | Applies? | Basis |
|---|---|---|
| Quality & Metrics | Applies | Applies to all repos |
| Code Quality | Applies | Applies to all repos (Python) |
| Security & Supply Chain | Applies — **ASVS L2** | The standard's own §1 table places ledger at L2 because it touches PII/identity (the contributor-identity vault) |
| CI/CD | Applies | Ledger has CI (three workflows: `ci.yml`, `codeql.yml`, `standards.yml`) |
| Release & Versioning | Applies — **mandatory** | The standard's own §1 table lists ledger under "Published library — Yes, mandatory" (intended as a `pipx`/PyPI-installable package) |
| Accessibility | Applies | The server renders a human-facing HTML browse surface; the standard names ledger explicitly as in scope, and the repo already treats it as applying (merge-blocking CI gate, committed ACR) |
| Observability | Applies — **Tier C** | OBSERVABILITY-STANDARD's tier table places ledger in Tier C (library/CLI): OTel tracing is out of scope (no network surface beyond the local browse server), `--log-format json` is opt-in, and the no-PII/no-secrets-in-logs control (OBS-11) applies unconditionally regardless of tier |
| Internationalization | Applies | The standard names ledger explicitly (EN/ES user-facing strings); the gettext migration (PR #17) is in fact the repo's strongest standard score |
| **AI Evaluation** | **N/A** | No model inference exists anywhere in ledger's user-facing or decision-making path (ingest, fixity, access policy, and disclosure are all deterministic, non-ML logic). The standard's own scope section lists ledger as out of scope. **This N/A is not permanent**: AI-EVALUATION-STANDARD's AIEV-01 trigger ("first LLM SDK use flips this to Applies") remains armed. If a future feature introduces any model inference — even something as narrow as a suggested-tag classifier — this decision must be revisited and the standard's gates (eval harness, red-team suite, groundedness/refusal thresholds) brought in before that feature merges, not after. |
| Documentation | Applies | Applies to all repos |
| Responsible Tech | Applies | Applies to all repos; ledger's no-outing sentinel suite is in fact this standard's own named exemplar for misuse-resistance testing (RTF-02) |

The Release & Versioning "mandatory" status and the Security "L2" designation are
also stated directly in the README table and in `docs/RESPONSIBLE-TECH-AUDITS.md`
(§F), not just here, so a reader does not have to find this ADR to learn them.

### Status vocabulary

The README's status line now uses one of DOCUMENTATION-STANDARD's fixed terms
(`Spec` · `Scaffolded` · `In build (Mx)` · `Beta` · `Production` · `Maintained` ·
`Archived`): **Beta**. ledger has a complete, tested feature set (528 tests, 86%
branch coverage) and functioning safety engineering (the no-outing sentinel suite,
disclosure tests, threat model), but has not had an independent security or
cryptography audit and has shipped no tagged release — "Beta," not "Production"
or "Maintained."

## Consequences

- The README's Standards Conformance table is now the single place a reviewer
  checks to learn what applies, what the current posture is, and where an open gap
  is tracked (`docs/ROADMAP.md`'s conformance gap tracker) — no more silent
  omission.
- The AI-Evaluation N/A is a live commitment, not a one-time note: any PR that adds
  model inference must also address AIEV-01 and bring in the AI-EVALUATION-STANDARD
  gate set. Reviewers should treat "adds an LLM call" as a trigger for re-opening
  this ADR, the same way a schema migration triggers ADR 0004.
- Because several "Applies" rows are honestly "gap tracked" rather than green, this
  ADR and the README table it backs will need a follow-up revision once the P1/P2
  remediation items land — the intent is a living declaration, re-verified per the
  cadence stated in the README table, not a one-time compliance artifact.

### Alternatives considered

- **Declare AI-Evaluation N/A with no ADR, relying only on the README's one-line
  reason.** Rejected: CQ-45 specifically requires the ADR for an N/A declaration,
  and a one-line README reason does not record the AIEV-01 trigger condition in a
  way a future contributor adding a feature is likely to find.
- **Wait to declare conformance until every gap in the table is closed.** Rejected:
  `STANDARDS/README.md` treats silent omission as worse than an honest "Applies —
  gap tracked" row, and the audit that prompted this ADR made that explicit. A
  candid, dated, gap-tracked table shipped now is more useful than a green one that
  waits on multi-day release-pipeline work (see `docs/ROADMAP.md`).
