"""ledger-preservation-core — a dependency-free digital-preservation toolkit.

Extracted from `ledger <https://github.com/ChelseaKR/ledger>`_ (EXP-05,
``docs/ideation/03-expansions.md``) so BagIt packaging (RFC 8493), fixity
auditing, content-addressed storage, PREMIS event logging, Dublin Core
metadata, and format identification are usable independently of the ledger
application. Pure standard library: no runtime dependencies.

Submodules:

* :mod:`ledger_preservation_core.models` — shared value objects (hash algorithms,
  content addresses, fixity results, PREMIS events, Dublin Core).
* :mod:`ledger_preservation_core.errors` — the exception hierarchy.
* :mod:`ledger_preservation_core.fixity` — streaming checksums and manifest audits.
* :mod:`ledger_preservation_core.cas` — a filesystem content-addressed object store.
* :mod:`ledger_preservation_core.bag` — RFC 8493 BagIt packaging and validation.
* :mod:`ledger_preservation_core.metadata.premis` — PREMIS event log (JSON + XML).
* :mod:`ledger_preservation_core.metadata.dublincore` — Dublin Core (JSON + ``oai_dc`` XML).
* :mod:`ledger_preservation_core.preservation` — dependency-free format identification.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
