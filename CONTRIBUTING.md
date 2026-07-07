# Contributing to ledger

Thank you for considering a contribution. ledger holds records for people for whom exposure
can be dangerous, so contributing here carries one obligation most projects do not: the safety
of contributors to the archive is a first-class engineering requirement, equal to bit-integrity.
Please read this whole document before opening an issue or a pull request. One rule — the
redaction-safe rule — is unusual and non-negotiable.

If you have not yet, read [`README.md`](README.md) for what the project is and why, and
[`SECURITY.md`](SECURITY.md) for how to report a vulnerability. The
[Code of Conduct](CODE_OF_CONDUCT.md) applies to every interaction.

## Standards this repo is held to

ledger is one repo in a personal portfolio governed by a shared set of engineering
standards (code quality, security & supply chain, CI/CD, accessibility,
observability, i18n, documentation, release & versioning, responsible tech, quality
& metrics). Those standards are fetched at CI time (`.github/workflows/standards.yml`,
pinned to a released tag) rather than vendored here. **Which** of them apply to
ledger, and ledger's current posture against each, is declared in the
[`## Standards conformance`](README.md#standards-conformance) table in the README —
check it before assuming a gate exists or doesn't. Gaps tracked there are fair game
for a contribution.

## Project independence

ledger is an independent, personal open-source project. It is not affiliated with, sponsored by,
or endorsed by any employer, client, government, or institutional customer, and it contains no
proprietary, confidential, or client material. Please keep it that way: do not contribute
anything you do not have the right to release under AGPL-3.0, and never bring material from a
client, an employer, or a closed community into this repository. See [`NOTICE`](NOTICE) for the
full independence statement.

## The redaction-safe rule (read this first)

**Never paste a real sealed value, a real contributor identity, or any record content that is not
already public into an issue, a pull request, a commit message, a log, a screenshot, a test, or a
fixture.** A report or a test that helps us fix a leak must not itself become a leak.

- Reproduce every bug with the **synthetic fixtures** in [`tests/fixtures/`](tests/fixtures/).
  They carry sentinel values designed exactly for this — known fake identities and one record per
  access policy. If a fixture you need does not exist, add a synthetic one; do not reach for real data.
- Describe a disclosure flaw by its **shape**, not its content: "field X with policy `sealed-until`
  rendered to an anonymous viewer on route Y," never the field's actual value.
- Scrub screenshots and pasted logs. Structured logs in ledger are scrubbed of contributor identity
  by construction; do not defeat that by hand-copying an unredacted line into a comment.
- New tests assert the *absence* of identity and sealed content on a surface. They must use sentinel
  fixtures so the assertion is meaningful and the test itself never embeds real material.

This rule is enforced socially in review and mechanically where we can (secret scanning, the
no-outing audit). A pull request that violates it will be closed and, if needed, the history scrubbed.

## Getting set up

ledger targets Python 3.11+ and a single runtime dependency. One command installs everything:

```sh
make install
```

This creates a virtual environment in `.venv` and installs ledger plus the dev tooling
(ruff, mypy, pytest, pip-audit) in editable mode. Run `make help` to see every target.

Optionally, install the pre-commit hooks so lint/format/secret issues are caught before they
leave your machine, not just in CI:

```sh
pipx install pre-commit  # or: pip install pre-commit
pre-commit install               # ruff check + ruff format + gitleaks, on every commit
pre-commit install --hook-type pre-push  # adds strict mypy, on push only (it's the slow one)
```

CI (`ci.yml`) is still the gate of record — these hooks are a convenience, not a replacement for
`make verify`, and they intentionally scope to `src/`+`tests/` (ruff) or the whole repo (gitleaks),
matching exactly what CI checks. See `.pre-commit-config.yaml` for the pinned hook versions.

## The merge gate

A change merges when the full gate is green. Reproduce it locally with:

```sh
make verify
```

`make verify` runs **lint + type + test + i18n + accessibility + audit + secret-scan** — the same
`make` targets CI's required checks run, on the same pinned toolchain, so green locally means green
in CI (CI-CD-STANDARD CICD-27: local `make verify` and the CI required-check set are kept in parity
by hand; if you add a CI job, add its target to `verify` in the same PR).

| Gate | Command | What it checks |
| --- | --- | --- |
| Lint | `make lint` | ruff check + format-check: correctness, security (bandit rules), import hygiene |
| Type | `make type` | mypy strict over `src/ledger` |
| Test | `make test` | pytest: preservation, disclosure, and the no-outing audit |
| i18n | `make i18n` | POT current, EN/ES key-parity + completeness, PO compiles, BCP-47 valid |
| Accessibility | `make accessibility` | static checks (landmarks, labels, `lang`, alt text, contrast) |
| Audit | `make audit` | pip-audit dependency vulnerability scan — blocking, never muted |
| Secret scan | `make secret-scan` | gitleaks over full history if installed locally; CI is authoritative |

Two gates are called out separately because they protect the project's core promises, and a
regression in either must be unmistakable, not buried:

- **No-outing / disclosure gate.** The disclosure suite (`pytest -m disclosure`) asserts that
  contributor identity never appears in any public surface, log, filename, metric label, or error,
  and that sealed records and fields never render to a viewer without a grant. If your change
  touches `access/`, `identity.py`, `server.py`, redaction, logging, or any read path, expect to
  prove the guarantee still holds with sentinel fixtures. Treat a no-outing regression the way you
  would treat memory unsafety elsewhere.
- **Accessibility gate.** If you touch anything under `web/` or any rendered surface, the
  accessibility checks (`make accessibility`) must pass: landmarks, labels, `lang`, alt text, and
  contrast tokens, with no browser. The full axe plus manual screen-reader review (NVDA, VoiceOver)
  and the equivalent list/table view are part of the bar; the result is recorded in the committed
  Accessibility Conformance Report (`docs/accessibility/ACR.md`). Accessibility is merge-blocking;
  a regression fails the build.

Useful extras that are run individually above but are also handy standalone:

```sh
make cov            # tests with a coverage report (--cov-fail-under=85; verify's `test` target
                     # runs pytest without coverage for speed, so run this before relying on the
                     # floor locally)
make acr            # regenerate the Accessibility Conformance Report
```

## Running the demo

```sh
make demo
```

The demo is a scripted, end-to-end walk through the actual product: it ingests a synthetic record,
seals a contributor's identity while publishing the story, issues a grant, replicates the bag and
re-verifies fixity, and then proves that no public surface or log reveals who contributed it. It
uses only synthetic fixtures, so it is safe to run and safe to screenshot. To browse a local
archive interactively instead:

```sh
make serve   # accessible archive browse at the printed URL
```

## Commit style: Conventional Commits

This repository uses [Conventional Commits](https://www.conventionalcommits.org/). The type drives
the changelog and the next semver bump.

```
<type>[optional scope]: <description>

[optional body]

[optional footer(s)]
```

Common types: `feat`, `fix`, `docs`, `refactor`, `test`, `perf`, `build`, `ci`, `chore`.
A breaking change is marked with `!` after the type/scope (`feat(access)!: ...`) and explained
in a `BREAKING CHANGE:` footer. Useful scopes mirror the architecture: `ingest`, `cas`, `bag`,
`fixity`, `metadata`, `access`, `identity`, `replicate`, `moderate`, `oais`, `server`, `web`,
`docs`, `infra`.

Examples:

```
feat(access): add sealed-conditional policy with PREMIS event on unseal
fix(server): stop leaking contributor id in 404 error on sealed route
docs(adr): record decision to address content with BLAKE2b and SHA-256
```

## ADRs: record significant decisions

Any decision that is hard to reverse or that shapes the architecture, the threat model, or a public
interface gets an **Architecture Decision Record** in `docs/ADRs/`. That includes choices about the
storage layout, the access-policy model, the identity vault, the metadata schema and its migrations,
and anything affecting the no-outing or fixity guarantees.

Add an ADR as a numbered Markdown file (`docs/ADRs/0007-short-title.md`) using the standard shape:
**Title**, **Status** (Proposed / Accepted / Superseded), **Context**, **Decision**, and
**Consequences**. Reference the ADR from the pull request that implements it. Superseding an earlier
decision means marking the old ADR `Superseded by NNNN`, not deleting it — the record of *why* is
part of the project.

## Pull requests

Open a PR against `main`. The [pull request template](.github/PULL_REQUEST_TEMPLATE.md) carries the
checklist; the short version:

- `make verify` is green.
- No identity, sealed value, or non-public record content appears in any new surface, log, test, or
  fixture — only synthetic sentinels.
- The accessibility gate is green if you touched a UI surface.
- An ADR is added if you made a significant decision.
- Docs are updated to match the change.

Keep PRs focused, explain the *why* in the description, and link any related issue. Reviews look
hardest at anything near a read path, a log line, or the disclosure model.

## Reporting bugs and security issues

- **Security and any disclosure / no-outing flaw:** do **not** open a public issue. Use GitHub's
  private vulnerability reporting (the **Security** tab → "Report a vulnerability"), or email
  **ckellyreif@gmail.com** as a fallback. See [`SECURITY.md`](SECURITY.md). The redaction-safe rule
  applies in full: describe the shape, reproduce with synthetic fixtures, paste no sealed content.
- **Ordinary bugs:** use the [bug report form](.github/ISSUE_TEMPLATE/bug_report.yml). It is built
  to never ask you for sealed content or real identities.
- **Accessibility barriers:** use the
  [accessibility issue form](.github/ISSUE_TEMPLATE/accessibility_issue.yml).

## Versioning and releases

ledger follows [Semantic Versioning](https://semver.org/). Before 1.0, the public interfaces and
the metadata schema may still change, but a breaking change is always flagged in the commit and the
[changelog](CHANGELOG.md). The metadata schema is versioned with a documented deprecation and
migration path; a bag written by an older release stays readable.

**Current state (2026-07-05):** no tag or release has shipped yet — `v0.1.0` is declared in the
changelog but not cut (see the README's Standards conformance table, Release & Versioning row, and
`docs/adr/0006-standards-applicability.md`). The intended release posture, once the release
workflow lands, is: tags `vX.Y.Z` signed and verified before a build runs, SHA-pinned SLSA-friendly
GitHub Actions (already true today for CI), a CycloneDX SBOM and cosign/SLSA provenance attached to
each release, and every CI gate green at the tagged commit before anything ships. Until that
workflow exists, treat any build as unsigned and unverified provenance, and pin a specific commit
if you deploy from source. GitHub Actions dependencies are pinned by SHA today regardless of
release status.

## License

By contributing, you agree that your contributions are licensed under the project's
[AGPL-3.0-or-later](LICENSE) license. Source files carry SPDX headers. You must have the right to
release what you contribute, and it must contain no proprietary or client material.
