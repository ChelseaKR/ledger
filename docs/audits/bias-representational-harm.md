# Bias & representational-harm review — ledger

Dated artifact for RTF-03 (`docs/ROADMAP.md`, Responsible Tech). Read alongside
[`../RESPONSIBLE-TECH-AUDITS.md`](../RESPONSIBLE-TECH-AUDITS.md) §B, which this
document supersedes as the committed artifact that section's "Gap" line named.

**Draft prepared: 2026-07-07 · Human reviewer: pending · Recheck cadence after
approval: per release.** This automated draft is not Chelsea Kelly-Reif's review.

## Scope and method

ledger has no model and makes no automated decision about a record's worth,
truth, or priority (confirmed in
[`../adr/0006-standards-applicability.md`](../adr/0006-standards-applicability.md):
AI-Evaluation is N/A). So the bias surface here is not "model bias" — it is the
**design choices that shape whose material gets preserved fully, described
fairly, found easily, and represented in governance.** This review reads the
relevant modules directly (`moderate.py`, `config.py`, `search.py`,
`metadata/dublincore.py`, `i18n.py`, `GOVERNANCE.md`) rather than restating the
threat model's adversary analysis, which already lives in
[`../THREAT-MODEL.md`](../THREAT-MODEL.md). It is a structural/design review
conducted by reading the code and docs as they exist on this date, not a
community consultation or user study — see "What this review is not," below.

**Segments considered:** contributors (who submit material), subjects (people
named or described in a record who may not be its contributor), viewers at each
grant level (anonymous, community, steward), and stewards (who exercise
discretion). A single person can hold more than one of these roles at once for
the same record.

## Findings

### 1. Content-warning vocabulary: two community-relevant tags are glossed but not seeded by default

`src/ledger/config.py`'s `_STARTER_CONTENT_WARNINGS` — the controlled vocabulary
a fresh archive is created with — has twelve entries: `violence`,
`sexual-violence`, `abuse`, `self-harm`, `suicide`, `medical`, `death`,
`incarceration`, `deportation`, `outing`, `hate-speech`, `substance-use`.

`src/ledger/i18n.py`'s `_cw_glosses` supplies plain-language glosses for
fourteen tags — the twelve above, plus `police-violence` and `deadnaming`.
Both of the extra tags are squarely on-topic for ledger's stated communities
(queer history, mutual aid, protest ephemera): deadnaming is a first-class harm
this project already reasons about elsewhere (the no-outing rule, `GOVERNANCE.md`
§ on named subjects), and police violence is a predictable subject of protest
and mutual-aid records. Because `_STARTER_CONTENT_WARNINGS` is what a new
archive actually ships with, a fresh community archive today does not offer
`police-violence` or `deadnaming` as a warning a steward can select from the
starter set — they exist only if a steward happens to know the tag string and
adds it, or if the vocabulary is later extended. The gloss dictionary and the
starter vocabulary have drifted apart; nothing enforces that every glossed tag
is also a starter tag or vice versa.

**Representational-harm risk:** low-to-moderate. The vocabulary is
community-editable (`GOVERNANCE.md` §3), so this is not a hard gate — but a
starter set is what most small archives will actually run with, and the two
missing tags are exactly the ones a community serving deadnamed or
police-brutality survivors would reach for first. A content warning a reader
needs and doesn't get is a real, if small, harm each time it happens.

**Recommendation:** add `police-violence` and `deadnaming` to
`_STARTER_CONTENT_WARNINGS`, or add a test asserting the two vocabularies stay
in sync (`set(_STARTER_CONTENT_WARNINGS) == set(_cw_glosses(...).keys())` up to
the fixed gloss set), so a future added gloss can't silently outpace the
starter list again.

### 2. Search relevance ranking favors verbose, English-fluent description

`src/ledger/search.py::_relevance` scores a record by term-frequency occurrence
in its indexed, disclosed text (`index_text`) — a deterministic, non-learned
count, which is the right choice for avoiding engagement-optimization or
popularity bias (there is no click-through feedback loop; the module docstring
in `RESPONSIBLE-TECH-AUDITS.md` §B is correct that there is no *model* here).
But a purely lexical, term-count ranking has its own quiet bias: a record with
a longer, more descriptive Dublin Core `description` and more subject terms
will out-rank an equally important record whose contributor wrote two
sentences — because they were rushed, because English isn't their first
language, because they were submitting from a phone at a protest, or because
the record concerns a subject the contributor was reluctant to elaborate on
(e.g., an outing-adjacent or self-incriminating detail deliberately left
terse). Thin metadata is not a proxy for a thin or unimportant record, but it
is what this ranking treats it as.

The module is already forthright about a related, narrower limitation:
`search.looks_non_latin()` exists specifically because "search is
Latin/English-biased" (its own docstring) — a query in Cyrillic, Arabic, Han,
Devanagari, or heavily accented Latin script may match incompletely, and the
function's purpose is to let a caller *disclose* that limitation to the
searcher rather than fail silently. That is good practice and this review
does not ask ledger to solve general multilingual full-text search; it asks
that the same honesty be extended to the verbosity bias described above,
which today is not surfaced anywhere.

**Recommendation:** no change to the ranking algorithm is warranted (a fuller
alternative — e.g., BM25 length normalization — is a legitimate future
enhancement but not a bias fix per se, since it would still reward the
*presence* of description over the *substance* of a record). Instead, document
the limitation next to `looks_non_latin`'s existing disclosure — a short note
in `search.py`'s module docstring and in `ARCHITECTURE.md`'s search section —
so a steward configuring browse/search defaults (e.g., whether "relevance" or
"newest" is the default sort) makes that choice knowingly. Consider defaulting
newly-created archives to `newest` rather than `relevance` for the plain browse
view, reserving relevance ranking for an explicit search query, so a record
with thin metadata is not systematically buried in ordinary browsing.

### 3. Descriptive metadata imposes no classification scheme — a deliberate, correct choice, worth stating as one

`src/ledger/metadata/dublincore.py` and the record schema
(`metadata/schema/record.schema.json`) treat `dc.subject` as free text with no
controlled subject-heading authority (no LCSH, no Homosaurus, no imposed
taxonomy). For a queer community archive this is the right default: standard
library subject-heading vocabularies have a long, documented history of
representational harm against LGBTQ+ subjects (obsolete or pathologizing
terminology, inconsistent treatment of gender identity, headings written by
institutions the communities ledger serves have reason to distrust). Not
imposing one avoids importing that harm wholesale. The trade-off, stated
honestly rather than left implicit, is that free-text subjects reduce
cross-archive discoverability and consistency (two contributors describing the
same event may use different terms, and nothing in ledger reconciles them).

**Recommendation:** no code change. Record this as a deliberate design
decision — not yet written down anywhere — in `ARCHITECTURE.md` or a short ADR,
so a future contributor doesn't "fix" the absence of a controlled vocabulary by
importing one without weighing the harm history above. A community that wants
a shared vocabulary can build one collaboratively (e.g., seeded from
[Homosaurus](https://homosaurus.org/), which is maintained by and for the
communities it describes) — that is a community governance decision, not a
default this project should make for them.

### 4. Moderation-pattern review has an appeal path but no periodic aggregate check

`GOVERNANCE.md` §3 and `moderate.py` give ledger a real accountability
mechanism per decision: every `ModerationAction` is justified, attributed, and
appealable, and the log is append-only. `RESPONSIBLE-TECH-AUDITS.md` §B
correctly notes there is no automated fairness *test* because there is no
automated *decision* — every warn/takedown/restore is a human steward action.
That is sound as far as it goes, but it also means the only way a systematic
pattern (e.g., warnings added more readily to records about one topic or
community than another, or takedown requests handled faster for some
contributors than others) would surface is if someone actually reads the log
looking for a pattern. Nothing today prompts that review to happen.

**Recommendation:** add a lightweight, offline aggregate check a steward can
run against a `ModerationLog` — counts of each action type by reason keyword
and by content-warning tag, with no identity or record content in the output
(`action_id`/`target_record`/`actor` only, consistent with the no-outing rule
already enforced by `to_dict()`) — as a `make` target or CLI subcommand, and
recommend running it at the same per-release cadence as this document. This
is a review aid, not a fairness verdict: a skew in the counts is a prompt to
ask stewards why, not proof of bias by itself.

### 5. Language coverage is EN/ES only; the gap is disclosed, but not from a representational-harm angle

`docs/I18N.md` documents EN/ES gettext coverage of the end-user surface
candidly and completely from an internationalization-conformance angle. From a
representational-harm angle specifically: a community whose primary language is
neither English nor Spanish (many of ledger's stated use cases — mutual aid and
queer organizing — happen in Indigenous, Southeast Asian, and other diaspora
languages) gets a browse/contribution UI in a language its members may not read
fluently, which pushes toward English-fluent members mediating access for
others — exactly the kind of access-gatekeeping ledger's consent and no-outing
design otherwise tries to avoid. This is already tracked as a roadmap item
(more UI languages, `docs/ROADMAP.md` and the ideation backlog) for capability
reasons; this review adds the representational-harm framing so the priority
reflects who is excluded, not only how many languages are missing.

**Recommendation:** no new tracked item — the roadmap already covers "more UI
languages." Cross-reference this document from that roadmap entry so the
prioritization discussion includes the harm framing above, not only coverage
percentage.

### 6. Governance structure avoids the most common representational failure mode by design

Positive finding, stated for completeness rather than left as an unstated
assumption: `GOVERNANCE.md` requires steward *decisions* to be justified,
attributed, and appealable, and separates "can administer" from "can unseal an
identity" (§1). It does not, however, require anything about the
*composition* of the steward body itself (no diversity, rotation, or term-limit
requirement). That is a deliberate scope choice stated in `GOVERNANCE.md`'s
own preamble — "a community adopting ledger is expected to adapt the specifics
... to its own size and needs" — and this review agrees it should stay a
community decision rather than a project-imposed rule. It is listed here so
that "governance representation" is not silently absent from a document titled
bias-and-representational-harm review; ledger's answer is "the community
decides," which this review endorses as correct for a self-hosted,
community-governed tool, rather than treating as an oversight.

## What this review is not

This is a structural review of ledger's own design surfaces, conducted by
reading the code and documentation on the date above. It is **not**:

- A community consultation. The people best positioned to identify
  representational harm in a specific archive's content-warning taxonomy,
  moderation patterns, or language needs are the community running that
  archive, not this project's maintainer reading source code. Any community
  deploying ledger should treat its own moderation log and its own vocabulary
  choices as the primary evidence, not this document.
- A live audit of a running archive's actual moderation history — no deployed
  ledger instance's real `ModerationLog` was reviewed here, both because this
  is the upstream software project (not an operator) and because doing so
  would itself require named-subject consent this review has no standing to
  obtain.
- A substitute for the aggregate moderation-pattern check recommended in
  Finding 4, once it exists — that tool, run by an actual steward against an
  actual log, is where a real pattern would be found.

## Summary of recommendations

| # | Finding | Action | Tracking |
|---|---|---|---|
| 1 | `police-violence`/`deadnaming` glossed but not in starter content-warning vocabulary | Add both to `_STARTER_CONTENT_WARNINGS`; add a vocabulary-parity test | New — see below |
| 2 | Relevance ranking favors verbose/English-fluent metadata, undisclosed | Document the limitation next to `looks_non_latin`; consider `newest` as plain-browse default | New — see below |
| 3 | No controlled subject vocabulary (deliberate) | Write down the decision and the harm history it avoids in `ARCHITECTURE.md`/an ADR | New — see below |
| 4 | No periodic aggregate moderation-pattern check | Add an offline, identity-free aggregate report tool | New — see below |
| 5 | EN/ES-only UI, harm framing missing from the existing roadmap item | Cross-reference this document from the i18n roadmap item | Existing roadmap item, cross-ref only |
| 6 | Steward-composition diversity left to community (by design) | No action — confirmed as correct scope | N/A |

Findings 1-4 are new, small, non-breaking follow-ups. They are intentionally
**not** bundled into this review commit, which is the dated review artifact
itself (RTF-03); each should be tracked as its own scoped change so review and
remediation stay separable and auditable, matching how the rest of this
repo's remediation history works (`docs/ROADMAP.md`'s own convention of one
gap, one remediating change).

## Sign-off

- **Draft prepared:** 2026-07-07
- **Human reviewer and verdict:** pending
- **Method:** structural/design review of the modules and docs named above;
  no live archive instance or community was consulted (see "What this review
  is not")
- **Next review due:** next release, or sooner if `moderate.py`,
  `config.py`'s content-warning vocabulary, `search.py`'s ranking, or
  `metadata/dublincore.py` change substantively
