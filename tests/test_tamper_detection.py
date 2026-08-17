"""Two tampers that used to pass every integrity check ledger has.

Both attacks below take the same shape and have the same consequence. Neither
needs a hash collision, a key, or a running server: they need write access to the
store, which is precisely the threat ``docs/THREAT-MODEL.md`` treats as in scope
(seizure, a malicious steward, a compromised host). Both flip an *embargoed* field
— the ``SEALED_UNTIL`` case the README advertises as "publish the story while the
names stay sealed" — into a public one, so an anonymous viewer reads the withheld
plaintext. And in both, before the fix, ``Archive.audit_fixity`` stayed green and
:func:`ledger.attestation.build_attestation` still signed ``fixity_ok: true``.

1. **The unread copy.** Every disclosure decision is made from the *fast-lookup*
   manifest under ``records/`` — ``Archive.get`` prefers it and ``_all_records``
   (browse, search, OAI, sitemap, feed, CSV) reads only it. BagIt fixity covers
   the *other* copy, inside the bag. Nothing compared them, so the file every read
   path trusts had no fixity coverage at all: a fixity check that passes over a
   store it never read.

2. **The undeclared tag file.** ``record.json`` inside the bag carries every
   field's AccessPolicy and is covered by the tag manifests — but validation only
   ever verified the entries a tag manifest *declares*. Deleting ``record.json``'s
   line from every ``tagmanifest-*.txt`` first exempts it from being hashed at all,
   after which it can be rewritten freely: a gate that cannot fail, because the
   attacker removes the file from the gate's field of view before tripping it. The
   PREMIS hash chain does not close this — ``record.json`` is not in the log.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from ledger.access.grants import build_grant
from ledger.attestation import build_attestation
from ledger.bag import validate_bag
from ledger.config import Config
from ledger.fixity import AuditReport
from ledger.ingest import Archive
from ledger.models import (
    AccessPolicy,
    DublinCore,
    Field,
    PremisEvent,
    PremisEventType,
    Record,
)

pytestmark = [pytest.mark.preservation, pytest.mark.disclosure]

_VAULT_KEY = b"0123456789abcdef0123456789abcdef0123456789a="
_NOW = "2026-06-16T12:00:00Z"
_FIXTURES = Path(__file__).resolve().parent / "fixtures"

# The embargoed plaintext. It sits in the manifest in the clear (unlike an
# absolute-SEALED value, which ingest encrypts into the vault), gated only by its
# field policy — so a policy the audit does not protect is the whole defence.
_EMBARGOED = "the names of everyone in the safehouse network"
_RECORD_ID = "rec-embargo"


def _ingest_embargoed(tmp_path: Path) -> Archive:
    """An archive with one public record carrying one embargoed field."""
    archive = Archive.init(Config.default("Tamper Archive", tmp_path / "archive"))
    payload = _FIXTURES / "public.txt"
    archive.ingest(
        {payload.name: payload},
        Record(
            title="Oral history: a safehouse network",
            record_id=_RECORD_ID,
            default_policy=AccessPolicy.PUBLIC,
            dublin_core=DublinCore(title=["Oral history: a safehouse network"]),
            fields=[
                Field(name="story", value="We moved people to safety.", policy=AccessPolicy.PUBLIC),
                Field(
                    name="participants",
                    value=_EMBARGOED,
                    policy=AccessPolicy.SEALED_UNTIL,
                    unseal_at="2099-01-01T00:00:00Z",
                ),
            ],
        ),
        vault_key=_VAULT_KEY,
        agent="test-steward",
        now=_NOW,
    )
    return archive


def _anonymous_fields(archive: Archive) -> dict[str, str]:
    return dict(archive.disclose(_RECORD_ID, build_grant("anon"), now=_NOW).fields)


def _publish_the_embargoed_field(manifest_path: Path) -> None:
    """Rewrite a record manifest so the embargoed field reads as public."""
    doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    for field in doc["fields"]:
        if field["name"] == "participants":
            field["policy"] = AccessPolicy.PUBLIC.value
            field.pop("unseal_at", None)
    manifest_path.write_text(json.dumps(doc, indent=2, sort_keys=True), encoding="utf-8")


def _failing_paths(reports: list[tuple[str, AuditReport]]) -> list[str]:
    return [result.path for _name, report in reports for result in report.failed]


def test_the_embargo_holds_before_any_tampering(tmp_path: Path) -> None:
    """Baseline, so a green result below can never be a test that checks nothing."""
    archive = _ingest_embargoed(tmp_path)

    assert _anonymous_fields(archive) == {"story": "We moved people to safety."}
    assert all(report.ok for _name, report in archive.audit_fixity())
    assert build_attestation(archive, now=_NOW).fixity_ok


def test_editing_only_the_fast_lookup_manifest_fails_the_audit(tmp_path: Path) -> None:
    """Attack 1: the bag is never touched; only ``records/<id>.json`` is rewritten.

    The read path serves the tampered policy either way — detection is the
    guarantee here, not prevention — so what must change is that the audit and the
    signed attestation stop calling this archive healthy.
    """
    archive = _ingest_embargoed(tmp_path)
    bag_manifest = (archive.bags_dir / _RECORD_ID / "record.json").read_bytes()

    _publish_the_embargoed_field(archive.records_dir / f"{_RECORD_ID}.json")

    # The tamper really does disclose the embargoed value: this is worth catching.
    assert _anonymous_fields(archive)["participants"] == _EMBARGOED
    # ...and it really did leave the bag alone, so BagIt fixity alone cannot see it.
    assert (archive.bags_dir / _RECORD_ID / "record.json").read_bytes() == bag_manifest
    assert validate_bag(archive.bags_dir / _RECORD_ID).ok

    reports = archive.audit_fixity()
    assert not all(report.ok for _name, report in reports), (
        "the manifest every read path trusts was rewritten; the audit must not pass"
    )
    assert f"records/{_RECORD_ID}.json" in _failing_paths(reports)
    assert not build_attestation(archive, now=_NOW).fixity_ok


def test_a_record_manifest_dropped_from_the_tag_manifests_fails_the_audit(
    tmp_path: Path,
) -> None:
    """Attack 2: de-declare ``record.json``, then rewrite it inside the bag.

    Removing the line is what makes the rewrite invisible: an entry that is not in
    a tag manifest is never re-hashed, so the check has nothing to fail on. Both
    tag manifests are edited, so this cannot be caught by the two algorithms
    merely disagreeing with each other.
    """
    archive = _ingest_embargoed(tmp_path)
    bag_dir = archive.bags_dir / _RECORD_ID

    tagmanifests = sorted(bag_dir.glob("tagmanifest-*.txt"))
    assert len(tagmanifests) == 2, "expected a tag manifest per algorithm"
    for tagmanifest in tagmanifests:
        kept = [
            line
            for line in tagmanifest.read_text(encoding="utf-8").splitlines()
            if not line.endswith("  record.json")
        ]
        tagmanifest.write_text("".join(f"{line}\n" for line in kept), encoding="utf-8")

    _publish_the_embargoed_field(bag_dir / "record.json")
    shutil.copyfile(bag_dir / "record.json", archive.records_dir / f"{_RECORD_ID}.json")

    assert _anonymous_fields(archive)["participants"] == _EMBARGOED

    report = validate_bag(bag_dir)
    assert not report.ok, "a tag file no manifest declares was silently exempt from fixity"
    assert "record.json" in [res.path for res in report.failed]
    assert not build_attestation(archive, now=_NOW).fixity_ok


def test_tag_coverage_is_enforced_per_manifest_not_across_their_union(
    tmp_path: Path,
) -> None:
    """Dropping the line from ONE algorithm's tag manifest is already a finding.

    Checking the union of what all tag manifests declare would let an attacker
    weaken one algorithm and hide behind the other — the same "a single weakened
    algorithm cannot mask tampering" rule the payload side already enforces.
    """
    archive = _ingest_embargoed(tmp_path)
    bag_dir = archive.bags_dir / _RECORD_ID

    target = bag_dir / "tagmanifest-sha256.txt"
    kept = [
        line
        for line in target.read_text(encoding="utf-8").splitlines()
        if not line.endswith("  record.json")
    ]
    target.write_text("".join(f"{line}\n" for line in kept), encoding="utf-8")

    report = validate_bag(bag_dir)
    assert not report.ok
    assert any(
        res.path == "record.json" and "tagmanifest-sha256.txt" in res.actual
        for res in report.failed
    )


def test_an_untouched_bag_still_validates_clean(tmp_path: Path) -> None:
    """The guard must be capable of passing, or it is not a guard but a wall.

    An ordinary bag — and one legitimately resealed after a consent change, which
    rewrites ``record.json`` and recomputes the tag manifests — stays green.
    """
    archive = _ingest_embargoed(tmp_path)

    assert validate_bag(archive.bags_dir / _RECORD_ID).ok

    record = archive.get(_RECORD_ID)
    archive.apply_update(
        Record(
            title=record.title,
            record_id=record.record_id,
            default_policy=AccessPolicy.COMMUNITY,
            dublin_core=record.dublin_core,
            fields=list(record.fields),
            payloads=list(record.payloads),
            identity_ref=record.identity_ref,
            created_at=record.created_at,
        ),
        PremisEvent(
            event_type=PremisEventType.CONSENT_CHANGE,
            agent="test-steward",
            outcome="success",
            detail="contributor tightened visibility",
            linked_object=_RECORD_ID,
            event_datetime=_NOW,
        ),
    )

    assert validate_bag(archive.bags_dir / _RECORD_ID).ok
    assert all(report.ok for _name, report in archive.audit_fixity()), (
        "a lawful reseal must not read as tampering"
    )
