"""Grant construction under least privilege.

A :class:`~ledger.models.Grant` says, narrowly, what one viewer may see. These
helpers build the common grants explicitly rather than letting privilege accrue
implicitly (least privilege, securability). The crucial separation:
``identity_unseal`` is independent of ``is_steward`` -- a steward can administer
the archive without being able to resolve any contributor's real identity, so
ordinary stewardship is never an outing risk (confidentiality).
"""

from __future__ import annotations

import hmac
import json
import re
from collections.abc import Iterable
from hashlib import sha256
from pathlib import Path

from ledger.errors import LedgerError
from ledger.models import PUBLIC_GRANT, AccessPolicy, Grant, parse_iso

# A grant subject must be a plain, delimiter-free label so it survives round-tripping
# through a ``subject:expiry:mac`` token unambiguously. Real subjects ("steward-1")
# already satisfy this; a subject that does not is rejected at issue time rather than
# minted into an unparseable token (correctness, fail-closed).
_SUBJECT_RE = re.compile(r"\A[A-Za-z0-9._-]+\Z")


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


def designated_successor(subject: str, *, expires_at: str | None = None) -> Grant:
    """Build a designated-successor grant: steward-level, but with NO identity-unseal.

    Mutual-aid groups and volunteer collectives disband, and their knowledge dies
    with them unless someone is empowered to take over. A *designated successor* is
    the person or collective a folding group names to inherit stewardship of its
    archive (EX1, group continuity). This grant gives them everything an ordinary
    steward has — they can read access-restricted content and administer the archive
    — but, exactly like :func:`steward`, it holds **no** ``identity_unseal`` tokens:
    inheriting the archive is not inheriting the power to out the people in it. The
    successor must be granted identity-unseal separately and deliberately, under the
    community's own governance, if ever (least privilege, the no-outing rule).

    ``expires_at`` may bound the grant when the hand-off is meant to be temporary
    (e.g. a caretaker during a transition); omitted, it does not expire.
    """
    return build_grant(
        subject,
        levels=(AccessPolicy.PUBLIC, AccessPolicy.COMMUNITY, AccessPolicy.STEWARDS),
        is_steward=True,
        expires_at=expires_at,
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


# --- authenticated grant tokens ---------------------------------------------
#
# The old ``X-Ledger-Grant: <subject>`` header made the subject string itself the
# whole credential: anyone who guessed or shoulder-surfed ``devon-steward`` held
# steward access to sealed *content*. That is the sharpest gap in the archive's
# threat model (doxxing, hostile observers). These helpers replace the bare subject
# with an unforgeable, HMAC-signed token, reusing the very pattern the consent claim
# token already uses (:func:`ledger.consent.issue_claim_token`): an HMAC-SHA256 over
# a public identifier under a server-held secret, verified in constant time. Zero new
# dependencies. Without the secret a token cannot be minted, so a guessed subject is
# byte-for-byte the anonymous experience (no oracle).


def _epoch(iso: str) -> int:
    """Whole seconds since the Unix epoch for an ISO-8601 instant (UTC-aware)."""
    return int(parse_iso(iso).timestamp())


def issue_grant_token(subject: str, secret: bytes, *, expires_at: str | None = None) -> str:
    """Mint the ``X-Ledger-Grant`` token a steward presents to authenticate ``subject``.

    The token is ``<subject>:<expiry>:<hmac>`` where ``expiry`` is ``0`` (never
    expires) or the token's expiry as whole epoch seconds, and ``hmac`` is
    HMAC-SHA256 over ``<subject>:<expiry>`` under ``secret``. Because the MAC binds
    both the subject and the expiry, neither can be altered without invalidating the
    token. It is a *sealed* value (it authorises access), so — like a claim token —
    it is never logged or placed in an error (no-outing rule).

    ``expires_at`` (ISO-8601) bounds how long a captured token can be replayed. A
    subject that is not a plain ``[A-Za-z0-9._-]`` label raises
    :class:`~ledger.errors.LedgerError` rather than minting an ambiguous token.
    """
    if not _SUBJECT_RE.match(subject):
        raise LedgerError("grant subject must be a plain [A-Za-z0-9._-] label to be tokenizable")
    expiry = "0" if expires_at is None else str(_epoch(expires_at))
    mac = hmac.new(secret, f"{subject}:{expiry}".encode(), sha256).hexdigest()
    return f"{subject}:{expiry}:{mac}"


def verify_grant_token(token: str, secret: bytes, *, now: str) -> str | None:
    """Return the authenticated subject of ``token``, or ``None`` if it is not valid.

    Returns ``None`` — never raising, never distinguishing *why* — for an empty
    secret, a malformed token, a wrong/forged MAC, or an expired token, so every
    rejection is byte-for-byte the anonymous experience and leaks no oracle. The MAC
    is compared in constant time (:func:`hmac.compare_digest`) so a near-miss forgery
    reveals nothing about how many bytes matched (safety). ``now`` (ISO-8601) is the
    instant expiry is judged against; an unparseable ``now`` fails closed (expired).
    """
    if not secret:
        # No configured secret means no token can be authenticated: deny by default.
        return None
    parts = token.split(":")
    if len(parts) != 3:
        return None
    subject, expiry, mac = parts
    if not _SUBJECT_RE.match(subject) or not expiry.isdigit():
        return None
    expected = hmac.new(secret, f"{subject}:{expiry}".encode(), sha256).hexdigest()
    if not hmac.compare_digest(expected, mac):
        return None
    if expiry != "0":
        try:
            if _epoch(now) >= int(expiry):
                return None
        except (ValueError, TypeError, OverflowError):
            return None
    return subject
