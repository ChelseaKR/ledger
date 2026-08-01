#!/usr/bin/env python3
"""Suppression and marker hygiene gate (CODE-QUALITY-STANDARD CQ-34/CQ-35).

A suppression is a promise that a human looked at a warning and decided it was wrong
*here*. Three things make that promise checkable, and this gate enforces all three:

1. **A code.** A bare ``# noqa`` or ``# type: ignore`` silences every current and every
   future diagnostic on that line. The next real bug on the line arrives silent.
2. **A reason, in prose.** ``# noqa: S310`` alone tells a reader the rule number and
   nothing about why the author was right. Six months later nobody can tell a considered
   exception from a shrug, so nobody dares remove it, and the suppression becomes
   permanent by default. The reason may sit after the code on the same line, or in a
   comment on the line immediately above — long explanations read better above.
3. **An issue link, for the suppressions that are meant to be temporary.** Complexity
   waivers (``C901``) are not exceptions — they are debt. Each one says "this function
   is too complex and we are shipping it anyway", so each one must point at the issue
   that will retire it. Rule-level exceptions like "this URL is loopback, S310 does not
   apply" are permanent and correct; demanding an issue link for those would just
   manufacture issues nobody intends to close.

Bare ``TODO``/``FIXME``/``HACK`` markers fail for the same reason as (2): a marker with
no ticket is a note to a person who has already forgotten.

Run via ``make hygiene``; part of ``make verify``.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Scanned trees. Excludes generated catalogs, vendored web assets, and docs prose.
SCAN_DIRS = ("src", "tests", "tools")

#: A marker with no ticket in parentheses right after it. Does not fire on "TODOs" in
#: running prose because the word boundary requires the bare token.
_BARE_MARKER = re.compile(r"\b(TODO|FIXME|HACK)\b(?!\()")

#: A noqa comment not followed by `:` — silences everything, now and later.
_UNCODED_NOQA = re.compile(r"#\s*noqa\b(?!\s*:)")

#: A type-ignore comment with no `[code]`.
_UNCODED_TYPE_IGNORE = re.compile(r"#\s*type:\s*ignore\s*(?!\[)")

#: A coded suppression, capturing everything after the code so we can require prose.
_CODED_NOQA = re.compile(r"#\s*noqa:\s*([A-Z]+[0-9]+(?:\s*,\s*[A-Z]+[0-9]+)*)(?P<rest>.*)$")
_CODED_TYPE_IGNORE = re.compile(r"#\s*type:\s*ignore\[[a-z0-9_, -]+\](?P<rest>.*)$")

#: Prose counts only if it is more than punctuation: a trailing dash is not a reason.
_HAS_PROSE = re.compile(r"[A-Za-z]{3}")

#: `(#123)` or a full GitHub issue URL.
_ISSUE_REFERENCE = re.compile(
    r"\(#\d+\)|https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/issues/\d+"
)

#: Suppressions that are debt rather than a considered rule-level exception, and so must
#: name the issue that retires them. C901 is "this function is too complex"; there is no
#: reading of that which is permanently fine.
_DEBT_CODES = frozenset({"C901"})


def _tracked_files(*dirs: str) -> list[Path]:
    """Every git-tracked file under *dirs*. Tracked-only keeps stray local scratch files
    and virtualenvs out of a gate that would otherwise fail for reasons no reviewer can see."""
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("hygiene gate requires git on PATH")
    # Fixed argv, no shell, no user input — a dev-time/CI gate script.
    out = subprocess.run(  # noqa: S603 - resolved executable, fixed argv, no shell
        [git, "-C", str(ROOT), "ls-files", "-z", *dirs],
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    return [ROOT / p for p in out.split("\0") if p]


def scan_line(rel: str, lineno: int, line: str, previous: str = "") -> list[str]:
    """Every hygiene problem on one source line. Pure; the tests drive this directly.

    *previous* is the line above, because a suppression whose explanation is a comment
    directly above it is explained — insisting the prose share the line would just push
    authors toward terse, useless reasons that fit in the remaining columns.
    """
    problems: list[str] = []
    shown = line.strip()
    stripped_previous = previous.strip()
    explained_above = stripped_previous.startswith("#") and bool(
        _HAS_PROSE.search(stripped_previous)
    )

    if _BARE_MARKER.search(line):
        problems.append(f"{rel}:{lineno}: bare TODO/FIXME/HACK with no issue — {shown!r}")

    if _UNCODED_NOQA.search(line):
        problems.append(
            f"{rel}:{lineno}: '# noqa' with no rule code silences every future "
            f"diagnostic on this line — {shown!r}"
        )
    if _UNCODED_TYPE_IGNORE.search(line):
        problems.append(
            f"{rel}:{lineno}: '# type: ignore' with no bracketed error code — {shown!r}"
        )

    noqa = _CODED_NOQA.search(line)
    if noqa is not None:
        codes = [code.strip() for code in noqa.group(1).split(",")]
        if not _HAS_PROSE.search(noqa.group("rest")) and not explained_above:
            problems.append(
                f"{rel}:{lineno}: suppression of {'/'.join(codes)} has no written reason "
                f"— {shown!r}"
            )
        debt = sorted(_DEBT_CODES.intersection(codes))
        if debt and not _ISSUE_REFERENCE.search(line):
            problems.append(
                f"{rel}:{lineno}: {'/'.join(debt)} waiver is tracked debt and must link the "
                f"issue that retires it, e.g. '(#83)' — {shown!r}"
            )

    ignore = _CODED_TYPE_IGNORE.search(line)
    if ignore is not None and not _HAS_PROSE.search(ignore.group("rest")) and not explained_above:
        problems.append(f"{rel}:{lineno}: 'type: ignore' has no written reason — {shown!r}")

    return problems


def scan(paths: list[Path]) -> list[str]:
    problems: list[str] = []
    for path in paths:
        if path.resolve() == Path(__file__).resolve():
            continue  # this file's own patterns and examples would flag itself
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary or unreadable; encoding is a separate gate's job
        rel = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
        lines = text.splitlines()
        for lineno, line in enumerate(lines, start=1):
            problems.extend(scan_line(rel, lineno, line, lines[lineno - 2] if lineno > 1 else ""))
    return problems


def main() -> int:
    paths = _tracked_files(*SCAN_DIRS)
    problems = scan(paths)

    if problems:
        print(f"hygiene: {len(problems)} problem(s)")
        for problem in problems:
            print(f"  - {problem}")
        print(
            "hygiene FAILED: a suppression must name its rule, say why, and — for "
            "complexity debt — link the issue that retires it",
            file=sys.stderr,
        )
        return 1

    print(f"hygiene OK: {len(paths)} tracked files; every suppression is coded and explained.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
