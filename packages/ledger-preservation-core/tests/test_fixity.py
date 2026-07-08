"""Tests for :mod:`ledger_preservation_core.fixity` — the integrity floor of the preservation core.

Covers hashing correctness against published vectors, the equivalence of streaming
and in-memory hashing (the property that lets a multi-gigabyte payload be hashed in
constant memory), multi-algorithm single-pass hashing, verification outcomes, and
the deterministic ordering of an audit report.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

import pytest

from ledger_preservation_core.fixity import (
    AuditReport,
    audit_files,
    hash_bytes,
    hash_file,
    hash_file_multi,
    verify_file,
)
from ledger_preservation_core.models import FixityResult, HashAlgo

# Published NIST/RFC test vectors. These are stable forever, so a regression in the
# hashing path is caught against an external source of truth, not our own output.
_SHA256_EMPTY = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
_SHA256_ABC = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
_BLAKE2B_EMPTY = (
    "786a02f742015903c6c6fd852552d272912f4740e15847618a86e217f71f5419"
    "d25e1031afee585313896444934eb04b903a685b1448b755d56f701afe9be2ce"
)
_BLAKE2B_ABC = (
    "ba80a53f981c4d0d6a2797b69f12f6e94c212f14685ac4b74b12bb6fdbffa2d1"
    "7d87c5392aab792dc252d5de4533cc9518d38aa8dbf1925ab92386edd4009923"
)


@pytest.mark.preservation
@pytest.mark.parametrize(
    ("data", "algo", "expected"),
    [
        (b"", HashAlgo.SHA256, _SHA256_EMPTY),
        (b"abc", HashAlgo.SHA256, _SHA256_ABC),
        (b"", HashAlgo.BLAKE2B, _BLAKE2B_EMPTY),
        (b"abc", HashAlgo.BLAKE2B, _BLAKE2B_ABC),
    ],
)
def test_hash_bytes_matches_known_vectors(data: bytes, algo: HashAlgo, expected: str) -> None:
    """``hash_bytes`` reproduces the published digest for each algorithm."""
    assert hash_bytes(data, algo) == expected


@pytest.mark.preservation
def test_hash_file_matches_known_vector(write_file: Callable[[str, bytes], Path]) -> None:
    """A file containing ``abc`` hashes to the known SHA-256 vector."""
    path = write_file("vec.bin", b"abc")
    assert hash_file(path, HashAlgo.SHA256) == _SHA256_ABC


@pytest.mark.preservation
def test_streaming_equals_in_memory(
    write_file: Callable[[str, bytes], Path], sample_bytes: bytes
) -> None:
    """Streaming a file digest equals hashing the same bytes in memory.

    This is the contract that lets large payloads be hashed in constant memory:
    the chunked file path and the all-at-once bytes path must never disagree.
    """
    path = write_file("payload.bin", sample_bytes)
    for algo in (HashAlgo.SHA256, HashAlgo.BLAKE2B):
        assert hash_file(path, algo) == hash_bytes(sample_bytes, algo)


@pytest.mark.preservation
def test_streaming_equals_in_memory_across_chunk_boundary(
    write_file: Callable[[str, bytes], Path],
) -> None:
    """A payload larger than one read window streams to the in-memory digest.

    Forcing the data past the 1 MiB :data:`~ledger_preservation_core.fixity.CHUNK_SIZE` exercises the
    multi-chunk loop, proving the streaming accumulation is correct, not just the
    single-chunk case.
    """
    big = b"\x00\x01\x02\x03" * (1024 * 1024)  # 4 MiB, several chunks
    path = write_file("big.bin", big)
    assert hash_file(path, HashAlgo.SHA256) == hashlib.sha256(big).hexdigest()


@pytest.mark.preservation
def test_hash_file_multi_single_pass(
    write_file: Callable[[str, bytes], Path], sample_bytes: bytes
) -> None:
    """``hash_file_multi`` returns one correct digest per requested algorithm."""
    path = write_file("multi.bin", sample_bytes)
    digests = hash_file_multi(path, [HashAlgo.SHA256, HashAlgo.BLAKE2B])
    assert digests[HashAlgo.SHA256] == hash_bytes(sample_bytes, HashAlgo.SHA256)
    assert digests[HashAlgo.BLAKE2B] == hash_bytes(sample_bytes, HashAlgo.BLAKE2B)


@pytest.mark.preservation
def test_hash_file_multi_deduplicates_algorithms(write_file: Callable[[str, bytes], Path]) -> None:
    """Repeated algorithms collapse to one entry; an empty request yields nothing."""
    path = write_file("dup.bin", b"abc")
    digests = hash_file_multi(path, [HashAlgo.SHA256, HashAlgo.SHA256])
    assert digests == {HashAlgo.SHA256: _SHA256_ABC}
    assert hash_file_multi(path, []) == {}


@pytest.mark.preservation
def test_verify_file_ok(write_file: Callable[[str, bytes], Path]) -> None:
    """``verify_file`` reports ``ok`` when the digest matches expectation."""
    path = write_file("ok.bin", b"abc")
    result = verify_file(path, HashAlgo.SHA256, _SHA256_ABC)
    assert isinstance(result, FixityResult)
    assert result.ok
    assert result.expected == _SHA256_ABC
    assert result.actual == _SHA256_ABC


@pytest.mark.preservation
def test_verify_file_detects_mismatch(write_file: Callable[[str, bytes], Path]) -> None:
    """A flipped byte makes ``verify_file`` report not-ok with the real digest.

    Failure transparency: it returns the mismatch rather than raising, so a caller
    auditing many files collects every outcome before reacting.
    """
    path = write_file("bad.bin", b"abd")  # not "abc"
    result = verify_file(path, HashAlgo.SHA256, _SHA256_ABC)
    assert not result.ok
    assert result.actual != _SHA256_ABC


@pytest.mark.preservation
def test_audit_files_ordering_is_deterministic(
    write_file: Callable[[str, bytes], Path], tmp_path: Path
) -> None:
    """``audit_files`` orders results by relative path for reproducible reports."""
    write_file("b.bin", b"abc")
    write_file("a.bin", b"abc")
    manifest = {"b.bin": _SHA256_ABC, "a.bin": _SHA256_ABC}
    report = audit_files(tmp_path, manifest, HashAlgo.SHA256)
    assert isinstance(report, AuditReport)
    assert report.ok
    assert report.checked == 2
    assert [r.path for r in report.results] == sorted(r.path for r in report.results)


@pytest.mark.preservation
def test_audit_report_isolates_failures(
    write_file: Callable[[str, bytes], Path], tmp_path: Path
) -> None:
    """A single corrupt file shows up in ``failed`` and flips ``ok`` to False."""
    write_file("good.bin", b"abc")
    write_file("bad.bin", b"xyz")
    manifest = {"good.bin": _SHA256_ABC, "bad.bin": _SHA256_ABC}
    report = audit_files(tmp_path, manifest, HashAlgo.SHA256)
    assert not report.ok
    assert [r.path for r in report.failed] == [str(tmp_path / "bad.bin")]
