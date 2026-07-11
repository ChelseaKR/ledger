"""Performance budgets: a small, dependency-free benchmark gate for CI (QM-02).

`make verify` proves *correctness*; nothing in the gate previously asserted
anything about *speed*, so a change that accidentally turned an O(1) store
lookup into an O(n) scan, or made fixity hashing read a file byte-by-byte
instead of streaming, would pass every check and ship anyway. This script closes
that gap with a handful of budgets over the operations a steward actually waits
on: content-addressed storage, fixity hashing, one full ingest, and a browse
listing.

Design choices, and the quality attributes they serve:

* **Stdlib + ledger only** (no pytest-benchmark, no external service) ->
  affordability, no heavier CI dependency tree for a project whose whole pitch is
  "runs on one inexpensive box."
* **Median of several trials, not a single sample** -> repeatability: a single
  slow tick on a noisy shared CI runner should not flip the gate red.
* **Budgets set with wide headroom over locally-measured medians** (see the
  comment on each budget) -> the gate exists to catch a *regression* — an
  accidental linear scan, a dropped streaming read, a broken cache — not to
  chase CI runner variance. A budget this script trips is a real, order-of-
  magnitude slowdown, not noise.
* **Human-readable report + machine exit code** -> a contributor sees exactly
  which operation regressed and by how much, and CI fails the build the same way
  every other gate does (`make verify`, `pip-audit`, ...).

No-outing: every fixture used here is synthetic (no real archive content), and
the report never prints payload bytes or identity — only operation names, sizes,
and durations.
"""

from __future__ import annotations

import statistics
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ledger.access.grants import anonymous
from ledger.cas import ContentStore
from ledger.config import Config
from ledger.fixity import hash_file_multi
from ledger.ingest import Archive
from ledger.models import AccessPolicy, DublinCore, Field, HashAlgo, Record

TRIALS = 5


@dataclass(frozen=True)
class Budget:
    """One benchmarked operation and the ceiling its median trial must clear."""

    name: str
    budget_seconds: float
    rationale: str
    run: Callable[[Path], None]


# --- benchmarked operations --------------------------------------------------


def _cas_put_get(root: Path) -> None:
    """Write then read back 200 distinct ~64 KiB objects through the CAS."""
    store = ContentStore(root / "cas")
    addresses = []
    for i in range(200):
        payload = f"object-{i}-".encode() + (b"x" * 65536)
        addresses.append(store.put_bytes(payload))
    for address in addresses:
        store.read_bytes(address)


def _fixity_hash_large_file(root: Path) -> None:
    """Dual-algorithm streaming hash of a single 32 MiB file."""
    target = root / "large.bin"
    with target.open("wb") as fh:
        chunk = b"a community keeps its own history\n" * 30000  # ~1.05 MiB
        for _ in range(30):
            fh.write(chunk)
    hash_file_multi(target, (HashAlgo.SHA256, HashAlgo.BLAKE2B))


def _build_record(i: int) -> Record:
    return Record(
        title=f"Perf-budget record {i}",
        record_id=f"perf-{i:08d}",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(
            title=[f"Perf-budget record {i}"],
            creator=["Community Archive Collective"],
            subject=["queer history", "mutual aid"],
            type=["oral history"],
            language=["en"],
        ),
        fields=[Field(name="story", value="the public account", policy=AccessPolicy.PUBLIC)],
        payloads=[],
        content_warnings=[],
        identity_ref=None,
        created_at="2026-01-01T00:00:00Z",
    )


def _ingest_20_records(root: Path) -> None:
    """Stand up a fresh archive and ingest 20 small, payload-free records."""
    config = Config.default("Perf Budget Archive", root / "archive")
    archive = Archive.init(config)
    for i in range(20):
        archive.ingest({}, _build_record(i), agent="perf-budget", now="2026-01-01T00:00:00Z")


def _build_and_browse_200_records(root: Path) -> None:
    """Ingest 200 records, then browse-list them (a combined scale-path budget)."""
    config = Config.default("Perf Budget Browse Archive", root / "archive")
    archive = Archive.init(config)
    for i in range(200):
        archive.ingest({}, _build_record(i), agent="perf-budget", now="2026-01-01T00:00:00Z")
    grant = anonymous()
    archive.browse(grant, now="2026-01-01T00:00:00Z")


# Budgets are set to roughly 8-10x the median measured on an ordinary developer
# laptop (see the PR that introduced this file for the calibration run), which
# comfortably absorbs a slower or contended shared CI runner while still catching
# an accidental linear-scan-where-there-should-be-a-lookup, a dropped streaming
# read, or similar order-of-magnitude regression.
BUDGETS: list[Budget] = [
    Budget(
        name="cas_put_get_200x64kib",
        budget_seconds=5.0,
        rationale="200 put+get round trips through the content-addressed store",
        run=_cas_put_get,
    ),
    Budget(
        name="fixity_hash_32mib_dual_algo",
        budget_seconds=4.0,
        rationale="streaming SHA-256 + BLAKE2b over one 32 MiB file",
        run=_fixity_hash_large_file,
    ),
    Budget(
        name="ingest_20_records",
        budget_seconds=8.0,
        rationale="full ingest path (fixity, bag, PREMIS, Dublin Core) x20",
        run=_ingest_20_records,
    ),
    Budget(
        name="build_and_browse_200_records",
        budget_seconds=6.0,
        rationale="ingest 200 records, then produce one disclosed browse listing",
        run=_build_and_browse_200_records,
    ),
]


def _time_trial(run: Callable[[Path], None]) -> float:
    with tempfile.TemporaryDirectory(prefix="ledger-perf-") as tmp:
        start = time.perf_counter()
        run(Path(tmp))
        return time.perf_counter() - start


def main() -> int:
    failures: list[str] = []
    rows: list[tuple[str, float, float, str]] = []

    for budget in BUDGETS:
        trials = [_time_trial(budget.run) for _ in range(TRIALS)]
        median = statistics.median(trials)
        status = "OK" if median <= budget.budget_seconds else "OVER BUDGET"
        rows.append((budget.name, median, budget.budget_seconds, status))
        if median > budget.budget_seconds:
            failures.append(
                f"{budget.name}: median {median:.3f}s over budget "
                f"{budget.budget_seconds:.3f}s ({budget.rationale})"
            )

    name_width = max(len(r[0]) for r in rows)
    print(f"performance budgets ({TRIALS} trials each, median reported)")
    print("-" * (name_width + 40))
    for name, median, budget_seconds, status in rows:
        print(
            f"{name:<{name_width}}  median {median:7.3f}s  budget {budget_seconds:6.2f}s  {status}"
        )
    print("-" * (name_width + 40))

    if failures:
        print("\nOVER BUDGET:")
        for line in failures:
            print(f"  - {line}")
        print(
            "\nA benchmark exceeded its budget by a wide enough margin that CI "
            "runner noise is an unlikely explanation. If this is a deliberate "
            "trade-off (e.g. a new integrity check that costs real time), raise "
            "the budget in tools/perf_budget.py in the same PR and say why in the "
            "commit message; do not silently widen it to make a regression pass."
        )
        return 1

    print("\nperf budgets: all operations within budget.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
