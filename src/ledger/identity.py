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

.. note::
   This vault's confidentiality primitives (Fernet, scrypt) and unversioned
   ``"enc:"`` ciphertext marker are the subject of a committed harvest-now-
   decrypt-later analysis and algorithm-lifecycle policy —
   ``docs/audits/crypto-agility-pq-posture.md`` (EXP-13). It documents which
   parts of this module are quantum-exposed today (a Grover-reduced symmetric
   margin only; no public-key primitive exists here yet) and the conditions
   that gate any future algorithm change, including hybrid post-quantum
   encryption. Read that document, and the companion envelope/key-hierarchy
   design it cross-references, before changing the encryption construction in
   this module or adding a new algorithm to it. The companion sealing-layer
   design also records that this vault's single Fernet key currently protects two
   different asset classes
   (identity ciphertext, via :meth:`add`/:meth:`resolve`; sealed-content
   ciphertext, via :meth:`encrypt_text`/:meth:`encrypt_bytes`), and
   :meth:`encrypt_text`'s ``"enc:"`` string prefix is an in-band, unversioned type
   marker. Both are open design questions pending an external cryptography
   review before RM2 broadens at-rest encryption on top of them — see
   ``docs/audits/crypto-design-review-sealing-layer.md`` (FIX-11). Do not add a
   fourth use of this key, or a second in-band string-prefix convention, without
   reading ``docs/audits/crypto-design-review-sealing-layer.md`` first.
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

from ledger._filelock import file_lock
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
        # Check-and-create under the same stable sibling lock used by mutations.
        # Otherwise two processes can both observe an absent path and the second
        # can silently replace the first process's newly-created vault.
        with file_lock(target):
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
        with file_lock(target):
            # Re-check after acquiring the lock in case a concurrent operation
            # removed/replaced the path between the optimistic check and here.
            if not target.exists():
                raise IdentityVaultError(f"vault not found: {target}")
            vault._reload()
        return vault

    # --- mutation -----------------------------------------------------------

    def add(self, identity: ContributorIdentity) -> str:
        """Encrypt *identity* under a fresh random ref and persist atomically.

        Returns the new opaque ``identity_ref``. The ref is generated from a CSPRNG
        and is independent of the identity contents, so it carries no identifying
        signal and may safely live in a record -> unlinkability.

        Locked (:func:`ledger._filelock.file_lock`) around the mutate-then-persist
        step: the threaded browse server can run concurrent ingests against the
        same ``Archive`` (and so the same vault instance), and :meth:`_persist`'s
        temp filename is derived only from ``os.getpid()`` -- identical across
        threads in one process. Without a lock, two concurrent calls open and
        write the *same* temp path, and the second thread's truncating open can
        corrupt the first thread's in-flight write before either renames it into
        place (fault tolerance, integrity of the archive's most sensitive file).

        The on-disk store is reloaded *inside* that lock. This matters when two
        archive handles or processes have separate ``IdentityVault`` instances:
        locking only each instance's stale in-memory dictionary would still let a
        later writer erase the earlier writer's update.
        """
        with file_lock(self._path):
            self._reload()
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
        # Refresh under the same lock used by revoke. A long-lived archive handle
        # must not keep disclosing an identity from stale memory after another
        # process has durably revoked it.
        with file_lock(self._path):
            self._reload()
            token = self._store.get(ref)
            if token is None:
                raise IdentityVaultError(f"unknown identity ref: {ref}")
            return self._decrypt(token)

    def revoke(self, ref: str) -> None:
        """Remove the mapping for *ref*, persisting atomically.

        Idempotent: revoking an absent ref is a no-op so a takedown can be retried
        safely. Honours consent revocation / takedown -> autonomy, consent.

        Locked like :meth:`add` so a concurrent takedown and ingest never race on
        the same temp file (see :meth:`add`'s docstring).
        """
        with file_lock(self._path):
            self._reload()
            if ref in self._store:
                del self._store[ref]
                self._persist()

    def contains(self, ref: str) -> bool:
        """Return whether *ref* currently maps to a stored identity."""
        with file_lock(self._path):
            self._reload()
            return ref in self._store

    def __len__(self) -> int:
        """Number of sealed identities in the vault (no contents revealed)."""
        with file_lock(self._path):
            self._reload()
            return len(self._store)

    # --- key rotation -------------------------------------------------------

    def rekey(self, new_key: bytes) -> int:
        """Re-encrypt every sealed identity under *new_key* and persist atomically.

        Key rotation is a *when*, not an *if* (steward turnover, a suspected
        exposure, a compliance cadence), so it must be a first-class, recoverable
        operation rather than a manual file-surgery a steward improvises. Every
        entry is decrypted with the current key and re-encrypted with the new one;
        the swap to the new key happens only after *all* entries re-encrypt, so a
        failure part-way through leaves the vault untouched (atomicity, fault
        tolerance). The refs are unchanged — they are random and key-independent —
        so no record that points at the vault needs to change (unlinkability holds
        across a rotation).

        Returns the number of identities re-encrypted. Never echoes a key, a ref's
        plaintext, or any ciphertext (no-outing rule). Raises
        :class:`IdentityVaultError` on an invalid *new_key* or if any stored entry
        fails to decrypt with the current key (wrong current key or tampering),
        before anything is written.
        """
        try:
            new_fernet = Fernet(new_key)
        except (ValueError, TypeError) as exc:
            raise IdentityVaultError("invalid Fernet key") from exc
        # Locked like :meth:`add`/:meth:`revoke` so a rotation can never race a
        # concurrent add/revoke on the same vault file (see :meth:`add`'s
        # docstring for why the unlocked temp-file write is unsafe).
        with file_lock(self._path):
            self._reload()
            # Re-encrypt into a fresh map first; only commit if every entry succeeds,
            # so a mid-rotation failure cannot leave a half-rotated vault -> integrity.
            rotated: dict[str, str] = {}
            for ref, token in self._store.items():
                try:
                    raw = self._fernet.decrypt(token.encode("ascii"))
                except InvalidToken as exc:
                    raise IdentityVaultError(
                        "identity decryption failed during rekey (wrong key or tampering)"
                    ) from exc
                rotated[ref] = new_fernet.encrypt(raw).decode("ascii")
            self._fernet = new_fernet
            self._store = rotated
            self._persist()
            return len(rotated)

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

    def _reload(self) -> None:
        """Replace the in-memory map with the current authenticated disk state.

        Callers that need a linearizable read or mutation hold :func:`file_lock`
        around this method and the rest of their operation. Reloading inside the
        critical section prevents separate ``IdentityVault`` instances from
        clobbering one another with stale whole-file snapshots.
        """
        try:
            loaded = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise IdentityVaultError(f"vault could not be read: {self._path}") from exc
        if not isinstance(loaded, dict):
            raise IdentityVaultError(f"vault is malformed: {self._path}")
        store: dict[str, str] = {}
        for ref, ciphertext in loaded.items():
            if not isinstance(ref, str) or not isinstance(ciphertext, str):
                raise IdentityVaultError(f"vault is malformed: {self._path}")
            store[ref] = ciphertext
        # Verify the active key against one entry, surfacing a rekey performed by
        # another process (or tampering) before this instance can overwrite it.
        for ciphertext in store.values():
            self._decrypt(ciphertext)
            break
        self._store = store

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
            return ContributorIdentity._from_json_bytes(raw)
        except (InvalidToken, UnicodeError, ValueError, TypeError, AttributeError) as exc:
            raise IdentityVaultError("identity decryption failed (wrong key or tampering)") from exc

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
