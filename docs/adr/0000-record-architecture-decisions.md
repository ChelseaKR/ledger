# 0000. Record architecture decisions

Status: Accepted

Date: 2026-07-11

Deciders: Chelsea Kelly-Reif

## Context

Architecturally significant decisions need a durable record of what was chosen,
why, and what it costs. Per the portfolio Documentation Standard, those decisions
belong in ADRs rather than being recoverable only from roadmap edits, pull-request
threads, or chat history.

## Decision

Use Architecture Decision Records in `docs/adr/`, numbered sequentially with a
four-digit prefix. Each ADR records Status, Context, Decision, Consequences, and
alternatives where useful. An accepted ADR is append-only: a later decision that
changes course adds a new ADR and marks the old decision `Superseded by NNNN`
rather than silently rewriting history.

Any change to a safety guardrail (including no-outing, consent, identity
separation, workflow permissions, or a coverage/security threshold) must link an
ADR in its pull request.

## Consequences

Decisions are reviewable, diffable, and preserved with the code. Contributors
must spend a little more time recording expensive-to-reverse choices, and the ADR
index must remain sequential.
