"""Grounded finding-aid generation (mission requirement #1).

:func:`generate_finding_aid` is the only entry point. It takes a
:class:`~ledger.ai.context.GroundedContext` — already access-controlled by
:func:`ledger.ai.context.build_context`, which the caller must have called —
and a :class:`~ledger.ai.client.ModelClient`, asks the model for a small set
of cited claims, and runs every one of them through
:func:`ledger.ai.grounding.verify_claims` before returning. An unverifiable
claim never reaches the caller: it is withheld and counted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ledger.ai.client import ModelClient
from ledger.ai.context import GroundedContext
from ledger.ai.grounding import Citation, Claim, verify_claims
from ledger.ai.prompts import DESCRIBE_SYSTEM_PROMPT, PROMPT_VERSION
from ledger.ai.provenance import AIProvenance
from ledger.errors import LedgerError

__all__ = ["DescribeError", "FindingAid", "generate_finding_aid"]


class DescribeError(LedgerError):
    """The model response could not be parsed into claims at all.

    Not a grounding failure — those are withheld per-claim, not a hard error.
    This is raised only when the response isn't even well-formed JSON of the
    expected shape.
    """


@dataclass(frozen=True)
class FindingAid:
    """A grounded, cited, verified finding aid for one record."""

    record_id: str
    claims: tuple[Claim, ...]
    withheld_count: int
    provenance: AIProvenance

    def to_dict(self) -> dict[str, object]:
        return {
            "record_id": self.record_id,
            "claims": [
                {"text": claim.text, "citation": _citation_dict(claim.citation)}
                for claim in self.claims
            ],
            "withheld_count": self.withheld_count,
            "provenance": self.provenance.to_dict(),
        }


def _citation_dict(citation: Citation) -> dict[str, str]:
    out = {"kind": citation.kind, "ref": citation.ref}
    if citation.quote:
        out["quote"] = citation.quote
    return out


def _build_user_prompt(context: GroundedContext) -> str:
    lines = [f"Evidence for record {context.record_id}:"]
    for item in context.evidence:
        lines.append(f"- kind={item.kind} ref={item.ref!r}: {item.text}")
    return "\n".join(lines)


def _parse_claims(raw_text: str) -> list[Claim]:
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise DescribeError(f"model response was not valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise DescribeError("model response JSON was not a list of claims")
    claims: list[Claim] = []
    for entry in data:
        if not isinstance(entry, dict) or "text" not in entry or "citation" not in entry:
            continue  # malformed entry: skip rather than fail the whole response
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
                    quote=str(citation_raw.get("quote", "")),
                ),
            )
        )
    return claims


def generate_finding_aid(
    context: GroundedContext,
    client: ModelClient,
    *,
    commit: str,
    max_tokens: int = 1024,
) -> FindingAid:
    """Generate, then VERIFY, a finding aid for one already-disclosed record.

    :func:`ledger.ai.grounding.verify_claims` runs unconditionally before this
    returns; an unverifiable claim is dropped and counted, never shown,
    regardless of how confident the model's own text sounded. Provenance
    (:class:`~ledger.ai.provenance.AIProvenance`) names the backend and model
    the call actually used, not merely what was requested.
    """
    result = client.complete(
        system=DESCRIBE_SYSTEM_PROMPT, user=_build_user_prompt(context), max_tokens=max_tokens
    )
    claims = _parse_claims(result.text)
    grounding = verify_claims(claims, context)
    provenance = AIProvenance(
        provider=result.backend, model=result.model, prompt_version=PROMPT_VERSION, commit=commit
    )
    return FindingAid(
        record_id=context.record_id,
        claims=grounding.verified,
        withheld_count=grounding.withheld_count,
        provenance=provenance,
    )
