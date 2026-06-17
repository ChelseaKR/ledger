"""The encrypted contributor-identity vault.

This module is the *structural* guarantee behind ledger's central promise: holding
a record cannot out the person who contributed it. A :class:`Record` carries only
an opaque ``identity_ref`` — a random token with no relationship to the contents it
points at. The mapping from that token to a real :class:`ContributorIdentity` lives
nowhere but here, encrypted with authenticated symmetric encryption (Fernet), and
is only ever returned through :meth:`IdentityVault.resolve` under an explicit
:class:`~ledger.models.Grant` that names the ref.

Design choices and the quality attributes they serve:

* A separate encrypted vault plus grant-gated resolve keeps identity out of every
  record and read path -> safety, confidentiality, autonomy.
* Refs are random (``secrets.token_urlsafe``) and independent of the identity, so a
  record leaks no identifying signal even if its ref is observed -> unlinkability.
* Authenticated encryption (Fernet) detects tampering on read -> integrity.
* :meth:`IdentityVault.revoke` deletes a mapping, so consent is revocable and a
  takedown is honoured at the storage layer -> autonomy, consent.
* ``__repr__``/``__str__`` of both the identity and the vault are redacted, and no
  identity, sealed value, or ciphertext is ever logged or placed in an exception
  message -> the no-outing rule.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from ledger.errors import AccessDenied, IdentityVaultError
from ledger.models import PUBLIC_GRANT, Grant, canonical_json

# scrypt cost parameters (RFC 7914). These are interactive-login-grade and produce
# a 32-byte key that is base64-encoded into a Fernet key. Chosen for resistance to
# brute force on a human passphrase -> confidentiality.
_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 1
_KEY_LEN = 32

# Bytes of entropy in a generated ref. 32 bytes is far beyond collision risk and
# carries no structure that could be linked back to an identity -> unlinkability.
_REF_NBYTES = 32


@dataclass
class ContributorIdentity:
    """The sensitive contributor data — name and contact for a real person.

    This object exists in memory only transiently and on disk only as Fernet
    ciphertext inside an :class:`IdentityVault`. It must never be logged,
    serialized outside the vault, or placed in a record, filename, metric, or
    exception message. Its :meth:`__repr__` is deliberately redacted so an
    accidental ``print``/``log`` of the object cannot out anyone -> safety.
    """

    name: str
    contact: str = ""
    pronouns: str = ""
    notes: str = ""

    def __repr__(self) -> str:
        """Redacted representation; never reveals contents -> no-outing rule."""
        return "ContributorIdentity(<redacted>)"

    __str__ = __repr__

    def _to_canonical_json(self) -> str:
        """Serialize to canonical JSON for encryption (deterministic ciphertext input)."""
        return canonical_json(
            {
                "name": self.name,
                "contact": self.contact,
                "pronouns": self.pronouns,
                "notes": self.notes,
            }
        )

    @classmethod
    def _from_json_bytes(cls, raw: bytes) -> ContributorIdentity:
        """Reconstruct from decrypted JSON bytes."""
        data = json.loads(raw.decode("utf-8"))
        return cls(
            name=str(data.get("name", "")),
            contact=str(data.get("contact", "")),
            pronouns=str(data.get("pronouns", "")),
            notes=str(data.get("notes", "")),
        )


class IdentityVault:
    """A grant-gated, encrypted store mapping opaque refs to contributor identities.

    On disk the vault is a single JSON object mapping ``ref`` -> base64 Fernet
    ciphertext of the canonical-JSON identity. The refs are random and reveal
    nothing; the ciphertext is authenticated, so tampering is detected on read.
    Writes are atomic (write-temp-then-rename) -> integrity, fault tolerance.
    """

    def __init__(self, path: Path, key: bytes) -> None:
        """Bind a vault to an on-disk *path* and a Fernet *key*.

        Does not read or create the file; use :meth:`create` or :meth:`open`. The
        key is held only to encrypt/decrypt and is never serialized or logged.
        """
        self._path = Path(path)
        try:
            self._fernet = Fernet(key)
        except (ValueError, TypeError) as exc:
            # Do not echo the key or its bytes -> no-outing rule.
            raise IdentityVaultError("invalid Fernet key") from exc
        self._store: dict[str, str] = {}

    def __repr__(self) -> str:
        """Redacted representation: path and count only, never contents."""
        return f"IdentityVault(path={self._path!s}, entries={len(self._store)})"

    __str__ = __repr__

    # --- construction -------------------------------------------------------

    @classmethod
    def create(cls, path: Path, key: bytes) -> IdentityVault:
        """Create an empty vault file, refusing to overwrite an existing one.

        Refusing to overwrite protects an already-populated vault from accidental
        destruction -> fault tolerance, consent.
        """
        target = Path(path)
        vault = cls(target, key)
        if target.exists():
            raise IdentityVaultError(f"vault already exists: {target}")
        vault._store = {}
        vault._persist()
        return vault

    @classmethod
    def open(cls, path: Path, key: bytes) -> IdentityVault:
        """Open an existing vault, verifying the key against stored ciphertext.

        Raises :class:`IdentityVaultError` if the file is missing, malformed, or
        the key cannot authenticate the stored entries (wrong key or tampering).
        """
        target = Path(path)
        if not target.exists():
            raise IdentityVaultError(f"vault not found: {target}")
        vault = cls(target, key)
        try:
            raw = target.read_text(encoding="utf-8")
            loaded = json.loads(raw)
        except (OSError, ValueError) as exc:
            raise IdentityVaultError(f"vault could not be read: {target}") from exc
        if not isinstance(loaded, dict):
            raise IdentityVaultError(f"vault is malformed: {target}")
        store: dict[str, str] = {}
        for ref, ciphertext in loaded.items():
            if not isinstance(ref, str) or not isinstance(ciphertext, str):
                raise IdentityVaultError(f"vault is malformed: {target}")
            store[ref] = ciphertext
        # Verify the key by decrypting one entry; a wrong key surfaces immediately
        # rather than at first resolve -> failure transparency.
        for ciphertext in store.values():
            vault._decrypt(ciphertext)
            break
        vault._store = store
        return vault

    # --- mutation -----------------------------------------------------------

    def add(self, identity: ContributorIdentity) -> str:
        """Encrypt *identity* under a fresh random ref and persist atomically.

        Returns the new opaque ``identity_ref``. The ref is generated from a CSPRNG
        and is independent of the identity contents, so it carries no identifying
        signal and may safely live in a record -> unlinkability.
        """
        ref = self._new_ref()
        token = self._encrypt(identity)
        self._store[ref] = token
        self._persist()
        return ref

    def resolve(self, ref: str, grant: Grant, now: str) -> ContributorIdentity:
        """Decrypt and return the identity for *ref*, gated by *grant* at *now*.

        Resolving an identity is the single most sensitive disclosure in the
        system, so it enforces the *same* time-bounding as every other read path:
        an expired grant is downgraded to the anonymous public grant before the
        check, and the public grant unseals nothing. A stale or time-revoked
        credential therefore can never out a contributor -> safety, least
        privilege, fail-closed.

        Raises :class:`~ledger.errors.AccessDenied` if the (effective) grant does
        not name *ref* in ``identity_unseal`` (the grant check runs before any
        lookup, so the decision does not depend on whether the ref exists).
        Raises :class:`IdentityVaultError` if the ref is unknown or decryption
        fails.
        """
        effective = PUBLIC_GRANT if grant.is_expired(now) else grant
        if ref not in effective.identity_unseal:
            # Name the ref and the missing capability, never the protected value.
            raise AccessDenied(f"grant does not permit unsealing identity ref {ref}")
        token = self._store.get(ref)
        if token is None:
            raise IdentityVaultError(f"unknown identity ref: {ref}")
        return self._decrypt(token)

    def revoke(self, ref: str) -> None:
        """Remove the mapping for *ref*, persisting atomically.

        Idempotent: revoking an absent ref is a no-op so a takedown can be retried
        safely. Honours consent revocation / takedown -> autonomy, consent.
        """
        if ref in self._store:
            del self._store[ref]
            self._persist()

    def contains(self, ref: str) -> bool:
        """Return whether *ref* currently maps to a stored identity."""
        return ref in self._store

    # --- at-rest encryption of absolute-sealed content ----------------------

    def encrypt_text(self, plaintext: str) -> str:
        """Encrypt arbitrary text under the vault key, returning a tagged token.

        Used to store an absolute-``SEALED`` field value as ciphertext at rest, so a
        stolen disk or a hostile replica host reveals nothing — not even to a steward
        — for content a contributor sealed from everyone (user research P2-4). The
        token is prefixed so a reader can tell sealed-at-rest content from plain
        text. Encryption is authenticated (Fernet), so tampering is detected.
        """
        return "enc:" + self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt_text(self, token: str) -> str:
        """Inverse of :meth:`encrypt_text`. Raises :class:`IdentityVaultError` on a
        wrong key or tampering, never echoing the token or plaintext."""
        if not token.startswith("enc:"):
            raise IdentityVaultError("not a sealed-at-rest token")
        try:
            return self._fernet.decrypt(token[4:].encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise IdentityVaultError(
                "sealed-content decryption failed (wrong key or tampering)"
            ) from exc

    def encrypt_bytes(self, data: bytes) -> bytes:
        """Encrypt arbitrary bytes under the vault key (authenticated Fernet token).

        Used to encrypt an absolute-``SEALED`` payload FILE at rest, so the bytes a
        contributor sealed from everyone are ciphertext in the content store and the
        bag — never clear-text on a stolen disk or a hostile replica (user research
        P2-4, payload tier). Such a payload is never served on any read path, so it
        is only encrypted, never decrypted, on the request path."""
        return self._fernet.encrypt(data)

    def decrypt_bytes(self, token: bytes) -> bytes:
        """Inverse of :meth:`encrypt_bytes` (for an authorized off-path recovery)."""
        try:
            return self._fernet.decrypt(token)
        except InvalidToken as exc:
            raise IdentityVaultError(
                "sealed-payload decryption failed (wrong key or tampering)"
            ) from exc

    # --- key helpers --------------------------------------------------------

    @staticmethod
    def generate_key() -> bytes:
        """Return a fresh 32-byte urlsafe-base64 Fernet key from a CSPRNG."""
        return Fernet.generate_key()

    @staticmethod
    def derive_key(passphrase: str, salt: bytes) -> bytes:
        """Derive a Fernet key from a human *passphrase* and *salt* via scrypt.

        Lets a memorable passphrase unlock a vault while resisting brute force
        (scrypt is memory-hard) -> confidentiality. The same passphrase and salt
        always yield the same key -> determinism. The salt must be stored
        alongside the vault by the caller; it need not be secret.
        """
        kdf = Scrypt(salt=salt, length=_KEY_LEN, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
        raw = kdf.derive(passphrase.encode("utf-8"))
        return base64.urlsafe_b64encode(raw)

    # --- internals ----------------------------------------------------------

    @staticmethod
    def _new_ref() -> str:
        """Generate a fresh opaque ref from a CSPRNG -> unlinkability."""
        return secrets.token_urlsafe(_REF_NBYTES)

    def _encrypt(self, identity: ContributorIdentity) -> str:
        """Encrypt an identity to a base64 Fernet token string."""
        plaintext = identity._to_canonical_json().encode("utf-8")
        return self._fernet.encrypt(plaintext).decode("ascii")

    def _decrypt(self, token: str) -> ContributorIdentity:
        """Decrypt a Fernet token; re-raise tampering/wrong-key as vault error.

        Catches :class:`InvalidToken` (raised on a wrong key or any tampering) and
        re-raises without echoing the ciphertext or any plaintext -> no-outing
        rule, integrity.
        """
        try:
            raw = self._fernet.decrypt(token.encode("ascii"))
        except InvalidToken as exc:
            raise IdentityVaultError("identity decryption failed (wrong key or tampering)") from exc
        return ContributorIdentity._from_json_bytes(raw)

    def _persist(self) -> None:
        """Write the store to disk atomically (temp file then rename).

        The temp file is created in the same directory so ``os.replace`` is an
        atomic rename on the same filesystem; a crash mid-write leaves the old
        vault intact -> integrity, fault tolerance.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = canonical_json(self._store)
        tmp = self._path.with_name(f"{self._path.name}.{os.getpid()}.tmp")
        try:
            # Create owner-only (0o600) BEFORE writing any ciphertext, so the vault
            # is never momentarily world-readable on a shared host -> confidentiality.
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(tmp, self._path)
            os.chmod(self._path, 0o600)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise IdentityVaultError(f"vault could not be written: {self._path}") from exc
