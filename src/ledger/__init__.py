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

__all__ = ["__version__"]

__version__ = "0.1.0"
