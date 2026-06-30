"""Tests for :mod:`ledger.replicate` — verify-on-arrival, quarantine, and heal.

Covers replicating a bag and verifying every replica is healthy, the heal cycle that
restores a corrupted replica from a copy that still validates, and the degradability
rule that an unreachable location is *reported* (``ok=False``) rather than raising —
one offline mirror must not blind the steward to the others.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from ledger.bag import Bag, write_bag
from ledger.config import StorageLocation
from ledger.errors import FixityError, ReplicationError
from ledger.models import PremisEvent, PremisEventType
from ledger.replicate import heal, replicate_bag, verify_replicas

_NOW = "2026-01-01T00:00:00Z"
_AGENT = "ledger.replicate"


@pytest.fixture
def source_bag(
    tmp_path: Path,
    write_file: Callable[[str, bytes], Path],
    sample_bytes: bytes,
    other_bytes: bytes,
) -> Bag:
    """A valid source bag named ``bag-001`` with two payload files."""
    payload = {
        "photo.jpg": write_file("src/photo.jpg", sample_bytes),
        "story.txt": write_file("src/story.txt", other_bytes),
    }
    return write_bag(tmp_path / "bag-001", payload, bag_info={"Bagging-Date": "2026-01-01"})


def _location(tmp_path: Path, name: str) -> StorageLocation:
    """A mirror location backed by its own directory under ``tmp_path``."""
    return StorageLocation(name=name, path=str(tmp_path / name), kind="mirror")


@pytest.mark.preservation
def test_replicate_bag_then_verify_ok(tmp_path: Path, source_bag: Bag) -> None:
    """Replicating a bag yields a success event and a verified replica."""
    loc = _location(tmp_path, "mirror-a")
    event = replicate_bag(source_bag.path, loc, agent=_AGENT, now=_NOW)
    assert isinstance(event, PremisEvent)
    assert event.event_type is PremisEventType.REPLICATION
    assert event.outcome == "success"
    assert event.event_datetime == _NOW

    statuses = verify_replicas(source_bag.name, [loc])
    assert len(statuses) == 1
    assert statuses[0].ok
    assert statuses[0].location == "mirror-a"


@pytest.mark.preservation
def test_replicate_to_two_locations_both_verify(tmp_path: Path, source_bag: Bag) -> None:
    """Two replicas of the same bag both validate independently."""
    locs = [_location(tmp_path, "mirror-a"), _location(tmp_path, "mirror-b")]
    for loc in locs:
        replicate_bag(source_bag.path, loc, agent=_AGENT, now=_NOW)
    statuses = verify_replicas(source_bag.name, locs)
    assert all(status.ok for status in statuses)
    assert [s.location for s in statuses] == ["mirror-a", "mirror-b"]


@pytest.mark.preservation
def test_corrupt_replica_then_heal_restores_it(tmp_path: Path, source_bag: Bag) -> None:
    """Corrupting one replica's payload, then healing, restores it from a good copy.

    Recoverability: heal copies only from a replica that *just* validated, then
    re-validates on arrival, so a healthy fleet is the end state and no divergent
    copy can propagate.
    """
    good = _location(tmp_path, "mirror-good")
    bad = _location(tmp_path, "mirror-bad")
    replicate_bag(source_bag.path, good, agent=_AGENT, now=_NOW)
    replicate_bag(source_bag.path, bad, agent=_AGENT, now=_NOW)

    # Flip a byte in the bad replica's payload so it fails fixity.
    corrupt = Path(bad.path) / source_bag.name / "data" / "photo.jpg"
    raw = bytearray(corrupt.read_bytes())
    raw[0] ^= 0x01
    corrupt.write_bytes(bytes(raw))

    before = verify_replicas(source_bag.name, [good, bad])
    assert before[0].ok
    assert not before[1].ok

    events = heal(source_bag.name, [good, bad], agent=_AGENT, now=_NOW)
    assert len(events) == 1
    assert events[0].event_type is PremisEventType.REPLICATION
    assert events[0].outcome == "success"

    after = verify_replicas(source_bag.name, [good, bad])
    assert all(status.ok for status in after)


@pytest.mark.preservation
def test_heal_missing_replica_rebuilds_it(tmp_path: Path, source_bag: Bag) -> None:
    """A wholly missing replica is rebuilt from a healthy one and then validates."""
    good = _location(tmp_path, "mirror-good")
    absent = _location(tmp_path, "mirror-absent")
    replicate_bag(source_bag.path, good, agent=_AGENT, now=_NOW)

    before = verify_replicas(source_bag.name, [good, absent])
    assert before[0].ok
    assert not before[1].ok  # never replicated to absent

    events = heal(source_bag.name, [good, absent], agent=_AGENT, now=_NOW)
    assert len(events) == 1

    after = verify_replicas(source_bag.name, [good, absent])
    assert all(status.ok for status in after)


@pytest.mark.preservation
def test_heal_is_noop_when_all_healthy(tmp_path: Path, source_bag: Bag) -> None:
    """Healing an already-healthy fleet performs no copies (idempotence)."""
    locs = [_location(tmp_path, "mirror-a"), _location(tmp_path, "mirror-b")]
    for loc in locs:
        replicate_bag(source_bag.path, loc, agent=_AGENT, now=_NOW)
    assert heal(source_bag.name, locs, agent=_AGENT, now=_NOW) == []


@pytest.mark.preservation
def test_heal_raises_when_no_replica_validates(tmp_path: Path, source_bag: Bag) -> None:
    """With no trustworthy source, heal raises rather than blessing a bad copy.

    Never trust a divergent copy: there is nothing safe to copy *from*, so a
    :class:`FixityError` is raised.
    """
    only = _location(tmp_path, "mirror-only")
    replicate_bag(source_bag.path, only, agent=_AGENT, now=_NOW)
    corrupt = Path(only.path) / source_bag.name / "data" / "photo.jpg"
    raw = bytearray(corrupt.read_bytes())
    raw[0] ^= 0x01
    corrupt.write_bytes(bytes(raw))

    with pytest.raises(FixityError, match="nothing trustworthy"):
        heal(source_bag.name, [only], agent=_AGENT, now=_NOW)


@pytest.mark.preservation
def test_unreachable_location_reported_not_raised(tmp_path: Path, source_bag: Bag) -> None:
    """A location with no replica is reported ``ok=False``, never raising.

    Degradability/availability: one offline or never-populated mirror must not blind
    the steward to the health of the others.
    """
    good = _location(tmp_path, "mirror-good")
    unreachable = _location(tmp_path, "mirror-offline")  # directory never created
    replicate_bag(source_bag.path, good, agent=_AGENT, now=_NOW)

    statuses = verify_replicas(source_bag.name, [good, unreachable])
    assert statuses[0].ok
    assert not statuses[1].ok
    assert statuses[1].location == "mirror-offline"
    assert statuses[1].report.results == []  # empty report, not an exception


@pytest.mark.preservation
def test_replicate_corrupt_bag_quarantines_and_raises(
    tmp_path: Path,
    write_file: Callable[[str, bytes], Path],
    sample_bytes: bytes,
) -> None:
    """A source bag that is corrupt before replication is quarantined on arrival.

    The failure is surfaced (a :class:`ReplicationError`) with its QUARANTINE event
    attached, and the bad copy is moved aside into a sibling ``quarantine/`` dir
    rather than served (failure transparency, recoverability).
    """
    payload = {"photo.jpg": write_file("src2/photo.jpg", sample_bytes)}
    bag = write_bag(tmp_path / "bag-002", payload, bag_info={"Bagging-Date": "2026-01-01"})
    # Corrupt the source payload so verify-on-arrival fails at the destination.
    target = bag.payload_dir / "photo.jpg"
    raw = bytearray(target.read_bytes())
    raw[0] ^= 0x01
    target.write_bytes(bytes(raw))

    loc = _location(tmp_path, "mirror-q")
    with pytest.raises(ReplicationError) as excinfo:
        replicate_bag(bag.path, loc, agent=_AGENT, now=_NOW)

    # The QUARANTINE event is attached both as ``error.quarantine_event`` and as the
    # second positional arg; the latter avoids a dynamic-attribute access in the test.
    event = excinfo.value.args[1]
    assert isinstance(event, PremisEvent)
    assert event.event_type is PremisEventType.QUARANTINE
    assert event.outcome == "failure"
    # The bad copy is isolated under quarantine/, not left at the live replica path.
    assert (Path(loc.path) / "quarantine" / bag.name).exists()
    assert not (Path(loc.path) / bag.name).exists()


@pytest.mark.preservation
def test_truncated_replica_is_reported_then_healed(tmp_path: Path, source_bag: Bag) -> None:
    """A replica truncated mid-transfer fails verify and is rebuilt from a good copy.

    Models a transfer interrupted partway through a payload file: one file arrives
    truncated to zero length. The divergent copy is never trusted — ``verify_replicas``
    degrades it to ``ok=False`` (the manifest digest no longer matches) and ``heal``
    restores it from the replica that still validates.
    """
    good = _location(tmp_path, "mirror-good")
    torn = _location(tmp_path, "mirror-torn")
    replicate_bag(source_bag.path, good, agent=_AGENT, now=_NOW)
    replicate_bag(source_bag.path, torn, agent=_AGENT, now=_NOW)

    # Truncate one payload file, as if the byte stream was cut off mid-copy.
    target = Path(torn.path) / source_bag.name / "data" / "photo.jpg"
    target.write_bytes(b"")

    before = verify_replicas(source_bag.name, [good, torn])
    assert before[0].ok
    assert not before[1].ok  # the truncated copy is not promoted to trusted

    events = heal(source_bag.name, [good, torn], agent=_AGENT, now=_NOW)
    assert len(events) == 1
    assert events[0].outcome == "success"

    after = verify_replicas(source_bag.name, [good, torn])
    assert all(status.ok for status in after)


@pytest.mark.preservation
def test_structurally_partial_replica_is_not_promoted(tmp_path: Path, source_bag: Bag) -> None:
    """A replica missing a required bag file (interrupted early) is reported, not trusted.

    A transfer that stopped before a structural file arrived is not a valid bag at
    all. ``verify_replicas`` degrades it to ``ok=False`` rather than raising (one
    partial copy must not blind the steward to the others), and ``heal`` rebuilds it.
    """
    good = _location(tmp_path, "mirror-good")
    partial = _location(tmp_path, "mirror-partial")
    replicate_bag(source_bag.path, good, agent=_AGENT, now=_NOW)
    replicate_bag(source_bag.path, partial, agent=_AGENT, now=_NOW)

    # Remove a required structural file, as if the transfer ended before it arrived.
    (Path(partial.path) / source_bag.name / "bagit.txt").unlink()

    before = verify_replicas(source_bag.name, [good, partial])
    assert before[0].ok
    assert not before[1].ok

    events = heal(source_bag.name, [good, partial], agent=_AGENT, now=_NOW)
    assert len(events) == 1

    after = verify_replicas(source_bag.name, [good, partial])
    assert all(status.ok for status in after)
