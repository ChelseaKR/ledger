"""Optional, opt-in AI layer: grounded finding aids and tier-respecting search.

ADR 0013 records the owner-directed decision to add this. Everything in this
package lives strictly at the *edges* of ledger, never in the preservation or
disclosure core:

* Access control is enforced BEFORE any model sees anything.
  :func:`ledger.ai.context.build_context` is the one required entry point for
  every feature in this package, and it works by calling
  :meth:`ledger.ingest.Archive.disclose` FIRST — the exact same single
  disclosure chokepoint (:mod:`ledger.access.policy`) every other read path in
  this repo uses. The type it returns, :class:`~ledger.ai.context.GroundedContext`,
  structurally cannot carry a contributor identity or a withheld field/payload,
  for the same reason :class:`~ledger.models.DisclosedRecord` cannot: the
  source it is built from does not have one.
* Nothing here is a runtime dependency of ledger. The optional ``anthropic``
  SDK is imported lazily, guarded exactly like the optional ``segno`` import in
  :mod:`ledger.print_edition`, so ``import ledger.ai`` — and every deterministic
  preservation/access/browse path in this repo — never requires it installed.
  See ``tests/test_ai_isolation.py``.
* A verifier sits before display. :func:`ledger.ai.grounding.verify_claims`
  checks every model-produced claim's citation against the disclosed evidence
  it is supposed to come from; an unverifiable claim is withheld and counted,
  never shown.
* The outing-refusal rule (a real person's identity must never be inferred,
  guessed, or stated) is enforced in the system prompts
  (:mod:`ledger.ai.prompts`), architecturally (no identity ever reaches a
  :class:`~ledger.ai.context.GroundedContext`), and by a narrow deterministic
  backstop (:func:`ledger.ai.grounding.looks_like_identity_inference`) — proven
  by the adversarial suite in ``tests/test_ai_outing_refusal.py``, not merely
  asserted by any one of those layers alone.

See ``docs/adr/0013-ai-at-the-edges.md`` for the full decision and
``docs/AI-EVALUATION.md`` for the committed eval results and their provenance.
"""

from __future__ import annotations
