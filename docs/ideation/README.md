# Ideation — large-scale fixes & expansions

**Drafted: 2026-07-01.** This folder is the third planning layer for ledger, produced
from a fresh full read of the codebase, docs, CI, and git history on 2026-07-01.

## How this relates to the existing plans

- **The original build spec** is the README's *Build plan* (Phases 1–4). Note: unlike
  most repos in this portfolio, ledger has no separate `docs/ROADMAP.md`; the README
  section plays that role and is largely realized.
- **[`../RESEARCH-ROADMAP.md`](../RESEARCH-ROADMAP.md)** (2026-06-30) is the
  research-backed backlog: remediations **RM1–RM12** and expansions **EX1–EX12**, of
  which RM4 (format identification / OAIS preservation planning, `src/ledger/preservation.py`)
  and EX1 (group succession, `src/ledger/succession.py`) already shipped.
- **[`../USER-RESEARCH.md`](../USER-RESEARCH.md)** (2026-06-30) is the synthetic
  persona panel behind that backlog.

This folder is deliberately **net-new**: nothing here restates an RM/EX item or a
Build-plan phase. Where an idea builds on an existing item it says so by ID and goes
beyond it. IDs here use a different namespace (**FIX-NN**, **EXP-NN**) to avoid
collision with RM/EX.

## Contents

| File | What it holds |
|---|---|
| [`01-deep-dive.md`](01-deep-dive.md) | Current-state assessment from the 2026-07-01 read: architecture, genuine strengths, structural debt actually observed, portfolio position. |
| [`02-large-scale-fixes.md`](02-large-scale-fixes.md) | FIX-01…FIX-12 — deep structural fixes (correctness, security, performance, operability), each grounded in specific files. |
| [`03-expansions.md`](03-expansions.md) | EXP-01…EXP-15 — expansion ideas in three horizons (deepen core / adjacent / transformative bets). |
| [`04-impact-and-sequencing.md`](04-impact-and-sequencing.md) | Impact×effort matrix, dependencies, a Now/Next/Later sequence beyond the existing roadmaps, and the human/legal/SME/real-data gates. |

## Honest framing

These are **ideas for evaluation, not commitments.** They come from one engineer's
close reading (no code was executed for this pass; test-suite and deployment claims
are as-documented, marked where unverified). Several items — anything touching
cryptography, legal process, or the safety of real at-risk contributors — carry
explicit gates in `04-impact-and-sequencing.md` and must not ship on the strength of
this document alone. That is the same discipline `RESEARCH-ROADMAP.md` applies to its
own synthetic findings, and the same portfolio ethos: defer and report honestly,
never fake.
