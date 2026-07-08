"""One exception hierarchy for the preservation core (analyzability, debuggability).

Every error this library raises deliberately is a subclass of
:class:`LedgerPreservationError`, so a caller can catch the family and react.
Error messages name the *object* (a content address, a bag path) and the
*condition*, never file contents — this library never reads or emits payload
bytes in an error message.
"""

from __future__ import annotations


class LedgerPreservationError(Exception):
    """Base class for every error this library raises deliberately."""


class StoreError(LedgerPreservationError):
    """The content-addressed store could not satisfy a request."""


class ObjectNotFound(StoreError):
    """No object exists at the given content address."""


class BagValidationError(LedgerPreservationError):
    """A BagIt bag is malformed or fails manifest validation (RFC 8493)."""
