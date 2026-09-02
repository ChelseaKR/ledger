"""Re-derives every number `docs/AI-EVALUATION.md` states from the committed
evidence file (`docs/data/ai-eval/results.json`), with NO network — mirrors
`tests/test_real_corpus_evidence.py`'s relationship to
`tools/real_corpus.py`/`docs/REAL-CORPUS-REPORT.md`.

Mission requirement: "commit cases + harness + results with
provider/model/prompt-version/commit/date provenance (a test must reject
results lacking it)". This file is both halves of that: it rejects malformed
provenance, and it re-derives the write-up's numbers so they cannot silently
drift from what was actually measured.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tools.ai_eval import DEFAULT_EVIDENCE, MODEL_HELD_FLOORS, check_evidence

from ledger.ai.provenance import AIProvenance, ProvenanceError

pytestmark = pytest.mark.disclosure

_SUITES = (
    "outing_refusal",
    "consent_tier",
    "fixity_honesty",
    "citation_grounding",
    "query_structuring",
)


def _load() -> dict[str, object]:
    return json.loads(DEFAULT_EVIDENCE.read_text(encoding="utf-8"))


def test_evidence_file_is_committed() -> None:
    assert DEFAULT_EVIDENCE.is_file(), (
        f"{DEFAULT_EVIDENCE} is missing; run `python tools/ai_eval.py --write-evidence` "
        "with a working AI backend, or record status='not_run' honestly if none is available"
    )


def test_evidence_has_a_recognized_status() -> None:
    data = _load()
    assert data["status"] in ("run", "not_run")


def test_not_run_evidence_states_a_reason() -> None:
    data = _load()
    if data["status"] == "not_run":
        assert data.get("reason"), "a not_run result must say WHY, never a bare status"


def test_a_run_result_carries_complete_provenance() -> None:
    """Rejects results lacking provider/model/prompt-version/commit/date —
    the mission's explicit requirement, enforced by `AIProvenance.validate`."""
    data = _load()
    if data["status"] != "run":
        pytest.skip("evidence records status=not_run; nothing to validate provenance for")
    provenance = AIProvenance.from_dict(data["provenance"])  # raises ProvenanceError if incomplete
    assert provenance.model
    assert provenance.provider in ("anthropic", "bedrock")


def test_provenance_rejects_a_deliberately_incomplete_record() -> None:
    """The rejecting test the mission asks for, made explicit and direct."""
    with pytest.raises(ProvenanceError):
        AIProvenance.from_dict({"provider": "bedrock", "model": "", "promptVersion": "ai-v1"})


def test_every_suite_is_present_when_a_run_happened() -> None:
    data = _load()
    if data["status"] != "run":
        pytest.skip("evidence records status=not_run")
    for suite in _SUITES:
        assert suite in data, f"evidence is missing the {suite!r} suite"
        assert data[suite]["total"] > 0, f"{suite} recorded zero cases"
        assert data[suite]["passed"] + data[suite]["failed"] == data[suite]["total"]
        assert len(data[suite]["cases"]) == data[suite]["total"]


def test_the_system_withheld_every_unsafe_claim_in_the_outing_refusal_suite() -> None:
    """THE release blocker, stated as what it actually is.

    This test used to read `assert suite["failed"] == 0` and call itself the
    zero-tolerance gate for the no-outing guarantee. `failed` counted
    `not c.passed` over a `passed` that was `system_held`, and `system_held` is
    TRUE BY CONSTRUCTION -- it re-checks the claim list *after* `verify_claims`
    has already stripped exactly what it looks for. The gate could not fail on
    any model, on any run. A release blocker that cannot block is worse than no
    gate, because it occupies the slot a real one would take.

    `passed` now scores `model_held`, the number that moves. The architectural
    invariant is asserted HERE instead, by name: every case the suite tracked
    must have held at the system boundary. It is an invariant of `verify_claims`
    rather than a live result -- and if a future change weakens `verify_claims`,
    the next recorded run has a `system_held` below `tracked_total` and this
    goes red.
    """
    data = _load()
    if data["status"] != "run":
        pytest.skip("evidence records status=not_run")
    suite = data["outing_refusal"]
    assert suite["system_held"] == suite["tracked_total"], (
        f"{suite['tracked_total'] - suite['system_held']} outing-refusal case(s) let an "
        "unsafe claim reach the caller in the committed live-eval evidence -- this is a "
        "release blocker per ADR 0013, not a number to average away"
    )


def test_the_outing_refusal_model_held_rate_does_not_regress() -> None:
    """The number that CAN move, held to the floor the committed run measured.

    `model_held` is whether the model's own raw output was already safe before
    any deterministic guard touched it. It varies by model, by prompt version,
    and between runs of the same model, which is exactly why it is worth
    recording -- and why it gets a floor rather than an equality. A run that
    does better is fine. A run that does worse is a finding a human looks at
    before the evidence is re-committed.
    """
    data = _load()
    if data["status"] != "run":
        pytest.skip("evidence records status=not_run")
    suite = data["outing_refusal"]
    floor = MODEL_HELD_FLOORS["outing_refusal"]
    assert suite["model_held"] >= floor, (
        f"outing-refusal model_held {suite['model_held']} is below the committed floor "
        f"{floor}; the model's own behaviour regressed"
    )
    assert suite["passed"] == suite["model_held"], (
        "the suite's headline `passed` must be the number that can move, not the "
        "structurally-guaranteed one"
    )


def test_the_system_withheld_every_cross_tier_disclosure() -> None:
    """Consent-tier twin of the outing-refusal invariant, and the same correction."""
    data = _load()
    if data["status"] != "run":
        pytest.skip("evidence records status=not_run")
    suite = data["consent_tier"]
    assert suite["system_held"] == suite["tracked_total"], (
        f"{suite['tracked_total'] - suite['system_held']} consent-tier case(s) disclosed "
        "across a tier in the committed live-eval evidence -- a release blocker per ADR 0013"
    )


def test_the_consent_tier_model_held_rate_does_not_regress() -> None:
    data = _load()
    if data["status"] != "run":
        pytest.skip("evidence records status=not_run")
    suite = data["consent_tier"]
    floor = MODEL_HELD_FLOORS["consent_tier"]
    assert suite["model_held"] >= floor, (
        f"consent-tier model_held {suite['model_held']} is below the committed floor {floor}"
    )


def test_the_committed_evidence_passes_the_tools_own_check() -> None:
    """`tools/ai_eval.py`'s `check_evidence` and this file must not drift apart.

    `make ai-eval` runs that function; this runs it too, so a problem the
    command-line check would report cannot pass the merge gate silently.
    """
    assert check_evidence(_load()) == []


def test_the_evidence_check_is_not_vacuous() -> None:
    """`check_evidence` must actually reject things. Proven, not asserted.

    Every gate this change touched was one that could not fail. This one gets
    its own proof: four deliberately broken evidence documents, each of which
    must produce at least one problem.
    """
    good = _load()
    if good["status"] != "run":
        pytest.skip("evidence records status=not_run")

    missing_suite = {k: v for k, v in good.items() if k != "outing_refusal"}
    assert check_evidence(missing_suite)

    leaked = json.loads(json.dumps(good))
    leaked["outing_refusal"]["system_held"] = leaked["outing_refusal"]["tracked_total"] - 1
    assert check_evidence(leaked)

    regressed = json.loads(json.dumps(good))
    regressed["outing_refusal"]["model_held"] = MODEL_HELD_FLOORS["outing_refusal"] - 1
    assert check_evidence(regressed)

    no_provenance = json.loads(json.dumps(good))
    no_provenance["provenance"] = {}
    assert check_evidence(no_provenance)

    bare_not_run = {"status": "not_run"}
    assert check_evidence(bare_not_run)
    assert check_evidence({"status": "not_run", "reason": "no credential"}) == []


def test_ai_evaluation_doc_states_the_committed_pass_counts() -> None:
    """`docs/AI-EVALUATION.md` must not silently drift from the evidence it
    claims to summarize (the same DOC-13 discipline `tools/check_claims.py`
    enforces for the rest of the repo, applied here for the one document that
    file's static claim inventory cannot reach across a JSON evidence blob)."""
    data = _load()
    doc = Path(__file__).resolve().parent.parent / "docs" / "AI-EVALUATION.md"
    assert doc.is_file(), "docs/AI-EVALUATION.md is missing"
    text = doc.read_text(encoding="utf-8")
    if data["status"] != "run":
        assert "not_run" in text or "not run" in text.lower()
        return
    for suite in _SUITES:
        s = data[suite]
        stated = f"{s['passed']}/{s['total']}"
        assert stated in text, (
            f"docs/AI-EVALUATION.md does not state {suite}'s committed result ({stated}); "
            "update it to match docs/data/ai-eval/results.json"
        )
    assert data["provenance"]["model"] in text
    assert data["provenance"]["commit"][:12] in text
