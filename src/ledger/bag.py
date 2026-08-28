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

import os
import secrets
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from ledger.errors import BagValidationError
from ledger.fixity import CHUNK_SIZE, AuditReport, hash_file, hash_file_multi, verify_file
from ledger.models import FixityResult, HashAlgo

_BAGIT_VERSION = "1.0"
_TAG_FILE_ENCODING = "UTF-8"
_BAGIT_TXT = "bagit.txt"
_BAG_INFO_TXT = "bag-info.txt"
_DATA_PREFIX = "data/"
# Two-space separator between digest and path, per RFC 8493 manifest grammar.
_SEP = "  "

# Tag files ledger itself writes into a bag at ingest (:func:`ledger.ingest.
# ingest_sip`) or export (:mod:`ledger.export_drive`). ``record.json`` is the
# policy-bearing one: it carries every field's AccessPolicy, so it is precisely
# the file an attacker rewrites to turn a sealed value into a public one. These
# are required to be covered by EVERY tag manifest whenever they are present at
# the bag root -- see :func:`_tag_coverage_results`.
_LEDGER_TAG_FILES = ("record.json", "premis.json", "dublincore.json")


def _reject_unsafe_relpath(relpath: str, *, context: str) -> None:
    """Refuse a payload path that could escape ``data/`` or the bag.

    A manifest or payload key like ``../../etc/passwd`` or ``/abs/path`` must never
    be joined and written/read: doing so would let a crafted or corrupted bag write
    or hash files outside the bag (a path-traversal vulnerability). Validation is
    purely lexical, so it holds before any file is touched (securability, safety).
    """
    posix = PurePosixPath(relpath)
    windows = PureWindowsPath(relpath)
    if (
        not relpath
        or relpath.startswith("/")
        or "\\" in relpath
        or posix.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or ".." in posix.parts
        or ".." in windows.parts
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


def atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` as UTF-8 atomically (temp file + fsync + ``os.replace``).

    Explicit ``\\n`` newlines with no platform translation keep bags
    byte-identical across operating systems (reproducibility, portability). The
    temp-file + ``os.replace`` discipline (the same one as ``PremisLog.write``)
    means a crash mid-write leaves the previous good file intact instead of a
    torn one that reads as tampering at the next audit (integrity, fault
    tolerance). Note this makes each *file* atomic; a multi-file sequence (e.g.
    a manifest rewrite followed by a reseal) still has a between-files crash
    window, which the caller must keep as small as possible.

    The temp file carries a random suffix rather than ``os.getpid()``. Every thread of
    the browse server shares one process id, so a pid-derived temp name is the *same*
    path in each of them: two concurrent writers truncate and write one another's temp
    file, then race to ``os.replace`` a path the other already renamed away, surfacing
    as :class:`FileNotFoundError` out of a request thread (#155's second cause). A
    failed write also removes its own temp file rather than leaving a stray ``.tmp``
    beside a bag that BagIt validation would then have to account for.
    """
    path = Path(path)
    tmp = path.with_name(f"{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        with open(tmp, "wb") as handle:
            handle.write(text.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _write_text(path: Path, text: str) -> None:
    """Bag-internal alias for :func:`atomic_write_text`."""
    atomic_write_text(path, text)


#: The three characters RFC 8493 §2.1.3 requires a manifest filepath to
#: percent-encode, and the encodings it names. ``%`` MUST be first: it is the escape
#: character, so encoding it after CR/LF would double-encode the ``%`` this table
#: just introduced.
_PERCENT_ENCODINGS: tuple[tuple[str, str], ...] = (
    ("%", "%25"),
    ("\r", "%0D"),
    ("\n", "%0A"),
)


def _encode_manifest_path(relpath: str) -> str:
    """Percent-encode a payload path for a manifest line, per RFC 8493 §2.1.3.

    Only ``%``, CR, and LF are encoded — the RFC names exactly these three and no
    others, so a UTF-8 filename with spaces, accents, or emoji stays readable rather
    than being mangled into ``%``-soup no human can check against a directory listing.

    This is not pedantry about a spec. ``bag.py`` chooses BagIt precisely so that
    "any conformant tool — now or decades from now, run by people who never met us —
    can validate and unpack a ledger bag without ledger itself", and an unencoded
    ``%`` breaks exactly that: the Library of Congress ``bagit-python`` reference
    implementation percent-*decodes* what it reads, so a ledger payload named ``%41``
    resolved to a file called ``A``. A filename containing a newline broke the
    line-oriented grammar outright. ledger round-tripped its own bags either way,
    because both halves were consistently wrong — which is the worst shape for an
    interoperability defect to take, since nothing local can see it.
    """
    for char, escape in _PERCENT_ENCODINGS:
        relpath = relpath.replace(char, escape)
    return relpath


def _decode_manifest_path(relpath: str) -> str:
    """Decode a manifest filepath written per RFC 8493 §2.1.3.

    Deliberately decodes **only** the three escapes the RFC defines, and leaves any
    other ``%`` sequence alone. That is what makes this a migration rather than a
    flag day: a bag written by an older ledger carries raw, unencoded ``%``
    characters, and a general percent-decoder would silently turn a payload named
    ``%41`` into a lookup for ``A`` — corrupting the read of every existing bag in
    order to fix the write of new ones.

    The one case this cannot disambiguate is a *pre-migration* bag holding a file
    literally named ``%25``, which reads back as ``%``. That ambiguity is inherent to
    introducing an escape character after the fact; it is documented rather than
    hidden, and :func:`migrate_manifest_encoding` exists so an archive can move to
    unambiguous manifests deliberately.
    """
    for char, escape in _PERCENT_ENCODINGS:
        relpath = relpath.replace(escape, char)
    return relpath


def _manifest_body(entries: Mapping[str, str]) -> str:
    """Render manifest lines ``<hex>  <path>`` sorted by path, newline-terminated.

    Sorting by path makes the manifest a deterministic function of its inputs
    (reproducibility); the trailing newline matches the RFC's line-oriented grammar.
    Paths are percent-encoded per §2.1.3 on the way out (see
    :func:`_encode_manifest_path`). Sorting happens on the *decoded* path so the
    ordering stays a property of the payload rather than of its escaping.
    """
    lines = [f"{entries[p]}{_SEP}{_encode_manifest_path(p)}" for p in sorted(entries)]
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
    (efficiency via :func:`~ledger.fixity.hash_file_multi`).

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
        # Stream the copy in fixed-size chunks (shutil.copyfileobj) rather than
        # ``write_bytes(read_bytes())``, which would hold the entire payload in RAM
        # — the difference between bagging a multi-gigabyte oral-history video and
        # exhausting memory on the "one inexpensive box" the archive targets. True of
        # bagging, and of every step FIX-03 reached; NOT true of sealing a payload at
        # rest, which is why SEALED payloads are size-capped (ADR 0011).
        with source.open("rb") as src_handle, dest.open("wb") as dest_handle:
            shutil.copyfileobj(src_handle, dest_handle, length=CHUNK_SIZE)
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


def refresh_tag_manifests(bag_dir: Path) -> Bag:
    """Recompute every ``tagmanifest-<algo>.txt`` from the bag's current tag files.

    A lawful post-ingest change rewrites a *tag* file inside a sealed bag — the
    record manifest after a consent/policy change, ``premis.json`` after a new
    event, the Dublin Core sidecar after an edit. Those files are covered by the tag
    manifests, so once their bytes change the stale tag manifests no longer match and
    :func:`validate_bag` reports the bag's own tag files as failing — making a
    legitimate steward action indistinguishable from tampering at the next audit
    (the core integrity claim, silently broken for every archive that ever changes a
    record). This reseals the bag by recomputing the tag-manifest digests over the
    current tag files so the bag re-validates.

    Only the *tag* manifests are touched. The payload manifests (``manifest-*.txt``)
    — the real content fixity — are left exactly as written at ingest, so a byte
    flipped in a payload file, or a payload manifest edited by hand, is still caught
    (integrity: this reseals metadata revisions, it does not paper over content rot).

    The set of tag files is the union of the entries the existing tag manifests
    already declare — the canonical set sealed at :func:`write_bag` time — never
    "whatever happens to be at the bag root". A stray file that drifted in beside
    the bag (an OS index file, an editor backup) is not silently sealed into the
    archive's integrity claim, and its later disappearance cannot fail a
    validation it was never part of. Each declared tag file is re-hashed under
    each algorithm an existing tag manifest declares, so a refreshed bag is
    byte-identical to one :func:`write_bag` would have emitted for the same
    tag-file contents (reproducibility). Raises
    :class:`~ledger.errors.BagValidationError` if the bag has no tag manifest to
    refresh, or if a declared tag file is missing on disk (fail closed: a reseal
    must never paper over a vanished tag file).
    """
    bag_dir = Path(bag_dir)
    tagmanifest_paths = sorted(bag_dir.glob("tagmanifest-*.txt"))
    if not tagmanifest_paths:
        raise BagValidationError(f"no tag manifest to refresh: {bag_dir}")

    # The canonical tag set: what the bag's own tag manifests already declare
    # (a tag manifest never lists itself, per RFC 8493). Sorted for determinism.
    declared: set[str] = set()
    for tagmanifest_path in tagmanifest_paths:
        declared.update(_parse_manifest(tagmanifest_path))
    tag_files = sorted(declared)
    for name in tag_files:
        _reject_unsafe_relpath(name, context="tagmanifest refresh")
        if not (bag_dir / name).is_file():
            raise BagValidationError(f"declared tag file absent, refusing to reseal: {name}")
    for tagmanifest_path in tagmanifest_paths:
        algo = _algo_of_manifest(tagmanifest_path)
        tag_entries = {name: hash_file(bag_dir / name, algo) for name in tag_files}
        _write_text(tagmanifest_path, _manifest_body(tag_entries))
    return Bag(path=bag_dir)


def migrate_manifest_encoding(bag_dir: Path) -> bool:
    """Rewrite a bag's manifests with RFC 8493 §2.1.3 percent-encoding.

    The migration path for bags written before ledger encoded anything. It is only
    needed by an archive holding a payload whose name contains ``%``, CR, or LF —
    every other bag is already byte-identical under both rules, which is why this is
    a deliberate maintenance action rather than an automatic rewrite of an archive's
    entire history at upgrade time. Reading is migrated for free: those bags keep
    validating untouched, because :func:`_decode_manifest_path` only decodes the
    three escapes the RFC defines and leaves any other ``%`` alone.

    Rewriting a payload manifest changes bytes the *tag* manifests cover, so the bag
    is resealed through :func:`refresh_tag_manifests` — the same route a lawful
    post-ingest metadata change takes. The payload digests themselves are copied
    across untouched: this re-serialises how paths are spelled, and must never be
    able to paper over content rot.

    Idempotent, and returns whether anything changed, so running it across a whole
    archive does not churn tag manifests on bags that needed nothing.
    """
    bag_dir = Path(bag_dir)
    manifest_paths = sorted(bag_dir.glob("manifest-*.txt"))
    if not manifest_paths:
        raise BagValidationError(f"no payload manifest to migrate: {bag_dir}")
    changed = False
    for manifest_path in manifest_paths:
        before = manifest_path.read_text(encoding="utf-8")
        after = _manifest_body(_parse_manifest(manifest_path))
        if after != before:
            _write_text(manifest_path, after)
            changed = True
    if changed:
        refresh_tag_manifests(bag_dir)
    return changed


def _required_tag_files(bag_dir: Path) -> list[str]:
    """The tag files every tag manifest in ``bag_dir`` MUST cover.

    A bag's structural tag files (``bagit.txt``, ``bag-info.txt``, and every
    payload manifest actually on disk) plus whichever of :data:`_LEDGER_TAG_FILES`
    the bag contains. Deliberately *not* "every file at the bag root": an OS index
    file or a crash-orphaned ``*.tmp`` that drifted in beside a bag was never part
    of the bag's integrity claim, and :func:`refresh_tag_manifests` pointedly
    refuses to seal such a stray in — so demanding coverage of it would turn
    incidental filesystem litter into a permanent, unfixable audit failure
    (operability). Every name here, by contrast, is one a ledger bag writer always
    emits *and* always declares, so requiring its coverage can only ever fire on a
    manifest that was edited after the fact.
    """
    required = [_BAGIT_TXT, _BAG_INFO_TXT]
    required.extend(sorted(p.name for p in bag_dir.glob("manifest-*.txt")))
    required.extend(name for name in _LEDGER_TAG_FILES if (bag_dir / name).is_file())
    return required


def _tag_coverage_results(bag_dir: Path, tagmanifest_paths: Sequence[Path]) -> list[FixityResult]:
    """One failing result per tag file a tag manifest fails to declare.

    Verifying only the entries a tag manifest *lists* is a check that cannot fail
    for a file the manifest does not mention. An attacker with disk access needs no
    hash collision and no re-sealing to exploit that: deleting ``record.json``'s
    line from every ``tagmanifest-*.txt`` and then rewriting ``record.json`` to
    flip a ``sealed-until`` field to ``public`` leaves the bag validating clean,
    :meth:`~ledger.ingest.Archive.audit_fixity` green, and the signed health
    attestation reporting ``fixity_ok: true`` — while the read path now discloses
    the embargoed value to an anonymous viewer. The PREMIS hash chain does not
    cover this: ``record.json`` is not in the log.

    So completeness is enforced on the tag side exactly as it already is on the
    payload side ("undeclared bytes are as suspicious as missing ones"), and it is
    enforced per manifest rather than across their union, so dropping a line from
    one algorithm's manifest cannot hide behind the other's.

    Reported as failing :class:`~ledger.models.FixityResult` entries rather than a
    raised :class:`~ledger.errors.BagValidationError` so the rest of the bag's
    per-file outcomes still reach the steward in the same report (failure
    transparency), and so a replica that arrives this way is quarantined by the
    ordinary ``report.ok`` path.
    """
    required = _required_tag_files(bag_dir)
    results: list[FixityResult] = []
    for tagmanifest_path in tagmanifest_paths:
        declared = set(_parse_manifest(tagmanifest_path))
        for name in required:
            if name not in declared:
                results.append(
                    FixityResult(
                        path=name,
                        algo=_algo_of_manifest(tagmanifest_path),
                        expected=f"covered by {tagmanifest_path.name}",
                        actual=f"absent from {tagmanifest_path.name}",
                    )
                )
    return results


def _parse_manifest(path: Path) -> dict[str, str]:
    """Parse a BagIt manifest into ``{path: hex_digest}``.

    Splits each non-empty line on the first run of whitespace per the RFC grammar,
    then percent-decodes the filepath per §2.1.3 (see :func:`_decode_manifest_path`
    for why the decoder is deliberately narrow, and how bags written before ledger
    encoded anything keep validating).

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
        entries[_decode_manifest_path(rel)] = digest
    return entries


def _algo_of_manifest(manifest_path: Path) -> HashAlgo:
    """Parse the algorithm out of a ``manifest-<algo>.txt`` filename.

    Raises :class:`~ledger.errors.BagValidationError` (not a bare ``ValueError``)
    on an unknown algorithm, so every malformed-bag failure is the one type a
    caller catches (analyzability, robustness).
    """
    try:
        return HashAlgo(manifest_path.stem.split("-", 1)[1])
    except (IndexError, ValueError) as exc:
        raise BagValidationError(f"unknown manifest algorithm: {manifest_path.name}") from exc


# Pre-existing complexity (one function walks the full RFC 8493 structural +
# fixity check); surfaced 2026-07-05 when CQ-05's complexity gate was enabled.
# Waived, not re-muted: this is preservation-integrity code, so a split is tracked
# as a deliberate, well-tested follow-up rather than rushed under audit time
# pressure. Tracked in issue #83.
def validate_bag(bag_dir: Path) -> AuditReport:  # noqa: C901 - BagIt validation reports EVERY failure rather than stopping at the first, so each check is its own branch (#83)
    """Validate the bag at ``bag_dir`` against RFC 8493 structure and manifests.

    Structural failures raise :class:`~ledger.errors.BagValidationError`:

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
    against every tag manifest -- including the check that each of those tag files
    is *declared* by every tag manifest in the first place, so a manifest edited to
    simply drop a line cannot exempt a file from being hashed
    (:func:`_tag_coverage_results`). The combined :class:`~ledger.fixity.AuditReport` is
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
    tagmanifest_paths = sorted(bag_dir.glob("tagmanifest-*.txt"))
    # Completeness FIRST: a tag file no manifest declares is never re-hashed below,
    # so without this the loop is a check that cannot fail for exactly the file an
    # attacker would rewrite (see _tag_coverage_results).
    results.extend(_tag_coverage_results(bag_dir, tagmanifest_paths))
    for tagmanifest_path in tagmanifest_paths:
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
