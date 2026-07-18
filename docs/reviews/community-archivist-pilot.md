# Community archivist pilot packet

## Purpose

Invite one community archivist or mutual-aid steward to evaluate whether ledger's
workflow is understandable, appropriately scoped, and worth a supervised future
pilot. This is **not** a request to deposit real material, adopt the software, or
vouch for its safety.

## Invitation text

> I maintain an open-source, pre-1.0 reference implementation for a community
> archive called ledger. It combines preservation packaging (BagIt, PREMIS, Dublin
> Core) with consent-based selective disclosure. I am looking for feedback on a
> synthetic, local-only walkthrough—not real records, not a production deployment,
> and not an endorsement. Would you be open to a 60–90 minute paid or otherwise
> mutually agreed review of the workflow and its adoption checklist? Your feedback
> would be recorded only with your consent, and you may keep it private.

Adapt the compensation and contact details before sending. Do not imply that the
reviewer is responsible for security certification, legal advice, or the safety of a
future deployment.

## Before the session

- Agree on compensation, confidentiality, and whether any feedback may be quoted.
- Share [Try ledger in five minutes](../TRY-LEDGER.md),
  [the adoption checklist](../ADOPTING.md), and
  [the threat model](../THREAT-MODEL.md).
- Run the demo together or have the reviewer run it locally. Use synthetic data only.
- Make clear that the session is discovery, not a real-data pilot and not evidence of
  demand or community approval.

## Suggested 60–90 minute agenda

1. **Context (10 min).** What kind of records and governance constraints does the
   reviewer work with? Do not collect sensitive archive details.
2. **Walkthrough (20 min).** Run `make demo` and browse the synthetic local archive.
3. **Workflow critique (20 min).** Ask where a contributor, steward, or community
   governor would be confused, blocked, or put at risk.
4. **Adoption conditions (15 min).** Read the adoption checklist together. Which
   conditions are non-negotiable, missing, or inappropriate for a small collective?
5. **Close (5 min).** Confirm whether follow-up is welcome and what, if anything,
   may be made public.

## Questions to answer

- Does the contributor-safety model use words and controls a community can understand?
- Are the differences between public, community, steward, and sealed access clear?
- Does the interface honestly communicate what is withheld and why?
- Which operational requirements would stop a real deployment first?
- What evidence would the reviewer need before considering a supervised pilot?
- What should ledger explicitly *not* do or claim?

## Review record

Record only what the reviewer authorizes. A concise report should state:

```text
Date:
Reviewer role or organization (only if approved):
Scope completed:
Synthetic-only confirmation: yes/no
What was clear:
What was confusing or unsafe:
Adoption conditions:
Concrete follow-up requested:
Permission to publish this summary: yes/no/edited version only
```

Do not add a reviewer’s name, quote, or approval to this repository without explicit
written permission. A session with no publishable result is still valuable discovery;
it is not a public endorsement.
