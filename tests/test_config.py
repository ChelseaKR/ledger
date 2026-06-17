"""Tests for :mod:`ledger.config` — versioned, declarative archive configuration.

Covers the secure single-box default, validation of the documented invariants, the
JSON save/load round-trip (including a TOML read path), the migration shim that
re-stamps an older file to the current schema, and the safety rule that a file from a
*newer* ledger is refused rather than silently misread.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ledger.config import (
    CONFIG_SCHEMA_VERSION,
    Config,
    StorageLocation,
)
from ledger.errors import ConfigError
from ledger.models import AccessPolicy


@pytest.mark.preservation
def test_default_is_secure_single_box(tmp_path: Path) -> None:
    """``Config.default`` yields a self-contained, narrowest-policy archive.

    Safety (default to narrowest): the default disclosure policy is SEALED_UNTIL, and
    store + vault both live beneath one root (affordability/installability).
    """
    config = Config.default("Test Archive", tmp_path)
    config.validate()  # the default must always be valid
    assert config.archive_name == "Test Archive"
    assert config.default_policy is AccessPolicy.SEALED_UNTIL
    assert config.store_root.startswith(str(tmp_path))
    assert config.vault_path.startswith(str(tmp_path))
    assert config.schema_version == CONFIG_SCHEMA_VERSION
    assert [loc.kind for loc in config.locations] == ["local"]


@pytest.mark.preservation
def test_validate_rejects_empty_archive_name(tmp_path: Path) -> None:
    """An empty ``archive_name`` (stamped on every record/bag) is rejected."""
    config = Config.default("Test", tmp_path)
    config.archive_name = ""
    with pytest.raises(ConfigError, match="archive_name"):
        config.validate()


@pytest.mark.preservation
def test_validate_rejects_unknown_storage_kind(tmp_path: Path) -> None:
    """A storage location with a typo'd ``kind`` fails at validation time."""
    config = Config.default("Test", tmp_path)
    config.locations.append(StorageLocation(name="bad", path=str(tmp_path / "loc"), kind="cloud"))
    with pytest.raises(ConfigError, match="unknown kind"):
        config.validate()


@pytest.mark.preservation
def test_storage_location_validate_requires_name_and_path(tmp_path: Path) -> None:
    """A nameless or pathless location is caught at load time, not downstream."""
    with pytest.raises(ConfigError, match="empty name"):
        StorageLocation(name="", path=str(tmp_path / "loc")).validate()
    with pytest.raises(ConfigError, match="empty path"):
        StorageLocation(name="primary", path="").validate()


@pytest.mark.preservation
def test_save_load_round_trip(tmp_path: Path) -> None:
    """A saved config loads back equal, field for field."""
    config = Config.default("Round Trip", tmp_path)
    path = tmp_path / "ledger.json"
    config.save(path)
    loaded = Config.load(path)
    assert loaded == config


@pytest.mark.preservation
def test_save_is_atomic_leaving_no_temp(tmp_path: Path) -> None:
    """A successful save leaves the target file and no ``.tmp`` sibling behind."""
    config = Config.default("Atomic", tmp_path)
    path = tmp_path / "ledger.json"
    config.save(path)
    assert path.exists()
    assert list(tmp_path.glob("ledger.json.tmp")) == []


@pytest.mark.preservation
def test_to_dict_from_dict_round_trip(tmp_path: Path) -> None:
    """``from_dict(to_dict())`` reproduces an equal, validated config."""
    config = Config.default("Mapping", tmp_path)
    assert Config.from_dict(config.to_dict()) == config


@pytest.mark.preservation
def test_load_toml(tmp_path: Path) -> None:
    """A hand-editable TOML config loads (interoperability)."""
    toml_path = tmp_path / "ledger.toml"
    toml_path.write_text(
        "schema_version = 1\n"
        'archive_name = "Toml Archive"\n'
        f'store_root = "{tmp_path / "store"}"\n'
        f'vault_path = "{tmp_path / "vault"}"\n'
        'default_policy = "sealed-until"\n',
        encoding="utf-8",
    )
    loaded = Config.load(toml_path)
    assert loaded.archive_name == "Toml Archive"
    assert loaded.default_policy is AccessPolicy.SEALED_UNTIL


@pytest.mark.preservation
def test_migration_shim_restamps_schema_version(tmp_path: Path) -> None:
    """A file with no/older ``schema_version`` is upgraded and re-stamped on load.

    Upgradability: an older file keeps working across releases, loaded as the current
    schema rather than being stranded.
    """
    raw: dict[str, object] = {
        "archive_name": "Legacy Archive",
        "store_root": str(tmp_path / "store"),
        "vault_path": str(tmp_path / "vault"),
        "default_policy": "sealed-until",
        # schema_version intentionally omitted — an old, pre-versioning file.
    }
    config = Config.from_dict(raw)
    assert config.schema_version == CONFIG_SCHEMA_VERSION
    assert config.archive_name == "Legacy Archive"


@pytest.mark.preservation
def test_rejects_future_schema_version_from_dict(tmp_path: Path) -> None:
    """A config from a newer ledger is refused, never silently misread (safety)."""
    raw = {
        "schema_version": CONFIG_SCHEMA_VERSION + 1,
        "archive_name": "From The Future",
        "store_root": str(tmp_path / "store"),
        "vault_path": str(tmp_path / "vault"),
    }
    with pytest.raises(ConfigError, match="newer than this build"):
        Config.from_dict(raw)


@pytest.mark.preservation
def test_rejects_future_schema_version_on_load(tmp_path: Path) -> None:
    """The same refusal holds when reading the future file from disk."""
    path = tmp_path / "future.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": CONFIG_SCHEMA_VERSION + 99,
                "archive_name": "Future",
                "store_root": str(tmp_path / "store"),
                "vault_path": str(tmp_path / "vault"),
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="newer than this build"):
        Config.load(path)


@pytest.mark.preservation
def test_validate_rejects_future_schema_version_directly(tmp_path: Path) -> None:
    """``validate`` itself refuses a forward-incompatible schema version."""
    config = Config.default("Direct", tmp_path)
    config.schema_version = CONFIG_SCHEMA_VERSION + 1
    with pytest.raises(ConfigError, match="newer than this build"):
        config.validate()


@pytest.mark.preservation
def test_load_missing_file_raises_config_error(tmp_path: Path) -> None:
    """A missing config file becomes a :class:`ConfigError` naming the path."""
    with pytest.raises(ConfigError, match="not found"):
        Config.load(tmp_path / "does-not-exist.json")


@pytest.mark.preservation
def test_load_malformed_json_raises_config_error(tmp_path: Path) -> None:
    """Unparseable JSON becomes a :class:`ConfigError`, never an opaque crash."""
    path = tmp_path / "broken.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ConfigError):
        Config.load(path)
