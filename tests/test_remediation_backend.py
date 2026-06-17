"""Tests for the user-research remediation backend.

Covers the absolute ``SEALED`` tier, structured withheld reasons, the
outsider-vs-insider serialization granularity, at-rest encryption of sealed
content, the minimum-metadata backfill, and the governance config fields.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from ledger.access import disclose, is_visible, withheld_reason
from ledger.access.grants import anonymous, community_member, steward
from ledger.config import Config
from ledger.identity import IdentityVault
from ledger.ingest import Archive
from ledger.models import AccessPolicy, DublinCore, Field, Record

_NOW = "2026-06-17T00:00:00Z"

pytestmark = pytest.mark.disclosure


# --- absolute SEALED tier ---------------------------------------------------


@pytest.mark.parametrize("grant", [anonymous(), community_member("c"), steward("s")])
def test_sealed_absolute_visible_to_no_one(grant: object) -> None:
    """The absolute SEALED tier is visible to no grant, not even a steward."""
    assert is_visible(AccessPolicy.SEALED, grant, _NOW) is False  # type: ignore[arg-type]


def test_disclose_withholds_sealed_field_even_from_steward() -> None:
    rec = Record(
        title="t",
        default_policy=AccessPolicy.PUBLIC,
        fields=[
            Field("story", "public", AccessPolicy.PUBLIC),
            Field("name", "secret", AccessPolicy.SEALED),
        ],
    )
    dr = disclose(rec, steward("s"), _NOW)
    assert "name" not in dr.fields
    reasons = {r.name: r.reason for r in dr.withheld}
    assert reasons["name"] == "sealed from everyone, including stewards"


# --- structured withheld reasons + granularity ------------------------------


def test_withheld_reason_labels() -> None:
    assert withheld_reason(AccessPolicy.COMMUNITY, None) == "shared with community members"
    assert withheld_reason(AccessPolicy.STEWARDS, None) == "restricted to stewards"
    assert (
        withheld_reason(AccessPolicy.SEALED_UNTIL, "2030-01-01T00:00:00Z")
        == "sealed until 2030-01-01"
    )
    assert withheld_reason(AccessPolicy.SEALED_UNTIL, None) == "sealed (no opening date set)"


def test_to_dict_generalizes_for_outsiders() -> None:
    rec = Record(
        title="t",
        default_policy=AccessPolicy.PUBLIC,
        fields=[
            Field("story", "public", AccessPolicy.PUBLIC),
            Field("loc", "secret place", AccessPolicy.STEWARDS),
        ],
    )
    dr = disclose(rec, anonymous(), _NOW)
    insider = dr.to_dict(withheld_reasons=True)
    outsider = dr.to_dict(withheld_reasons=False)
    assert any(w["name"] == "loc" for w in insider["withheld"])  # type: ignore[index]
    assert "withheld" not in outsider and outsider["withheld_count"] == 1


# --- at-rest encryption of sealed content -----------------------------------


def test_vault_text_roundtrip(tmp_path: Path) -> None:
    vault = IdentityVault.create(tmp_path / "v.vault", IdentityVault.generate_key())
    token = vault.encrypt_text("a sealed secret")
    assert token.startswith("enc:") and "a sealed secret" not in token
    assert vault.decrypt_text(token) == "a sealed secret"


def test_sealed_field_encrypted_at_rest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEDGER_VAULT_KEY", Fernet.generate_key().decode())
    cfg = Config.default("Probe", tmp_path)
    cfg.save(tmp_path / "store" / "config.json")
    archive = Archive.init(cfg)
    payload = tmp_path / "s.txt"
    payload.write_text("public story body")
    rec = Record(
        title="t",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(title=["t"]),
        fields=[Field("absolute", "DO-NOT-PERSIST-PLAINTEXT", AccessPolicy.SEALED)],
    )
    archive.ingest({payload.name: payload}, rec, now=_NOW)
    on_disk = (archive.bags_dir / rec.record_id / "record.json").read_text()
    assert "DO-NOT-PERSIST-PLAINTEXT" not in on_disk  # encrypted at rest
    assert "enc:" in on_disk


def test_sealed_field_requires_vault(tmp_path: Path) -> None:
    cfg = Config.default("Probe", tmp_path)
    cfg.save(tmp_path / "store" / "config.json")
    archive = Archive.init(cfg)
    payload = tmp_path / "s.txt"
    payload.write_text("x")
    rec = Record(
        title="t",
        default_policy=AccessPolicy.PUBLIC,
        fields=[Field("absolute", "secret", AccessPolicy.SEALED)],
    )
    from ledger.errors import LedgerError

    with pytest.raises(LedgerError):
        archive.ingest({payload.name: payload}, rec, now=_NOW)


# --- minimum-metadata backfill ----------------------------------------------


def test_dc_date_backfilled_from_title_year(tmp_path: Path) -> None:
    cfg = Config.default("Probe", tmp_path)
    cfg.save(tmp_path / "store" / "config.json")
    archive = Archive.init(cfg)
    payload = tmp_path / "s.txt"
    payload.write_text("x")
    rec = Record(
        title="Flyer: Pride march 1991",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(title=["Flyer: Pride march 1991"]),
        fields=[Field("text", "hi", AccessPolicy.PUBLIC)],
    )
    archive.ingest({payload.name: payload}, rec, now=_NOW)
    assert archive.get(rec.record_id).dublin_core.date == ["1991"]


# --- governance config round-trip -------------------------------------------


def test_config_governance_fields_roundtrip(tmp_path: Path) -> None:
    cfg = Config.default("Rosewater", tmp_path)
    assert cfg.about and cfg.operators  # defaults are populated
    path = tmp_path / "store" / "config.json"
    cfg.save(path)
    loaded = Config.load(path)
    assert loaded.about == cfg.about
    assert loaded.steward_vetting == cfg.steward_vetting
    assert loaded.consent_response_time == cfg.consent_response_time
