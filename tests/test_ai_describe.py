"""End-to-end `generate_finding_aid`, against a hand-written fake client.

No network, no `anthropic` install required: `ModelClient` is a one-method
protocol (`ledger.ai.client.ModelClient`), so a fake implementing it exercises
the full describe -> parse -> verify -> provenance pipeline deterministically.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ledger.ai.client import CompletionResult
from ledger.ai.context import build_context
from ledger.ai.describe import DescribeError, generate_finding_aid
from ledger.ai.provenance import UNREVIEWED_LABEL
from tests import ai_fixtures as fx

pytestmark = pytest.mark.disclosure


class _FakeClient:
    def __init__(self, text: str) -> None:
        self._text = text
        self.calls: list[dict[str, object]] = []

    def complete(self, *, system: str, user: str, max_tokens: int) -> CompletionResult:
        self.calls.append({"system": system, "user": user, "max_tokens": max_tokens})
        return CompletionResult(text=self._text, backend="fake", model="fake-model-v1")


@pytest.fixture
def seeded_context(tmp_path: Path):
    archive = fx.build_archive(tmp_path)
    ids = fx.seed(archive)
    return build_context(archive, ids["public_a"], fx.anonymous_grant())


def test_well_formed_claims_are_verified(seeded_context) -> None:
    claims = [
        {
            "text": "This is about mutual aid.",
            "citation": {"kind": "dublin_core", "ref": "subject"},
        },
    ]
    client = _FakeClient(json.dumps(claims))
    finding_aid = generate_finding_aid(seeded_context, client, commit="abc123")
    assert len(finding_aid.claims) == 1
    assert finding_aid.withheld_count == 0
    assert finding_aid.record_id == seeded_context.record_id


def test_unverifiable_claims_are_withheld_and_counted_not_shown(seeded_context) -> None:
    claims = [
        {"text": "About mutual aid.", "citation": {"kind": "dublin_core", "ref": "subject"}},
        {"text": "Fabricated.", "citation": {"kind": "field", "ref": "does-not-exist"}},
    ]
    client = _FakeClient(json.dumps(claims))
    finding_aid = generate_finding_aid(seeded_context, client, commit="abc123")
    assert len(finding_aid.claims) == 1
    assert finding_aid.withheld_count == 1


def test_malformed_json_raises_describe_error(seeded_context) -> None:
    client = _FakeClient("not json at all")
    with pytest.raises(DescribeError):
        generate_finding_aid(seeded_context, client, commit="abc123")


def test_non_list_json_raises_describe_error(seeded_context) -> None:
    client = _FakeClient(json.dumps({"not": "a list"}))
    with pytest.raises(DescribeError):
        generate_finding_aid(seeded_context, client, commit="abc123")


def test_malformed_entries_are_skipped_not_fatal(seeded_context) -> None:
    claims = [
        {"text": "missing citation entirely"},
        {"citation": {"kind": "dublin_core", "ref": "subject"}},  # missing text
        {"text": "ok", "citation": {"kind": "dublin_core", "ref": "subject"}},
    ]
    client = _FakeClient(json.dumps(claims))
    finding_aid = generate_finding_aid(seeded_context, client, commit="abc123")
    assert len(finding_aid.claims) == 1


def test_provenance_names_the_backend_and_model_actually_used(seeded_context) -> None:
    client = _FakeClient(json.dumps([]))
    finding_aid = generate_finding_aid(seeded_context, client, commit="deadbeef")
    assert finding_aid.provenance.provider == "fake"
    assert finding_aid.provenance.model == "fake-model-v1"
    assert finding_aid.provenance.commit == "deadbeef"
    assert finding_aid.provenance.label == UNREVIEWED_LABEL


def test_to_dict_embeds_full_provenance(seeded_context) -> None:
    client = _FakeClient(json.dumps([]))
    finding_aid = generate_finding_aid(seeded_context, client, commit="deadbeef")
    data = finding_aid.to_dict()
    assert data["provenance"]["commit"] == "deadbeef"
    assert data["provenance"]["label"] == UNREVIEWED_LABEL


def test_max_tokens_is_forwarded_to_the_client(seeded_context) -> None:
    client = _FakeClient(json.dumps([]))
    generate_finding_aid(seeded_context, client, commit="abc123", max_tokens=42)
    assert client.calls[0]["max_tokens"] == 42


def test_system_prompt_is_the_versioned_describe_prompt(seeded_context) -> None:
    from ledger.ai.prompts import DESCRIBE_SYSTEM_PROMPT

    client = _FakeClient(json.dumps([]))
    generate_finding_aid(seeded_context, client, commit="abc123")
    assert client.calls[0]["system"] == DESCRIBE_SYSTEM_PROMPT
