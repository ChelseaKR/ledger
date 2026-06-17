"""Contributor consent-request backend (user research P0-2: "revocable consent").

A contributor must be able to *act* on the consent they gave at ingest — to
withdraw a record, tighten its disclosure, correct a detail, or simply reach a
steward — without holding an account. This module is the storage and verification
layer behind that promise:

* :class:`ConsentRequest` is the small, typed record of one such ask. It carries a
  ``record_id``, a ``kind`` (one of :data:`VALID_KINDS`), a free-text ``message``,
  and a lifecycle ``status``. It is append-only state a steward works through.
* :class:`ConsentRequestStore` is an append-only, atomically-written JSON list of
  those requests, so a request is never lost and a crash mid-write can never leave
  a half-written queue.
* A *claim token* lets the contributor prove they are the author of a record
  without an account. It is a stateless HMAC over the ``record_id`` under a server
  secret, handed to the contributor at ingest and required to file a request. It is
  verified in constant time so a forged token leaks no timing signal.

No-outing rule, enforced here by construction:

* This module never stores, logs, or places into an exception a contributor's
  identity. A :class:`ConsentRequest` references a record by its public
  ``record_id`` only — never an ``identity_ref`` and never a real person.
* The contributor-supplied ``message`` is *content*, not identity, but it is still
  private: it is persisted to the store, yet it is never written to a log or echoed
  in an error. Malformed-input errors name the offending *field*, never its value.
* A claim token is a sealed value (it authorises action on a record), so it is
  never logged or placed in an error message either.

Determinism: callers that need reproducible output (golden fixtures, tests) pass an
explicit ``request_id`` and ``created_at`` rather than relying on the random/clock
defaults.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path

from ledger.errors import LedgerError
from ledger.models import now_iso

__all__ = [
    "VALID_KINDS",
    "VALID_STATUSES",
    "ConsentRequest",
    "ConsentRequestStore",
    "issue_claim_token",
    "verify_claim_token",
]

# The documented set of asks a contributor can make about their own record. Kept
# small and closed so a steward queue has a predictable, styleable vocabulary and a
# typo'd kind is rejected at construction rather than mis-routed (correctness).
VALID_KINDS: frozenset[str] = frozenset({"withdraw", "tighten", "correct", "contact"})

# The lifecycle a steward may move a request through. "open" is the initial state a
# request is filed in; a steward acknowledges it (seen) and later resolves it
# (acted on). Kept closed so :meth:`ConsentRequestStore.resolve` cannot stamp an
# arbitrary status onto the audit trail (correctness, accountability).
_OPEN: str = "open"
VALID_STATUSES: frozenset[str] = frozenset({_OPEN, "acknowledged", "resolved"})

# The prefix that marks a claim token, so a token is recognisable and cannot be
# confused with another opaque string. The body is a hex HMAC-SHA256 digest.
_CLAIM_PREFIX: str = "claim:"


@dataclass(frozen=True)
class ConsentRequest:
    """One contributor ask about one record, with its lifecycle status.

    Immutable so a queued request cannot be mutated in place; a steward advances it
    by rewriting the store (see :meth:`ConsentRequestStore.resolve`). The
    ``record_id`` is the record's *public* identifier — never an ``identity_ref``
    and never a contributor identity (no-outing rule).
    """

    record_id: str
    kind: str
    message: str
    request_id: str = field(default_factory=lambda: secrets.token_hex(8))
    status: str = _OPEN
    created_at: str = field(default_factory=now_iso)

    def __post_init__(self) -> None:
        # Reject an unknown kind/status at construction so a malformed request can
        # never enter the queue. The error names the offending field, never the
        # private message content (no-outing rule).
        if self.kind not in VALID_KINDS:
            raise LedgerError(
                f"unknown consent request kind; expected one of {sorted(VALID_KINDS)}"
            )
        if self.status not in VALID_STATUSES:
            raise LedgerError(
                f"unknown consent request status; expected one of {sorted(VALID_STATUSES)}"
            )

    def to_dict(self) -> dict[str, str]:
        """Serialize to a plain, JSON-ready mapping with a stable field order."""
        return {
            "record_id": self.record_id,
            "kind": self.kind,
            "message": self.message,
            "request_id": self.request_id,
            "status": self.status,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> ConsentRequest:
        """Rebuild from a mapping produced by :meth:`to_dict`.

        Robustness/correctness: a malformed entry (missing required key, non-string
        scalar, unknown kind/status) raises :class:`~ledger.errors.LedgerError`
        naming the *field* at fault — never the private message content or any
        sealed value (no-outing rule).
        """
        if not isinstance(data, dict):
            raise LedgerError("consent request must be a mapping")
        record_id = _require_str(data, "record_id")
        kind = _require_str(data, "kind")
        message = _require_str(data, "message")
        request_id = _require_str(data, "request_id")
        status = _require_str(data, "status")
        created_at = _require_str(data, "created_at")
        # __post_init__ re-validates kind/status, turning a tampered file into a
        # clear LedgerError rather than a silently-accepted bad value.
        return cls(
            record_id=record_id,
            kind=kind,
            message=message,
            request_id=request_id,
            status=status,
            created_at=created_at,
        )


class ConsentRequestStore:
    """An append-only, atomically-written queue of :class:`ConsentRequest`.

    Persisted as a single JSON list. Append-only -> auditability: a request, once
    filed, is never dropped; a steward advances its ``status`` in place but the
    request itself remains. Atomic write (temp file + ``os.replace``) ->
    integrity/fault tolerance: a crash mid-write leaves the prior queue intact, and
    a reader never sees a half-written list. A missing file reads as an empty queue,
    so a fresh archive needs no setup step (installability).
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def all(self) -> list[ConsentRequest]:
        """Every request ever filed, in file order. Missing file -> empty list."""
        return self._read()

    def open_requests(self) -> list[ConsentRequest]:
        """The subset of requests still awaiting steward action (``status == open``)."""
        return [req for req in self._read() if req.status == _OPEN]

    def add(self, req: ConsentRequest) -> None:
        """Append ``req`` to the queue and persist atomically (append-only).

        Reads the current queue, appends, and rewrites the whole list under an
        atomic rename. The message content is persisted but never logged
        (no-outing rule).
        """
        requests = self._read()
        requests.append(req)
        self._write(requests)

    def resolve(self, request_id: str, status: str) -> None:
        """Advance the request with ``request_id`` to ``status`` and persist.

        ``status`` must be ``acknowledged`` or ``resolved`` — a steward may move a
        request forward in its lifecycle but may not re-open it or stamp an
        arbitrary state (correctness, accountability). A missing ``request_id``
        raises :class:`~ledger.errors.LedgerError` naming the id (which is public,
        not identity) so a typo fails loudly rather than silently no-op'ing.
        """
        if status not in {"acknowledged", "resolved"}:
            raise LedgerError(
                "consent request can only be resolved to 'acknowledged' or 'resolved'"
            )
        requests = self._read()
        found = False
        updated: list[ConsentRequest] = []
        for req in requests:
            if req.request_id == request_id:
                found = True
                updated.append(
                    ConsentRequest(
                        record_id=req.record_id,
                        kind=req.kind,
                        message=req.message,
                        request_id=req.request_id,
                        status=status,
                        created_at=req.created_at,
                    )
                )
            else:
                updated.append(req)
        if not found:
            raise LedgerError(f"no consent request with id {request_id!r}")
        self._write(updated)

    def _read(self) -> list[ConsentRequest]:
        """Load and parse the JSON list; a missing file is an empty queue."""
        try:
            text = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        try:
            raw: object = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LedgerError(f"consent store {self._path} is not valid JSON: {exc}") from exc
        if not isinstance(raw, list):
            raise LedgerError(f"consent store {self._path} must contain a JSON list")
        return [ConsentRequest.from_dict(_as_mapping(item)) for item in raw]

    def _write(self, requests: list[ConsentRequest]) -> None:
        """Write the whole queue atomically (temp file in the same dir, then rename).

        The temp file lives beside the target so ``os.replace`` is an atomic rename
        on the same filesystem; a crash mid-write leaves the prior queue intact
        -> integrity, fault tolerance.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps([req.to_dict() for req in requests], indent=2, ensure_ascii=False)
        tmp = self._path.with_name(f"{self._path.name}.{os.getpid()}.tmp")
        try:
            tmp.write_text(payload + "\n", encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise LedgerError(f"consent store could not be written: {self._path}") from exc


def issue_claim_token(record_id: str, secret: bytes) -> str:
    """Mint the claim token a contributor uses to prove authorship of ``record_id``.

    Stateless by design: the token is an HMAC-SHA256 over the public ``record_id``
    under the server ``secret``, so no per-contributor account or stored token table
    is needed (affordability, unlinkability). The token is a *sealed* value — it
    authorises action on a record — so it is never logged or placed in an error.
    """
    digest = hmac.new(secret, record_id.encode("utf-8"), sha256).hexdigest()
    return f"{_CLAIM_PREFIX}{digest}"


def verify_claim_token(record_id: str, token: str, secret: bytes) -> bool:
    """Whether ``token`` is a valid claim token for ``record_id`` under ``secret``.

    Constant-time comparison (:func:`hmac.compare_digest`) so a near-miss forgery
    leaks no timing signal about how many bytes matched (safety). Returns ``False``
    for any malformed, wrong-record, wrong-secret, or tampered token rather than
    raising, so a verification check is a simple boolean gate.
    """
    expected = issue_claim_token(record_id, secret)
    return hmac.compare_digest(expected, token)


def _require_str(data: Mapping[str, object], key: str) -> str:
    """Return ``data[key]`` as a ``str`` or raise naming the missing/bad field.

    Never echoes the value, so a malformed message field cannot leak its content
    into an error (no-outing rule).
    """
    if key not in data:
        raise LedgerError(f"consent request is missing required field {key!r}")
    value = data[key]
    if not isinstance(value, str):
        raise LedgerError(f"consent request field {key!r} must be a string")
    return value


def _as_mapping(item: object) -> dict[str, object]:
    """Coerce a JSON list element to a mapping, or raise a clear error."""
    if not isinstance(item, dict):
        raise LedgerError("consent store entries must be mappings")
    return item
