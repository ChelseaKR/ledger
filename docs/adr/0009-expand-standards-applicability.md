# 0009. Expand standards applicability to incident response and data governance

Status: Accepted

Date: 2026-07-11

Supersedes: 0006

## Context

ADR 0006 recorded eleven portfolio standards. The shared portfolio standards now
also define Incident Response and Data Governance, and the current automated
checker expects canonical standard names. Ledger also plainly emits a human-facing
HTML surface, even though the shared applicability manifest currently carries a
stale `html: false` flag; declaring Accessibility N/A would be factually wrong.

## Decision

Ledger declares all thirteen current standards in its README. Eleven apply in
full or according to their documented tier. AI Evaluation is `N/A` because there
is no model/LLM component. Tier A observability controls are N/A within the
applicable Observability standard because ledger is a Tier C local-first
library/CLI. Incident Response applies to data, secret, consent, and integrity
events even without a maintained hosted service. Data Governance applies at L3
because contributor identity and potentially outing-sensitive content are core
data classes.

Accessibility remains Applies because ledger renders archive, contribution, and
steward HTML. The portfolio applicability manifest's stale HTML flag should be
corrected upstream; local conformance declarations remain truthful while that
shared metadata catches up.

The first model/LLM dependency reopens AI Evaluation before merge. A future
maintainer-operated hosted deployment re-evaluates Observability from Tier C to
Tier A before launch.

## Consequences

The README, incident runbook, data-governance policy/data card, responsible-tech
artifacts, and Definition of Done must evolve together. Reviewers can distinguish
whole-standard N/A decisions from tier-scoped controls without silently omitting
either new standard.
