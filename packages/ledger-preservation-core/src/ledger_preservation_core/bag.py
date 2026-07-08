"""BagIt packaging per IETF RFC 8493 — a self-describing preservation hand-off unit.

A *bag* is a self-describing directory: payload files live under ``data/`` and
tag files at the top level enumerate and checksum everything. A bag is the unit
a caller replicates and exports. Design choices and quality attributes:

* **An open, standardized format** (RFC 8493) -> interoperability, portability,
  and survivability: any conformant tool — now or decades from now, run by people
  who never met the original archive — can validate and unpack a bag without this
  library itself.
* **Deterministic emission** (manifest lines sorted by path, a fixed two-space
  separator, stable tag-file ordering) -> reproducibility: the same payload always
  produces byte-identical manifests, so bags can be diffed, golden-tested, and
  fixity-compared across machines.

.. warning::
   ``bag-info.txt`` is human-readable metadata that travels with the payload in
   the clear. If the caller's domain has a notion of sealed or sensitive fields
   (as ledger's own contributor-identity model does), it MUST NEVER pass one of
   those values into ``bag_info`` or ``extra_tag_files`` — this module has no
   concept of sealing and will write exactly what it is given. This function
   injects nothing of its own beyond ``Payload-Oxum``; every other
   ``bag-info.txt`` value is caller-controlled, and the caller bears that duty.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ledger_preservation_core.errors import BagValidationError
from ledger_preservation_core.fixity import AuditReport, hash_file, hash_file_multi, verify_file
from ledger_preservation_core.models import HashAlgo

_BAGIT_VERSION = "1.0"
_TAG_FILE_ENCODING = "UTF-8"
_BAGIT_TXT = "bagit.txt"
_BAG_INFO_TXT = "bag-info.txt"
_DATA_PREFIX = "data/"
# Two-space separator between digest and path, per RFC 8493 manifest grammar.
_SEP = "  "


def _reject_unsafe_relpath(relpath: str, *, context: str) -> None:
    """Refuse a payload path that could escape ``data/`` or the bag.

    A manifest or payload key like ``../../etc/passwd`` or ``/abs/path`` must never
    be joined and written/read: doing so would let a crafted or corrupted bag write
    or hash files outside the bag (a path-traversal vulnerability). Validation is
    purely lexical, so it holds before any file is touched (securability, safety).
    """
    pure = PurePosixPath(relpath)
    if (
        not relpath
        or relpath.startswith("/")
        or pure.is_absolute()
        or ".." in pure.parts
        or "\x00" in relpath
    ):
        raise BagValidationError(f"unsafe path in {context}: {relpath!r}")


def _reject_unsafe_tagname(name: str) -> None:
    """Refuse a tag-file name that is anything but a plain top-level filename."""
    if not name or "/" in name or "\\" in name or name in {".", ".."} or "\x00" in name:
        raise BagValidationError(f"unsafe tag-file name: {name!r}")


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
    extra_tag_files: Mapping[str, bytes] | None = None,
) -> Bag:
    """Write a RFC 8493 bag at ``bag_dir`` containing ``payload``.

    ``payload`` maps a payload-relative path (placed under ``data/``) to the source
    file to copy in. For each algorithm in ``algos`` a payload manifest and a tag
    manifest are written; the payload is read once per file to compute all digests
    (efficiency via :func:`~ledger_preservation_core.fixity.hash_file_multi`).

    Emitted files:

    * ``data/<relpath>`` — the copied payload files.
    * ``manifest-<algo>.txt`` — payload digests, one line per file, sorted by path.
    * ``bagit.txt`` — the version + tag encoding declaration.
    * ``bag-info.txt`` — ``Payload-Oxum`` plus any caller-provided keys (including
      ``Bagging-Date`` only if the caller supplies it; nothing is invented here so
      the bag stays reproducible).
    * ``<name>`` for each entry in ``extra_tag_files`` — written verbatim at the bag
      root (e.g. ``record.json``) and, crucially, covered by the tag manifest so
      their integrity is part of the bag's own fixity (integrity, auditability).
    * ``tagmanifest-<algo>.txt`` — digests of the tag files above, sorted by path.

    .. warning::
       ``bag_info`` values are written verbatim into clear-text ``bag-info.txt``.
       Never pass a contributor identity, contact, or sealed value through it.

    Returns a :class:`Bag` handle to the written directory.
    """
    if not algos:
        raise BagValidationError(f"at least one hash algorithm is required: {bag_dir}")

    # Validate every caller-supplied name lexically BEFORE creating anything, so a
    # traversal attempt never writes a file (securability).
    for relpath in payload:
        _reject_unsafe_relpath(relpath, context="payload")
    for name in extra_tag_files or {}:
        _reject_unsafe_tagname(name)

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

    # Extra tag files (e.g. the record manifest, Dublin Core, PREMIS) written at the
    # bag root so the tag manifest below hashes them too — tampering with a record's
    # access policy or identity_ref then fails validation (integrity).
    for name, content in (extra_tag_files or {}).items():
        (bag_dir / name).write_bytes(content)

    # Tag manifests cover bagit.txt, bag-info.txt, every payload manifest, and every
    # extra tag file.
    tag_files = (
        [_BAGIT_TXT, _BAG_INFO_TXT]
        + [_manifest_name(a) for a in algos]
        + sorted(extra_tag_files or {})
    )
    for algo in algos:
        tag_entries = {name: hash_file(bag_dir / name, algo) for name in tag_files}
        _write_text(bag_dir / _tagmanifest_name(algo), _manifest_body(tag_entries))

    return Bag(path=bag_dir)


def _parse_manifest(path: Path) -> dict[str, str]:
    """Parse a BagIt manifest into ``{path: hex_digest}``.

    Splits each non-empty line on the first run of whitespace per the RFC grammar.
    Raises :class:`~ledger_preservation_core.errors.BagValidationError` on a malformed line.
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


def _algo_of_manifest(manifest_path: Path) -> HashAlgo:
    """Parse the algorithm out of a ``manifest-<algo>.txt`` filename.

    Raises :class:`~ledger_preservation_core.errors.BagValidationError` (not a bare ``ValueError``)
    on an unknown algorithm, so every malformed-bag failure is the one type a
    caller catches (analyzability, robustness).
    """
    try:
        return HashAlgo(manifest_path.stem.split("-", 1)[1])
    except (IndexError, ValueError) as exc:
        raise BagValidationError(f"unknown manifest algorithm: {manifest_path.name}") from exc


# Pre-existing complexity (one function walks the full RFC 8493 structural +
# fixity check). Waived, not re-muted: this is preservation-integrity code, so a
# split is tracked as a deliberate, well-tested follow-up rather than rushed
# under audit time pressure.
def validate_bag(bag_dir: Path) -> AuditReport:  # noqa: C901
    """Validate the bag at ``bag_dir`` against RFC 8493 structure and manifests.

    Structural failures raise :class:`~ledger_preservation_core.errors.BagValidationError`:

    * ``bagit.txt`` is missing, or no ``manifest-<algo>.txt`` exists.
    * A manifest entry escapes its expected root (a ``..``/absolute path — refused
      *before* the file is touched, so a crafted bag cannot hash files outside it).
    * A path listed in a manifest is absent on disk.
    * A payload file on disk is absent from *any* payload manifest (completeness is
      enforced per manifest — undeclared bytes are as suspicious as missing ones).
    * A tag file listed in a tag manifest is missing.

    On a structurally sound bag, every payload file is verified against every
    payload manifest, and every tag file (``bagit.txt``, ``bag-info.txt``, the
    payload manifests, and any extra tag files such as ``record.json``) is verified
    against every tag manifest. The combined :class:`~ledger_preservation_core.fixity.AuditReport` is
    returned so a caller sees each per-file outcome — so tampering with a record's
    access policy or identity_ref is caught, not just payload-byte rot
    (integrity, inspectability, failure transparency).
    """
    if not (bag_dir / _BAGIT_TXT).exists():
        raise BagValidationError(f"missing {_BAGIT_TXT}: {bag_dir}")

    manifest_paths = sorted(bag_dir.glob("manifest-*.txt"))
    if not manifest_paths:
        raise BagValidationError(f"no payload manifest found: {bag_dir}")

    data_dir = bag_dir / "data"
    data_root = data_dir.resolve()
    on_disk = {
        f"{_DATA_PREFIX}{p.relative_to(data_dir).as_posix()}"
        for p in data_dir.rglob("*")
        if p.is_file()
    }

    results = []
    # --- payload manifests: verify entries + per-manifest completeness ---------
    for manifest_path in manifest_paths:
        algo = _algo_of_manifest(manifest_path)
        entries = _parse_manifest(manifest_path)
        for rel in sorted(entries):
            _reject_unsafe_relpath(rel, context=manifest_path.name)
            if not rel.startswith(_DATA_PREFIX):
                raise BagValidationError(f"payload manifest entry outside data/: {rel}")
            target = bag_dir / rel
            if not target.resolve().is_relative_to(data_root):
                raise BagValidationError(f"manifest entry escapes data/: {rel}")
            if not target.exists():
                raise BagValidationError(f"file in {manifest_path.name} absent on disk: {rel}")
            results.append(verify_file(target, algo, entries[rel]))
        # Completeness is checked against THIS manifest: a file missing from even one
        # manifest is a defect (a single weakened algorithm cannot hide a file).
        missing = on_disk - set(entries)
        if missing:
            raise BagValidationError(
                f"payload file absent from {manifest_path.name}: {sorted(missing)[0]}"
            )

    # --- tag manifests: verify the tag files (bagit/bag-info/manifests/extras) ---
    bag_root = bag_dir.resolve()
    for tagmanifest_path in sorted(bag_dir.glob("tagmanifest-*.txt")):
        algo = _algo_of_manifest(tagmanifest_path)
        for rel in sorted(_parse_manifest(tagmanifest_path)):
            _reject_unsafe_relpath(rel, context=tagmanifest_path.name)
            target = bag_dir / rel
            if not target.resolve().is_relative_to(bag_root):
                raise BagValidationError(f"tag manifest entry escapes bag: {rel}")
            if not target.exists():
                raise BagValidationError(f"tag file in {tagmanifest_path.name} absent: {rel}")
        for rel, digest in _parse_manifest(tagmanifest_path).items():
            results.append(verify_file(bag_dir / rel, algo, digest))

    return AuditReport(results=results)
