"""The ADR set's own rules, enforced (MP-07).

`docs/MULTIYEAR-PLAN.md` MP-07. ADR 0000 makes the ADR set load-bearing -- "any
change to a safety guardrail ... must link an ADR in its pull request" -- and ADR
0001 makes an accepted ADR immutable except for its status. Two invariants follow,
and neither was checked by anything.

ADR 0009 has said `Supersedes: 0006` since the day it was accepted. ADR 0006's status
stayed a bare `Accepted` for six weeks. The pointer was one-way, so a reader arriving
at 0006 -- the more likely direction, since that is the number older documents cite --
was told nothing, and would have taken a superseded decision as current. That is the
failure this file makes impossible to reintroduce.

These are cheap, total checks over committed files: they read every ADR, so a new one
is covered the moment it is added rather than when someone remembers to extend a list.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ADR_DIR = Path(__file__).resolve().parent.parent / "docs" / "adr"

#: `0009-expand-standards-applicability.md` -> number 9.
_FILENAME = re.compile(r"^(\d{4})-[a-z0-9-]+\.md$")

#: `Supersedes: 0006` / `Supersedes 0006 and 0007`, in a heading or a status line.
_SUPERSEDES = re.compile(r"Supersedes:?\s+((?:\d{4}[,\s and]*)+)", re.IGNORECASE)


def _adrs() -> dict[int, Path]:
    """Every committed ADR, keyed by its number."""
    found: dict[int, Path] = {}
    for path in sorted(_ADR_DIR.glob("*.md")):
        match = _FILENAME.match(path.name)
        assert match, f"{path.name} does not follow the NNNN-kebab-title.md convention"
        found[int(match.group(1))] = path
    return found


def test_the_adr_directory_is_not_empty() -> None:
    """A vacuous pass here would make every check below meaningless."""
    assert len(_adrs()) >= 13


def test_every_adr_declares_a_status() -> None:
    """An ADR with no status is a decision of unknown standing."""
    missing = [
        path.name
        for path in _adrs().values()
        if not re.search(r"^\s*(Status:|## Status)", path.read_text(encoding="utf-8"), re.M)
    ]
    assert missing == [], f"ADRs with no Status: {missing}"


def test_a_superseded_adr_points_back_at_what_superseded_it() -> None:
    """`Supersedes: N` in one ADR requires N's own status to say so.

    ADR 0001 permits exactly this edit to an accepted ADR and no other, so there is
    no reason for the pointer to stay one-way. A reader who arrives at the older
    number -- the one older documents cite -- must be told it is not current.
    """
    adrs = _adrs()
    violations: list[str] = []
    for number, path in sorted(adrs.items()):
        text = path.read_text(encoding="utf-8")
        for match in _SUPERSEDES.finditer(text):
            for superseded in re.findall(r"\d{4}", match.group(1)):
                target = int(superseded)
                if target not in adrs:
                    violations.append(f"{path.name} supersedes {superseded}, which does not exist")
                    continue
                target_text = adrs[target].read_text(encoding="utf-8")
                if not re.search(
                    rf"[Ss]uperseded by\D*{target:04d}|[Ss]uperseded by\D*{number:04d}", target_text
                ):
                    violations.append(
                        f"{adrs[target].name} is superseded by {number:04d} but its status "
                        f"does not say so (ADR 0001 permits exactly this edit)"
                    )
    assert violations == [], "one-way supersession pointers:\n  " + "\n  ".join(violations)


def test_no_two_adrs_share_a_number() -> None:
    """Two decisions under one number is a citation that resolves to either."""
    numbers = [int(m.group(1)) for p in _ADR_DIR.glob("*.md") if (m := _FILENAME.match(p.name))]
    duplicates = {n for n in numbers if numbers.count(n) > 1}
    assert duplicates == set(), f"duplicate ADR numbers: {sorted(duplicates)}"


@pytest.mark.parametrize("number", [6])
def test_the_known_superseded_adr_is_marked(number: int) -> None:
    """0006 specifically: the instance that motivated this file.

    Kept as its own named case so a regression reads as "0006 lost its marker"
    rather than as a generic invariant failure.
    """
    text = _adrs()[number].read_text(encoding="utf-8")
    assert "Superseded by" in text
    assert "0009" in text
