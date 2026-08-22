"""`ledger ai-describe` / `ledger ai-ask` — the opt-in CLI entry points.

Exercises `cli.main([...])` exactly as a steward or script would, over a real
on-disk archive built via `tests/ai_fixtures.py`. The real model client is
monkeypatched (a hand-written fake, `ledger.ai.client.ModelClient`'s single
method) so this suite needs no network and no `anthropic` install, matching
every other deterministic test in this package.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ledger import cli
from ledger.ai.client import CompletionResult
from tests import ai_fixtures as fx


class _FakeClient:
    def __init__(self, text: str) -> None:
        self._text = text

    def complete(self, *, system: str, user: str, max_tokens: int) -> CompletionResult:
        return CompletionResult(text=self._text, backend="fake", model="fake-model-v1")


@pytest.fixture
def archive_root(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    root = tmp_path / "arc"
    archive = fx.build_archive(root)
    ids = fx.seed(archive)
    return root, ids


def _enable_ai(root: Path) -> None:
    config_path = root / "store" / "config.json"
    data = json.loads(config_path.read_text(encoding="utf-8"))
    data["ai"]["enabled"] = True
    config_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def test_ai_describe_refuses_with_no_model_call_when_disabled(
    archive_root: tuple[Path, dict[str, str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, ids = archive_root
    rc = cli.main(["ai-describe", "--root", str(root), "--id", ids["public_a"]])
    assert rc == 1
    err = capsys.readouterr().err
    assert "disabled" in err
    assert "config.ai.enabled" in err


def test_ai_ask_refuses_with_no_model_call_when_disabled(
    archive_root: tuple[Path, dict[str, str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, _ids = archive_root
    rc = cli.main(["ai-ask", "--root", str(root), "--question", "what is this about"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "disabled" in err


def test_ai_describe_reports_unavailable_when_enabled_but_no_backend(
    archive_root: tuple[Path, dict[str, str]],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, ids = archive_root
    _enable_ai(root)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    from ledger.ai.client import AIUnavailable

    def _raise(*, model: str | None = None) -> None:
        raise AIUnavailable("no backend available (test)")

    monkeypatch.setattr(cli, "build_ai_client", _raise)
    rc = cli.main(["ai-describe", "--root", str(root), "--id", ids["public_a"]])
    assert rc == 1
    assert "unavailable" in capsys.readouterr().err


def test_ai_describe_end_to_end_with_a_fake_client(
    archive_root: tuple[Path, dict[str, str]],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, ids = archive_root
    _enable_ai(root)
    claims = [{"text": "About mutual aid.", "citation": {"kind": "dublin_core", "ref": "subject"}}]
    monkeypatch.setattr(
        cli, "build_ai_client", lambda *, model=None: _FakeClient(json.dumps(claims))
    )
    rc = cli.main(["ai-describe", "--root", str(root), "--id", ids["public_a"]])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["record_id"] == ids["public_a"]
    assert len(out["claims"]) == 1
    assert out["provenance"]["label"] == "AI-generated, unreviewed"


def test_ai_describe_denies_a_sealed_record_with_no_model_call(
    archive_root: tuple[Path, dict[str, str]],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Access control runs before the client is even constructed."""
    root, ids = archive_root
    _enable_ai(root)
    calls: list[str] = []
    monkeypatch.setattr(
        cli, "build_ai_client", lambda *, model=None: calls.append("called") or _FakeClient("[]")
    )
    rc = cli.main(["ai-describe", "--root", str(root), "--id", ids["sealed"]])
    assert rc == 1
    assert calls == []
    assert "cannot describe" in capsys.readouterr().err


def test_ai_ask_end_to_end_with_a_fake_client(
    archive_root: tuple[Path, dict[str, str]],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, ids = archive_root
    _enable_ai(root)
    payload = {
        "claims": [
            {
                "text": "One record is about mutual aid.",
                "citation": {"kind": "dublin_core", "ref": "subject", "record_id": ids["public_a"]},
            }
        ],
        "found_anything": True,
    }
    monkeypatch.setattr(
        cli, "build_ai_client", lambda *, model=None: _FakeClient(json.dumps(payload))
    )
    rc = cli.main(["ai-ask", "--root", str(root), "--question", "mutual aid"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["found_anything"] is True
    assert len(out["claims"]) == 1


def test_ai_ask_never_surfaces_above_tier_records_to_anonymous(
    archive_root: tuple[Path, dict[str, str]],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fake client that tries to answer about a sealed record must still be
    refused -- the record never even reaches the prompt (`ai_contexts_for`
    excludes it before `client.complete` is called)."""
    root, ids = archive_root
    _enable_ai(root)
    payload = {
        "claims": [
            {
                "text": "This describes the sealed record.",
                "citation": {"kind": "field", "ref": "story", "record_id": ids["sealed"]},
            }
        ],
        "found_anything": True,
    }
    monkeypatch.setattr(
        cli, "build_ai_client", lambda *, model=None: _FakeClient(json.dumps(payload))
    )
    rc = cli.main(["ai-ask", "--root", str(root), "--question", "tell me about the sealed record"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["claims"] == []
    assert out["found_anything"] is False


def test_ai_rate_limit_refuses_after_the_daily_cap(
    archive_root: tuple[Path, dict[str, str]],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, ids = archive_root
    _enable_ai(root)
    config_path = root / "store" / "config.json"
    data = json.loads(config_path.read_text(encoding="utf-8"))
    data["ai"]["daily_request_cap"] = 1
    config_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    monkeypatch.setattr(cli, "build_ai_client", lambda *, model=None: _FakeClient("[]"))

    rc1 = cli.main(["ai-describe", "--root", str(root), "--id", ids["public_a"]])
    assert rc1 == 0
    capsys.readouterr()
    rc2 = cli.main(["ai-describe", "--root", str(root), "--id", ids["public_b"]])
    assert rc2 == 1
    assert "refused" in capsys.readouterr().err
