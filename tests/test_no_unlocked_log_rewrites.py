"""Structural gate: no whole-document PREMIS log rewrite may run unserialized.

This exists because the *enumeration* was the gate, and an enumeration is only as
good as whoever last remembered to extend it.

``ledger._filelock`` states the hazard in this repository's strongest terms -- "a lost
withdrawal is the worst class of bug this project can have" -- and eleven modules took
the lesson. The ones that did not were not argued for; they were simply never added to
the list. ``tests/test_filelock.py`` covers six JSON workflow stores by name, so a
seventh store, or a PREMIS log with the identical read-modify-write shape, was covered
by nothing and reported green. #155 sat open against a passing suite for exactly that
reason.

A per-site behavioural test (``tests/test_audit_log_concurrency.py``) proves the sites
that exist today are safe. This one proves the *class* stays closed: it fails on a
newly-written appender nobody thought to add a concurrency test for, which is the case
the behavioural tests structurally cannot cover.

The shape it refuses, inside one function:

    log = PremisLog.read(path)     # or PremisLog.read(...) if ... else PremisLog()
    log.record(event)
    log.write(path)                # <-- not inside `with file_lock(...)`

The fix is never to add an exemption here. It is to call
:func:`ledger.metadata.premis.append_event`, which holds the lock across the whole
cycle. This gate has **no allowlist**, deliberately: an allowlist is how a gate quietly
stops gating.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "ledger"


def _source_files() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def _is_premis_read(node: ast.AST) -> bool:
    """``PremisLog.read(...)`` -- loading an existing log off disk."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "read"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "PremisLog"
    )


def _is_log_write(node: ast.AST) -> bool:
    """``<name>.write(<something>)`` on a plain local -- a PremisLog write-back.

    Deliberately narrow. A one-argument ``.write`` on a bare local name is the
    ``log.write(path)`` shape; ``self.wfile.write(body)``, ``handle.write(data)``, and
    ``path.write_text(...)`` are attribute chains or different method names and are not
    matched.
    """
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "write"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id not in {"handle", "tmp", "wfile", "sys", "stdout", "stderr"}
        and len(node.args) == 1
    )


def _holds_file_lock(node: ast.With) -> bool:
    """True if this ``with`` statement acquires ``file_lock(...)``."""
    for item in node.items:
        call = item.context_expr
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name == "file_lock":
            return True
    return False


def _unlocked_rewrites(tree: ast.AST) -> list[tuple[str, int]]:
    """Every ``(function, lineno)`` performing a log write-back outside a lock.

    Walks with an explicit stack of enclosing ``with``-statements so "is this write
    inside a ``file_lock`` block" is answered lexically, at any nesting depth.
    """
    found: list[tuple[str, int]] = []

    def walk(node: ast.AST, func: str | None, locked: bool) -> None:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            func = node.name
            # A nested function does not inherit an enclosing `with`: it runs later.
            locked = False
        elif isinstance(node, ast.With | ast.AsyncWith):
            locked = locked or _holds_file_lock(node)
        elif _is_log_write(node) and not locked:
            found.append((func or "<module>", getattr(node, "lineno", -1)))
        for child in ast.iter_child_nodes(node):
            walk(child, func, locked)

    walk(tree, None, False)
    return found


def test_every_premis_log_writeback_is_serialized() -> None:
    """No module rewrites a PREMIS log outside ``file_lock``.

    Only files that also *read* a log are considered: a freshly-built log written once
    (ingest's brand-new bag) has nothing to lose to a racing writer, because there is no
    prior state it could clobber.
    """
    offenders: list[str] = []
    for path in _source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if not any(_is_premis_read(node) for node in ast.walk(tree)):
            continue
        offenders += [
            f"{path.relative_to(SRC.parent.parent)}:{line} in {func}()"
            for func, line in _unlocked_rewrites(tree)
        ]

    assert offenders == [], (
        "PREMIS log rewritten without ledger._filelock.file_lock:\n  "
        + "\n  ".join(offenders)
        + "\n\nCall ledger.metadata.premis.append_event() instead; it holds the lock "
        "across the whole read-record-write. Do not add an exemption here."
    )


# --- the gate's own teeth ------------------------------------------------------
#
# A structural gate that cannot fail is worse than no gate, and this one is a pile of
# AST predicates that would silently match nothing if `PremisLog` were renamed or the
# walk were broken. These pin that each half still fires.


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        # The defect: read, append, write back, no lock.
        (
            "def f(p):\n    log = PremisLog.read(p)\n    log.record(e)\n    log.write(p)\n",
            1,
        ),
        # The fix: the same cycle inside a file_lock.
        (
            "def f(p):\n"
            "    with file_lock(p):\n"
            "        log = PremisLog.read(p)\n"
            "        log.write(p)\n",
            0,
        ),
        # Nested deeper inside the locked block still counts as locked.
        (
            "def f(p):\n"
            "    with file_lock(p):\n"
            "        if x:\n"
            "            for _ in y:\n"
            "                log.write(p)\n",
            0,
        ),
        # A lock held by an *enclosing* function does not cover a nested one, which
        # runs later, outside the `with`.
        (
            "def outer(p):\n"
            "    with file_lock(p):\n"
            "        def inner():\n"
            "            log.write(p)\n"
            "        return inner\n",
            1,
        ),
        # A `with` that is not a file_lock does not launder the write.
        (
            "def f(p):\n    with open(p) as h:\n        log.write(p)\n",
            1,
        ),
    ],
)
def test_the_detector_actually_detects(source: str, expected: int) -> None:
    """The predicate fires on the defect and stays quiet on the fix."""
    assert len(_unlocked_rewrites(ast.parse(source))) == expected


def test_the_detector_ignores_unrelated_writes() -> None:
    """Stream and path writes are not log write-backs, so they never trip the gate."""
    source = (
        "def f(self, body, path, text):\n"
        "    self.wfile.write(body)\n"
        "    handle.write(body)\n"
        "    path.write_text(text)\n"
        "    tmp.write(body)\n"
    )
    assert _unlocked_rewrites(ast.parse(source)) == []


def test_the_gate_reads_a_real_nonempty_corpus() -> None:
    """The scan actually opens files, and some of them really do use PremisLog.

    Without this, renaming ``PremisLog`` or moving ``src/ledger`` would leave the gate
    scanning nothing at all and passing forever -- vacuously green, which is the exact
    failure this file was written to prevent.
    """
    files = _source_files()
    assert len(files) > 40, f"expected the whole package, scanned {len(files)} files"
    users = [
        p
        for p in files
        if any(_is_premis_read(n) for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))))
    ]
    assert len(users) >= 5, f"only {len(users)} modules read a PremisLog; detector may be broken"
