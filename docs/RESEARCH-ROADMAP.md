# Research-Backed Roadmap

> Companion to [`USER-RESEARCH.md`](USER-RESEARCH.md) (synthetic persona panel,
> assembled 2026-06-30). This roadmap turns that panel's frictions and wishes into a
> sequenced, evidence-tagged backlog. **It is a planning instrument built from a
> synthetic exercise, not a commitment** — every item is a hypothesis to confirm with
> real users before it ships (see *Validate with real users* below). **Last assembled:
> 2026-06-30.**

## Framing — how this complements the existing roadmap

ledger already has a roadmap, and it is largely *done*:

- **The README Build plan** (Phases 1–4) lays out the preservation core, the disclosure
  + identity layer, replication + browse, and the generalize-and-govern phase. Phases 1–3
  are realized; Phase 4 ("a config so any community can define its own storage locations,
  policies, and warnings" + "adopt this for your collective in an afternoon") is partially
  in.
- **The first synthetic study** ([`docs/research/USER-RESEARCH.md`](research/USER-RESEARCH.md),
  2026-06-17) produced a prioritized roadmap (its §8) whose every item has since been
  **implemented and tested** (its §9): temporal-seal enforcement, contributor consent
  agency, content retrieval, the web safety surface, the HTML status page, CW
  accessibility, search over Dublin Core, the steward console, i18n, side-channel
  closures, OAI-PMH/sitemap, the absolute `SEALED` tier with at-rest encryption, and
  low-bandwidth performance.
- **The `CHANGELOG` [Unreleased]** adds the disclosure-policy workflow (`ledger seal`,
  `ledger redact`) and reading-room enforcement proofs.

So this roadmap deliberately **does not re-litigate** any of that. It picks up at the
**next horizon**: hardening the **honest residual risks the project already documents**
(threat model §4–§5, the ACR's `Partially Supports` rows, the operational controls in
`ADOPTING.md`), closing the one large **preservation** gap the standards demand (format
obsolescence — OAIS Preservation Planning, which the current pipeline doesn't model), and
finishing the **continuity, legibility, and consent-richness** themes the panel raised.
Items are tagged **[corroborates …]** where they triangulate an existing doc/roadmap line
and **[NET-NEW]** where the panel surfaced them.

## Research basis / evidence (real sources, accessed 2026-06-30)

The backlog is anchored to external standards and lived community practice, not only to
internal docs.

- **Preservation standards demand more than bit-fixity.** OAIS includes a
  **Preservation Planning** functional entity for format obsolescence — [ISO 14721](https://www.iso.org/standard/57284.html),
  [oais.info](http://www.oais.info/); the [NDSA Levels of Digital Preservation](https://www.ndsa.org/publications/levels-of-digital-preservation/)
  and the [DPC Handbook on fixity](https://www.dpconline.org/handbook/technical-solutions-and-tools/fixity-and-checksums)
  treat format identification and migration as core, alongside checksums. ledger's
  packaging (BagIt [RFC 8493](https://www.rfc-editor.org/rfc/rfc8493.html), [PREMIS v3](https://www.loc.gov/standards/premis/v3/),
  [Dublin Core](https://www.dublincore.org/specifications/dublin-core/dces/) /
  [ISO 15836](https://www.iso.org/standard/71339.html)) is excellent; its
  *planning* layer is the gap (RM4, RM5).
- **Consent-based, community-defined access is a proven model to extend toward.**
  [Mukurtu CMS](https://mukurtu.org/about/) and [Traditional Knowledge Labels](https://mukurtu.org/support/traditional-knowledge-labels-faq/)
  let communities author their *own* access protocols; the [post-custodial, participatory](https://texlibris.lib.utexas.edu/2021/05/participatory-community-archiving-the-south-asian-american-digital-archive/)
  model of [SAADA](https://www.saada.org/) and [Michelle Caswell](https://en.wikipedia.org/wiki/Michelle_Caswell)'s
  community-archives scholarship motivate richer access vocabulary (EX5) and federation
  among independent community instances (EX4).
- **At-risk records get people hurt; compulsion and surveillance are the adversary.**
  [Archiving Protests, Protecting Activists](https://medium.com/documenting-docnow/archiving-protests-protecting-activists-e628b49eab47),
  the [ethics of activist archives](https://www.researchgate.net/publication/325666515),
  and [archival ethics and surveillance](https://lucidea.com/blog/archival-ethics-and-surveillance/)
  underwrite the safety-hardening items (RM1 threshold key, RM2 broader at-rest
  encryption, RM3 timing/correlation) — each closing a residual the threat model itself
  flags as unimplemented.
- **Community archives are precarious; continuity is the mission.** The
  [Digital Transgender Archive depends on a third party for preservation](https://daily.jstor.org/preserving-history-at-the-digital-transgender-archive-with-portico/);
  the [Lesbian Herstory Archives is volunteer-run and hard to sustain](https://pubmed.ncbi.nlm.nih.gov/26914823/);
  [ONE Archives needed a $4.2M endowment to be safe](https://today.usc.edu/one-archives-at-the-usc-libraries-receives-4-2-million-in-gifts-endowing-curator-director-positions/);
  the [Queer Zine Archive Project's collection lives in its founders' home, mostly
  un-digitized](https://guides.libraries.indiana.edu/zines/qzap). And mutual-aid groups
  [routinely disband](https://www.pbs.org/newshour/nation/mutual-aid-groups-ponder-future-of-community-based-help),
  [struggle to sustain past the crisis that formed them](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC8563598/),
  and are [unincorporated and volunteer-led](https://cunyurbanfoodpolicy.org/news/2023/08/22/mutual-aid-101-history-politics-and-organizational-structures-of-community-care/).
  This is the evidence base for the continuity items (RM10 backup, EX1 succession, EX6
  installer) and the bus-factor items (RM9).
- **Minimal computing is the design constraint, not an afterthought.** [GO::DH Minimal
  Computing](https://sas-dhrh.github.io/dhcc-toolkit/toolkit/minimal-computing.html) and
  [Risam & Gil](https://digitalhumanities.org/dhq/vol/16/2/000646/000646.html) frame the
  affordability bar that bounds every item: it must run on one cheap box for a broke
  collective (constrains RM7/EX2/EX6).

---

## Remediation backlog — close documented gaps & residual risks

Priority: **P0** now · **P1** next · **P2** soon · **P3** opportunistic. Effort: **S**
afternoon · **M** day or two · **L** week+.

| ID | Remediation | Personas | Pri | Effort | Evidence / tag |
|---|---|---|---|---|---|
| RM1 | **Threshold / split-knowledge vault key** (Shamir or 2-of-N) so no single holder can be compelled or compromised into outing contributors | A2, E3, B1, F1 | P0 | L | Threat model §4.2/§4.4 calls split/threshold key control *"not implemented; today the key is a single secret."* **[corroborates THREAT-MODEL §4.2]** |
| RM2 | **Optional at-rest encryption for `community`/`steward` content** (not only absolute `SEALED`), + mirror-side encryption guidance | A2, C2, E3 | P1 | L | `ADOPTING.md` + §4.4–§4.5: bags are clear-text at rest; only identity (and absolute-SEALED) are encrypted. **[corroborates ADOPTING / §4.5]** |
| RM3 | **Access-pattern & timing hardening** — constant-time read paths, response padding, narrow the `identity_ref`-on-manifest signal | E1, E3 | P2 | L | §4.6–§4.7 + §9a: timing floor is *"best-effort, not a cryptographic guarantee"*; correlation/traffic analysis remain. **[corroborates §4.7]** |
| RM4 | **Format identification + preservation-planning at ingest** (PRONOM/DROID-style ID, at-risk flags, normalization policy) | C1, A3, A4 | P0 | L | OAIS Preservation Planning + [NDSA Levels](https://www.ndsa.org/publications/levels-of-digital-preservation/); not currently modeled. **[NET-NEW vs pipeline]** |
| RM5 ✅ | **PREMIS Rights entity + per-record citation/provenance block + persistent identifiers** | D1, C1 | P1 | M | [PREMIS v3](https://www.loc.gov/standards/premis/v3/) Rights; prior study T11 (metadata). DC date backfilled, rights/PID thin. **[corroborates research §8 P2 metadata]** + **[NET-NEW PID]** — *shipped: `PremisRights` + ARK PID + PID in citation.* |
| RM6 | **Captions/transcripts as a first-class ingest step** for audio/video oral histories | E2, A3, D2 | P1 | M | `ACCESSIBILITY.md` (captions/transcripts "where source allows"); ACR. **[corroborates ACCESSIBILITY]** |
| RM7 | ✅ **Shipped.** **More UI languages + RTL + glossed CW tags in every language**, with a community translation path | D3, A2 | P1 | M | Prior §9 shipped en/es; linguistic access flagged under-served. Now ships **en/es/fr/ar** with full CW glosses per language, RTL plumbing (`i18n.text_direction` → `<html dir>`), and the remaining i18n gates **G1** (UTF-8), **G9** (pseudolocale), **G10** (RTL), **G11** (`Content-Language`/`Vary`), **G12** (CLDR pin) — see `docs/I18N.md`. **[corroborates research §8 P2 i18n]** |
| RM8 | **Authoring-time accessibility prompts** (alt text, captions, minimal metadata) in the ingest/contribute flow | A4, E2 | P2 | M | ACR's `504` row `Partially Supports`: the ingest CLI doesn't prompt for accessibility info. **[corroborates ACR 504]** |
| RM9 | **Onboard a co-maintainer + commission independent security & accessibility audits** | F1, F2 | P0 | L | `CONTINUITY.md` §1/§3: single maintainer, pre-1.0, no independent audit. **[corroborates CONTINUITY]** |
| RM10 | ✅ **Scheduled, encrypted off-box backup + key-backup tooling/runbook** (extend `verify-backup`) — **done:** `ledger backup`/`restore-backup` (`backup.py`), `docs/BACKUP-RUNBOOK.md`, cron/timer examples | F3, B1, A5 | P2 | M | `verify-backup` exists (K1); `ADOPTING.md` backup items are manual. **[corroborates ADOPTING]** |
| RM11 | ✅ **Committed cadence of manual NVDA/VoiceOver review** (`docs/accessibility/MANUAL-REVIEW-CADENCE.md`) + browser-real **axe CI** over the served site (`tools/a11y_browser/`, `accessibility-browser` job) as engine-backed depth over the static gate (FIX-12). `aria-live` polish on dynamic states still owed. | E2 | P3 | S | ACR: automated gate is a *floor*, manual review periodic. **[corroborates ACCESSIBILITY]** |
| RM12 ✅ | **Time-bound, recorded steward response to named-subject objections** (subject claim tokens minted at ingest + a verified `subject-objection` request with a recorded `due_by`/`resolved_at`) + small-collective "no uninvolved reviewer" handling | B2, B1 | P3 | S | `GOVERNANCE.md` §3–§4 (objection is a request; small-collective gap named). **[corroborates GOVERNANCE]** · **Shipped (RM12+EXP-04):** `consent.issue_subject_token`/`verify_subject_token`, `SubjectTokenStore` (hashes only), `ConsentRequest.due_by`/`resolved_at`, server `named_subjects_count` receipt + `/object` token verification. |

## Expansion backlog — new capability

| ID | Expansion | Personas | Pri | Effort | Evidence / tag |
|---|---|---|---|---|---|
| EX1 | **Group-continuity / succession workflow** — designated-successor grants + a clean "if this group folds" hand-off export | A5, B1, F1 | P0 | M | [Mutual-aid groups disband](https://www.pbs.org/newshour/nation/mutual-aid-groups-ponder-future-of-community-based-help) and lose knowledge; export + grants exist to build on. **[NET-NEW]** |
| EX2 | **Federation / cross-instance discovery** — OAI-PMH harvest in/out, a movement-wide union catalog of *public* records only | D1, C2, F1 | P2 | L | [Post-custodial](https://texlibris.lib.utexas.edu/2021/05/participatory-community-archiving-the-south-asian-american-digital-archive/) networks; `/oai` already serves public records. **[NET-NEW]** |
| EX3 | **Map/place + timeline browse with the list/table non-visual equivalent** | D1, D2, A4 | P2 | M | `ACCESSIBILITY.md` explicitly anticipates "where a map view is later added." **[corroborates ACCESSIBILITY (anticipated)]** |
| EX4 | **Relationships between records** (zine ↔ maker ↔ oral history) via PREMIS/DC relations + accessible, list-equivalent graph | D1, A4 | P2 | M | Scholarly contextualization; DC/PREMIS relation support. **[NET-NEW]** |
| EX5 | **Community-authored graduated access labels** (à la TK Labels) layered on the one disclosure decision point | B1, A3, C2 | P1 | L | [Traditional Knowledge Labels](https://mukurtu.org/support/traditional-knowledge-labels-faq/) / [Mukurtu](https://mukurtu.org/about/). **[NET-NEW]** |
| EX6 | ✅ **Done (scoped to the checkup).** **"Adopt in an afternoon" adoption-readiness checkup** — `ledger checkup` verifies a live deployment against `ADOPTING.md` (structural readiness, vault-key-off-disk provenance, FDE heuristic, off-box replica topology, TLS-exposure hint), writing a dated, identity-free advisory report to `audits/`. Advisory only: no `--fix`, no OS-level installer (`ledger.checkup`). | F3, A4, B1 | P0 | M | README Phase 4 ("adopt this for your collective in an afternoon"); `ADOPTING.md` controls are manual. **[corroborates README Phase 4]** |
| EX7 | **Mobile-first contributor experience** — on-device redaction preview, "see my record as a stranger," offline queue, one-tap seal/withdraw | A1, A2, A5, D2 | P1 | L | Extends the shipped contribute path + consent flow; prior study's contributor-agency theme. **[extends contribute/consent]** |
| EX8 | **Signed, tamper-evident deposit/export bundle** a partner institution can verify on receipt | C1, C2, F1 | P2 | M | Deposit-readiness; signed releases pattern exists. **[NET-NEW]** |
| EX9 | **"Summary / key-points" reading view + saved offline copy** for long runbooks in the field | D2, A5 | P3 | S | [Minimal-computing](https://digitalhumanities.org/dhq/vol/16/2/000646/000646.html) field use. **[NET-NEW]** |
| EX10 | **Fuller steward web console** — more moderation actions in-UI with a readable audit trail | A4, B1 | P3 | M | Prior §9 shipped a gated `/steward` list; extend to actioning. **[extends /steward]** · ✅ **Shipped**: in-UI content-warning + takedown on gated `/steward` rows, each recorded to the audit log the `/steward/audit` page reads. |
| EX11 | **Lightweight version history for living documents** (current vs. last season, diff) | A5, B1 | P3 | M | Mutual-aid runbooks are living docs; CAS already content-addresses revisions. **[NET-NEW]** · ✅ **Shipped**: append-only per-record version index over the CAS + a steward-gated `/record/{id}/history` current-vs-previous field diff. |
| EX12 | **Format migration / normalization pipeline** for at-risk media (pairs with RM4) | C1, A3 | P3 | L | OAIS migration; [NDSA Levels](https://www.ndsa.org/publications/levels-of-digital-preservation/). **[NET-NEW]** |
| EX13 (EXP-02) | ~~**Lockdown mode — one-command duress posture**: dual-controlled `ledger lockdown` stops all non-PUBLIC disclosure and, only after an off-box replica verifies clean, shreds the local vault; inverse `ledger stand-up` restores from the replica~~ **✅ DONE** — `lockdown.py`; server fails closed to PUBLIC-only; replica-gated shred; PREMIS-logged; reversible. | A1, B1, F1 | P1 | M | Seizure/coercion threat (`THREAT-MODEL.md`); builds on dual-control + `verify-backup` + the off-box replica. **[SHIPPED — EXP-02]** |

---

## Sequenced roadmap

Three horizons. Each respects the minimal-computing constraint (must still run on one
cheap box) and the no-regression rule (the no-outing audit, fixity audit, and a11y gate
stay green).

**Horizon 1 — Harden the safety floor & de-risk the project (next).**
RM1 threshold key · RM4 format ID + planning · RM9 co-maintainer + audits · EX1
succession workflow · EX6 "adopt in an afternoon" installer.
*Theme: close the sharpest documented safety residual, fight format obsolescence, reduce
the bus factor, and make the mission (continuity) and the safe config real.*

**Horizon 2 — Deepen legibility, consent, and preservation rigor.**
RM2 broader at-rest encryption · RM5 PREMIS Rights + citation + PIDs · RM6
captions/transcripts · RM7 more languages + RTL · EX5 community access labels · EX7
mobile-first contributor experience.
*Theme: make the guarantee legible to the scared and constrained, let communities author
their own access, and meet the scholarly/preservation bar.*

**Horizon 3 — Connect, contextualize, and scale carefully.**
RM3 timing/correlation hardening · RM8 authoring a11y prompts · RM10 backup tooling ·
EX2 federation · EX3 map/timeline · EX4 relationships · EX8 signed deposit bundle ·
EX10–EX12 console / versioning / migration.
*Theme: cross-instance discovery and richer context, once the floor and the mission are
solid.*

## Recommended first sprint (highest leverage, mostly leaning on existing infra)

The research and the panel converge on five. Ship these first:

1. **RM4 — format identification + preservation-planning at ingest.** The single biggest
   *preservation* gap: ledger proves bytes are unchanged but does nothing about format
   obsolescence — exactly what defeats real volunteer archives (LHA's "outdated formats,"
   QZAP's un-digitized media, Margit's tape). Highest mission value; builds on the ingest
   pipeline. **[NET-NEW, standards-grounded]**
2. **EX1 — group-continuity / succession workflow.** The most research-grounded,
   mission-defining, and *cheap* item: mutual-aid knowledge dies when groups fold;
   designated-successor grants + a hand-off export lean on `export.py` + `grants.py`.
   **[NET-NEW]**
3. **RM1 — threshold / split-knowledge vault key.** The sharpest *safety* residual the
   threat model itself flags as unimplemented; it shrinks the compellable surface to "no
   single person," defeating the subpoena/seizure-of-one-key-holder case. **[corroborates
   THREAT-MODEL §4.2]**
4. **RM6 + RM7 — captions/transcripts + more languages/RTL.** The under-served
   sensory-and-linguistic axis for the very contributors and readers the archive is for;
   turns "accessible structure" into "accessible *content*." **[corroborates
   ACCESSIBILITY / research §8]**
5. **EX6 — "adopt in an afternoon" installer with safe defaults.** Converts the threat
   model's "your responsibility" operational controls (key off-box, FDE, TLS, off-box
   replica) into defaults a broke collective gets for free — the gap between a reference
   implementation and something actually deployed safely. **[corroborates README Phase 4]**

Bundle the afternoon-sized wins alongside: **RM11** (manual a11y cadence), **RM12**
(objection response window), **EX9** (summary/offline reading view).

---

## Traceability matrix (persona → findings)

| Persona | Remediations | Expansions |
|---|---|---|
| A1 Río (not-out contributor) | — | EX7 |
| A2 Teodora (undocumented organizer) | RM1, RM2, RM7 | EX7 |
| A3 Margit (elder narrator) | RM4, RM5, RM6 | EX5, EX12 |
| A4 Devon (volunteer archivist) | RM4, RM8 | EX3, EX4, EX6, EX10 |
| A5 Bex (mutual-aid runbooks) | RM10 | EX1, EX7, EX9, EX11 |
| B1 Casa Abierta (collective governor) | RM10, RM12 | EX1, EX5, EX6, EX10, EX11 |
| B2 Marisol (named subject) | RM12 | — |
| C1 Dr. Halloran (preservation librarian) | RM4, RM5 | EX8, EX12 |
| C2 Nneka (export / re-host) | RM2 | EX2, EX5, EX8 |
| D1 Dr. Okafor (historian) | RM5 | EX2, EX3, EX4 |
| D2 Wren (mobile field reader) | RM6 | EX3, EX7, EX9 |
| D3 Amir (multilingual reader) | RM7 | — |
| E1 Kestrel (security reviewer) | RM3 | — |
| E2 Sable (screen-reader user) | RM6, RM8, RM11 | — |
| E3 The Summons (adversary) | RM1, RM2, RM3 | — |
| F1 Imani (funder) | RM1, RM9 | EX1, EX2, EX8 |
| F2 The maintainer (owner) | RM9 | — |
| F3 Sasha (self-host operator) | RM10 | EX6 |

---

## Validate with real users / risks

Before any P0 work ships, confirm the synthetic findings with real discovery — run with
the project's own redaction-safe discipline so research can never become a leak.

- **Talk to real at-risk contributors first** (not-out, undocumented, criminalized
  organizing) about RM1/RM2/EX7 — *especially* whether the honest truth about what
  "sealed" protects changes their willingness to contribute. The highest-stakes claims
  must be checked against lived experience, not personas.
- **Sit a real preservation librarian and a real community archivist** in front of RM4/RM5
  with their *own* at-risk media; format planning that doesn't match their collections is
  wasted.
- **Test EX6 with a real broke collective** on real cheap hardware and a bad network — the
  installer's value is entirely whether a non-ops volunteer can stand it up *safely*.
- **Recruit real screen-reader, low-vision, and multilingual readers** for RM6/RM7/RM8/EX3
  on actual assistive-technology hardware — the prior study evaluated via `curl`/CLI, which
  the panel notes is no substitute.

**Risks to weigh.** RM1 (threshold keys) adds operational complexity that could *reduce*
safety if it confuses a small collective — design for the minimal-computing operator or it
backfires. EX2 (federation) and EX3/EX4 (map, relationships) expand the surface that must
honor no-outing and the list/table equivalent — each is a place a leak or an inaccessible
surface could be reintroduced; gate them behind the same audits. EX5 (community access
labels) must not become a back door around the single disclosure decision point — extend
*through* `access/`, never around it. RM2/RM4 add weight that must not break the
"runs-on-one-cheap-box" promise.

## Honest limits

This roadmap is derived from a **synthetic** persona panel triangulated against a prior
**synthetic** study and the project's own documentation. It is a prioritization
instrument and a hypothesis generator — not evidence of demand, adoption, or safety. It
inevitably over-weights what is already documented (the threat model's residuals, the
ACR's partial rows) and under-weights what only real communities would surface. Priorities
and effort estimates are rough reads, not commitments. Nothing here overturns the existing,
largely-completed roadmap; it sharpens and extends it — toward closing the residual risks
the project has always named honestly, fighting format obsolescence, and making the
archive survivable for the people and groups it is for. Confirm before you build; ship
nothing that can endanger a contributor on the strength of a synthetic exercise alone.

---
## Implementation status — 2026-06-30 (working tree, uncommitted)
Shipped this pass: **RM4** format-identification + OAIS preservation-planning at ingest (`preservation.py`) · **EX1** group-continuity / succession hand-off (`succession.py`) · **EX13 / EXP-02** lockdown mode — one-command duress posture (`lockdown.py`): dual-controlled, replica-gated vault shred + disclosure freeze, with an inverse `stand-up` · **RM10** scheduled, encrypted, off-box backup + key-backup runbook (`backup.py`, `ledger backup`/`restore-backup`, `docs/BACKUP-RUNBOOK.md`). Verify: `make verify` green. Deferred: RM1 threshold / split-knowledge vault key (crypto review), RM6/RM7 captions + RTL (partly human).

Also shipped: **EX10 + EX11** — the gated `/steward` console gained in-UI moderation actions (add-content-warning and takedown, each with a mandatory reason and an audit-trail entry the `/steward/audit` page reads, reusing the CLI's takedown effect via `moderate.execute_takedown`), and record manifests gained a lightweight living-document history: `Archive.apply_update` snapshots the superseded manifest into the CAS and appends to an append-only `records/{id}.versions.json` index, surfaced through a steward-gated `/record/{id}/history` current-vs-previous field diff (`Archive.record_versions` / `Archive.get_version`, `render._history_main_html`). Every snapshot is the already-identity-free manifest, so the no-outing rule holds; the index lives outside bags, so no reseal is needed.

Shipped since (2026-07-02): **RM11 + FIX-12** accessibility verification depth — a browser-real Playwright + axe-core CI job (`accessibility-browser`) drives the served demo in headless Chromium under light and dark schemes across the ~8 canonical pages (browse, search/facets, the CW-interstitial record view, contribute, steward console), plus a keyboard-traversal spec; and a committed manual NVDA/VoiceOver review cadence (`docs/accessibility/MANUAL-REVIEW-CADENCE.md`) now backs the ACR's stated evidence basis. All new deps are CI/dev-only under `tools/a11y_browser/` — the stdlib-only runtime is unchanged. The axe run also surfaced and fixed a real dark-mode button/skip-link contrast defect (white text on the light accent, 2.4:1) by taking the button foreground from the already-audited `--bg` token. Still owed under RM11: `aria-live` announcement of status messages (ACR 4.1.3).

Also shipped since (2026-07-02), corrected 2026-07-11: **RM5** PREMIS Rights entity + authority-free UUID URN persistent identifiers + PID in the citation block (`models.PremisRights`, `metadata/pid.py`, PREMIS `rights` in the sidecar/XML, DC `identifier` at ingest, `_citation_html`). Free-text rights are recorded without inferring legal permission to disseminate or replicate.

Also shipped since (2026-07-02): **RM12+EXP-04** named-subject consent standing — subject claim tokens minted at ingest (`consent.issue_subject_token`/`verify_subject_token`, `SubjectTokenStore` persisting hashes only), a verified `subject-objection` consent request with a recorded, time-bound `due_by`/`resolved_at`, and a contribution-receipt one-time token hand-off (`server.py`, `contribute.py`).

**RM7 — more UI languages + RTL + remaining i18n gates (shipped).** Added **French** and **Arabic** UI catalogs (`locales/fr`, `locales/ar`) alongside en/es, each fully translated including plural forms (Arabic's six CLDR categories) and every content-warning gloss. Added RTL plumbing — `i18n.text_direction()` and autonyms `Français`/`العربية` — so the page shell emits `<html lang="…" dir="ltr|rtl">`. Closed the remaining i18n gates: **G1** UTF-8 byte check (`tools/check_i18n_utf8.py`), **G9** test-only pseudolocale (`i18n.pseudolocalize`), **G10** RTL direction, **G11** `Content-Language` + `Vary: Accept-Language` on every response, **G12** CLDR/Babel freshness pin (`tools/check_i18n_deps.py` + `babel<3`). The `make i18n` gate now enforces en/es/fr/ar key-parity/completeness. Verify: `make verify` green (549 tests). See `docs/I18N.md`.
