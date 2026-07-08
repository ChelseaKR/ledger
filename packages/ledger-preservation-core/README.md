# ledger-preservation-core

A dependency-free digital-preservation toolkit: BagIt packaging (IETF RFC 8493),
streaming fixity (SHA-256 + BLAKE2b), a content-addressed object store, an
append-only PREMIS event log, Dublin Core metadata (JSON + `oai_dc` XML), and a
PRONOM/DROID-style format identifier.

Extracted from [ledger](https://github.com/ChelseaKR/ledger) — a privacy-first
community archive — as **EXP-05** in
[`docs/ideation/03-expansions.md`](../../docs/ideation/03-expansions.md), so the
BagIt/PREMIS packaging and the fixity auditor ledger's own README already
claims are "usable on their own" actually are: any project that needs to
package, checksum, and audit preservation-grade payloads can depend on this
library without pulling in ledger's application layer (access policy, identity
vault, consent, moderation).

## Why

* **Standards-first.** RFC 8493 (BagIt), the Library of Congress PREMIS Data
  Dictionary, DCMI/ISO 15836 (Dublin Core), and PRONOM-style format signatures
  — not a bespoke format.
* **Zero runtime dependencies.** Pure standard library, so it runs anywhere
  Python 3.11+ runs, with no added supply-chain surface.
* **Deterministic and auditable.** Canonical JSON and sorted manifests, so two
  runs over identical input produce byte-identical output — bags, checksums,
  and event logs can be diffed and golden-tested.
* **No hidden application concepts.** This library has no notion of identity,
  access policy, or consent — it moves and verifies bytes and metadata. A
  consuming application (like ledger) layers its own access-control and
  no-outing rules on top.

## Install

Not yet published to PyPI. Until then, install it as a local/editable
dependency from this path — e.g. from a workspace root:

```bash
pip install -e packages/ledger-preservation-core
```

or, in a project's own `pyproject.toml`, pin a Git subdirectory reference:

```toml
dependencies = [
  "ledger-preservation-core @ git+https://github.com/ChelseaKR/ledger.git#subdirectory=packages/ledger-preservation-core",
]
```

## Quick start

```python
from pathlib import Path

from ledger_preservation_core.bag import validate_bag, write_bag
from ledger_preservation_core.models import HashAlgo

bag = write_bag(
    Path("./my-bag"),
    payload={"photo.jpg": Path("./source/photo.jpg")},
    algos=(HashAlgo.SHA256, HashAlgo.BLAKE2B),
)
report = validate_bag(bag.path)
assert report.ok
```

```python
from ledger_preservation_core.metadata.premis import PremisLog, to_premis_xml
from ledger_preservation_core.models import PremisEvent, PremisEventType

log = PremisLog()
log.record(PremisEvent(event_type=PremisEventType.INGESTION, agent="my-app", outcome="success"))
log.write(Path("./premis.json"))
print(to_premis_xml(log.events))
```

## Modules

| Module | What it does |
| --- | --- |
| `ledger_preservation_core.models` | Shared value objects: `HashAlgo`, `ContentAddress`, `FixityResult`, `PremisEvent`/`PremisEventType`, `DublinCore`. |
| `ledger_preservation_core.errors` | The exception hierarchy (`BagValidationError`, `ObjectNotFound`, `StoreError`). |
| `ledger_preservation_core.fixity` | Streaming SHA-256/BLAKE2b hashing and manifest-based fixity audits. |
| `ledger_preservation_core.cas` | A filesystem content-addressed object store with atomic writes. |
| `ledger_preservation_core.bag` | RFC 8493 BagIt packaging and full structural + fixity validation. |
| `ledger_preservation_core.metadata.premis` | An append-only PREMIS event log (JSON + PREMIS XML). |
| `ledger_preservation_core.metadata.dublincore` | Dublin Core JSON sidecar + `oai_dc` XML. |
| `ledger_preservation_core.preservation` | Dependency-free, signature-based format identification and at-risk-format flagging. |

## Versioning

SemVer, per the portfolio release standard. `0.x` while this extraction settles
against ledger as its first consumer; a `1.0.0` tag is the API-freeze commitment.

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check src tests
mypy
```

This package's test suite is self-contained: it exercises only
`ledger_preservation_core` and has no dependency on the `ledger` application
package, so it can be developed, tested, and released independently.

## License

AGPL-3.0-or-later, same as ledger (see `LICENSE`).
