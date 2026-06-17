# 2. AGPL-3.0 license

## Status

Accepted

## Context

ledger must carry a license. The choice is consequential because of what ledger is:
a privacy- and safety-sensitive tool whose core promises — the no-outing guarantee
and the consent model — are properties of the *running system*, not just of the
source. A community archive will most often be reached as a hosted service: a member
or a partnering institution stands up the browse/search server
(`src/ledger/server.py`) at a URL and other people use it over the network.

That deployment shape is exactly where a permissive license (MIT, BSD, Apache-2.0)
has a gap that matters here. Under a permissive license, anyone may take ledger,
modify it, and run the modified version as a network service **without sharing the
modifications**. Someone could fork ledger, quietly weaken the disclosure
chokepoint or the identity vault — surface "who contributed this," loosen a sealed
field, log a grant subject — and operate that altered, less-safe service for a real
community, who would have no way to see that the safety properties they are trusting
have been removed.

The standard copyleft license, GPL-3.0, closes that gap only for *distributed*
software. It does not treat running a modified program as a network service as a
trigger to share source. For a tool whose primary delivery is a hosted service, that
is the loophole that matters.

The cost of copyleft is real and must be weighed: a strong copyleft license narrows
who will adopt the code (some organizations will not touch AGPL), and it is not the
frictionless default of a library meant for the widest possible reuse.

## Decision

We license ledger under the **GNU Affero General Public License, version 3 or later
(`AGPL-3.0-or-later`)**, declared in `pyproject.toml` and in the full `LICENSE`
file at the repository root.

We choose AGPL specifically for its **network-use clause** (section 13): a party who
runs a modified ledger and lets users interact with it over a network must offer
those users the corresponding source of their modified version.

## Consequences

- **The no-outing and consent model are protected against a SaaS fork.** Anyone who
  runs a modified ledger as a hosted service for a community must make their
  modifications available to that community's users. A fork cannot silently weaken
  the safety guarantee behind a service wall; if the disclosure logic or the
  identity vault is changed, the people relying on it can obtain and inspect the
  change. This is the precise case AGPL exists for, and it is why a privacy- and
  safety-sensitive tool for vulnerable contributors warrants it.
- **Improvements flow back to the commons.** Communities and institutions that
  extend ledger and offer it as a service contribute their changes back, which suits
  a community-governed project meant to be a shared steward rather than a base for
  closed derivatives.
- **Adoption is narrower than under a permissive license.** Some organizations
  decline AGPL on policy. We accept this trade: for this tool the protection of the
  safety model is worth more than maximal frictionless reuse.
- **Operators take on an obligation.** A community or institution that modifies and
  hosts ledger must be prepared to provide source to its users. For an honest
  steward this is light; it is a burden only for someone who wanted to ship a
  modified, less-safe service quietly — which is the outcome the choice is meant to
  prevent.
- **The dependency footprint must stay license-compatible.** ledger's single runtime
  dependency, `cryptography`, is permissively licensed and combines cleanly with
  AGPL. Any future dependency must be checked for compatibility.

### Alternatives considered

- **MIT / BSD / Apache-2.0 (permissive).** Maximizes adoption and reuse but leaves
  the network-service loophole open, which is disqualifying for this tool's threat
  model.
- **GPL-3.0 (copyleft, not network-triggered).** Protects distributed copies but not
  the hosted-service case that is ledger's primary delivery, so it does not close the
  gap that matters.
- **AGPL-3.0 (chosen).** The only common license whose copyleft reaches the hosted
  modified service, which is exactly where ledger's safety properties live.
