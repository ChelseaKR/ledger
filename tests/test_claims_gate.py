"""The truthfulness gate itself (`tools/check_claims.py`, #124).

A gate nobody tests is a gate that silently stops gating — and this one had a subtler
failure than that: it was green while five load-bearing README/architecture statements
were false, because its inventory could only see paths and banned phrases. Green meant
"the eight things I look at are fine", never "the documentation is true".

So these tests assert two different things, and the second is the one that matters:

* the five corrected statements are **absent** from the docs today (asserting the
  absence of the unearned claim, not merely the presence of a fix); and
* the gate **detects** each of them when it is reintroduced into a scratch tree. A
  check that passes on a repaired repo proves nothing about the check; only a check
  that fails on a broken one does.

The stale phrases are held here independently of `CLAIMS`, so deleting an inventory
entry cannot quietly delete its regression test with it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from tools import check_claims
from tools.check_claims import (
    CLAIMS,
    OWNER_BYPASS,
    UNCOVERED,
    ConfigNumber,
    ForbiddenString,
    PathExists,
    ReferenceExists,
    RequiredString,
    RulesetBypass,
    RulesetContexts,
    StatedCount,
    bypass_findings,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# The five statements #124 measured as false at 9a2c395, in the words they used.
# (file, distinguishing phrase, the inventory entry that must catch it)
STALE_CLAIMS: tuple[tuple[str, str, str], ...] = (
    ("README.md", "dependency pinning is a range today", "lockfile-is-committed"),
    (
        "README.md",
        "human review and the residual-risk register",
        "residual-risk-register-is-committed",
    ),
    ("README.md", "a fuller `docs/audits/` set is tracked", "audits-set-is-committed"),
    ("docs/ARCHITECTURE.md", "`/healthz` reports counts only", "healthz-counts-are-gated"),
    # The fifth is shaped differently: a pointer at a roadmap item that does not exist.
    ("README.md", "`docs/ROADMAP.md`, P3-6", "roadmap-item-pointers"),
)


def claim(name: str) -> object:
    """The inventory entry called ``name`` (fails loudly if it was removed)."""
    for entry in CLAIMS:
        if entry.name == name:
            return entry
    raise AssertionError(f"no claim named {name!r} in the inventory — was it deleted?")


def fake_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, files: dict[str, str]) -> Path:
    """A scratch tree the gate is pointed at, so a test can make the repo lie."""
    for rel, text in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    monkeypatch.setattr(check_claims, "ROOT", tmp_path)
    return tmp_path


# --- the repository, as it stands --------------------------------------------


def test_the_repository_passes_its_own_truthfulness_gate() -> None:
    assert check_claims.main() == 0


@pytest.mark.parametrize(("path", "phrase", "_name"), STALE_CLAIMS)
def test_the_stale_claim_is_gone_from_the_docs(path: str, phrase: str, _name: str) -> None:
    """#124's five statements must not be in the tree — the fix is their absence."""
    text = (REPO_ROOT / path).read_text(encoding="utf-8")
    assert phrase not in text, f"{path} still makes the corrected claim {phrase!r}"


@pytest.mark.parametrize(
    ("path", "phrase"),
    [
        # Each correction states something checkable rather than deleting the sentence:
        # a claim that vanishes is not a claim that was fixed.
        ("README.md", "hash-pinned `uv.lock`"),
        ("README.md", "residual-risk\nregister](docs/audits/residual-risk-register.md)"),
        ("README.md", "review documents under"),
        ("docs/ARCHITECTURE.md", "gated to a steward grant"),
        ("infra/README.md", "gated to a steward grant"),
    ],
)
def test_the_corrected_claim_is_actually_stated(path: str, phrase: str) -> None:
    text = (REPO_ROOT / path).read_text(encoding="utf-8")
    assert phrase in text, f"{path} no longer states the corrected claim {phrase!r}"


def test_the_healthz_claim_is_gone_from_the_self_host_runbook_too() -> None:
    """The fifth claim had a second home nobody listed: `infra/README.md`.

    It is the operator-facing copy, and the worse of the two — it tells someone
    standing up a server to point an uptime monitor at `/healthz` and read counts an
    anonymous request has not returned since P2-2 gated them.
    """
    text = (REPO_ROOT / "infra/README.md").read_text(encoding="utf-8")
    assert "fixity counts only" not in text


def test_every_stale_claim_has_an_inventory_entry_that_names_it() -> None:
    for _path, _phrase, name in STALE_CLAIMS:
        assert claim(name) is not None


# --- the gate detects each one when it comes back ----------------------------


@pytest.mark.parametrize(("path", "phrase", "name"), STALE_CLAIMS[:4])
def test_reintroducing_a_stale_claim_fails_the_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str, phrase: str, name: str
) -> None:
    """The check that matters: the gate goes red on a repo that makes the claim again."""
    entry = claim(name)
    assert isinstance(entry, ForbiddenString)
    fake_repo(tmp_path, monkeypatch, {path: f"prose before. {phrase} and prose after.\n"})
    problem = entry.check()
    assert problem is not None
    assert phrase in problem


def test_a_pointer_at_a_missing_roadmap_item_fails_the_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#124's second claim: `docs/ROADMAP.md`, P3-6 pointed at nothing at all."""
    entry = claim("roadmap-item-pointers")
    assert isinstance(entry, ReferenceExists)
    fake_repo(
        tmp_path,
        monkeypatch,
        {
            "docs/ROADMAP.md": "# Roadmap\n\nNothing here mentions that item.\n",
            "README.md": "the flag is not shipped (tracked in `docs/ROADMAP.md`, P3-6).\n",
        },
    )
    problem = entry.check()
    assert problem is not None
    assert "P3-6" in problem
    assert "README.md" in problem


def test_a_pointer_at_a_live_roadmap_item_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = claim("roadmap-item-pointers")
    assert isinstance(entry, ReferenceExists)
    fake_repo(
        tmp_path,
        monkeypatch,
        {
            "docs/ROADMAP.md": "| Release | REL-08, P1-6 | open |\n",
            "README.md": "no release yet (tracked in `docs/ROADMAP.md`, P1-6).\n",
        },
    )
    assert entry.check() is None


def test_the_pointer_sweep_reads_every_markdown_file_not_just_the_readme(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A README-only sweep is the blind spot that let DEFINITION_OF_DONE.md drift too."""
    entry = claim("roadmap-item-pointers")
    assert isinstance(entry, ReferenceExists)
    fake_repo(
        tmp_path,
        monkeypatch,
        {
            "docs/ROADMAP.md": "# Roadmap\n",
            "README.md": "clean prose with no pointer.\n",
            "DEFINITION_OF_DONE.md": "not gated (tracked in `docs/ROADMAP.md`, P2-3).\n",
        },
    )
    problem = entry.check()
    assert problem is not None
    assert "DEFINITION_OF_DONE.md" in problem


def test_an_item_id_in_a_later_paragraph_is_not_read_as_a_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pointer is the id in the same breath as the path; a noisy gate gets ignored."""
    entry = claim("roadmap-item-pointers")
    assert isinstance(entry, ReferenceExists)
    fake_repo(
        tmp_path,
        monkeypatch,
        {
            "docs/ROADMAP.md": "# Roadmap\n",
            "README.md": "see [`docs/ROADMAP.md`](docs/ROADMAP.md).\n\nSeparately, P4-9 is a "
            "phase id in a different paragraph.\n",
        },
    )
    assert entry.check() is None


def test_standards_control_ids_are_out_of_scope_and_stay_that_way(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A documented limit, pinned so it is a decision rather than an accident.

    SEC/A11Y/CQ ids are defined in the portfolio's `STANDARDS/`, not here, so this repo
    has nothing to resolve them against. The limit is published in `UNCOVERED`; this
    test makes sure it is what actually happens.
    """
    entry = claim("roadmap-item-pointers")
    assert isinstance(entry, ReferenceExists)
    fake_repo(
        tmp_path,
        monkeypatch,
        {
            "docs/ROADMAP.md": "# Roadmap\n",
            "README.md": "tracked in `docs/ROADMAP.md`, SEC-99 and A11Y-77.\n",
        },
    )
    assert entry.check() is None
    assert any("STANDARDS/" in item.why for item in UNCOVERED)


# --- the meta-bug: a check that cannot fail ----------------------------------


def test_a_stated_count_that_stopped_being_stated_fails_loudly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure mode this whole file guards: a regex that matches nothing, passes.

    If the sentence naming the number is reworded away, the honest outcome is a red
    build telling a maintainer the claim is no longer verified — not a silent green.
    """
    entry = claim("audits-count")
    assert isinstance(entry, StatedCount)
    fake_repo(
        tmp_path,
        monkeypatch,
        {"README.md": "audit artifacts live under docs/audits/.\n", "docs/audits/dpia.md": "x"},
    )
    problem = entry.check()
    assert problem is not None
    assert "no longer states the count" in problem


def test_a_stated_count_that_drifted_from_the_tree_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = claim("audits-count")
    assert isinstance(entry, StatedCount)
    files = {"README.md": "eight review documents under `docs/audits/`.\n"}
    files.update({f"docs/audits/{n}.md": "x" for n in range(9)})
    fake_repo(tmp_path, monkeypatch, files)
    problem = entry.check()
    assert problem is not None
    assert "states eight but the repo has 9" in problem


def test_a_stated_count_that_matches_the_tree_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = claim("audits-count")
    assert isinstance(entry, StatedCount)
    files = {"README.md": "eight review documents under `docs/audits/`.\n"}
    files.update({f"docs/audits/{n}.md": "x" for n in range(8)})
    files["docs/audits/README.md"] = "index, not a review"
    fake_repo(tmp_path, monkeypatch, files)
    assert entry.check() is None


def test_deleting_the_evidence_fails_even_though_the_dead_phrase_is_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`forbidden_string` alone is satisfied by an empty file — that is the trap.

    "The old overclaim is absent" is trivially true of a deleted paragraph and of a
    lockfile with no hashes in it. The paired `required_string` is what makes the
    corrected claim earned rather than merely unstated.
    """
    forbidden = claim("lockfile-is-committed")
    required = claim("lockfile-is-hash-pinned")
    assert isinstance(forbidden, ForbiddenString)
    assert isinstance(required, RequiredString)
    fake_repo(tmp_path, monkeypatch, {"README.md": "", "uv.lock": "version = 1\n"})
    assert forbidden.check() is None  # the dead phrase is absent...
    problem = required.check()  # ...but nothing supports the replacement
    assert problem is not None
    assert "uv.lock" in problem


def test_a_missing_file_fails_rather_than_vacuously_passing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A claim about a file that is not there is unverified, which is not the same as true."""
    fake_repo(tmp_path, monkeypatch, {})
    for entry in CLAIMS:
        if isinstance(
            entry,
            ForbiddenString
            | RequiredString
            | StatedCount
            | PathExists
            | ConfigNumber
            | RulesetContexts,
        ):
            assert entry.check() is not None, f"{entry.name} passed against an empty tree"


# --- config_number: the documented threshold is tied to the enforcing config -----

_PYPROJECT = "[tool.coverage.report]\nfail_under = 88\n"


def config_number_claim() -> ConfigNumber:
    entry = claim("coverage-floor-in-definition-of-done")
    assert isinstance(entry, ConfigNumber)
    return entry


def test_a_documented_floor_that_drifted_below_the_enforced_one_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real drift: three files said 85 for two weeks while pyproject enforced 88.

    Documentation that understates a gate is the same defect as documentation that
    overstates one — a reader plans against a number the build does not use.
    """
    entry = config_number_claim()
    fake_repo(
        tmp_path,
        monkeypatch,
        {
            "pyproject.toml": _PYPROJECT,
            "DEFINITION_OF_DONE.md": "the 85% branch-coverage floor applies.\n",
        },
    )
    problem = entry.check()
    assert problem is not None
    assert "states 85" in problem and "88" in problem


def test_a_documented_floor_that_matches_the_enforced_one_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = config_number_claim()
    fake_repo(
        tmp_path,
        monkeypatch,
        {
            "pyproject.toml": _PYPROJECT,
            "DEFINITION_OF_DONE.md": "the 88% branch-coverage floor applies.\n",
        },
    )
    assert entry.check() is None


def test_a_floor_that_stopped_being_stated_fails_rather_than_passing_silently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = config_number_claim()
    fake_repo(
        tmp_path,
        monkeypatch,
        {"pyproject.toml": _PYPROJECT, "DEFINITION_OF_DONE.md": "coverage is enforced.\n"},
    )
    problem = entry.check()
    assert problem is not None
    assert "no longer states the threshold" in problem


def test_an_unreadable_config_fails_rather_than_being_read_as_agreement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No source to check against is "unverified", which is not "true"."""
    entry = config_number_claim()
    fake_repo(
        tmp_path,
        monkeypatch,
        {
            "pyproject.toml": "[tool.coverage]\n",
            "DEFINITION_OF_DONE.md": "the 88% branch-coverage floor applies.\n",
        },
    )
    problem = entry.check()
    assert problem is not None
    assert "cannot read" in problem


def test_the_three_documents_state_the_floor_pyproject_enforces() -> None:
    """All three live copies of the number, checked against the tree as it stands."""
    for name in (
        "coverage-floor-in-definition-of-done",
        "coverage-floor-in-contributing",
        "coverage-floor-in-dora-review",
    ):
        entry = claim(name)
        assert isinstance(entry, ConfigNumber)
        assert entry.check() is None, entry.check()


def test_the_stale_coverage_flag_is_gone_from_every_document() -> None:
    """`--cov-fail-under=85` named a flag this repo never passed and a floor it no
    longer enforces; it survived in three files because nothing tied it to a source."""
    for rel in ("DEFINITION_OF_DONE.md", "CONTRIBUTING.md", "docs/DORA-DELIVERY-HEALTH-REVIEW.md"):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "--cov-fail-under" not in text, f"{rel} still cites a flag the repo does not pass"


# --- ruleset_contexts: a required check that no job can ever report --------------


_RULESET = """{
  "name": "protect-main",
  "rules": [
    {"type": "deletion"},
    {"type": "required_status_checks", "parameters": {"required_status_checks": [
      {"context": "lint \\u00b7 type \\u00b7 test (py3.12)"},
      {"context": "CodeQL analyze (python)"}
    ]}}
  ]
}
"""

_CI = """name: ci
jobs:
  gate:
    name: lint · type · test (py${{ matrix.python }})
    runs-on: ubuntu-latest
"""

_CODEQL = """name: codeql
jobs:
  analyze:
    name: CodeQL analyze (${{ matrix.language }})
    runs-on: ubuntu-latest
"""


def ruleset_claim() -> RulesetContexts:
    entry = claim("ruleset-contexts-name-real-jobs")
    assert isinstance(entry, RulesetContexts)
    return entry


def test_a_required_context_that_matches_a_matrix_job_name_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Job names interpolate matrix values, so the resolution is a pattern match."""
    entry = ruleset_claim()
    fake_repo(
        tmp_path,
        monkeypatch,
        {
            ".github/rulesets/main.json": _RULESET,
            ".github/workflows/ci.yml": _CI,
            ".github/workflows/codeql.yml": _CODEQL,
        },
    )
    assert entry.check() is None


def test_renaming_a_job_out_from_under_the_ruleset_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure this claim exists for.

    Rename the job and nothing goes red: the ruleset keeps requiring a context no
    workflow will ever report, the branch reads as protected, and the gate it named
    has quietly stopped being merge-blocking.
    """
    entry = ruleset_claim()
    fake_repo(
        tmp_path,
        monkeypatch,
        {
            ".github/rulesets/main.json": _RULESET,
            ".github/workflows/ci.yml": _CI.replace("lint · type · test", "checks"),
            ".github/workflows/codeql.yml": _CODEQL,
        },
    )
    problem = entry.check()
    assert problem is not None
    assert "lint" in problem


def test_a_ruleset_requiring_nothing_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty required-check list is the purest gate that cannot fail."""
    entry = ruleset_claim()
    fake_repo(
        tmp_path,
        monkeypatch,
        {
            ".github/rulesets/main.json": '{"rules": [{"type": "required_status_checks",'
            ' "parameters": {"required_status_checks": []}}]}',
            ".github/workflows/ci.yml": _CI,
        },
    )
    problem = entry.check()
    assert problem is not None
    assert "zero status checks" in problem


def test_a_ruleset_with_no_status_check_rule_at_all_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = ruleset_claim()
    fake_repo(
        tmp_path,
        monkeypatch,
        {
            ".github/rulesets/main.json": '{"rules": [{"type": "deletion"}]}',
            ".github/workflows/ci.yml": _CI,
        },
    )
    problem = entry.check()
    assert problem is not None
    assert "no `required_status_checks` rule" in problem


def test_the_committed_mirror_matches_the_required_contexts_the_workflows_declare() -> None:
    """The repository as it stands: all thirteen required contexts resolve to real jobs.

    Eleven through #148; #79 (2026-08-21) added `OSV lockfile scan (uv.lock)` and
    `Semgrep SAST (p/ci)` to the live ruleset's required-check set.
    """
    entry = ruleset_claim()
    contexts = entry.contexts()
    assert isinstance(contexts, list), contexts
    assert len(contexts) == 13
    assert entry.check() is None


# --- the boundary is published, not implied ----------------------------------


def _contributing_boundary_items() -> list[str]:
    """The bullets under CONTRIBUTING.md's "does not cover" heading."""
    text = (REPO_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    match = re.search(
        r"#### What the truthfulness gate does not cover\n(.*?)(?=\n#{1,4} )", text, re.DOTALL
    )
    assert match is not None, "CONTRIBUTING.md no longer publishes the gate's boundary"
    return [
        line[2:].split(" — ")[0].strip() for line in match.group(1).splitlines() if line[:2] == "- "
    ]


def test_the_gate_prints_what_it_cannot_see(capsys: pytest.CaptureFixture[str]) -> None:
    assert check_claims.main() == 0
    printed = capsys.readouterr().out
    assert "outside this gate" in printed
    for item in UNCOVERED:
        assert item.claim in printed


def test_the_published_boundary_matches_the_gate_exactly() -> None:
    """A boundary a reader can see, kept from drifting from the one the gate applies."""
    assert _contributing_boundary_items() == [item.claim for item in UNCOVERED]


def test_the_truthfulness_gate_actually_runs_on_a_pull_request() -> None:
    """It was documented as merge-blocking while no CI job ran it (#124 follow-on).

    `make verify` ran it locally and `release.yml` ran it at tag time, so the one place
    it never ran was the pull request it was supposed to block.
    """
    ci = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "tools/check_claims.py" in ci


# --- ruleset_bypass: an empty list is the lockout, not the tighter setting -------
#
# Live ruleset 18823575, read 2026-08-28 from
# `gh api repos/ChelseaKR/ledger/rulesets/18823575`: `bypass_actors` is exactly the
# owner's standing bypass and `current_user_can_bypass` is "always". Reproduced here
# rather than fetched, because the gate makes no network request. The mirror declared
# `[]` for a week and the README argued that was the tight posture; re-applying it
# would have locked the owner out of the repository, which has already happened once
# across eighteen of them.

_LIVE_RULESET: dict[str, object] = {
    "id": 18823575,
    "name": "protect-main",
    "target": "branch",
    "enforcement": "active",
    "bypass_actors": [dict(OWNER_BYPASS)],
    "current_user_can_bypass": "always",
}


def _committed_ruleset() -> dict[str, object]:
    """The mirror as it stands on disk, not a fixture of it."""
    import json

    return json.loads((REPO_ROOT / ".github/rulesets/main.json").read_text(encoding="utf-8"))


def bypass_claim() -> RulesetBypass:
    entry = claim("ruleset-records-the-owner-bypass")
    assert isinstance(entry, RulesetBypass)
    return entry


def test_the_committed_mirror_records_the_owner_bypass_and_nothing_else() -> None:
    """Not a fixture: the actual `.github/rulesets/main.json`."""
    assert _committed_ruleset()["bypass_actors"] == [OWNER_BYPASS]
    assert bypass_claim().check() is None


def test_the_real_live_configuration_reads_as_conformance() -> None:
    """The configuration the repository is actually in must pass.

    A check that fails forever against a correct repository is not a stricter check,
    it is a broken one — which is what asserting `bypass_actors == []` was.
    """
    assert bypass_findings(_LIVE_RULESET, _committed_ruleset()) == []


def test_a_second_bypass_actor_is_reported_on_either_side() -> None:
    """The threat actually worth guarding: a team, a GitHub App or a second
    repository role handed the ability to skip the merge gate."""
    committed = _committed_ruleset()
    for extra in (
        {"actor_id": 4242, "actor_type": "Team", "bypass_mode": "pull_request"},
        {"actor_id": 99, "actor_type": "Integration", "bypass_mode": "always"},
        {"actor_id": 2, "actor_type": "RepositoryRole", "bypass_mode": "always"},
    ):
        live = dict(_LIVE_RULESET, bypass_actors=[dict(OWNER_BYPASS), extra])
        found = bypass_findings(live, committed)
        assert len(found) == 1, found
        assert "unreviewed bypass actor" in found[0]

        planted = dict(committed, bypass_actors=[dict(OWNER_BYPASS), extra])
        found = bypass_findings(_LIVE_RULESET, planted)
        assert len(found) == 1, found
        assert "committed and not enforced" in found[0]


def test_the_owner_losing_their_live_bypass_is_reported() -> None:
    """The incident the rule exists for. An empty list coming back from the API is
    the owner locked out of their own repository."""
    found = bypass_findings(dict(_LIVE_RULESET, bypass_actors=[]), _committed_ruleset())
    assert len(found) == 1, found
    assert "the live protect-main ruleset" in found[0]
    assert "lockout" in found[0]


def test_both_sides_emptied_together_is_two_findings_not_zero() -> None:
    """The case a parity check would pass with a green tick on it.

    A tidy revert of the mirror on a day the owner had also been locked out makes the
    two sides agree, which is exactly why the owner's bypass is held against each side
    separately rather than compared between them.
    """
    live = dict(_LIVE_RULESET, bypass_actors=[])
    committed = dict(_committed_ruleset(), bypass_actors=[])
    found = bypass_findings(live, committed)
    assert len(found) == 2, found
    assert any("the live protect-main ruleset" in line for line in found), found
    assert any(".github/rulesets/main.json" in line for line in found), found


def test_the_gate_fails_when_the_mirror_goes_back_to_an_empty_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The offline half, proved against a broken tree rather than a repaired one."""
    entry = bypass_claim()
    fake_repo(tmp_path, monkeypatch, {".github/rulesets/main.json": '{"bypass_actors": []}'})
    problem = entry.check()
    assert problem is not None
    assert "lockout" in problem


def test_the_gate_fails_when_a_second_actor_is_planted_in_the_mirror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = bypass_claim()
    mirror = (
        '{"bypass_actors": ['
        '{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"},'
        '{"actor_id": 4242, "actor_type": "Team", "bypass_mode": "always"}]}'
    )
    fake_repo(tmp_path, monkeypatch, {".github/rulesets/main.json": mirror})
    problem = entry.check()
    assert problem is not None
    assert "4242" in problem


def test_the_gate_fails_when_the_mirror_has_no_bypass_field_at_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing key must not read as a vacuous pass."""
    entry = bypass_claim()
    fake_repo(tmp_path, monkeypatch, {".github/rulesets/main.json": '{"name": "protect-main"}'})
    assert entry.check() is not None
