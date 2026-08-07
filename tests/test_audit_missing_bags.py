"""Tests for issue #121: ``audit_fixity`` must not report health it never checked.

``Archive.audit_fixity`` used to walk only ``bags/`` — a deleted bag was simply a
directory that was not there to iterate, never a validation failure. Every caller
computes ``all(report.ok for _, report in reports)`` (or the equivalent "0
failed"), and that predicate is vacuously true over a shrunken or empty list: a
steward with raw disk access who deletes a bag makes the audit, ``ledger audit``,
``/healthz``, and the signed health attestation report *more* health, not less —
up to ``fixity_ok: true`` over zero bags, while ``browse()`` still lists the record.

These exercise the fix directly (``Archive.audit_fixity`` reconciling ``bags/``
against ``records/``) and end to end through :func:`ledger.attestation.
build_attestation`, whose ``fixity_ok`` field is what a steward's ``ledger
attest-health`` signs and publishes.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ledger.attestation import build_attestation
from ledger.config import Config
from ledger.identity import ContributorIdentity
from ledger.ingest import Archive
from ledger.models import AccessPolicy, DublinCore, Field, Record

_VAULT_KEY = b"0123456789abcdef0123456789abcdef0123456789a="
_NOW = "2026-06-16T12:00:00Z"
_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _record(title: str) -> Record:
    return Record(
        title=title,
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(
            title=[title], publisher=["Test Archive"], type=["oral history"], language=["en"]
        ),
        fields=[Field(name="story", value="A public account.", policy=AccessPolicy.PUBLIC)],
    )


def _ingest(archive: Archive, title: str) -> str:
    payload = _FIXTURES / "public.txt"
    record = _record(title)
    archive.ingest(
        {payload.name: payload},
        record,
        identity=ContributorIdentity(name="Test Contributor"),
        vault_key=_VAULT_KEY,
        agent="test-steward",
        now=_NOW,
    )
    return record.record_id


def test_a_bag_deleted_outside_remove_all_copies_fails_the_audit(tmp_path: Path) -> None:
    """The measured bug. Deleting a bag by hand (not through the one method that
    keeps bag/manifest/tombstone in sync) must not read as a clean audit."""
    archive = Archive.init(Config.default("Missing Bag Archive", tmp_path / "archive"))
    rid = _ingest(archive, "Solo record")

    shutil.rmtree(archive.bags_dir / rid)

    reports = archive.audit_fixity()
    assert len(reports) == 1
    name, report = reports[0]
    assert name == rid
    assert not report.ok
    assert "no bag directory found" in report.failed[0].actual


def test_deleting_every_bag_does_not_read_as_zero_bags_audited(tmp_path: Path) -> None:
    """The sharpest case from the issue: ``PASS: 0 bag(s) audited, 0 failed`` for
    an emptied archive that ``browse()`` still lists records for."""
    archive = Archive.init(Config.default("Emptied Archive", tmp_path / "archive"))
    ids = [_ingest(archive, f"Record {i}") for i in range(3)]
    shutil.rmtree(archive.bags_dir)
    archive.bags_dir.mkdir()

    reports = archive.audit_fixity()

    assert len(reports) == 3, "an emptied archive must not audit as though nothing existed"
    assert not any(report.ok for _name, report in reports)
    assert {name for name, _report in reports} == set(ids)


def test_a_healthy_bag_is_unaffected_by_the_reconciliation(tmp_path: Path) -> None:
    """The overwhelmingly common path — nothing missing — must still pass, and
    must not be reported twice (once from the bag walk, once from records/)."""
    archive = Archive.init(Config.default("Healthy Archive", tmp_path / "archive"))
    _ingest(archive, "Solo record")

    reports = archive.audit_fixity()

    assert len(reports) == 1
    assert reports[0][1].ok


def test_a_genuinely_empty_archive_still_audits_clean(tmp_path: Path) -> None:
    """No records, no bags, nothing to reconcile — this is not the bug. An
    archive that has never held anything is honestly healthy, not merely quiet."""
    archive = Archive.init(Config.default("Empty Archive", tmp_path / "archive"))

    assert archive.audit_fixity() == []


def test_a_bag_missing_its_records_manifest_is_not_double_counted(tmp_path: Path) -> None:
    """The reverse gap: a bag that exists but whose fast-lookup copy under
    records/ was separately lost. The bag walk already reports it; reconciling
    against records/ must not also report it a second time, or drop it."""
    archive = Archive.init(Config.default("Orphaned Manifest Archive", tmp_path / "archive"))
    rid = _ingest(archive, "Solo record")
    (archive.records_dir / f"{rid}.json").unlink()

    reports = archive.audit_fixity()

    assert len(reports) == 1
    name, report = reports[0]
    assert name == rid
    assert report.ok, "the bag itself is untouched; only its records/ copy was removed"


def test_build_attestation_fixity_ok_is_false_when_a_bag_is_missing(tmp_path: Path) -> None:
    """End to end through the exact function a steward's signed attestation is
    built from (:func:`ledger.attestation.build_attestation`). This is the
    concrete artifact `ledger attest-health` publishes to /proof."""
    archive = Archive.init(Config.default("Attested Archive", tmp_path / "archive"))
    rid = _ingest(archive, "Solo record")
    shutil.rmtree(archive.bags_dir / rid)

    attestation = build_attestation(archive, now=_NOW)

    assert attestation.fixity_ok is False


def test_build_attestation_fixity_ok_is_true_for_a_genuinely_empty_archive(
    tmp_path: Path,
) -> None:
    """The companion to the test above: an attestation over nothing is not the
    same claim as an attestation over a corpus that used to have bags."""
    archive = Archive.init(Config.default("Attested Empty Archive", tmp_path / "archive"))

    attestation = build_attestation(archive, now=_NOW)

    assert attestation.fixity_ok is True
