"""Shared fixtures for the ledger-preservation-core test suite.

These fixtures give every test the same primitives — a fresh on-disk
:class:`~ledger_preservation_core.cas.ContentStore`, a deterministic block of
sample bytes, and a helper to write a file under pytest's own ``tmp_path`` — so
tests describe *behaviour* rather than rebuilding scaffolding (modularity,
reproducibility). This suite is self-contained: it has no dependency on any
application package built on top of this library.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from ledger_preservation_core.cas import ContentStore

# A fixed, multi-chunk-friendly sample. Held as a module constant so independent
# fixtures hand back the *same* bytes (dedupe tests rely on this) without
# recomputing.
_SAMPLE_TEXT: bytes = b"a community keeps its own history\n" * 64


@pytest.fixture
def sample_bytes() -> bytes:
    """A deterministic, non-trivial block of payload bytes.

    Reproducibility: identical content every call, so two stores given this fixture
    derive the same content address.
    """
    return _SAMPLE_TEXT


@pytest.fixture
def other_bytes() -> bytes:
    """A second, distinct block of payload bytes for non-collision assertions."""
    return b"a different oral history, different bytes\n" * 32


@pytest.fixture
def store(tmp_path: Path) -> ContentStore:
    """A fresh, empty :class:`ContentStore` rooted in pytest's ``tmp_path``.

    Isolation: each test gets its own root under a unique temp directory, so stores
    never share objects between tests (test independence).
    """
    return ContentStore(tmp_path / "cas-root")


@pytest.fixture
def write_file(tmp_path: Path) -> Callable[[str, bytes], Path]:
    """Return a helper that writes ``data`` to ``name`` under ``tmp_path``.

    Convenience for streaming/file-based tests that need a real on-disk source.
    """

    def _write(name: str, data: bytes) -> Path:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    return _write
