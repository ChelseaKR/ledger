"""Identity-vault key rotation: re-encrypt under a new key, recorded and fail-closed.

Rotating the vault key is a *when*, not an *if* — steward turnover, a suspected
exposure, a compliance cadence. These tests pin the guarantees the operation must
hold: every identity re-encrypts atomically under the new key, the old key stops
working, the rotation is recorded as a PREMIS event that names no one, and the
operation refuses (rather than silently orphaning) when absolute-sealed content is
encrypted under the same key.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ledger.access.grants import build_grant
from ledger.config import Config
from ledger.errors import IdentityVaultError, LedgerError
from ledger.identity import ContributorIdentity, IdentityVault
from ledger.ingest import Archive
from ledger.metadata.premis import PremisLog
from ledger.models import AccessPolicy, DublinCore, Field, PremisEventType, Record

_SENTINEL = "SENTINEL-REKEY-DO-NOT-LEAK-2K9X"
_OLD_KEY = b"0123456789abcdef0123456789abcdef0123456789a="
_NOW = "2026-06-17T00:00:00Z"


@pytest.mark.disclosure
def test_vault_rekey_reencrypts_and_invalidates_old_key(tmp_path: Path) -> None:
    """After rekey, the new key resolves every identity and the old key fails closed."""
    path = tmp_path / "identity.vault"
    old = IdentityVault.generate_key()
    new = IdentityVault.generate_key()
    vault = IdentityVault.create(path, old)
    ref = vault.add(ContributorIdentity(name=_SENTINEL))

    assert vault.rekey(new) == 1

    reopened = IdentityVault.open(path, new)
    grant = build_grant("unsealer", identity_unseal=[ref])
    assert reopened.resolve(ref, grant, _NOW).name == _SENTINEL

    with pytest.raises(IdentityVaultError):
        IdentityVault.open(path, old)


@pytest.mark.disclosure
def test_vault_rekey_is_atomic_on_a_bad_new_key(tmp_path: Path) -> None:
    """An invalid new key aborts the rotation and leaves the vault on the old key."""
    path = tmp_path / "identity.vault"
    old = IdentityVault.generate_key()
    vault = IdentityVault.create(path, old)
    ref = vault.add(ContributorIdentity(name=_SENTINEL))

    with pytest.raises(IdentityVaultError):
        vault.rekey(b"not-a-valid-fernet-key")

    # The vault is untouched: the original key still opens and resolves it.
    reopened = IdentityVault.open(path, old)
    grant = build_grant("unsealer", identity_unseal=[ref])
    assert reopened.resolve(ref, grant, _NOW).name == _SENTINEL


@pytest.mark.disclosure
def test_vault_rekey_replace_failure_restores_live_handle_and_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed atomic replace keeps both disk and the current handle on the old key."""
    path = tmp_path / "identity.vault"
    old = IdentityVault.generate_key()
    new = IdentityVault.generate_key()
    vault = IdentityVault.create(path, old)
    ref = vault.add(ContributorIdentity(name=_SENTINEL))
    old_disk = path.read_bytes()
    old_fernet = vault._fernet
    old_store = dict(vault._store)
    old_key_check = vault._key_check
    old_token = vault.encrypt_text("still decryptable after rollback")

    def fail_replace(*_args: object, **_kwargs: object) -> None:
        raise OSError("injected replace failure")

    with monkeypatch.context() as patch:
        patch.setattr("ledger.identity.os.replace", fail_replace)
        with pytest.raises(IdentityVaultError, match="vault could not be written"):
            vault.rekey(new)

    assert path.read_bytes() == old_disk
    assert vault._fernet is old_fernet
    assert vault._store == old_store
    assert vault._key_check == old_key_check
    assert vault.decrypt_text(old_token) == "still decryptable after rollback"
    grant = build_grant("unsealer", identity_unseal=[ref])
    reopened = IdentityVault.open(path, old)
    resolved = reopened.resolve(ref, grant, _NOW)
    assert resolved.name == _SENTINEL
    with pytest.raises(IdentityVaultError):
        IdentityVault.open(path, new)


def _ingest_identity_only(archive: Archive, payload: Path) -> Record:
    """Ingest one record whose only sensitive content is a sealed identity."""
    record = Record(
        title="Thursday gatherings",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(
            title=["Thursday gatherings"], publisher=[archive.config.archive_name]
        ),
        fields=[Field(name="story", value="A public account.", policy=AccessPolicy.PUBLIC)],
    )
    archive.ingest(
        {payload.name: payload},
        record,
        identity=ContributorIdentity(name=_SENTINEL),
        vault_key=_OLD_KEY,
        agent="rekey-test",
        now=_NOW,
    )
    return record


@pytest.mark.disclosure
def test_archive_rekey_rotates_records_event_and_outs_no_one(tmp_path: Path) -> None:
    """``Archive.rekey_vault`` rotates the key, logs a REKEY event, and leaks nothing."""
    root = tmp_path / "arc"
    payload = tmp_path / "public.txt"
    payload.write_text("a community keeps its own history\n", encoding="utf-8")
    archive = Archive.init(Config.default("Rekey Archive", root))
    record = _ingest_identity_only(archive, payload)
    assert record.identity_ref is not None

    new_key = IdentityVault.generate_key()
    assert archive.rekey_vault(new_key, old_key=_OLD_KEY, agent="steward-x", now=_NOW) == 1

    # A REKEY PREMIS event was recorded, and it names no contributor.
    log = PremisLog.read(root / "store" / "logs" / "key-rotations.premis.json")
    assert PremisEventType.REKEY in [e.event_type for e in log.events]
    assert _SENTINEL not in log.to_json()

    # A fresh archive opened with the NEW key resolves the identity; the OLD key fails.
    fresh = Archive(Config.load(root / "store" / "config.json"))
    fresh._open_vault(new_key)
    unseal = build_grant("investigator", identity_unseal=[record.identity_ref])
    assert fresh.resolve_identity(record.record_id, unseal, now=_NOW).name == _SENTINEL

    stale = Archive(Config.load(root / "store" / "config.json"))
    with pytest.raises(LedgerError):
        stale._open_vault(_OLD_KEY)


@pytest.mark.disclosure
def test_archive_rekey_refuses_when_absolute_sealed_content_present(tmp_path: Path) -> None:
    """Rekey fails closed when absolute-sealed at-rest content shares the key."""
    root = tmp_path / "arc"
    payload = tmp_path / "public.txt"
    payload.write_text("a community keeps its own history\n", encoding="utf-8")
    archive = Archive.init(Config.default("Sealed Archive", root))

    record = Record(
        title="Sealed record",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(title=["Sealed record"], publisher=[archive.config.archive_name]),
        fields=[Field(name="secret", value="for no one", policy=AccessPolicy.SEALED)],
    )
    archive.ingest(
        {payload.name: payload},
        record,
        identity=ContributorIdentity(name=_SENTINEL),
        vault_key=_OLD_KEY,
        agent="rekey-test",
        now=_NOW,
    )

    new_key = IdentityVault.generate_key()
    with pytest.raises(LedgerError, match="absolute-sealed"):
        archive.rekey_vault(new_key, old_key=_OLD_KEY, now=_NOW)


@pytest.mark.disclosure
def test_archive_rekey_refuses_when_no_vault_exists(tmp_path: Path) -> None:
    """Rekeying an archive that never sealed an identity is a clean, loud no-op."""
    root = tmp_path / "arc"
    archive = Archive.init(Config.default("Empty Archive", root))
    with pytest.raises(LedgerError, match="no identity vault"):
        archive.rekey_vault(IdentityVault.generate_key(), old_key=_OLD_KEY, now=_NOW)


@pytest.mark.disclosure
def test_cli_vault_rekey_reads_keys_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``ledger vault rekey`` rotates using env-supplied keys and prints only a count."""
    from ledger import cli

    root = tmp_path / "arc"
    payload = tmp_path / "public.txt"
    payload.write_text("a community keeps its own history\n", encoding="utf-8")
    archive = Archive.init(Config.default("CLI Rekey Archive", root))
    record = _ingest_identity_only(archive, payload)

    new_key = IdentityVault.generate_key()
    monkeypatch.setenv("LEDGER_VAULT_KEY", _OLD_KEY.decode("ascii"))
    monkeypatch.setenv("LEDGER_NEW_VAULT_KEY", new_key.decode("ascii"))

    code = cli.main(
        ["vault", "rekey", "--root", str(root), "--actor", "steward-cli", "--now", _NOW]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "rekeyed 1 identity" in out
    assert _SENTINEL not in out  # never echoes an identity
    assert new_key.decode("ascii") not in out  # never echoes a key

    # The new key now resolves the sealed identity.
    fresh = Archive(Config.load(root / "store" / "config.json"))
    fresh._open_vault(new_key)
    unseal = build_grant("investigator", identity_unseal=[record.identity_ref])
    assert fresh.resolve_identity(record.record_id, unseal, now=_NOW).name == _SENTINEL
