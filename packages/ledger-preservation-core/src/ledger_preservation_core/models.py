"""Shared, typed value objects for the preservation core (analyzability).

Deliberately behaviour-light: it defines *what* a content address, a fixity
result, and a PREMIS event are, so hashing, BagIt packaging, content-addressed
storage, and metadata serialization can each depend on one stable shape
(modularity, orthogonality, interchangeability). This module carries no
application-level concepts (no access policy, no identity, no consent) — those
belong to the application embedding this library, not to the preservation core.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
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


# --- preservation metadata --------------------------------------------------


class PremisEventType(StrEnum):
    """PREMIS event vocabulary (accountability, auditability). Every meaningful
    preservation action is one of these."""

    INGESTION = "ingestion"
    FIXITY_CHECK = "fixity check"
    FORMAT_IDENTIFICATION = "format identification"
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
# is repeatable, so each is a list; empty lists are dropped on serialization.
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
