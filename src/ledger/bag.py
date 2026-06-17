"""BagIt packaging per IETF RFC 8493 — the archive's hand-off format.

A *bag* is a self-describing directory: payload files live under ``data/`` and
tag files at the top level enumerate and checksum everything. Bags are the unit
ledger replicates and exports. Design choices and quality attributes:

* **An open, standardized format** (RFC 8493) -> interoperability, portability,
  and survivability: any conformant tool — now or decades from now, run by people
  who never met us — can validate and unpack a ledger bag without ledger itself.
* **Deterministic emission** (manifest lines sorted by path, a fixed two-space
  separator, stable tag-file ordering) -> reproducibility: the same payload always
  produces byte-identical manifests, so bags can be diffed, golden-tested, and
  fixity-compared across machines.

.. warning::
   ``bag-info.txt`` is human-readable metadata that travels with the payload in
   the clear. It MUST NEVER carry a contributor's identity, contact, or any sealed
   field value. Identity lives only in the encrypted vault (:mod:`ledger.identity`).
   This function injects nothing of its own beyond ``Payload-Oxum``; every other
   ``bag-info.txt`` value is caller-controlled, and the caller bears the same duty.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ledger.errors import BagValidationError
from ledger.fixity import AuditReport, hash_file, hash_file_multi, verify_file
from ledger.models import HashAlgo

_BAGIT_VERSION = "1.0"
_TAG_FILE_ENCODING = "UTF-8"
_BAGIT_TXT = "bagit.txt"
_BAG_INFO_TXT = "bag-info.txt"
_DATA_PREFIX = "data/"
# Two-space separator between digest and path, per RFC 8493 manifest grammar.
_SEP = "  "


@dataclass(frozen=True)
class Bag:
    """A handle to a BagIt bag on disk."""

    path: Path

    @property
    def payload_dir(self) -> Path:
        """The ``data/`` directory holding the payload files."""
        return self.path / "data"

    @property
    def name(self) -> str:
        """The bag's directory name (its identifier on disk)."""
        return self.path.name


def _manifest_name(algo: HashAlgo) -> str:
    """The payload-manifest filename for ``algo`` (e.g. ``manifest-sha256.txt``)."""
    return f"manifest-{algo.value}.txt"


def _tagmanifest_name(algo: HashAlgo) -> str:
    """The tag-manifest filename for ``algo`` (e.g. ``tagmanifest-sha256.txt``)."""
    return f"tagmanifest-{algo.value}.txt"


def _write_text(path: Path, text: str) -> None:
    """Write ``text`` as UTF-8 with explicit newlines, no platform translation.

    Disabling newline translation keeps bags byte-identical across operating
    systems (reproducibility, portability).
    """
    path.write_text(text, encoding="utf-8", newline="\n")


def _manifest_body(entries: Mapping[str, str]) -> str:
    """Render manifest lines ``<hex>  <path>`` sorted by path, newline-terminated.

    Sorting by path makes the manifest a deterministic function of its inputs
    (reproducibility); the trailing newline matches the RFC's line-oriented grammar.
    """
    lines = [f"{entries[p]}{_SEP}{p}" for p in sorted(entries)]
    return "".join(f"{line}\n" for line in lines)


def write_bag(
    bag_dir: Path,
    payload: Mapping[str, Path],
    *,
    algos: Sequence[HashAlgo] = (HashAlgo.SHA256, HashAlgo.BLAKE2B),
    bag_info: Mapping[str, str] | None = None,
) -> Bag:
    """Write a RFC 8493 bag at ``bag_dir`` containing ``payload``.

    ``payload`` maps a payload-relative path (placed under ``data/``) to the source
    file to copy in. For each algorithm in ``algos`` a payload manifest and a tag
    manifest are written; the payload is read once per file to compute all digests
    (efficiency via :func:`~ledger.fixity.hash_file_multi`).

    Emitted files:

    * ``data/<relpath>`` — the copied payload files.
    * ``manifest-<algo>.txt`` — payload digests, one line per file, sorted by path.
    * ``bagit.txt`` — the version + tag encoding declaration.
    * ``bag-info.txt`` — ``Payload-Oxum`` plus any caller-provided keys (including
      ``Bagging-Date`` only if the caller supplies it; nothing is invented here so
      the bag stays reproducible).
    * ``tagmanifest-<algo>.txt`` — digests of the tag files above, sorted by path.

    .. warning::
       ``bag_info`` values are written verbatim into clear-text ``bag-info.txt``.
       Never pass a contributor identity, contact, or sealed value through it.

    Returns a :class:`Bag` handle to the written directory.
    """
    if not algos:
        raise BagValidationError(f"at least one hash algorithm is required: {bag_dir}")

    data_dir = bag_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # Copy payload and accumulate per-algorithm digests in a single read per file.
    manifests: dict[HashAlgo, dict[str, str]] = {algo: {} for algo in algos}
    total_bytes = 0
    file_count = 0
    for relpath, source in payload.items():
        dest = data_dir / relpath
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(source.read_bytes())
        digests = hash_file_multi(dest, algos)
        manifest_path = f"{_DATA_PREFIX}{relpath}"
        for algo in algos:
            manifests[algo][manifest_path] = digests[algo]
        total_bytes += dest.stat().st_size
        file_count += 1

    # Payload manifests.
    for algo in algos:
        _write_text(bag_dir / _manifest_name(algo), _manifest_body(manifests[algo]))

    # bagit.txt — the format declaration every reader checks first.
    _write_text(
        bag_dir / _BAGIT_TXT,
        f"BagIt-Version: {_BAGIT_VERSION}\nTag-File-Character-Encoding: {_TAG_FILE_ENCODING}\n",
    )

    # bag-info.txt — Payload-Oxum first, then caller keys in given order.
    info_lines = [f"Payload-Oxum: {total_bytes}.{file_count}"]
    if bag_info is not None:
        info_lines.extend(f"{key}: {value}" for key, value in bag_info.items())
    _write_text(bag_dir / _BAG_INFO_TXT, "".join(f"{line}\n" for line in info_lines))

    # Tag manifests cover bagit.txt, bag-info.txt, and every payload manifest.
    tag_files = [_BAGIT_TXT, _BAG_INFO_TXT] + [_manifest_name(a) for a in algos]
    for algo in algos:
        tag_entries = {name: hash_file(bag_dir / name, algo) for name in tag_files}
        _write_text(bag_dir / _tagmanifest_name(algo), _manifest_body(tag_entries))

    return Bag(path=bag_dir)


def _parse_manifest(path: Path) -> dict[str, str]:
    """Parse a BagIt manifest into ``{path: hex_digest}``.

    Splits each non-empty line on the first run of whitespace per the RFC grammar.
    Raises :class:`~ledger.errors.BagValidationError` on a malformed line.
    """
    entries: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        digest, sep, rel = line.partition("  ")
        if not sep:
            digest, _, rel = line.partition(" ")
        rel = rel.strip()
        if not digest or not rel:
            raise BagValidationError(f"malformed manifest line in {path.name}")
        entries[rel] = digest
    return entries


def validate_bag(bag_dir: Path) -> AuditReport:
    """Validate the bag at ``bag_dir`` against RFC 8493 structure and manifests.

    Structural failures raise :class:`~ledger.errors.BagValidationError`:

    * ``bagit.txt`` is missing.
    * No ``manifest-<algo>.txt`` exists.
    * A path listed in a manifest is absent on disk.
    * A payload file on disk appears in no manifest (completeness — undeclared
      bytes are as suspicious as missing ones).

    On a structurally sound bag, every payload file is verified against every
    manifest digest and the combined :class:`~ledger.fixity.AuditReport` returned,
    so a caller sees each per-file outcome (inspectability, failure transparency).
    """
    if not (bag_dir / _BAGIT_TXT).exists():
        raise BagValidationError(f"missing {_BAGIT_TXT}: {bag_dir}")

    manifest_paths = sorted(bag_dir.glob("manifest-*.txt"))
    if not manifest_paths:
        raise BagValidationError(f"no payload manifest found: {bag_dir}")

    data_dir = bag_dir / "data"
    on_disk = {
        f"{_DATA_PREFIX}{p.relative_to(data_dir).as_posix()}"
        for p in data_dir.rglob("*")
        if p.is_file()
    }

    declared: set[str] = set()
    results = []
    for manifest_path in manifest_paths:
        # manifest-<algo>.txt -> the algo between the first '-' and '.txt'.
        algo = HashAlgo(manifest_path.stem.split("-", 1)[1])
        entries = _parse_manifest(manifest_path)
        for rel in sorted(entries):
            declared.add(rel)
            target = bag_dir / rel
            if not target.exists():
                raise BagValidationError(f"file in {manifest_path.name} absent on disk: {rel}")
            results.append(verify_file(target, algo, entries[rel]))

    missing_from_manifest = on_disk - declared
    if missing_from_manifest:
        offending = sorted(missing_from_manifest)[0]
        raise BagValidationError(f"payload file absent from manifest: {offending}")

    return AuditReport(results=results)
