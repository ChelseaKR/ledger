# ledger — Competitive Analysis & Strategy

> A market analysis of the digital-preservation / community-archive landscape and a
> strategy for ledger's adoption, positioning, and sustainability. Written for
> maintainers and prospective partners, in the same candid register as
> [`ROADMAP.md`](./ROADMAP.md) and [`THREAT-MODEL.md`](./THREAT-MODEL.md): where
> ledger is behind, it says so.
>
> **Drafted: 2026-06-18.** ledger is a pre-1.0 reference implementation, AGPL-3.0,
> an independent open-source project. This document is strategy, not a forecast or a
> commercial plan; ledger is community-governed by design and is not for sale.

---

## 1. TL;DR

The preservation market splits cleanly into two camps, and **neither serves
ledger's user**:

- **Institutional, preservation-grade** tools (Archivematica, LOCKSS, Preservica)
  are excellent at bit-integrity and standards, but assume a *trusted custodian*
  and a friendly threat model. They have no concept of protecting the contributor
  *from the archive itself*, and they are heavy to run.
- **Ethical / community-controlled** tools (Mukurtu) and **publishing** tools
  (Omeka) get community ownership and access nuance right, but are
  publishing-oriented, Drupal/PHP-heavy, and were not built against an *adversarial*
  threat model (seizure, subpoena, doxxing, a hostile host).

ledger's wedge is the empty quadrant: **adversary-aware *and* preservation-grade
*and* runnable on one cheap box by a volunteer**, with one non-negotiable promise no
competitor makes — *holding a record can never out the person who contributed it*,
enforced structurally and tested as a CI gate.

The strategy is therefore not "win share." It is **earn trust, land 3–5 real
communities, and ally rather than compete** with the activist-archiving ecosystem
that already shares ledger's threat model but lacks a preservation backend.

---

## 2. Market definition & who we serve

**Category:** community-controlled digital preservation for *at-risk* records —
oral histories, zines, protest ephemera, mutual-aid runbooks — where the
contributor's safety is a preservation requirement equal to bit-integrity.

**Primary user (ICP):** a small, volunteer-run queer / mutual-aid / activist
community archive, with no institutional backing, holding material that could
endanger a contributor who is not out, is undocumented, or organizes somewhere
hostile. They have a laptop and maybe a cheap VPS — not a digital-preservation
department.

**Jobs to be done:**
1. *Keep our records from vanishing* when an organizer moves or a group folds.
2. *Never let holding a record expose who made it.*
3. *Let a contributor control what's public, community-only, or sealed* — and revoke.
4. *Survive a seizure or a subpoena* without handing over everyone.
5. *Do all of that on a budget, without a vendor and without an IT team.*

**The honest status quo / default "competitor":** a single laptop, a Google Drive,
a dead Facebook group, a shoebox. Fragile, platform-dependent, no preservation, no
safety. This — not other software — is what most of ledger's users run today.

---

## 3. Competitive landscape

### 3.1 The comparison that matters

| | **ledger** | **Mukurtu** | **Archivematica** | **LOCKSS** | **Preservica** | **Omeka S** | **DIY (Drive/social)** |
|---|---|---|---|---|---|---|---|
| Primary purpose | Safe community preservation | Community heritage CMS | Institutional preservation | Distributed fixity network | Enterprise preservation SaaS | Digital publishing/exhibits | — |
| **Contributor-safety / no-outing** | **Structural + tested** | Cultural protocols (access), not anti-outing | None | None | None | None | None |
| Threat model includes the *holder/host* | **Yes** (seizure, subpoena, doxxing) | No | No | Peer-distrust for *fixity*, not safety | No (you trust the vendor) | No | No |
| Preservation-grade (BagIt/PREMIS/DC/OAIS, fixity) | **Yes** | Partial (file mgmt, not OAIS pipeline) | **Yes** (gold standard) | Fixity/replication only | **Yes** | No (not by default) | No |
| Selective disclosure / sealed-until / per-field consent | **Yes, revocable, recorded** | Granular cultural access | No | No | Access controls (institutional) | Basic public/private | No |
| Encrypted identity vault, separate from record | **Yes** | No | No | No | No | No |
| Runs on one cheap box by a non-expert | **Yes** (stdlib, 1 dep, pipx/Docker) | Heavy (Drupal/PHP stack) | Heavy (multi-service) | Network membership | Hosted SaaS | Moderate (PHP/MySQL) | Trivial but unsafe |
| Cost | Free (AGPL) | Free (AGPL) + hosting/support | Free (AGPL) + ops/support | Membership | Paid (free 5 GB Starter) | Free + hosting | Free |
| Governance | Community, not platform/funder | Community-built (Indigenous) | Vendor (Artefactual) | Consortium (Stanford) | Commercial vendor | Nonprofit (CHNM) | Platform |
| Accessibility (WCAG 2.2 AA, committed ACR) | **Yes, gated** | Partial | N/A (back office) | N/A | Varies | Theme-dependent | N/A |
| Maturity | **Pre-1.0, unproven in production** | Mature, 600+ groups | Mature, widely deployed | Mature, 20+ yrs | Mature, 200+ orgs | Mature | n/a |

### 3.2 Closest comparators, profiled

**Mukurtu CMS — the philosophical sibling (and the bar to clear).**
The most aligned project in spirit: built *with* communities (Warumungu and other
Indigenous communities since 2007), free/open, with fine-grained **Cultural
Protocols** and **Traditional Knowledge Labels** for community-defined access —
600+ Indigenous groups use it. Where ledger differs decisively: Mukurtu's access
model is about *cultural appropriateness and circulation*, not *protecting a
contributor from exposure under an adversary*. It has no encrypted identity vault,
no seizure/subpoena threat model, and no structural guarantee that holding a record
can't out its maker. It is a Drupal CMS (heavier to host) oriented toward
*publishing* heritage. **ledger is not a Mukurtu competitor so much as the same
ethic applied to a different, adversarial threat model and a lighter stack.** Mukurtu
is the proof that "built with the community, access on the community's terms" is a
real, fundable category — and a model to learn from, not undercut.

**Archivematica — the preservation gold standard (and what ledger borrows from).**
AGPL-3.0, full OAIS pipeline, METS/PREMIS/Dublin Core/BagIt, microservice
architecture, trusted by institutions. ledger deliberately speaks the *same
standards* so its bags are portable into an Archivematica/AtoM world later. But
Archivematica assumes an institution with staff and infrastructure, has *no*
contributor-safety model, and is far too heavy for a volunteer on one box.
**Complement, not competitor**: ledger is the safe front-of-house for communities
who may one day hand standards-clean AIPs to an institution.

**LOCKSS — the replication ethic, at library scale.**
"Lots of Copies Keep Stuff Safe": mutually-distrusting peers that validate each
other and repair divergence — almost exactly ledger's *verify-on-arrival,
quarantine-and-heal, never-trust-a-divergent-copy* discipline, but for libraries and
journals over a membership network. ledger brings the same integrity ethic down to a
3-location community fleet a collective actually controls. **Shared DNA, different
scale and user.**

**Preservica — the commercial incumbent.**
Mature SaaS, 200+ institutions including 26 US state archives, free 5 GB Starter.
Exactly the wrong shape for ledger's user: proprietary, hosted (you trust a vendor
and its jurisdiction — the opposite of seizure-resistance), and priced/positioned
for institutions. Useful as a contrast in every pitch: *"we are the anti-vendor."*

**Permanent.org — mission-aligned, wrong architecture.**
Nonprofit, no subscriptions, a one-time ~$10/GB endowment model for long-term
digital legacy. Shares ledger's "not a platform, not rent-seeking" values, but is
*centralized and hosted* (you trust the nonprofit), framed around personal/family
legacy, and has no adversarial threat model or community governance. A model worth
studying for **sustainable funding** (endowment over subscription), not a head-to-head.

**Omeka / Omeka S — the easy on-ramp that isn't preservation.**
Beloved by small cultural orgs for publishing exhibits. Low barrier, but not
preservation-grade (no fixity/PREMIS/BagIt by default) and no consent/safety model.
Where a community wants a *public exhibit*, Omeka may sit happily *in front of* a
ledger archive.

**The activist-archiving ecosystem — allies, not rivals.**
Documenting the Now (DocNow — social-media capture), WITNESS (ethical video-archiving
guidance and consent practice), and secure-capture tools like Tella share ledger's
threat model and consent ethic but are mostly *capture and guidance*, not
preservation-grade, community-controlled *storage*. **This is ledger's natural
alliance and distribution channel**: they bring at-risk material and trust; ledger
provides the safe, durable home they don't.

---

## 4. Positioning

### 4.1 The map

Plot the field on two axes that actually matter to ledger's user:

```
                 PRESERVATION-GRADE (fixity, BagIt/PREMIS/OAIS)
                              ▲
            Archivematica •   │   • LOCKSS
                Preservica •  │
                              │        ★ ledger
        ──────────────────────┼──────────────────────►
        TRUSTED-CUSTODIAN     │     ADVERSARY-AWARE
        threat model          │     (protects contributor
                              │      from holder/host/seizure)
              Omeka •         │   • Mukurtu
                 DIY •        │   • DocNow / WITNESS / Tella
                              ▼
                 PUBLISHING / CAPTURE (not preservation)
```

ledger is alone in the top-right: **preservation-grade _and_ adversary-aware.** Every
other tool gives up one axis.

### 4.2 One-liners

- **Positioning statement.** *For a community holding records that could endanger
  the people who made them, ledger is a digital-preservation tool that treats
  contributor safety as a hard requirement equal to bit-integrity — so the archive
  can outlive a seizure, a subpoena, or a folded collective without ever outing a
  contributor. Unlike institutional preservation systems, it assumes the holder and
  the host can be compromised; unlike consumer tools, it is standards-based,
  self-hostable, and community-governed.*
- **Tagline candidates.** "Keep the record. Protect the person." · "Preservation
  that can't out you." · "A safe-keeping place you run yourself."

### 4.3 The three proof points (what makes the claim credible)

1. **The no-outing rule is *structural and tested*** — identity lives in a separate
   encrypted vault, every read path goes through one disclosure chokepoint, and a
   dedicated CI audit injects sentinel identities and asserts their absence from
   every surface, log, and error. The guarantee is executable, not aspirational.
2. **An honest, published threat model and ACR** — `THREAT-MODEL.md` names the
   adversaries and the residual risks; the VPAT 2.5 ACR is candid about partial
   conformance. Credibility comes from *not* overstating.
3. **Affordability as a feature** — stdlib-first, one runtime dependency, `pipx
   install` or one Docker container. A collective can run it for the cost of a cheap
   VPS, with no vendor and no IT team.

---

## 5. SWOT

**Strengths**
- The only tool occupying the adversary-aware + preservation-grade quadrant.
- A *tested* safety guarantee (the no-outing CI audit) — a rare, demonstrable moat.
- Standards-based (BagIt/PREMIS/Dublin Core/OAIS) → exit-friendly, institution-portable.
- Radically low operational footprint and cost.
- Values-congruent governance (community, not platform/funder) — matches the buyer.

**Weaknesses**
- Pre-1.0, **unproven in a real community archive**; no reference deployments yet.
- Effectively single-maintainer → bus-factor and trust risk for a safety tool.
- No hosted option — self-hosting is still a real barrier for the least-technical.
- No capture/exhibit front-end; relies on other tools or the CLI for input.
- Security claims are only as strong as an external audit they haven't yet had.

**Opportunities**
- A rising tide of *at-risk* queer/activist archiving (anti-queer backlash,
  takedowns) with no safety-first preservation home.
- Natural alliances with DocNow / WITNESS / Tella and digital-security orgs.
- Grant funders (Mellon, IMLS, NEH-style, digital-rights funders) already fund
  Mukurtu-shaped work — a fundable category exists.
- Standards-portability lets ledger be the safe *front* of an institutional pipeline.

**Threats**
- A safety bug that outs someone would be existential for trust — the bar is brutal.
- Mukurtu or an institution could add a "safety mode" and absorb the niche.
- Volunteer burnout / abandonment is the most likely failure mode (it kills trust).
- Legal exposure (jurisdiction, subpoena of a maintainer) the project must navigate.
- The DIY default is "good enough" until the day it isn't — inertia is the real rival.

---

## 6. Strategy

### 6.1 Strategic posture

ledger should **not** try to displace incumbents or chase scale. For a pre-1.0
safety tool, the only currency is **trust**, and trust is earned in small numbers.
The posture is: *be the obviously-right tool for a narrow, underserved, high-need
segment; prove it with a handful of real archives; grow by alliance and reputation,
not marketing.*

### 6.2 Beachhead

Win one segment completely before broadening: **volunteer-run queer / mutual-aid
community archives in hostile or precarious contexts**, reached through 2–3 trusted
intermediaries (a digital-security trainer, an activist-archiving org, a queer
-history collective). Land **3–5 lighthouse communities** who run ledger for real,
co-design with them, and let their word-of-mouth carry it. Adjacent segments to
expand into later (in order): reproductive-justice and immigrant-defense mutual aid;
small oral-history projects; then a documented path for institutions to *receive*
ledger AIPs.

### 6.3 Go-to-market & adoption (alliance-led, not ad-led)

1. **Partner for distribution.** Approach DocNow, WITNESS, and digital-security
   training networks (e.g., Access Now's helpline community, EFF/Tactical Tech
   circles) to position ledger as the *preservation home* downstream of capture and
   guidance. Their trust and their at-risk constituents are the channel.
2. **Lead with the methodology, not the software.** Publish the threat model, the
   no-outing audit, and the ACR as a *credibility artifact* — a written "how we keep
   contributors safe" piece is the marketing. Present at community-archives and
   digital-preservation venues (iPRES, SAA Human Rights Archives section, queer-tech
   and library-carpentry communities).
3. **Make adoption a one-evening task.** Ship a genuinely turnkey self-host (the
   Docker quickstart already exists; harden the runbook), a "first archive in 30
   minutes" guide, and a migration path *out* (standards-clean bags) so adopting
   ledger is low-risk and reversible.
4. **Earn the security claim.** Pursue a pro-bono or grant-funded **third-party
   security/threat-model review**, and publish it. For a safety tool this is the
   single highest-leverage credibility investment.
5. **Reduce bus-factor visibly.** Recruit a second maintainer and document
   succession (the repo already has `CONTINUITY.md`); funders and cautious adopters
   both look for this.

### 6.4 Where to invest in the product (to win the position)

Tie roadmap to the wedge. From the executed [`IMPROVEMENT-PLAN.md`](./IMPROVEMENT-PLAN.md),
the resilience work (tested backup/restore, vault-key rotation, deeper health,
replication hardening) *is* competitive strategy: it converts "trust us" into "watch
the test pass." Next, in priority order for adoption:

1. **An accessible, safe capture/contribution front-end** (the biggest gap vs.
   Mukurtu/Omeka) — so a contributor, not just a steward at a CLI, can submit.
2. **A hosted-but-sovereign option** — a documented "managed by a trusted
   intermediary, owned by the community" deployment, lowering the self-host barrier
   without becoming a platform.
3. **An external security audit** and a published remediation (see 6.3.4).
4. **An institutional hand-off guide** — proving the standards-portability claim end
   to end (ledger → Archivematica/AtoM), which de-risks adoption.

### 6.5 Moats / defensibility

- **A *tested* safety guarantee** is hard to copy credibly — an incumbent bolting on
  a "safety mode" can't easily match a structural, audited no-outing property without
  re-architecting around it. Keep that the centre of gravity.
- **Trust + reference communities** compound and don't transfer to a fast follower.
- **Values-congruent governance** (community-owned, AGPL network-clause) is a real
  moat with this buyer, who actively distrusts platforms and vendors.
- **Standards-portability** removes the lock-in objection, which paradoxically makes
  cautious communities *more* willing to commit.

### 6.6 Sustainability (true to the project's values)

Avoid the subscription/platform path that contradicts the threat model and the
governance ethic. Viable, congruent options:
- **Grants** from funders already underwriting Mukurtu-shaped and digital-rights work
  (Mellon, IMLS, NEH-style, digital-defenders/rapid-response funds).
- **Fiscal sponsorship** under an aligned nonprofit for legal/financial cover.
- **An endowment-style fund** for long-term storage (Permanent.org's model, adapted)
  so communities aren't rent-charged.
- **Paid, optional setup/training** by trusted intermediaries — revenue accrues to
  the allies, not a central platform.

### 6.7 Risks to the strategy & counters

| Risk | Counter |
|---|---|
| A safety/outing bug destroys trust | Keep the no-outing audit blocking; fund an external audit; private disclosure path; never overstate (the ACR/threat-model honesty *is* the policy). |
| Single-maintainer abandonment | Recruit a co-maintainer; `CONTINUITY.md`; fiscal sponsor; keep the stack tiny so a successor can hold it. |
| Self-host barrier limits reach | Turnkey Docker + "sovereign managed" option via intermediaries. |
| Incumbent adds a "safety mode" | Move first on the audited guarantee + reference communities; make standards-portability and community governance the story they can't copy. |
| Legal/jurisdiction exposure | Document operator obligations; fiscal-sponsor legal cover; design already minimizes what a subpoena can reach. |

### 6.8 What success looks like (12-month signals)

- **3–5 real communities** running ledger with live records (not demos).
- **One published third-party security review** and its remediation.
- **A second active maintainer** and a fiscal-sponsorship or grant in place.
- **Two named alliance partners** routing at-risk material to ledger.
- **Zero** no-outing incidents; the safety audit stays green every release.
- A documented, demonstrated **ledger → institutional AIP hand-off**.

---

## 7. Bottom line

ledger does not need to beat Archivematica at preservation or Mukurtu at community
publishing. It needs to own the quadrant only it occupies — *preservation-grade,
adversary-aware, community-run on a cheap box* — and to convert its one
uncopyable asset, a **tested promise that holding a record can never out its
contributor**, into the trust of a small number of real communities and the allies
who already serve them. Win narrow, prove it, ally outward.

---

## Sources

- [Mukurtu CMS — About](https://mukurtu.org/about/) · [Mellon Foundation on Mukurtu](https://www.mellon.org/grant-story/mukurtu-provides-ethical-tools-for-archiving-and-preservation) · [NEH on Mukurtu](https://www.neh.gov/article/mukurtu-digital-platform-does-more-manage-content)
- [Archivematica](https://www.archivematica.org/en/) · [Archivematica technical architecture](https://www.archivematica.org/en/docs/archivematica-1.13/getting-started/overview/intro/)
- [LOCKSS — Preservation Principles](https://www.lockss.org/about/preservation-principles) · [LOCKSS (Wikipedia)](https://en.wikipedia.org/wiki/LOCKSS)
- [Preservica](https://preservica.com/) · [Preservica pricing](https://preservica.com/pricing)
- [Permanent.org](https://www.permanent.org/) · [Permanent.org for professionals](https://www.permanent.org/pros/)
- [Omeka (Wikipedia)](https://en.wikipedia.org/wiki/Omeka)
- [Documenting the Now — Archiving Protests, Protecting Activists (SAA)](https://www2.archivists.org/groups/human-rights-archives-section/archiving-protests-protecting-activists-documenting-the-now) · [WITNESS — ethical archiving guidelines](https://archiving.witness.org/2015/11/ethical-wednesdays-archives-and-our-ethical-guidelines-for-using-eyewitness-videos/) · [Fighting Anti-Queer Backlash with Citizen Archivists (In These Times)](https://inthesetimes.com/article/fighting-anti-queer-backlash-with-citizen-archivists)
