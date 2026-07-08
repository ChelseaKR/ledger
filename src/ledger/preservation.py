"""Format identification and preservation planning (OAIS Preservation Planning).

EXP-05 (docs/ideation/03-expansions.md): this module's implementation now lives
in the standalone, independently installable and independently tested
:mod:`ledger_preservation_core.preservation` library. This is a thin,
behaviour-preserving re-export: every name below is the identical object
defined in the core library, so every existing
``from ledger.preservation import ...`` keeps working unchanged.
"""

from __future__ import annotations

from ledger_preservation_core.preservation import (
    FormatId,
    FormatInfo,
    identify_file,
    identify_format,
)

__all__ = ["FormatId", "FormatInfo", "identify_file", "identify_format"]
