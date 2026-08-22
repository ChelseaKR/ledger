"""Natural-language query structuring (mission requirement #5's model-assisted
half): turn a free-text search request into the small, deterministic
vocabulary `ledger.search` already consumes (terms, an optional date range),
or an honest refusal when the request cannot be turned into one.

This is deliberately NOT wired into `ledger ai-ask`'s default flow yet: that
command already runs the request string directly through the existing
deterministic `ledger.search.search` (itself just a whitespace-split
substring match) before any model call, which is simpler and needs no
grounding verifier of its own. `structure_query` exists as a separately
evaluated capability (`tools/ai_eval.py`'s query-structuring suite) — a
future integration would use its output only to build the SAME
`ledger.search.search`/`filter_by_date_range` call `ai-ask` already makes, so
a mis-structured query can narrow or miss results but can never itself reach
a model with ungrounded content (the search index it feeds is exactly the
viewer's own `Archive.browse` output).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ledger.ai.client import ModelClient
from ledger.ai.prompts import QUERY_STRUCTURE_SYSTEM_PROMPT
from ledger.errors import LedgerError

__all__ = ["QueryStructureError", "StructuredQuery", "structure_query"]


class QueryStructureError(LedgerError):
    """The model response could not be parsed at all."""


@dataclass(frozen=True)
class StructuredQuery:
    """The result of turning a natural-language request into search terms.

    ``refused`` is `True` when the request could not honestly be turned into
    search terms (too vague, or itself an identity question rather than a
    topic/era/kind-of-record question) — the mission's "refused to guess"
    requirement made an explicit, checkable field rather than an empty list a
    caller could mistake for "no terms happened to match."
    """

    terms: tuple[str, ...]
    date_start: str
    date_end: str
    refused: bool
    refusal_reason: str


def _parse(raw_text: str) -> StructuredQuery:
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise QueryStructureError(f"model response was not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise QueryStructureError("model response JSON was not an object")
    terms_raw = data.get("terms", [])
    terms = tuple(str(t) for t in terms_raw) if isinstance(terms_raw, list) else ()
    return StructuredQuery(
        terms=terms,
        date_start=str(data.get("date_start", "")),
        date_end=str(data.get("date_end", "")),
        refused=bool(data.get("refused", False)),
        refusal_reason=str(data.get("refusal_reason", "")),
    )


def structure_query(
    nl_query: str, client: ModelClient, *, max_tokens: int = 256
) -> StructuredQuery:
    """Ask the model to extract search terms/date range from `nl_query`.

    Pure extraction, never an answer: the system prompt
    (`ledger.ai.prompts.QUERY_STRUCTURE_SYSTEM_PROMPT`) explicitly forbids
    describing any record. `refused=True` with no terms is the correct,
    scored outcome for a vague or identity-shaped request — never a guessed
    term list.
    """
    result = client.complete(
        system=QUERY_STRUCTURE_SYSTEM_PROMPT, user=nl_query, max_tokens=max_tokens
    )
    return _parse(result.text)
