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
"""

from __future__ import annotations


class LedgerError(Exception):
    """Base class for every error ledger raises deliberately."""


class ConfigError(LedgerError):
    """The configuration is missing, malformed, or internally inconsistent."""


# --- preservation -----------------------------------------------------------


class StoreError(LedgerError):
    """The content-addressed store could not satisfy a request."""


class ObjectNotFound(StoreError):
    """No object exists at the given content address."""


class FixityError(LedgerError):
    """A checksum did not match its manifest. The object is presumed corrupt."""


class QuarantineError(FixityError):
    """A copy failed fixity and has been quarantined; it must not be served."""


class BagValidationError(LedgerError):
    """A BagIt bag is malformed or fails manifest validation (RFC 8493)."""


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
