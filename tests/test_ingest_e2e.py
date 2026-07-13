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

import pytest

from ledger.access.grants import anonymous, build_grant, steward
from ledger.config import Config, StorageLocation
from ledger.errors import AccessDenied, BagValidationError, LedgerError
from ledger.identity import ContributorIdentity
from ledger.ingest import Archive, serialize_record
from ledger.metadata.premis import PremisLog
from ledger.models import (
    AccessPolicy,
    ContentAddress,
    DublinCore,
    Field,
    HashAlgo,
    PayloadFile,
    PremisEvent,
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


@pytest.mark.parametrize(
    "filename",
    [r"..\escape.bin", r"C:\escape.bin", r"\rooted.bin"],
)
def test_sealed_payload_rejects_windows_escape_before_any_payload_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, filename: str
) -> None:
    """Windows traversal/drive/root forms fail before temp or CAS writes."""
    archive = Archive.init(Config.default("Path Safety Archive", tmp_path / "arc"))
    archive._open_vault(_VAULT_KEY)
    source = tmp_path / "source.bin"
    source.write_bytes(b"sensitive payload")
    record = Record(
        title="Hostile sealed path",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(title=["Hostile sealed path"]),
        payloads=[
            PayloadFile(
                filename=filename,
                address=ContentAddress(HashAlgo.SHA256, "0" * 64),
                media_type="application/octet-stream",
                size_bytes=0,
                policy=AccessPolicy.SEALED,
            )
        ],
    )

    def unexpected_write(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("unsafe path reached a payload write boundary")

    monkeypatch.setattr("ledger.ingest.tempfile.mkdtemp", unexpected_write)
    monkeypatch.setattr(archive.store, "put_file", unexpected_write)

    with pytest.raises(BagValidationError, match="unsafe path in sealed payload"):
        archive.ingest({filename: source}, record, vault_key=_VAULT_KEY, now=_NOW_INGEST)

    assert not any(archive.bags_dir.iterdir())


def test_record_collision_precedes_all_sealed_payload_and_identity_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A duplicate id leaves payload temp/CAS, caller plaintext, and vault untouched."""
    archive = Archive.init(Config.default("Collision Archive", tmp_path / "arc"))
    original = Record(
        title="Original",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(title=["Original"]),
    )
    archive.ingest({}, original, now=_NOW_INGEST)

    assert not archive.vault_path.exists()
    before_tree = {
        path.relative_to(archive.store_root): None if path.is_dir() else path.read_bytes()
        for path in sorted(archive.store_root.rglob("*"))
    }
    plaintext = "must remain recoverable by the rejected caller"
    source = tmp_path / "sealed-source.bin"
    source.write_bytes(b"must never be encrypted or copied on collision")
    duplicate = Record(
        record_id=original.record_id,
        title="Duplicate",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(title=["Duplicate"]),
        fields=[Field(name="sealed", value=plaintext, policy=AccessPolicy.SEALED)],
        payloads=[
            PayloadFile(
                filename=source.name,
                address=ContentAddress(HashAlgo.SHA256, "0" * 64),
                media_type="application/octet-stream",
                size_bytes=0,
                policy=AccessPolicy.SEALED,
            )
        ],
    )

    def unexpected_side_effect(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("collision reached sealed-payload processing")

    monkeypatch.setattr(archive, "_open_vault", unexpected_side_effect)
    monkeypatch.setattr("ledger.ingest.identify_file", unexpected_side_effect)
    monkeypatch.setattr("ledger.ingest.tempfile.mkdtemp", unexpected_side_effect)
    monkeypatch.setattr(archive.store, "put_file", unexpected_side_effect)

    with pytest.raises(LedgerError, match="bag already exists"):
        archive.ingest(
            {source.name: source},
            duplicate,
            identity=ContributorIdentity(name="not orphaned"),
            vault_key=_VAULT_KEY,
            now=_NOW_INGEST,
        )

    assert duplicate.fields[0].value == plaintext
    assert duplicate.identity_ref is None
    assert not archive.vault_path.exists()
    after_tree = {
        path.relative_to(archive.store_root): None if path.is_dir() else path.read_bytes()
        for path in sorted(archive.store_root.rglob("*"))
    }
    assert after_tree == before_tree


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

    # RM5: the DC sidecar carries a minted, deterministic UUID URN.
    from ledger.metadata.dublincore import read_sidecar
    from ledger.metadata.pid import is_pid, mint_urn

    dc = read_sidecar(aip.dc_path)
    expected_pid = mint_urn(rid)
    assert expected_pid in dc.identifier
    assert any(is_pid(v) for v in dc.identifier)

    # RM5: the PREMIS log carries a rights statement (basis + granted acts), and it
    # survives the on-disk round trip. The record declared no licence, so it falls back
    # to the honest default basis.
    assert premis.rights is not None
    assert premis.rights.rights_basis == "other"
    assert premis.rights.granted_acts == ()
    assert premis.rights.linked_object == rid

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


def test_declared_licence_becomes_a_license_basis_rights_statement(tmp_path: Path) -> None:
    """A record declaring a Dublin Core licence yields a ``license``-basis rights entity.

    The declared rights value is lifted into the PREMIS rights statement's note, so the
    reuse terms travel with the preservation log rather than living only in the
    descriptive sidecar (RM5).
    """
    from ledger.metadata.premis import PremisLog as _PremisLog

    config = Config.default("Licence Archive", tmp_path / "arc")
    archive = Archive.init(config)
    record = Record(
        title="A CC-licensed zine",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(
            title=["A CC-licensed zine"],
            rights=["CC-BY-SA-4.0"],
        ),
    )
    aip = archive.ingest({}, record, now=_NOW_INGEST)

    rights = _PremisLog.read(aip.premis_path).rights
    assert rights is not None
    assert rights.rights_basis == "license"
    assert rights.rights_note == "CC-BY-SA-4.0"
    assert rights.linked_object == record.record_id


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


def test_apply_update_reseals_bag_so_audit_fixity_stays_green(tmp_path: Path) -> None:
    """FIX-01: a post-ingest steward edit must not make audit_fixity flag its own bag.

    ``Archive.apply_update`` is the shared write path behind every post-ingest
    change (consent/policy change, content warning, review decision). Before the
    fix, it rewrote ``record.json``/``premis.json`` -- tag files covered by the
    bag's own tag manifests -- without refreshing those manifests, so the very next
    ``audit_fixity`` sweep reported a lawful steward edit as tampering. This pins
    the round trip: ingest, apply an update, audit -- still all green -- and that a
    genuine corrupted payload byte is still caught right after that same update.
    """
    config = Config.default("Reseal Archive", tmp_path / "arc")
    archive = Archive.init(config)
    record = Record(
        title="Reseal sample",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(title=["Reseal sample"]),
        fields=[Field(name="story", value="before", policy=AccessPolicy.PUBLIC)],
    )
    payload = _FIXTURES / "public.txt"
    archive.ingest({payload.name: payload}, record, now=_NOW_INGEST)
    rid = record.record_id

    # Sanity: freshly ingested bag is valid.
    reports = dict(archive.audit_fixity())
    assert reports[rid].ok

    # A steward edit: tighten the record's default policy (a real post-ingest
    # change -- consent tightening, a content-warning addition, a review decision
    # all go through the same apply_update path).
    updated = archive.get(rid)
    updated.default_policy = AccessPolicy.STEWARDS
    event = PremisEvent(
        event_type=PremisEventType.CONSENT_CHANGE,
        agent="e2e-steward",
        outcome="success",
        detail="tighten default policy to stewards",
        event_datetime=_NOW_CONSENT,
    )
    archive.apply_update(updated, event)

    # The update actually took effect (a no-op apply_update must not pass this
    # test): the next read reflects the tightened policy.
    assert archive.get(rid).default_policy is AccessPolicy.STEWARDS

    # The change is durably recorded in the bag's PREMIS log...
    premis = PremisLog.read(archive.bags_dir / rid / "premis.json")
    assert any(e.event_type is PremisEventType.CONSENT_CHANGE for e in premis.events)

    # ...the reseal itself is recorded as a VALIDATION event carrying the
    # record.json digest transition, so a lawful reseal is never bit-for-bit
    # indistinguishable from an edit that skipped the log...
    reseal_events = [
        e
        for e in premis.events
        if e.event_type is PremisEventType.VALIDATION and "resealed" in e.detail
    ]
    assert len(reseal_events) == 1
    assert "record.json sha256" in reseal_events[0].detail
    assert " -> " in reseal_events[0].detail
    assert reseal_events[0].linked_object == rid

    # ...and, crucially, the bag still re-validates: the lawful edit does not read
    # as tampering at the next audit (the FIX-01 guarantee).
    reports = dict(archive.audit_fixity())
    assert reports[rid].ok

    # A genuine payload corruption right after the update is still caught -- the
    # reseal only recomputes tag manifests, never payload fixity.
    payload_files = list((archive.bags_dir / rid / "data").rglob("*"))
    payload_file = next(p for p in payload_files if p.is_file())
    payload_file.write_bytes(b"corrupted after update")
    reports = dict(archive.audit_fixity())
    assert not reports[rid].ok
