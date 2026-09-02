"""The one place ledger talks to a model provider — thin, swappable, optional.

``anthropic`` is **not** a runtime dependency of ``ledger-archive``; it is the
opt-in ``ai`` extra (``pip install ledger-archive[ai]``), imported here the
same way :mod:`ledger.print_edition` imports the optional ``segno`` package: a
guarded top-level ``try``/``except`` so importing this module — or the whole
:mod:`ledger.ai` package — never requires it installed. Every deterministic
preservation/access/browse path in this repo has zero import-time or runtime
dependency on this module; see ``tests/test_ai_isolation.py``.

Two backends behind one narrow interface (:class:`ModelClient`):

* Anthropic's direct API — credential from ``ANTHROPIC_API_KEY`` in the
  environment only, never a file, never a config value, never logged.
* Amazon Bedrock (``anthropic.AnthropicBedrock``) — credential via the
  standard AWS chain (environment, profile, or instance role), also never
  written to a file by this module.

The code default model is ``claude-sonnet-5``; ``LEDGER_AI_MODEL`` overrides
it, which is how a deployment whose Bedrock access is scoped to a different
model (recorded with full provenance in ``docs/AI-EVALUATION.md``) runs
without a code change.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol

from ledger.errors import LedgerError

# `anthropic` is checked by mypy in whichever configuration the checker runs in:
# with the `ai` extra installed it resolves to the real package, without it the
# import fails and the `Any` fallback keeps the module checkable either way.
# `Any` rather than `None` because the two configurations otherwise disagree
# about this name's type, and CI only ever sees one of them -- `make install`
# does not install the extra, so a real-SDK type error would never be caught.
anthropic: Any
try:  # pragma: no cover - exercised in whichever branch the `ai` extra provides
    import anthropic

    _HAVE_ANTHROPIC = True
except ImportError:  # pragma: no cover - exercised when the optional extra is absent
    anthropic = None
    _HAVE_ANTHROPIC = False

__all__ = [
    "DEFAULT_MODEL",
    "AIBackend",
    "AIUnavailable",
    "CompletionResult",
    "ModelClient",
    "build_client",
    "have_anthropic",
]

#: The model the committed evidence was ACTUALLY produced by, pinned here so the
#: code default and the recorded run cannot drift apart.
#:
#: This was `"claude-sonnet-5"`, described as a deliberate choice to keep the
#: code default at Sonnet 5 "regardless of which model a given deployment's
#: Bedrock access authorizes". On this account that identifier does not answer:
#: the entitlement API reports the agreement as AUTHORIZED and `InvokeModel`
#: still returns 403. So the default named a model no run here has ever used,
#: while every committed number came from a different one -- a default that is
#: aspirational rather than true. Verified the only honest way, by invoking:
#: `global.anthropic.claude-sonnet-4-6` answers, and it is what
#: `docs/data/ai-eval/results.json` records in its provenance.
#:
#: `LEDGER_AI_MODEL` still overrides, so a deployment with different Bedrock
#: entitlements sets it there rather than editing this line.
DEFAULT_MODEL = "global.anthropic.claude-sonnet-4-6"

# Env vars, read here and ONLY here — never written to a file, never logged,
# never accepted as a `Config`/CLI value (credentials-from-env-only).
_ENV_MODEL = "LEDGER_AI_MODEL"
_ENV_BACKEND = "LEDGER_AI_BACKEND"  # "anthropic" | "bedrock"; unset = auto-detect
_ENV_ANTHROPIC_KEY = "ANTHROPIC_API_KEY"
_ENV_AWS_REGION = "AWS_REGION"
_DEFAULT_AWS_REGION = "us-east-1"


class AIUnavailable(LedgerError):
    """No usable model backend.

    Raised when the ``ai`` extra is not installed, or no credential is
    present in the environment, or an unknown backend was requested. Callers
    treat this exactly like a provider outage: refuse the AI feature, leave
    everything else untouched (fail closed to the deterministic experience).
    """


class AIBackend:
    """The two supported backend names (``LEDGER_AI_BACKEND`` values)."""

    ANTHROPIC = "anthropic"
    BEDROCK = "bedrock"


@dataclass(frozen=True)
class CompletionResult:
    """One model call's raw text, plus enough to build honest provenance.

    ``backend``/``model`` name what was *actually* used — not what was
    requested — so :mod:`ledger.ai.provenance` records reality even if a
    caller only asked generically.
    """

    text: str
    backend: str
    model: str
    stop_reason: str | None = None
    #: Token counts as the provider reported them, or ``None`` when the backend
    #: did not report any. ``None`` is deliberate and is never coerced to ``0``:
    #: a call whose cost is unknown must not be summed as a free one.
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_input_tokens: int | None = None


def have_anthropic() -> bool:
    """Whether the optional ``anthropic`` SDK is importable in this environment."""
    return _HAVE_ANTHROPIC


class ModelClient(Protocol):
    """The narrow interface every AI feature in this package depends on.

    Deliberately a single method, so a test can supply a hand-written fake
    implementing this shape with no ``anthropic`` install at all. Every
    deterministic test in this package's eval harness runs against a fake;
    only ``tools/ai_eval.py``'s live run (never part of ``make verify`` or CI)
    talks to a real backend.
    """

    def complete(self, *, system: str, user: str, max_tokens: int) -> CompletionResult:
        """Return one completion for ``user`` under the fixed ``system`` prompt."""


class _AnthropicModelClient:
    """The real client: Anthropic direct API or Bedrock, picked at construction."""

    def __init__(self, *, backend: str, model: str, raw_client: object) -> None:
        self._backend = backend
        self._model = model
        self._raw_client = raw_client

    def complete(self, *, system: str, user: str, max_tokens: int) -> CompletionResult:
        # Prompt caching (mission cost control): the system prompt is the part
        # that repeats identically across every call for a given feature and
        # prompt version, so it — and only it — carries `cache_control`.
        response = self._raw_client.messages.create(  # type: ignore[attr-defined]
            model=self._model,
            max_tokens=max_tokens,
            system=[
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}},
            ],
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )
        usage = getattr(response, "usage", None)
        return CompletionResult(
            text=text,
            backend=self._backend,
            model=self._model,
            stop_reason=getattr(response, "stop_reason", None),
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
            cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", None),
        )


def build_client(*, model: str | None = None) -> ModelClient:
    """Construct the real model client from environment credentials only.

    Backend selection: ``LEDGER_AI_BACKEND`` if set; otherwise Anthropic
    direct if ``ANTHROPIC_API_KEY`` is present, else Bedrock. Raises
    :class:`AIUnavailable` — never a bare ``ImportError``/``KeyError`` a
    caller might not expect — whenever the ``ai`` extra is missing or no
    credential is present, so every call site has exactly one exception type
    to handle, the same way it would handle a provider outage.
    """
    if not _HAVE_ANTHROPIC:
        raise AIUnavailable(
            "the `anthropic` package is not installed; run "
            "`pip install ledger-archive[ai]` (or `uv sync --extra ai`) to enable AI features"
        )
    resolved_model = model or os.environ.get(_ENV_MODEL) or DEFAULT_MODEL
    backend = os.environ.get(_ENV_BACKEND, "").strip().lower()
    if not backend:
        backend = AIBackend.ANTHROPIC if os.environ.get(_ENV_ANTHROPIC_KEY) else AIBackend.BEDROCK

    if backend == AIBackend.ANTHROPIC:
        api_key = os.environ.get(_ENV_ANTHROPIC_KEY)
        if not api_key:
            raise AIUnavailable(
                f"{_ENV_BACKEND}=anthropic but {_ENV_ANTHROPIC_KEY} is not set in the environment"
            )
        return _AnthropicModelClient(
            backend=backend,
            model=resolved_model,
            raw_client=anthropic.Anthropic(api_key=api_key),
        )

    if backend == AIBackend.BEDROCK:
        region = os.environ.get(_ENV_AWS_REGION) or _DEFAULT_AWS_REGION
        return _AnthropicModelClient(
            backend=backend,
            model=resolved_model,
            raw_client=anthropic.AnthropicBedrock(aws_region=region),
        )

    raise AIUnavailable(f"unknown {_ENV_BACKEND}={backend!r}; expected 'anthropic' or 'bedrock'")
