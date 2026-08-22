"""Grounded, tier-respecting natural-language discovery (mission requirement #5).

Every record :func:`ask` can draw on comes from :func:`contexts_for`, which
re-derives a :class:`~ledger.ai.context.GroundedContext` for each candidate
record by calling :func:`ledger.ai.context.build_context` again — meaning
:meth:`~ledger.ingest.Archive.disclose` runs a second time, right here, even
though the caller (typically :meth:`~ledger.ingest.Archive.browse` composed
with :func:`ledger.search.search`) already filtered to the viewer's tier. This
is deliberate belt-and-suspenders: the access-control gate is enforced at
THIS module's boundary too, not only trusted from an earlier call, so a
caller that filtered a record list some other way still cannot get
above-tier content into a prompt through this module.

The model is given only what :func:`contexts_for` returns. It cannot invent a
record, a field, or a cross-record link that was not in that set — and
whatever it does produce is still run through the same grounding verifier as
:mod:`ledger.ai.describe`, so a claim mixing evidence from two records still
needs a citation naming one real, disclosed evidence item in one real,
disclosed record (mission requirement #2's aggregation-attack case: this is
what stops a model from synthesizing an identity claim by combining two
individually-safe records — no single evidence item in either record's
context ever contains such a claim for it to cite).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ledger.ai.client import ModelClient
from ledger.ai.context import GroundedContext, build_context
from ledger.ai.grounding import Citation, Claim, verify_claims
from ledger.ai.prompts import ASK_SYSTEM_PROMPT, PROMPT_VERSION
from ledger.ai.provenance import AIProvenance
from ledger.errors import LedgerError
from ledger.ingest import Archive
from ledger.models import DisclosedRecord, Grant

__all__ = ["AskError", "AskResult", "ask", "contexts_for"]


class AskError(LedgerError):
    """The model response could not be parsed at all (see `describe.DescribeError`)."""


@dataclass(frozen=True)
class AskResult:
    """A grounded, cited, verified answer — or an honest "found nothing"."""

    claims: tuple[Claim, ...]
    withheld_count: int
    found_anything: bool
    provenance: AIProvenance

    def to_dict(self) -> dict[str, object]:
        return {
            "claims": [
                {
                    "text": claim.text,
                    "citation": {
                        "kind": claim.citation.kind,
                        "ref": claim.citation.ref,
                        "record_id": claim.citation.record_id,
                        **({"quote": claim.citation.quote} if claim.citation.quote else {}),
                    },
                }
                for claim in self.claims
            ],
            "withheld_count": self.withheld_count,
            "found_anything": self.found_anything,
            "provenance": self.provenance.to_dict(),
        }


def contexts_for(
    archive: Archive, disclosed: list[DisclosedRecord], grant: Grant, now: str | None = None
) -> dict[str, GroundedContext]:
    """Re-derive :class:`~ledger.ai.context.GroundedContext` for each candidate.

    A record that is no longer visible to ``grant`` (revoked, re-sealed, or
    simply passed in by mistake) is silently excluded, exactly like
    :meth:`~ledger.ingest.Archive.browse` excludes a non-listable record from
    a listing — its absence must not itself be informative.
    """
    out: dict[str, GroundedContext] = {}
    for record in disclosed:
        try:
            out[record.record_id] = build_context(archive, record.record_id, grant, now)
        except LedgerError:
            # AccessDenied (no longer listable) or any other disclosure failure:
            # exclude silently, exactly like Archive.browse excludes a
            # non-listable record. Its absence must not itself be informative.
            continue
    return out


def _build_user_prompt(question: str, contexts: dict[str, GroundedContext]) -> str:
    lines = [f"Question: {question}", "", "Disclosed records you may use:"]
    if not contexts:
        lines.append("(none -- the archive returned no records for this query)")
    for record_id, context in contexts.items():
        lines.append(f"\nRecord {record_id}:")
        for item in context.evidence:
            lines.append(f"- kind={item.kind} ref={item.ref!r}: {item.text}")
    return "\n".join(lines)


def _parse(raw_text: str) -> tuple[list[Claim], bool]:
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise AskError(f"model response was not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise AskError("model response JSON was not an object")
    found = bool(data.get("found_anything", False))
    claims: list[Claim] = []
    for entry in data.get("claims", []) or []:
        if not isinstance(entry, dict) or "text" not in entry or "citation" not in entry:
            continue
        citation_raw = entry["citation"]
        if (
            not isinstance(citation_raw, dict)
            or "kind" not in citation_raw
            or "ref" not in citation_raw
        ):
            continue
        claims.append(
            Claim(
                text=str(entry["text"]),
                citation=Citation(
                    kind=str(citation_raw["kind"]),
                    ref=str(citation_raw["ref"]),
                    record_id=str(citation_raw.get("record_id", "")),
                    quote=str(citation_raw.get("quote", "")),
                ),
            )
        )
    return claims, found


def ask(
    question: str,
    contexts: dict[str, GroundedContext],
    client: ModelClient,
    *,
    commit: str,
    max_tokens: int = 1024,
) -> AskResult:
    """Answer ``question`` using ONLY ``contexts``.

    Refuses — ``found_anything=False``, no claims — rather than guessing when
    nothing in ``contexts`` answers the question, or when every claim the
    model offered fails grounding (mission requirement #6).
    """
    result = client.complete(
        system=ASK_SYSTEM_PROMPT, user=_build_user_prompt(question, contexts), max_tokens=max_tokens
    )
    claims, found = _parse(result.text)
    grounding = verify_claims(claims, contexts)
    provenance = AIProvenance(
        provider=result.backend, model=result.model, prompt_version=PROMPT_VERSION, commit=commit
    )
    return AskResult(
        claims=grounding.verified,
        withheld_count=grounding.withheld_count,
        found_anything=found and bool(grounding.verified),
        provenance=provenance,
    )
