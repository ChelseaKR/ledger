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


class _RecordingFakeClient:
    """A fake that keeps the exact prompts it was handed.

    The point of the tier assertion below is what the model was *shown*, not
    what it happened to answer, so the wire has to be inspectable.
    """

    def __init__(self, text: str) -> None:
        self._text = text
        self.calls: list[tuple[str, str]] = []

    def complete(self, *, system: str, user: str, max_tokens: int) -> CompletionResult:
        self.calls.append((system, user))
        return CompletionResult(text=self._text, backend="fake", model="fake-model-v1")


#: The sealed fixture record's own field text. It must never appear in a prompt.
_SEALED_FIELD_TEXT = "This must never be listed to a non-steward viewer."


def test_ai_ask_never_surfaces_above_tier_records_to_anonymous(
    archive_root: tuple[Path, dict[str, str]],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tier enforcement on the `ask` path, asserted on the wire.

    This test used to ask "tell me about the sealed record". `cli._cmd_ai_ask`
    pre-filters candidates with `ledger.search.search`, which is a logical AND
    over the whitespace-split query terms, and no fixture record contains
    "tell", "about" AND "sealed" together -- so retrieval returned nothing, the
    claim was withheld for having no context at all, and the assertion would
    have passed identically with every access-control check deleted. A vacuous
    tier-safety test on the outing-refusal path is worse than no test, because
    it reads as coverage.

    So: the question is now the single term "text", which is the Dublin Core
    *type* every fixture record carries -- including the sealed one. That
    choice is the whole point. A question the sealed record does not match
    would be excluded by RETRIEVAL rather than by TIER, and the test would be
    vacuous again in a new way. "text" matches every record, so the only thing
    that can keep the sealed record out of the prompt is access control.

    The fake model is then handed a claim citing the SEALED record, and three
    separate things are asserted --

    1. retrieval was not empty (the case is live, not a repeat of the vacuum);
    2. the sealed record's id, title, and sealed field text appear NOWHERE in
       the prompt the model was shown; and
    3. the model's attempt to answer about the sealed record is dropped, so it
       reaches no caller.

    Remove tier enforcement and (2) fails immediately: the sealed record enters
    `Archive.browse`'s output, matches the pre-filter like every other record,
    and is rendered into the user prompt. Verified by neutering `is_visible`
    and `is_listable` to `return True` and watching this test go red.
    """
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
    fake = _RecordingFakeClient(json.dumps(payload))
    monkeypatch.setattr(cli, "build_ai_client", lambda *, model=None: fake)
    rc = cli.main(["ai-ask", "--root", str(root), "--question", "text"])
    assert rc == 0

    # (1) The case is live: the model was actually called, over real candidates.
    assert len(fake.calls) == 1, "the model was never called; this case is vacuous again"
    _system, user_prompt = fake.calls[0]
    assert ids["public_a"] in user_prompt, (
        "retrieval returned nothing, so nothing about tiers is being tested here"
    )
    assert ids["public_b"] in user_prompt

    # (2) The sealed record never reached the prompt, by id or by content.
    assert ids["sealed"] not in user_prompt
    assert _SEALED_FIELD_TEXT not in user_prompt
    assert "Sealed record, indefinite" not in user_prompt

    # (3) The model's attempt to answer about it is dropped before any caller.
    out = json.loads(capsys.readouterr().out)
    assert out["claims"] == []
    assert out["found_anything"] is False
    assert ids["sealed"] not in json.dumps(out)


def test_ai_ask_shows_a_steward_the_sealed_record_it_is_entitled_to(
    archive_root: tuple[Path, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The positive control for the test above.

    Without it, the tier assertion could be satisfied by a pipeline that shows
    the model nothing at all, for anyone. The *same* question, from a steward,
    must reach the sealed record -- which is what proves the anonymous case was
    excluded by tier rather than by anything else in the pipeline.
    """
    root, ids = archive_root
    _enable_ai(root)
    fake = _RecordingFakeClient(json.dumps({"claims": [], "found_anything": False}))
    monkeypatch.setattr(cli, "build_ai_client", lambda *, model=None: fake)
    rc = cli.main(["ai-ask", "--root", str(root), "--question", "text", "--as", "steward"])
    assert rc == 0
    assert len(fake.calls) == 1
    _system, user_prompt = fake.calls[0]
    assert ids["sealed"] in user_prompt, (
        "a steward must be able to reach the sealed record; if not, the anonymous "
        "test above proves nothing about tiers"
    )


def test_the_ask_prefilter_is_an_and_over_terms_so_a_prose_question_matches_nothing(
    archive_root: tuple[Path, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A known narrowness of `ai-ask`, pinned rather than left implicit.

    `cli._cmd_ai_ask` retrieves with `ledger.search.search`, an AND over the
    whitespace-split query terms, so a prose question requires every one of its
    words to appear in one record and in practice matches nothing. That is what
    made the tier test above vacuous. Widening retrieval changes what reaches a
    paid model on the outing-refusal path and deserves its own review, so the
    behaviour is pinned here and tracked, rather than being rediscovered as a
    surprise by the next person who writes a test with a prose question.
    """
    root, _ids = archive_root
    _enable_ai(root)
    fake = _RecordingFakeClient(json.dumps({"claims": [], "found_anything": False}))
    monkeypatch.setattr(cli, "build_ai_client", lambda *, model=None: fake)
    rc = cli.main(["ai-ask", "--root", str(root), "--question", "tell me about mutual aid please"])
    assert rc == 0
    assert len(fake.calls) == 1
    _system, user_prompt = fake.calls[0]
    assert "(none -- the archive returned no records for this query)" in user_prompt


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
