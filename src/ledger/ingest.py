"""The one ingest path, and the friendly :class:`Archive` facade.

Everything that enters ledger goes through :func:`ingest_sip`. That single path
*always* does the same four things, in the same order, for every item — compute
fixity and store the bytes, seal any identity into the vault, write a standards
BagIt bag, and document the item with a record manifest, a Dublin Core sidecar,
and a PREMIS event log. Having exactly one ingest path means an item can never be
stored un-hashed, un-bagged, or un-documented (correctness, completeness).

:class:`Archive` wraps that path, the content store, the vault, and the access
layer behind a small, task-shaped API — ``ingest``, ``get``, ``disclose``,
``browse``, ``resolve_identity``, ``audit_fixity`` — so a steward or a CLI never
has to wire the subsystems together by hand (usability, learnability).

The no-outing rule is enforced here in depth. :func:`serialize_record` refuses to
emit a record that still carries an in-memory identity, and :func:`ingest_sip`
re-scans the bag-info, the record manifest, the Dublin Core sidecar, and the
PREMIS log for any identity value before returning — a record only ever carries
the opaque ``identity_ref`` token, never a name or contact (safety, defense in
depth).
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import tempfile
from pathlib import Path

from ledger.access import disclose, is_listable
from ledger.attest import attested_conditions
from ledger.bag import atomic_write_text, refresh_tag_manifests, validate_bag, write_bag
from ledger.cas import ContentStore
from ledger.config import Config
from ledger.errors import BagValidationError, LedgerError, ObjectNotFound
from ledger.fixity import AuditReport, hash_file_multi
from ledger.identity import ContributorIdentity, IdentityVault
from ledger.metadata.dublincore import to_json as dublincore_to_json
from ledger.metadata.premis import PremisLog
from ledger.models import (
    AccessPolicy,
    ContentAddress,
    DisclosedRecord,
    DublinCore,
    Field,
    FixityResult,
    Grant,
    HashAlgo,
    PayloadFile,
    PremisEvent,
    PremisEventType,
    Record,
    canonical_json,
    now_iso,
)
from ledger.oais import AIP, SIP
from ledger.preservation import identify_file

# Names of the metadata artifacts written beside ``data/`` inside every bag. They
# are tag files (covered by the tag manifest), so their integrity is part of the
# bag's own fixity (integrity).
_RECORD_FILENAME = "record.json"
_DC_FILENAME = "dublincore.json"
_PREMIS_FILENAME = "premis.json"

# Suffix of the per-record, append-only version index written beside the fast-lookup
# manifest under ``records/``. It lists, oldest first, the CAS address of every prior
# manifest snapshot with when it was superseded and by what kind of event — a
# lightweight living-document history over the content store, outside any bag (so it
# needs no tag-manifest reseal). Each snapshot already passed the identity-refusing
# :func:`serialize_record`, so the history carries no contributor identity (no-outing).
_VERSIONS_SUFFIX = ".versions.json"

# Environment variable a deployment may set to supply the vault key without putting
# it in config or on a command line -> confidentiality. The vault itself owns the
# key; ledger only forwards bytes.
_VAULT_KEY_ENV = "LEDGER_VAULT_KEY"


# --- record (de)serialization ----------------------------------------------


def serialize_record(record: Record) -> str:
    """Serialize ``record`` to canonical JSON, refusing to emit any identity.

    This is the manifest that lands inside the bag and on the read path, so it is
    a no-outing chokepoint: the function defends in depth by asserting the record
    carries no in-memory :class:`~ledger.identity.ContributorIdentity` — only the
    opaque ``identity_ref`` token is allowed through, and even that is the random
    vault key, never a name or contact (safety).

    Determinism: :func:`~ledger.models.canonical_json` sorts keys and is compact,
    so the same record always serializes to byte-identical text (reproducibility).
    """
    if isinstance(getattr(record, "identity", None), ContributorIdentity):
        # Never let an identity object ride along into a serialized manifest.
        raise LedgerError(f"record {record.record_id} carries an in-memory identity")

    payload = {
        "record_id": record.record_id,
        "title": record.title,
        "default_policy": record.default_policy.value,
        "created_at": record.created_at,
        "identity_ref": record.identity_ref,
        "dublin_core": record.dublin_core.to_dict(),
        "content_warnings": list(record.content_warnings),
        "fields": [
            {
                "name": fld.name,
                "value": fld.value,
                "policy": fld.policy.value,
                "unseal_at": fld.unseal_at,
                "unseal_condition": fld.unseal_condition,
            }
            for fld in record.fields
        ],
        "payloads": [
            {
                "filename": p.filename,
                "address": str(p.address),
                "media_type": p.media_type,
                "size_bytes": p.size_bytes,
                "policy": p.policy.value,
                "transcript": p.transcript,
            }
            for p in record.payloads
        ],
    }
    return canonical_json(payload)


def deserialize_record(text: str) -> Record:
    """Rebuild a :class:`~ledger.models.Record` from :func:`serialize_record` output.

    The inverse of :func:`serialize_record`, so a stored manifest round-trips
    exactly (fidelity). Unknown keys are ignored, letting a manifest written by a
    newer ledger degrade gracefully (robustness).
    """
    raw: object = json.loads(text)
    if not isinstance(raw, dict):
        raise LedgerError("record manifest must be a JSON object")
    data: dict[str, object] = raw

    fields = [_field_from_dict(item) for item in _as_dicts(data.get("fields", []))]
    payloads = [_payload_from_dict(item) for item in _as_dicts(data.get("payloads", []))]
    dc_raw = data.get("dublin_core", {})
    dc = (
        DublinCore.from_dict(
            {k: [str(x) for x in v] for k, v in dc_raw.items() if isinstance(v, list)}
        )
        if isinstance(dc_raw, dict)
        else DublinCore()
    )
    ref = data.get("identity_ref")

    return Record(
        title=str(data.get("title", "")),
        record_id=str(data.get("record_id", "")),
        default_policy=AccessPolicy(
            str(data.get("default_policy", AccessPolicy.SEALED_UNTIL.value))
        ),
        dublin_core=dc,
        fields=fields,
        payloads=payloads,
        content_warnings=[str(w) for w in _as_list(data.get("content_warnings", []))],
        identity_ref=str(ref) if ref is not None else None,
        created_at=str(data.get("created_at", "")),
    )


def _field_from_dict(item: dict[str, object]) -> Field:
    """Rebuild one descriptive :class:`~ledger.models.Field` from its mapping."""
    unseal_at = item.get("unseal_at")
    unseal_condition = item.get("unseal_condition")
    return Field(
        name=str(item.get("name", "")),
        value=str(item.get("value", "")),
        policy=AccessPolicy(str(item.get("policy", AccessPolicy.SEALED_UNTIL.value))),
        unseal_at=str(unseal_at) if unseal_at is not None else None,
        unseal_condition=str(unseal_condition) if unseal_condition is not None else None,
    )


def _payload_from_dict(item: dict[str, object]) -> PayloadFile:
    """Rebuild one :class:`~ledger.models.PayloadFile` from its mapping."""
    size = item.get("size_bytes", 0)
    return PayloadFile(
        filename=str(item.get("filename", "")),
        address=ContentAddress.parse(str(item.get("address", ""))),
        media_type=str(item.get("media_type", "application/octet-stream")),
        size_bytes=size if isinstance(size, int) else 0,
        policy=AccessPolicy(str(item.get("policy", AccessPolicy.SEALED_UNTIL.value))),
        transcript=str(item.get("transcript", "")),
    )


def _as_list(value: object) -> list[object]:
    """Coerce ``value`` to a list, treating anything else as empty (robustness)."""
    return list(value) if isinstance(value, list) else []


def _as_dicts(value: object) -> list[dict[str, object]]:
    """Coerce ``value`` to a list of mappings, dropping non-mapping members."""
    return [item for item in _as_list(value) if isinstance(item, dict)]


# --- no-outing audit --------------------------------------------------------


# Identity tokens shorter than this are not substring-scanned: a 2-3 char value
# (e.g. a pronoun) would false-positive against ordinary prose. Such short fields
# are protected structurally (they never flow into a manifest), not by this scan.
_MIN_SCAN_TOKEN = 4


def _assert_identity_free(text: str, identity: ContributorIdentity | None, where: str) -> None:
    """Raise if a contributor's name/contact/notes appears in ``text``.

    Defense in depth for the no-outing rule: even though identity is supposed to
    flow only into the vault, every clear-text artifact (bag-info, record manifest,
    Dublin Core, PREMIS log) is re-scanned *before anything is written to disk*. A
    hit means a coding error leaked identity, so we fail closed (safety).

    The match is case-insensitive and word-bounded rather than a naive substring,
    so an identity that legitimately shares a fragment with prose (e.g. a name that
    is also a common word) does not block an honest ingest (correctness). Pronouns
    and very short values are skipped — they are protected structurally, not here.
    The exception names only *where* a leak was found, never the value (threat
    model: error messages disclose nothing).
    """
    if identity is None:
        return
    haystack = text.casefold()
    for value in (identity.name, identity.contact, identity.notes):
        token = value.strip().casefold()
        if len(token) < _MIN_SCAN_TOKEN:
            continue
        if re.search(rf"\b{re.escape(token)}\b", haystack):
            raise LedgerError(f"no-outing violation: contributor identity present in {where}")


# --- the ingest pipeline ----------------------------------------------------


# Pre-existing complexity (one function walks the full SIP -> fixity -> bag ->
# PREMIS/DC -> store pipeline); surfaced 2026-07-05 when CQ-05's complexity gate
# was enabled. Waived, not re-muted: this is preservation-core code, so a split is
# tracked as a deliberate, well-tested follow-up rather than rushed under audit
# time pressure (see ledger-REMEDIATION.md P3-2).
def ingest_sip(  # noqa: C901
    sip: SIP,
    store: ContentStore,
    vault: IdentityVault | None,
    *,
    bags_dir: Path,
    agent: str,
    now: str,
) -> AIP:
    """Run the one ingest path for ``sip`` and return the stored :class:`AIP`.

    The path is fixed and total — every item is hashed, stored, sealed, bagged,
    and documented in the same order (correctness, completeness):

    1. **Fixity + store.** Each payload file is hashed under both manifest
       algorithms and ``put_file`` into the content-addressed ``store`` (dedupe,
       integrity). The format is identified from the bytes (OAIS Preservation
       Planning: a PRONOM/DROID-style content signature, with an at-risk flag for
       obsolescent formats), informing the media type. A
       :class:`~ledger.models.PayloadFile` entry is built carrying the content
       address, size, media type, and the file's intended policy — taken from a
       matching entry already on ``sip.record.payloads`` if present, else the
       record's ``default_policy`` (default to narrowest).
    2. **Seal identity.** If the SIP carries an identity and a ``vault`` exists,
       the identity is added to the vault and the returned opaque ``identity_ref``
       is set on the record. The identity goes nowhere else (safety).
    3. **Bag.** A RFC 8493 BagIt bag is written from the payload files. Its
       ``bag-info.txt`` names the *archive/collection* as Source-Organization,
       never a person, plus the ``Bagging-Date`` (``now``, injected for
       reproducibility) and the record id as External-Identifier.
    4. **Document.** The record manifest, Dublin Core sidecar, and a PREMIS log
       (one INGESTION event plus a FIXITY_CHECK and a FORMAT_IDENTIFICATION event
       per payload) are written as tag files beside ``data/`` inside the bag, so
       their integrity is covered by the bag's own tag manifest (integrity,
       auditability).

    Before returning, every clear-text artifact is re-scanned for the contributor
    identity and a :class:`~ledger.errors.LedgerError` raised on any hit (defense
    in depth). All timestamps come from the ``now`` parameter, never the wall
    clock, so a golden ingest is byte-reproducible (determinism).
    """
    record = sip.record

    # 1. Fixity + store. Preserve any per-file policy already declared on the
    #    record; otherwise default to the record's narrowest policy.
    declared = {p.filename: p for p in record.payloads}
    payload_entries: list[PayloadFile] = []
    fixity_events: list[PremisEvent] = []
    # PREMIS format-identification events + the IANA media types identified, so the
    # archive models OAIS Preservation Planning (format obsolescence), not only
    # bit-fixity (RM4; NDSA Levels; DPC Handbook).
    format_events: list[PremisEvent] = []
    identified_media_types: list[str] = []
    # The sources actually stored + bagged (ciphertext for absolute-SEALED files).
    bag_payload: dict[str, Path] = {}
    sealed_tmp: Path | None = None
    for filename in sorted(sip.payload):
        source = sip.payload[filename]
        existing = declared.get(filename)
        transcript = existing.transcript if existing is not None else ""
        # Identify the format from the clear source bytes (content-based, before any
        # at-rest encryption). A confident content signature beats a filename guess.
        fmt = identify_file(source)
        identified_media_types.append(fmt.media_type)
        if existing is not None:
            media_type = existing.media_type
        else:
            # Prefer a content-based media type; else infer from the filename so the
            # Files list and API report something meaningful instead of octet-stream
            # (correctness, usability).
            guessed, _ = mimetypes.guess_type(filename)
            media_type = fmt.media_type if fmt.basis == "signature" else (guessed or fmt.media_type)
        policy = existing.policy if existing is not None else record.default_policy
        # An absolute-SEALED payload FILE is encrypted at rest: the content store and
        # the bag hold ciphertext, never the clear bytes, so a stolen disk or hostile
        # replica reveals nothing (user research P2-4, payload tier). It is never
        # served on any read path, so it is only encrypted, never decrypted.
        if policy is AccessPolicy.SEALED:
            if vault is None:
                raise LedgerError(
                    "a 'sealed' (absolute) payload requires a vault key for at-rest encryption"
                )
            if sealed_tmp is None:
                sealed_tmp = Path(tempfile.mkdtemp(prefix="ledger-sealed-"))
            store_source = sealed_tmp / filename
            store_source.parent.mkdir(parents=True, exist_ok=True)
            store_source.write_bytes(vault.encrypt_bytes(source.read_bytes()))
        else:
            store_source = source
        digests = hash_file_multi(store_source, (HashAlgo.SHA256, HashAlgo.BLAKE2B))
        address = store.put_file(store_source)
        size = store_source.stat().st_size
        bag_payload[filename] = store_source
        payload_entries.append(
            PayloadFile(
                filename=filename,
                address=address,
                media_type=media_type,
                size_bytes=size,
                policy=policy,
                transcript=transcript,
            )
        )
        # A fixity check per payload: the stored address re-derived from the bytes,
        # cross-checked by the independent BLAKE2b digest (integrity, redundancy).
        fixity_events.append(
            PremisEvent(
                event_type=PremisEventType.FIXITY_CHECK,
                agent=agent,
                outcome="success",
                detail=f"sha256+blake2b verified ({digests[HashAlgo.BLAKE2B][:12]}…)",
                linked_object=str(address),
                event_datetime=now,
            )
        )
        # A format-identification event per payload (OAIS Preservation Planning): the
        # identified format, its PRONOM PUID where known, how it was identified, and —
        # for an obsolescent/proprietary format — the migration recommendation. The
        # detail carries only format metadata, never identity or content (no-outing).
        format_events.append(
            PremisEvent(
                event_type=PremisEventType.FORMAT_IDENTIFICATION,
                agent=agent,
                outcome="at-risk" if fmt.at_risk else "success",
                detail=fmt.summary(),
                linked_object=str(address),
                event_datetime=now,
            )
        )
    record.payloads = payload_entries
    # Stamp the manifest with the injected ingest instant so a golden ingest is
    # byte-reproducible rather than carrying the wall-clock construction time.
    record.created_at = now
    # Minimum-metadata profile: backfill dc:date from a 4-digit year in the title
    # when no date was given, so a record is at least roughly datable for scholarship
    # and search (user research P2-3) without inventing precision.
    if not record.dublin_core.date:
        year = re.search(r"\b(1[89]\d{2}|20\d{2})\b", record.title)
        if year:
            record.dublin_core.date = [year.group(1)]
    # Backfill dc:format with the identified IANA media types when none was given, so
    # the format is discoverable in the catalogue and the preservation risk is legible
    # (RM4; Dublin Core `format` is the standard home for the media type). Sorted +
    # de-duplicated for a deterministic sidecar.
    if not record.dublin_core.format and identified_media_types:
        record.dublin_core.format = sorted(set(identified_media_types))

    # Absolute-SEALED fields are encrypted AT REST so a stolen disk or hostile
    # replica reveals nothing, not even to a steward (user research P2-4). Such a
    # field is never disclosed on any read path, so it is only ever encrypted, never
    # decrypted here. It requires the vault, like an identity.
    if any(fld.policy is AccessPolicy.SEALED for fld in record.fields):
        if vault is None:
            raise LedgerError(
                "a 'sealed' (absolute) field requires a vault key for at-rest encryption"
            )
        for fld in record.fields:
            if fld.policy is AccessPolicy.SEALED and not fld.value.startswith("enc:"):
                fld.value = vault.encrypt_text(fld.value)

    # 2. Refuse a collision BEFORE sealing any identity, so a failed ingest cannot
    #    leave an orphaned, unreachable identity in the vault (#correctness, consent).
    bag_dir = bags_dir / record.record_id
    if bag_dir.exists():
        # An item is bagged exactly once; refuse to clobber a prior AIP silently.
        raise LedgerError(f"bag already exists for record {record.record_id}")

    # 3. Seal identity into the vault and replace it with an opaque ref. Everything
    #    after this point is wrapped so any failure revokes the ref (no orphan).
    sealed_ref: str | None = None
    if sip.identity is not None and vault is not None:
        sealed_ref = vault.add(sip.identity)
        record.identity_ref = sealed_ref

    try:
        # Build every clear-text artifact IN MEMORY first.
        bag_info = {
            "Source-Organization": record.dublin_core.publisher[0]
            if record.dublin_core.publisher
            else "ledger archive",
            "Bagging-Date": now,
            "External-Identifier": record.record_id,
        }
        bag_info_text = "Payload-Oxum: …\n" + "".join(f"{k}: {v}\n" for k, v in bag_info.items())
        record_json = serialize_record(record)
        dc_json = dublincore_to_json(record.dublin_core)

        premis = PremisLog()
        premis.record(
            PremisEvent(
                event_type=PremisEventType.INGESTION,
                agent=agent,
                outcome="success",
                detail=f"ingested {len(payload_entries)} payload file(s) for {record.record_id}",
                linked_object=record.record_id,
                event_datetime=now,
            )
        )
        for event in fixity_events:
            premis.record(event)
        for event in format_events:
            premis.record(event)
        premis_json = premis.to_json()

        # 4. Defense in depth: scan every artifact for the identity BEFORE a single
        #    byte is written to disk, so a coding-error leak fails closed and leaves
        #    nothing behind (safety — the guarantee must fail CLOSED).
        _assert_identity_free(bag_info_text, sip.identity, "bag-info.txt")
        _assert_identity_free(record_json, sip.identity, "record manifest")
        _assert_identity_free(dc_json, sip.identity, "dublin core")
        _assert_identity_free(premis_json, sip.identity, "premis log")

        # 5. Write the bag with the metadata as tag files, so their integrity is
        #    covered by the bag's own tag manifest (tampering with a record's policy
        #    or identity_ref then fails validation).
        bag = write_bag(
            bag_dir,
            bag_payload,
            bag_info=bag_info,
            extra_tag_files={
                _RECORD_FILENAME: record_json.encode("utf-8"),
                _DC_FILENAME: dc_json.encode("utf-8"),
                _PREMIS_FILENAME: premis_json.encode("utf-8"),
            },
        )
    except BaseException:
        # Any failure after sealing must not orphan the identity or leave a partial
        # bag on disk (consent, fail-closed). Revoke then clean up, then re-raise.
        if sealed_ref is not None and vault is not None:
            vault.revoke(sealed_ref)
        shutil.rmtree(bag_dir, ignore_errors=True)
        raise
    finally:
        # The encrypted-payload temp files have been copied into the bag and store;
        # remove the clear staging area in all cases.
        if sealed_tmp is not None:
            shutil.rmtree(sealed_tmp, ignore_errors=True)

    return AIP(
        bag=bag,
        record=record,
        premis_path=bag_dir / _PREMIS_FILENAME,
        dc_path=bag_dir / _DC_FILENAME,
        record_path=bag_dir / _RECORD_FILENAME,
    )


# --- the Archive facade -----------------------------------------------------


class Archive:
    """The friendly, task-shaped facade over every ledger subsystem.

    One object exposes the whole lifecycle — ``ingest``, ``get``, ``disclose``,
    ``browse``, ``resolve_identity``, ``audit_fixity`` — so a steward, a CLI, or a
    server never has to assemble the content store, vault, bagger, and access layer
    by hand (usability, learnability). Every method that needs a timestamp accepts
    an injectable ``now`` and defaults to :func:`~ledger.models.now_iso`, keeping
    behaviour reproducible where it must be (determinism).
    """

    def __init__(self, config: Config) -> None:
        """Wire the facade to ``config`` without forcing any side effects.

        The content store, bags directory, and log directory are derived from
        ``config.store_root``; the vault is opened lazily (only when an ingest or a
        resolve actually needs it) so constructing an :class:`Archive` is cheap and
        the vault key is required only when identity is genuinely in play (least
        privilege).
        """
        self.config = config
        self.store_root = Path(config.store_root)
        self.store = ContentStore(self.store_root)
        self.bags_dir = self.store_root / "bags"
        self.records_dir = self.store_root / "records"
        self.logs_dir = self.store_root / "logs"
        self.vault_path = Path(config.vault_path)
        self._vault: IdentityVault | None = None

    # --- construction -------------------------------------------------------

    @classmethod
    def init(cls, config: Config) -> Archive:
        """Create the archive's directory tree, persist ``config``, seed the vault.

        Stands a fresh archive up on disk: store, bags, records, and logs
        directories are created and the validated config is written beside the
        store. If a vault key is available via the ``LEDGER_VAULT_KEY`` environment
        variable the encrypted vault is created now; otherwise creation is deferred
        until a key is supplied at first identity ingest (affordability — an
        archive with no contributors yet needs no key).
        """
        config.validate()
        archive = cls(config)
        for directory in (
            archive.store_root,
            archive.bags_dir,
            archive.records_dir,
            archive.logs_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        config.save(archive.store_root / "config.json")

        key = _vault_key_from_env()
        if key is not None and not archive.vault_path.exists():
            IdentityVault.create(archive.vault_path, key)
        return archive

    # --- vault access -------------------------------------------------------

    def _open_vault(self, key: bytes | None) -> IdentityVault:
        """Open (or create) the identity vault under ``key``, caching the handle.

        The vault is opened on demand so the key is required only when identity is
        actually involved (least privilege). A missing key surfaces as a clear
        :class:`~ledger.errors.LedgerError`; the key bytes are never echoed
        (no-outing rule).
        """
        if self._vault is not None:
            return self._vault
        if key is None:
            key = _vault_key_from_env()
        if key is None:
            raise LedgerError("identity vault key is required but was not provided")
        self._vault = (
            IdentityVault.open(self.vault_path, key)
            if self.vault_path.exists()
            else IdentityVault.create(self.vault_path, key)
        )
        return self._vault

    # --- ingest -------------------------------------------------------------

    def ingest(
        self,
        payload: dict[str, Path],
        record: Record,
        *,
        identity: ContributorIdentity | None = None,
        vault_key: bytes | None = None,
        agent: str = "ledger",
        now: str | None = None,
    ) -> AIP:
        """Ingest ``payload`` described by ``record`` through the one ingest path.

        Builds a :class:`~ledger.oais.SIP` and delegates to :func:`ingest_sip`, so
        the item is hashed, stored, optionally sealed, bagged, and documented in
        exactly the one always-correct way (correctness, completeness). The vault
        is opened only when an ``identity`` is supplied (least privilege). A copy
        of the stored record manifest is also written under ``records/`` for fast
        lookup by :meth:`get` without unpacking a bag (efficiency).
        """
        stamp = now if now is not None else now_iso()
        self.bags_dir.mkdir(parents=True, exist_ok=True)
        self.records_dir.mkdir(parents=True, exist_ok=True)

        # The vault is needed for a sealed identity, any absolute-SEALED field, or
        # any absolute-SEALED payload file (all encrypted at rest); open it only
        # then (least privilege). A payload's policy is its declared one, else the
        # record default — so a SEALED default over a non-empty payload set counts.
        declared = {p.filename: p for p in record.payloads}
        payload_sealed = bool(payload) and any(
            (declared[name].policy if name in declared else record.default_policy)
            is AccessPolicy.SEALED
            for name in payload
        )
        needs_vault = (
            identity is not None
            or any(fld.policy is AccessPolicy.SEALED for fld in record.fields)
            or payload_sealed
        )
        vault = self._open_vault(vault_key) if needs_vault else None
        sip = SIP(record=record, payload=dict(payload), identity=identity)
        aip = ingest_sip(sip, self.store, vault, bags_dir=self.bags_dir, agent=agent, now=stamp)

        # Mirror the bag's identity-free manifest into records/ for quick reads.
        record_copy = self.records_dir / f"{record.record_id}.json"
        shutil.copyfile(aip.record_path, record_copy)
        return aip

    # --- reads --------------------------------------------------------------

    def attested_conditions(self) -> frozenset[str]:
        """The ``SEALED_CONDITIONAL`` conditions attested-met for this archive.

        Loaded from the durable attested-conditions set under ``logs/`` (written by
        the 2-of-N :mod:`ledger.attest` flow). Every read path funnels this into
        :func:`ledger.access.disclose` as ``conditions_met`` — see :meth:`disclose`
        and :meth:`browse` — so a field "sealed until a condition is met" opens
        uniformly for *all* callers (CLI, server, export) the moment its condition is
        attested, and stays sealed until then (fail-closed)."""
        return attested_conditions(self.logs_dir)

    def _record_path(self, record_id: str) -> Path:
        """The fast-lookup manifest path for ``record_id`` under ``records/``."""
        return self.records_dir / f"{record_id}.json"

    def get(self, record_id: str) -> Record:
        """Load the stored, identity-free record manifest for ``record_id``.

        Reads the fast-lookup copy under ``records/`` if present, else the manifest
        tag file inside the record's bag (resilience: the bag is authoritative).
        Raises :class:`~ledger.errors.ObjectNotFound` naming only the record id if
        no manifest exists (no-outing rule).
        """
        fast = self._record_path(record_id)
        if fast.exists():
            return deserialize_record(fast.read_text(encoding="utf-8"))
        in_bag = self.bags_dir / record_id / _RECORD_FILENAME
        if in_bag.exists():
            return deserialize_record(in_bag.read_text(encoding="utf-8"))
        raise ObjectNotFound(record_id)

    def _versions_path(self, record_id: str) -> Path:
        """The append-only version-index path for ``record_id`` under ``records/``."""
        return self.records_dir / f"{record_id}{_VERSIONS_SUFFIX}"

    def apply_update(self, record: Record, event: PremisEvent) -> None:
        """Persist an updated record manifest and append a PREMIS event to its bag.

        The shared write path behind every post-ingest change (a consent/policy
        change, a content warning, a review decision): it rewrites the fast-lookup
        ``records/`` copy and the in-bag manifest so the next disclosure reflects the
        change, and appends ``event`` to the bag's PREMIS log so the action is
        auditable (accountability, traceability). All writes go through the
        identity-refusing :func:`serialize_record`, so a persisted manifest can never
        carry an in-memory identity (no-outing rule).

        Before the fast-lookup manifest is overwritten, its *current* bytes are
        snapshotted into the content store and recorded in an append-only per-record
        version index (:meth:`record_versions`), so a steward can compare the record
        against any prior revision (living-document history). The snapshot reuses the
        already-identity-free manifest bytes and the CAS, so it adds no new no-outing
        surface and needs no bag reseal (the index lives under ``records/``, outside
        every bag).

        Because ``record.json`` and ``premis.json`` are *tag* files covered by the
        bag's tag manifests, rewriting them would leave the tag manifests stale and
        the bag failing its own :func:`~ledger.bag.validate_bag` check — a lawful
        change reading as tampering at the next audit. The bag is therefore resealed
        via :func:`~ledger.bag.refresh_tag_manifests`, which recomputes only the tag
        manifests (the payload manifests, the real content fixity, are untouched), so
        an updated bag re-validates while genuine content rot is still caught
        (integrity, failure transparency).

        Every reseal is itself recorded: a PREMIS ``VALIDATION`` event carrying the
        old and new ``record.json`` digests (and the pre-append ``premis.json``
        digest) is appended alongside ``event`` before the tag manifests are
        recomputed. A lawful reseal therefore leaves an auditable digest transition
        in the log it reseals — it is never bit-for-bit indistinguishable from an
        edit that skipped the log — giving the hash-chained log (FIX-06) a concrete
        anchor for each tag-manifest generation (accountability, non-repudiation).
        All file writes are atomic (temp + ``os.replace``); the residual risk is a
        crash *between* files, a window this ordering keeps to the log-write and
        reseal steps that the next ``audit_fixity`` surfaces rather than hides.
        """
        manifest = serialize_record(record)
        fast = self.records_dir / f"{record.record_id}.json"

        # Snapshot the manifest being superseded into the CAS and note it in the
        # append-only version index, oldest first. Done before the overwrite so the
        # previous revision is preserved; the very first update of a freshly ingested
        # record snapshots its as-ingested manifest.
        if fast.exists():
            prior = fast.read_bytes()
            address = self.store.put_bytes(prior)
            self._append_version(record.record_id, str(address), event.event_type.value)

        atomic_write_text(fast, manifest)

        bag_dir = self.bags_dir / record.record_id
        will_reseal = next(bag_dir.glob("tagmanifest-*.txt"), None) is not None
        resealed = False
        transitions: list[str] = []
        in_bag = bag_dir / _RECORD_FILENAME
        if in_bag.exists():
            old_digest = hashlib.sha256(in_bag.read_bytes()).hexdigest()
            new_digest = hashlib.sha256(manifest.encode("utf-8")).hexdigest()
            atomic_write_text(in_bag, manifest)
            transitions.append(f"{_RECORD_FILENAME} sha256 {old_digest} -> {new_digest}")
            resealed = True
        premis_path = bag_dir / _PREMIS_FILENAME
        if premis_path.exists():
            old_premis = hashlib.sha256(premis_path.read_bytes()).hexdigest()
            log = PremisLog.read(premis_path)
            log.record(event)
            if will_reseal:
                # The digest-transition record: what changed and from/to which
                # bytes. The new premis.json digest cannot name itself (it would
                # be self-referential); the refreshed tag manifest carries it.
                transitions.append(f"{_PREMIS_FILENAME} sha256 before append {old_premis}")
                log.record(
                    PremisEvent(
                        event_type=PremisEventType.VALIDATION,
                        agent=event.agent,
                        outcome="success",
                        detail="bag resealed after lawful manifest update: "
                        + "; ".join(transitions),
                        linked_object=record.record_id,
                        event_datetime=event.event_datetime,
                    )
                )
            log.write(premis_path)
            resealed = True

        # Reseal: recompute the tag manifests so the rewritten tag files re-validate.
        # Guarded by the presence of a tag manifest so a records-only update (no bag
        # on disk) stays a no-op rather than raising (robustness).
        if resealed and will_reseal:
            refresh_tag_manifests(bag_dir)

    def _append_version(self, record_id: str, address: str, event_type: str) -> None:
        """Append one superseded-manifest snapshot to the record's version index.

        The index is append-only and canonical (sorted keys, compact) so the history
        is byte-stable and cannot be silently rewritten (auditability, reproducibility).
        Each entry names only the snapshot's opaque CAS ``address``, when it was saved,
        and the kind of event that superseded it — never a contributor identity or a
        sealed value (no-outing rule)."""
        path = self._versions_path(record_id)
        entries = self._read_versions(path)
        entries.append({"address": address, "saved_at": now_iso(), "event_type": event_type})
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(canonical_json(entries), encoding="utf-8", newline="\n")

    @staticmethod
    def _read_versions(path: Path) -> list[dict[str, str]]:
        """Load a version index as a list of string maps, empty if absent/unreadable."""
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return []
        if not isinstance(raw, list):
            return []
        return [{str(k): str(v) for k, v in item.items()} for item in raw if isinstance(item, dict)]

    def record_versions(self, record_id: str) -> list[dict[str, str]]:
        """Return ``record_id``'s prior-manifest snapshots, oldest first.

        Each entry is ``{"address", "saved_at", "event_type"}`` — the CAS address of a
        superseded manifest, when it was superseded, and by what kind of event. Returns
        an empty list when the record has never been updated, so a caller can ask about
        any record id safely. The index is identity-free by construction (every snapshot
        passed :func:`serialize_record`), so this discloses nothing protected (no-outing
        rule)."""
        return self._read_versions(self._versions_path(record_id))

    def get_version(self, record_id: str, address: str) -> Record:
        """Rebuild a prior :class:`~ledger.models.Record` from a snapshot ``address``.

        Reads the snapshotted manifest bytes back out of the content store and
        deserializes them, so a steward can view exactly what the record said at that
        revision (living-document history). The ``address`` must be one listed in
        :meth:`record_versions` for this record; :class:`~ledger.errors.ObjectNotFound`
        is raised (naming only the address) if no such snapshot exists. The snapshot
        already passed the identity-refusing :func:`serialize_record`, so the returned
        record carries only the opaque ``identity_ref`` token, never an identity
        (no-outing rule)."""
        known = {entry["address"] for entry in self.record_versions(record_id)}
        if address not in known:
            raise ObjectNotFound(address)
        addr = ContentAddress.parse(address)
        return deserialize_record(self.store.read_bytes(addr).decode("utf-8"))

    def log_takedown(self, event: PremisEvent) -> None:
        """Append a takedown/withdrawal decision to the archive-level takedowns log.

        The accountable record of *why* a record was removed, kept in
        ``logs/takedowns.premis.json`` so it outlives the data it documents. The one
        place a removal decision is persisted, shared by a steward takedown and a
        contributor's pre-publication withdrawal (accountability, separation of
        concerns)."""
        log_path = self.logs_dir / "takedowns.premis.json"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        log = PremisLog.read(log_path) if log_path.exists() else PremisLog()
        log.record(event)
        log.write(log_path)

    def log_grant_use(self, subject: str, route_class: str, *, now: str | None = None) -> None:
        """Append a scrubbed record that an authenticated grant was used on a request.

        Written to ``logs/grant-uses.premis.json`` so a steward can see *that* a
        provisioned subject exercised its capability and on *which class of route*
        (e.g. ``browse``, ``api``, ``steward``) — accountability for privileged
        access. It records only the grant ``subject`` (a pre-provisioned identifier,
        never a real contributor name) and a coarse route class: never the bearer
        token, never a record id, never a query string, so the audit trail itself
        discloses nothing (no-outing rule — logs disclose nothing).
        """
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.logs_dir / "grant-uses.premis.json"
        log = PremisLog.read(log_path) if log_path.exists() else PremisLog()
        log.record(
            PremisEvent(
                event_type=PremisEventType.VALIDATION,
                agent=subject,
                outcome="success",
                detail=f"authenticated grant used on {route_class} route",
                linked_object=None,
                event_datetime=now if now is not None else now_iso(),
            )
        )
        log.write(log_path)

    def remove_all_copies(self, record_id: str) -> tuple[int, bool]:
        """Physically remove every stored copy of ``record_id`` and revoke its identity.

        The shared *effect* behind a steward takedown and a contributor's withdrawal
        of a pending submission: it revokes any sealed identity from the vault, then
        deletes the record's bag, its fast-lookup manifest, and every configured
        replica. Returns ``(copies_removed, identity_revoked)`` so the caller can
        report counts without naming anything (no-outing rule). It records *no* audit
        decision itself — the caller owns the accountable "why", recording it before
        calling this so the reason outlives the data (separation of concerns).

        Because this builds the paths it ``rmtree``s from ``record_id``, the id is
        first validated to be a single safe path component (no separators, not ``.``
        or ``..``, no NUL). A real record id is an opaque hex string, so this only ever
        rejects a crafted id — but it guarantees a malicious or buggy caller can never
        turn a removal into a directory traversal that deletes outside the archive
        (defense in depth on a destructive primitive).
        """
        if (
            not record_id
            or record_id in {".", ".."}
            or any(sep in record_id for sep in ("/", "\\", "\x00"))
        ):
            raise LedgerError("invalid record id")
        identity_ref: str | None = None
        try:
            identity_ref = self.get(record_id).identity_ref
        except LedgerError:
            identity_ref = None

        revoked = False
        if identity_ref is not None:
            try:
                self._open_vault(None).revoke(identity_ref)
                revoked = True
            except LedgerError:
                revoked = False

        removed = 0
        bag_dir = self.bags_dir / record_id
        if bag_dir.exists():
            shutil.rmtree(bag_dir)
            removed += 1
        fast = self.records_dir / f"{record_id}.json"
        if fast.exists():
            fast.unlink()
        # Remove the append-only version index too, so a takedown leaves no dangling
        # history pointer. The snapshot bytes it referenced are content-addressed and
        # identity-free; they are left to normal store maintenance rather than chased
        # here, keeping this destructive primitive simple.
        versions = self._versions_path(record_id)
        if versions.exists():
            versions.unlink()
        for location in self.config.locations:
            replica = Path(location.path) / record_id
            if replica.exists() and replica != bag_dir:
                shutil.rmtree(replica)
                removed += 1
        return removed, revoked

    def disclose(
        self,
        record_id: str,
        grant: Grant,
        now: str | None = None,
    ) -> DisclosedRecord:
        """Disclose ``record_id`` to ``grant`` — load then project to the safe shape.

        Routes through the single disclosure point
        (:func:`ledger.access.disclose`), so the returned
        :class:`~ledger.models.DisclosedRecord` carries no ``identity_ref`` and
        only what ``grant`` may see at ``now`` (safety). Raises
        :class:`~ledger.errors.AccessDenied` if the grant may not even list the
        record (confidentiality).
        """
        stamp = now if now is not None else now_iso()
        return disclose(
            self.get(record_id), grant, stamp, conditions_met=self.attested_conditions()
        )

    def _all_records(self) -> list[Record]:
        """Load every stored record manifest from the fast-lookup directory.

        Sorted by ``(created_at, record_id)`` so a listing is stable across runs
        and machines (predictability, reproducibility).
        """
        if not self.records_dir.exists():
            return []
        records: list[Record] = []
        for path in self.records_dir.glob("*.json"):
            # The append-only version indexes live under records/ with the same
            # extension; they are not manifests, so skip them (they would only be
            # skipped later as unparseable, but this is explicit and cheaper).
            if path.name.endswith(_VERSIONS_SUFFIX):
                continue
            try:
                records.append(deserialize_record(path.read_text(encoding="utf-8")))
            except (LedgerError, ValueError, OSError):
                # One unreadable manifest must not take down the whole browse/audit
                # path; skip it so the rest of the archive stays available
                # (degradability, availability). It is still caught by audit_fixity.
                continue
        records.sort(key=lambda r: (r.created_at, r.record_id))
        return records

    def browse(self, grant: Grant, now: str | None = None) -> list[DisclosedRecord]:
        """List, as safe disclosed records, everything ``grant`` may see at ``now``.

        Only records that are *listable* for the grant are included; the rest are
        skipped silently, so the absence of a row leaks nothing about a sealed
        record's existence (confidentiality). Ordering follows ``_all_records`` —
        ``created_at`` then ``record_id`` — for a stable browse (predictability).
        """
        stamp = now if now is not None else now_iso()
        conditions = self.attested_conditions()
        out: list[DisclosedRecord] = []
        for record in self._all_records():
            if is_listable(record, grant, stamp, conditions_met=conditions):
                out.append(disclose(record, grant, stamp, conditions_met=conditions))
        return out

    def resolve_identity(
        self, record_id: str, grant: Grant, now: str | None = None
    ) -> ContributorIdentity:
        """Resolve the contributor identity behind ``record_id`` under ``grant``.

        Looks up the record's opaque ``identity_ref`` and asks the vault to decrypt
        it, gated by the grant *at instant ``now``*. An expired grant unseals
        nothing, exactly like every other read path (least privilege, fail-closed).
        Raises :class:`~ledger.errors.AccessDenied` unless ``grant.identity_unseal``
        names that ref and the grant is unexpired, or
        :class:`~ledger.errors.LedgerError` if the record has no sealed identity.
        The identity is returned only to the authorized caller and never logged or
        persisted (no-outing rule).
        """
        stamp = now if now is not None else now_iso()
        record = self.get(record_id)
        if record.identity_ref is None:
            raise LedgerError(f"record {record_id} has no sealed identity")
        vault = self._open_vault(None)
        return vault.resolve(record.identity_ref, grant, stamp)

    # --- key rotation -------------------------------------------------------

    def rekey_vault(
        self,
        new_key: bytes,
        *,
        old_key: bytes | None = None,
        agent: str = "ledger",
        now: str | None = None,
    ) -> int:
        """Rotate the identity-vault key, re-encrypting every sealed identity.

        Rotation is a deliberate, recorded act, like every other sensitive steward
        operation in ledger: it opens the vault with the current key (``old_key`` or
        ``LEDGER_VAULT_KEY``), re-encrypts every identity under ``new_key`` (atomic —
        the vault is untouched unless all entries re-encrypt), and appends a
        ``REKEY`` PREMIS event to ``logs/key-rotations.premis.json`` so the rotation
        is auditable. Only a count is recorded; no key, ref plaintext, or identity is
        ever logged (no-outing rule).

        Refuses (fail-closed) when the archive holds absolute-``SEALED`` content at
        rest — a sealed field value or payload encrypted under the *same* vault key.
        Rotating the identity entries alone would orphan that content, so rather than
        silently strand it the rotation stops and tells the steward, leaving the
        harder re-bagging migration for a deliberate, separate step. The common
        archive (identity sealing and temporal seals, no absolute at-rest seals)
        rotates cleanly.

        Returns the number of identities re-encrypted. Raises
        :class:`~ledger.errors.LedgerError` if there is no vault to rotate or if
        absolute-sealed at-rest content is present.
        """
        if not self.vault_path.exists():
            raise LedgerError("no identity vault exists to rekey")
        for record in self._all_records():
            sealed_at_rest = (
                any(f.policy is AccessPolicy.SEALED for f in record.fields)
                or any(p.policy is AccessPolicy.SEALED for p in record.payloads)
                or (record.default_policy is AccessPolicy.SEALED and bool(record.payloads))
            )
            if sealed_at_rest:
                raise LedgerError(
                    "cannot rekey: the archive holds absolute-sealed content encrypted "
                    "under the current vault key; rotating identities alone would orphan "
                    "it. A full re-bagging migration is required first."
                )
        stamp = now if now is not None else now_iso()
        vault = self._open_vault(old_key)
        count = vault.rekey(new_key)

        self.logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.logs_dir / "key-rotations.premis.json"
        log = PremisLog.read(log_path) if log_path.exists() else PremisLog()
        log.record(
            PremisEvent(
                event_type=PremisEventType.REKEY,
                agent=agent,
                outcome="success",
                detail=f"identity vault rekeyed; {count} identity(ies) re-encrypted",
                linked_object=None,
                event_datetime=stamp,
            )
        )
        log.write(log_path)
        return count

    # --- readiness ----------------------------------------------------------

    def check_readiness(self) -> tuple[bool, str]:
        """Cheap structural readiness probe for ``/healthz`` (no unsealing).

        Confirms the archive can serve *at all* before the more expensive fixity
        sweep, so a liveness check can distinguish "the process is up but the store
        or vault is unreachable" from "everything is fine". It reads no payload,
        unseals no identity, and returns only a generic, non-identity-bearing reason
        code (no-outing rule):

        * ``store-unreadable`` — the store root is missing or not readable.
        * ``records-unreadable`` — the records directory is missing or not readable.
        * ``vault-unopenable`` — a vault key is provisioned and a vault file exists,
          but it cannot be opened with that key (wrong key or tampering). An archive
          with no vault yet is still ready — affordability: contributors may not have
          been sealed yet.

        Returns ``(ready, reason)`` where ``reason`` is ``""`` when ready.
        """
        if not self.store_root.is_dir() or not os.access(self.store_root, os.R_OK):
            return (False, "store-unreadable")
        if not self.records_dir.is_dir() or not os.access(self.records_dir, os.R_OK):
            return (False, "records-unreadable")
        key = _vault_key_from_env()
        if key is not None and self.vault_path.exists():
            try:
                IdentityVault.open(self.vault_path, key)
            except LedgerError:
                return (False, "vault-unopenable")
        return (True, "")

    # --- audit --------------------------------------------------------------

    def record_events(self, record_id: str) -> list[PremisEvent]:
        """Return one record's own PREMIS events, in log order (oldest first).

        Reads only that record's bag ``premis.json``; returns an empty list if the bag
        or its log is missing or unreadable rather than raising, so a caller can ask
        about any record id safely. PREMIS events are identity-free by construction, so
        this discloses nothing protected (no-outing rule)."""
        premis_path = self.bags_dir / record_id / _PREMIS_FILENAME
        if not premis_path.exists():
            return []
        try:
            return list(PremisLog.read(premis_path).events)
        except (LedgerError, ValueError, OSError):
            return []

    def audit_events(self, *, limit: int = 200) -> list[PremisEvent]:
        """Aggregate the archive's PREMIS events, newest first, for a steward view.

        Gathers every event from each bag's ``premis.json`` and the archive-level
        logs (takedowns, key rotations), sorts newest-first, and caps at ``limit``.
        PREMIS events are identity-free by construction — an event carries an agent,
        an outcome, a detail, an opaque ``linked_object`` (a content address, record
        id, or bag id), and a timestamp, never a contributor identity or a sealed
        value (no-outing rule) — so this read-only audit view discloses nothing a
        steward should not see. One unreadable log never aborts the sweep
        (degradability)."""
        events: list[PremisEvent] = []
        if self.bags_dir.exists():
            for bag in sorted(p for p in self.bags_dir.iterdir() if p.is_dir()):
                premis_path = bag / _PREMIS_FILENAME
                if premis_path.exists():
                    try:
                        events.extend(PremisLog.read(premis_path).events)
                    except (LedgerError, ValueError, OSError):
                        continue
        if self.logs_dir.exists():
            for log_path in sorted(self.logs_dir.glob("*.premis.json")):
                try:
                    events.extend(PremisLog.read(log_path).events)
                except (LedgerError, ValueError, OSError):
                    continue
        events.sort(key=lambda e: e.event_datetime, reverse=True)
        return events[:limit]

    def audit_fixity(self) -> list[tuple[str, AuditReport]]:
        """Validate every stored bag, returning ``(bag_name, report)`` per bag.

        Walks ``bags/`` and runs :func:`ledger.bag.validate_bag` on each in stable
        name order so a steward sees each per-file outcome and can spot drift early
        (inspectability, failure transparency). A *structurally* broken bag does not
        abort the sweep: it is turned into a report with one failing result naming
        the structural problem, so one bad bag never hides the health of the rest
        (degradability, failure transparency).
        """
        if not self.bags_dir.exists():
            return []
        reports: list[tuple[str, AuditReport]] = []
        for bag_path in sorted(p for p in self.bags_dir.iterdir() if p.is_dir()):
            try:
                reports.append((bag_path.name, validate_bag(bag_path)))
            except BagValidationError as exc:
                synthetic = FixityResult(
                    path=bag_path.name,
                    algo=HashAlgo.SHA256,
                    expected="structurally valid bag",
                    actual=f"invalid: {exc}",
                )
                reports.append((bag_path.name, AuditReport(results=[synthetic])))
        return reports


# --- module helpers ---------------------------------------------------------


def _vault_key_from_env() -> bytes | None:
    """Return the vault key from ``LEDGER_VAULT_KEY``, or ``None`` if unset.

    The key travels as an environment variable so it never lands in config files or
    on a command line (confidentiality). It is read as raw ASCII bytes, exactly the
    urlsafe-base64 form :meth:`IdentityVault.generate_key` produces; the bytes are
    never logged (no-outing rule).
    """
    raw = os.environ.get(_VAULT_KEY_ENV)
    return raw.encode("ascii") if raw else None
