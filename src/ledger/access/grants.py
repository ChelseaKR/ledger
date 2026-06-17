"""Grant construction under least privilege.

A :class:`~ledger.models.Grant` says, narrowly, what one viewer may see. These
helpers build the common grants explicitly rather than letting privilege accrue
implicitly (least privilege, securability). The crucial separation:
``identity_unseal`` is independent of ``is_steward`` -- a steward can administer
the archive without being able to resolve any contributor's real identity, so
ordinary stewardship is never an outing risk (confidentiality).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from ledger.models import PUBLIC_GRANT, AccessPolicy, Grant


def anonymous() -> Grant:
    """Return the shared anonymous public grant (sees only PUBLIC, unsealed).

    This is the default a read path uses when no one has authenticated -- the
    narrowest grant (deny by default, least privilege).
    """
    return PUBLIC_GRANT


def build_grant(
    subject: str,
    *,
    levels: Iterable[AccessPolicy] = (AccessPolicy.PUBLIC,),
    is_steward: bool = False,
    identity_unseal: Iterable[str] = (),
    expires_at: str | None = None,
) -> Grant:
    """Build a grant for ``subject`` from explicit, least-privilege parameters.

    Every capability must be named: a caller cannot accidentally hand out
    community access or identity-unseal power by omission. ``identity_unseal`` is
    kept separate from ``is_steward`` so granting stewardship never silently
    grants the ability to out a contributor (least privilege, securability).
    """
    return Grant(
        subject=subject,
        levels=frozenset(levels),
        is_steward=is_steward,
        identity_unseal=frozenset(identity_unseal),
        expires_at=expires_at,
    )


def community_member(subject: str) -> Grant:
    """Build a community-member grant: PUBLIC + COMMUNITY, no stewardship.

    Sees public and community-level material; cannot see steward-only or sealed
    content, and holds no identity-unseal tokens (least privilege).
    """
    return build_grant(
        subject,
        levels=(AccessPolicy.PUBLIC, AccessPolicy.COMMUNITY),
    )


def steward(subject: str) -> Grant:
    """Build a steward grant: PUBLIC + COMMUNITY + STEWARDS, ``is_steward=True``.

    A steward can see every disclosure level (including sealed material, for
    administration) but holds NO ``identity_unseal`` tokens: seeing a record is
    not the same as seeing who contributed it. Resolving a contributor identity
    always requires an explicit ``identity_unseal`` grant (least privilege,
    confidentiality — stewardship is not an outing risk).
    """
    return build_grant(
        subject,
        levels=(AccessPolicy.PUBLIC, AccessPolicy.COMMUNITY, AccessPolicy.STEWARDS),
        is_steward=True,
    )


def load_grants(path: Path) -> dict[str, Grant]:
    """Load a subject -> grant mapping from a JSON file.

    The file maps each subject to a grant spec with optional keys ``levels``,
    ``is_steward``, ``identity_unseal``, and ``expires_at``. A missing file is
    tolerated by returning ``{}`` -- absence of a grant file means no one is
    privileged, the safe default (deny by default, fault tolerance).

    Each spec is rebuilt through :func:`build_grant`, so loaded grants obey the
    same least-privilege construction as code-built ones (one construction path).
    """
    if not path.exists():
        return {}

    raw = json.loads(path.read_text(encoding="utf-8"))
    grants: dict[str, Grant] = {}
    for subject, spec in raw.items():
        levels = tuple(AccessPolicy(level) for level in spec.get("levels", ("public",)))
        grants[subject] = build_grant(
            subject,
            levels=levels,
            is_steward=bool(spec.get("is_steward", False)),
            identity_unseal=tuple(spec.get("identity_unseal", ())),
            expires_at=spec.get("expires_at"),
        )
    return grants
