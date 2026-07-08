"""PREMIS event log — the archive's append-only record of what happened.

EXP-05 (docs/ideation/03-expansions.md): this module's implementation now lives
in the standalone, independently installable and independently tested
:mod:`ledger_preservation_core.metadata.premis` library. This is a thin,
behaviour-preserving re-export: every name below is the identical object
defined in the core library, so every existing
``from ledger.metadata.premis import ...`` keeps working unchanged.
"""

from __future__ import annotations

from ledger_preservation_core.metadata.premis import PremisLog, escape, to_premis_xml

__all__ = ["PremisLog", "escape", "to_premis_xml"]
