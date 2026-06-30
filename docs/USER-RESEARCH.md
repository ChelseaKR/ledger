# User Research — Synthetic Persona Panel & Simulated Interviews

> [!WARNING]
> **These personas and interviews are synthetic.** They were generated as a
> structured brainstorming device — *not* conducted with real people. No real
> contributor, organizer, steward, librarian, or funder said any of this. The panel
> exists to pressure-test ledger from every stakeholder angle at once; it is **not**
> evidence of demand, adoption, or safety, and it does **not** substitute for real
> discovery with the people the archive is for. Treat every "quote" as a hypothesis
> to validate, not a finding. The synthetic personas are labelled exactly the way
> ledger labels its synthetic test fixtures and sentinel identities
> (`tests/fixtures/`, `tests/test_no_outing.py`) — a fiction used to exercise a real
> system so that a test for a leak can never itself become a leak.
>
> **Last assembled: 2026-06-30.**

This is a *second, independent* panel. A first synthetic study
([`docs/research/USER-RESEARCH.md`](research/USER-RESEARCH.md), assembled 2026-06-17)
ran 12 personas against an earlier build, surfaced four critical gaps, and its entire
roadmap has since been implemented and tested (that study's §9). This panel therefore
interviews against the **post-remediation reference implementation** — contributor
agency, content retrieval, enforced temporal seals, the web safety surface, i18n,
OAI-PMH, an absolute `SEALED` tier with at-rest encryption, and the steward console
already exist and are treated here as *real features*, not wishes. The job of this
round is the **next horizon**: what a stakeholder cast hits *after* the obvious floor
is in, and where the project's own honest residual risks (the threat model's §4 and
the deployment checklist) become the next thing to harden. Findings feed
[`RESEARCH-ROADMAP.md`](RESEARCH-ROADMAP.md).

---

## Method

- **Frame.** ledger's real stakeholder map, drawn from the README, the threat model,
  the governance model, and the adopting checklist: people who **contribute** records
  whose safety is paramount; people who **steward and govern** the archive; people who
  hold it to **preservation standards**; people who **reuse and research** it; people
  who **assure** it or who are the **adversary** the design must defeat; and people who
  **operate and sustain** it — including the community *as a collective governor* and
  the owner/maintainer.
- **Protocol.** Each persona was given a goal, walked through the live surfaces and
  commands they would actually touch (the stdlib browse server, the `ledger` CLI, the
  bags and metadata on disk, the governance and threat-model docs), and asked four
  things: what they would value today, where they would stall, what they would want
  next, and the one thing that would make them adopt — or walk.
- **Mapping discipline.** "Values today" points **only at features that exist in the
  repo** (named in `src/ledger/…`, the CLI, the docs, or the prior study's shipped §9).
  "Gets stuck" points at **documented** gaps and residual risks — chiefly the threat
  model's stated residuals (`docs/THREAT-MODEL.md` §4–§5), the ACR's candid
  `Partially Supports` rows (`docs/accessibility/ACR.md`), and the operational controls
  the adopter, not the code, must supply (`docs/ADOPTING.md`). Nothing about the
  project's history or the people it serves was invented.
- **Synthesis.** Frictions become **R**emediations, wishes become **E**xpansions, each
  tagged `[corroborates …]` where it triangulates an existing doc/roadmap item or
  `[NET-NEW]` where the panel surfaced it. Effort scale: **S** ≈ an afternoon · **M** ≈
  a day or two · **L** ≈ a week or more.

### Research basis (real sources, accessed 2026-06-30)

The personas are fiction; the pressures they voice are not. Each is grounded in the
documented experience of real community and preservation practice.

- **The preservation playbook ledger builds on.** OAIS reference model — [ISO
  14721](https://www.iso.org/standard/57284.html), [oais.info](http://www.oais.info/);
  packaging — [BagIt / RFC 8493](https://www.rfc-editor.org/rfc/rfc8493.html) and
  [BagIt at the Library of Congress](https://blogs.loc.gov/thesignal/2019/04/bagit-at-the-library-of-congress/);
  preservation events — [PREMIS Data Dictionary v3.0](https://www.loc.gov/standards/premis/v3/);
  description — [Dublin Core Element Set](https://www.dublincore.org/specifications/dublin-core/dces/)
  / [ISO 15836-1:2017](https://www.iso.org/standard/71339.html); fixity practice —
  [NDSA Levels of Digital Preservation](https://www.ndsa.org/publications/levels-of-digital-preservation/),
  [NDSA fixity guidance](https://www.digitalpreservation.gov/documents/NDSA-Fixity-Guidance-Report-final100214.pdf),
  and the [DPC Digital Preservation Handbook on fixity and checksums](https://www.dpconline.org/handbook/technical-solutions-and-tools/fixity-and-checksums).
- **Community / post-custodial / participatory archiving and consent-based access.**
  [Michelle Caswell](https://en.wikipedia.org/wiki/Michelle_Caswell)'s critical and
  community-archives scholarship; the [South Asian American Digital Archive](https://www.saada.org/)
  and its [post-custodial, participatory model](https://texlibris.lib.utexas.edu/2021/05/participatory-community-archiving-the-south-asian-american-digital-archive/);
  consent-based, community-defined access via [Mukurtu CMS](https://mukurtu.org/about/)
  and [Traditional Knowledge Labels](https://mukurtu.org/support/traditional-knowledge-labels-faq/).
- **Queer / LGBTQ community archives and their preservation precarity.** The
  [Digital Transgender Archive](https://www.digitaltransgenderarchive.net/) and its
  [reliance on a third party (Portico) for long-term preservation](https://daily.jstor.org/preserving-history-at-the-digital-transgender-archive-with-portico/);
  the all-volunteer [Lesbian Herstory Archives](https://en.wikipedia.org/wiki/Lesbian_Herstory_Archives)
  and the [documented difficulty of sustaining it as a volunteer organization](https://pubmed.ncbi.nlm.nih.gov/26914823/);
  [ONE Archives](https://libraries.usc.edu/article/safeguarding-future-worlds-largest-lgbtq-archive),
  the world's largest LGBTQ+ repository, requiring a
  [$4.2M endowment to safeguard its future](https://today.usc.edu/one-archives-at-the-usc-libraries-receives-4-2-million-in-gifts-endowing-curator-director-positions/);
  the DIY [Queer Zine Archive Project](https://en.wikipedia.org/wiki/Queer_Zine_Archive_Project),
  whose physical collection lives in its founders' home and
  [only a fraction of which is digitized](https://guides.libraries.indiana.edu/zines/qzap).
- **Risk to at-risk records — outing, surveillance, compulsion.** Bergis Jules /
  Documenting the Now, [Archiving Protests, Protecting Activists](https://medium.com/documenting-docnow/archiving-protests-protecting-activists-e628b49eab47);
  the [ethics of activist social-media archives](https://www.researchgate.net/publication/325666515);
  [archival ethics and surveillance](https://lucidea.com/blog/archival-ethics-and-surveillance/).
- **Loss of mutual-aid / organizing knowledge when groups fold.** [Mutual-aid groups
  pondering their own future](https://www.pbs.org/newshour/nation/mutual-aid-groups-ponder-future-of-community-based-help);
  [sustaining mutual-aid groups beyond the crisis that formed them](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC8563598/);
  [the unincorporated, volunteer-led structure of most mutual aid](https://cunyurbanfoodpolicy.org/news/2023/08/22/mutual-aid-101-history-politics-and-organizational-structures-of-community-care/).
- **Minimal computing / low-resource preservation.** [GO::DH Minimal Computing](https://sas-dhrh.github.io/dhcc-toolkit/toolkit/minimal-computing.html)
  and [Risam & Gil's "The Questions of Minimal Computing"](https://digitalhumanities.org/dhq/vol/16/2/000646/000646.html)
  — computing under real constraints of hardware, power, network, and money, for the
  exact communities most often left out of the digital record.

---

## Persona roster

| # | Persona | Group · archetype | Primary goal | Top friction |
|---|---|---|---|---|
| A1 | **Río** — not-out trans contributor | Contribute · at-risk contributor | Publish a story while sealing name, place, and self; keep the right to change their mind | Has to *believe* the seal before trusting it with something dangerous |
| A2 | **Teodora** — undocumented mutual-aid organizer | Contribute · at-risk contributor | Preserve a record of a raid + a know-your-rights runbook without it becoming evidence | "Sealed content" is clear-text in the bag; key + vault could be seized together |
| A3 | **Margit**, 74 — oral-history narrator | Contribute · embargo | Tell the whole story now, sealed until after she's gone or the climate changes | Trusting a *dated* embargo binds even stewards; format obsolescence of her audio |
| A4 | **Devon** — volunteer community archivist / steward | Contribute · steward | Ingest donated boxes of zines + flyers, warn, audit, moderate accountably | Backlog is physical; no format triage; steward web actions still partly CLI |
| A5 | **Bex** — mutual-aid organizer preserving runbooks | Contribute · continuity | Keep the pantry/bail-fund runbooks alive when the collective folds | No "hand this to the next crew" succession path; runbook version history is manual |
| B1 | **Casa Abierta Collective** — the community *as governor* | Govern · collective | Decide who stewards, what comes down, how disputes end — by documented process | Governance is a doc + code primitives; the *deliberation* still lives off-platform |
| B2 | **Marisol** — a person named in someone else's record | Govern · named subject | Have a voice about a record that describes her but that she didn't contribute | Her objection is a request, not a veto — correct, but she must trust a steward |
| C1 | **Dr. Halloran** — preservation librarian / standards steward | Preserve · institutional | Judge ledger against OAIS/BagIt/PREMIS/DC for a supervised deposit/pilot | No format identification, normalization, or PREMIS Rights/PID — bit-fixity ≠ planning |
| C2 | **Nneka** — diaspora partner-archive steward | Preserve · portability | Export the archive and re-host it elsewhere with no lock-in | Bags aren't encrypted at rest by ledger; transport/replication security is on her |
| D1 | **Dr. Okafor** — historian / researcher | Reuse · access + cite | Find, judge, and cite records responsibly | Citation/provenance/PID thin; no relationships between records; no map/timeline |
| D2 | **Wren** — community member, phone in the field | Reuse · mobile/low-bandwidth | Pull the right runbook fast on a metered connection | Field reading is text-dense; media has no captions; no offline copy |
| D3 | **Amir** — multilingual / non-native-English reader | Reuse · linguistic access | Read the archive and the *safety labels* in a language he's sure of | Only en/es; safety-critical words are the hardest; no RTL |
| E1 | **Kestrel** — security / threat reviewer | Assure · red-team | Make a public surface reveal an identity or a sealed value | Can't — and says so; residue is timing/correlation, not a structural hole |
| E2 | **Sable** — blind screen-reader + keyboard user | Assure · accessibility | Browse and read non-visually, trust the content warnings | Contrast/structure gate is a *floor*; CW announce + transcripts still partial |
| E3 | **"The Summons"** — institution / platform / subpoena | Assure · the adversary | Compel, seize, or trick the archive into producing who made a record | Defeated at the application layer — but operational missteps re-open the door |
| F1 | **Imani** — movement-infrastructure funder | Operate · backer | Fund credible, governed, *survivable* safety infrastructure | Single maintainer, pre-1.0; wants the bus factor reduced before scaling adoption |
| F2 | **The maintainer** — owner/operator of the codebase | Operate · maintainer | Keep the guarantees true while the project grows past one person | Everything routes through one person; co-maintainer and audit are goals, not facts |
| F3 | **Sasha** — small-collective self-host operator | Operate · adopter | Stand it up on one cheap box and not get anyone hurt | The hardest controls (key off-box, FDE, TLS, off-box replicas) are *her* job |

---

## Interviews

Each card is the simulated interview compressed to five lines: **Goal · Values today
(real features) · Gets stuck · Wants next · Adopts / walks.**

### Group A — Contribute & Stand By (the people the archive is *for*)

#### A1 — Río, not-out trans contributor
- **Goal.** Publish one painful, true story so it survives, while never letting the
  archive say who wrote it — and keep the ability to pull it back.
- **Values today.** The no-outing guarantee is *structural*, not a promise: `Record`
  carries only an opaque `identity_ref`, `DisclosedRecord` has no identity field at all,
  and the encrypted `IdentityVault` is the only `ref → person` map (`identity.py`,
  `models.py`). Per-field selective disclosure lets one record be a public story with a
  sealed name and a sealed location (`access/policy.py`). The plain-language `/how-it-works`
  and `/proof` pages and the `/record/{id}/consent` flow mean she can read the protection
  and exercise it without a developer. The page itself can't snitch: no cookies, no JS,
  strict CSP, `no-referrer` (`server.py`).
- **Gets stuck.** She has to *believe* the seal before she'll feed it something
  dangerous, and belief is hard for a non-technical person on a phone. The web no-outing
  proof shows the property but doesn't yet feel like *her* record being protected; the
  contribute front door is functional but spare for a scared first-timer.
- **Wants next.** A contributor-facing, mobile-first contribute experience with
  on-device redaction preview before anything uploads; a "show me my record as a
  stranger sees it" view; an emergency one-tap "seal everything / withdraw" she can hit
  at 2am. *(echoes the prior study's contributor-agency theme, now built — deepen.)*
- **Adopts if** the experience makes the structural guarantee *legible* to her. **Walks
  if** the only convincing proof lives in a test file she'll never read.

#### A2 — Teodora, undocumented mutual-aid organizer
- **Goal.** Preserve a contemporaneous record of an ICE raid and a know-your-rights
  runbook so the community keeps both — without either becoming evidence against anyone.
- **Values today.** The threat model is written for exactly her adversary: device
  seizure, subpoena, a hostile host (`THREAT-MODEL.md` §4.1, §4.2, §4.5). Identity is
  vaulted off the records; a seized bag is "a preservation copy and nothing more." The
  absolute `SEALED` tier encrypts sealed field *values* and payload *files* at rest, and
  dual-control (`dualcontrol.py`, `propose`/`approve`) means no single coerced steward
  can unseal or take down alone. Minimal-disclosure defaults (`SEALED_UNTIL` by default)
  mean a record reveals as little as it can.
- **Gets stuck.** The honest residual the docs already name: contributor *identity* is
  vault-encrypted, but ordinary `community`/`steward`-level sealed *content* sits in
  clear text in the bag and is readable by a steward or anyone with raw disk/replica
  access (`ADOPTING.md`; §4.4–§4.5). And if a deployment keeps the vault key on the same
  box as the vault, seizure of both is total identity compromise (§4.1) — a property of
  *operations*, not code.
- **Wants next.** Split-knowledge / threshold control of the vault key so no one person
  can be compelled to decrypt (the threat model flags this as *not implemented*, §4.2);
  optional at-rest encryption for community/steward content, not only the absolute tier;
  a plain-language "what 'sealed' actually protects you from" panel at the decision point.
- **Adopts if** she's told the truth about what sealing does and doesn't do *and* the
  key can't be coerced out of one person. **Walks if** "sealed" oversells and a steward
  or a raid can quietly read it.

#### A3 — Margit, 74, oral-history narrator
- **Goal.** Record the whole account now — names, the affair, the arrest — and have it
  open only after she's gone, or after the law changes, not before.
- **Values today.** Temporal seals are *enforced*, not labels: a dated `SEALED_UNTIL`
  embargo binds **every** tier, including stewards, until the date passes (shipped in the
  prior study's §9; `access/policy.py`). The seal is recorded as a PREMIS
  `access-policy change` event with an agent and outcome, so the embargo is auditable.
  Content warnings are structured metadata shown as a text interstitial before anything
  renders (`ACCESSIBILITY.md`), which she wants on hard material.
- **Gets stuck.** Two horizons longer than the seal: her *media* may not survive to the
  unseal date — ledger checks fixity faithfully but does no format identification or
  normalization, and obsolete audio/video is exactly what defeats volunteer archives
  ([LHA's "outdated formats"](https://en.wikipedia.org/wiki/Lesbian_Herstory_Archives)).
  And a `SEALED_CONDITIONAL` ("until it's safe") still needs a human to judge the
  condition.
- **Wants next.** Format triage and a preservation-planning step at ingest (OAIS's
  Preservation Planning entity, which ledger doesn't yet model); a transcript/caption so
  the account is reachable even if the audio degrades; a documented break-glass record
  when a conditional seal is opened.
- **Adopts if** the embargo is real *and* the bytes will still be readable when it lifts.
  **Walks if** the lock holds but the tape rots.

#### A4 — Devon, volunteer community archivist / steward
- **Goal.** Get a donated banker's box of zines, flyers, and cassettes into a real,
  audited archive without a preservation degree.
- **Values today.** `ledger ingest` runs one fixed path — fixity (dual SHA-256 +
  BLAKE2b), content-addressed store with dedupe, a deterministic RFC 8493 bag, PREMIS +
  Dublin Core — so an item is documented the moment it lands (`ingest.py`, `bag.py`,
  `cas.py`). `make demo` walks the whole cycle; `ledger audit` returns non-zero on any
  bad bag so cron/CI can alarm. Content warnings, post-publication `ledger cw`, the
  append-only attributed `ModerationLog`, and the gated `/steward` console give her
  accountable moderation. The contribute path + review (`contribute.py`, `review.py`)
  let community members submit.
- **Gets stuck.** Her real bottleneck is physical and human: scanning, transcribing, and
  describing a backlog she'll never clear alone — the QZAP problem, where
  [most of the collection isn't digitized](https://guides.libraries.indiana.edu/zines/qzap).
  No format triage flags an at-risk cassette. Some steward actions still route through
  the CLI rather than the web console.
- **Wants next.** A guided, accessible description/ingest wizard that prompts for alt
  text, captions, and minimal metadata (ACR's 504 authoring gap); batch ingest with a
  format-risk report; fuller steward web actions with a readable audit trail.
- **Adopts if** it lowers the per-item labor of doing this *right*. **Walks if** every
  flyer is a JSON exercise.

#### A5 — Bex, mutual-aid organizer preserving runbooks
- **Goal.** Make sure the pantry route, the bail-fund spreadsheet logic, and the
  de-escalation runbook outlive the three people who currently hold them in their heads.
- **Values today.** Ingest + replication keep the runbook in N independent locations,
  re-verified on arrival, so it survives a dead laptop or a folded group
  (`replicate.py`) — directly the failure mode where
  [mutual-aid groups disband and their knowledge evaporates](https://www.pbs.org/newshour/nation/mutual-aid-groups-ponder-future-of-community-based-help).
  PREMIS gives every runbook an event history; `export.py` and plain BagIt mean the
  collective can walk away with everything (`CONTINUITY.md` §2). Content retrieval
  (`/record/{id}/file/{name}`) serves the actual file, fixity-verified.
- **Gets stuck.** There's no *succession* workflow — no "if this crew folds, here's the
  designated next steward and a clean hand-off bundle." Runbook versioning is just
  re-ingest; there's no diff or "current vs. last season." Unincorporated, volunteer-led
  groups ([the norm](https://cunyurbanfoodpolicy.org/news/2023/08/22/mutual-aid-101-history-politics-and-organizational-structures-of-community-care/))
  have no IT person to run the hand-off.
- **Wants next.** A continuity/"hand-off" export with designated-successor grants; a
  lightweight version history for living documents; a one-command "stand up the
  successor's box."
- **Adopts if** it makes the knowledge survive the group. **Walks if** keeping it alive
  needs the same people who are burning out.

### Group B — Govern (the community as collective governor)

#### B1 — Casa Abierta Collective, the community *as governor*
- **Goal.** Make and record the decisions — who stewards, what comes down, how a dispute
  ends — by a process the collective wrote, not by whoever holds the server.
- **Values today.** Authority is documented policy, enforced by code primitives:
  steward ≠ identity-unsealer and maintainer ≠ steward are *grant-level* facts, not
  etiquette (`grants.py`, `GOVERNANCE.md` §1); every consequential action is justified,
  attributed, and contestable (`moderate.py`); appeals are first-class linked actions;
  the named-subject objection mechanism balances a contributor's account against a
  subject's safety without a heckler's veto. Dual-control turns "a second steward" into
  an enforced threshold for takedown / unseal / publish.
- **Gets stuck.** The *deliberation* still happens off-platform — the code records the
  decision but the meeting, the quorum, the comment window live in minutes elsewhere.
  Adapting the reference thresholds (quorum sizes, timelines) is a docs-and-config
  exercise the collective has to drive itself.
- **Wants next.** Consent-/access models richer than the fixed policy vocabulary —
  community-authored access "labels" in the spirit of
  [Traditional Knowledge Labels](https://mukurtu.org/support/traditional-knowledge-labels-faq/)
  (e.g. "elders only," "no commercial reuse," "seasonal"); an in-archive record of
  governance decisions tied to the records they touch; a proposal/vote affordance that
  feeds the dual-control gate.
- **Adopts if** the tool makes its *own* governance legible and adaptable. **Walks if**
  authority quietly collapses back onto the server-holder.

#### B2 — Marisol, named subject in someone else's record
- **Goal.** Have a real voice about a record that describes her — without being able to
  silently erase someone else's account of harm.
- **Values today.** The objection mechanism is exactly this balance:
  `/record/{id}/object` (no claim token needed) files a recorded `kind="object"` request
  a steward must weigh; it does **not** auto-restrict, and the contributor keeps control
  of their own record (`GOVERNANCE.md` §3). Narrowest-disclosure defaults mean a record
  that names her is sealed-pending until a steward reviews who's named *before* anything
  is public. None of this can out the contributor — the objection runs through the same
  identity-free surfaces.
- **Gets stuck.** She has to trust a steward's adjudication, and in a small collective an
  uninvolved reviewer may not exist (governance §4 names this honestly). She gets no
  standing notification that a record naming her exists if it's sealed (correctly — but
  it's a real asymmetry).
- **Wants next.** A documented, time-bound steward response to objections; clearer
  guidance to subjects on what they can and can't ask for; a recorded mediation path for
  contested objections.
- **Adopts if** her voice is genuinely weighed and recorded. **Walks if** "object" is a
  button that goes nowhere.

### Group C — Preserve & Standardize (hold it to the professional bar)

#### C1 — Dr. Halloran, preservation librarian / standards steward
- **Goal.** Decide whether her university can supervise a pilot deposit of a community
  collection running on ledger.
- **Values today.** Real standards, not name-drops: RFC 8493 bags with dual manifests
  and a tagmanifest, PREMIS v3 (canonical JSON + XML), Dublin Core (`oai_dc`), OAIS
  SIP/AIP/DIP as distinct typed objects, OAI-PMH at `/oai` and a sitemap for public
  records (ADR 0004; `bag.py`, `metadata/`, `oais.py`, `oai.py`). Fixity is a guarantee
  she can break and watch fail — append 8 bytes, the audit exits non-zero. A committed
  VPAT 2.5 ACR that honestly marks `Partially Supports` reads as trustworthy, not
  green-washed.
- **Gets stuck.** ledger checks *bit* fixity but does no OAIS **Preservation Planning**:
  no format identification (PRONOM/DROID-style), no normalization, no migration path for
  obsolete formats — the discipline [NDSA Levels](https://www.ndsa.org/publications/levels-of-digital-preservation/)
  treats as core. There's no PREMIS **Rights** entity and no persistent-identifier
  policy; `dc:date` is backfilled but accession/provenance/citation are thin for
  scholarship.
- **Wants next.** Format ID + a normalization/migration policy; a PREMIS Rights statement
  per record; a persistent-identifier scheme; a signed, tamper-evident deposit/export
  bundle she can verify on receipt.
- **Adopts if** it meets the preservation bar, not just the packaging bar. **Walks if**
  it's BagIt-shaped but doesn't fight obsolescence.

#### C2 — Nneka, diaspora partner-archive steward
- **Goal.** Take a copy of a sister collection, re-host it on her own infrastructure, and
  owe nobody anything.
- **Values today.** No lock-in by construction: the archive is plain BagIt + sidecar
  PREMIS/Dublin Core, content-addressed and inspectable, one runtime dependency, no
  hosted service (`CONTINUITY.md` §2; ADR 0005). `export.py` hands her a self-contained
  set she can read with standard tools and re-host; the records are identity-free, so a
  copy reveals no contributor.
- **Gets stuck.** The bags aren't encrypted at rest by ledger, so she's responsible for
  confidentiality of community/steward content on her box (`ADOPTING.md`; §4.5);
  replication transport security and an off-box audit-log copy are her job; there's no
  tooling to *verify* she received an authentic, complete copy beyond per-bag validation.
- **Wants next.** Optional at-rest encryption she can turn on for sensitive content; a
  signed export manifest she can verify; guided, secure replication transport between
  trusted instances.
- **Adopts if** walking away is genuinely clean and safe. **Walks if** "portable" means
  "portable but you're on your own for safety."

### Group D — Reuse & Research (the people who read it)

#### D1 — Dr. Okafor, historian / researcher
- **Goal.** Find publishable, citable material without exposing anyone, and cite it so
  the next scholar can find it too.
- **Values today.** Search indexes title + all Dublin Core + visible field values with
  browsable subject/type facets (`search.py`); honest redaction names withheld fields
  without leaking them; sealed-until records carry an on-screen notice; the public OAI-PMH
  feed and sitemap make the open layer harvestable. The redaction discipline lets her
  publish from the public layer
  [without exposing a contributor](https://medium.com/documenting-docnow/archiving-protests-protecting-activists-e628b49eab47).
- **Gets stuck.** Scholarly plumbing is thin: no per-record citation block
  (rights/accession/provenance), no persistent identifiers (opaque hash URLs hurt
  citation), no relationships between records (a zine ↔ its maker ↔ an oral history that
  mentions it), no map or timeline browse to situate material.
- **Wants next.** A citation/provenance block + PIDs; record relationships with an
  accessible (list-equivalent) graph; a place/time browse with the same list/table
  equivalent the archive already mandates.
- **Adopts if** it's a dependable, citable scholarly substrate. **Walks if** she can find
  a record but can't cite or contextualize it.

#### D2 — Wren, community member on a phone in the field
- **Goal.** Pull the *right* runbook in thirty seconds on a metered connection at the
  pantry door.
- **Values today.** Content retrieval serves the real file, fixity-verified by
  construction; the surface is no-JS, tiny, with `Cache-Control`/`ETag`/gzip/`304` on
  static assets (prior §9); the list/table views are mobile-friendly and don't depend on
  a pointer. This is the
  [minimal-computing](https://digitalhumanities.org/dhq/vol/16/2/000646/000646.html)
  posture done right — usable on a cheap phone and a bad link.
- **Gets stuck.** Field reading is still text-dense; long media has no captions or
  transcript to skim; there's no offline copy for when the signal drops mid-shift.
- **Wants next.** A "summary/key-points" view for long runbooks; captions/transcripts on
  audio/video; an offline-friendly saved copy of a permitted record.
- **Adopts if** it's faster than texting someone who knows. **Walks if** it dangles a
  runbook she can't actually open in time.

#### D3 — Amir, multilingual / non-native-English reader
- **Goal.** Read the archive — and especially the *safety labels* — in a language he's
  sure he understands.
- **Values today.** `i18n.py` negotiates `Accept-Language`, ships en/es UI strings,
  glosses content-warning tags in plain language, and uses friendly control words
  ("Continue," not "Proceed") — the prior study's linguistic-access fix, shipped.
- **Gets stuck.** Two languages isn't the diaspora reality; the safety-critical words are
  exactly the ones he most needs in his language; no right-to-left support; contributed
  content itself is whatever language it was deposited in.
- **Wants next.** More languages and a community translation path; RTL; plain-language
  glosses for every CW tag in every shipped language; a per-record "available languages"
  signal.
- **Adopts if** the trauma-decision moment is legible to him. **Walks if** "multilingual"
  is English-plus-one theater.

### Group E — Assure & Adversary (scrutiny, and the threat the design must defeat)

#### E1 — Kestrel, security / threat reviewer
- **Goal.** Make a public surface — HTML, JSON API, export, log, error, health endpoint —
  reveal an identity or a sealed value.
- **Values today.** Can't, and the design is why: `DisclosedRecord` has nowhere to put an
  identity; every read path flows through the one `disclose` chokepoint; real/fake/absent
  record ids return byte-identical 404s; the grant header is a lookup key that confers
  nothing; the `X-Ledger-Grant` header is never logged; `/healthz` is aggregate and
  steward-gated. `tests/test_no_outing.py` injects sentinels and asserts their absence
  from every surface — the requirement is executable (`THREAT-MODEL.md` §3).
- **Gets stuck.** The residue isn't a structural hole, it's physics and inference: the
  neutral-404 timing floor is best-effort, not a cryptographic guarantee (§9a); response
  size/timing and cross-record correlation can still leak interest or re-identify from
  *published* content (§4.6–§4.7); an `identity_ref` on a seized bag confirms *that* a
  contributor is sealed.
- **Wants next.** Constant-time read paths and access-pattern/padding mitigations; a
  documented correlation-risk note for contributors who publish multiple records; suppress
  the residual `identity_ref` signal on at-rest manifests where possible.
- **Adopts** the no-outing claim as *real for who you are*. **Flags** that it's "slightly
  chatty about what you deposited" until timing/correlation are hardened.

#### E2 — Sable, blind screen-reader + keyboard user
- **Goal.** Browse, search, and read a record non-visually, and *trust* the
  content-warning before the material renders.
- **Values today.** Genuinely strong: skip link, clean landmarks and heading order, one
  `h1`, labelled search, a captioned data-table equivalent with `<th scope>`, descriptive
  links, no positive `tabindex`, strong focus-visible, reduced-motion, zero JavaScript —
  the calmest thing a screen-reader user can be handed. The CW interstitial is
  `role="alert"` with the warning as the page `h1`, announced on load. WCAG 2.2 AA is a
  merge-blocking gate (`accessibility_check.py`), contrast is now measured against AA and
  fails the build on regression (§9a), and the VPAT ACR is candid.
- **Gets stuck.** The automated gate is a *floor*, not lived experience — the ACR says so;
  manual NVDA/VoiceOver review is periodic, not continuous. Audio/video oral histories
  lack captions/transcripts; the ingest tool doesn't yet prompt contributors for alt text
  or captions (ACR 504).
- **Wants next.** A committed cadence of independent manual screen-reader audits;
  captions/transcripts as a first-class ingest step; authoring-time accessibility prompts.
- **Adopts if** it catches real barriers, not just structural ones. **Walks if** a green
  gate green-lights a record she personally can't use.

#### E3 — "The Summons", the institution / platform / subpoena as threat
- **Goal** *(adversarial persona — the threat the architecture exists to defeat).* Compel,
  seize, subpoena, doxx, or trick the archive into producing **who contributed** a record,
  or into reading sealed content.
- **Where it's defeated today.** Device seizure yields identity-free bags (§4.1). A
  subpoena finds no single party who can technically produce identities, because a steward
  holds no `identity_unseal` by default and the community can provision *no* standing
  unseal grant (§4.2). A doxxer finds no surface that attributes a record to a person
  (§4.3). A malicious steward can't out anyone without a separate, scoped capability, and
  every action is logged (§4.4). A hostile replica can't make a bad copy the truth
  (§4.5). Dual-control means no single coerced steward can unseal, take down, or publish
  alone.
- **Where it could still win.** The threat model says so plainly: an attacker holding
  **both** the vault file and its key reads everything (§4.1); a person who genuinely
  holds key + unseal grant can be *compelled* (§4.2) — split/threshold key control is
  *not implemented*; raw filesystem access reads clear-text sealed content (§4.4–§4.5);
  traffic analysis and cross-record correlation remain (§4.6–§4.7).
- **What the design must do next.** Make the compellable surface smaller: threshold key
  control so no one person can decrypt; broader at-rest content encryption; access-pattern
  hardening — turning the threat model's named residuals into closed doors.
- **Defeated when** every residual that depends on a single human or a single secret is
  removed by construction. **Wins if** a deployment concentrates the key and an unseal
  grant in one coercible person.

### Group F — Operate & Sustain (keep it alive and honest)

#### F1 — Imani, movement-infrastructure funder
- **Goal.** Fund safety infrastructure that is credible, community-governed, and
  *survivable* — not another tool that dies with its author or a grant cycle.
- **Values today.** The promises pass tests, not prose: the no-outing audit, the fixity
  audit, a candid ACR, AGPL-3.0 chosen so a fork can't quietly weaken the guarantees,
  documented governance and threat model. `CONTINUITY.md` names the bus factor *honestly*
  and de-risks the **records** (open BagIt survives the software), which is exactly the
  fragility she sees across the field —
  [DTA leaning on Portico](https://daily.jstor.org/preserving-history-at-the-digital-transgender-archive-with-portico/),
  [LHA's aging volunteers](https://pubmed.ncbi.nlm.nih.gov/26914823/),
  [ONE needing a $4.2M endowment to be safe](https://today.usc.edu/one-archives-at-the-usc-libraries-receives-4-2-million-in-gifts-endowing-curator-director-positions/).
- **Gets stuck.** One maintainer, pre-1.0, no independent security or accessibility audit
  yet (`CONTINUITY.md` §1). She can't responsibly push adoption to at-risk communities
  ahead of that.
- **Wants next.** A funded second maintainer and an independent security + a11y audit
  before scale; a deploy/adoption track so collectives can stand it up; light reporting
  she can take to her board.
- **Adopts if** funding measurably reduces the bus factor and buys an audit. **Walks if**
  it stays a one-person project carrying records that can get people hurt.

#### F2 — The maintainer, owner of the codebase
- **Goal.** Grow the project past one person without ever loosening the guarantees.
- **Values today.** `make verify` reproduces the full gate (ruff, mypy --strict, the
  no-outing suite, the accessibility check); deterministic, content-hashed bags; signed,
  tagged releases; pip-audit, CodeQL, gitleaks, Dependabot; a private security process
  with stated SLAs and a redaction-safe reporting rule; versioned config/metadata with
  migrations (`CONTINUITY.md`, `CONTRIBUTING.md`, `SECURITY.md`).
- **Gets stuck.** Every review, release, and security triage routes through one person —
  the literal bus factor. The co-maintainer path is documented but the seat is empty; no
  external audit has happened; the roadmap surface area (format planning, threshold keys,
  more languages) is larger than one maintainer.
- **Wants next.** A co-maintainer onboarded through the documented path; an independent
  security review of the disclosure boundary; CI capacity for the manual-review steps that
  can't be automated.
- **Adopts** the discipline as the cost of a safety-sensitive tool. **At risk** of
  burnout being the project's real single point of failure.

#### F3 — Sasha, small-collective self-host operator
- **Goal.** Put ledger on one cheap box for her collective and not be the reason someone
  gets outed.
- **Values today.** `pipx install`, a container image, a stdlib server that binds to
  loopback by default, `Config.default` secure single-box defaults, `make demo`, the
  one-page `ADOPTING.md` deployment-readiness checklist, and `verify-backup` to prove a
  backup actually restores. Affordable by design — no cloud account required
  ([minimal computing](https://sas-dhrh.github.io/dhcc-toolkit/toolkit/minimal-computing.html)).
- **Gets stuck.** The hardest, most consequential controls are *operational and hers*:
  keep the vault key off the disk, full-disk-encrypt every host, terminate TLS in front
  of the plain-HTTP server, run off-box replicas, replicate the audit log, mirror sealed
  content only to trusted hosts (`ADOPTING.md`). One misstep re-opens the threat model's
  worst cases.
- **Wants next.** A guided, opinionated installer that sets the safe defaults (key
  off-box, FDE check, TLS proxy, an off-box replica) so the checklist is mostly done for
  her; a "stand up the successor's box in an afternoon" path (README Phase 4).
- **Adopts if** the safe configuration is the *easy* configuration. **Walks if** doing it
  safely needs an ops engineer her collective doesn't have.

---

## Cross-cutting themes (what the cast agrees on)

1. **The safety core is real; the residual risks are now the frontier.** Every
   adversarial/professional evaluator (E1, E3, C1, F1) confirms the no-outing guarantee is
   structural and survives attack — the same verdict as the prior study. What's left isn't
   a hole, it's the **threat model's own named residuals**: a single coercible vault key
   (A2, E3), clear-text sealed *content* at rest (A2, C2, E3), and timing/correlation
   (E1). These were always documented honestly; this panel says they're the next thing to
   *close*, not just disclose.
2. **Bit-fixity is not preservation.** Three independent personas (C1, A3, A4) hit the
   same gap: ledger superbly checks that bytes haven't changed, but does nothing about
   *format obsolescence* — no identification, normalization, or migration. For oral-history
   audio and old media, that's the difference between a verified file and a *readable* one,
   and it's exactly what defeats real volunteer archives.
3. **Continuity is the mission, and it's only half-built.** The archive de-risks the
   *records* (open BagIt, replication, export) — but the *group* and the *software* are the
   fragile parts. Mutual-aid succession (A5), collective governance handoff (B1), and the
   single-maintainer bus factor (F1, F2) are the same theme at three scales: knowledge
   survives only if *people and process* survive, and that's where the tooling is thinnest.
4. **Legibility for the scared and the constrained is the adoption gate.** The structural
   guarantee means nothing to Río, Teodora, Amir, or Wren if they can't *read* it in their
   language, on their phone, at the moment of decision. Accessibility, plain language, more
   languages, and a believable contributor experience are not polish here — they are
   whether the people the archive is *for* can use it at all.
5. **Consent wants to be richer than five policies.** B1, A3, and C2 all reach past the
   fixed `AccessPolicy` vocabulary toward community-authored, graduated access — the
   [TK Labels / Mukurtu](https://mukurtu.org/support/traditional-knowledge-labels-faq/)
   model of "the community defines the protocol." ledger's one-decision-point architecture
   is the right place to extend, carefully.
6. **The safe configuration must become the easy configuration.** F3 and the adopting
   checklist agree the worst residuals are *operational*. An opinionated installer that
   makes key-off-box, FDE, TLS, and off-box replicas the default would convert the
   threat model's "your responsibility" lines into "done for you."

---

## Honest limits of this exercise

This panel is **synthetic**, like the first one. It can role-play the full stakeholder
cast and surface plausible needs and real documented gaps, but it cannot tell you *which*
matter most to actual communities, how many would adopt, or what a real at-risk
contributor would feel standing at the contribute screen with something dangerous in
hand. It over-represents the author's mental model and the project's own documentation,
and it will miss what only real people surprise you with. It is deliberately a *second
opinion* triangulating the first study — agreement across two synthetic panels is a
weak signal, not proof.

The non-negotiable next step is real discovery with the people this is for — above all
**real contributors whose safety is at stake** and **real disabled and multilingual
readers** — conducted with the project's own redaction-safe discipline so that
researching the archive can never itself endanger anyone. Do not prioritize a roadmap off
this document alone; use it to design those conversations and to sharpen
[`RESEARCH-ROADMAP.md`](RESEARCH-ROADMAP.md), where these findings are triaged into a
sequenced, evidence-tagged backlog.
