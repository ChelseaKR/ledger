# 8. `segno` as an optional (not runtime) dependency, for print-edition QR codes only

## Status

Accepted

## Context

ADR 0005 commits ledger to a pure-standard-library core with exactly one runtime
dependency (`cryptography`, for the identity vault) and names the path forward
explicitly: "if a future need genuinely exceeds the standard library, adding a
dependency is a new decision to be recorded as its own ADR, weighed against these
same forces and checked for AGPL compatibility (ADR 0002)."

EXP-08 (`docs/ideation/03-expansions.md`) asks for `ledger print-edition`: an
accessible, zine-style booklet of PUBLIC records for offline/paper distribution,
with a per-record fixity QR code so a reader can later scan and confirm a printed
page still matches the live record. Generating a scannable QR code — correct
module placement, Reed–Solomon error correction, mask-pattern selection — is a
well-specified but easy-to-get-subtly-wrong algorithm (ISO/IEC 18004). Hand-rolling
it would be exactly the kind of "rolling our own where a small, sound, widely-used
library already exists" ADR 0005 warns against for cryptography, applied to a
different but analogous correctness risk: a booklet with a QR code that silently
fails to scan is worse than no QR code, because it invites false confidence in a
paper artifact that cannot be patched after it is printed.

`segno` is a pure-Python QR/Micro QR generator (ISO/IEC 18004) with **zero runtime
dependencies of its own**, BSD-3-Clause licensed (compatible with ledger's AGPL-3.0,
ADR 0002 — a permissive dependency inside a copyleft project), and it renders
directly to SVG text with no native/binary extension (no Pillow, no C toolchain) —
so pulling it in does not reintroduce the "heavy transitive tree" ADR 0005 exists to
avoid.

## Decision

`segno` is added as an **optional** `print` extra
(`pip install ledger-archive[print]`), not a base runtime dependency:

- The base install (`pip install ledger-archive`) is unchanged — still exactly one
  runtime dependency, `cryptography`.
- `ledger.print_edition` imports `segno` lazily, inside a `try`/`except ImportError`,
  and degrades gracefully when it is absent: the booklet still renders, every entry
  still carries its plain-text SHA-256 fixity line (the primary, always-present
  verification path — see the module docstring for why a QR code must never be the
  *sole* carrier of that information), and a visible note explains how to install
  the extra for scannable codes. Nothing is faked: an uninstalled `segno` produces
  no QR image at all, never a broken or placeholder one.
- Every other subsystem (the browse server, ingest, bagging, fixity, `export-drive`)
  remains dependency-free beyond `cryptography`, unaffected by this decision.

## Consequences

- **The core archive's dependency footprint is untouched.** A community running
  `ledger init`/`ingest`/`serve`/`export-drive` never needs `segno`; it is pulled in
  only by collectives that also want to print booklets with scannable codes.
- **The one place ledger touches QR generation uses a small, focused, zero-
  dependency, permissively-licensed library** rather than hand-rolled ISO/IEC 18004
  encoding — the same trade-off ADR 0005 already made for `cryptography`, applied
  here because a subtly-wrong QR implementation is a correctness/trust risk on a
  physical artifact nobody can hot-fix.
- **A steward who skips the extra loses nothing essential**: the booklet's
  accessibility and its verifiability both stand on the plain-text fixity line,
  which needs no dependency at all.
- **A future removal is low-cost.** `segno` is imported in exactly one module
  (`ledger/print_edition.py`) behind one `try`/`except`, so dropping it (or swapping
  it for another small library) touches one file.

### Alternatives considered

- **Hand-roll a minimal QR encoder in the standard library.** Rejected for the same
  reason ADR 0005 rejected hand-rolled crypto: a subtly incorrect implementation
  (wrong error-correction level, a mask-selection bug) is hard to catch without a
  QR decoder to test against, and a booklet already printed and mailed cannot be
  patched. The failure mode (a QR code that silently doesn't scan) is worse than
  simply not offering one.
- **`qrcode` (the more commonly known PyPI package).** Rejected: its image backend
  depends on Pillow, a much larger, native-extension dependency exactly of the kind
  ADR 0005 avoids; `segno`'s native SVG text output needs no image library at all.
- **Make `segno` a base runtime dependency.** Rejected: `export-drive`, `ingest`,
  `serve`, and the rest of the archive have no need for QR generation, so forcing it
  on every install would violate ADR 0005's "single runtime dependency" property for
  no benefit to those paths.
