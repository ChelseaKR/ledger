"""Hashing and checksum verification — the integrity floor of the archive.

EXP-05 (docs/ideation/03-expansions.md): this module's implementation now lives
in the standalone, independently installable and independently tested
:mod:`ledger_preservation_core.fixity` library, extracted so the fixity auditor
is genuinely "usable on its own" (the claim the README already made). This is a
thin, behaviour-preserving re-export: every name below is the identical object
defined in the core library, not a copy, so ``ledger.fixity.hash_file is
ledger_preservation_core.fixity.hash_file`` and every existing
``from ledger.fixity import ...`` keeps working unchanged.
"""

from __future__ import annotations

from ledger_preservation_core.fixity import (
    CHUNK_SIZE,
    AuditReport,
    audit_files,
    hash_bytes,
    hash_file,
    hash_file_multi,
    verify_file,
)

__all__ = [
    "CHUNK_SIZE",
    "AuditReport",
    "audit_files",
    "hash_bytes",
    "hash_file",
    "hash_file_multi",
    "verify_file",
]
