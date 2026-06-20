"""Shared, typed value objects — the contract every layer agrees on.

This module is deliberately behaviour-free: it defines *what* a record, a policy, a
grant, a fixity result, and a preservation event are, so that ingest, storage,
disclosure, identity, replication, and the browse server can each depend on one
stable shape (modularity, orthogonality, interchangeability).

The single most important invariant lives here in the type system:

    A `Record` never contains a contributor's identity. It carries at most an
    opaque `identity_ref` — a random token whose mapping to a real person exists
    only inside the encrypted vault (`ledger.identity`). A `DisclosedRecord`, the
    only shape a read path may emit, carries neither identity nor `identity_ref`.

If you are tempted to add a `contributor_name` field to `Record`, stop: that is the
exact coupling this design forbids. Identity flows through `ledger.identity` under
an explicit grant, never through the record.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum

# --- time -------------------------------------------------------------------
# Timeliness/traceability: every event is stamped in UTC ISO-8601. Determinism:
# callers that need reproducible output (golden bags, tests) pass an explicit
# timestamp rather than relying on the wall clock.


def now_iso() -> str:
    """Current instant as a UTC ISO-8601 string with a trailing ``Z``."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 timestamp (accepts a trailing ``Z``) to an aware datetime."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def canonical_json(obj: object) -> str:
    """Deterministic JSON: sorted keys, compact, UTF-8 safe.

    Reproducibility: identical input yields a byte-identical string, so metadata
    sidecars and audit records hash the same on every machine and every run.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# --- hashing & addressing ---------------------------------------------------


class HashAlgo(StrEnum):
    """Fixity algorithms. SHA-256 is the addressing algorithm; BLAKE2b is the
    independent second manifest, so a single weakened algorithm cannot hide
    tampering (integrity, redundancy)."""

    SHA256 = "sha256"
    BLAKE2B = "blake2b"


@dataclass(frozen=True)
class ContentAddress:
    """A name derived from content. A changed byte is a different address, so
    drift is detectable rather than silent (integrity, inspectability)."""

    algo: HashAlgo
    digest: str

    def __str__(self) -> str:
        return f"{self.algo.value}:{self.digest}"

    @classmethod
    def parse(cls, value: str) -> ContentAddress:
        algo, _, digest = value.partition(":")
        if not digest:
            raise ValueError(f"not a content address: {value!r}")
        return cls(HashAlgo(algo), digest)


@dataclass(frozen=True)
class FixityResult:
    """The outcome of comparing a stored object to its manifest entry."""

    path: str
    algo: HashAlgo
    expected: str
    actual: str

    @property
    def ok(self) -> bool:
        return self.expected == self.actual


# --- access policy ----------------------------------------------------------


class AccessPolicy(StrEnum):
    """The small, documented set of disclosure levels.

    Predictability/determinability: the same (viewer, grant, policy, instant)
    always resolves to the same decision. New records and fields default to the
    *narrowest* level that still lets the record exist (``SEALED_UNTIL`` with no
    unseal date == sealed indefinitely).
    """

    PUBLIC = "public"
    COMMUNITY = "community"
    STEWARDS = "stewards"
    SEALED_UNTIL = "sealed-until"
    SEALED_CONDITIONAL = "sealed-conditional"
    # An ABSOLUTE seal: restricted from everyone on every read path, including
    # stewards. There is no grant that satisfies it. Used for content a contributor
    # needs kept from even the people who run the archive; such values are encrypted
    # at rest at ingest rather than left as clear text in the manifest
    # (user research C8 / P2-4 — the "seal from everyone, including stewards" tier).
    SEALED = "sealed"

    @property
    def is_sealed(self) -> bool:
        return self in (
            AccessPolicy.SEALED_UNTIL,
            AccessPolicy.SEALED_CONDITIONAL,
            AccessPolicy.SEALED,
        )


# --- preservation metadata --------------------------------------------------


class PremisEventType(StrEnum):
    """PREMIS event vocabulary used across the archive (accountability,
    auditability). Every meaningful action is one of these."""

    INGESTION = "ingestion"
    FIXITY_CHECK = "fixity check"
    REPLICATION = "replication"
    REDACTION = "redaction"
    POLICY_CHANGE = "access-policy change"
    CONSENT_CHANGE = "consent change"
    CORRECTION = "correction"
    TAKEDOWN = "deletion"
    QUARANTINE = "quarantine"
    VALIDATION = "validation"
    MODERATION = "moderation"
    REKEY = "key rotation"


@dataclass(frozen=True)
class PremisEvent:
    """A single auditable event with its agent and outcome (provability)."""

    event_type: PremisEventType
    agent: str
    outcome: str  # "success" | "failure"
    detail: str = ""
    linked_object: str | None = None  # content address, record id, or bag id
    event_datetime: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, str]:
        d = {
            "eventType": self.event_type.value,
            "eventDateTime": self.event_datetime,
            "linkingAgentIdentifier": self.agent,
            "eventOutcome": self.outcome,
            "eventDetail": self.detail,
        }
        if self.linked_object is not None:
            d["linkingObjectIdentifier"] = self.linked_object
        return d


# The fifteen Dublin Core Metadata Element Set elements (ISO 15836). Every element
# is repeatable, so each is a list; empty lists are dropped on serialization. None
# of these elements is permitted to carry contributor-identifying free text — the
# `creator` of an archived record is the *community/collection*, not the (possibly
# closeted) person who contributed it. Identity lives only in the vault.
DC_ELEMENTS: tuple[str, ...] = (
    "title",
    "creator",
    "subject",
    "description",
    "publisher",
    "contributor",
    "date",
    "type",
    "format",
    "identifier",
    "source",
    "language",
    "relation",
    "coverage",
    "rights",
)


@dataclass
class DublinCore:
    title: list[str] = field(default_factory=list)
    creator: list[str] = field(default_factory=list)
    subject: list[str] = field(default_factory=list)
    description: list[str] = field(default_factory=list)
    publisher: list[str] = field(default_factory=list)
    contributor: list[str] = field(default_factory=list)
    date: list[str] = field(default_factory=list)
    type: list[str] = field(default_factory=list)
    format: list[str] = field(default_factory=list)
    identifier: list[str] = field(default_factory=list)
    source: list[str] = field(default_factory=list)
    language: list[str] = field(default_factory=list)
    relation: list[str] = field(default_factory=list)
    coverage: list[str] = field(default_factory=list)
    rights: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, list[str]]:
        """Drop empty elements for compact, deterministic serialization."""
        out: dict[str, list[str]] = {}
        for name in DC_ELEMENTS:
            values = getattr(self, name)
            if values:
                out[name] = list(values)
        return out

    @classmethod
    def from_dict(cls, data: dict[str, list[str]]) -> DublinCore:
        known = {k: list(v) for k, v in data.items() if k in DC_ELEMENTS}
        return cls(**known)


# --- record (description + access + payload manifest) -----------------------


@dataclass
class Field:
    """A structured descriptive field with its own disclosure policy.

    Selective disclosure: a single record can publish ``story`` while sealing
    ``names`` and ``location`` (autonomy — the contributor decides per field)."""

    name: str
    value: str
    policy: AccessPolicy = AccessPolicy.SEALED_UNTIL
    unseal_at: str | None = None
    unseal_condition: str | None = None


@dataclass
class PayloadFile:
    """A file inside the bag, addressed by content, carrying its own policy.

    ``transcript`` is a first-class caption/transcript for audio or video so the
    content is available to a Deaf or hard-of-hearing reader, and to anyone on a slow
    or silent connection (user research H3). It is plain descriptive text — never a
    warning conveyed only in audio — and is disclosed under the same policy as the
    payload it describes.
    """

    filename: str
    address: ContentAddress
    media_type: str = "application/octet-stream"
    size_bytes: int = 0
    policy: AccessPolicy = AccessPolicy.SEALED_UNTIL
    transcript: str = ""


@dataclass
class Record:
    """The descriptive + access manifest for one archived item.

    Distinct from the bag payload (the bytes). The record says what the item is
    and who may see which part of it. It carries NO identity — only an opaque
    `identity_ref` resolvable solely through the vault.
    """

    title: str
    record_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    default_policy: AccessPolicy = AccessPolicy.SEALED_UNTIL
    dublin_core: DublinCore = field(default_factory=DublinCore)
    fields: list[Field] = field(default_factory=list)
    payloads: list[PayloadFile] = field(default_factory=list)
    content_warnings: list[str] = field(default_factory=list)
    identity_ref: str | None = None  # opaque token into the vault; NEVER an identity
    created_at: str = field(default_factory=now_iso)

    def field_named(self, name: str) -> Field | None:
        for f in self.fields:
            if f.name == name:
                return f
        return None


@dataclass(frozen=True)
class Redaction:
    """One withheld field or payload, with a SAFE reason for the withholding.

    `reason` is a human label derived from the policy (e.g. "community members
    only", "sealed until 2030-01-01") — never the protected value. Surfacing the
    reason to a *legitimate* viewer is honesty (user research T5/P1-3); a read path
    serving an outsider should generalize it so the set of reasons cannot become
    targeting metadata (user research T12/P2-2). `category` is the raw policy value
    so a UI can style it; it carries no value either.
    """

    name: str
    reason: str
    category: str


@dataclass(frozen=True)
class DisclosedRecord:
    """The ONLY record shape a read path (browse, search, API, export) may emit.

    It contains only the fields and payloads a given grant is allowed to see at a
    given instant, and it structurally cannot carry identity: there is no
    `identity_ref` here. `ledger.access.disclose` is the sole constructor used by
    read paths; building one any other way bypasses the safety boundary.
    """

    record_id: str
    title: str
    dublin_core: dict[str, list[str]]
    fields: dict[str, str]
    payloads: tuple[PayloadFile, ...]
    content_warnings: tuple[str, ...]
    withheld: tuple[Redaction, ...]  # fields/payloads withheld, each with a safe reason

    @property
    def redactions(self) -> tuple[str, ...]:
        """The names of withheld fields/payloads (compatibility accessor)."""
        return tuple(r.name for r in self.withheld)

    def to_dict(self, *, withheld_reasons: bool = True) -> dict[str, object]:
        """Serialize for an API response.

        `withheld_reasons=False` emits only a count of withheld parts, not their
        names or reasons — the form a read path serves to an *outsider* so the
        redaction set cannot be scraped as targeting metadata (P2-2). With reasons,
        each withheld part is named for a legitimate viewer (honesty, P1-3).
        """
        out: dict[str, object] = {
            "record_id": self.record_id,
            "title": self.title,
            "dublin_core": {k: list(v) for k, v in self.dublin_core.items()},
            "fields": dict(self.fields),
            "payloads": [
                {
                    "filename": p.filename,
                    "address": str(p.address),
                    "media_type": p.media_type,
                    "size_bytes": p.size_bytes,
                }
                for p in self.payloads
            ],
            "content_warnings": list(self.content_warnings),
        }
        if withheld_reasons:
            out["withheld"] = [
                {"name": r.name, "reason": r.reason, "category": r.category} for r in self.withheld
            ]
        else:
            out["withheld_count"] = len(self.withheld)
        return out


# --- grants & viewers -------------------------------------------------------


@dataclass(frozen=True)
class Grant:
    """What one viewer is permitted to see.

    `levels` is the set of access levels this viewer satisfies. `identity_unseal`
    is the set of `identity_ref` tokens this grant may resolve to a real identity
    — empty for almost everyone, including most stewards (least privilege).
    """

    subject: str
    levels: frozenset[AccessPolicy] = frozenset({AccessPolicy.PUBLIC})
    is_steward: bool = False
    identity_unseal: frozenset[str] = frozenset()
    expires_at: str | None = None

    def is_expired(self, now: str) -> bool:
        """Whether this grant has expired at instant ``now``.

        Fails CLOSED: a grant with no expiry never expires, but a grant whose
        ``expires_at`` (or the supplied ``now``) is malformed is treated as
        *expired* rather than crashing the disclosure decision. A corrupt
        timestamp must downgrade a credential to the public grant, never widen
        access (safety, robustness; mirrors access.policy._unseal_reached).
        """
        if self.expires_at is None:
            return False
        try:
            return parse_iso(now) >= parse_iso(self.expires_at)
        except (ValueError, TypeError):
            return True


# The anonymous public: sees only what is `PUBLIC` and unsealed. This is the grant
# a read path uses when no one has authenticated (default to narrowest).
PUBLIC_GRANT = Grant(subject="anonymous", levels=frozenset({AccessPolicy.PUBLIC}))


def with_redaction(record: Record, field_name: str) -> Record:
    """Return a copy of `record` with one field's value redacted in place.

    A convenience for the redaction transform; the caller records the PREMIS
    event. The original (unredacted) record stays access-controlled elsewhere.
    """
    new_fields = [
        replace(f, value="[redacted]") if f.name == field_name else f for f in record.fields
    ]
    return replace(record, fields=new_fields)
