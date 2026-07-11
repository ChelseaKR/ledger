# Software continuity and security response

ledger is, today, a pre-1.0 reference implementation maintained by one person. A funder
and a preservation librarian both named the same risk in the user research: a single
maintainer is a bus factor, and a community deciding whether to trust an archive with
records that can get someone hurt is entitled to know what happens if that maintainer
stops. This document answers that, plainly and without aspiration. It covers the
co-maintainer goal and how to become one, the reason the *records* are de-risked even
if the *software* stalls, the security-response process, the release and signing
practice, and an honest statement of where the project actually is.

The governing rule is the one repeated across this project: maintaining the software is
not the same as governing any community's archive (`docs/GOVERNANCE.md`, §1). Nothing in
this document gives a maintainer authority over a community's records, policies, or
identities. Continuity of the code and governance of an archive are deliberately separate.

---

## 1. Current status, stated honestly

- **Maturity:** pre-1.0 reference implementation. Public interfaces and the metadata
  schema may still change; a breaking change is always flagged in the commit and the
  changelog (`CONTRIBUTING.md`), and a bag written by an older release stays readable.
- **Maintainers:** one. This is the bus factor finding (user research §5, T14), and it
  is named here rather than hidden. A second maintainer is a goal, not a fact.
- **What "reference implementation" means here:** the code is meant to be correct,
  auditable, and adoptable, and it is exercised by the test suites that back its
  promises — but it has not been hardened by years of multi-operator production use, and
  it has not had an independent security audit. Adopt it with the threat model
  (`docs/THREAT-MODEL.md`) and the deployment checklist (`docs/ADOPTING.md`) open.
- **What is *not* uncertain:** the no-outing guarantee and fixity are enforced in code
  and asserted by tests (`tests/test_no_outing.py`, the fixity audit), not promised in
  prose. The maturity caveats above are about the breadth of operational hardening, not
  about whether the core guarantees hold.

---

## 2. Why the records are de-risked regardless of the software

This is the most important point, and it is the reason a single maintainer is a medium
risk rather than a fatal one: **a community's records do not depend on ledger continuing
to exist.**

- **The archive is plain BagIt.** Each preserved item is an RFC 8493 BagIt bag — a
  payload directory, `manifest-sha256.txt` and `manifest-blake2b.txt`, a `tagmanifest`,
  and `bag-info.txt` — with sidecar PREMIS and Dublin Core. These are open standards and
  plain files. Any preservation tool, or a person with standard command-line utilities
  and the published checksums, can read a bag and verify its fixity without ledger.
- **Storage is content-addressed and inspectable.** A record is named by its hash, so a
  changed byte is a different address; integrity can be re-verified by anyone holding the
  bag and the manifest. There is no proprietary index that must be alive to make sense of
  the bytes.
- **No hosted dependency, no lock-in.** ledger has a single runtime dependency and runs
  on commodity storage. A community can export the whole archive, hand a peer a
  self-contained set of bags, and read or re-host it with other tools. ledger is the
  steward of the format, not the owner of the data.
- **The identity vault is the one part that needs the key, by design.** The encrypted
  vault is *useless without its key* (that is the whole point — see the threat model
  §4.1). It is also a small, documented Fernet-ciphertext file; if ledger vanished, the
  vault format is recoverable from the source and the records themselves remain
  identity-free and readable. Losing the software never silently outs anyone, and never
  makes the records unreadable.

So the durability of the records rests on open formats and replication that the community
controls, not on this codebase. If development stopped tomorrow, every community could
still read, verify, and re-host its archive. The software is a convenience layer over a
format chosen specifically so the community can walk away.

---

## 3. The co-maintainer goal

The explicit goal is **at least two maintainers** who can independently review and merge
changes, cut a signed release, and triage a security report. Mirroring the governance
default of a minimum of two stewards (`docs/GOVERNANCE.md`, §2), no single person should
be the only one able to ship a fix to a safety-sensitive tool.

A maintainer maintains the *software*. A maintainer does **not** govern any deployed
archive, hold any community's vault key, or hold an `identity_unseal` grant by virtue of
being a maintainer. Those are separate capabilities held by stewards under each
community's governance (`docs/GOVERNANCE.md`, §1; threat model §4.2, §4.4).

### How to become a co-maintainer

There is no committee; this is a small project, and the path is earned trust through the
public record:

1. **Contribute, under the existing rules.** Land non-trivial pull requests through the
   normal merge gate (`make verify`: lint, type, test), following `CONTRIBUTING.md` —
   including the redaction-safe rule, which is non-negotiable. Work near the read paths,
   the disclosure model, fixity, and the no-outing audit is the most load-bearing and the
   most telling.
2. **Demonstrate the safety mindset.** A co-maintainer must treat a no-outing or
   sealed-disclosure regression the way one treats memory unsafety elsewhere, and must
   never paste a real sealed value or identity into an issue, PR, log, or test. This is
   judged from your actual contributions, not asserted.
3. **Help with review and triage.** Review others' changes against the guarantees, and
   help triage incoming reports (ordinary bugs first; security reports only once you hold
   commit access and are added to private advisories).
4. **Be invited and provisioned.** The existing maintainer extends commit access, adds
   you to the private security-advisory and release process, and you obtain your own
   release-signing key (§5). The invitation and the date are recorded in the repository
   (a `MAINTAINERS` entry and the changelog), the same audit-as-record discipline used
   everywhere else here.

A maintainer may step back at any time. Stepping back means removing commit access and
revoking or rotating shared release credentials, and recording it — the same shape as a
steward stepping down in governance.

---

## 4. Security-response process

Security here is not only about the software; it is about the safety of contributors to
the archive. The full reporting policy is `SECURITY.md`; this section states the process
and the service levels a reporter and an adopter can expect.

### Reporting (private by default)

- **Preferred:** GitHub's private vulnerability reporting ("Report a vulnerability" under
  the repository's **Security** tab), which opens a private advisory only maintainers can
  see.
- **Fallback:** email **ckellyreif@gmail.com** with `ledger security` in the subject.
- **Do not** open a public issue for a security or disclosure flaw, and do not disclose
  publicly until a fix is available.
- **The redaction-safe rule applies in full.** Never paste a real sealed value, a real
  contributor identity, or any non-public record content. Describe the *shape* of a leak
  ("field X with policy `sealed-until` rendered to an anonymous viewer on route Y") and
  reproduce it with the synthetic sentinel fixtures in `tests/fixtures/`. A report that
  helps fix a leak must never itself become a leak (`SECURITY.md`, `CONTRIBUTING.md`).

### What counts as a security bug

Beyond the usual classes (RCE, auth bypass, injection, secret exposure), these are
**first-class** security bugs, equal in severity (`SECURITY.md`, threat model §3):

- any path by which holding or operating an archive reveals who contributed a record to a
  viewer without an explicit `identity_unseal` grant — via a view, the JSON API, an
  export, a filename, a log line, a metric label, an error message, a timing difference,
  or inference from what is *not* shown;
- any path by which a sealed record or field renders to a viewer whose grant does not
  permit it, including after a takedown or consent change;
- any silent fixity outcome — corruption accepted as valid, or a `fixity-failure`
  swallowed instead of quarantined;
- any consent or takedown that does not propagate to all known replicas.

### Triage SLA

This is a volunteer, single-maintainer project, so the targets below are honest about
that. They are commitments to *acknowledge and act*, not guarantees of an instant fix.

| Stage | Target |
| --- | --- |
| **Acknowledge receipt** | within a few days (typically 72 hours) |
| **Initial severity assessment** | within 7 days of acknowledgement |
| **Status updates to the reporter** | at least every 14 days while the report is open |
| **Fix for a no-outing or sealed-disclosure flaw** | highest priority; worked ahead of all other changes |
| **Fix for other security classes** | prioritized by severity and exploitability |

If a report sits without acknowledgement past the window, it is reasonable to re-send via
the fallback channel. A second maintainer (§3) directly reduces the chance a report
languishes — which is precisely the bus-factor concern.

### Disclosure policy

- **Coordinated disclosure.** Maintainers work with the reporter on a fix before any
  public detail is published. We ask reporters not to disclose publicly until a fix is
  available; we will not sit on a fix indefinitely.
- **Advisory on fix.** When a fix ships, we publish a GitHub Security Advisory describing
  the flaw by its *shape* — never with a real sealed value or identity — so adopters can
  assess and upgrade. No-outing and sealed-disclosure fixes are called out explicitly so
  an operator knows to upgrade promptly.
- **Credit on request.** Reporters who want credit are credited; reporters who want
  anonymity are respected (`SECURITY.md`).
- **Supported versions.** Fixes land on `main` and the latest tagged release; older tags
  are not patched. Pin a tag and watch releases for advisories (`SECURITY.md`).

---

## 5. Release and signing practice

A safety-sensitive tool must be verifiable end to end, so an operator can confirm they
are running what the maintainers actually shipped.

- **Signed, tagged releases.** Releases are signed and tagged `vX.Y.Z`. **Verify a
  release's signature before deploying it; never run an unsigned build of a
  safety-sensitive tool** (`CONTRIBUTING.md`).
- **Reproducible, pinned supply chain.** Dependencies are pinned and hashed; GitHub
  Actions are pinned and SLSA-friendly. Bagging and metadata generation are deterministic,
  so identical input yields a byte-identical bag (README, "Repeatability").
- **Green gate to ship.** Every CI gate — lint, type, test, including the no-outing
  disclosure suite and the accessibility gate — must be green for a tag to ship. Each
  release regenerates and re-commits the Accessibility Conformance Report
  (`docs/accessibility/ACR.md`), the same audit-as-artifact discipline applied to fixity.
- **Dependency scanning.** pip-audit, CodeQL, and gitleaks run in CI
  (`SECURITY.md`, `CONTRIBUTING.md`); Dependabot tracks updates. Supply-chain compromise
  is mitigated, not eliminated (threat model §5), which is one more reason signature
  verification at deploy time matters.
- **Per-maintainer signing keys.** Each maintainer signs with their own key. Adding a
  maintainer means provisioning their signing key and recording it; a maintainer stepping
  back means rotating or revoking shared credentials. A community can pin the specific key
  fingerprints it trusts.

### If the project goes dormant

Because the records are de-risked by the open BagIt format (§2), dormancy is survivable.
Concretely, a community should:

1. keep its off-box replicas, scheduled fixity audits, and scheduled encrypted backups
   running — these need no upstream releases (`docs/ADOPTING.md`; the backup, key-backup,
   and restore-drill procedure is in `docs/BACKUP-RUNBOOK.md`, and the *location* of the
   vault key and the separate backup passphrase should be recorded for a successor here);
2. pin and keep the last signed release it verified, rather than tracking an unsigned
   `main`;
3. retain the ability to read and verify bags with standard tools, independent of ledger;
4. fork under AGPL-3.0 if it needs changes — the network-use clause is chosen precisely so
   a fork serving a community cannot quietly weaken the no-outing guarantee or the consent
   model (README, "License choice").

The honest bottom line: ledger is a single-maintainer, pre-1.0 reference implementation,
and that is a real continuity risk for the *software*. It is not a continuity risk for the
*records*, because they live in open, inspectable, replicated formats a community fully
controls. The plan above is how the software risk is reduced — a co-maintainer, a private
security process with stated SLAs, and signed releases — without overstating where the
project is.
