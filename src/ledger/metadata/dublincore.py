"""Dublin Core descriptive metadata (DCMI Metadata Element Set / ISO 15836).

The fifteen-element Dublin Core set is the lingua franca of resource description.
This module serializes a :class:`~ledger.models.DublinCore` to and from the
archive's canonical JSON sidecar, and exports the standard ``oai_dc:dc`` XML form
used by OAI-PMH harvesters.

Quality attributes:

* **Standards-compliance.** JSON keys are the canonical DC element names; the XML
  is the ``oai_dc:dc`` profile with ``dc:`` elements in the DCMI namespace.
* **Discoverability.** Stable, well-known element names let general-purpose
  catalogues and search tools index the collection without bespoke mapping.
* **Interoperability.** OAI-PMH-style ``oai_dc`` XML is harvestable by any
  conforming aggregator.

No-outing rule: ``dc.creator`` and ``dc.contributor`` describe the *collection*
or *community* that holds an item — never a closeted individual who contributed
it. Identity lives only in the encrypted vault (:mod:`ledger.identity`) behind an
explicit grant; it is never written to a discoverable sidecar or XML record. This
module trusts that the values handed to it already honour that rule and adds no
identity of its own.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from xml.sax.saxutils import escape

from ledger.models import DC_ELEMENTS, DublinCore, canonical_json

__all__ = [
    "from_json",
    "read_sidecar",
    "to_json",
    "to_oai_dc_xml",
    "write_sidecar",
]

_OAI_DC_NS = "http://www.openarchives.org/OAI/2.0/oai_dc/"
_DC_NS = "http://purl.org/dc/elements/1.1/"
_XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
_OAI_DC_SCHEMA = "http://www.openarchives.org/OAI/2.0/oai_dc.xsd"


def to_json(dc: DublinCore) -> str:
    """Serialize ``dc`` to canonical JSON (empty elements dropped).

    Reproducibility: canonical JSON sorts keys and is compact, so the same record
    yields a byte-identical sidecar everywhere it is written.
    """
    return canonical_json(dc.to_dict())


def from_json(text: str) -> DublinCore:
    """Parse a Dublin Core sidecar produced by :func:`to_json`.

    Unknown keys are ignored by :meth:`DublinCore.from_dict`, so a sidecar from a
    newer or looser producer degrades gracefully (robustness).
    """
    raw: object = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError("Dublin Core JSON must be an object")
    data: dict[str, list[str]] = {}
    for key, value in raw.items():
        if not isinstance(value, list):
            raise ValueError(f"Dublin Core element {key!r} must be a list of strings")
        data[str(key)] = [str(item) for item in value]
    return DublinCore.from_dict(data)


def to_oai_dc_xml(dc: DublinCore) -> str:
    """Render ``dc`` as a standard ``oai_dc:dc`` XML record.

    Interoperability/standards-compliance: emits one ``dc:<element>`` per value in
    the DCMI element namespace, all values XML-escaped, in the canonical element
    order so the output is deterministic. The values are taken verbatim from the
    (collection-level) Dublin Core; no identity is introduced here.
    """
    parts: dict[str, list[str]] = dc.to_dict()
    lines = ['<?xml version="1.0" encoding="UTF-8"?>']
    lines.append(
        "<oai_dc:dc "
        f'xmlns:oai_dc="{_OAI_DC_NS}" '
        f'xmlns:dc="{_DC_NS}" '
        f'xmlns:xsi="{_XSI_NS}" '
        f'xsi:schemaLocation="{_OAI_DC_NS} {_OAI_DC_SCHEMA}">'
    )
    for element in DC_ELEMENTS:
        for value in parts.get(element, []):
            lines.append(f"  <dc:{element}>{escape(value)}</dc:{element}>")
    lines.append("</oai_dc:dc>")
    return "\n".join(lines)


def write_sidecar(dc: DublinCore, path: Path) -> None:
    """Write the JSON Dublin Core sidecar to ``path`` atomically.

    Atomic write (temp file + ``os.replace``) -> integrity/fault-tolerance: a
    reader never sees a partial sidecar, and an interrupted write leaves any prior
    sidecar untouched.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    data = to_json(dc).encode("utf-8")
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def read_sidecar(path: Path) -> DublinCore:
    """Read a Dublin Core sidecar written by :func:`write_sidecar`."""
    return from_json(Path(path).read_text(encoding="utf-8"))
