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


class CaptionParseError(LedgerError):
    """An uploaded WebVTT or SRT caption/transcript file is malformed.

    Raised by :mod:`ledger.captions` on a structural problem (missing ``WEBVTT``
    signature, an unparsable timestamp, an end time not after its start, or a
    cue/block with no timings). The message names only the *line number and
    condition*, never the caption text itself, so a malformed file's content
    cannot leak into a log or an error (no-outing rule, defense in depth: caption
    text is contributor-supplied prose, potentially about the same sensitive
    material a transcript field would carry).
    """


class AggregationRefused(LedgerError):
    """A reading-room aggregate query (EXP-14) was refused to protect k-anonymity.

    Raised when answering would fall below the archive's k-anonymity floor even
    after cell suppression, or when two queries' matching-record sets differ by
    fewer than the k-floor (a differencing attack). Fail-closed: the caller gets
    no partial answer, and the refusal itself — never the underlying counts or
    record ids — is what gets logged.
    """
