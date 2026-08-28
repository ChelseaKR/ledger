#!/usr/bin/env python3
"""Per-module coverage floors (CODE-QUALITY-STANDARD CQ-08; MP-04) -- merge-blocking.

``coverage report --fail-under`` gates the **TOTAL row** of a report, not each module
in it. That is the whole defect this gate exists to remove. The ``Makefile`` scoped
one report over ``access/*`` + ``consent.py`` + ``dualcontrol.py`` and gated it at
95%, and the line passed -- while ``grants.py`` sat at 92% and ``consent.py`` at 91%,
carried by three neighbours at 100%. Two modules were below the floor their own gate
advertised, and the gate was structurally incapable of saying so.

A pooled figure fails in one direction and hides in three. This gate checks each of
them:

1. **Every floored module meets its floor**, measured on its own. No averaging.
2. **Every violation is reported**, not just the first. ``coverage report`` exits at
   the first failing scope, so a chain of ``--fail-under`` lines in a Makefile tells
   you about one module per run; fixing it reveals the next. A reviewer should see
   the whole picture once.
3. **Every security-core module has a floor.** ``[tool.ledger].security_core`` is a
   list of globs; a module matching one and missing from
   ``[tool.ledger.coverage_floors]`` fails the build. Adding a safety-critical module
   and forgetting to floor it was previously invisible -- worse than invisible, since
   appending it to the pooled ``--include`` would have bought it a passing grade from
   its neighbours.
4. **No floor names a module that does not exist.** A dead config key is a floor
   nobody is meeting, reading as a floor somebody is.

A floor is a ratchet, set where the suite ALREADY measures, per
``[tool.coverage.report]``'s own note: setting one above the measured number adds no
test, it just makes ``main`` red until someone lowers it, and a floor that gets
lowered is not a floor.

The comparison is :func:`coverage.results.should_fail_under`, coverage's own
function, at the same precision ``coverage report`` uses. Rolling a bespoke
comparison here would make this gate disagree with every ``--fail-under`` elsewhere in
the repo at the rounding boundary -- ``moderate.py`` measures 89.90% and
``coverage report --fail-under=90`` passes it, so this must too, or the same tree
would be green under one gate and red under the other. The precise figure is printed
beside the rounded one, so nothing is hidden by the rounding that decides it.

Reads the coverage data ``make cov`` has already written, so it adds no second test
run. Run via ``make cov``; not part of ``make verify``, which runs pytest without
coverage for speed.
"""

from __future__ import annotations

import io
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path

import coverage
from coverage.results import should_fail_under

ROOT = Path(__file__).resolve().parent.parent
_PYPROJECT = ROOT / "pyproject.toml"
_DATA_FILE = ROOT / ".coverage"

#: Digits after the decimal point the comparison considers. 0 is coverage's own
#: default and what ``coverage report`` displays, so this gate agrees with every
#: ``--fail-under`` in the repo rather than disagreeing at the rounding boundary.
_PRECISION = 0


def _config() -> tuple[dict[str, int], list[str]]:
    """The declared floors and the security-core globs, from ``pyproject.toml``."""
    with _PYPROJECT.open("rb") as handle:
        data = tomllib.load(handle)
    ledger = data.get("tool", {}).get("ledger", {})
    floors = {str(k): int(v) for k, v in ledger.get("coverage_floors", {}).items()}
    core = [str(g) for g in ledger.get("security_core", [])]
    return floors, core


def _measure(module: str) -> float:
    """Branch-coverage percentage for one module, from the existing coverage data."""
    cov = coverage.Coverage(data_file=str(_DATA_FILE))
    cov.load()
    sink = io.StringIO()  # the number is the return value; the table is not wanted here
    return float(cov.report(morfs=[str(ROOT / module)], file=sink, show_missing=False))


def _core_modules(globs: list[str]) -> set[str]:
    """Every file matching a security-core glob, repo-relative and sorted."""
    found: set[str] = set()
    for pattern in globs:
        for path in ROOT.glob(pattern):
            if path.name != "__init__.py":  # a re-export shim holds no logic to floor
                found.add(path.relative_to(ROOT).as_posix())
    return found


def check(
    floors: dict[str, int],
    core_modules: set[str],
    measured: dict[str, float],
    *,
    exists: Callable[[str], bool],
) -> list[str]:
    """Every violation of the four rules, as messages. Empty means the gate passes.

    Pure: the caller supplies the declared floors, the security-core module set, the
    measured percentages, and an existence predicate, so this can be exercised
    directly (``tests/test_coverage_floors_gate.py``) without a coverage run or a
    fabricated repository on disk. A gate whose logic can only be tested by running
    the whole suite is a gate nobody tests.
    """
    failures: list[str] = []

    if not floors:
        return ["  no floors are declared -- an empty gate is not a passing one"]

    # (4) A floor naming a module that does not exist is a dead config key.
    for module in sorted(floors):
        if not exists(module):
            failures.append(
                f"  {module}: floored at {floors[module]}% but the file does not exist "
                f"-- remove the dead floor or restore the module"
            )

    # (3) A security-core module with no floor of its own.
    for module in sorted(core_modules - set(floors)):
        failures.append(
            f"  {module}: matches [tool.ledger].security_core but has no floor "
            f"-- add one to [tool.ledger.coverage_floors] at its measured value"
        )

    # (1) and (2): every floored module against its own floor, every shortfall listed.
    for module in sorted(floors):
        if not exists(module):
            continue
        percent = measured[module]
        if should_fail_under(percent, floors[module], _PRECISION):
            failures.append(f"  {module}: {percent:.2f}% is below its floor of {floors[module]}%")
    return failures


def main() -> int:
    """Check every floor and every drift shape; report all failures at once."""
    floors, core_globs = _config()
    if not _DATA_FILE.exists():
        print(f"coverage floors: no coverage data at {_DATA_FILE} -- run `make cov` first")
        return 1

    def exists(module: str) -> bool:
        return (ROOT / module).is_file()

    measured = {m: _measure(m) for m in sorted(floors) if exists(m)}
    failures = check(floors, _core_modules(core_globs), measured, exists=exists)

    for module in sorted(measured):
        percent = measured[module]
        print(f"  {module:38s} {percent:6.2f}%  (reported {percent:.0f}%)  floor {floors[module]}%")

    if failures:
        print("\ncoverage floors FAILED:")
        for line in failures:
            print(line)
        print(
            "\nEach module is measured on its own: a floor is never met by a neighbour's\n"
            "score. Raise coverage, or lower a floor only with the reason recorded."
        )
        return 1

    print(
        f"coverage floors OK: {len(measured)} modules, each measured and gated on its own; "
        f"every security-core module has a floor; no dead floors."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
