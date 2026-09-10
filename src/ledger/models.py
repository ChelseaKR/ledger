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
    LOCKDOWN = "lockdown"
    STANDUP = "stand-up"
    QUERY = "query"  # EXP-14 reading-room enclave: an aggregate query, answered or refused
    # --- physical holdings (#188) ------------------------------------------
    # PREMIS leaves the event vocabulary to the repository, and these three are
    # local terms in the same way LOCKDOWN and QUERY above already are. They exist
    # because a physical holding's whole history happens outside ledger: the only
    # thing the archive can honestly record is that somebody *told* it these things
    # happened, and an event log is exactly the right shape for that (ADR 0019).
    #
    # ``CUSTODY_TRANSFER`` — the object moved from one person's keeping to
    # another's. The event records that a transfer was reported; the names of both
    # parties stay in the sealed custody fields and never in the event detail.
    CUSTODY_TRANSFER = "custody transfer"
    # ``CONDITION_CHECK`` — somebody looked at the object and wrote down what state
    # it is in. This is the physical analogue of a fixity check and is deliberately
    # NOT spelled ``fixity check``: a person's eyes are not a digest, and a reader
    # filtering the log by ``fixity check`` must not pick up a human judgement.
    CONDITION_CHECK = "condition check"
    # ``DIGITIZATION`` — a surrogate (a phone photo, a scan of one page) was
    # attached to a physical record. It links the record to the bytes of its
    # derivative, which is what makes the two findable from each other later.
    DIGITIZATION = "digitization"


# PREMIS ``linkingObjectIdentifierType`` values ledger writes (ADR 0012). PREMIS
# leaves identifier types to the repository; these say what kind of thing the
# identifier value names, so a consumer never has to guess from its shape.
#
# * ``ledger-payload`` — ``<record_id>/<filename>``: one *File* inside one record
#   (the record is the PREMIS Representation). This is ledger's PREMIS Object for
#   format identification and fixity: identification is a function of the bytes
#   *and the filename*, so it is a fact about the payload, not about the bytes.
# * ``ledger-record`` — a record id: the Representation an ingest or a consent
#   change is about.
# * ``content-address`` — ``<algo>:<hex>``: the bytes themselves, wherever they sit.
#   The content store deduplicates, so two payloads may share one address; an
#   event carries the address as a *second* link (what was examined), never as the
#   object's identity — that conflation is what let one address carry two
#   contradictory verdicts (#149).
# * ``ledger-bag`` — a bag directory name: the on-disk container a replication or
#   quarantine event is about. A bag name equals its record id today, but the two
#   are not the same *kind* of thing: one names a storage container a replica holds,
#   the other names the Representation. An event that quarantines a bag is not an
#   event about the record's content, and a consumer must be able to tell.
# * ``ledger-proposal`` — a dual-control proposal id: the authorization decision a
#   reading-room query event is about, not the records the query touched.
OBJECT_TYPE_PAYLOAD = "ledger-payload"
OBJECT_TYPE_RECORD = "ledger-record"
OBJECT_TYPE_CONTENT_ADDRESS = "content-address"
OBJECT_TYPE_BAG = "ledger-bag"
OBJECT_TYPE_PROPOSAL = "ledger-proposal"

#: Every identifier type ledger writes. A writer must pick one of these or leave the
#: type unset; nothing infers a type from an identifier's shape except the
#: content-address case in :attr:`PremisEvent.object_identifier_type`, where the
#: parse is unambiguous.
OBJECT_TYPES = frozenset(
    {
        OBJECT_TYPE_PAYLOAD,
        OBJECT_TYPE_RECORD,
        OBJECT_TYPE_CONTENT_ADDRESS,
        OBJECT_TYPE_BAG,
        OBJECT_TYPE_PROPOSAL,
    }
)


def payload_object_id(record_id: str, filename: str) -> str:
    """The PREMIS object identifier of one payload file within one record.

    ``<record_id>/<filename>``. A record id is a single allow-listed path component
    (letters, digits, ``_``, ``-``), so the first ``/`` always separates the record
    from the bag-relative filename even when the filename itself has directories in
    it. Refused, never silently mangled, if the record id could make that split
    ambiguous (fail closed).
    """
    if not record_id or "/" in record_id:
        raise ValueError(f"record id cannot form a payload object identifier: {record_id!r}")
    return f"{record_id}/{filename}"


@dataclass(frozen=True)
class PremisEvent:
    """A single auditable event with its agent and outcome (provability).

    ``linked_object`` is the PREMIS ``linkingObjectIdentifier`` value — the object
    the event is about — and ``linked_object_type`` says what kind of identifier it
    is (one of the ``OBJECT_TYPE_*`` values; ``None`` on events written before ADR
    0012 and on events whose writers have not been typed yet, see
    :attr:`object_identifier_type`). ``linked_content_address`` is a second link,
    to the bytes the event examined; it is what lets a fixity or identification
    event stay bound to the exact bytes it was about even if the record it lives in
    is later revised, without making the address the object's *identity*.

    Both new fields are omitted from :meth:`to_dict` when unset, so an event written
    before they existed serialises — and therefore hash-chains — byte-for-byte as it
    always did (chain stability is the reason; see :mod:`ledger.chain`).
    """

    event_type: PremisEventType
    agent: str
    # "success" | "failure", plus the format-identification outcomes "at-risk",
    # "unidentified", and "empty" (ADR 0010), and whatever a specific writer documents.
    outcome: str
    detail: str = ""
    linked_object: str | None = None  # payload id, content address, record id, or bag id
    event_datetime: str = field(default_factory=now_iso)
    linked_object_type: str | None = None
    linked_content_address: str | None = None

    @property
    def object_identifier_type(self) -> str | None:
        """The identifier type of :attr:`linked_object`, explicit or inferred.

        Explicit when the writer said so (ADR 0012). Otherwise inferred only where
        the inference is safe: a value that parses as a :class:`ContentAddress` is
        one, which is how every fixity-check and format-identification event written
        before ADR 0012 is keyed. Anything else stays ``None`` — a record id, a bag
        name, and a proposal id all look alike, and guessing among them would be the
        same defect this field exists to prevent.
        """
        if self.linked_object_type is not None:
            return self.linked_object_type
        if self.linked_object is None:
            return None
        try:
            ContentAddress.parse(self.linked_object)
        except ValueError:
            return None
        return OBJECT_TYPE_CONTENT_ADDRESS

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
        if self.linked_object_type is not None:
            d["linkingObjectIdentifierType"] = self.linked_object_type
        if self.linked_content_address is not None:
            d["linkingObjectContentAddress"] = self.linked_content_address
        return d


@dataclass(frozen=True)
class PremisRights:
    """A PREMIS v3 Rights statement: the terms under which content may be used.

    A rights statement says *what may be done* with an object and *under what
    authority*, so a downstream reader (or a partner repository) knows whether it
    may disseminate or replicate a record without having to re-negotiate terms
    (interoperability, standards-compliance; PREMIS v3 rightsStatement).

    The shape is deliberately minimal and opaque, mirroring
    :class:`PremisEvent`:

    * ``rights_basis`` — the PREMIS ``rightsBasis`` vocabulary value
      (``"license"``, ``"statute"``, ``"copyright"``, ``"other"``, …).
    * ``rights_note`` — a free-text ``rightsBasis``/``copyrightNote`` style
      description of the terms (e.g. a licence name), never an identity.
    * ``granted_acts`` — the PREMIS ``act`` values that ARE permitted
      (``"disseminate"``, ``"replicate"``, ``"migrate"``, …).
    * ``restrictions`` — the PREMIS ``restriction`` values that constrain each
      granted act (e.g. ``"attribution required"``, ``"no commercial use"``).
    * ``linked_object`` — an opaque record/content id the statement is about,
      exactly like :attr:`PremisEvent.linked_object`.

    No-outing rule: a rights statement carries no contributor identity and no
    ``rightsHolder`` name — the terms are those of the *collection/community*, and
    any real person stays only in the encrypted vault. This is why there is no
    rights-holder or agent field here.
    """

    rights_basis: str
    rights_note: str = ""
    granted_acts: tuple[str, ...] = ()
    restrictions: tuple[str, ...] = ()
    linked_object: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Serialize to the canonical on-disk mapping (empty parts dropped).

        Mirrors :meth:`PremisEvent.to_dict`: only populated members are emitted so
        the sidecar stays compact and a rights statement round-trips exactly.
        """
        d: dict[str, object] = {"rightsBasis": self.rights_basis}
        if self.rights_note:
            d["rightsNote"] = self.rights_note
        if self.granted_acts:
            d["grantedActs"] = list(self.granted_acts)
        if self.restrictions:
            d["restrictions"] = list(self.restrictions)
        if self.linked_object is not None:
            d["linkingObjectIdentifier"] = self.linked_object
        return d

    def canonical_json(self) -> str:
        """Deterministic JSON for hashing/round-trip, like the rest of the metadata."""
        return canonical_json(self.to_dict())


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


# --- physical holdings (a catalogue entry for something not digitized) ------
#
# The README's shoebox under someone's bed is full of zines, flyers, buttons,
# photographs and cassettes, and ledger can only *preserve* them once they are
# digitized. What a community needs first is a catalogue of what exists — and a
# catalogue entry is a record whose payload is absent **by declaration**, which is
# a different fact from a payload that was lost, never written, or could not be
# read. Every surface in this package that answers "is this safe?" has to be able
# to tell those apart, which is why the kind is a declared field and never
# inferred from an empty payload list (#188).


class HoldingKind(StrEnum):
    """Whether a record's content is digital, physical, or physical with a scan.

    Declared, never derived. An empty ``payloads`` list is not evidence that a
    record describes a physical object: it is equally what a failed ingest, a
    withdrawn payload, or a record built by a caller that has not attached files
    yet looks like. Deriving the kind from that absence is the same defect this
    field exists to prevent — an archive stating something it did not read.

    The three values, and the question each one answers differently:

    * :data:`DIGITAL` — ledger holds the bytes. Fixity is a real question with a
      real answer.
    * :data:`PHYSICAL` — ledger holds a *description* of an object somebody else
      is keeping. There are no content bytes, so content fixity is
      :data:`~ledger.fixity.FixityStatus.NOT_APPLICABLE`: not a pass, not a
      failure, and not "could not verify" either, because nothing was expected.
    * :data:`PHYSICAL_WITH_SURROGATE` — the object is still physical, and a phone
      photo or a scan of one page has been attached. The surrogate is ordinary
      digital content with ordinary fixity; the *object* is still unverifiable by
      any digital means, and a verified surrogate must never be read as a verified
      holding.
    """

    DIGITAL = "digital"
    PHYSICAL = "physical"
    PHYSICAL_WITH_SURROGATE = "physical_with_surrogate"

    @property
    def is_physical(self) -> bool:
        """True for a record describing an object ledger does not hold the bytes of."""
        return self in (HoldingKind.PHYSICAL, HoldingKind.PHYSICAL_WITH_SURROGATE)


class PhysicalFormat(StrEnum):
    """A controlled vocabulary for what the physical thing *is*.

    Controlled rather than free text so browse can facet on it, a print edition can
    group by it, and two volunteers cataloguing the same box cannot produce
    ``cassette``/``Cassette tape``/``audio cassette`` as three formats. The list is
    drawn from what community and movement collections actually hold (the README's
    shoebox), not from a library-supply catalogue.

    :data:`OTHER` exists so a volunteer is never blocked by the vocabulary, and it
    is deliberately the honest answer rather than a near-miss: an object filed as
    ``OTHER`` with an ``extent`` note is more useful than one mis-filed as
    ``EPHEMERA`` because that was the closest word on the list.
    """

    ZINE = "zine"
    FLYER = "flyer"
    POSTER = "poster"
    BUTTON = "button"
    PHOTOGRAPH = "photograph"
    NEGATIVE = "negative"
    SLIDE = "slide"
    AUDIO_CASSETTE = "audio-cassette"
    VIDEO_CASSETTE = "video-cassette"
    FILM_REEL = "film-reel"
    VINYL_RECORD = "vinyl-record"
    CORRESPONDENCE = "correspondence"
    NOTEBOOK = "notebook"
    PERIODICAL = "periodical"
    BOOK = "book"
    BANNER = "banner"
    TEXTILE = "textile"
    ARTWORK = "artwork"
    EPHEMERA = "ephemera"
    OTHER = "other"


class CustodyState(StrEnum):
    """What a read path may say about custody, in three states.

    Custody — where the object is and who is keeping it — is the most exposed
    datum in a physical holding: the custodian is often the person whose apartment
    holds the box. It is therefore carried as ordinary sealed
    :class:`Field` values that go through the one disclosure decision point
    (:func:`ledger.access.policy.is_visible`), never as a second channel beside it.

    What a viewer sees *about* custody is this word, and it has three values for
    the reason the rest of this codebase has three-state verdicts:

    * :data:`NOT_RECORDED` — nobody wrote down who is holding it. A real gap in the
      catalogue and the thing a steward most needs to see.
    * :data:`WITHHELD` — it was recorded and this viewer may not see it.
    * :data:`DISCLOSED` — this viewer may see it, and the values are in the
      record's disclosed fields.

    Collapsing the first two — always saying "withheld" — would publish "somebody
    is looking after this" over a record where nobody is, which is this project's
    named defect (absence rendered as a value) pointed at the one number a steward
    reads to decide whether a collection is safe. Collapsing them the other way
    would out the gap only when it is absent. So all three are rendered, to every
    viewer, and the *values* are gated. The state word says whether a fact was
    recorded; it never says what the fact is.
    """

    NOT_RECORDED = "not-recorded"
    WITHHELD = "withheld"
    DISCLOSED = "disclosed"


#: The reserved :class:`Field` names a custody block occupies. Custody deliberately
#: does **not** get a structured block of its own on :class:`Record`: a second place
#: that holds protected values is a second place disclosure can go wrong, and this
#: package's whole safety argument is that there is exactly one. Carried as fields,
#: custody inherits per-value policies, at-rest encryption for an absolute
#: ``SEALED``, the redaction verb, the withheld-reason vocabulary, and every
#: existing no-outing sentinel, with no new code on the read path.
CUSTODY_LOCATION_FIELD = "custody.location"
CUSTODY_CUSTODIAN_FIELD = "custody.custodian"
CUSTODY_FIELDS: tuple[str, ...] = (CUSTODY_LOCATION_FIELD, CUSTODY_CUSTODIAN_FIELD)


@dataclass(frozen=True)
class PhysicalHolding:
    """The description of an object ledger does **not** hold the bytes of.

    Descriptive, not protected: ``format``, ``extent`` and ``condition`` are what a
    community member needs in order to find out that the thing exists and what
    state it is in, and they are disclosed with the record's other collection-level
    description (the same rule :class:`DublinCore` follows). The protected half —
    where it is and who has it — is **not** here; it lives in the sealed
    :data:`CUSTODY_FIELDS`, so it cannot be disclosed without going through
    :func:`ledger.access.policy.is_visible`.

    ``extent`` is the archivist's word for how much there is ("1 box, ~380
    flyers", "12 cassettes"). ``condition`` is free text because condition
    reporting is a judgement a volunteer writes in their own words ("water damage
    along the spine"), and a controlled vocabulary here would push them into
    picking a wrong word rather than describing what they see.
    """

    format: PhysicalFormat = PhysicalFormat.OTHER
    extent: str = ""
    condition: str = ""

    def to_dict(self) -> dict[str, str]:
        """Serialize, dropping empty parts (compact, deterministic)."""
        out: dict[str, str] = {"format": self.format.value}
        if self.extent:
            out["extent"] = self.extent
        if self.condition:
            out["condition"] = self.condition
        return out

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> PhysicalHolding:
        """Rebuild from :meth:`to_dict` output.

        An unknown ``format`` degrades to :data:`PhysicalFormat.OTHER` rather than
        raising: a manifest written by a newer ledger whose vocabulary has grown
        must still open here, and "other" is the honest reading of a word this
        build does not know (robustness; the same fallback discipline as
        :meth:`DublinCore.from_dict`).
        """
        raw_format = str(data.get("format", PhysicalFormat.OTHER.value))
        try:
            fmt = PhysicalFormat(raw_format)
        except ValueError:
            fmt = PhysicalFormat.OTHER
        return cls(
            format=fmt,
            extent=str(data.get("extent", "")),
            condition=str(data.get("condition", "")),
        )


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


def custody_fields(
    *,
    location: str = "",
    custodian: str = "",
    policy: AccessPolicy = AccessPolicy.SEALED_UNTIL,
    unseal_at: str | None = None,
    unseal_condition: str | None = None,
) -> list[Field]:
    """Build the sealed :class:`Field` pair that carries a custody block.

    Default policy is :data:`AccessPolicy.SEALED_UNTIL` with **no** date — the same
    default every other :class:`Field` gets, and the narrowest level that still
    lets a steward run the archive. An empty ``location``/``custodian`` produces no
    field at all rather than a field holding ``""``: "not recorded" must stay
    distinguishable from "recorded as nothing" (:class:`CustodyState`).
    """
    values = ((CUSTODY_LOCATION_FIELD, location), (CUSTODY_CUSTODIAN_FIELD, custodian))
    return [
        Field(
            name=name,
            value=value,
            policy=policy,
            unseal_at=unseal_at,
            unseal_condition=unseal_condition,
        )
        for name, value in values
        if value
    ]


@dataclass(frozen=True)
class TranscriptCue:
    """One timed segment of a caption/transcript track (a WebVTT cue or SRT block).

    RM6: captions/transcripts as a first-class *ingest* step for an already
    -transcribed WebVTT or SRT file a contributor or steward uploads — ledger does
    no speech-to-text of its own. A cue is the atomic unit both formats share: a
    start instant, an end instant, and the text spoken in that span.

    ``start``/``end`` are normalized to WebVTT's own timestamp grammar,
    ``[hh:]mm:ss.mmm`` (zero-padded, dot-separated milliseconds — see
    :mod:`ledger.captions`), whether the cue was parsed from a WebVTT file (which
    uses this form natively) or an SRT file (whose ``,`` millisecond separator is
    converted at parse time). One shape regardless of source format, so a reader
    or an export never has to branch on which file ingested the cue.

    ``speaker`` carries a voice label when the source format actually names one:
    WebVTT's ``<v Speaker Name>`` voice span. SRT has no standardized speaker
    syntax, so an SRT-derived cue always carries ``speaker=None`` rather than a
    guessed value (honesty over a fabricated convention).

    Immutable, like every other model here. A cue's disclosure is NOT decided
    per-cue: it travels with, and is gated by, the single policy on the
    :class:`PayloadFile` that carries it (the same rule that already governs the
    flat ``transcript`` field). Whether a future version should support a finer,
    per-cue disclosure policy — mirroring the per-segment ``Field`` policy the
    oral-history session kit (EXP-09, unmerged) uses for a facilitator's
    hand-marked session segments — is an open product/consent-design question this
    module deliberately does not answer; see the RM6 implementation notes.
    """

    start: str
    end: str
    text: str
    speaker: str | None = None


@dataclass
class PayloadFile:
    """A file inside the bag, addressed by content, carrying its own policy.

    ``transcript`` is a first-class caption/transcript for audio or video so the
    content is available to a Deaf or hard-of-hearing reader, and to anyone on a slow
    or silent connection (user research H3). It is plain descriptive text — never a
    warning conveyed only in audio — and is disclosed under the same policy as the
    payload it describes.

    ``cues`` is the RM6 extension: the same transcript, additionally carrying real
    segment/timing structure when it was ingested from a WebVTT or SRT caption
    file (:mod:`ledger.captions`) rather than typed as a single block of plain
    text. It is empty whenever no structured captions were supplied — ``transcript``
    alone remains fully supported and is what every existing reader/exporter keeps
    using. When captions are the only transcript source, ``transcript`` is flattened
    from ``cues``; an explicitly supplied flat transcript is retained as a distinct,
    potentially fuller human-authored alternative. ``cues`` is disclosed under the
    *same* payload policy as
    everything else here — see :class:`TranscriptCue` for why no finer-grained,
    per-cue policy is implemented yet.

    ``media_type_basis`` records where ``media_type`` came from, so the record can
    never assert more confidence than the pipeline actually had. It is the
    :attr:`~ledger.preservation.FormatId.basis` of the identification that produced
    the type (``signature`` — matched on content, the strongest; ``extension`` —
    inferred by the format registry from the filename; ``text``,
    ``xml-declaration``, ``signature-offset``, ``empty``; or ``unknown``, where the
    type is the honest ``application/octet-stream``), or ``declared`` when a steward
    supplied the type themselves. Empty on records written before this field existed,
    which is why nothing may read it as "verified" by default (ADR 0010).
    """

    filename: str
    address: ContentAddress
    media_type: str = "application/octet-stream"
    media_type_basis: str = ""
    size_bytes: int = 0
    policy: AccessPolicy = AccessPolicy.SEALED_UNTIL
    transcript: str = ""
    cues: tuple[TranscriptCue, ...] = ()


@dataclass
class Record:
    """The descriptive + access manifest for one archived item.

    Distinct from the bag payload (the bytes). The record says what the item is
    and who may see which part of it. It carries NO identity — only an opaque
    `identity_ref` resolvable solely through the vault.

    ``holding_kind`` and ``physical`` describe an item ledger does not hold the
    bytes of (#188). Both default to the digital case, and both are **omitted from
    the serialized manifest when they are at their defaults**, so a record written
    before this field existed round-trips byte-for-byte and its hash chain is
    undisturbed — the same rule :meth:`PremisEvent.to_dict` already follows. That
    absence *is* the migration: a manifest with no ``holding_kind`` reads as
    :data:`HoldingKind.DIGITAL`, which is what every record written before #188
    is.
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
    holding_kind: HoldingKind = HoldingKind.DIGITAL
    physical: PhysicalHolding | None = None

    def field_named(self, name: str) -> Field | None:
        for f in self.fields:
            if f.name == name:
                return f
        return None

    def has_custody(self) -> bool:
        """Whether custody was recorded on this record at all.

        The *storage-side* question, answered before any grant is considered: a
        record either carries a non-empty custody field or it does not. The
        read-side question — may this viewer see the values — is answered by
        :attr:`DisclosedRecord.custody_state`, which takes a different input and
        must not be confused with this one.
        """
        return any(f.name in CUSTODY_FIELDS and f.value for f in self.fields)


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
    # #188. Defaulted so every existing construction site keeps compiling and keeps
    # meaning what it meant: a record that says nothing about a holding is digital.
    holding_kind: HoldingKind = HoldingKind.DIGITAL
    physical: PhysicalHolding | None = None

    @property
    def redactions(self) -> tuple[str, ...]:
        """The names of withheld fields/payloads (compatibility accessor)."""
        return tuple(r.name for r in self.withheld)

    @property
    def custody_state(self) -> CustodyState:
        """What this viewer may be told about custody, in three states.

        Derived from what disclosure actually did, never from a flag a caller could
        set: :data:`CustodyState.DISCLOSED` when a custody field survived into
        :attr:`fields`, :data:`CustodyState.WITHHELD` when one was withheld, and
        :data:`CustodyState.NOT_RECORDED` when the record carries none at all.

        Deriving it here rather than passing it in is what makes it impossible for
        this word to disagree with the values beside it — a read path cannot render
        "withheld" over a disclosed custodian or vice versa, because both come from
        the same projection.
        """
        if any(name in CUSTODY_FIELDS for name in self.fields):
            return CustodyState.DISCLOSED
        if any(r.name in CUSTODY_FIELDS for r in self.withheld):
            return CustodyState.WITHHELD
        return CustodyState.NOT_RECORDED

    def to_dict(self, *, withheld_reasons: bool = True) -> dict[str, object]:
        """Serialize for an API response.

        `withheld_reasons=False` emits only a count of withheld parts, not their
        names or reasons — the form a read path serves to an *outsider* so the
        redaction set cannot be scraped as targeting metadata (P2-2). With reasons,
        each withheld part is named for a legitimate viewer (honesty, P1-3).

        ``holding_kind`` and ``custody_state`` are emitted on **every** response,
        including the outsider's. Both are facts about what the archive knows, not
        about who anybody is: the first is already visible as the browse badge, and
        the second says whether a custodian was recorded without saying who. Hiding
        the second would publish "somebody is looking after this" over a record
        where nobody is (#188). ``physical`` is emitted only for a physical holding,
        where it is the description that makes the object findable.
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
                    # Disclosed alongside the type, never separately: a consumer that
                    # sees `application/pdf` is entitled to know whether the bytes
                    # said so or the filename did (ADR 0010).
                    "media_type_basis": p.media_type_basis,
                    "size_bytes": p.size_bytes,
                }
                for p in self.payloads
            ],
            "content_warnings": list(self.content_warnings),
            "holding_kind": self.holding_kind.value,
            "custody_state": self.custody_state.value,
        }
        if self.physical is not None:
            out["physical"] = self.physical.to_dict()
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
