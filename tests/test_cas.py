"""Tests for :mod:`ledger.cas` — the content-addressed object store.

Covers the round-trip (bytes in, same bytes out at the derived address), dedupe
(identical bytes map to one address and a single on-disk object), idempotent and
atomic puts, the absence behaviour of ``get_path``/``read_bytes``, and the integrity
guarantee that ``verify`` re-proves the stored bytes still hash to their own name —
and detects a flipped byte.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from ledger.cas import ContentStore
from ledger.errors import ObjectNotFound
from ledger.fixity import hash_bytes
from ledger.models import ContentAddress, HashAlgo


@pytest.mark.preservation
def test_put_bytes_round_trip(store: ContentStore, sample_bytes: bytes) -> None:
    """Bytes stored come back identical, addressed by their own SHA-256."""
    addr = store.put_bytes(sample_bytes)
    assert addr.algo is HashAlgo.SHA256
    assert addr.digest == hash_bytes(sample_bytes, HashAlgo.SHA256)
    assert store.read_bytes(addr) == sample_bytes


@pytest.mark.preservation
def test_address_is_a_function_of_content(
    store: ContentStore, sample_bytes: bytes, other_bytes: bytes
) -> None:
    """Same bytes -> same address; different bytes -> different address."""
    a1 = store.put_bytes(sample_bytes)
    a2 = store.put_bytes(sample_bytes)
    b = store.put_bytes(other_bytes)
    assert a1 == a2
    assert a1 != b


@pytest.mark.preservation
def test_dedupe_stores_a_single_object(store: ContentStore, sample_bytes: bytes) -> None:
    """Putting identical bytes twice yields one address and one on-disk object.

    Content addressing -> dedupe (efficiency): the second put is a no-op write.
    """
    addr = store.put_bytes(sample_bytes)
    store.put_bytes(sample_bytes)
    objects = [p for p in (store.root / "objects").rglob("*") if p.is_file()]
    assert objects == [store.path_for(addr)]


@pytest.mark.preservation
def test_put_is_idempotent_and_leaves_no_temp_files(
    store: ContentStore, sample_bytes: bytes
) -> None:
    """A repeated put returns the same address and leaves no ``.tmp`` orphans.

    Atomicity: an interrupted write would leave a temp file, never a corrupt object
    at the real address; a clean idempotent put leaves nothing behind at all.
    """
    addr1 = store.put_bytes(sample_bytes)
    addr2 = store.put_bytes(sample_bytes)
    assert addr1 == addr2
    leftover = list((store.root / "objects").rglob("*.tmp"))
    assert leftover == []


@pytest.mark.preservation
def test_put_file_matches_put_bytes(
    store: ContentStore,
    sample_bytes: bytes,
    write_file: Callable[[str, bytes], Path],
) -> None:
    """Storing a file and storing its bytes land at the same address."""
    src = write_file("src.bin", sample_bytes)
    file_addr = store.put_file(src)
    bytes_addr = store.put_bytes(sample_bytes)
    assert file_addr == bytes_addr
    assert store.read_bytes(file_addr) == sample_bytes


@pytest.mark.preservation
def test_exists_reflects_presence(store: ContentStore, sample_bytes: bytes) -> None:
    """``exists`` is False before a put and True afterwards."""
    addr = ContentAddress(algo=HashAlgo.SHA256, digest=hash_bytes(sample_bytes, HashAlgo.SHA256))
    assert not store.exists(addr)
    store.put_bytes(sample_bytes)
    assert store.exists(addr)


@pytest.mark.preservation
def test_path_for_is_pure(store: ContentStore, sample_bytes: bytes) -> None:
    """``path_for`` computes a sharded location without touching the filesystem."""
    digest = hash_bytes(sample_bytes, HashAlgo.SHA256)
    addr = ContentAddress(algo=HashAlgo.SHA256, digest=digest)
    path = store.path_for(addr)
    assert not path.exists()  # pure: computing a path creates nothing
    assert path.name == digest
    assert path.parent.name == digest[2:4]
    assert path.parent.parent.name == digest[0:2]


@pytest.mark.preservation
def test_get_path_raises_object_not_found(store: ContentStore) -> None:
    """``get_path`` raises :class:`ObjectNotFound` for an absent object.

    No-outing: the message names only the content address, never any content.
    """
    missing = ContentAddress(algo=HashAlgo.SHA256, digest="0" * 64)
    with pytest.raises(ObjectNotFound) as excinfo:
        store.get_path(missing)
    assert str(missing) in str(excinfo.value)


@pytest.mark.preservation
def test_read_bytes_raises_object_not_found(store: ContentStore) -> None:
    """``read_bytes`` also raises :class:`ObjectNotFound` when absent."""
    missing = ContentAddress(algo=HashAlgo.SHA256, digest="f" * 64)
    with pytest.raises(ObjectNotFound):
        store.read_bytes(missing)


@pytest.mark.preservation
def test_verify_ok_for_intact_object(store: ContentStore, sample_bytes: bytes) -> None:
    """``verify`` confirms a freshly stored object hashes to its own address."""
    addr = store.put_bytes(sample_bytes)
    result = store.verify(addr)
    assert result.ok
    assert result.expected == addr.digest


@pytest.mark.preservation
def test_verify_detects_flipped_byte(store: ContentStore, sample_bytes: bytes) -> None:
    """Flipping one byte on disk makes ``verify`` report a mismatch.

    Integrity: the address *is* the expected digest, so silent drift in the stored
    bytes is impossible to hide from ``verify``.
    """
    addr = store.put_bytes(sample_bytes)
    on_disk = store.path_for(addr)
    raw = bytearray(on_disk.read_bytes())
    raw[0] ^= 0x01  # flip a single bit of a single byte
    on_disk.write_bytes(bytes(raw))

    result = store.verify(addr)
    assert not result.ok
    assert result.actual != addr.digest


@pytest.mark.preservation
def test_open_yields_readable_handle(store: ContentStore, sample_bytes: bytes) -> None:
    """``open`` returns a binary handle positioned at the start of the object."""
    addr = store.put_bytes(sample_bytes)
    with store.open(addr) as handle:
        assert handle.read() == sample_bytes
