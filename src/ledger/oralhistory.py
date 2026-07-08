"""EXP-09 — the oral-history session kit: a session manifest and its ingest mapping.

RM6 makes a transcript first-class *after the fact*. This module shapes consent
*during capture* instead — where an elder narrator (user research A3) actually
negotiates what is public, what is community-only, and what stays sealed for
twenty years. No recording software is built here: the kit wraps whatever
recorder a community already has. What it provides is:

* :class:`SessionSegment` / :class:`SessionManifest` — a small, typed session
  manifest format: the segments a facilitator marked during the recording, each
  with its own disclosure policy and the timestamp at which the narrator's spoken
  consent for *that* policy was captured.
* :func:`parse_session_manifest` / :func:`session_manifest_to_json` — the JSON
  (de)serialization for that manifest, so a facilitator's tablet, laptop, or a
  steward's later transcription pass can all produce/consume the same file.
* :func:`validate_session_manifest` — the enforcement of the kit's one hard rule:
  a segment may only carry a policy that ever discloses something to someone if
  the manifest also carries a spoken-consent timestamp for it. A segment destined
  for ``public``/``community``/``stewards`` view, a date-bound
  ``sealed-until``, or a ``sealed-conditional`` release with no recorded consent
  moment is a manifest error, not a silent gap (autonomy, no-outing-adjacent
  safety: disclosure must trace to a moment the narrator agreed to it).
* :func:`apply_session_manifest` — maps a validated manifest onto a
  :class:`~ledger.models.Record`: one descriptive :class:`~ledger.models.Field`
  per segment carrying the segment's own policy, one companion stewards-only
  audit field recording *when* consent for that policy was spoken (so the policy
  is provably traceable to the spoken moment — the ideation "Excellent" bar), and
  — for a segment that names a distinct payload file (e.g. a clipped audio
  segment) — a pre-declared :class:`~ledger.models.PayloadFile` entry carrying
  that same policy, ready for the one ingest path
  (:func:`ledger.ingest.ingest_sip`) to hash and store.

Like every other part of ledger, this module carries no contributor identity: a
manifest's ``narrator_ref`` and ``facilitator`` fields are opaque labels/roles,
never names, and are never asserted to be identity-free by scanning (that
defense lives in :mod:`ledger.ingest`, applied to the record this module
produces, same as any other ingest).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from ledger.errors import LedgerError
from ledger.models import AccessPolicy, ContentAddress, Field, HashAlgo, PayloadFile, Record

__all__ = [
    "SessionManifest",
    "SessionSegment",
    "apply_session_manifest",
    "parse_session_manifest",
    "session_manifest_to_json",
    "validate_session_manifest",
]

# A pre-declared payload's content address before the real bytes are hashed by the
# ingest path. Mirrors the placeholder ``ledger.cli`` already uses for a
# pre-declared transcript payload: :func:`ledger.ingest.ingest_sip` recomputes the
# real address, size, and media type from the actual bytes at ingest time, so this
# value is never trusted or disclosed — it exists only to carry the policy and
# transcript through to the one ingest path.
_PLACEHOLDER_ADDRESS = ContentAddress(algo=HashAlgo.SHA256, digest="0" * 64)

# Policies for which nothing is ever disclosed to anyone at any instant: an
# absolute seal, or a temporal seal with no unseal date/condition recorded (sealed
# indefinitely — the narrowest state a record/field can be in per
# ``AccessPolicy``'s own docstring). A segment left in one of these states makes no
# disclosure promise, so it does not require a spoken-consent timestamp — though a
# facilitator is free to record one anyway.
_NEVER_DISCLOSED = (AccessPolicy.SEALED,)


def _to_float(value: object) -> float:
    """Coerce a JSON-decoded ``value`` (``int``/``float``/numeric ``str``) to ``float``.

    Raises ``TypeError``/``ValueError`` for anything else, exactly like the
    built-in ``float()`` would for the types it accepts, so callers can catch
    both uniformly.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise TypeError(f"expected a number, got {type(value).__name__}")
    return float(value)


@dataclass(frozen=True)
class SessionSegment:
    """One consented segment of a recording session.

    ``start_seconds``/``end_seconds`` are the segment markers a facilitator (or a
    later transcription pass) captured during recording — offsets into whatever
    audio/video file the community's own recorder produced, not bytes ledger
    manages itself. ``spoken_consent_at`` is the wall-clock instant, within the
    session, at which the narrator's spoken agreement to *this segment's policy*
    was captured (e.g. "this part public, this part sealed twenty years" — see
    the facilitator script), so a disclosure decision traces to a specific
    consented moment rather than a blanket after-the-fact assumption.
    """

    segment_id: str
    label: str
    start_seconds: float
    end_seconds: float
    policy: AccessPolicy = AccessPolicy.SEALED_UNTIL
    unseal_at: str | None = None
    unseal_condition: str | None = None
    spoken_consent_at: str | None = None
    consent_note: str = ""
    payload_filename: str | None = None
    transcript: str = ""

    def __post_init__(self) -> None:
        if not self.segment_id.strip():
            raise LedgerError("a session segment requires a non-empty segment_id")
        if self.end_seconds < self.start_seconds:
            raise LedgerError(
                f"segment {self.segment_id!r} has end_seconds before start_seconds"
            )
        if self.start_seconds < 0:
            raise LedgerError(f"segment {self.segment_id!r} has a negative start_seconds")

    @property
    def ever_discloses(self) -> bool:
        """Whether this segment's policy discloses to *anyone* at *any* instant.

        ``sealed`` never does. ``sealed-until`` with no ``unseal_at`` is sealed
        indefinitely and also never does (narrowest default). Every other policy,
        and a ``sealed-until`` that names an actual date, eventually shows the
        segment to someone — that is exactly the case spoken consent must cover.
        """
        if self.policy in _NEVER_DISCLOSED:
            return False
        return not (self.policy is AccessPolicy.SEALED_UNTIL and self.unseal_at is None)

    def to_dict(self) -> dict[str, object]:
        return {
            "segment_id": self.segment_id,
            "label": self.label,
            "start_seconds": self.start_seconds,
            "end_seconds": self.end_seconds,
            "policy": self.policy.value,
            "unseal_at": self.unseal_at,
            "unseal_condition": self.unseal_condition,
            "spoken_consent_at": self.spoken_consent_at,
            "consent_note": self.consent_note,
            "payload_filename": self.payload_filename,
            "transcript": self.transcript,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> SessionSegment:
        if not isinstance(data, Mapping):
            raise LedgerError("a session segment must be a JSON object")
        try:
            policy = AccessPolicy(str(data.get("policy", AccessPolicy.SEALED_UNTIL.value)))
        except ValueError as exc:
            raise LedgerError(
                f"segment {data.get('segment_id', '?')!r} has an unknown policy"
            ) from exc
        unseal_at = data.get("unseal_at")
        unseal_condition = data.get("unseal_condition")
        spoken_consent_at = data.get("spoken_consent_at")
        payload_filename = data.get("payload_filename")
        try:
            start = _to_float(data.get("start_seconds", 0))
            end = _to_float(data.get("end_seconds", 0))
        except (TypeError, ValueError) as exc:
            raise LedgerError(
                f"segment {data.get('segment_id', '?')!r} has a non-numeric marker"
            ) from exc
        return cls(
            segment_id=str(data.get("segment_id", "")),
            label=str(data.get("label", "")),
            start_seconds=start,
            end_seconds=end,
            policy=policy,
            unseal_at=str(unseal_at) if unseal_at is not None else None,
            unseal_condition=str(unseal_condition) if unseal_condition is not None else None,
            spoken_consent_at=str(spoken_consent_at) if spoken_consent_at is not None else None,
            consent_note=str(data.get("consent_note", "")),
            payload_filename=str(payload_filename) if payload_filename is not None else None,
            transcript=str(data.get("transcript", "")),
        )


@dataclass(frozen=True)
class SessionManifest:
    """A guided recording session: a set of consented, timestamped segments.

    ``narrator_ref`` is an opaque, session-scoped label the facilitator chooses
    (e.g. a claim/reference token, or simply ``"narrator"`` for a single-narrator
    session) — never a name. ``facilitator`` names a *role*, not a person (e.g.
    "community steward"), for the same reason. Neither field is scanned for
    identity here; the record :func:`apply_session_manifest` produces flows
    through the one ingest path exactly like any other, which re-scans every
    clear-text artifact before anything is written (no-outing rule, defense in
    depth — see :mod:`ledger.ingest`).
    """

    session_id: str
    recorded_at: str
    facilitator: str
    segments: tuple[SessionSegment, ...] = field(default_factory=tuple)
    narrator_ref: str | None = None

    def __post_init__(self) -> None:
        if not self.session_id.strip():
            raise LedgerError("a session manifest requires a non-empty session_id")
        if not self.segments:
            raise LedgerError(f"session {self.session_id!r} has no segments")
        seen: set[str] = set()
        for seg in self.segments:
            if seg.segment_id in seen:
                raise LedgerError(
                    f"session {self.session_id!r} has a duplicate segment_id "
                    f"{seg.segment_id!r}"
                )
            seen.add(seg.segment_id)

    def to_dict(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "recorded_at": self.recorded_at,
            "facilitator": self.facilitator,
            "narrator_ref": self.narrator_ref,
            "segments": [seg.to_dict() for seg in self.segments],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> SessionManifest:
        if not isinstance(data, Mapping):
            raise LedgerError("a session manifest must be a JSON object")
        raw_segments = data.get("segments", [])
        if not isinstance(raw_segments, list):
            raise LedgerError("session manifest 'segments' must be a JSON array")
        segments = tuple(
            SessionSegment.from_dict(item)
            for item in raw_segments
            if isinstance(item, Mapping)
        )
        narrator_ref = data.get("narrator_ref")
        return cls(
            session_id=str(data.get("session_id", "")),
            recorded_at=str(data.get("recorded_at", "")),
            facilitator=str(data.get("facilitator", "")),
            segments=segments,
            narrator_ref=str(narrator_ref) if narrator_ref is not None else None,
        )


def parse_session_manifest(text: str) -> SessionManifest:
    """Parse a session manifest JSON document into a :class:`SessionManifest`.

    Raises :class:`~ledger.errors.LedgerError` — naming only the structural
    problem, never any segment content — on malformed JSON or a manifest shape
    that does not match the documented format.
    """
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LedgerError(f"session manifest is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise LedgerError("session manifest must be a JSON object")
    return SessionManifest.from_dict(raw)


def session_manifest_to_json(manifest: SessionManifest) -> str:
    """Serialize ``manifest`` to indented, stable-order JSON for archival storage."""
    return json.dumps(manifest.to_dict(), indent=2, sort_keys=True, ensure_ascii=False)


def validate_session_manifest(manifest: SessionManifest) -> None:
    """Enforce the kit's hard rule: disclosure requires a spoken-consent moment.

    Every segment whose policy will ever disclose something to someone
    (:attr:`SessionSegment.ever_discloses`) must carry a non-empty
    ``spoken_consent_at``. A ``sealed-conditional`` segment must also name its
    ``unseal_condition`` (otherwise there is no way to ever honour it), and a
    date-bound ``sealed-until`` segment must name its ``unseal_at``. Raises
    :class:`~ledger.errors.LedgerError` naming the offending ``segment_id`` — never
    the segment's label, transcript, or consent note (no-outing-adjacent: an error
    is diagnostic, not a leak) — on the first violation found, in manifest order.
    """
    for seg in manifest.segments:
        if seg.policy is AccessPolicy.SEALED_CONDITIONAL and not (
            seg.unseal_condition and seg.unseal_condition.strip()
        ):
            raise LedgerError(
                f"segment {seg.segment_id!r} is sealed-conditional but names no "
                "unseal_condition"
            )
        if (
            seg.policy is AccessPolicy.SEALED_UNTIL
            and seg.unseal_at is not None
            and not seg.unseal_at.strip()
        ):
            raise LedgerError(f"segment {seg.segment_id!r} has a blank unseal_at")
        if seg.ever_discloses and not (seg.spoken_consent_at and seg.spoken_consent_at.strip()):
            raise LedgerError(
                f"segment {seg.segment_id!r} has policy {seg.policy.value!r}, which "
                "discloses content, but carries no spoken_consent_at — record the "
                "moment the narrator's spoken consent for this segment's policy was "
                "captured before it can be ingested"
            )


# Field-name prefixes for the per-segment scaffolding this module writes onto a
# record. Kept as module constants so a caller (or a test) can recognise/filter
# session-kit fields without restating the format.
SEGMENT_FIELD_PREFIX = "segment:"
CONSENT_FIELD_SUFFIX = ":consent"


def apply_session_manifest(record: Record, manifest: SessionManifest) -> Record:
    """Return a copy of ``record`` with ``manifest``'s segments mapped onto it.

    Validates the manifest first (:func:`validate_session_manifest`) — a manifest
    that fails the spoken-consent rule is never applied, so a record can never be
    built from it (fail closed). For each segment this adds:

    * a descriptive :class:`~ledger.models.Field` named
      ``f"segment:{segment_id}"`` carrying the segment's own label/transcript text
      and its own ``policy``/``unseal_at``/``unseal_condition`` — the "per-segment
      policy" the ideation pitch describes (selective disclosure, autonomy);
    * a companion, ``stewards``-only audit :class:`~ledger.models.Field` named
      ``f"segment:{segment_id}:consent"`` recording *when* spoken consent for that
      policy was captured and the facilitator's paraphrase — never public, so the
      proof of consent is itself available only to the people accountable for
      honouring it, while still being provable on demand (the ideation
      "Excellent" bar: a segment's policy provably matches its spoken-consent
      timestamp).

    A segment that names a ``payload_filename`` also gets a pre-declared
    :class:`~ledger.models.PayloadFile` entry on ``record.payloads`` carrying that
    same policy and transcript, ready for the one ingest path
    (:func:`ledger.ingest.ingest_sip`) to hash the real bytes and recompute the
    address/size/media type when the matching file is supplied at ingest — mirroring
    how a pre-declared transcript payload already works elsewhere in ledger (see
    ``ledger.cli._cmd_ingest``). A segment with no ``payload_filename`` contributes
    only its descriptive fields — it describes part of whatever single recording
    the community already has, not a separate file.

    Pure: builds and returns a new :class:`~ledger.models.Record` via
    :func:`dataclasses.replace`, leaving ``record`` itself untouched, consistent
    with the rest of ledger's pre-ingest record-shaping (e.g.
    :func:`ledger.contribute.apply_edit`).
    """
    validate_session_manifest(manifest)

    new_fields: list[Field] = list(record.fields)
    new_payloads: list[PayloadFile] = list(record.payloads)
    existing_payload_names = {p.filename for p in new_payloads}

    for seg in manifest.segments:
        new_fields.append(
            Field(
                name=f"{SEGMENT_FIELD_PREFIX}{seg.segment_id}",
                value=seg.transcript or seg.label,
                policy=seg.policy,
                unseal_at=seg.unseal_at,
                unseal_condition=seg.unseal_condition,
            )
        )
        consent_detail = (
            f"spoken consent captured at {seg.spoken_consent_at} for policy "
            f"{seg.policy.value!r}"
            if seg.spoken_consent_at
            else f"no spoken consent recorded (policy {seg.policy.value!r} never discloses)"
        )
        if seg.consent_note:
            consent_detail = f"{consent_detail}: {seg.consent_note}"
        new_fields.append(
            Field(
                name=f"{SEGMENT_FIELD_PREFIX}{seg.segment_id}{CONSENT_FIELD_SUFFIX}",
                value=consent_detail,
                policy=AccessPolicy.STEWARDS,
            )
        )
        if seg.payload_filename and seg.payload_filename not in existing_payload_names:
            new_payloads.append(
                PayloadFile(
                    filename=seg.payload_filename,
                    address=_PLACEHOLDER_ADDRESS,
                    policy=seg.policy,
                    transcript=seg.transcript,
                )
            )
            existing_payload_names.add(seg.payload_filename)

    return replace(record, fields=new_fields, payloads=new_payloads)
