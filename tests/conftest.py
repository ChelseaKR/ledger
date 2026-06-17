"""Shared fixtures for the preservation test suite.

These fixtures give every preservation test the same three primitives — a fresh
on-disk :class:`~ledger.cas.ContentStore`, a deterministic block of sample bytes,
and a builder for a small :class:`~ledger.models.Record` — so the tests describe
*behaviour* rather than rebuilding scaffolding (modularity, reproducibility).

Determinism: the record builder accepts an explicit ``record_id`` and ``created_at``
default so a built record is byte-stable across runs, and never consults the wall
clock or a random source for values a test will assert on.

No-outing: the sample record carries only collection-level Dublin Core and an
opaque ``identity_ref`` token. No fixture ever places a contributor identity or a
sealed value anywhere a read path, log, or filename could see it.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from ledger.cas import ContentStore
from ledger.models import (
    AccessPolicy,
    ContentAddress,
    DublinCore,
    Field,
    HashAlgo,
    PayloadFile,
    Record,
)

# A fixed, multi-megabyte-free but multi-chunk-friendly sample. Held as a module
# constant so independent fixtures hand back the *same* bytes (dedupe tests rely
# on this) without recomputing.
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


@pytest.fixture
def make_record() -> Callable[..., Record]:
    """Return a builder for a small, deterministic :class:`Record`.

    The builder fixes ``record_id`` and ``created_at`` by default so a built record
    serializes byte-identically across runs (determinism). It carries only an opaque
    ``identity_ref`` and collection-level Dublin Core — never a contributor identity
    (no-outing).
    """

    def _make(
        *,
        title: str = "Pride march, 1987",
        record_id: str = "rec-0000000000000000",
        created_at: str = "2026-01-01T00:00:00Z",
        identity_ref: str | None = "vault-token-opaque",
        payloads: list[PayloadFile] | None = None,
    ) -> Record:
        dc = DublinCore(
            title=[title],
            creator=["Community Archive Collective"],
            subject=["queer history", "mutual aid"],
            type=["Image"],
            language=["en"],
            rights=["CC-BY-SA-4.0"],
        )
        fields = [
            Field(name="story", value="the public account", policy=AccessPolicy.PUBLIC),
            Field(name="location", value="withheld", policy=AccessPolicy.SEALED_UNTIL),
        ]
        return Record(
            title=title,
            record_id=record_id,
            default_policy=AccessPolicy.SEALED_UNTIL,
            dublin_core=dc,
            fields=fields,
            payloads=payloads if payloads is not None else [],
            content_warnings=["outing"],
            identity_ref=identity_ref,
            created_at=created_at,
        )

    return _make


@pytest.fixture
def sample_payload_file(sample_bytes: bytes) -> PayloadFile:
    """A :class:`PayloadFile` whose address is the SHA-256 of ``sample_bytes``."""
    from ledger.fixity import hash_bytes

    digest = hash_bytes(sample_bytes, HashAlgo.SHA256)
    return PayloadFile(
        filename="photo.jpg",
        address=ContentAddress(algo=HashAlgo.SHA256, digest=digest),
        media_type="image/jpeg",
        size_bytes=len(sample_bytes),
        policy=AccessPolicy.PUBLIC,
    )
