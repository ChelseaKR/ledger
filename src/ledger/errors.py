"""One exception hierarchy for the whole archive (analyzability, debuggability).

Every error a caller can reasonably handle is a subclass of :class:`LedgerError`,
so a CLI, server, or test can catch the family and react. Two rules hold across
the hierarchy and are part of ledger's threat model:

1. **No error message ever discloses a contributor's identity or a sealed value.**
   Messages name the *object* (a content address, a record id, a bag path) and the
   *condition*, never the protected content. This is asserted by the no-outing audit.
2. **Failures are surfaced, never swallowed.** A fixity mismatch or an unreachable
   replica raises (or is recorded as a labelled preservation event); it is never
   silently treated as success (failure transparency).

EXP-05 (docs/ideation/03-expansions.md): the preservation-layer errors
(:class:`StoreError`, :class:`ObjectNotFound`, :class:`BagValidationError`) now
live in the standalone :mod:`ledger_preservation_core` library and are
re-exported here unchanged, so ``from ledger.errors import BagValidationError``
keeps working exactly as before. Because they root at
:class:`~ledger_preservation_core.errors.LedgerPreservationError` rather than at
:class:`LedgerError` (that library has no knowledge of ledger), any call site that
needs to catch *both* app-level and preservation-core errors broadly — the CLI's
top-level handler, and the server's and ingest's defensive blocks around bag/store
operations — catches the tuple ``(LedgerError, LedgerPreservationError)``; see
``ledger.cli``, ``ledger.server``, and ``ledger.ingest``. A narrow
``except BagValidationError`` (or any other single re-exported class) is
unaffected and needs no change.
"""

from __future__ import annotations

from ledger_preservation_core.errors import (
    BagValidationError,
    LedgerPreservationError,
    ObjectNotFound,
    StoreError,
)

__all__ = [
    "AccessDenied",
    "BagValidationError",
    "ConfigError",
    "ConsentError",
    "FixityError",
    "IdentityVaultError",
    "LedgerError",
    "LedgerPreservationError",
    "ModerationError",
    "ObjectNotFound",
    "PolicyError",
    "QuarantineError",
    "ReplicationError",
    "StoreError",
    "ValidationError",
]


class LedgerError(Exception):
    """Base class for every application-level error ledger raises deliberately.

    See the module docstring: the preservation-core errors re-exported above are
    NOT subclasses of this class. A caller that needs to catch every error ledger
    *or* its preservation-core dependency can raise must catch both bases.
    """


class ConfigError(LedgerError):
    """The configuration is missing, malformed, or internally inconsistent."""


class ValidationError(LedgerError):
    """User input was rejected, carrying a *localizable* reason code.

    The contributor write path shows a contributor *why* a submission was declined,
    and that reason must be in their language. So this error carries a language-neutral
    ``code`` (an i18n catalog key) plus any ``fields`` to interpolate, and a UI renders
    ``i18n.t(lang, code, **fields)`` rather than the raw English ``str(exc)``. The
    English ``message`` is still set for logs and non-UI callers. Like every ledger
    error, neither the code, the fields, nor the message names a submitted value or an
    identity (no-outing rule): codes describe the *condition* ("a title is required"),
    never the content.
    """

    def __init__(self, message: str, *, code: str, **fields: object) -> None:
        super().__init__(message)
        self.code = code
        self.fields = fields


# --- preservation -----------------------------------------------------------
#
# StoreError, ObjectNotFound, and BagValidationError are defined in
# ledger_preservation_core.errors (EXP-05 extraction) and re-exported above.


class FixityError(LedgerError):
    """A checksum did not match its manifest. The object is presumed corrupt."""


class QuarantineError(FixityError):
    """A copy failed fixity and has been quarantined; it must not be served."""


class ReplicationError(LedgerError):
    """A replica location was unreachable or rejected a bag.

    Raised after the failure has been recorded as a preservation event, so the
    condition is auditable rather than hidden (failure transparency).
    """


# --- disclosure & safety ----------------------------------------------------


class PolicyError(LedgerError):
    """An access policy is malformed or references an unknown level."""


class AccessDenied(LedgerError):
    """A viewer's grant does not permit the requested field, payload, or record.

    The message names only the requested object and the missing level, never the
    withheld value.
    """


class IdentityVaultError(LedgerError):
    """The contributor-identity vault could not be opened, read, or written.

    Includes a missing or wrong key. The message never echoes vault contents.
    """


class ConsentError(LedgerError):
    """A consent or takedown request could not be honoured as stated."""


class ModerationError(LedgerError):
    """A moderation or takedown action was malformed or unauthorized."""
