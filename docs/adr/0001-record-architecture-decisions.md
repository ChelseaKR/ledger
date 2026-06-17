# 1. Record architecture decisions

## Status

Accepted

## Context

ledger makes a small number of consequential, hard-to-reverse decisions — how
contributor identity is kept apart from the record, which preservation standards it
commits to, which license governs it, how thin its dependency footprint is. These
decisions are load-bearing: the project's central safety promise and its claim to
survivability both rest on them. A decision of that weight that lives only in a
maintainer's head, a commit message, or a closed pull-request thread is effectively
undocumented. When the original author moves on — and in a volunteer, unfunded,
community-governed project that will happen — the reasoning is lost, and a later
contributor either re-litigates a settled question or, worse, unknowingly reverses a
decision that was made for a reason they never saw.

We want the *why* behind structural decisions to travel with the repository, in the
repository, in a format that is plain to read and cheap to write.

## Decision

We will record architecture decisions in **Architecture Decision Records (ADRs)**
using the format described by Michael Nygard.

- Each ADR is a short Markdown file in `docs/adr/`, numbered sequentially and named
  `NNNN-title-in-kebab-case.md`.
- Each ADR has the sections **Title**, **Status**, **Context**, **Decision**, and
  **Consequences**.
- **Status** is one of *Proposed*, *Accepted*, *Deprecated*, or *Superseded*. A
  superseded ADR is not deleted; it is marked superseded and points to the ADR that
  replaces it, and the replacement points back. The record of how thinking changed
  is itself worth keeping.
- ADRs are immutable once accepted, except to change their status. A new decision is
  a new ADR, not an edit to an old one.

This ADR is the first record and establishes the practice for all that follow.

## Consequences

- The reasoning behind structural decisions is preserved in the repository and
  versioned alongside the code it explains, so a new contributor can read why before
  changing what.
- Writing an ADR is a small, deliberate friction on consequential change, which is
  the intent: it makes reversing a load-bearing decision a visible, reasoned act
  rather than an accident.
- The ADR log doubles as onboarding material and as the public artifact a partnering
  institution can read to understand how the project decides things.
- ADRs add a modest maintenance habit. They are not a substitute for the
  architecture and threat-model documentation; they capture the decisions, not the
  full design.
