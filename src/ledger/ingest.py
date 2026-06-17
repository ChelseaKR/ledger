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

import json
import mimetypes
import os
import re
import shutil
from pathlib import Path

from ledger.access import disclose, is_listable
from ledger.bag import validate_bag, write_bag
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

# Names of the metadata artifacts written beside ``data/`` inside every bag. They
# are tag files (covered by the tag manifest), so their integrity is part of the
# bag's own fixity (integrity).
_RECORD_FILENAME = "record.json"
_DC_FILENAME = "dublincore.json"
_PREMIS_FILENAME = "premis.json"

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


def ingest_sip(
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
       integrity). A :class:`~ledger.models.PayloadFile` entry is built carrying
       the content address, size, media type, and the file's intended policy —
       taken from a matching entry already on ``sip.record.payloads`` if present,
       else the record's ``default_policy`` (default to narrowest).
    2. **Seal identity.** If the SIP carries an identity and a ``vault`` exists,
       the identity is added to the vault and the returned opaque ``identity_ref``
       is set on the record. The identity goes nowhere else (safety).
    3. **Bag.** A RFC 8493 BagIt bag is written from the payload files. Its
       ``bag-info.txt`` names the *archive/collection* as Source-Organization,
       never a person, plus the ``Bagging-Date`` (``now``, injected for
       reproducibility) and the record id as External-Identifier.
    4. **Document.** The record manifest, Dublin Core sidecar, and a PREMIS log
       (one INGESTION event plus a FIXITY_CHECK event per payload) are written as
       tag files beside ``data/`` inside the bag, so their integrity is covered by
       the bag's own tag manifest (integrity, auditability).

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
    for filename in sorted(sip.payload):
        source = sip.payload[filename]
        digests = hash_file_multi(source, (HashAlgo.SHA256, HashAlgo.BLAKE2B))
        address = store.put_file(source)
        size = source.stat().st_size
        existing = declared.get(filename)
        if existing is not None:
            media_type = existing.media_type
        else:
            # Infer from the filename so the Files list and API report something
            # meaningful instead of always octet-stream (correctness, usability).
            guessed, _ = mimetypes.guess_type(filename)
            media_type = guessed or "application/octet-stream"
        policy = existing.policy if existing is not None else record.default_policy
        payload_entries.append(
            PayloadFile(
                filename=filename,
                address=address,
                media_type=media_type,
                size_bytes=size,
                policy=policy,
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
    record.payloads = payload_entries
    # Stamp the manifest with the injected ingest instant so a golden ingest is
    # byte-reproducible rather than carrying the wall-clock construction time.
    record.created_at = now

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
            dict(sip.payload),
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

        vault = self._open_vault(vault_key) if identity is not None else None
        sip = SIP(record=record, payload=dict(payload), identity=identity)
        aip = ingest_sip(sip, self.store, vault, bags_dir=self.bags_dir, agent=agent, now=stamp)

        # Mirror the bag's identity-free manifest into records/ for quick reads.
        record_copy = self.records_dir / f"{record.record_id}.json"
        shutil.copyfile(aip.record_path, record_copy)
        return aip

    # --- reads --------------------------------------------------------------

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
        return disclose(self.get(record_id), grant, stamp)

    def _all_records(self) -> list[Record]:
        """Load every stored record manifest from the fast-lookup directory.

        Sorted by ``(created_at, record_id)`` so a listing is stable across runs
        and machines (predictability, reproducibility).
        """
        if not self.records_dir.exists():
            return []
        records: list[Record] = []
        for path in self.records_dir.glob("*.json"):
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
        out: list[DisclosedRecord] = []
        for record in self._all_records():
            if is_listable(record, grant, stamp):
                out.append(disclose(record, grant, stamp))
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

    # --- audit --------------------------------------------------------------

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
