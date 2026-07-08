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
"""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ledger.bag import validate_bag
from ledger.config import StorageLocation
from ledger.errors import BagValidationError, FixityError, LedgerError, ReplicationError
from ledger.fixity import AuditReport
from ledger.metadata.premis import PremisLog
from ledger.models import PremisEvent, PremisEventType

_PREMIS_FILENAME = "premis.json"

_QUARANTINE_DIR = "quarantine"


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
    """
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
) -> list[PremisEvent]:
    """Rebuild every failing or missing replica of ``bag_name`` from a good one.

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
    """
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
