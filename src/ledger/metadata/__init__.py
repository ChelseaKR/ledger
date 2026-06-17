"""Preservation + descriptive metadata for the archive.

Two standards-based concerns live here, kept separate so a change to one cannot
disturb the other (modularity, orthogonality):

* :mod:`ledger.metadata.premis` — the append-only PREMIS preservation event log
  (Library of Congress Data Dictionary), for auditability and provability.
* :mod:`ledger.metadata.dublincore` — Dublin Core descriptive metadata
  (DCMI / ISO 15836), for discoverability and interoperability.

A JSON Schema (draft 2020-12) for the serialized record manifest ships alongside
at ``schema/record.schema.json``; it structurally forbids any top-level identity
field, encoding contributor identity only as the opaque ``identity_ref`` token.
"""

from __future__ import annotations

from ledger.metadata.dublincore import (
    from_json,
    read_sidecar,
    to_json,
    to_oai_dc_xml,
    write_sidecar,
)
from ledger.metadata.premis import PremisLog, to_premis_xml

__all__ = [
    "PremisLog",
    "from_json",
    "read_sidecar",
    "to_json",
    "to_oai_dc_xml",
    "to_premis_xml",
    "write_sidecar",
]
