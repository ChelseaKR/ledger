"""Tests for the scheduled, encrypted, off-box backup path (roadmap RM10).

``verify-backup`` (K1) proves a *restored* copy is intact; :mod:`ledger.backup`
turns the manual "snapshot the volume, keep the key apart" runbook into one
encrypted, cron-runnable command. These tests hold that path to the project's two
non-negotiables:

* **it actually recovers** — a create -> restore round-trip re-validates every bag's
  fixity, and retention keeps exactly the N newest;
* **it fails closed and outs no one** — a wrong passphrase and a tampered ciphertext
  both raise a clear error rather than yielding plaintext, and neither the report nor
  a decrypt error ever carries the sealed contributor name.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from ledger.backup import (
    BackupError,
    _manifest_for,
    create_backup,
    list_backups,
    prune_backups,
    restore_backup,
    verify_backup,
)
from ledger.config import Config
from ledger.identity import ContributorIdentity
from ledger.ingest import Archive
from ledger.models import AccessPolicy, DublinCore, Field, Record

# A sentinel contributor name that must never appear in a report, a log line, or a
# decrypt error. A fixed Fernet key stands in for the env-held vault secret; the
# backup *passphrase* is a separate secret from that vault key by design.
_SENTINEL_NAME = "SENTINEL-BACKUP-DO-NOT-LEAK-9Q4F"
_VAULT_KEY = b"0123456789abcdef0123456789abcdef0123456789a="
_PASSPHRASE = "correct horse battery staple"  # noqa: S105 - test passphrase, not a real secret
_NOW = "2026-06-17T00:00:00Z"


def _archive_with_identity(tmp_path: Path) -> tuple[Archive, str]:
    """Stand up an archive with one record carrying a sealed sentinel identity."""
    root = tmp_path / "archive"
    payload = tmp_path / "public.txt"
    payload.write_text("a community keeps its own history\n", encoding="utf-8")

    config = Config.default("Backup Community Archive", root)
    archive = Archive.init(config)
    record = Record(
        title="Thursday gatherings",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(
            title=["Thursday gatherings"],
            publisher=[config.archive_name],
            language=["en"],
        ),
        fields=[Field(name="story", value="A public account.", policy=AccessPolicy.PUBLIC)],
    )
    archive.ingest(
        {payload.name: payload},
        record,
        identity=ContributorIdentity(name=_SENTINEL_NAME),
        vault_key=_VAULT_KEY,
        agent="backup-test",
        now=_NOW,
    )
    return archive, root.name


@pytest.mark.recovery
def test_round_trip_create_then_restore_validates_bags(tmp_path: Path) -> None:
    """create -> restore reconstructs the archive and every bag re-validates fixity."""
    archive, _ = _archive_with_identity(tmp_path)
    dest = tmp_path / "offbox"

    report = create_backup(archive.config, dest, _PASSPHRASE)
    assert report.archive_path.exists()
    assert report.manifest_path.exists()
    assert report.bag_count == 1

    target = tmp_path / "restored"
    verify = restore_backup(report.archive_path, _PASSPHRASE, target)
    assert verify.ok
    assert verify.failures == 0
    assert verify.bag_results, "a restored archive should contain at least one bag"
    assert all(ok for _name, ok, _checked in verify.bag_results)
    # The encrypted vault rode along in the tar and lands at the restored root.
    assert (target / "identity.vault").exists()
    assert (target / "store" / "config.json").exists()


def test_wrong_passphrase_fails_with_a_clear_error(tmp_path: Path) -> None:
    """A wrong passphrase fails closed with a no-outing-safe message, not plaintext."""
    archive, _ = _archive_with_identity(tmp_path)
    dest = tmp_path / "offbox"
    report = create_backup(archive.config, dest, _PASSPHRASE)

    with pytest.raises(BackupError) as excinfo:
        restore_backup(report.archive_path, "the wrong passphrase", tmp_path / "restored")
    message = str(excinfo.value)
    assert "wrong passphrase" in message
    assert _SENTINEL_NAME not in message


def test_tampered_ciphertext_is_detected(tmp_path: Path) -> None:
    """A flipped ciphertext byte is caught by Fernet's authenticator on restore."""
    archive, _ = _archive_with_identity(tmp_path)
    dest = tmp_path / "offbox"
    report = create_backup(archive.config, dest, _PASSPHRASE)

    raw = bytearray(report.archive_path.read_bytes())
    raw[len(raw) // 2] ^= 0x01
    report.archive_path.write_bytes(bytes(raw))

    with pytest.raises(BackupError) as excinfo:
        restore_backup(report.archive_path, _PASSPHRASE, tmp_path / "restored")
    assert _SENTINEL_NAME not in str(excinfo.value)


def test_backup_refuses_destination_inside_archive_root(tmp_path: Path) -> None:
    """An on-archive recursive copy cannot be presented as an off-box backup."""
    archive, _ = _archive_with_identity(tmp_path)
    with pytest.raises(BackupError, match="outside the archive root"):
        create_backup(archive.config, Path(archive.config.store_root) / "backups", _PASSPHRASE)


def test_restore_refuses_nonempty_target(tmp_path: Path) -> None:
    """Restore never overwrites an existing tree with attacker-controlled members."""
    archive, _ = _archive_with_identity(tmp_path)
    report = create_backup(archive.config, tmp_path / "offbox", _PASSPHRASE)
    target = tmp_path / "restored"
    target.mkdir()
    (target / "keep.txt").write_text("do not overwrite", encoding="utf-8")
    with pytest.raises(BackupError, match="must be empty"):
        restore_backup(report.archive_path, _PASSPHRASE, target)


def test_prune_keeps_the_n_newest(tmp_path: Path) -> None:
    """Retention keeps exactly the N newest backups and removes their sidecars too."""
    archive, _ = _archive_with_identity(tmp_path)
    dest = tmp_path / "offbox"

    created: list[Path] = []
    for _ in range(4):
        report = create_backup(archive.config, dest, _PASSPHRASE)
        created.append(report.archive_path)
        # The filename timestamp has one-second resolution; separate the backups so
        # each gets a distinct, chronologically-sortable name.
        time.sleep(1.05)

    assert len(list_backups(dest)) == 4
    removed = prune_backups(dest, keep=2)
    assert len(removed) == 2

    remaining = list_backups(dest)
    assert len(remaining) == 2
    # The two newest survive; the two oldest and their manifests are gone.
    assert set(remaining) == {created[2], created[3]}
    for gone in created[:2]:
        assert not gone.exists()
        assert not _manifest_for(gone).exists()  # sidecar cleaned up alongside


def test_prune_refuses_to_delete_everything(tmp_path: Path) -> None:
    """A non-positive keep is rejected — a safety guard, never a wipe."""
    archive, _ = _archive_with_identity(tmp_path)
    dest = tmp_path / "offbox"
    create_backup(archive.config, dest, _PASSPHRASE)
    with pytest.raises(BackupError):
        prune_backups(dest, keep=0)


def test_report_and_manifest_carry_no_vault_plaintext(tmp_path: Path) -> None:
    """No-outing: the report, manifest, and env-independent surfaces leak no identity.

    The whole point of encrypting off-box is that a stolen backup — its ciphertext,
    its sidecar manifest, and any operator-facing summary — reveals no contributor.
    """
    archive, _ = _archive_with_identity(tmp_path)
    dest = tmp_path / "offbox"
    report = create_backup(archive.config, dest, _PASSPHRASE)

    assert _SENTINEL_NAME not in repr(report)
    assert _SENTINEL_NAME not in str(report)
    manifest_text = report.manifest_path.read_text(encoding="utf-8")
    assert _SENTINEL_NAME not in manifest_text
    # The passphrase and derived key must never be persisted in the sidecar either.
    assert _PASSPHRASE not in manifest_text
    # The ciphertext must not contain the plaintext sentinel (it is encrypted).
    assert _SENTINEL_NAME.encode("utf-8") not in report.archive_path.read_bytes()


def test_verify_backup_on_a_directly_restored_tree(tmp_path: Path) -> None:
    """verify_backup re-points config at the restored copy and passes on a good tree."""
    archive, _ = _archive_with_identity(tmp_path)
    dest = tmp_path / "offbox"
    report = create_backup(archive.config, dest, _PASSPHRASE)
    target = tmp_path / "restored"
    restore_backup(report.archive_path, _PASSPHRASE, target)

    # Verifying the already-restored tree again is idempotent and stays green.
    again = verify_backup(target)
    assert again.ok
    assert again.reason == ""
