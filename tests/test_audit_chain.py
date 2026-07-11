"""Archive-level tests for FIX-06: PREMIS chain verification, archive-wide log
chains, chain heads, and cross-replica chain-head comparison.

These exercise :class:`~ledger.ingest.Archive`'s new surface
(``audit_fixity`` folding in chain checks, ``audit_log_chains``,
``premis_chain_heads``/``premis_chain_head``, ``chain_head_summary``) and
:func:`ledger.replicate.verify_replicas`'s ``source_head`` comparison, using the
same ingest pattern as ``tests/test_ingest_e2e.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

from ledger.chain import GENESIS_HASH
from ledger.config import Config, StorageLocation
from ledger.fixity import hash_file
from ledger.identity import ContributorIdentity
from ledger.ingest import Archive
from ledger.metadata.premis import PremisLog
from ledger.models import (
    AccessPolicy,
    DublinCore,
    Field,
    HashAlgo,
    PremisEvent,
    PremisEventType,
    Record,
)
from ledger.replicate import replicate_bag, verify_replicas

_VAULT_KEY = b"0123456789abcdef0123456789abcdef0123456789a="
_NOW = "2026-06-16T12:00:00Z"
_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _reseal_tagmanifests(bag_dir: Path) -> None:
    """Recompute every ``tagmanifest-<algo>.txt`` from the bag's current tag files.

    A test-local stand-in for what a sophisticated attacker with the archive's own
    hashing logic (but no dedicated "reseal" tool) could do by hand after editing a
    tag file directly: keeps each manifest's existing list of tag-file paths, just
    recomputes their digests, so byte-level BagIt fixity passes again.
    """
    for tagmanifest_path in sorted(bag_dir.glob("tagmanifest-*.txt")):
        algo = HashAlgo(tagmanifest_path.stem.split("-", 1)[1])
        entries: list[tuple[str, str]] = []
        for raw in tagmanifest_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line:
                continue
            _digest, _, relpath = line.partition("  ")
            entries.append((relpath, hash_file(bag_dir / relpath, algo)))
        body = "".join(f"{digest}  {relpath}\n" for relpath, digest in entries)
        tagmanifest_path.write_text(body, encoding="utf-8", newline="\n")


def _record() -> Record:
    return Record(
        title="Chain test record",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(
            title=["Chain test record"],
            publisher=["Test Archive"],
            type=["oral history"],
            language=["en"],
        ),
        fields=[Field(name="story", value="A public account.", policy=AccessPolicy.PUBLIC)],
    )


def _ingest(archive: Archive) -> tuple[str, Path]:
    payload = _FIXTURES / "public.txt"
    record = _record()
    aip = archive.ingest(
        {payload.name: payload},
        record,
        identity=ContributorIdentity(name="Test Contributor"),
        vault_key=_VAULT_KEY,
        agent="test-steward",
        now=_NOW,
    )
    return record.record_id, aip.premis_path


def test_audit_fixity_passes_on_untampered_chain(tmp_path: Path) -> None:
    archive = Archive.init(Config.default("Chain Archive", tmp_path / "archive"))
    _ingest(archive)
    reports = archive.audit_fixity()
    assert len(reports) == 1
    _name, report = reports[0]
    assert report.ok


def test_audit_fixity_catches_rewritten_history_after_reseal(tmp_path: Path) -> None:
    """A rewritten ``premis.json``, resealed with the archive's own tooling,
    still fails ``audit_fixity`` via the hash-chain check (FIX-06)."""
    archive = Archive.init(Config.default("Chain Archive", tmp_path / "archive"))
    rid, premis_path = _ingest(archive)

    data = json.loads(premis_path.read_text(encoding="utf-8"))
    data["entries"][0]["eventDetail"] = "rewritten by an attacker with disk access"
    premis_path.write_text(json.dumps(data, sort_keys=True, separators=(",", ":")))
    _reseal_tagmanifests(archive.bags_dir / rid)

    reports = archive.audit_fixity()
    _name, report = reports[0]
    assert not report.ok, "byte-level fixity was resealed, but the chain break must still fail"


def test_premis_chain_head_and_heads(tmp_path: Path) -> None:
    archive = Archive.init(Config.default("Chain Archive", tmp_path / "archive"))
    rid, premis_path = _ingest(archive)

    head = archive.premis_chain_head(rid)
    assert head is not None
    assert head != GENESIS_HASH
    assert head == PremisLog.read(premis_path).head

    heads = archive.premis_chain_heads()
    assert heads[rid] == head

    # A bag id with no log at all yields None.
    assert archive.premis_chain_head("does-not-exist") is None


def test_chain_head_summary_changes_when_history_is_rewritten(tmp_path: Path) -> None:
    archive = Archive.init(Config.default("Chain Archive", tmp_path / "archive"))
    _, premis_path = _ingest(archive)

    before = archive.chain_head_summary()

    data = json.loads(premis_path.read_text(encoding="utf-8"))
    data["entries"][0]["eventDetail"] = "rewritten"
    premis_path.write_text(json.dumps(data, sort_keys=True, separators=(",", ":")))

    after = archive.chain_head_summary()
    assert before != after


def test_audit_log_chains_covers_archive_level_logs(tmp_path: Path) -> None:
    archive = Archive.init(Config.default("Chain Archive", tmp_path / "archive"))
    rid, _ = _ingest(archive)

    # No archive-level logs yet.
    assert archive.audit_log_chains() == []

    # Two entries: a lone entry's own tamper can't be locally caught by
    # verify_chain (nothing after it to hold a stale, still-correct prevHash
    # pointer) — that limitation is deliberate and documented, so this test
    # tampers the FIRST of two entries, which the second entry's untouched
    # prevHash then contradicts.
    for i in range(2):
        archive.log_takedown(
            PremisEvent(
                event_type=PremisEventType.TAKEDOWN,
                agent="test-steward",
                outcome="success",
                detail=f"record taken down ({i})",
                linked_object=rid,
                event_datetime=_NOW,
            )
        )

    results = archive.audit_log_chains()
    assert len(results) == 1
    name, verification = results[0]
    assert name == "takedowns.premis.json"
    assert verification.ok

    # Tamper the first of the two entries directly, then confirm it is caught.
    takedowns_path = archive.logs_dir / "takedowns.premis.json"
    data = json.loads(takedowns_path.read_text(encoding="utf-8"))
    data["entries"][0]["eventDetail"] = "rewritten"
    takedowns_path.write_text(json.dumps(data, sort_keys=True, separators=(",", ":")))

    results = archive.audit_log_chains()
    _name, verification = results[0]
    assert not verification.ok


def test_verify_replicas_detects_divergent_chain_head(tmp_path: Path) -> None:
    """A replica whose chain head disagrees with the source is unhealthy (FIX-06),
    even though its own bag is internally self-consistent (bytes and chain both
    resealed after the divergent edit)."""
    root = tmp_path / "archive"
    mirror = tmp_path / "mirror"
    mirror.mkdir(parents=True, exist_ok=True)
    archive = Archive.init(Config.default("Chain Archive", root))
    rid, _ = _ingest(archive)

    bag_dir = archive.bags_dir / rid
    mirror_loc = StorageLocation(name="mirror-1", path=str(mirror), kind="mirror")
    replicate_bag(bag_dir, mirror_loc, agent="test-steward", now=_NOW)

    source_head = archive.premis_chain_head(rid)

    # A healthy, identical replica agrees with the source.
    statuses = verify_replicas(rid, [mirror_loc], source_head=source_head)
    assert len(statuses) == 1
    assert statuses[0].ok
    assert statuses[0].chain_ok
    assert statuses[0].chain_head == source_head

    # Now rewrite the replica's own history *and* reseal it locally, so it is
    # internally self-consistent (its own verify_chain() would say ok) but
    # diverges from the source archive's head.
    replica_premis = mirror / rid / "premis.json"
    replica_log = PremisLog.read(replica_premis)
    replica_log.record(
        PremisEvent(
            event_type=PremisEventType.MODERATION,
            agent="attacker",
            outcome="success",
            detail="an event the source archive never recorded",
            linked_object=rid,
            event_datetime=_NOW,
        )
    )
    replica_log.write(replica_premis)
    _reseal_tagmanifests(mirror / rid)

    statuses = verify_replicas(rid, [mirror_loc], source_head=source_head)
    assert not statuses[0].ok
    assert not statuses[0].chain_ok
    assert statuses[0].chain_head != source_head
