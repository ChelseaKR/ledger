# 5. Standard-library-first, single runtime dependency

## Status

Accepted

## Context

ledger is meant to be run by communities and collectives with little or no money,
often on a single inexpensive box or a member's drive, and kept running for years
without anyone's funding. It is also a safety-sensitive tool: the people relying on
it need to be able to trust what it does, and a partnering institution may want to
audit it. Three forces converge on the dependency footprint:

- **Affordability and self-sustainability.** Every dependency is a future cost — a
  thing to update, to keep compatible, to re-audit, to keep available. A large
  dependency tree quietly raises the bar for a broke collective to keep the archive
  running and the risk that the project rots when an upstream package is abandoned.
- **Auditability and trust.** The smaller and more standard the code that has to be
  trusted, the more realistically it can be reviewed — by a maintainer, by a
  contributor, by an institution's security reviewer. A sprawling transitive
  dependency graph is, in practice, unauditable.
- **Supply-chain risk.** Each third-party package is an attack surface. For a tool
  whose whole point is protecting vulnerable contributors, a compromised dependency
  is a serious threat, and fewer dependencies means fewer ways in.

At the same time, there is exactly one place where writing our own code would be the
*opposite* of safety: cryptography. The encrypted identity vault (ADR 0003) needs
authenticated symmetric encryption and a sound key-derivation function, and
hand-rolling those is a well-known way to introduce subtle, dangerous bugs.

## Decision

We build ledger **pure-standard-library-first, with a single runtime dependency**.

- The entire preservation and disclosure core is **pure Python standard library**:
  the content-addressed store, BagIt packaging, PREMIS and Dublin Core metadata,
  fixity, the access/disclosure logic, and the browse/search server. The server is
  framework-free, built on `http.server`, with HTML rendered in plain Python and no
  template engine, and the accessibility checker is built on the standard-library
  `html.parser`. No web framework, no build step, no template engine.
- The **only runtime dependency is `cryptography`** (`pyproject.toml`), pinned to a
  conservative range and used *solely* by the identity vault for Fernet
  (authenticated encryption) and scrypt (key derivation) — the one place where
  rolling our own would be unsafe. `cryptography` is free, widely deployed, and
  heavily audited.
- Development tooling (`pytest`, `pytest-cov`, `ruff`, `mypy`, `pip-audit`) lives in
  an optional `dev` extra and is not required to run ledger.
- The supply chain is guarded in CI: `pip-audit` for vulnerable dependencies and
  `gitleaks` for secrets, alongside the lint/type/test gate.

## Consequences

- **A community can run ledger on a single inexpensive box with no paid service and a
  trivial dependency tree**, which is what affordability and self-sustainability
  require. `pipx install ledger-archive` pulls in one library.
- **The code that must be trusted is small and mostly standard**, so a maintainer, a
  contributor, or an institution's reviewer can actually audit it. The one
  non-standard piece is a well-known, widely-reviewed crypto library rather than
  home-grown crypto.
- **The supply-chain attack surface is minimal** — one runtime dependency — which
  matters for a tool protecting vulnerable people.
- **Long-term maintenance is light and survivable.** With almost nothing to keep
  compatible, the project can sit quietly and still run, and is far less likely to
  rot when upstream packages move.
- **We do more by hand.** Rendering HTML in plain Python, serving over `http.server`,
  and scanning markup with `html.parser` is more verbose than reaching for a
  framework, and we forgo the conveniences those frameworks provide. We accept that
  cost; the verbosity buys auditability, portability, and zero lock-in.
- **Performance and feature ceilings are lower than a framework-backed stack.** For
  the read-mostly, static-friendly browse surface this is acceptable; if a future
  need genuinely exceeds the standard library, adding a dependency is a new decision
  to be recorded as its own ADR, weighed against these same forces and checked for
  AGPL compatibility (ADR 0002).

### Alternatives considered

- **A web framework (Flask, Django, FastAPI) for the browse surface.** More features
  and ergonomics, but pulls in a transitive dependency tree, a heavier runtime, and a
  larger audit and maintenance surface — against affordability, auditability, and
  self-sustainability. Rejected for the public surface, which is read-mostly and
  static-friendly.
- **Zero dependencies, including hand-rolled crypto.** Maximally lean, but
  implementing authenticated encryption and key derivation ourselves is exactly the
  kind of mistake that gets vulnerable people hurt. Rejected; `cryptography` is the
  one dependency worth taking.
- **A richer metadata/preservation library to do BagIt/PREMIS/Dublin Core for us.**
  Would add dependencies for work the standard library plus deterministic plain-file
  emission already handles, and would couple our durable formats to a third party.
  Rejected in favour of standard formats emitted by our own small, deterministic code
  (ADR 0004).
