"""A content-addressed store (CAS) for archived payload bytes.

EXP-05 (docs/ideation/03-expansions.md): this module's implementation now lives
in the standalone, independently installable and independently tested
:mod:`ledger_preservation_core.cas` library. This is a thin, behaviour-preserving
re-export: :class:`ContentStore` below is the identical class defined in the
core library, so every existing ``from ledger.cas import ContentStore`` keeps
working unchanged.
"""

from __future__ import annotations

from ledger_preservation_core.cas import ContentStore

__all__ = ["ContentStore"]
