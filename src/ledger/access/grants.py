"""Grant construction under least privilege.

A :class:`~ledger.models.Grant` says, narrowly, what one viewer may see. These
helpers build the common grants explicitly rather than letting privilege accrue
implicitly (least privilege, securability). The crucial separation:
``identity_unseal`` is independent of ``is_steward`` -- a steward can administer
the archive without being able to resolve any contributor's real identity, so
ordinary stewardship is never an outing risk (confidentiality).
"""

from __future__ import annotations

import base64
import binascii
import hmac
import json
from collections.abc import Iterable
from hashlib import sha256
from pathlib import Path

from ledger.models import PUBLIC_GRANT, AccessPolicy, Grant, parse_iso


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


# --- authenticated capability tokens ----------------------------------------
#
# A grant token is a *bearer capability* that authenticates the ``X-Ledger-Grant``
# header: possession of a valid token for ``subject`` is proof the header may be
# trusted to name that subject. It is stateless and self-verifying — an
# HMAC-SHA256 over ``subject`` and its expiry under a server-side secret — so no
# per-viewer session or token table is needed (affordability, unlinkability). The
# subject named in the header is still only ever a *lookup key* into the
# pre-provisioned grants file; a valid MAC confers no privilege by itself, it only
# lets the server believe the header (deny by default, least privilege).
#
# The wire format is three colon-separated parts, ``subject:expiry:mac``, where
# ``subject`` and ``expiry`` are individually base64url-encoded so an arbitrary
# subject (an email, a name with punctuation) can never collide with the ``:``
# field separator or with the colons inside an ISO-8601 timestamp. The token is a
# *sealed* value — like a claim token it authorises action — so it is never logged
# and never placed in an error message.

_GRANT_TOKEN_PARTS = 3


def _b64(text: str) -> str:
    """URL-safe base64 of ``text`` with padding stripped (no ``:`` in the output)."""
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _unb64(part: str) -> str:
    """Inverse of :func:`_b64`; raises on any non-base64 or non-UTF-8 input."""
    padding = "=" * (-len(part) % 4)
    return base64.urlsafe_b64decode(part + padding).decode("utf-8")


def _grant_mac(subject: str, expires_at: str, secret: bytes) -> str:
    """HMAC-SHA256 over the encoded ``subject`` and ``expiry`` under ``secret``.

    The MAC covers the *encoded* parts exactly as they travel on the wire, so the
    signed message is unambiguous: there is one and only one way to reconstruct the
    two fields from the token, closing off any splitting/confusion forgery.
    """
    message = f"{_b64(subject)}:{_b64(expires_at)}".encode("ascii")
    return hmac.new(secret, message, sha256).hexdigest()


def issue_grant_token(subject: str, secret: bytes, *, expires_at: str = "") -> str:
    """Mint an authenticated capability token binding ``subject`` and its ``expiry``.

    ``expires_at`` is an ISO-8601 instant (UTC ``Z`` form, as :func:`ledger.models.now_iso`
    produces); pass ``""`` for a token that never expires. The token is stateless —
    all the server needs to verify it is the shared ``secret`` — and is a sealed
    value the caller must never log (confidentiality).
    """
    return f"{_b64(subject)}:{_b64(expires_at)}:{_grant_mac(subject, expires_at, secret)}"


def verify_grant_token(token: str, secret: bytes, *, now: str) -> str | None:
    """Return the authenticated ``subject`` for a valid ``token``, else ``None``.

    Fail-closed at every step: an empty ``secret`` (none configured), a malformed
    token, a bad or forged MAC, or an expired token (``now`` at or after the token's
    ``expires_at``) all yield ``None`` rather than raising, so a verification check
    is a simple gate. The MAC is compared in constant time
    (:func:`hmac.compare_digest`) so a near-miss forgery leaks no timing signal
    about how many bytes matched (safety). ``now`` is an ISO-8601 UTC string and is
    compared against the token's expiry as parsed :class:`~datetime.datetime`
    instants (:func:`ledger.models.parse_iso`), not lexically -- two differently
    formatted but equivalent ISO-8601 timestamps must expire identically.
    """
    if not secret:
        return None
    parts = token.split(":")
    if len(parts) != _GRANT_TOKEN_PARTS:
        return None
    subject_b64, expires_b64, mac = parts
    try:
        subject = _unb64(subject_b64)
        expires_at = _unb64(expires_b64)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    expected = _grant_mac(subject, expires_at, secret)
    if not hmac.compare_digest(expected, mac):
        return None
    if expires_at:
        try:
            if parse_iso(now) >= parse_iso(expires_at):
                return None
        except ValueError:
            return None
    return subject


# --- revocation list --------------------------------------------------------
#
# A grant token is a stateless bearer capability, so the *only* way to retract one
# before it expires is an explicit deny list of subjects the server consults on
# every request. The format is deliberately trivial — a JSON array of subject
# strings, ``["subject", ...]`` — so a steward can read, diff, or hand-edit it, and
# a missing file means "nothing revoked" (deny by default is about privilege, not
# revocation; the safe default here is simply an empty set — fault tolerance).


def load_revocations(path: Path) -> set[str]:
    """Load the set of revoked subjects from a JSON array file.

    A missing file yields the empty set, so an archive that has never revoked
    anyone needs no revocations file at all (fault tolerance). The file is a plain
    ``["subject", ...]`` array, small enough to read whole on each request.
    """
    if not path.exists():
        return set()
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {str(subject) for subject in raw}


def save_revocations(path: Path, subjects: Iterable[str]) -> None:
    """Persist the revoked-subject set as a sorted JSON array (deterministic)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(sorted(set(subjects)), ensure_ascii=False, indent=2)
    path.write_text(payload + "\n", encoding="utf-8", newline="\n")


def revoke_subject(path: Path, subject: str) -> set[str]:
    """Add ``subject`` to the revocation list and return the updated set.

    Idempotent: revoking an already-revoked subject is a no-op on the set. Any
    live token for the subject stops being honoured the moment this file is
    re-read, without needing to rotate the server secret (immediate retraction).
    """
    revocations = load_revocations(path)
    revocations.add(subject)
    save_revocations(path, revocations)
    return revocations


def unrevoke_subject(path: Path, subject: str) -> set[str]:
    """Remove ``subject`` from the revocation list and return the updated set.

    Idempotent: un-revoking a subject that was not revoked leaves the set
    unchanged. After this the subject's still-unexpired tokens are honoured again.
    """
    revocations = load_revocations(path)
    revocations.discard(subject)
    save_revocations(path, revocations)
    return revocations
