"""Replication with verify-on-arrival and quarantine-and-heal.

Preservation survives loss only if there is more than one good copy and a
discipline for keeping the copies honest. This module mirrors whole BagIt bags to
configured :class:`~ledger.config.StorageLocation` targets and enforces three
rules, each tied to a named quality attribute:

* **Verify-on-arrival** -> reliability: a bag is re-validated at its destination,
  so a copy that arrived torn (a dropped block, a truncated transfer) is caught at
  write time rather than discovered years later when it is needed.
* **Quarantine-and-heal** -> recoverability / resilience / fault-tolerance: a copy
  that fails validation is moved aside into a sibling ``quarantine/`` directory and
  the failure is recorded as a labelled preservation event; a later :func:`heal`
  rebuilds any failing or missing replica from a copy that still validates.
* **Never trust a divergent copy** -> integrity: healing only ever copies *from* a
  replica that has just passed full RFC 8493 validation, and :func:`heal` refuses
  to act at all when no replica validates — there is nothing trustworthy to copy.
* **Chain heads travel with the bag, and are compared** -> tamper-evidence
  (FIX-06): ``premis.json`` is copied verbatim as part of the bag, so its hash
  chain (:mod:`ledger.chain`) rides along on every transfer for free. A replica
  whose own chain no longer verifies, or whose head disagrees with the source's,
  is reported unhealthy exactly like a replica with divergent bytes — this is
  what catches a steward who edited history *and* regenerated the bag's tag
  manifest to hide it, which byte-level fixity alone cannot.

Tolerating an unreachable location (:func:`verify_replicas` reports ``ok=False``
rather than raising) serves degradability / availability: one offline mirror must
not blind the steward to the health of the others.

No-outing: this module moves and validates opaque bag *directories*. It places
only bag directory names, storage-location names, and bag paths into events and
exception messages — never a contributor identity, a sealed field, or any payload
byte. Bag bytes are copied, never read into a log, a metric, or an error.

Mutual preservation aid (EXP-15, ``docs/ideation/03-expansions.md``): a second,
opt-in transport for community instances that want to hold *each other's* bags as
redundancy without either side having to trust the other with plaintext. A
:func:`seal_bag` encrypts a whole bag into a single authenticated ciphertext blob
with a key that never leaves the owning instance ("key stays home"); the blob —
not the bag — is what a partner location holds. :func:`replicate_sealed_bag`
writes that blob to a partner :class:`~ledger.config.StorageLocation` and verifies
it landed intact by re-reading and re-hashing the ciphertext (verify-on-arrival,
adapted: the partner cannot validate BagIt structure it cannot decrypt, so arrival
integrity is checked by digest instead). :func:`attest_sealed_replica` lets either
side — most usefully the *holding* partner, on a schedule — prove which bytes they
currently hold without ever decrypting them, closing the §4.5 threat-model residual
that a hostile or compromised replica host can read what it stores. Recovery is a
drill, not blind trust: :func:`recover_sealed_bag` pulls the blob back, decrypts it
locally, and runs the same :func:`validate_bag` used everywhere else, so "does the
partner's copy actually work" is answered by evidence rather than assumption.
"""

from __future__ import annotations

import hashlib
import io
import shutil
import tarfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from ledger.bag import validate_bag
from ledger.config import StorageLocation
from ledger.errors import BagValidationError, FixityError, LedgerError, ReplicationError
from ledger.fixity import AuditReport
from ledger.metadata.premis import PremisLog
from ledger.models import PremisEvent, PremisEventType, now_iso
from ledger.tombstones import TombstoneStore

_PREMIS_FILENAME = "premis.json"

_QUARANTINE_DIR = "quarantine"
_SEALED_SUFFIX = ".sealed"

#: The archive-level takedown log a per-location TAKEDOWN receipt is appended to,
#: the same file :meth:`ledger.ingest.Archive.log_takedown` writes the accountable
#: decision to. Kept in sync by name so a receipt and its decision live together.
_TAKEDOWNS_LOG = "takedowns.premis.json"

#: The PREMIS agent recorded on a receipt written by the reattach propagation sweep.
_TOMBSTONE_AGENT = "ledger.replicate.tombstone"


def _is_safe_component(name: str) -> bool:
    """True if ``name`` is a single safe path component (no traversal, no NUL).

    Mirrors the guard in :meth:`ledger.ingest.Archive.remove_all_copies`: this
    module builds the paths it ``rmtree``s from a stored ``record_id``, so a crafted
    id must never be allowed to escape a location's directory (defense in depth on a
    destructive primitive). A real record id is opaque hex and always passes.
    """
    return (
        bool(name)
        and name not in {".", ".."}
        and not any(sep in name for sep in ("/", "\\", "\x00"))
    )


def _append_takedown_receipt(log_path: Path, event: PremisEvent) -> None:
    """Append one TAKEDOWN receipt to the archive takedown log (append-only)."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = PremisLog.read(log_path) if log_path.exists() else PremisLog()
    log.record(event)
    log.write(log_path)


def apply_tombstones(
    locations: Sequence[StorageLocation],
    store: TombstoneStore,
    *,
    agent: str = _TOMBSTONE_AGENT,
    now: str | None = None,
) -> list[PremisEvent]:
    """Enforce every takedown tombstone at each *reachable* location.

    This is the reattach propagation sweep: a replica that was offline when a record
    was taken down still holds the stale copy, and the tombstone remembers that the
    removal is pending there. For each location whose directory is present (reachable)
    this deletes any tombstoned bag it still holds, appends a per-location PREMIS
    ``TAKEDOWN`` receipt naming only the opaque record id and the location, and marks
    that location confirmed in the store — so a copy can never quietly resurrect and
    ``/consent-status`` can report honestly which locations have applied a removal.

    Degradability: a location whose directory is absent is treated as unreachable and
    skipped, leaving its tombstones pending — one offline mirror must not be recorded
    as confirmed. Every sweep also re-checks previously confirmed tombstones. This is
    essential because a restored backup or remounted stale disk can make a deleted
    bag reappear after its first receipt; confirmation is historical evidence, not
    permission to stop enforcing deletion.

    Integrity/no-outing: a stored id that is not a safe path component is skipped
    rather than deleted (it can never be a real id), and receipts carry only the
    opaque id and location name — never a title, field, or identity. ``now`` stamps
    every receipt so runs are reproducible.
    """
    stamp = now if now is not None else now_iso()
    receipts: list[PremisEvent] = []
    takedowns_path = store.logs_dir / _TAKEDOWNS_LOG
    for location in locations:
        root = Path(location.path)
        if not root.exists():
            # Unreachable/offline: leave every tombstone pending here.
            continue
        for tombstone in store.all():
            record_id = tombstone.record_id
            if not _is_safe_component(record_id):
                continue
            replica = root / record_id
            if replica.exists():
                shutil.rmtree(replica)
                event = PremisEvent(
                    event_type=PremisEventType.TAKEDOWN,
                    agent=agent,
                    outcome="success",
                    detail=(
                        f"tombstoned record {record_id!r} removed from location "
                        f"{location.name!r} on reattach"
                    ),
                    linked_object=record_id,
                    event_datetime=stamp,
                )
                _append_takedown_receipt(takedowns_path, event)
                receipts.append(event)
            store.confirm(record_id, location.name, stamp)
    return receipts


def _rejected(message: str, event: PremisEvent) -> ReplicationError:
    """Build a :class:`~ledger.errors.ReplicationError` carrying its quarantine event.

    Failure transparency: the ``QUARANTINE`` preservation event is attached to the
    raised error — both as ``error.quarantine_event`` for direct access and as the
    second positional ``arg`` — so the caller can log the labelled event rather than
    re-deriving why the replica was rejected. The message itself names only the bag
    and location, never any payload content (no-outing).
    """
    error = ReplicationError(message, event)
    # ``setattr`` keeps mypy --strict happy without redefining the exception class
    # (errors.py is the shared contract and must not be edited).
    setattr(error, "quarantine_event", event)  # noqa: B010
    return error


@dataclass(frozen=True)
class ReplicaStatus:
    """The health of one replica of a bag at one location (inspectability).

    ``ok`` is true only when the replica exists, is structurally a bag, every
    payload file matches every manifest digest, and (FIX-06) its PREMIS chain both
    verifies on its own and — when a ``source_head`` was supplied to
    :func:`verify_replicas` — agrees with it. ``report`` is the per-file audit when
    one could be produced, or an empty :class:`~ledger.fixity.AuditReport` when the
    replica was missing or unreadable — so a missing copy and a corrupt copy are
    both representable without raising (failure transparency).

    ``chain_head`` is the replica's own PREMIS chain head (``None`` when the
    replica has no ``premis.json``, e.g. a bag built without preservation events in
    a test fixture). ``chain_ok`` is false when the replica's chain fails to
    verify, or diverges from the ``source_head`` given to :func:`verify_replicas` —
    the cross-copy check a single-replica byte comparison cannot make on its own.
    """

    location: str
    bag: str
    report: AuditReport
    ok: bool
    chain_head: str | None = None
    chain_ok: bool = True


def _replica_path(location_path: str, bag_name: str) -> Path:
    """The on-disk path a bag named ``bag_name`` occupies inside a location."""
    return Path(location_path) / bag_name


def _copy_bag(source: Path, dest: Path) -> None:
    """Copy a whole bag directory ``source`` to ``dest``, replacing any prior copy.

    Replacing first (integrity): a stale or partially written copy at ``dest`` is
    removed before the fresh copy is laid down, so the destination is never a mix
    of two generations of the bag.

    Self-copy guard (data loss): if ``source`` and ``dest`` resolve to the same
    directory, return untouched — deleting ``dest`` first would destroy the very
    bag we meant to copy, potentially the only good replica.
    """
    if source.resolve() == dest.resolve():
        return
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, dest)


def _quarantine(bad_copy: Path, location_path: str) -> Path:
    """Move a failed copy into the location's sibling ``quarantine/`` directory.

    Recoverability / integrity: a copy that failed verification is isolated where
    a read path will not serve it, yet kept (not deleted) so a steward can inspect
    why it diverged. The bad copy displaces any same-named prior quarantine entry.
    Returns the path the copy was moved to.
    """
    quarantine_root = Path(location_path) / _QUARANTINE_DIR
    quarantine_root.mkdir(parents=True, exist_ok=True)
    target = quarantine_root / bad_copy.name
    if target.exists():
        shutil.rmtree(target)
    shutil.move(str(bad_copy), str(target))
    return target


def replicate_bag(
    bag_dir: Path,
    location: StorageLocation,
    *,
    agent: str,
    now: str,
) -> PremisEvent:
    """Copy the bag at ``bag_dir`` into ``location`` and verify it on arrival.

    The bag directory is copied into ``location.path`` under its existing name,
    then re-validated in place. On success a :class:`~ledger.models.PremisEvent` of
    type ``REPLICATION`` with ``outcome="success"`` is returned and recorded.

    Verify-on-arrival (reliability): validation runs at the *destination*, so a
    transfer that corrupted or truncated the copy is caught immediately. On a
    validation failure the bad copy is quarantined (moved to a sibling
    ``quarantine/`` directory) and a ``QUARANTINE`` event with ``outcome="failure"``
    is constructed and attached to a :class:`~ledger.errors.ReplicationError`, which
    is then raised: the condition is surfaced and auditable, never hidden (failure
    transparency). The caller reads the event off ``error.quarantine_event``.

    Determinism: the supplied ``now`` stamps every event, so replication runs are
    reproducible and golden-testable.
    """
    bag_name = bag_dir.name
    dest = _replica_path(location.path, bag_name)
    _copy_bag(bag_dir, dest)

    try:
        report = validate_bag(dest)
    except BagValidationError as exc:
        # Structurally not a valid bag at the destination: quarantine and surface.
        quarantine_path = _quarantine(dest, location.path)
        event = PremisEvent(
            event_type=PremisEventType.QUARANTINE,
            agent=agent,
            outcome="failure",
            detail=(
                f"replica of bag {bag_name!r} at location {location.name!r} failed "
                f"validation on arrival ({exc}); quarantined to {quarantine_path}"
            ),
            linked_object=bag_name,
            event_datetime=now,
        )
        raise _rejected(
            f"replica of bag {bag_name!r} rejected at location {location.name!r}",
            event,
        ) from exc

    if not report.ok:
        # Structurally a bag, but a payload digest drifted: same quarantine path.
        quarantine_path = _quarantine(dest, location.path)
        event = PremisEvent(
            event_type=PremisEventType.QUARANTINE,
            agent=agent,
            outcome="failure",
            detail=(
                f"replica of bag {bag_name!r} at location {location.name!r} failed "
                f"fixity on arrival ({len(report.failed)} file(s)); "
                f"quarantined to {quarantine_path}"
            ),
            linked_object=bag_name,
            event_datetime=now,
        )
        raise _rejected(
            f"replica of bag {bag_name!r} rejected at location {location.name!r}",
            event,
        )

    return PremisEvent(
        event_type=PremisEventType.REPLICATION,
        agent=agent,
        outcome="success",
        detail=(
            f"bag {bag_name!r} replicated to location {location.name!r} and "
            f"verified on arrival ({report.checked} file(s) checked)"
        ),
        linked_object=bag_name,
        event_datetime=now,
    )


def _replica_chain_status(replica: Path, source_head: str | None) -> tuple[bool, str | None]:
    """The ``(chain_ok, chain_head)`` pair for one replica's ``premis.json``.

    No ``premis.json`` (a bag built without preservation events, e.g. in a test
    fixture) is not a chain failure — there is nothing to verify, so ``chain_ok``
    stays true and ``chain_head`` is ``None``. A present log that fails to parse or
    whose stored links no longer verify is a chain failure (FIX-06 tamper
    evidence). When ``source_head`` is given, a replica whose head disagrees with
    it is also a chain failure — the cross-copy check that catches a rewrite which
    stayed locally self-consistent.
    """
    premis_path = replica / _PREMIS_FILENAME
    if not premis_path.exists():
        return True, None
    try:
        verification = PremisLog.read(premis_path).verify_chain()
    except (LedgerError, ValueError, OSError):
        return False, None
    if not verification.ok:
        return False, verification.head
    if source_head is not None and verification.head != source_head:
        return False, verification.head
    return True, verification.head


def verify_replicas(
    bag_name: str,
    locations: Sequence[StorageLocation],
    *,
    source_head: str | None = None,
    tombstones: TombstoneStore | None = None,
    agent: str = _TOMBSTONE_AGENT,
    now: str | None = None,
) -> list[ReplicaStatus]:
    """Validate every replica of ``bag_name`` across ``locations``.

    Returns one :class:`ReplicaStatus` per location, in the order given. A replica
    that is missing or that raises while being read is reported with ``ok=False``
    and an empty audit report rather than aborting the sweep — one unreachable or
    absent mirror must not hide the health of the others (degradability /
    availability, failure transparency). A replica whose files drift is reported
    with ``ok=False`` and the per-file report that proves it (inspectability).

    Also verifies each replica's PREMIS hash chain (FIX-06) — ``premis.json`` is
    copied as part of the bag, so its chain rides along on every transfer for
    free. Pass ``source_head`` (e.g. from
    :meth:`ledger.ingest.Archive.premis_chain_head`) to additionally require every
    replica's chain head to agree with the source's: divergent history then
    surfaces exactly like divergent bytes, even for a replica whose own chain is
    locally self-consistent (the attack a single-copy check cannot catch alone).

    When a :class:`~ledger.tombstones.TombstoneStore` is supplied, pending takedowns
    are applied to every reachable location first (:func:`apply_tombstones`): a
    replica that was offline at takedown time and still holds the stale copy has it
    removed and a per-location TAKEDOWN receipt written *before* verification, so a
    tombstoned bag is honestly reported as gone rather than as a healthy replica.
    """
    if tombstones is not None:
        apply_tombstones(locations, tombstones, agent=agent, now=now)
    statuses: list[ReplicaStatus] = []
    for location in locations:
        replica = _replica_path(location.path, bag_name)
        if not replica.exists():
            statuses.append(
                ReplicaStatus(
                    location=location.name,
                    bag=bag_name,
                    report=AuditReport(results=[]),
                    ok=False,
                )
            )
            continue
        try:
            report = validate_bag(replica)
        except (BagValidationError, OSError):
            # Malformed bag or unreadable location: degrade to not-ok, never raise.
            statuses.append(
                ReplicaStatus(
                    location=location.name,
                    bag=bag_name,
                    report=AuditReport(results=[]),
                    ok=False,
                )
            )
            continue
        chain_ok, chain_head = _replica_chain_status(replica, source_head)
        statuses.append(
            ReplicaStatus(
                location=location.name,
                bag=bag_name,
                report=report,
                ok=report.ok and chain_ok,
                chain_head=chain_head,
                chain_ok=chain_ok,
            )
        )
    return statuses


def heal(
    bag_name: str,
    locations: Sequence[StorageLocation],
    *,
    agent: str,
    now: str,
    tombstones: TombstoneStore | None = None,
) -> list[PremisEvent]:
    """Rebuild every failing or missing replica of ``bag_name`` from a good one.

    Takedown wins over healing (safety): when a
    :class:`~ledger.tombstones.TombstoneStore` is supplied, pending takedowns are
    applied to every reachable location first, and if ``bag_name`` is itself
    tombstoned the heal returns immediately with the removal receipts it produced —
    it must NEVER re-copy a taken-down bag back from a replica that was offline at
    takedown time and still holds a stale copy. Without this guard a takedown could
    be silently undone by the next heal.

    Recoverability / resilience: :func:`verify_replicas` first sorts the replicas
    into trustworthy and untrustworthy. If at least one replica validates, each
    failing or missing replica is overwritten by copying from a verified source and
    re-validated on arrival, and a ``REPLICATION`` event with ``outcome="success"``
    is recorded for each heal performed; the returned list is empty when every
    replica was already healthy (idempotence — healing a healthy fleet is a no-op).

    Integrity: the copy source is always a replica that *just* passed full
    validation, so a divergent copy can never propagate. If **no** replica
    validates there is nothing trustworthy to heal from, and a
    :class:`~ledger.errors.FixityError` is raised rather than blessing a bad copy
    (never trust a divergent copy).

    Resilience: a heal that itself arrives torn is quarantined and recorded as a
    ``QUARANTINE`` failure event, and the sweep CONTINUES to the other targets, so
    one bad destination neither serves a divergent copy nor discards the heals
    already completed. The returned list therefore contains an event per attempted
    heal (success or quarantine).

    Determinism: ``now`` stamps each event; the source replica is chosen as the
    first healthy one in ``locations`` order, so repeated runs are reproducible.
    Locations and their statuses are paired by index (not by name), so two targets
    that happen to share a name are still healed independently and correctly.

    .. warning::
       **Honest limit — heal is fixity-aware, not revision-aware.** "Validates"
       means *internally consistent* (RFC 8493), not *current*. A replica made
       before a lawful :meth:`~ledger.ingest.Archive.apply_update` still fully
       self-validates after the primary bag is updated and resealed, so if the
       newer copies are lost, healing from the stale replica resurrects the
       pre-update ``record.json`` — including a consent/policy state the
       contributor has since *tightened* (a real privacy regression, pinned by
       ``test_heal_from_stale_replica_resurrects_pre_update_manifest``). Until
       heal learns revision ordering, re-replicate promptly after updates and
       treat replica currency as an operational duty, not a property this
       function checks. The reseal's PREMIS ``VALIDATION`` digest transition
       gives an auditor the evidence to *detect* such a resurrection after the
       fact.
    """
    receipts: list[PremisEvent] = []
    if tombstones is not None:
        receipts = apply_tombstones(locations, tombstones, agent=agent, now=now)
        if tombstones.is_tombstoned(bag_name):
            # This bag has been taken down. The sweep above already removed any stale
            # copy from every reachable location; healing it would resurrect it, so we
            # stop here and return only the removal receipts (never a REPLICATION).
            return receipts

    statuses = verify_replicas(bag_name, locations)
    paired = list(zip(locations, statuses, strict=True))

    healthy = [(location, status) for location, status in paired if status.ok]
    if not healthy:
        raise FixityError(
            f"no validating replica of bag {bag_name!r}: nothing trustworthy to heal from"
        )

    source_location, _ = healthy[0]
    source_path = _replica_path(source_location.path, bag_name)

    events: list[PremisEvent] = []
    for target_location, status in paired:
        if status.ok:
            continue
        dest = _replica_path(target_location.path, bag_name)
        if dest.resolve() == source_path.resolve():
            # Same physical copy as the source (e.g. a duplicate location entry):
            # it is already the good bag, nothing to heal.
            continue
        _copy_bag(source_path, dest)

        # Verify-on-arrival again: a heal that itself arrived torn must be
        # quarantined (never left live and servable) — then continue.
        try:
            report = validate_bag(dest)
            torn = not report.ok
            checked = report.checked
        except BagValidationError:
            torn = True
            checked = 0
        if torn:
            quarantine_path = _quarantine(dest, target_location.path)
            events.append(
                PremisEvent(
                    event_type=PremisEventType.QUARANTINE,
                    agent=agent,
                    outcome="failure",
                    detail=(
                        f"heal of bag {bag_name!r} at location {target_location.name!r} "
                        f"arrived torn; quarantined to {quarantine_path}"
                    ),
                    linked_object=bag_name,
                    event_datetime=now,
                )
            )
            continue

        events.append(
            PremisEvent(
                event_type=PremisEventType.REPLICATION,
                agent=agent,
                outcome="success",
                detail=(
                    f"bag {bag_name!r} healed at location {target_location.name!r} "
                    f"from location {source_location.name!r} and verified on arrival "
                    f"({checked} file(s) checked)"
                ),
                linked_object=bag_name,
                event_datetime=now,
            )
        )
    return events


# --- mutual preservation aid: encrypted replica exchange (EXP-15) -----------
#
# A second, opt-in transport alongside the plaintext one above. See the module
# docstring for the shape; in short: seal at home, ship ciphertext, attest by
# digest, recover by decrypting locally and running the same validate_bag used
# everywhere else in the archive.


def _sealed_path(location_path: str, bag_name: str) -> Path:
    """The on-disk path a sealed (ciphertext) blob of ``bag_name`` occupies."""
    return Path(location_path) / f"{bag_name}{_SEALED_SUFFIX}"


def _tar_bag(bag_dir: Path) -> bytes:
    """Pack a bag directory's files into an in-memory tar, deterministically.

    Sorted member order and zeroed mtime/uid/gid/uname make the plaintext tar
    byte-identical across runs on unchanged content, so sealing the same bag with
    the same key twice is reproducible rather than differing on incidental
    filesystem metadata.
    """
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for path in sorted(p for p in bag_dir.rglob("*") if p.is_file()):
            arcname = str(path.relative_to(bag_dir.parent))
            info = tar.gettarinfo(path, arcname=arcname)
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            with path.open("rb") as handle:
                tar.addfile(info, handle)
    return buffer.getvalue()


def _untar_bag(data: bytes, dest_parent: Path) -> Path:
    """Unpack a tar produced by :func:`_tar_bag` under ``dest_parent``.

    Every member must extract inside ``dest_parent`` — a member whose name tries
    to escape via ``../`` or an absolute path is rejected before anything is
    written to disk (integrity). Fernet authentication already rules out a
    tampered blob reaching here, but this guards the unseal path regardless of
    where the bytes came from.
    """
    dest_parent.mkdir(parents=True, exist_ok=True)
    resolved_root = dest_parent.resolve()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r") as tar:
        members = tar.getmembers()
        bag_names: set[str] = set()
        for member in members:
            member_path = (dest_parent / member.name).resolve()
            if member_path != resolved_root and resolved_root not in member_path.parents:
                raise ReplicationError(
                    f"sealed archive member {member.name!r} would extract outside destination"
                )
            bag_names.add(member.name.split("/", 1)[0])
        tar.extractall(dest_parent, members=members, filter="data")
    if len(bag_names) != 1:
        raise ReplicationError("sealed archive does not contain exactly one bag directory")
    return dest_parent / next(iter(bag_names))


@dataclass(frozen=True)
class SealedBag:
    """The result of sealing one bag: its ciphertext plus the digest to expect back.

    ``sealed_sha256`` is what a later :func:`attest_sealed_replica` must reproduce
    for the exchange to be considered healthy. The owning instance is expected to
    keep this digest — never the ciphertext, and never the key — alongside its own
    records.
    """

    bag: str
    ciphertext: bytes
    sealed_sha256: str


@dataclass(frozen=True)
class SealedFixityAttestation:
    """A digest-only claim about the ciphertext a location currently holds.

    ``sealed_sha256`` is computed straight off the bytes on disk at ``location`` —
    never decrypted, never even attempted — so a partner instance that has *never*
    held the key can produce this attestation about its own storage. Compared
    against the digest recorded at seal time (:class:`SealedBag.sealed_sha256`),
    it proves the partner still holds exactly what was sent: this is the
    "scheduled fixity attestation exchange" from the EXP-15 pitch (inspectability,
    no-outing — the attestation never touches plaintext).
    """

    location: str
    bag: str
    sealed_sha256: str
    exists: bool
    checked_at: str


def seal_bag(bag_dir: Path, key: bytes) -> SealedBag:
    """Encrypt the whole bag at ``bag_dir`` into one authenticated ciphertext blob.

    Key stays home (confidentiality): ``key`` is a Fernet key the *owning*
    instance generates and keeps. It is never written into the blob, never sent to
    a partner, and this function neither logs nor returns it. The returned
    :class:`SealedBag.sealed_sha256` is the only thing a partner needs to prove,
    later, that they still hold the right bytes (see :func:`attest_sealed_replica`).

    Raises :class:`~ledger.errors.ReplicationError` if the key is malformed —
    never proceeds with an unusable key.
    """
    try:
        fernet = Fernet(key)
    except (ValueError, TypeError) as exc:
        raise ReplicationError("invalid mutual-aid seal key") from exc
    plaintext = _tar_bag(bag_dir)
    ciphertext = fernet.encrypt(plaintext)
    digest = hashlib.sha256(ciphertext).hexdigest()
    return SealedBag(bag=bag_dir.name, ciphertext=ciphertext, sealed_sha256=digest)


def unseal_bag(ciphertext: bytes, key: bytes, dest_parent: Path) -> Path:
    """Decrypt a blob produced by :func:`seal_bag` and unpack it under ``dest_parent``.

    Authenticated decryption (integrity): Fernet rejects any ciphertext that was
    truncated, corrupted, or tampered with in transit or at rest — a bad blob
    raises :class:`~ledger.errors.ReplicationError` rather than silently yielding
    garbage bytes that might be mistaken for a bag. Returns the path of the
    recovered bag directory, ready for :func:`~ledger.bag.validate_bag`.
    """
    try:
        fernet = Fernet(key)
    except (ValueError, TypeError) as exc:
        raise ReplicationError("invalid mutual-aid seal key") from exc
    try:
        plaintext = fernet.decrypt(ciphertext)
    except InvalidToken as exc:
        raise ReplicationError(
            "sealed replica failed authentication on unseal (wrong key or tampered ciphertext)"
        ) from exc
    return _untar_bag(plaintext, dest_parent)


def replicate_sealed_bag(
    bag_dir: Path,
    location: StorageLocation,
    key: bytes,
    *,
    agent: str,
    now: str,
) -> tuple[PremisEvent, str]:
    """Seal the bag at ``bag_dir`` and hold the ciphertext at a partner ``location``.

    Distinct from :func:`replicate_bag`: ``location`` receives only ciphertext, so
    a partner instance can hold this replica as redundancy without ever being able
    to read its contents — closing the §4.5 threat-model residual that a hostile
    or compromised replica host can read what it stores. Verify-on-arrival is
    adapted for ciphertext: the blob is re-read from disk and re-hashed
    immediately after writing, so a write truncated or corrupted by the storage
    medium is caught here rather than at the next scheduled attestation.

    Returns the recorded event and the ciphertext's SHA-256 digest. The caller
    (steward tooling, a cron job) is responsible for keeping that digest — never
    the key or the ciphertext — alongside their own records, so it can later be
    checked against attestations from the partner.
    """
    sealed = seal_bag(bag_dir, key)
    dest = _sealed_path(location.path, sealed.bag)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_bytes(sealed.ciphertext)
    tmp.replace(dest)

    on_disk_digest = hashlib.sha256(dest.read_bytes()).hexdigest()
    if on_disk_digest != sealed.sealed_sha256:
        # Arrived torn: same quarantine discipline as the plaintext path, but the
        # "bag" here is a single opaque blob rather than a directory.
        quarantine_root = Path(location.path) / _QUARANTINE_DIR
        quarantine_root.mkdir(parents=True, exist_ok=True)
        quarantine_target = quarantine_root / dest.name
        if quarantine_target.exists():
            quarantine_target.unlink()
        dest.replace(quarantine_target)
        event = PremisEvent(
            event_type=PremisEventType.QUARANTINE,
            agent=agent,
            outcome="failure",
            detail=(
                f"sealed replica of bag {sealed.bag!r} at location {location.name!r} "
                f"arrived with a mismatched digest; quarantined to {quarantine_target}"
            ),
            linked_object=sealed.bag,
            event_datetime=now,
        )
        raise _rejected(
            f"sealed replica of bag {sealed.bag!r} rejected at location {location.name!r}",
            event,
        )

    event = PremisEvent(
        event_type=PremisEventType.REPLICATION,
        agent=agent,
        outcome="success",
        detail=(
            f"bag {sealed.bag!r} sealed and replicated to location {location.name!r} "
            f"as an encrypted blob ({len(sealed.ciphertext)} byte(s)); partner cannot "
            "read the contents"
        ),
        linked_object=sealed.bag,
        event_datetime=now,
    )
    return event, sealed.sealed_sha256


def attest_sealed_replica(
    location: StorageLocation,
    bag_name: str,
    *,
    now: str,
) -> SealedFixityAttestation:
    """Report the SHA-256 of the ciphertext blob a partner ``location`` holds.

    Never decrypts, never requests the key: this is exactly what a partner
    instance runs on a schedule to prove, to the owner, which bytes it currently
    holds — the "scheduled fixity attestation exchange" from the EXP-15 pitch.
    ``exists=False`` (with an empty digest) reports a missing blob rather than
    raising, matching :func:`verify_replicas`'s degradability rule: an
    unreachable or empty partner must not blind either side to its status.
    """
    path = _sealed_path(location.path, bag_name)
    if not path.exists():
        return SealedFixityAttestation(
            location=location.name,
            bag=bag_name,
            sealed_sha256="",
            exists=False,
            checked_at=now,
        )
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return SealedFixityAttestation(
        location=location.name,
        bag=bag_name,
        sealed_sha256=digest,
        exists=True,
        checked_at=now,
    )


def verify_sealed_attestation(expected_sha256: str, attestation: SealedFixityAttestation) -> bool:
    """True only if ``attestation`` reports the exact digest recorded at seal time.

    The owning instance calls this after requesting an attestation from a partner
    (or after checking its own copy): a mismatch means the partner's copy has
    drifted, been substituted, or gone missing, and :func:`recover_sealed_bag`
    should be run — as, or before, a real loss makes it necessary.
    """
    return attestation.exists and attestation.sealed_sha256 == expected_sha256


def recover_sealed_bag(
    location: StorageLocation,
    bag_name: str,
    key: bytes,
    dest_parent: Path,
) -> AuditReport:
    """Recovery drill: pull the sealed blob back from ``location``, decrypt it
    locally, and validate it as a bag.

    This is the proof the EXP-15 pitch asks for — "a full recovery drill from a
    partner's copy" — exercised the way a real recovery would be: read whatever
    ciphertext the partner is holding, decrypt with the key that never left home,
    and run it through the exact :func:`~ledger.bag.validate_bag` used everywhere
    else in the archive. Raises :class:`~ledger.errors.ReplicationError` if no
    sealed blob is present at ``location`` (nothing to recover) or if it fails
    authentication (see :func:`unseal_bag`).
    """
    path = _sealed_path(location.path, bag_name)
    if not path.exists():
        raise ReplicationError(
            f"no sealed replica of bag {bag_name!r} at location {location.name!r} to recover"
        )
    ciphertext = path.read_bytes()
    bag_dir = unseal_bag(ciphertext, key, dest_parent)
    return validate_bag(bag_dir)
