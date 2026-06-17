"""Tests for :mod:`ledger.bag` — RFC 8493 BagIt packaging.

Covers the structure a good bag produces, deterministic (byte-identical) manifests
for identical input, the core preservation guarantee that ``validate_bag`` passes
on a sound bag and *raises or fails* on a corrupted payload byte, and the structural
checks (missing ``bagit.txt``, undeclared payload, manifest pointing at an absent
file).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from ledger.bag import Bag, validate_bag, write_bag
from ledger.errors import BagValidationError
from ledger.fixity import hash_bytes
from ledger.models import HashAlgo


@pytest.fixture
def payload_sources(
    write_file: Callable[[str, bytes], Path], sample_bytes: bytes, other_bytes: bytes
) -> dict[str, Path]:
    """Two on-disk payload files keyed by their bag-relative path."""
    return {
        "photo.jpg": write_file("sources/photo.jpg", sample_bytes),
        "notes/story.txt": write_file("sources/story.txt", other_bytes),
    }


def _make_bag(bag_dir: Path, payload: Mapping[str, Path]) -> Bag:
    """Write a bag with default algorithms and a clean, identity-free bag-info."""
    return write_bag(
        bag_dir,
        payload,
        bag_info={"Bagging-Date": "2026-01-01", "Source-Organization": "Collective"},
    )


@pytest.mark.preservation
def test_write_bag_produces_valid_structure(
    tmp_path: Path, payload_sources: dict[str, Path]
) -> None:
    """A written bag has the data dir, both manifests, bagit.txt, and tag manifests."""
    bag = _make_bag(tmp_path / "bag", payload_sources)
    assert (bag.path / "bagit.txt").exists()
    assert (bag.path / "bag-info.txt").exists()
    assert bag.payload_dir.is_dir()
    assert (bag.payload_dir / "photo.jpg").exists()
    assert (bag.payload_dir / "notes/story.txt").exists()
    for algo in (HashAlgo.SHA256, HashAlgo.BLAKE2B):
        assert (bag.path / f"manifest-{algo.value}.txt").exists()
        assert (bag.path / f"tagmanifest-{algo.value}.txt").exists()


@pytest.mark.preservation
def test_bagit_txt_declares_version_and_encoding(
    tmp_path: Path, payload_sources: dict[str, Path]
) -> None:
    """``bagit.txt`` carries the required version + tag-encoding declaration."""
    bag = _make_bag(tmp_path / "bag", payload_sources)
    text = (bag.path / "bagit.txt").read_text(encoding="utf-8")
    assert "BagIt-Version: 1.0" in text
    assert "Tag-File-Character-Encoding: UTF-8" in text


@pytest.mark.preservation
def test_bag_info_payload_oxum_counts_bytes_and_files(
    tmp_path: Path, payload_sources: dict[str, Path], sample_bytes: bytes, other_bytes: bytes
) -> None:
    """``Payload-Oxum`` records total payload bytes and file count."""
    bag = _make_bag(tmp_path / "bag", payload_sources)
    text = (bag.path / "bag-info.txt").read_text(encoding="utf-8")
    expected = f"Payload-Oxum: {len(sample_bytes) + len(other_bytes)}.2"
    assert expected in text


@pytest.mark.preservation
def test_manifest_lists_correct_digests(
    tmp_path: Path, payload_sources: dict[str, Path], sample_bytes: bytes
) -> None:
    """The SHA-256 manifest line for a payload carries its true digest and path."""
    bag = _make_bag(tmp_path / "bag", payload_sources)
    manifest = (bag.path / "manifest-sha256.txt").read_text(encoding="utf-8")
    digest = hash_bytes(sample_bytes, HashAlgo.SHA256)
    assert f"{digest}  data/photo.jpg" in manifest


@pytest.mark.preservation
def test_manifest_is_deterministic(tmp_path: Path, payload_sources: dict[str, Path]) -> None:
    """Identical input -> byte-identical manifests across two separate writes.

    Reproducibility: bags can be diffed, golden-tested, and fixity-compared across
    machines only because emission is a deterministic function of the payload.
    """
    bag_a = _make_bag(tmp_path / "bag_a", payload_sources)
    bag_b = _make_bag(tmp_path / "bag_b", payload_sources)
    for algo in (HashAlgo.SHA256, HashAlgo.BLAKE2B):
        name = f"manifest-{algo.value}.txt"
        assert (bag_a.path / name).read_bytes() == (bag_b.path / name).read_bytes()


@pytest.mark.preservation
def test_validate_bag_passes_on_good_bag(tmp_path: Path, payload_sources: dict[str, Path]) -> None:
    """A freshly written, untouched bag validates with an all-ok report."""
    bag = _make_bag(tmp_path / "bag", payload_sources)
    report = validate_bag(bag.path)
    assert report.ok
    # Two payload files times two algorithms = four per-file checks.
    assert report.checked == 4


@pytest.mark.preservation
def test_validate_bag_detects_corrupted_payload_byte(
    tmp_path: Path, payload_sources: dict[str, Path]
) -> None:
    """Flipping one payload byte makes ``validate_bag`` report not-ok.

    This is the central preservation guarantee: a bag that has silently rotted on
    disk cannot pass validation. The audit report names every drifted file
    (inspectability) rather than merely a count.
    """
    bag = _make_bag(tmp_path / "bag", payload_sources)
    target = bag.payload_dir / "photo.jpg"
    raw = bytearray(target.read_bytes())
    raw[0] ^= 0x01
    target.write_bytes(bytes(raw))

    report = validate_bag(bag.path)
    assert not report.ok
    assert any(r.path.endswith("photo.jpg") for r in report.failed)


@pytest.mark.preservation
def test_validate_bag_raises_when_manifest_file_missing(
    tmp_path: Path, payload_sources: dict[str, Path]
) -> None:
    """Deleting a declared payload file raises :class:`BagValidationError`."""
    bag = _make_bag(tmp_path / "bag", payload_sources)
    (bag.payload_dir / "photo.jpg").unlink()
    with pytest.raises(BagValidationError):
        validate_bag(bag.path)


@pytest.mark.preservation
def test_validate_bag_raises_on_undeclared_payload(
    tmp_path: Path, payload_sources: dict[str, Path]
) -> None:
    """A payload file present on disk but in no manifest is rejected.

    Completeness: undeclared bytes are as suspicious as missing ones.
    """
    bag = _make_bag(tmp_path / "bag", payload_sources)
    (bag.payload_dir / "smuggled.bin").write_bytes(b"not in any manifest")
    with pytest.raises(BagValidationError):
        validate_bag(bag.path)


@pytest.mark.preservation
def test_validate_bag_raises_without_bagit_txt(
    tmp_path: Path, payload_sources: dict[str, Path]
) -> None:
    """A bag missing ``bagit.txt`` is structurally invalid and raises."""
    bag = _make_bag(tmp_path / "bag", payload_sources)
    (bag.path / "bagit.txt").unlink()
    with pytest.raises(BagValidationError):
        validate_bag(bag.path)


@pytest.mark.preservation
def test_write_bag_rejects_empty_algorithm_set(
    tmp_path: Path, payload_sources: dict[str, Path]
) -> None:
    """Asking for a bag with no hash algorithm raises rather than producing one."""
    with pytest.raises(BagValidationError):
        write_bag(tmp_path / "bag", payload_sources, algos=())
