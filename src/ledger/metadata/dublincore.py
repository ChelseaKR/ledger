"""Dublin Core descriptive metadata (DCMI Metadata Element Set / ISO 15836).

EXP-05 (docs/ideation/03-expansions.md): this module's implementation now lives
in the standalone, independently installable and independently tested
:mod:`ledger_preservation_core.metadata.dublincore` library. This is a thin,
behaviour-preserving re-export: every name below is the identical object
defined in the core library, so every existing
``from ledger.metadata.dublincore import ...`` keeps working unchanged.
"""

from __future__ import annotations

from ledger_preservation_core.metadata.dublincore import (
    escape,
    from_json,
    read_sidecar,
    to_json,
    to_oai_dc_xml,
    write_sidecar,
)

__all__ = [
    "escape",
    "from_json",
    "read_sidecar",
    "to_json",
    "to_oai_dc_xml",
    "write_sidecar",
]
