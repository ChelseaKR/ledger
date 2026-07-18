# Review packet

ledger is a pre-1.0 reference implementation. Automated checks and committed
analysis are useful evidence, but they are not a substitute for a community partner
or an independent human review. This directory makes those reviews bounded,
synthetic-data-only, and useful to both reviewer and maintainer.

| Review | Who it is for | Timebox | Output |
| --- | --- | --- | --- |
| [Community archivist pilot](community-archivist-pilot.md) | A community archivist or mutual-aid steward | 60–90 minutes | Workflow observations and adoption conditions |
| [Threat-model review](threat-model-review.md) | An independent security, privacy, or applied-cryptography reviewer | 2–4 hours | Findings, threat-model corrections, and residual-risk decision |
| [Manual accessibility review](manual-accessibility-review.md) | A screen-reader and keyboard user, ideally paid for their expertise | 60–90 minutes per AT/browser pair | Dated assistive-technology findings and ACR updates |

## Safety boundary for every review

- Use only the repository's synthetic records and sentinel identities.
- Do not paste real names, sealed fields, screenshots, logs, or archive content into
  an issue, pull request, or review report.
- A reviewer may report a flaw by shape (for example, “a sealed field rendered to an
  anonymous viewer on a route”), never by reproducing sensitive content.
- A review invitation, completed checklist, or issue label does **not** turn into a
  completed independent review until a named reviewer has submitted dated findings.

For vulnerability reporting, use [SECURITY.md](../../SECURITY.md), not a public
issue. For contribution rules, including the redaction-safe rule, see
[CONTRIBUTING.md](../../CONTRIBUTING.md).
