"""ledger — a privacy-first community archive for queer histories and mutual-aid knowledge.

The package is layered so that preservation and contributor safety are structurally
distinct (modularity, orthogonality):

    models       shared, typed value objects — the contract every layer agrees on
    errors       one exception hierarchy
    cas          content-addressed store (BLAKE2b/SHA-256 addressing, dedupe)
    fixity       checksum compute/verify; scheduled audit; quarantine on mismatch
    bag          BagIt write/validate (RFC 8493)
    metadata     premis (events) + dublincore (description)
    access       policy model, grants, selective disclosure, redaction
    identity     contributor-identity vault: separated, encrypted, grant-gated
    oais         SIP -> AIP -> DIP packaging (ISO 14721)
    replicate    push/pull bags to configured locations; re-verify on arrival
    moderate     content-warning model + accountable moderation workflow
    ingest       accept item -> fixity -> bag -> metadata -> store
    server       accessible browse/search + read-gated JSON API
    config       storage locations, policies, prompts as versioned files

The package never raises a contributor's identity to a read path: see
`ledger.access.disclose` and `ledger.identity`.
"""

from __future__ import annotations

from importlib import metadata as _metadata

__all__ = ["__version__"]

# Single source of version truth (RELEASE-AND-VERSIONING-STANDARD REL-02): derive
# from installed package metadata (itself built from `pyproject.toml`'s
# `[project] version`) rather than hand-copying the version string a second time.
# Before this, `pyproject.toml` and this file could silently drift out of sync.
# The fallback only fires for an uninstalled checkout (e.g. running straight from a
# source tree with no `pip install -e .`), which is not a supported way to run
# ledger but should not raise on import.
try:
    __version__ = _metadata.version("ledger-archive")
except _metadata.PackageNotFoundError:  # pragma: no cover - uninstalled checkout
    __version__ = "0.0.0+unknown"
