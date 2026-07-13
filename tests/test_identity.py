"""Identity-vault tests — the structural guarantee behind selective disclosure.

The vault maps an opaque, random ``identity_ref`` to an encrypted
:class:`~ledger.identity.ContributorIdentity`. These tests verify the properties the
no-outing rule depends on: the ref is unrelated to the identity it points at; resolving
requires an explicit ``identity_unseal`` grant naming that ref; the wrong key fails as a
vault error (never silently); revoking removes the mapping; and neither the identity nor
the vault ever leaks its contents through ``repr`` (confidentiality, integrity, autonomy).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ledger.access.grants import build_grant, steward
from ledger.errors import AccessDenied, IdentityVaultError
from ledger.identity import ContributorIdentity, IdentityVault
from ledger.models import Grant

pytestmark = pytest.mark.disclosure

_NAME = "Robin Vasquez"
_CONTACT = "robin@example.org"
_PRONOUNS = "they/them"
_NOTES = "prefers contact via the collective"
_NOW = "2026-01-01T00:00:00Z"


def _identity() -> ContributorIdentity:
    return ContributorIdentity(name=_NAME, contact=_CONTACT, pronouns=_PRONOUNS, notes=_NOTES)


def _new_vault(tmp_path: Path) -> IdentityVault:
    key = IdentityVault.generate_key()
    return IdentityVault.create(tmp_path / "identity.vault", key)


def _unseal_grant(ref: str) -> Grant:
    """A grant that may resolve exactly one ref (least privilege)."""
    return build_grant("custodian", identity_unseal=(ref,))


# --- add returns an opaque ref ----------------------------------------------


def test_add_returns_opaque_ref_unrelated_to_contents(tmp_path: Path) -> None:
    """The ref carries no fragment of the identity it points at (unlinkability)."""
    vault = _new_vault(tmp_path)
    ref = vault.add(_identity())
    assert isinstance(ref, str) and ref
    for secret in (_NAME, _CONTACT, _PRONOUNS, _NOTES):
        assert secret not in ref
        # Also guard against a naive substring of a name leaking into the token.
        assert secret.split()[0] not in ref


def test_distinct_adds_yield_distinct_refs(tmp_path: Path) -> None:
    """Two adds of identical identities still get independent random refs."""
    vault = _new_vault(tmp_path)
    ref_a = vault.add(_identity())
    ref_b = vault.add(_identity())
    assert ref_a != ref_b


# --- resolve is grant-gated -------------------------------------------------


def test_resolve_requires_ref_in_grant(tmp_path: Path) -> None:
    """resolve returns the identity only when the grant names the ref in identity_unseal."""
    vault = _new_vault(tmp_path)
    ref = vault.add(_identity())
    resolved = vault.resolve(ref, _unseal_grant(ref), _NOW)
    assert resolved.name == _NAME
    assert resolved.contact == _CONTACT
    assert resolved.pronouns == _PRONOUNS
    assert resolved.notes == _NOTES


def test_resolve_denied_without_unseal_grant(tmp_path: Path) -> None:
    """A grant lacking the ref in identity_unseal is denied (AccessDenied)."""
    vault = _new_vault(tmp_path)
    ref = vault.add(_identity())
    with pytest.raises(AccessDenied):
        vault.resolve(ref, build_grant("nobody"), _NOW)


def test_resolve_denied_even_for_steward_without_unseal(tmp_path: Path) -> None:
    """Stewardship alone never unseals identity: a steward grant still has no unseal token."""
    vault = _new_vault(tmp_path)
    ref = vault.add(_identity())
    with pytest.raises(AccessDenied):
        vault.resolve(ref, steward("steward"), _NOW)


def test_resolve_denied_for_expired_grant_even_with_unseal_token(tmp_path: Path) -> None:
    """An EXPIRED grant must not unseal an identity, even if it names the ref.

    Regression for the critical fail-open bug where resolve() ignored grant expiry:
    resolving an identity is the most sensitive disclosure, so a stale/time-revoked
    credential must be downgraded to the public grant and denied (fail-closed)."""
    vault = _new_vault(tmp_path)
    ref = vault.add(_identity())
    expired = build_grant("custodian", identity_unseal=(ref,), expires_at="2020-01-01T00:00:00Z")
    with pytest.raises(AccessDenied):
        vault.resolve(ref, expired, _NOW)  # _NOW (2026) is well past expiry
    # The very same grant, evaluated before its expiry, still works (not deletion).
    assert vault.resolve(ref, expired, "2019-06-01T00:00:00Z").name == _NAME


def test_resolve_denied_message_omits_contents(tmp_path: Path) -> None:
    """The AccessDenied message names only the ref/capability, never the identity."""
    vault = _new_vault(tmp_path)
    ref = vault.add(_identity())
    with pytest.raises(AccessDenied) as excinfo:
        vault.resolve(ref, build_grant("nobody"), _NOW)
    message = str(excinfo.value)
    for secret in (_NAME, _CONTACT, _PRONOUNS, _NOTES):
        assert secret not in message


def test_resolve_grant_check_precedes_lookup(tmp_path: Path) -> None:
    """An unknown ref with no unseal grant is denied (AccessDenied), not reported as unknown.

    The grant decision runs before the store lookup, so the response does not reveal
    whether the ref exists (least privilege, confidentiality).
    """
    vault = _new_vault(tmp_path)
    with pytest.raises(AccessDenied):
        vault.resolve("a-ref-that-does-not-exist", build_grant("nobody"), _NOW)


def test_resolve_unknown_ref_with_grant_raises_vault_error(tmp_path: Path) -> None:
    """A grant that permits a ref the vault never stored raises IdentityVaultError."""
    vault = _new_vault(tmp_path)
    bogus = "phantom-ref"
    with pytest.raises(IdentityVaultError):
        vault.resolve(bogus, _unseal_grant(bogus), _NOW)


# --- wrong key fails as a vault error ---------------------------------------


def test_open_with_wrong_key_raises_vault_error(tmp_path: Path) -> None:
    """Opening a populated vault with a different key raises IdentityVaultError."""
    path = tmp_path / "identity.vault"
    good_key = IdentityVault.generate_key()
    vault = IdentityVault.create(path, good_key)
    vault.add(_identity())

    wrong_key = IdentityVault.generate_key()
    with pytest.raises(IdentityVaultError):
        IdentityVault.open(path, wrong_key)


def test_open_with_wrong_key_message_omits_contents(tmp_path: Path) -> None:
    """The wrong-key error never echoes vault contents (no-outing rule)."""
    path = tmp_path / "identity.vault"
    vault = IdentityVault.create(path, IdentityVault.generate_key())
    vault.add(_identity())
    with pytest.raises(IdentityVaultError) as excinfo:
        IdentityVault.open(path, IdentityVault.generate_key())
    message = str(excinfo.value)
    for secret in (_NAME, _CONTACT, _PRONOUNS, _NOTES):
        assert secret not in message


def test_malformed_fernet_key_raises_vault_error(tmp_path: Path) -> None:
    """A structurally invalid key raises IdentityVaultError, not a raw crypto error."""
    with pytest.raises(IdentityVaultError):
        IdentityVault(tmp_path / "identity.vault", b"not-a-valid-fernet-key")


# --- revoke removes ---------------------------------------------------------


def test_revoke_removes_mapping(tmp_path: Path) -> None:
    """After revoke, the ref no longer resolves (takedown honoured at the vault)."""
    vault = _new_vault(tmp_path)
    ref = vault.add(_identity())
    assert vault.contains(ref) is True
    vault.revoke(ref)
    assert vault.contains(ref) is False
    with pytest.raises(IdentityVaultError):
        vault.resolve(ref, _unseal_grant(ref), _NOW)


def test_revoke_is_idempotent(tmp_path: Path) -> None:
    """Revoking an absent ref is a safe no-op so a takedown can be retried."""
    vault = _new_vault(tmp_path)
    vault.revoke("never-existed")  # must not raise


def test_revoke_persists_across_reopen(tmp_path: Path) -> None:
    """A revoke is durable: reopening the vault does not resurrect the mapping."""
    path = tmp_path / "identity.vault"
    key = IdentityVault.generate_key()
    vault = IdentityVault.create(path, key)
    ref = vault.add(_identity())
    vault.revoke(ref)

    reopened = IdentityVault.open(path, key)
    assert reopened.contains(ref) is False


# --- repr/str never leak contents -------------------------------------------


def test_identity_repr_is_redacted() -> None:
    """ContributorIdentity.__repr__ never reveals its fields (no-outing rule)."""
    identity = _identity()
    rendered = repr(identity)
    assert rendered == "ContributorIdentity(<redacted>)"
    for secret in (_NAME, _CONTACT, _PRONOUNS, _NOTES):
        assert secret not in rendered


def test_identity_str_is_redacted() -> None:
    """str(identity) is equally redacted, so an f-string cannot out anyone."""
    identity = _identity()
    rendered = f"{identity}"
    for secret in (_NAME, _CONTACT, _PRONOUNS, _NOTES):
        assert secret not in rendered


def test_vault_repr_does_not_leak_contents(tmp_path: Path) -> None:
    """IdentityVault.__repr__ shows only the path and an entry count, no contents."""
    vault = _new_vault(tmp_path)
    vault.add(_identity())
    rendered = repr(vault)
    for secret in (_NAME, _CONTACT, _PRONOUNS, _NOTES):
        assert secret not in rendered
    assert "entries=1" in rendered


def test_vault_file_holds_ciphertext_only(tmp_path: Path) -> None:
    """The on-disk vault is ciphertext: no plaintext identity field appears in it."""
    path = tmp_path / "identity.vault"
    vault = IdentityVault.create(path, IdentityVault.generate_key())
    vault.add(_identity())
    raw = path.read_text(encoding="utf-8")
    for secret in (_NAME, _CONTACT, _PRONOUNS, _NOTES):
        assert secret not in raw


def test_roundtrip_through_reopen_resolves(tmp_path: Path) -> None:
    """A sealed identity survives a write/reopen and resolves under the proper grant."""
    path = tmp_path / "identity.vault"
    key = IdentityVault.generate_key()
    vault = IdentityVault.create(path, key)
    ref = vault.add(_identity())

    reopened = IdentityVault.open(path, key)
    resolved = reopened.resolve(ref, _unseal_grant(ref), _NOW)
    assert resolved.name == _NAME


def test_separate_vault_handles_do_not_overwrite_each_others_adds(tmp_path: Path) -> None:
    """A stale whole-file snapshot cannot erase an add made by another handle."""
    path = tmp_path / "identity.vault"
    key = IdentityVault.generate_key()
    first = IdentityVault.create(path, key)
    second = IdentityVault.open(path, key)

    ref_a = first.add(ContributorIdentity(name="A"))
    ref_b = second.add(ContributorIdentity(name="B"))

    reopened = IdentityVault.open(path, key)
    assert len(reopened) == 2
    assert reopened.resolve(ref_a, _unseal_grant(ref_a), _NOW).name == "A"
    assert reopened.resolve(ref_b, _unseal_grant(ref_b), _NOW).name == "B"


def test_stale_handle_revoke_preserves_another_handles_add(tmp_path: Path) -> None:
    """A revoke reloads under lock, so it cannot resurrect/drop unrelated refs."""
    path = tmp_path / "identity.vault"
    key = IdentityVault.generate_key()
    first = IdentityVault.create(path, key)
    revoked_ref = first.add(ContributorIdentity(name="withdrawn"))
    stale = IdentityVault.open(path, key)

    retained_ref = first.add(ContributorIdentity(name="retained"))
    stale.revoke(revoked_ref)

    reopened = IdentityVault.open(path, key)
    assert not reopened.contains(revoked_ref)
    assert reopened.resolve(retained_ref, _unseal_grant(retained_ref), _NOW).name == "retained"


def test_stale_handle_cannot_resolve_after_another_handle_revokes(tmp_path: Path) -> None:
    """Durable consent revocation invalidates already-open vault handles too."""
    path = tmp_path / "identity.vault"
    key = IdentityVault.generate_key()
    first = IdentityVault.create(path, key)
    ref = first.add(_identity())
    stale = IdentityVault.open(path, key)

    first.revoke(ref)

    with pytest.raises(IdentityVaultError):
        stale.resolve(ref, _unseal_grant(ref), _NOW)


def test_stale_handle_rekey_includes_entries_added_by_another_handle(tmp_path: Path) -> None:
    """Rekey authenticates/reloads disk state before rotating the whole map."""
    path = tmp_path / "identity.vault"
    old_key = IdentityVault.generate_key()
    new_key = IdentityVault.generate_key()
    first = IdentityVault.create(path, old_key)
    stale = IdentityVault.open(path, old_key)
    ref = first.add(_identity())

    assert stale.rekey(new_key) == 1

    reopened = IdentityVault.open(path, new_key)
    assert reopened.resolve(ref, _unseal_grant(ref), _NOW).name == _NAME
