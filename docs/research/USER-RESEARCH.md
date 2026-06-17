# User research report — ledger community archive

**Study type:** moderated, task-based usability + trust study with a synthetic persona panel
**Build under test:** the live browse server + CLI on the reference implementation (post bug-fix pass)
**Panel:** 12 personas across every documented use case · **Date:** 2026-06-17
**Method:** each persona pursued a real task against a live, seeded instance
(`http://127.0.0.1:8011`, 5 records spanning all access levels, with grant headers for
anonymous / community / steward / steward-with-unseal), navigating with `curl` and the
`ledger` CLI, then sat for an 8–12 question in-depth interview. Findings were synthesized
by a lead-researcher pass and cross-checked against the code.

> **Note on method and honesty.** The panel is *synthetic* — personas are simulated, not
> recruited humans — so this report is a structured heuristic evaluation in persona form,
> not a substitute for testing with real community members (especially real contributors
> and disabled users). Its value is breadth, candor, and grounding: every finding traces
> to something the persona actually observed in the running system, and several were caught
> independently by three or more personas.

---

## 1. Executive summary

Ledger's hardest promise — *"holding a record cannot out the person who contributed it"* —
**is real and survives adversarial testing.** Its problem is the opposite of most projects:
the technical core earns trust from the most skeptical evaluators, while the **human-facing
experience does not yet let the people that promise is for actually use it.**

- **Task completion: 9/12 (75%). Mean satisfaction: 3.42/5** (median 3.5, no 1s).
- **The headline metric is a *credibility inversion*:** the highest trust came from the most
  adversarial/professional evaluators — funder **Tomas (5)**, security skeptic **Marcus (4)**,
  librarian **Priya (4)**, journalist **Riley (4)**, screen-reader user **Jordan (4)** —
  because the promises *passed tests*. The **lowest** satisfaction came from the protagonists
  the archive exists for: contributors **Mara (would not contribute alone)** and **Dana (2)**,
  field organizer **Lupe (2)**, elder **Eleanor (3)**. **Ledger currently earns the trust of
  the people checking it and frustrates the people meant to use it.**
- Completed sessions averaged **3.78**; the three failures averaged **2.33** — and the
  failures cluster precisely on two gaps: **you can't retrieve the content**, and
  **contributors have no agency** (no contribute path, no way to withdraw consent).
- **Two genuine integrity/honesty failures** (not polish) were found:
  1. A record labelled **"sealed until 2030" is served in full to stewards in 2026** with no
     enforcement, notice, or logging — caught independently by Aisha, Sam, and Riley. *(Fixed
     in this cycle — see §9.)*
  2. The documented **"revocable consent" promise has no mechanism on the site at all.**

The bones are ethical and unusually honest. The missing floor is a human-readable front door,
enforced seals, contributor agency, and the ability to actually open a record. **Most fixes
are presentation and small features, not re-architecture.**

---

## 2. The panel

| Persona | Use case / archetype | Task | Done | Sat |
|---|---|---|:--:|:--:|
| **Mara**, 26, closeted trans organizer | At-risk **contributor** | Decide if it's safe to contribute; can I publish a story but seal names, and change my mind? | ⚠️* | 4 |
| **Dr. Aisha Okoro** | **Historian / provenance auditor** | Find, judge, and cite records for scholarship | ✗ | 3 |
| **Sam** | Volunteer **steward / moderator** | Do moderation work; confirm I never see an identity I wasn't granted | ✓ | 3 |
| **Priya** | Preservation **librarian** (institutional adopter) | Evaluate against BagIt/PREMIS/DC/OAIS, ACR, governance | ✓ | 4 |
| **Jordan** | Blind **screen-reader + keyboard** user | Browse and read a record non-visually (WCAG 2.2) | ✓ | 4 |
| **Lupe** | **Mobile** mutual-aid organizer, slow link | Pull the pantry runbook fast in the field | ✗ | 2 |
| **Marcus** | **Security skeptic** | Make the site leak an identity or sealed value | ✓ | 4 |
| **Eleanor**, 71 | **Low-confidence elder** | Find a 1991 Pride flyer she remembers | ⚠️ | 3 |
| **Kenji** | **Non-native English** reader | Navigate + understand safety labels (localizability) | ⚠️ | 3 |
| **Riley** | Ethics-minded **journalist** | Find publishable material without exposing anyone | ✓ | 4 |
| **Dana** | **Contributor** returning to revoke consent | Tighten access / request takedown of own record | ✗ | 2 |
| **Tomas** | **Funder / grant evaluator** | Judge credibility + sustainability + governance | ✓ | 5 |

\* Mara completed her *evaluation* but concluded she **would not contribute alone from the
site as it presents today** — so true contributor-onboarding success is weaker than 75%.

---

## 3. What genuinely worked (keep these)

These are the load-bearing strengths; do not regress them.

- **The no-outing guarantee is structural, not honor-system, and it held under attack.**
  Marcus: *"You can't leak a field that doesn't exist — the record literally has no place to
  put a contributor's identity."* Sam grepped every view including the unseal grant and *"the
  vault never opened."* Tomas: *"The promise wasn't prose; it was a passing test."*
- **Sealed records are non-enumerable** — real id, fake id, and nonexistent id all return
  byte-identical 404s. Marcus: *"That closes the oldest trick in the book."*
- **Authorization is unforgeable and server-side.** Bogus/`admin`/injection `X-Ledger-Grant`
  values all *"fell straight back to anonymous."* Fails closed.
- **Fixity is a guarantee, not a claim.** Priya appended 8 bytes to a payload and the audit
  failed with a nonzero exit; real RFC 8493 BagIt with dual manifests and PREMIS.
- **Radical honesty as a trust signal.** The ACR/VPAT marks ten rows *"Partially Supports"*
  instead of all-green. Marcus: *"people who won't lie about their focus ring probably aren't
  lying about identity_ref."*
- **Genuinely strong screen-reader/keyboard accessibility.** Jordan: *"I trust this archive
  more than almost any site I navigate"* — working skip link, clean heading order, captioned
  table with scope, strong focus-visible ring, reduced-motion, zero JavaScript.
- **Privacy hygiene that lowers real risk:** strict CSP, no cookies, no JS, no-referrer.
  Mara: *"The page itself can't snitch on me."*
- **Honest redaction** that names withheld fields without exposing them. Riley: *"the Withheld
  box tells me protected material exists without handing it to me."*
- **Governance enforced in code:** steward ≠ identity-unsealer, maintainer ≠ steward, every
  privileged action requires a logged reason, appeals are first-class.

---

## 4. The four critical findings

### C1 — The safety story is invisible to the at-risk humans it is built for
*(Mara, Lupe, Eleanor, Marcus, Riley, Dana, Tomas — 7 personas)*
There is **no public contribute path and no human-readable explanation** of how protection
works. Everything Mara learned about her own safety, she learned from **developer command-line
help**. Marcus: *"the strongest reassurance is completely invisible to the people who most need
it."* A scared person on a phone sees an unverifiable footer promise, no way to submit, and
leaves.
→ **Ship a plain-language "How this protects you / How to contribute" page, an
About/Governance page naming operators and how stewards are vetted, and a web version of the
no-outing proof (show, don't tell).**

### C2 — Contributor agency is missing: "revocable consent" has no mechanism
*(Dana, Mara, Riley, Sam)*
Every consent/contact page **404s**. The takedown machinery exists but is *"bolted to the
steward side."* Dana: *"Revocable was true in the room. It is not true on the website."*
Mara: *"I can't log in and click unpublish at 2am when I panic."* For an archive built on
consent, this is the gap between a principle and a practice.
→ **Add a contributor-facing "Are you the contributor? Manage / withdraw consent" request flow
on every record and the footer — auditable, acknowledged, with claim-token verification, a
stated response time, and an emergency path — plus a steward-side console to action it.**

### C3 — "Sealed until 2030" is a label, not a lock *(integrity failure)*
*(Aisha, Sam, Riley — caught independently)*
A record explicitly embargoed **"until 2030" is served in full to stewards in 2026** with no
enforcement, notice, or logging. Aisha: *"an embargo must live in code, not in a string… it
poisons trust in every other sealed label."* A vulnerable contributor may have been promised a
lock that was actually a string.
→ **Enforce temporal seals in the access layer for *all* tiers until the date passes, with an
explicit, logged break-glass exception and an on-screen "sealed until \<date\>" notice.**
**✅ Fixed this cycle — see §9.**

### C4 — Records cannot be retrieved: you can read the label but not open the can
*(Lupe, Eleanor — the two lowest, both failed)*
The catalogue entry shows, but the **actual file content cannot be opened, viewed, or
downloaded**; the filename is an inert false affordance. Lupe: *"I tapped the filename.
Nothing. That's a design lie."* Eleanor: *"It dangled the flyer in front of me and then
wouldn't let me have it."*
→ **Provide a real download/inline-render link for permitted content (with a fixity-ok badge);
where an original artifact exists, surface the scan with the transcription clearly labelled.**

---

## 5. Cross-cutting themes (ranked)

| # | Theme | Severity | Personas |
|---|---|---|---|
| T1 | No public contribute path / no human-readable safety explanation | **Critical** | 7 |
| T2 | Contributor consent/takedown agency is absent | **Critical** | 4 |
| T3 | Temporal embargo not enforced for privileged users | **Critical** | 3 |
| T4 | Record file content cannot be retrieved | **Critical** | 2 |
| T5 | Withheld / sealed / missing / no-match states conflated & ambiguously worded | High | 6 |
| T6 | Search ignores subject/provenance (Dublin Core is "decorative") | High | 4 |
| T7 | Steward accountability gaps: unlogged privileged reads, CLI-only moderation, hidden tier boundary | High | 3 |
| T8 | "Sealed" has two strengths; content-vs-identity distinction is invisible | High | 4 |
| T9 | English-only, no language affordance, jargon at the trauma-decision point | High | 3 |
| T10 | "Status" nav dumps raw JSON; the real fixity proof stays buried | High | 4 |
| T11 | Scholarly metadata gaps (rights/provenance/citation/persistence, no OAI-PMH) | Medium | 3 |
| T12 | Inference side-channels: per-grant counts + public redaction labels leak *what* was deposited | Medium | 1 (deep) |
| T13 | Content-warning interstitial not announced / focus not managed; can't add CW post-publication | Medium | 2 |
| T14 | Bus factor: single maintainer, pre-1.0 (records de-risked by plain BagIt) | Medium | 2 |

---

## 6. Accessibility scorecard — **B−**
*(strong for screen-reader/keyboard; failing for linguistic access and content retrieval)*

**Genuinely strong (Jordan, 4/5):** first-focusable skip link to `#main`; `lang`; unique
titles; proper landmarks; one `h1` with clean heading order; labelled search; captioned table
with `scope`; descriptive links; no positive `tabindex`; strong `:focus-visible`;
reduced-motion; **zero JavaScript** — *"the calmest thing a screen-reader user can be handed."*

**Gaps by dimension:**
- **Status messages / focus management (WCAG 4.1.3, 2.4.3) — FAIL.** The content-warning
  interstitial isn't announced and focus isn't moved; "Proceed" doesn't manage focus. Highest
  stakes — it defeats the warning for the people who most need it.
- **Developer endpoints (1.3.1, 4.1.2) — FAIL.** "Status" serves raw JSON: a wall of
  punctuation to AT, and frightening to Eleanor (*"have I broken it?"*).
- **Linguistic access — FAIL.** English-only, no language selector, no `Accept-Language`, terse
  hyphenated CW tokens and uncommon vocabulary ("Withheld", "Proceed") at the
  should-I-view-this-trauma moment. The team's a11y lens has been visual/motor, not linguistic.
- **Content retrieval — FAIL.** The artifact can't be opened (see C4).
- **Cognitive/orientation — PARTIAL.** Ambiguous empty states read as "you are not permitted";
  opaque hash URLs hurt shareability; no `aria-current` on nav.
- **Low-bandwidth — PARTIAL.** Thoughtful no-JS/tiny payloads, but no `Cache-Control`/`ETag`
  and no compression, undoing the gains on a metered link.
- **Contrast — PARTIAL (self-disclosed).** Documented but not independently certified; the ACR
  already admits this audit is owed.

> Fix focus/announce on the CW, kill the JSON Status page, add a language layer + plain-language
> glosses, and ship content retrieval, and this becomes an **A−**.

---

## 7. Trust & safety assessment

**Does the mission-critical no-outing promise feel real? YES at the machinery level, verified
by the hardest skeptics; NOT YET at the experience and contributor-agency level.**

**Real where it matters most (contributor identity).** Four adversarial/professional evaluators
tried to break it and could not (Marcus, Sam, Riley, Tomas). The served record type has no
identity field; the grant header can't be forged or reflected; real/fake ids are
indistinguishable 404s; sealed values aren't searchable; steward ≠ unsealer is sound. *This is
a higher class of safety than a footer line.*

**But three things stop it feeling real to the people it protects:**
1. **The seal is two different strengths and the difference is hidden.** Contributor *identity*
   is encrypted; sealed *content* (real_names, location) sits in clear text in the bag and is
   readable by ordinary stewards and anyone with raw filesystem/replica access. The honesty is
   in the docs; it is **not on the surface** where the scared contributor stands. Mara would
   *"seal real, dangerous information under a false sense of total secrecy."*
2. **Temporal seals aren't enforced** (C3) — a contributor may have been promised a lock that
   is a string. *(Now fixed — §9.)*
3. **Contributor agency is absent** (C2) — *"the footer's 'identities are never shown here'
   quietly downgrades 'you stay in control' to 'trust us to hide it.'"* (Dana)

**Accountability of the humans behind the curtain is thin.** No on-site About/governance page
names operators or how stewards are vetted (Mara: *"an anonymous, unaccountable steward who can
read sealed names is a dealbreaker"*). Stewards take accountable actions but **cannot read the
audit log** of them.

**Metadata side-channels chatter about *what* was deposited** (Marcus): per-grant record counts
reveal and time the growth of the sealed collection; public redaction labels enumerate which
records hide names/locations ("targeting metadata"); a faint timing tell partially undermines
the identical-404; the `Server` header leaks the runtime version. *"Safe for who you are;
slightly chatty about what you deposited."*

> **Net verdict (multiple personas, same conclusion):** *"I'd trust them to keep my secret
> today, but not that I can change my mind tomorrow"* (Dana). *"The technology earned a maybe
> yes; the presentation earned a not like this"* (Mara). The promise is structurally
> trustworthy for **who** you are; not yet for **what** you deposited, for staying in
> **control**, or for the scared non-technical person who can't even find it.

---

## 8. Prioritized roadmap

| Pri | Effort | Recommendation |
|---|---|---|
| **P0** | medium | **Enforce temporal seals** for all tiers until the date passes, with logged break-glass + on-screen notice. *(✅ done — §9)* |
| **P0** | large | **Contributor agency**: per-record "manage/withdraw consent" request flow with claim-token verification, acknowledgement, response-time + emergency path. |
| **P0** | medium | **Make content retrievable**: real download/inline-render for permitted files + fixity badge; surface originals where they exist. |
| **P0** | medium | **Web-visible safety surface**: plain-language "how this protects you / how to contribute" + About/Governance naming operators; disclose that stewards can read sealed *content*; web no-outing proof. |
| **P1** | small | Replace raw-JSON "Status" with an accessible HTML health page; per-record fixity/"last verified" badge; keep JSON at `/healthz`. |
| **P1** | medium | Fix the content-warning interstitial for AT (announce + manage focus); allow stewards to add/edit content warnings after publication. |
| **P1** | small | Disambiguate + humanize state copy ("no matches" vs "restricted / how to request" vs "sealed until \<date\>"), preserving public non-enumerability; inline "withheld" marker per field. |
| **P1** | medium | Index all Dublin Core fields with subject/date/type facets + browsable subjects; non-English query hint; align marketing claims with shipped capability. |
| **P1** | large | Steward web console: consent change / takedown / post-publication CW (actor+reason), readable audit trail, time-seal interstitial, "sealed above your access" panel. |
| **P2** | large | Language layer (selector + `Accept-Language` + translatable strings); plain-language glosses for every CW tag; friendlier control words ("Continue", "English" not "en"). |
| **P2** | medium | Close metadata side-channels: don't vary visible counts by grant; generalize public redaction labels; equalize denied-id vs 404 timing; suppress `Server` version. |
| **P2** | large | Scholarly/interoperability metadata: per-record citation block (rights/accession/provenance/date), persistent-identifier policy, minimum-metadata profile at ingest, per-record JSON + OAI-PMH + sitemap. |
| **P2** | large | At-rest encryption for sealed payloads + a "seal from everyone, including stewards" tier; make content-redaction vs contributor-identity distinction explicit in the UI. |
| **P3** | small | Low-bandwidth perf: `Cache-Control` + `ETag` on the stylesheet; gzip/brotli on HTML/CSS. |
| **P3** | medium | Continuity: co-maintainer/security-response plan; "adopting institution" deployment checklist; commission the owed independent WCAG contrast audit; `aria-live` result count; reference-implementation banner. |

---

## 9. Acted on this cycle

**C3 / P0 — temporal seal enforcement (fixed).** `ledger.access.is_visible` now treats a
`SEALED_UNTIL` field that carries an `unseal_at` date as a *temporal* seal that binds **every**
tier, including stewards, until the date is reached — fail-closed, matching the project's
disclosure thesis and the promise made to contributors. An indefinite seal (no date) remains an
access-level seal a steward may read, as the threat model documents. A steward can still see the
record *exists* (its existence is governed by the record-level default policy) but the embargoed
field is reported under "Withheld" until the date. Covered by a new regression test. The
remaining P0s (contributor agency, content retrieval, the web safety surface) are feature work
scoped in the roadmap above.

---

## 10. Persona snapshots

- **Mara (contributor) — would not contribute alone.** *"There is no front door. Everything I
  learned about protecting myself, I learned from a developer's command-line help. I am not
  technical."* Trusts the machinery, not the experience.
- **Dr. Okoro (historian) — failed.** Search ignored the subject heading literally attached to
  the record; the 2030 embargo opened to stewards; no rights/provenance to cite. *"Promising
  bones, not yet a dependable scholarly repository."*
- **Sam (steward) — "it protects the community; it doesn't yet protect against the moderator."**
  Verified the vault never opened, but can't read the audit log, can't add a CW post-publication,
  and the 2030 seal opened with no notice.
- **Priya (librarian) — would defend a supervised pilot.** Verified real BagIt/PREMIS/DC, fixity
  catching a tampered byte, vaulted identity, 173 green tests. *"The rare activist tool that
  meets the professional bar it claims to."* Conditions: full-disk encryption, two-steward
  governance, independent contrast audit, treat facets/OAI-PMH as roadmap.
- **Jordan (screen reader) — "I trust this archive more than almost any site I navigate."*
  Reservations are polish (CW not announced, JSON Status), not integrity.
- **Lupe (mobile organizer) — failed.** *"It shows me the label on the can but won't let me open
  the can."* Safe to trust with privacy; not yet usable at noon when the doors open.
- **Marcus (security) — "safe for who you are; slightly chatty about what you deposited."**
  No-outing held; close the count/redaction-label/timing side-channels.
- **Eleanor (elder) — dented trust.** The footer promise landed hard; the JSON "Status" page
  made her fear she broke something; hash URLs felt like "the back rooms."
- **Kenji (non-English) — "I trust their intentions more than my own understanding."** The
  safety-critical words are the hardest, with no gloss or other language.
- **Riley (journalist) — ethical to publish from, at the public layer.** *"For once an archive
  actually keeps its word in the data, not just the copy."* Watchful of the unaccountable
  privileged layer.
- **Dana (returning contributor) — failed.** *"Revocable was true in the room. It is not true
  on the website."*
- **Tomas (funder) — fund, with people-focused conditions.** *"The central promise survives
  being run… an executable, passing audit."* Chief concern: bus factor (one maintainer, pre-1.0).

---

*Appendix — limitations: synthetic personas (not recruited humans); a single seeded dataset;
evaluation via `curl`/CLI rather than real assistive-technology hardware or live devices.
Treat as a prioritization instrument and a hypothesis generator, to be confirmed with real
community members — above all real contributors and disabled users — before P0/P1 work ships.*
