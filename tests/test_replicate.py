"""Tests for :mod:`ledger.replicate` — verify-on-arrival, quarantine, and heal.

Covers replicating a bag and verifying every replica is healthy, the heal cycle that
restores a corrupted replica from a copy that still validates, and the degradability
rule that an unreachable location is *reported* (``ok=False``) rather than raising —
one offline mirror must not blind the steward to the others. Also covers the EXP-15
mutual preservation aid transport: sealing a bag into an encrypted blob, replicating
that blob to a partner location, attesting to it by digest without decrypting, and
running a full recovery drill.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from ledger.bag import Bag, refresh_tag_manifests, write_bag
from ledger.config import StorageLocation
from ledger.errors import FixityError, ReplicationError
from ledger.models import PremisEvent, PremisEventType
from ledger.replicate import (
    attest_sealed_replica,
    heal,
    recover_sealed_bag,
    replicate_bag,
    replicate_sealed_bag,
    seal_bag,
    unseal_bag,
    verify_replicas,
    verify_sealed_attestation,
)

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
def test_heal_from_stale_replica_resurrects_pre_update_manifest(
    tmp_path: Path,
    write_file: Callable[[str, bytes], Path],
    sample_bytes: bytes,
) -> None:
    """Honest-limit pin: heal is fixity-aware, not revision-aware (FIX-01 residual).

    A replica made *before* a lawful update still fully self-validates after the
    primary is updated and resealed — staleness is invisible to RFC 8493 fixity.
    If the current copies are lost, healing from the stale replica resurrects the
    pre-update ``record.json``, including a consent state the contributor has
    since tightened. This test documents that residual (see the ``heal``
    docstring's warning) so a future revision-aware heal has a behavior to flip.
    """
    payload = {"photo.jpg": write_file("src/photo.jpg", sample_bytes)}
    bag = write_bag(
        tmp_path / "bag-stale",
        payload,
        extra_tag_files={"record.json": b'{"policy": "public"}'},
    )
    stale = _location(tmp_path, "mirror-stale")
    current = _location(tmp_path, "mirror-current")
    replicate_bag(bag.path, stale, agent=_AGENT, now=_NOW)

    # A lawful consent tightening on the primary: rewrite the tag file + reseal.
    (bag.path / "record.json").write_bytes(b'{"policy": "stewards"}')
    refresh_tag_manifests(bag.path)
    replicate_bag(bag.path, current, agent=_AGENT, now=_NOW)

    # The stale replica still fully self-validates: staleness is not detectable
    # by fixity alone.
    statuses = verify_replicas(bag.name, [stale, current])
    assert all(status.ok for status in statuses)

    # Lose the current replica; heal rebuilds it from the first healthy replica
    # in order — the stale one.
    corrupt = Path(current.path) / bag.name / "record.json"
    corrupt.write_bytes(b"torn")
    heal(bag.name, [stale, current], agent=_AGENT, now=_NOW)

    healed = (Path(current.path) / bag.name / "record.json").read_bytes()
    assert healed == b'{"policy": "public"}'  # the pre-update state came back


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


# --- mutual preservation aid: encrypted replica exchange (EXP-15) -----------


@pytest.mark.preservation
def test_seal_then_unseal_round_trips_to_a_valid_bag(tmp_path: Path, source_bag: Bag) -> None:
    """Sealing a bag then unsealing it with the same key recovers a validating bag."""
    key = Fernet.generate_key()
    sealed = seal_bag(source_bag.path, key)
    assert sealed.bag == source_bag.name
    assert sealed.sealed_sha256

    dest_parent = tmp_path / "recovered"
    bag_dir = unseal_bag(sealed.ciphertext, key, dest_parent)
    assert bag_dir.name == source_bag.name
    assert (bag_dir / "bagit.txt").exists()
    assert (bag_dir / "data" / "photo.jpg").read_bytes() == (
        source_bag.payload_dir / "photo.jpg"
    ).read_bytes()


@pytest.mark.preservation
def test_seal_is_confidential_partner_cannot_read_plaintext(
    tmp_path: Path, source_bag: Bag
) -> None:
    """The sealed blob is opaque ciphertext: no payload byte appears verbatim.

    No-outing / confidentiality: a partner holding only the ciphertext (never the
    key) cannot recover the plaintext photo bytes by inspecting the blob.
    """
    key = Fernet.generate_key()
    sealed = seal_bag(source_bag.path, key)
    payload = (source_bag.payload_dir / "photo.jpg").read_bytes()
    assert payload not in sealed.ciphertext


@pytest.mark.preservation
def test_unseal_wrong_key_fails_authentication(tmp_path: Path, source_bag: Bag) -> None:
    """Unsealing with the wrong key raises rather than yielding garbage bytes."""
    key = Fernet.generate_key()
    wrong_key = Fernet.generate_key()
    sealed = seal_bag(source_bag.path, key)

    with pytest.raises(ReplicationError, match="authentication"):
        unseal_bag(sealed.ciphertext, wrong_key, tmp_path / "recovered")


@pytest.mark.preservation
def test_replicate_sealed_bag_then_attest_matches_digest(tmp_path: Path, source_bag: Bag) -> None:
    """Replicating a sealed bag to a partner, then attesting, reproduces the digest.

    Models the scheduled fixity attestation exchange: the partner (who never
    receives the key) computes a digest purely from the ciphertext bytes on disk,
    and it matches what the owner recorded when it sealed the bag.
    """
    key = Fernet.generate_key()
    partner = _location(tmp_path, "partner-a")

    event, sealed_sha256 = replicate_sealed_bag(
        source_bag.path, partner, key, agent=_AGENT, now=_NOW
    )
    assert event.event_type is PremisEventType.REPLICATION
    assert event.outcome == "success"

    attestation = attest_sealed_replica(partner, source_bag.name, now=_NOW)
    assert attestation.exists
    assert attestation.location == "partner-a"
    assert verify_sealed_attestation(sealed_sha256, attestation)


@pytest.mark.preservation
def test_attest_missing_sealed_replica_reports_not_raises(tmp_path: Path) -> None:
    """Attesting a location that never received a sealed blob degrades, not raises.

    Degradability/availability, matching :func:`verify_replicas`: an unreachable or
    never-populated partner must not blind either side to the exchange's status.
    """
    partner = _location(tmp_path, "partner-offline")
    attestation = attest_sealed_replica(partner, "bag-001", now=_NOW)
    assert not attestation.exists
    assert attestation.sealed_sha256 == ""
    assert not verify_sealed_attestation("deadbeef", attestation)


@pytest.mark.preservation
def test_attest_detects_drifted_or_substituted_ciphertext(tmp_path: Path, source_bag: Bag) -> None:
    """A partner's ciphertext that has drifted no longer matches the sealed digest."""
    key = Fernet.generate_key()
    partner = _location(tmp_path, "partner-drift")
    _, sealed_sha256 = replicate_sealed_bag(source_bag.path, partner, key, agent=_AGENT, now=_NOW)

    # Simulate the partner's copy silently drifting (bit flip, substitution, etc.).
    blob_path = Path(partner.path) / f"{source_bag.name}.sealed"
    raw = bytearray(blob_path.read_bytes())
    raw[0] ^= 0x01
    blob_path.write_bytes(bytes(raw))

    attestation = attest_sealed_replica(partner, source_bag.name, now=_NOW)
    assert attestation.exists
    assert not verify_sealed_attestation(sealed_sha256, attestation)


@pytest.mark.preservation
def test_recover_sealed_bag_full_drill_validates(tmp_path: Path, source_bag: Bag) -> None:
    """The EXP-15 "Excellent" bar: a full recovery drill from a partner's copy.

    Pulls the sealed blob back from the partner, decrypts locally with the key
    that never left home, and validates it as a real bag via the same
    :func:`~ledger.bag.validate_bag` path used everywhere else.
    """
    key = Fernet.generate_key()
    partner = _location(tmp_path, "partner-recover")
    replicate_sealed_bag(source_bag.path, partner, key, agent=_AGENT, now=_NOW)

    report = recover_sealed_bag(partner, source_bag.name, key, tmp_path / "drill")
    assert report.ok
    assert report.checked > 0


@pytest.mark.preservation
def test_recover_sealed_bag_missing_raises(tmp_path: Path) -> None:
    """Recovering from a partner that never held a sealed blob raises, not silently no-ops."""
    key = Fernet.generate_key()
    partner = _location(tmp_path, "partner-empty")

    with pytest.raises(ReplicationError, match="no sealed replica"):
        recover_sealed_bag(partner, "bag-001", key, tmp_path / "drill")


@pytest.mark.preservation
def test_seal_bag_rejects_a_malformed_key(tmp_path: Path, source_bag: Bag) -> None:
    """Sealing with a malformed (non-Fernet) key raises rather than proceeding."""
    with pytest.raises(ReplicationError, match="invalid mutual-aid seal key"):
        seal_bag(source_bag.path, b"not-a-real-fernet-key")


@pytest.mark.preservation
def test_unseal_bag_rejects_a_malformed_key(tmp_path: Path, source_bag: Bag) -> None:
    """Unsealing with a malformed (non-Fernet) key raises rather than proceeding."""
    sealed = seal_bag(source_bag.path, Fernet.generate_key())
    with pytest.raises(ReplicationError, match="invalid mutual-aid seal key"):
        unseal_bag(sealed.ciphertext, b"not-a-real-fernet-key", tmp_path / "recovered")


@pytest.mark.preservation
def test_replicate_sealed_bag_quarantines_a_mismatched_write(
    tmp_path: Path, source_bag: Bag, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A digest recorded at seal time that disagrees with what landed on disk is
    quarantined rather than trusted (verify-on-arrival, adapted for ciphertext).

    Simulates an on-disk write that diverged from what was sealed (e.g. storage
    medium corruption) by having ``seal_bag`` report a digest that does not match
    the ciphertext it actually returns.
    """
    from ledger import replicate as replicate_module

    real_seal_bag = replicate_module.seal_bag

    def _lying_seal_bag(bag_dir: Path, key: bytes) -> replicate_module.SealedBag:
        sealed = real_seal_bag(bag_dir, key)
        return replicate_module.SealedBag(
            bag=sealed.bag, ciphertext=sealed.ciphertext, sealed_sha256="0" * 64
        )

    monkeypatch.setattr(replicate_module, "seal_bag", _lying_seal_bag)

    partner = _location(tmp_path, "partner-torn")
    with pytest.raises(ReplicationError) as excinfo:
        replicate_sealed_bag(
            source_bag.path, partner, Fernet.generate_key(), agent=_AGENT, now=_NOW
        )

    event = excinfo.value.args[1]
    assert event.event_type is PremisEventType.QUARANTINE
    assert event.outcome == "failure"
    assert (Path(partner.path) / "quarantine" / f"{source_bag.name}.sealed").exists()
    assert not (Path(partner.path) / f"{source_bag.name}.sealed").exists()


@pytest.mark.preservation
def test_untar_bag_rejects_path_traversal(tmp_path: Path) -> None:
    """A crafted archive whose member escapes the destination is rejected.

    Defense in depth: Fernet authentication already rules out a tampered blob
    reaching this point in the real seal/unseal flow, but the guard holds
    regardless of how the bytes arrived here.
    """
    import io
    import tarfile

    from ledger.replicate import _untar_bag

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        info = tarfile.TarInfo(name="../escape/evil.txt")
        payload = b"nope"
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))

    with pytest.raises(ReplicationError, match="escape"):
        _untar_bag(buffer.getvalue(), tmp_path / "dest")


@pytest.mark.preservation
def test_untar_bag_rejects_more_than_one_bag_directory(tmp_path: Path) -> None:
    """A crafted archive with two top-level directories is rejected as ambiguous."""
    import io
    import tarfile

    from ledger.replicate import _untar_bag

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for name in ("bag-a/data/x.txt", "bag-b/data/y.txt"):
            info = tarfile.TarInfo(name=name)
            payload = b"hi"
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))

    with pytest.raises(ReplicationError, match="exactly one bag directory"):
        _untar_bag(buffer.getvalue(), tmp_path / "dest")
