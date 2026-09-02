"""`ledger.ai.query.structure_query` — natural-language -> search terms, or an
honest refusal (mission requirement #5/#6: "refused to guess" is a scored,
checkable outcome, not an empty result a caller could mistake for a
no-match).
"""

from __future__ import annotations

import json

import pytest

from ledger.ai.client import CompletionResult
from ledger.ai.prompts import QUERY_STRUCTURE_SYSTEM_PROMPT
from ledger.ai.query import QueryStructureError, structure_query

pytestmark = pytest.mark.disclosure


class _FakeClient:
    def __init__(self, text: str) -> None:
        self._text = text
        self.calls: list[dict[str, object]] = []

    def complete(self, *, system: str, user: str, max_tokens: int) -> CompletionResult:
        self.calls.append({"system": system, "user": user})
        return CompletionResult(text=self._text, backend="fake", model="fake-model")


def test_well_formed_terms_are_returned() -> None:
    payload = {
        "terms": ["mutual", "aid"],
        "date_start": "1990",
        "date_end": "1999",
        "refused": False,
    }
    result = structure_query("mutual aid records from the 90s", _FakeClient(json.dumps(payload)))
    assert result.terms == ("mutual", "aid")
    assert result.date_start == "1990"
    assert result.date_end == "1999"
    assert result.refused is False


def test_refusal_is_a_first_class_field() -> None:
    payload = {"terms": [], "refused": True, "refusal_reason": "too vague to search on"}
    result = structure_query("tell me things", _FakeClient(json.dumps(payload)))
    assert result.refused is True
    assert result.terms == ()
    assert result.refusal_reason == "too vague to search on"


def test_malformed_json_raises() -> None:
    with pytest.raises(QueryStructureError):
        structure_query("anything", _FakeClient("not json"))


def test_uses_the_versioned_query_structure_prompt() -> None:
    client = _FakeClient(json.dumps({"terms": [], "refused": False}))
    structure_query("anything", client)
    assert client.calls[0]["system"] == QUERY_STRUCTURE_SYSTEM_PROMPT


def test_missing_terms_key_defaults_to_empty() -> None:
    result = structure_query("x", _FakeClient(json.dumps({"refused": False})))
    assert result.terms == ()
