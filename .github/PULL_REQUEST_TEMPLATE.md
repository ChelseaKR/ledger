<!-- Thank you for contributing to ledger. This project protects vulnerable
contributors; the checklist below is part of how we keep that promise. -->

## What this change does

<!-- A short, plain description. Link any issue with "Closes #123". -->

## Why

<!-- The problem or need. If this is a significant design decision, add an ADR
under docs/adr/ and link it here. -->

## Checklist

- [ ] `make verify` is green locally (ruff lint + format, mypy --strict, pytest).
- [ ] **No-outing:** this change adds no path by which a contributor identity or a
      sealed field/payload value can reach a read path, log line, filename, metric,
      error message, or any output other than the encrypted vault.
- [ ] **Redaction-safe:** no real sealed content or real personal identity appears
      in the diff, tests, or this PR description. New tests use the synthetic
      fixtures in `tests/fixtures/` (with sentinel values where a leak is checked).
- [ ] Tests added or updated for the behaviour changed.
- [ ] If a read surface changed: the accessibility gate
      (`make accessibility`) is green, and the change keeps the list/table
      equivalent and content-warning behaviour intact.
- [ ] If a preservation format changed: bags remain deterministic and
      backward-readable, and any metadata schema bump includes a migration note.
- [ ] Docs updated (README / docs/) where behaviour or operation changed.
- [ ] `CHANGELOG.md` updated under **Unreleased**.

## Notes for reviewers

<!-- Anything that needs special attention: trade-offs, residual risk, follow-ups. -->
