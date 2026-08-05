"""The suppression-hygiene gate itself (CQ-34/CQ-35, #83).

A gate nobody tests is a gate that silently stops gating. These build their sample lines
by concatenation rather than writing the markers out, because this file is inside the tree
the gate scans: a literal suppression marker here would make the repository fail its own
check over a string in a test fixture.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tools.check_hygiene import scan, scan_line

# Assembled at runtime so the gate does not see them as real suppressions in this file.
NOQA = "# " + "noqa"
TYPE_IGNORE = "# " + "type: ignore"
MARKER = "TO" + "DO"


def problems(line: str, previous: str = "") -> list[str]:
    return scan_line("sample.py", 1, line, previous)


def test_uncoded_noqa_is_rejected() -> None:
    """A bare suppression silences the NEXT diagnostic on the line too, forever."""
    found = problems(f"x = eval(src)  {NOQA}")
    assert any("no rule code" in p for p in found)


def test_uncoded_type_ignore_is_rejected() -> None:
    found = problems(f"x = untyped()  {TYPE_IGNORE}")
    assert any("no bracketed error code" in p for p in found)


def test_coded_suppression_without_a_reason_is_rejected() -> None:
    """A rule number is not an explanation. Six months on, nobody can tell a considered
    exception from a shrug — so nobody dares delete it."""
    found = problems(f"urlopen(u)  {NOQA}: S310")
    assert any("no written reason" in p for p in found)


def test_reason_on_the_same_line_satisfies_the_gate() -> None:
    assert problems(f"urlopen(u)  {NOQA}: S310 - loopback URL we constructed") == []


def test_reason_in_the_comment_above_satisfies_the_gate() -> None:
    """Long reasons belong above the line; forcing them into the remaining columns
    would just produce shorter, less useful reasons."""
    above = "    # The URL is a loopback address this test server just bound."
    assert problems(f"    urlopen(u)  {NOQA}: S310", previous=above) == []


def test_bare_punctuation_is_not_a_reason() -> None:
    found = problems(f"urlopen(u)  {NOQA}: S310 -")
    assert any("no written reason" in p for p in found)


def test_complexity_waiver_must_link_its_retirement_issue() -> None:
    """C901 is not a rule-level exception, it is debt: the function IS too complex and
    is shipping anyway. Debt with no ticket is debt nobody pays."""
    found = problems(f"def f() -> None:  {NOQA}: C901 - a long dispatch table")
    assert any("must link the issue" in p for p in found)


def test_complexity_waiver_with_an_issue_link_passes() -> None:
    assert problems(f"def f() -> None:  {NOQA}: C901 - a long dispatch table (#83)") == []


def test_issue_url_is_accepted_as_well_as_the_short_form() -> None:
    url = "https://github.com/ChelseaKR/ledger/issues/83"
    assert problems(f"def f() -> None:  {NOQA}: C901 - dispatch table, {url}") == []


def test_rule_level_exceptions_do_not_need_an_issue_link() -> None:
    """S310-on-a-loopback-URL is permanently correct. Demanding a ticket for it would
    manufacture issues nobody intends to close, which trains people to ignore the gate."""
    assert problems(f"urlopen(u)  {NOQA}: S310 - loopback URL we constructed") == []


def test_bare_marker_is_rejected_but_a_ticketed_one_is_not() -> None:
    assert any("with no issue" in p for p in problems(f"x = 1  # {MARKER}: fix later"))
    assert problems(f"x = 1  # {MARKER}(#83): fix later") == []


@pytest.mark.parametrize("word", ["TODOS", "shackle", "FIXMEs"])
def test_marker_regex_does_not_fire_on_ordinary_words(word: str) -> None:
    assert problems(f'x = "{word}"') == []


def test_the_repository_itself_is_clean() -> None:
    """The gate is only worth having if it is green on `main`; this is the regression."""
    root = Path(__file__).resolve().parent.parent
    tracked = [
        path
        for directory in ("src", "tests", "tools")
        for path in (root / directory).rglob("*.py")
        if "__pycache__" not in path.parts
    ]
    assert scan(tracked) == []
