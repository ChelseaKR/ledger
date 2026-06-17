"""End-to-end preservation lifecycle test: ingest, disclose, replicate, consent.

This walks one record through the whole archive lifecycle the way a steward would
and asserts the standards-tracked side effects at each stage:

* **ingest** records a PREMIS ``INGESTION`` event plus a ``FIXITY_CHECK`` per
  payload, seals the contributor identity into the vault, and stores an
  identity-free bag;
* **disclose** projects the record to the safe shape — the public field is shown,
  the sealed field is withheld and honestly named, identity never appears;
* **replicate** copies the bag to a second location, verifies it on arrival, and
  yields a ``REPLICATION`` event; the replica validates;
* **consent change** tightens the default policy, records a ``CONSENT_CHANGE``
  PREMIS event, and the *change is reflected* in a subsequent disclosure (the
  record stops being listable to the anonymous public).

The whole cycle uses fixed timestamps and a fixed vault key, so it is
byte-reproducible (determinism).
"""

from __future__ import annotations

from pathlib import Path

from ledger.access.grants import anonymous, build_grant, steward
from ledger.config import Config, StorageLocation
from ledger.errors import AccessDenied
from ledger.identity import ContributorIdentity
from ledger.ingest import Archive, serialize_record
from ledger.metadata.premis import PremisLog
from ledger.models import (
    AccessPolicy,
    DublinCore,
    Field,
    PremisEventType,
    Record,
)
from ledger.moderate import change_consent
from ledger.replicate import replicate_bag, verify_replicas

_SENTINEL_NAME = "SENTINEL-E2E-DO-NOT-LEAK-7Q4X"
_SEALED_FIELD = "SEALED-FIELD-E2E-9Z2K"
_VAULT_KEY = b"0123456789abcdef0123456789abcdef0123456789a="
_NOW_INGEST = "2026-06-16T12:00:00Z"
_NOW_CONSENT = "2026-06-16T12:05:00Z"

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _build_record(archive_name: str) -> Record:
    """A synthetic record with a public field, a sealed field, and content warnings."""
    return Record(
        title="Mutual-aid Thursdays",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(
            title=["Mutual-aid Thursdays"],
            description=["A synthetic oral history of weekly gatherings."],
            publisher=[archive_name],
            type=["oral history"],
            language=["en"],
        ),
        fields=[
            Field(name="story", value="A public account.", policy=AccessPolicy.PUBLIC),
            Field(
                name="real_names",
                value=f"roster: {_SEALED_FIELD}",
                policy=AccessPolicy.SEALED_UNTIL,
            ),
        ],
        content_warnings=["outing"],
    )


def test_full_lifecycle_ingest_disclose_replicate_consent(tmp_path: Path) -> None:
    """Ingest -> disclose -> replicate -> consent-change, asserting events + effects."""
    root = tmp_path / "archive"
    mirror = tmp_path / "mirror"
    mirror.mkdir(parents=True, exist_ok=True)

    config = Config.default("E2E Community Archive", root)
    archive = Archive.init(config)

    # --- 1. INGEST ---------------------------------------------------------
    payload = _FIXTURES / "public.txt"
    record = _build_record(config.archive_name)
    identity = ContributorIdentity(name=_SENTINEL_NAME)
    aip = archive.ingest(
        {payload.name: payload},
        record,
        identity=identity,
        vault_key=_VAULT_KEY,
        agent="e2e-steward",
        now=_NOW_INGEST,
    )
    rid = record.record_id

    # The identity was sealed: the record carries an opaque ref, not the name.
    assert record.identity_ref is not None
    assert _SENTINEL_NAME not in record.identity_ref

    # PREMIS: one INGESTION event and at least one FIXITY_CHECK event.
    premis = PremisLog.read(aip.premis_path)
    event_types = [e.event_type for e in premis.events]
    assert PremisEventType.INGESTION in event_types
    assert PremisEventType.FIXITY_CHECK in event_types

    # The stored bag is structurally valid (audit passes). audit_fixity now returns
    # (bag_name, report) pairs so a broken bag can be reported without aborting.
    reports = archive.audit_fixity()
    assert len(reports) == 1
    _name, report = reports[0]
    assert report.ok

    # --- 2. DISCLOSE -------------------------------------------------------
    # Anonymous: sees the public field, not the sealed field; honest about it.
    anon = archive.disclose(rid, anonymous(), now=_NOW_INGEST)
    assert anon.fields.get("story") == "A public account."
    assert "real_names" not in anon.fields
    assert "real_names" in anon.redactions
    anon_json = anon.to_dict()
    assert _SEALED_FIELD not in str(anon_json)
    assert _SENTINEL_NAME not in str(anon_json)

    # Steward: sees the sealed field at rest, yet still cannot see identity.
    steward_view = archive.disclose(rid, steward("e2e-steward"), now=_NOW_INGEST)
    assert _SEALED_FIELD in steward_view.fields.get("real_names", "")
    assert _SENTINEL_NAME not in str(steward_view.to_dict())

    # Identity resolves only under an explicit unseal grant (the seal is real).
    unseal = build_grant("investigator", identity_unseal=[record.identity_ref])
    resolved = archive.resolve_identity(rid, unseal)
    assert resolved.name == _SENTINEL_NAME
    # A steward grant (no unseal token) is denied.
    try:
        archive.resolve_identity(rid, steward("e2e-steward"))
        denied = False
    except AccessDenied:
        denied = True
    assert denied, "a steward without an unseal token must not resolve identity"

    # --- 3. REPLICATE ------------------------------------------------------
    mirror_loc = StorageLocation(name="mirror-1", path=str(mirror), kind="mirror")
    repl_event = replicate_bag(aip.bag.path, mirror_loc, agent="e2e-steward", now=_NOW_INGEST)
    assert repl_event.event_type is PremisEventType.REPLICATION
    assert repl_event.outcome == "success"

    statuses = verify_replicas(rid, [mirror_loc])
    assert len(statuses) == 1
    assert statuses[0].ok

    # The replicated bag-info / record manifest are identity-free too.
    replica_record = (mirror / rid / "record.json").read_text(encoding="utf-8")
    assert _SENTINEL_NAME not in replica_record

    # --- 4. CONSENT CHANGE -------------------------------------------------
    # Before: the record is public-listable to anonymous viewers.
    before = archive.browse(anonymous(), now=_NOW_CONSENT)
    assert any(r.record_id == rid for r in before)

    updated, consent_event, action = change_consent(
        archive.get(rid),
        AccessPolicy.STEWARDS,
        actor="e2e-steward",
        reason="contributor asked to tighten visibility to stewards only",
        now=_NOW_CONSENT,
    )
    assert consent_event.event_type is PremisEventType.CONSENT_CHANGE
    assert action.action == "consent-change"

    # Persist the tightened manifest and append the PREMIS event to the bag.
    manifest = serialize_record(updated)
    (archive.records_dir / f"{rid}.json").write_text(manifest, encoding="utf-8", newline="\n")
    (aip.bag.path / "record.json").write_text(manifest, encoding="utf-8", newline="\n")
    log = PremisLog.read(aip.premis_path)
    log.record(consent_event)
    log.write(aip.premis_path)

    # The CONSENT_CHANGE event is now durably recorded.
    reloaded = PremisLog.read(aip.premis_path)
    assert any(e.event_type is PremisEventType.CONSENT_CHANGE for e in reloaded.events)

    # The change is REFLECTED in disclosure: anonymous can no longer list it.
    after = archive.browse(anonymous(), now=_NOW_CONSENT)
    assert not any(r.record_id == rid for r in after)
    # A steward still sees it (visibility tightened to stewards, not removed).
    steward_after = archive.browse(steward("e2e-steward"), now=_NOW_CONSENT)
    assert any(r.record_id == rid for r in steward_after)
    # And a direct anonymous disclose is now denied.
    try:
        archive.disclose(rid, anonymous(), now=_NOW_CONSENT)
        anon_denied = False
    except AccessDenied:
        anon_denied = True
    assert anon_denied, "anonymous disclose must be denied after tightening consent"


def test_sealed_until_field_unseals_on_its_date(tmp_path: Path) -> None:
    """A SEALED_UNTIL field is withheld before its unseal date and shown after.

    Exercises the time dimension of disclosure deterministically: the same field,
    the same anonymous grant, two different ``now`` values, two different outcomes.
    """
    config = Config.default("Time Archive", tmp_path / "arc")
    archive = Archive.init(config)
    record = Record(
        title="Time-sealed sample",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(title=["Time-sealed sample"]),
        fields=[
            Field(
                name="later",
                value="visible only after the date",
                policy=AccessPolicy.SEALED_UNTIL,
                unseal_at="2027-01-01T00:00:00Z",
            ),
        ],
    )
    archive.ingest({}, record, now=_NOW_INGEST)
    rid = record.record_id

    before = archive.disclose(rid, anonymous(), now="2026-12-31T23:59:59Z")
    assert "later" not in before.fields
    assert "later" in before.redactions

    after = archive.disclose(rid, anonymous(), now="2027-01-02T00:00:00Z")
    assert after.fields.get("later") == "visible only after the date"
    assert "later" not in after.redactions
