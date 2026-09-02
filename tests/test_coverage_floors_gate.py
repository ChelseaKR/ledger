"""The per-module coverage gate can actually fail (MP-04).

`docs/MULTIYEAR-PLAN.md` MP-04. A coverage gate that is green and structurally
incapable of reporting what it appears to report is worse than no gate: it spends a
reviewer's trust without earning it. That is precisely what the pooled
`coverage report --include=<four modules> --fail-under=95` was. `--fail-under` gates
the report's TOTAL row, so the line passed at 95% while `grants.py` sat at 92% and
`consent.py` at 91%, carried by three neighbours at 100%.

`tools/check_coverage_floors.py` replaces it. This file holds that replacement to the
standard the old line failed: every rule it claims to enforce is shown failing here on
input that violates it, and passing on input that does not. `check` is pure -- it
takes the declared floors, the security-core set, the measurements and an existence
predicate -- so each rule is exercised directly rather than by staging a fake
repository and hoping the right branch runs.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_TOOL = _ROOT / "tools" / "check_coverage_floors.py"


def _load_tool() -> ModuleType:
    """Import the gate by path; `tools/` is a script directory, not a package."""
    spec = importlib.util.spec_from_file_location("check_coverage_floors", _TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate = _load_tool()


def _all_exist(_module: str) -> bool:
    return True


# --- the control: a clean configuration passes -------------------------------


def test_a_configuration_that_meets_every_floor_passes() -> None:
    """No violations when every module clears its own floor and nothing is missing."""
    assert (
        gate.check(
            {"a.py": 90, "b.py": 100},
            {"a.py", "b.py"},
            {"a.py": 90.0, "b.py": 100.0},
            exists=_all_exist,
        )
        == []
    )


# --- rule 1: a module below its own floor fails, with no averaging -----------


def test_a_module_below_its_floor_fails() -> None:
    """The basic claim: the gate says no when a module is under."""
    failures = gate.check({"a.py": 95}, {"a.py"}, {"a.py": 91.0}, exists=_all_exist)
    assert len(failures) == 1
    assert "a.py" in failures[0]
    assert "91.00% is below its floor of 95%" in failures[0]


def test_a_high_neighbour_cannot_lift_a_low_module() -> None:
    """The defect this gate exists to remove, stated as a test.

    Under the pooled report these two averaged to 96% and the scope passed at 95%.
    Measured separately, the module at 91% fails and the one at 100% does not.
    """
    failures = gate.check(
        {"low.py": 95, "high.py": 95},
        {"low.py", "high.py"},
        {"low.py": 91.0, "high.py": 100.0},
        exists=_all_exist,
    )
    assert len(failures) == 1
    assert "low.py" in failures[0]
    assert "high.py" not in failures[0]


def test_every_violation_is_reported_not_only_the_first() -> None:
    """A chain of `--fail-under` lines stops at the first; this does not.

    A reviewer should see the whole picture in one run rather than discovering the
    next shortfall each time they fix one.
    """
    failures = gate.check(
        {"a.py": 95, "b.py": 95, "c.py": 95},
        {"a.py", "b.py", "c.py"},
        {"a.py": 10.0, "b.py": 20.0, "c.py": 30.0},
        exists=_all_exist,
    )
    assert len(failures) == 3
    assert {"a.py", "b.py", "c.py"} == {f.strip().split(":")[0] for f in failures}


# --- rule 2: the comparison agrees with `coverage report --fail-under` -------


@pytest.mark.parametrize(
    ("percent", "floor", "should_fail"),
    [
        (89.90, 90, False),  # rounds to 90; `coverage report --fail-under=90` passes it
        (89.49, 90, True),  # rounds to 89
        (99.99, 100, True),  # coverage's special case: 100 must really be 100
        (100.0, 100, False),
    ],
)
def test_the_comparison_matches_coverage_at_the_rounding_boundary(
    percent: float, floor: int, should_fail: bool
) -> None:
    """The gate must not disagree with `--fail-under` on the same tree.

    A bespoke comparison would make the same repository green under one gate and red
    under another, which is how a floor stops meaning anything.
    """
    failures = gate.check({"a.py": floor}, set(), {"a.py": percent}, exists=_all_exist)
    assert bool(failures) is should_fail


# --- rule 3: the blind spot the pooled report never had ----------------------


def test_a_security_core_module_with_no_floor_fails() -> None:
    """Adding a safety-critical module and forgetting to floor it is now a failure.

    Under the pooled `--include` this was invisible; worse, appending the module to
    that list would have bought it a passing grade from its neighbours.
    """
    failures = gate.check(
        {"floored.py": 90},
        {"floored.py", "forgotten.py"},
        {"floored.py": 95.0},
        exists=_all_exist,
    )
    assert len(failures) == 1
    assert "forgotten.py" in failures[0]
    assert "has no floor" in failures[0]


# --- rule 4: a dead config key -----------------------------------------------


def test_a_floor_for_a_module_that_does_not_exist_fails() -> None:
    """A floor nobody is meeting reads as a floor somebody is."""
    failures = gate.check(
        {"deleted.py": 90}, set(), {}, exists=lambda module: module != "deleted.py"
    )
    assert len(failures) == 1
    assert "deleted.py" in failures[0]
    assert "does not exist" in failures[0]


def test_a_missing_module_is_not_also_reported_as_below_its_floor() -> None:
    """One defect, one message: a deleted module is a dead key, not a shortfall."""
    failures = gate.check(
        {"deleted.py": 90}, set(), {}, exists=lambda module: module != "deleted.py"
    )
    assert not any("below its floor" in f for f in failures)


# --- the gate cannot pass vacuously ------------------------------------------


def test_an_empty_floor_table_fails() -> None:
    """Deleting the floors is how a gate quietly stops gating, so it is a failure."""
    failures = gate.check({}, set(), {}, exists=_all_exist)
    assert len(failures) == 1
    assert "empty gate" in failures[0]


# --- the committed configuration is real -------------------------------------


def test_the_committed_floors_name_modules_that_exist() -> None:
    """Every floor in `pyproject.toml` points at a file in this repository."""
    floors, _core = gate._config()
    assert floors, "the repository declares no coverage floors"
    missing = [module for module in floors if not (_ROOT / module).is_file()]
    assert missing == [], f"floors name modules that do not exist: {missing}"


def test_every_security_core_module_is_floored_in_the_committed_config() -> None:
    """The real configuration satisfies the rule the gate enforces on itself."""
    floors, core_globs = gate._config()
    unfloored = sorted(gate._core_modules(core_globs) - set(floors))
    assert unfloored == [], f"security-core modules with no floor: {unfloored}"
