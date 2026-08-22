"""AI-output provenance is mandatory, not decorative (mission requirement).

"commit cases + harness + results with provider/model/prompt-version/commit/
date provenance (a test must reject results lacking it)" — this file is that
rejecting test.
"""

from __future__ import annotations

import pytest

from ledger.ai.provenance import UNREVIEWED_LABEL, AIProvenance, ProvenanceError, resolve_commit

pytestmark = pytest.mark.disclosure


def test_complete_provenance_serializes() -> None:
    provenance = AIProvenance(
        provider="bedrock",
        model="global.anthropic.claude-sonnet-4-6",
        prompt_version="ai-v1",
        commit="abc123",
    )
    data = provenance.to_dict()
    assert data["provider"] == "bedrock"
    assert data["model"] == "global.anthropic.claude-sonnet-4-6"
    assert data["promptVersion"] == "ai-v1"
    assert data["commit"] == "abc123"
    assert data["generatedAt"]
    assert data["label"] == UNREVIEWED_LABEL


@pytest.mark.parametrize(
    "field_name",
    ["provider", "model", "prompt_version", "commit"],
)
def test_missing_required_field_is_rejected(field_name: str) -> None:
    """Every one of the mission-named provenance fields is load-bearing: blank
    it out and `to_dict`/`validate` must refuse to produce a record."""
    kwargs = {
        "provider": "bedrock",
        "model": "some-model",
        "prompt_version": "ai-v1",
        "commit": "abc123",
    }
    kwargs[field_name] = ""
    provenance = AIProvenance(**kwargs)
    with pytest.raises(ProvenanceError):
        provenance.validate()
    with pytest.raises(ProvenanceError):
        provenance.to_dict()


def test_blank_generated_at_is_rejected() -> None:
    provenance = AIProvenance(
        provider="bedrock", model="m", prompt_version="ai-v1", commit="abc", generated_at=""
    )
    with pytest.raises(ProvenanceError):
        provenance.to_dict()


def test_from_dict_round_trips() -> None:
    provenance = AIProvenance(
        provider="anthropic", model="claude-sonnet-5", prompt_version="ai-v1", commit="deadbeef"
    )
    rebuilt = AIProvenance.from_dict(provenance.to_dict())
    assert rebuilt == provenance


def test_from_dict_rejects_incomplete_evidence() -> None:
    """A committed eval-evidence file missing a provenance field must fail to
    load, not silently degrade to blank strings."""
    with pytest.raises(ProvenanceError):
        AIProvenance.from_dict({"provider": "bedrock", "model": "", "promptVersion": "ai-v1"})


def test_resolve_commit_is_never_blank() -> None:
    assert resolve_commit() != ""


def test_resolve_commit_prefers_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEDGER_BUILD_COMMIT", "env-commit-sha")
    assert resolve_commit() == "env-commit-sha"


def test_resolve_commit_prefers_github_sha(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LEDGER_BUILD_COMMIT", raising=False)
    monkeypatch.setenv("GITHUB_SHA", "ci-commit-sha")
    assert resolve_commit() == "ci-commit-sha"
