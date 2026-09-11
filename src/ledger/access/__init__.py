"""The disclosure / access-control core — ledger's safety heart.

Every read path in ledger funnels through this package: :func:`disclose` is the
sole constructor of the safe read shape, and :func:`is_visible` is the single
visibility decision point. Grants are built under least privilege, and redaction
is a recorded transform. Importing these names from one place keeps the safety
boundary small and auditable (simplicity, provability).
"""

from __future__ import annotations

from ledger.access.grants import (
    anonymous,
    build_grant,
    community_member,
    steward,
)
from ledger.access.policy import (
    arrangement_permits,
    container_is_visible,
    disclose,
    disclose_container,
    is_listable,
    is_visible,
    visible_placement,
    withheld_reason,
)
from ledger.access.redaction import redact_field, redact_payload

__all__ = [
    "anonymous",
    "arrangement_permits",
    "build_grant",
    "community_member",
    "container_is_visible",
    "disclose",
    "disclose_container",
    "is_listable",
    "is_visible",
    "redact_field",
    "redact_payload",
    "steward",
    "visible_placement",
    "withheld_reason",
]
