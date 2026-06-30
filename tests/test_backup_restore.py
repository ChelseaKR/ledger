"""Disaster-recovery test: back up an archive, wipe it, restore, prove it intact.

ledger exists so a community does not lose its records to a single failure or a
seizure. The infra runbook tells a steward their two backup obligations are the
**data volume** (store + the encrypted vault file) and the **vault key, kept
apart** — ciphertext is useless without the key. That cold-restore path is the one
most likely to be botched and, until now, the one with no automated test.

This exercises the whole cycle on a real on-disk archive:

1. ingest a record with a public field, a temporally sealed field, and a sealed
   contributor identity;
2. snapshot the archive root (store + vault) to a separate "backup" location, with
   the vault key held apart (an in-test constant standing in for an env secret);
3. wipe the live archive completely;
4. restore the snapshot in place and re-open the archive;
5. assert the three things a restore must guarantee — fixity re-validates, the
   sealed identity still resolves *with the separately-held key*, and the no-outing
   sentinel survived nowhere on a public surface.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from ledger.access.grants import anonymous, build_grant
from ledger.config import Config
from ledger.identity import ContributorIdentity
from ledger.ingest import Archive
from ledger.models import AccessPolicy, DublinCore, Field, Record

# A sentinel contributor name that must never appear on a public surface, before or
# after a restore. A fixed Fernet key stands in for the env-held vault secret that a
# steward keeps *apart* from the data backup.
_SENTINEL_NAME = "SENTINEL-RESTORE-DO-NOT-LEAK-4F8Q"
_VAULT_KEY = b"0123456789abcdef0123456789abcdef0123456789a="
_NOW = "2026-06-17T00:00:00Z"


def _ingest_one(archive: Archive, payload_file: Path) -> Record:
    """Ingest a single record with a public field, a sealed field, and an identity."""
    record = Record(
        title="Thursday gatherings",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(
            title=["Thursday gatherings"],
            publisher=[archive.config.archive_name],
            language=["en"],
        ),
        fields=[
            Field(name="story", value="A public account.", policy=AccessPolicy.PUBLIC),
            Field(name="location", value="withheld", policy=AccessPolicy.SEALED_UNTIL),
        ],
    )
    archive.ingest(
        {payload_file.name: payload_file},
        record,
        identity=ContributorIdentity(name=_SENTINEL_NAME),
        vault_key=_VAULT_KEY,
        agent="restore-test",
        now=_NOW,
    )
    return record


@pytest.mark.recovery
def test_backup_then_wipe_then_restore_preserves_everything(tmp_path: Path) -> None:
    """A cold restore re-validates fixity, resolves identity, and outs no one."""
    root = tmp_path / "archive"
    backup = tmp_path / "backup"
    payload = tmp_path / "public.txt"
    payload.write_text("a community keeps its own history\n", encoding="utf-8")

    # --- stand up and populate the archive ---------------------------------
    config = Config.default("Restore Community Archive", root)
    archive = Archive.init(config)
    record = _ingest_one(archive, payload)
    rid = record.record_id
    assert record.identity_ref is not None
    ref = record.identity_ref

    # Sanity: healthy before the disaster.
    assert all(report.ok for _name, report in archive.audit_fixity())

    # --- 1. back up the whole archive root (store + encrypted vault) --------
    # The vault key is NOT in the backup; it is held apart (the _VAULT_KEY constant),
    # exactly as the runbook requires — a stolen backup is ciphertext only.
    shutil.copytree(root, backup)
    assert (backup / "identity.vault").exists()

    # --- 2. disaster: the live archive is destroyed ------------------------
    shutil.rmtree(root)
    assert not root.exists()

    # --- 3. restore the snapshot in place ----------------------------------
    shutil.copytree(backup, root)
    restored = Archive(Config.load(root / "store" / "config.json"))

    # --- 4a. fixity re-validates: every bag passes against its manifest -----
    reports = restored.audit_fixity()
    assert reports, "restored archive should contain at least one bag"
    assert all(report.ok for _name, report in reports)

    # --- 4b. identity still resolves, but only with the separately-held key --
    unseal = build_grant("recovery-steward", identity_unseal=[ref])
    # The key is provided out-of-band (as it would be from the env on a real host).
    restored._open_vault(_VAULT_KEY)
    resolved = restored.resolve_identity(rid, unseal, now=_NOW)
    assert resolved.name == _SENTINEL_NAME

    # --- 4c. the no-outing rule survived the round-trip --------------------
    public = restored.disclose(rid, anonymous(), now=_NOW)
    assert _SENTINEL_NAME not in str(public.to_dict())
    listed = restored.browse(anonymous(), now=_NOW)
    assert any(r.record_id == rid for r in listed)
    assert _SENTINEL_NAME not in str([r.to_dict() for r in listed])


@pytest.mark.recovery
def test_restored_vault_is_useless_without_the_key(tmp_path: Path) -> None:
    """A restored data volume *without* the key cannot out anyone — confidentiality.

    The whole reason the key is kept apart: a backup (or a seized disk) that carries
    the encrypted vault but not the key reveals no contributor. Opening the restored
    vault under the wrong key fails closed rather than yielding plaintext.
    """
    root = tmp_path / "archive"
    backup = tmp_path / "backup"
    payload = tmp_path / "public.txt"
    payload.write_text("a community keeps its own history\n", encoding="utf-8")

    config = Config.default("Restore Community Archive", root)
    archive = Archive.init(config)
    _ingest_one(archive, payload)

    shutil.copytree(root, backup)
    shutil.rmtree(root)
    shutil.copytree(backup, root)

    from ledger.errors import IdentityVaultError
    from ledger.identity import IdentityVault

    wrong_key = IdentityVault.generate_key()
    with pytest.raises(IdentityVaultError):
        IdentityVault.open(root / "identity.vault", wrong_key)
