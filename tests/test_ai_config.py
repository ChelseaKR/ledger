"""`AIConfig` (ADR 0013): off by default, no credential lives in the file."""

from __future__ import annotations

from pathlib import Path

import pytest

from ledger.config import AIConfig, Config
from ledger.errors import ConfigError

pytestmark = pytest.mark.disclosure


def test_default_archive_has_ai_disabled(tmp_path: Path) -> None:
    config = Config.default("Test Archive", tmp_path)
    assert config.ai.enabled is False
    assert config.ai.provider == "anthropic"
    assert config.ai.model == "claude-sonnet-5"


def test_ai_section_round_trips_through_to_dict_from_dict(tmp_path: Path) -> None:
    config = Config.default("Test Archive", tmp_path)
    config.ai = AIConfig(
        enabled=True, provider="bedrock", model="global.anthropic.claude-sonnet-4-6"
    )
    rebuilt = Config.from_dict(config.to_dict())
    assert rebuilt.ai == config.ai


def test_a_config_file_with_no_ai_section_defaults_to_disabled(tmp_path: Path) -> None:
    """A pre-ADR-0013 config file (no `ai` key at all) must still load, with
    AI off -- this is the config-level half of "zero AI, byte-for-byte the
    pre-AI system" for an existing archive that upgrades ledger."""
    config = Config.default("Test Archive", tmp_path)
    data = config.to_dict()
    del data["ai"]
    rebuilt = Config.from_dict(data)
    assert rebuilt.ai == AIConfig()
    assert rebuilt.ai.enabled is False


@pytest.mark.parametrize(
    "kwargs",
    [
        {"provider": "openai"},
        {"model": ""},
        {"per_client_rate_limit_per_minute": 0},
        {"daily_request_cap": 0},
        {"max_output_tokens": 0},
    ],
)
def test_invalid_ai_config_is_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(ConfigError):
        # kwargs is deliberately untyped (a parametrized mix of str/int overrides
        # across different AIConfig fields); mypy cannot narrow it field-by-field.
        AIConfig(**kwargs).validate()  # type: ignore[arg-type]


def test_ai_config_is_not_a_credential_store() -> None:
    """No field on `AIConfig` could hold an API key -- this is a structural
    assertion, not just a convention, so a future field addition trips it."""
    field_words = {
        word.lower()
        for f in __import__("dataclasses").fields(AIConfig)
        for word in f.name.split("_")
    }
    assert field_words.isdisjoint({"key", "secret", "token", "credential", "password", "apikey"})


def test_config_validate_rejects_a_bad_ai_section(tmp_path: Path) -> None:
    config = Config.default("Test Archive", tmp_path)
    config.ai = AIConfig(daily_request_cap=0)
    with pytest.raises(ConfigError):
        config.validate()
