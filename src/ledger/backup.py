"""Scheduled, encrypted, off-box backups of the whole archive (roadmap RM10).

``verify-backup`` (backlog K1) already proves a *restored* copy is intact, but it
assumes a steward has some other way to make the copy and move it off the box. The
infra runbook's backup obligations were manual: snapshot the data volume, keep the
vault key apart. This module turns that manual discipline into one command a cron
job can run nightly, so a community on a single inexpensive box gets a real,
tested, off-box disaster-recovery story (durability, self-sustainability).

What it does, and the qualities it serves:

* :func:`create_backup` tars the whole archive root (``store/`` — which holds every
  bag, the records, the PREMIS logs, and ``config.json`` — plus the encrypted
  ``identity.vault``) into a single archive, derives a 32-byte key from a human
  *passphrase* with the **exact** scrypt parameters the identity vault uses
  (:meth:`~ledger.identity.IdentityVault.derive_key`), and encrypts the tar with
  authenticated Fernet. The output is ``ledger-backup-<UTC>.tar.fernet`` plus a
  small JSON sidecar manifest. A stolen backup is ciphertext only — confidentiality
  even off-box, on a host the community does not control.
* :func:`restore_backup` decrypts, untars, and then runs the **same**
  readability + RFC 8493 fixity checks ``verify-backup`` runs, so every restore is
  verified rather than merely unpacked — an untested restore is a hope, not a
  restore (failure transparency).
* :func:`prune_backups` keeps the *N* newest and removes the rest, so an unattended
  nightly job does not fill the disk (operability).

The no-outing rule holds here as everywhere: this module tars and encrypts the
vault ciphertext but never *decrypts* it, never opens the vault, and never puts an
identity, a payload byte, a passphrase, a salt-derived key, or ciphertext into a
log line, an exception message, or a :class:`BackupReport`. The backup *passphrase*
is a separate secret from the *vault key* and is kept apart from both the data and
the vault key (see ``docs/BACKUP-RUNBOOK.md``).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import secrets
import tarfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from ledger.config import Config
from ledger.errors import LedgerError
from ledger.identity import IdentityVault
from ledger.ingest import Archive
from ledger.models import canonical_json, now_iso

_CONFIG_FILENAME = "config.json"

# The single archive filename pattern and its sidecar. The timestamp is UTC and
# filesystem-safe (no colons), so backups sort chronologically by name.
_BACKUP_PREFIX = "ledger-backup-"
_BACKUP_SUFFIX = ".tar.fernet"
_MANIFEST_SUFFIX = ".manifest.json"

# A fresh random salt per backup so two backups of the same archive under the same
# passphrase derive different keys (the salt is stored in the sidecar; it is not a
# secret). 16 bytes matches common scrypt-salt guidance.
_SALT_NBYTES = 16


class BackupError(LedgerError):
    """A backup could not be created, or a restore could not be completed.

    Like every ledger error, the message names only the *condition* and at most a
    file path — never a passphrase, a derived key, ciphertext, or a contributor
    identity (no-outing rule).
    """


@dataclass(frozen=True)
class BackupReport:
    """The no-outing-safe outcome of :func:`create_backup`.

    Carries only paths, counts, a timestamp, and the SHA-256 of the *ciphertext*
    (an integrity check a steward can re-compute) — never the passphrase, the
    derived key, the salt, or any plaintext, so it is safe to print or log.
    """

    archive_path: Path
    manifest_path: Path
    created_at: str
    bag_count: int
    ciphertext_sha256: str
    ciphertext_bytes: int


@dataclass(frozen=True)
class VerifyReport:
    """The no-outing-safe outcome of :func:`verify_backup` / a restore's re-check.

    ``bag_results`` is one ``(bag_name, ok, files_checked)`` triple per bag — bag
    names and counts only, never a payload byte or an identity.
    """

    ok: bool
    reason: str
    bag_results: list[tuple[str, bool, int]]
    failures: int


# --- create -----------------------------------------------------------------


def _archive_root(config: Config) -> Path:
    """The archive root that holds ``store/`` and ``identity.vault``.

    Both live under one parent by construction (:meth:`Config.default`), so the
    root is the store's parent. Used only to resolve the two things to tar.
    """
    return Path(config.store_root).parent


def _count_bags(config: Config) -> int:
    """Number of BagIt bags under the store, or 0 if none yet (affordability)."""
    bags_dir = Path(config.store_root) / "bags"
    if not bags_dir.is_dir():
        return 0
    return sum(1 for p in bags_dir.iterdir() if p.is_dir())


def _build_tar_bytes(config: Config) -> bytes:
    """Tar ``store/`` and ``identity.vault`` into in-memory bytes (deterministic layout).

    The two members are added under fixed arcnames (``store`` and
    ``identity.vault``) so the archive restores to ``<target>/store`` and
    ``<target>/identity.vault`` regardless of the original box's absolute paths —
    portability across hosts. The vault is added *as an opaque file*; its bytes are
    never decrypted here (no-outing rule). A missing vault is allowed: a young
    archive may have sealed no one yet.
    """
    store_root = Path(config.store_root)
    if not store_root.is_dir():
        raise BackupError(f"store root is missing or not a directory: {store_root}")
    vault_path = Path(config.vault_path)

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        tar.add(str(store_root), arcname="store", recursive=True)
        if vault_path.exists():
            tar.add(str(vault_path), arcname="identity.vault", recursive=False)
    return buffer.getvalue()


def create_backup(config: Config, dest_dir: Path, passphrase: str) -> BackupReport:
    """Tar, encrypt, and write one off-box backup of the archive under *config*.

    Derives a Fernet key from *passphrase* with a fresh random salt using the exact
    scrypt parameters the identity vault uses (:meth:`IdentityVault.derive_key`),
    encrypts the tar with authenticated Fernet, and writes
    ``<dest_dir>/ledger-backup-<UTC>.tar.fernet`` plus a JSON sidecar manifest
    (``created-at``, ``salt``, the ciphertext SHA-256, and the bag count). Returns a
    :class:`BackupReport`. The passphrase, the derived key, and every plaintext byte
    stay in memory only and never reach disk in the clear, a log, or the report
    (confidentiality, no-outing rule).

    Raises :class:`BackupError` on a missing store or an unwritable destination.
    """
    dest_dir = Path(dest_dir)
    archive_root = _archive_root(config).resolve()
    resolved_dest = dest_dir.resolve()
    if resolved_dest == archive_root or archive_root in resolved_dest.parents:
        raise BackupError(
            "backup destination must be outside the archive root; an on-archive copy "
            "is not an off-box backup"
        )
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BackupError(f"backup destination is not writable: {dest_dir}") from exc

    plaintext = _build_tar_bytes(config)
    salt = secrets.token_bytes(_SALT_NBYTES)
    key = IdentityVault.derive_key(passphrase, salt)
    ciphertext = Fernet(key).encrypt(plaintext)

    created_at = now_iso()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    archive_path = dest_dir / f"{_BACKUP_PREFIX}{stamp}{_BACKUP_SUFFIX}"
    manifest_path = dest_dir / f"{_BACKUP_PREFIX}{stamp}{_MANIFEST_SUFFIX}"

    digest = hashlib.sha256(ciphertext).hexdigest()
    bag_count = _count_bags(config)
    manifest = {
        "schema": "ledger-backup/1",
        "created-at": created_at,
        "salt": salt.hex(),
        "ciphertext-sha256": digest,
        "ciphertext-bytes": len(ciphertext),
        "bag-count": bag_count,
        "archive-file": archive_path.name,
    }

    # Write the ciphertext owner-only (0o600) BEFORE any bytes land, so an off-box
    # backup is never momentarily world-readable on a shared host (confidentiality).
    try:
        fd = os.open(archive_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(ciphertext)
        manifest_path.write_text(canonical_json(manifest) + "\n", encoding="utf-8")
    except OSError as exc:
        archive_path.unlink(missing_ok=True)
        manifest_path.unlink(missing_ok=True)
        raise BackupError(f"backup could not be written under: {dest_dir}") from exc

    return BackupReport(
        archive_path=archive_path,
        manifest_path=manifest_path,
        created_at=created_at,
        bag_count=bag_count,
        ciphertext_sha256=digest,
        ciphertext_bytes=len(ciphertext),
    )


# --- verify -----------------------------------------------------------------


def verify_backup(backup_root: Path) -> VerifyReport:
    """Re-validate a restored archive root *in place* (shared by CLI + restore).

    Point this at a directory holding ``store/`` and ``identity.vault``. It
    re-points the config at that location (the stored paths are the original box's),
    confirms the store — and, when ``LEDGER_VAULT_KEY`` is set, the vault — are
    readable *without unsealing anything*, then runs full RFC 8493 fixity over every
    bag. Returns a :class:`VerifyReport` naming only bags and counts (no-outing
    rule). This is the exact logic behind ``ledger verify-backup``, factored here so
    a restore verifies through the same path.
    """
    backup_root = Path(backup_root)
    config = Config.load(backup_root / "store" / _CONFIG_FILENAME)
    # The config records the ORIGINAL box's absolute paths; re-point it at the
    # restored copy so we verify the bytes on disk, not wherever they first lived.
    config.store_root = str(backup_root / "store")
    config.vault_path = str(backup_root / "identity.vault")
    archive = Archive(config)

    ready, reason = archive.check_readiness()
    if not ready:
        return VerifyReport(ok=False, reason=reason, bag_results=[], failures=0)

    results: list[tuple[str, bool, int]] = []
    failures = 0
    for name, report in archive.audit_fixity():
        ok = report.ok
        if not ok:
            failures += 1
        results.append((name, ok, report.checked))
    return VerifyReport(ok=failures == 0, reason="", bag_results=results, failures=failures)


# --- restore ----------------------------------------------------------------


def _safe_extract(tar: tarfile.TarFile, target_dir: Path) -> None:
    """Extract *tar* into *target_dir*, refusing any member that escapes it.

    A backup archive is trusted (this project made it), but an attacker who can
    substitute a tampered ``.tar.fernet`` cannot forge a Fernet tag without the key,
    so a decrypt that succeeds already authenticates the bytes. This path-traversal
    guard is defence in depth: a member whose resolved path leaves *target_dir* is
    rejected rather than allowed to write outside the restore tree (integrity).
    """
    target = target_dir.resolve()
    for member in tar.getmembers():
        destination = (target / member.name).resolve()
        if destination != target and target not in destination.parents:
            raise BackupError(f"refusing to extract a member outside the target: {member.name}")
    # The stdlib ``data`` filter (Python 3.12; backported to 3.11.4) is the second
    # line of defence: it rejects absolute paths and traversal and strips setuid/dev
    # nodes, so even a member we failed to reason about cannot write outside the tree.
    tar.extractall(target_dir, filter="data")


def restore_backup(archive: Path, passphrase: str, target_dir: Path) -> VerifyReport:
    """Decrypt and untar *archive* into *target_dir*, then verify the restore.

    Reverses :func:`create_backup`: derives the key from *passphrase* and the salt
    read from the sidecar manifest (using the same scrypt parameters), decrypts the
    Fernet token — a wrong passphrase or any tampering fails here with a clear,
    no-outing-safe error rather than yielding garbage — untars ``store/`` and
    ``identity.vault`` under *target_dir*, and then runs :func:`verify_backup` so the
    restore is *proven* intact, not merely unpacked. Returns that
    :class:`VerifyReport`.

    Raises :class:`BackupError` on a missing archive/manifest, a wrong passphrase, or
    tampered ciphertext; the message never echoes the passphrase, the key, or any
    plaintext (no-outing rule).
    """
    archive = Path(archive)
    target_dir = Path(target_dir)
    if not archive.is_file():
        raise BackupError(f"backup archive not found: {archive}")
    if target_dir.exists() and any(target_dir.iterdir()):
        raise BackupError("restore target must be empty to avoid overwriting existing data")

    manifest_path = _manifest_for(archive)
    if not manifest_path.is_file():
        raise BackupError(f"backup manifest not found alongside archive: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        salt = bytes.fromhex(str(manifest["salt"]))
    except (OSError, ValueError, KeyError) as exc:
        raise BackupError(f"backup manifest is malformed: {manifest_path}") from exc

    key = IdentityVault.derive_key(passphrase, salt)
    ciphertext = archive.read_bytes()
    try:
        plaintext = Fernet(key).decrypt(ciphertext)
    except InvalidToken as exc:
        # Wrong passphrase OR tampered ciphertext — indistinguishable by design, and
        # neither the passphrase nor the bytes are echoed (no-outing rule).
        raise BackupError(
            "backup decryption failed (wrong passphrase or tampered archive)"
        ) from exc

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(plaintext), mode="r") as tar:
            _safe_extract(tar, target_dir)
    except (OSError, tarfile.TarError) as exc:
        raise BackupError(f"backup could not be unpacked into: {target_dir}") from exc

    return verify_backup(target_dir)


# --- retention --------------------------------------------------------------


def _manifest_for(archive_path: Path) -> Path:
    """The sidecar manifest path for a ``*.tar.fernet`` archive."""
    name = archive_path.name
    if name.endswith(_BACKUP_SUFFIX):
        stem = name[: -len(_BACKUP_SUFFIX)]
        return archive_path.with_name(stem + _MANIFEST_SUFFIX)
    return archive_path.with_name(name + _MANIFEST_SUFFIX)


def list_backups(dest_dir: Path) -> list[Path]:
    """Return the backup archives in *dest_dir*, oldest first (name-sorted).

    Names embed a UTC timestamp, so a lexical sort is chronological. Only the
    ``*.tar.fernet`` archives are returned, not their sidecar manifests.
    """
    dest_dir = Path(dest_dir)
    if not dest_dir.is_dir():
        return []
    return sorted(
        p
        for p in dest_dir.iterdir()
        if p.is_file() and p.name.startswith(_BACKUP_PREFIX) and p.name.endswith(_BACKUP_SUFFIX)
    )


def prune_backups(dest_dir: Path, keep: int) -> list[Path]:
    """Keep the *keep* newest backups in *dest_dir*, removing older ones + sidecars.

    Retention keeps an unattended nightly job from filling the disk (operability).
    Removes each pruned archive together with its manifest so no orphan sidecars are
    left behind. Returns the list of archive paths removed (for a no-outing-safe
    summary). ``keep <= 0`` is rejected: refusing to delete everything is a safety
    guard, not a feature.
    """
    if keep <= 0:
        raise BackupError("retention count must be a positive number of backups to keep")
    archives = list_backups(dest_dir)
    if len(archives) <= keep:
        return []
    to_remove = archives[: len(archives) - keep]
    removed: list[Path] = []
    for archive in to_remove:
        archive.unlink(missing_ok=True)
        _manifest_for(archive).unlink(missing_ok=True)
        removed.append(archive)
    return removed
