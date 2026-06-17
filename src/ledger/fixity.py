"""Hashing and checksum verification — the integrity floor of the archive.

Every other preservation layer (the content-addressed store, BagIt packaging,
replication audits) leans on the primitives here. Two design choices serve named
quality attributes:

* **Dual-algorithm support** (SHA-256 *and* BLAKE2b) -> integrity and redundancy:
  a single weakened or backdoored algorithm cannot mask tampering, because an
  independent digest must agree too.
* **Constant-memory streaming** (fixed-size chunks) -> efficiency and scalability:
  a multi-gigabyte oral-history video is hashed without ever being held in RAM.

No-outing: nothing here ever reads or emits file *contents*. It emits hex digests,
relative paths, and pass/fail outcomes only — never a byte of payload, never an
identity.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ledger.models import FixityResult, HashAlgo

if TYPE_CHECKING:  # pragma: no cover - typing only
    from hashlib import _Hash

# 1 MiB read window: large enough to amortize syscalls, small enough to keep
# memory flat regardless of file size (efficiency, scalability).
CHUNK_SIZE: int = 1024 * 1024


def _new_hasher(algo: HashAlgo) -> _Hash:
    """Construct a fresh hashlib object for ``algo``.

    Centralizing construction keeps the algorithm-to-constructor mapping in one
    place (analyzability) and guards against an unknown algorithm slipping through.
    """
    if algo is HashAlgo.SHA256:
        return hashlib.sha256()
    if algo is HashAlgo.BLAKE2B:
        # hashlib.new keeps the return type uniform (HASH) across algorithms.
        return hashlib.new("blake2b")
    raise ValueError(f"unsupported hash algorithm: {algo!r}")


def hash_bytes(data: bytes, algo: HashAlgo) -> str:
    """Return the hex digest of ``data`` under ``algo``."""
    hasher = _new_hasher(algo)
    hasher.update(data)
    return hasher.hexdigest()


def hash_file(path: Path, algo: HashAlgo) -> str:
    """Return the hex digest of the file at ``path`` under ``algo``.

    Streams the file in :data:`CHUNK_SIZE` windows so memory stays constant no
    matter the file size (efficiency, scalability).
    """
    hasher = _new_hasher(algo)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def hash_file_multi(path: Path, algos: Iterable[HashAlgo]) -> dict[HashAlgo, str]:
    """Return ``{algo: hex_digest}`` for every requested algorithm in one pass.

    The file is read exactly once and each chunk is fed to every hasher, so
    computing both manifests costs one disk read rather than two (efficiency).
    Deduplicates the requested algorithms while preserving first-seen order.
    """
    seen: dict[HashAlgo, _Hash] = {}
    for algo in algos:
        if algo not in seen:
            seen[algo] = _new_hasher(algo)
    if not seen:
        return {}
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            for hasher in seen.values():
                hasher.update(chunk)
    return {algo: hasher.hexdigest() for algo, hasher in seen.items()}


def verify_file(path: Path, algo: HashAlgo, expected: str) -> FixityResult:
    """Re-hash ``path`` and compare against ``expected``.

    Returns a :class:`~ledger.models.FixityResult` rather than raising, so a
    caller auditing many files can collect every outcome before deciding how to
    react (failure transparency). The ``path`` recorded is exactly what was
    passed in.
    """
    actual = hash_file(path, algo)
    return FixityResult(path=str(path), algo=algo, expected=expected, actual=actual)


@dataclass(frozen=True)
class AuditReport:
    """The aggregate outcome of verifying a set of files against a manifest.

    Carries every individual :class:`~ledger.models.FixityResult` so a steward
    can see exactly which objects drifted (inspectability), not merely a count.
    """

    results: list[FixityResult]

    @property
    def ok(self) -> bool:
        """True only if every checked file matched its expected digest."""
        return all(result.ok for result in self.results)

    @property
    def failed(self) -> list[FixityResult]:
        """The subset of results whose digest did not match (the corrupt ones)."""
        return [result for result in self.results if not result.ok]

    @property
    def checked(self) -> int:
        """How many files were verified."""
        return len(self.results)


def audit_files(base_dir: Path, manifest: Mapping[str, str], algo: HashAlgo) -> AuditReport:
    """Verify each file named in ``manifest`` under ``base_dir``.

    ``manifest`` maps a relative path to its expected hex digest. Every entry is
    verified and an :class:`AuditReport` returned; results are ordered by relative
    path so two runs over the same tree produce identical reports (reproducibility,
    inspectability).
    """
    results = [
        verify_file(base_dir / relpath, algo, expected)
        for relpath, expected in sorted(manifest.items())
    ]
    return AuditReport(results=results)
