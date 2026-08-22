"""`ledger.ai.client` — backend selection, env-only credentials, and the
Anthropic Messages API request/response shape.

`anthropic` is not installed in this dev/CI environment (the `ai` extra is
opt-in), so this suite monkeypatches `ledger.ai.client.anthropic` with a tiny
hand-written stand-in exposing the same two constructors
(`Anthropic`/`AnthropicBedrock`) the real SDK does, and exercises
`build_client`/`ModelClient.complete` end to end through the public API —
proving the request shape (model, max_tokens, system with `cache_control`,
one user message) and the response parsing (concatenated text blocks, stop
reason) without needing the real SDK.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from ledger.ai import client as client_mod
from ledger.ai.client import AIUnavailable, build_client, have_anthropic


@dataclass
class _FakeTextBlock:
    text: str
    type: str = "text"


@dataclass
class _FakeResponse:
    content: list[_FakeTextBlock]
    stop_reason: str = "end_turn"


@dataclass
class _FakeMessages:
    calls: list[dict[str, object]] = field(default_factory=list)
    reply_text: str = "[]"

    def create(self, **kwargs: object) -> _FakeResponse:
        self.calls.append(kwargs)
        return _FakeResponse(content=[_FakeTextBlock(self.reply_text)])


class _FakeRawClient:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.messages = _FakeMessages()


class _FakeAnthropicModule:
    def __init__(self) -> None:
        self.anthropic_instances: list[_FakeRawClient] = []
        self.bedrock_instances: list[_FakeRawClient] = []

    def Anthropic(self, **kwargs: object) -> _FakeRawClient:
        instance = _FakeRawClient(**kwargs)
        self.anthropic_instances.append(instance)
        return instance

    def AnthropicBedrock(self, **kwargs: object) -> _FakeRawClient:
        instance = _FakeRawClient(**kwargs)
        self.bedrock_instances.append(instance)
        return instance


@pytest.fixture
def fake_anthropic(monkeypatch: pytest.MonkeyPatch) -> _FakeAnthropicModule:
    """Install a fake `anthropic` module and force `_HAVE_ANTHROPIC = True`."""
    fake = _FakeAnthropicModule()
    monkeypatch.setattr(client_mod, "anthropic", fake)
    monkeypatch.setattr(client_mod, "_HAVE_ANTHROPIC", True)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("LEDGER_AI_BACKEND", raising=False)
    monkeypatch.delenv("LEDGER_AI_MODEL", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)
    return fake


def test_have_anthropic_reflects_the_real_import_state() -> None:
    # In this dev/CI environment the `ai` extra is not installed, so this is
    # simply asserting the module told the truth about its own state.
    assert have_anthropic() == client_mod._HAVE_ANTHROPIC


def test_build_client_raises_ai_unavailable_when_anthropic_is_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client_mod, "_HAVE_ANTHROPIC", False)
    with pytest.raises(AIUnavailable, match="not installed"):
        build_client()


def test_defaults_to_anthropic_backend_when_api_key_present(
    fake_anthropic: _FakeAnthropicModule, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    client = build_client()
    result = client.complete(system="sys", user="hello", max_tokens=100)
    assert result.backend == "anthropic"
    assert len(fake_anthropic.anthropic_instances) == 1
    assert fake_anthropic.anthropic_instances[0].kwargs == {"api_key": "sk-test-key"}


def test_defaults_to_bedrock_backend_when_no_api_key(
    fake_anthropic: _FakeAnthropicModule,
) -> None:
    client = build_client()
    result = client.complete(system="sys", user="hello", max_tokens=100)
    assert result.backend == "bedrock"
    assert len(fake_anthropic.bedrock_instances) == 1
    assert fake_anthropic.bedrock_instances[0].kwargs == {"aws_region": "us-east-1"}


def test_bedrock_region_follows_aws_region_env(
    fake_anthropic: _FakeAnthropicModule, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    build_client()
    assert fake_anthropic.bedrock_instances[0].kwargs == {"aws_region": "us-west-2"}


def test_explicit_backend_env_overrides_autodetection(
    fake_anthropic: _FakeAnthropicModule, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    monkeypatch.setenv("LEDGER_AI_BACKEND", "bedrock")
    client = build_client()
    result = client.complete(system="sys", user="hello", max_tokens=10)
    assert result.backend == "bedrock"


def test_anthropic_backend_without_api_key_is_unavailable(
    fake_anthropic: _FakeAnthropicModule, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LEDGER_AI_BACKEND", "anthropic")
    with pytest.raises(AIUnavailable, match="ANTHROPIC_API_KEY"):
        build_client()


def test_unknown_backend_is_unavailable(
    fake_anthropic: _FakeAnthropicModule, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LEDGER_AI_BACKEND", "openai")
    with pytest.raises(AIUnavailable, match="unknown"):
        build_client()


def test_model_defaults_to_sonnet_5(fake_anthropic: _FakeAnthropicModule) -> None:
    client = build_client()
    result = client.complete(system="sys", user="hello", max_tokens=10)
    assert result.model == "claude-sonnet-5"


def test_explicit_model_argument_wins_over_env(
    fake_anthropic: _FakeAnthropicModule, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LEDGER_AI_MODEL", "env-model")
    client = build_client(model="explicit-model")
    result = client.complete(system="sys", user="hello", max_tokens=10)
    assert result.model == "explicit-model"


def test_env_model_overrides_the_code_default(
    fake_anthropic: _FakeAnthropicModule, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LEDGER_AI_MODEL", "global.anthropic.claude-sonnet-4-6")
    client = build_client()
    result = client.complete(system="sys", user="hello", max_tokens=10)
    assert result.model == "global.anthropic.claude-sonnet-4-6"


def test_request_shape_carries_prompt_caching_on_the_system_prompt(
    fake_anthropic: _FakeAnthropicModule,
) -> None:
    client = build_client()
    client.complete(system="the system prompt", user="the user prompt", max_tokens=256)
    call = fake_anthropic.bedrock_instances[0].messages.calls[0]
    assert call["max_tokens"] == 256
    assert call["system"] == [
        {"type": "text", "text": "the system prompt", "cache_control": {"type": "ephemeral"}}
    ]
    assert call["messages"] == [{"role": "user", "content": "the user prompt"}]


def test_response_concatenates_only_text_blocks(fake_anthropic: _FakeAnthropicModule) -> None:
    """A response block without `type == "text"` (e.g. a future block kind)
    must be skipped, never concatenated in as if it were text."""
    client = build_client()
    raw = fake_anthropic.bedrock_instances

    # Prime the fake to return a mixed block list on the next call.
    class _NonTextBlock:
        type = "other"
        text = "should not appear"

    def _create(**kwargs: object) -> _FakeResponse:
        return _FakeResponse(
            content=[_FakeTextBlock("hello "), _NonTextBlock(), _FakeTextBlock("world")],
            stop_reason="end_turn",
        )

    # build_client() above already constructed one bedrock instance; patch its
    # messages.create for this one call.
    instance = raw[0]
    # A deliberate monkeypatch of a fake test double's method, not a real SDK one.
    instance.messages.create = _create  # type: ignore[method-assign]
    result = client.complete(system="s", user="u", max_tokens=10)
    assert result.text == "hello world"
    assert result.stop_reason == "end_turn"
