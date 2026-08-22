# 0013. AI at the edges: grounded finding aids and tier-respecting discovery, never in the trust boundary

Status: Accepted (owner-directed change of direction)

Date: 2026-08-22

Reopens: ADR 0009's AI Evaluation `N/A` ruling, as that ADR itself anticipated
("the first model/LLM dependency reopens AI Evaluation before merge").

## Context

Until this decision, ledger had zero AI: no model client, no LLM dependency,
nothing in `dependencies` or the dependency graph that could reach a model
provider. `README.md`'s standards table declared `AI Evaluation | N/A — no
model or LLM component`, correctly, because it was true.

The owner has directed a change of direction: add a real, grounded
description-and-discovery layer so the archive is genuinely usable by the
community that runs it — plain-language finding aids for a record, and
natural-language search — without weakening the one guarantee this project
exists for. `README.md` states that guarantee as the founding rule: **holding
a record can never out the person who made it.** Everything below is designed
so that rule constrains an AI feature exactly as hard as it constrains every
other read path, by construction, not by prompt wording.

The existing trust boundaries (`docs/THREAT-MODEL.md` section 2) already
establish the pattern this ADR extends rather than invents: `Record` carries
no identity; only `access.disclose` may construct a `DisclosedRecord`; a
steward's ability to see sealed content is independent of the ability to
resolve who contributed it. An AI layer is a new kind of reader. It gets no
new kind of access.

## Decision

Add an optional, opt-in AI layer, `src/ledger/ai/`, wired into exactly one
core entry point (`cli.py`'s `ai-describe`/`ai-ask` subcommands) and nowhere
else. Four design commitments make the founding rule structural rather than
aspirational:

### 1. Access control runs before the model, not around it

`ledger.ai.context.build_context(archive, record_id, grant, now)` is the ONE
function every AI feature calls, and its first line is
`archive.disclose(record_id, grant, now)` — the exact same disclosure
chokepoint (`ledger.access.policy.disclose`) every other read path (browse,
search, the JSON API, export) already uses. It raises `AccessDenied` under
precisely the condition every other read path does. The type it returns,
`GroundedContext`, is built exclusively from the resulting `DisclosedRecord`
and that record's PREMIS events (filtered to visible payloads as
defense-in-depth, even though PREMIS events are identity-free by
construction) — there is no field on it that *could* carry a withheld value
or a contributor identity, for the same structural reason `DisclosedRecord`
cannot. `ledger.ai.describe` and `ledger.ai.ask` accept only a
`GroundedContext` (or a mapping of them); neither accepts an `Archive`, a
`Grant`, or a raw `Record`. A prompt cannot leak what the type system will
not let it hold.

### 2. A verifier sits before display

The model is asked for a small set of claims, each naming the exact evidence
item (a field, a Dublin Core element, a payload, a PREMIS event, or a
verbatim quote) it comes from. `ledger.ai.grounding.verify_claims` checks
every citation against the disclosed evidence — not against the model's
say-so — before anything is returned. An unverifiable claim is withheld and
counted, never shown. The same verifier is the second, structural line of
defense against outing: since `GroundedContext` never carries an identity, a
model that hallucinates one can only ever produce an *ungrounded* claim,
which this verifier strips. On top of that structural guarantee, a narrow
deterministic backstop (`looks_like_identity_inference`, plus a
verbatim-name-grounding check and a cross-record-id check for the
multi-record `ask` path) refuses a claim whose language reads as an identity
guess or a cross-record link — proven, not just asserted, by the adversarial
suite named below. Preservation-metadata claims are verified the same way:
`ledger.ai.fixity_honesty.payload_fixity_status` derives one of exactly three
honest strings (`fixity has not yet been checked`, `fixity was verified [on
date]`, `a fixity check failed [on date]`) from actual PREMIS `FIXITY_CHECK`
events — never from the payload's mere presence — so a model cannot describe
an unrun or failed check as "verified" or "authentic" (this portfolio's
dominant defect, "absence rendered as a value," would be a claim about
evidentiary integrity here, not a cosmetic gap).

### 3. Search adds no record a viewer could not already reach

`ledger ai-ask` runs the existing deterministic `ledger.search.search` over
`Archive.browse(grant)` — the viewer's own already-tier-filtered listing —
*before* any model call, then re-derives a `GroundedContext` per candidate
record (re-running `Archive.disclose`, belt-and-suspenders against a caller
that filtered some other way). The AI layer never expands what a viewer can
reach; it narrates a subset of what `browse`/`search` already would have
shown them. When nothing matches, or every claim the model offered fails
grounding, the honest answer is "found nothing" (`found_anything=False`),
never a guess.

### 4. Everything above is proven by a committed eval harness, not merely designed

`tools/ai_eval.py` runs five suites — outing-refusal (adversarial phrasings:
direct, indirect, "just between us", researcher-framing, hypothetical,
aggregation across two individually-safe records, bilingual), consent-tier
leakage (including existence disclosure), preservation-metadata honesty,
citation grounding, and query structuring (vague/unanswerable cases scored on
refusal) — against a real model when credentials are available, and records
`not_run` rather than a fabricated number otherwise. Every result carries
`AIProvenance` (provider, model, prompt version, commit, date);
`AIProvenance.validate()` refuses to serialize an incomplete record, and
`tests/test_ai_eval_evidence.py` re-derives every number the write-up states
from the committed evidence with no network, exactly like
`docs/REAL-CORPUS-REPORT.md`'s relationship to
`tests/test_real_corpus_evidence.py`. Live numbers, the model used, and
provenance are recorded in `docs/AI-EVALUATION.md`.

The deterministic guardrails themselves (the grounding verifier, the
access-control gate, the identity-inference backstop) are proven separately
and unconditionally by `tests/test_ai_outing_refusal.py`,
`tests/test_ai_consent_tier.py`, `tests/test_ai_grounding.py`, and
`tests/test_ai_fixity_honesty.py` — hand-written adversarial fake clients
that always *try* to leak, run in CI with no network and no `anthropic`
install required, so the guarantee does not rest on a live model behaving
well on any given day.

### Consequential choices

- **Provider and model.** The public `anthropic` SDK, both backends it
  supports: Anthropic's direct API and Amazon Bedrock
  (`ledger.ai.client.build_client`). The code default model is
  `claude-sonnet-5`; `LEDGER_AI_MODEL` overrides it. This AWS account can
  invoke `global.anthropic.claude-sonnet-4-6` on Bedrock but not
  `claude-sonnet-5` (`AccessDeniedException` despite the availability API
  reporting it authorized) — `docs/AI-EVALUATION.md` records the live eval
  run against 4.6 via the override, with the code default left at Sonnet 5 as
  directed.
- **Not a runtime dependency.** `anthropic` is the opt-in `ai` extra
  (`pip install ledger-archive[ai]`), imported with the same guarded
  `try`/`except` pattern `ledger.print_edition` already uses for the optional
  `segno` package. Every deterministic preservation/access/browse path has
  zero import-time or runtime dependency on `ledger.ai`
  (`tests/test_ai_isolation.py` checks this structurally via AST inspection
  of the core modules, and functionally by importing `ledger.ai` with
  `anthropic` absent).
- **Credentials from the environment only.** `ANTHROPIC_API_KEY` or the
  standard AWS credential chain; never written to a file, never accepted as a
  `Config`/CLI value, never logged.
- **Cost controls from the start.** `ledger.ai.limits.RateLimiter` enforces a
  per-client-per-minute rate and a persisted, cross-process daily cap
  *before* any model call; exceeding either is handled exactly like a
  provider 429 — refuse the AI call, leave the deterministic path untouched.
  Prompt caching (`cache_control: ephemeral`) is applied to the system
  prompt, the part that repeats identically across calls for a given feature
  and prompt version.
- **Off by default.** `Config.ai.enabled` defaults to `False` — the same
  "default to narrowest, nothing opens by inaction" posture the rest of this
  config already takes for disclosure policy. A steward opts in explicitly.
  `tests/test_ai_isolation.py` and the existing (unmodified) preservation/
  disclosure test suite together are the proof that a zero-AI archive is
  byte-for-byte the pre-AI system.
- **CLI-only surface, for now.** Only `ledger ai-describe`/`ledger ai-ask`
  exist; the browse server and web UI are untouched by this decision. Wiring
  the AI layer into the served site is a deliberately separate, open decision
  (see Consequences).
- **No CI job calls a live model.** The deterministic guardrail tests run in
  the existing `gate` job with no new dependency and no new egress. The live
  eval (`tools/ai_eval.py`) is local/manual only — mirroring
  `tools/real_corpus.py`/`make real-corpus` and `make mutation` — so the
  Harden-Runner egress policy (issue #78) is **not** widened by this change;
  if a future decision wires a live model call into CI, that job's
  allowlist must name the provider endpoint explicitly and deliberately at
  that time, not inherit a pre-widened default.
- **Deployment is a DECISION NEEDED, not decided here.** No cloud
  infrastructure is provisioned. See Consequences.
- **The subprocessor question is a DECISION NEEDED, not decided here.**
  Sending archive content to a third-party model provider is a consent
  question the community's own policy may not cover, in a way it is not for
  most tools this portfolio ships. See Consequences and
  `docs/DATA-GOVERNANCE.md`.

## Consequences

- `README.md`'s standards table changes `AI Evaluation` from `N/A — no model
  or LLM component` to `Applies`, naming this ADR and `docs/AI-EVALUATION.md`.
  `docs/ARCHITECTURE.md` gains a section describing where the AI layer sits
  relative to the trust boundary. `docs/THREAT-MODEL.md` gains a trust
  boundary (disclosed content → model provider) and an adversary case (a
  compromised or curious model provider) in section 4, and a note in section
  4.7 that "found nothing" is the AI layer's own no-padded-listing behavior,
  mirroring `browse`'s existing guarantee. `docs/DATA-GOVERNANCE.md` gains a
  DECISION NEEDED entry for the subprocessor question. All AI output is
  labeled `AI-generated, unreviewed` (`ledger.ai.provenance.UNREVIEWED_LABEL`)
  and is never positioned as an authoritative description — a finding aid is
  a draft a steward or reader evaluates against the cited evidence, not a
  catalog record.
- A community that never sets `config.ai.enabled = true` runs exactly the
  system that existed before this ADR. Turning it on requires the `ai` extra
  installed and a real credential; without either, `ledger ai-describe`/
  `ledger ai-ask` refuse cleanly (`AIUnavailable`) rather than crash.
- **Deployment.** No terraform/CDK/CloudFormation is applied by this change.
  A hosted deployment that exposes AI features to real users needs: a cost
  envelope beyond the per-process rate limiter here (this is in-process only
  — a fleet of workers needs a shared limiter, e.g. backed by the existing
  storage locations or a small external store), abuse handling for a public
  endpoint, and the subprocessor and deployment decisions below resolved
  first. Tracked as an open decision, not a roadmap item with an owner yet.
- **The subprocessor question.** Every `ledger ai-describe`/`ledger ai-ask`
  call sends the requester's own disclosed evidence — never above their tier,
  never a contributor identity — to whichever provider `LEDGER_AI_BACKEND`
  resolves to. For most repositories in this portfolio that is an ordinary,
  reviewed vendor relationship. For ledger it is not automatically that: the
  people this archive serves are explicitly named in `docs/THREAT-MODEL.md`
  as people for whom "a third party learned this" can be a safety event, and
  `docs/GOVERNANCE.md`'s consent model was written before any third-party
  processor existed in this system at all. Whether a community's existing
  consent language covers "an AI feature you opt into may send the record's
  disclosed text to Anthropic/AWS for that one request" is a policy question
  this ADR does not resolve — it is opt-in and off by default specifically so
  that no existing archive is affected until a steward makes that call
  consciously, informed by this section.
- **Eval results are a living artifact, not a one-time claim.** A prompt
  change bumps `ledger.ai.prompts.PROMPT_VERSION`; the next `make ai-eval`
  run's evidence supersedes the prior one, named by provenance rather than by
  overwriting silently.

## Alternatives considered

- **A conversational agent that answers any free-text question.** Rejected.
  Unbounded Q&A widens the citation-grounding surface without bound, and
  makes an aggregation attack ("tell me about everyone who mentions mutual
  aid in the 1990s") much harder to keep grounded record-by-record. `ask` is
  scoped to the viewer's own already-filtered `browse`/`search` results for
  exactly this reason.
- **Let the model see the whole archive and self-censor via the system
  prompt alone.** Rejected outright. This is precisely the "prompt rule, not
  an architecture requirement" the mission exists to avoid; a model asked
  nicely not to look at something it can see is not a safety property.
- **Vector embeddings / a retrieval index over the full corpus.** Not needed
  at this scale and rejected for now on the same grounds as
  `permit-bearings` ADR 0004's equivalent decision: retrieval here is scoped
  by the viewer's own tier-filtered `browse`/`search`, which is inspectable
  and adds no new provider dependency; it can change later without changing
  the citation contract.
- **Ship the AI layer default-on.** Rejected. `default_policy` for a fresh
  record is `SEALED_UNTIL`, not `PUBLIC`, for the same reason: nothing new
  opens by inaction.
