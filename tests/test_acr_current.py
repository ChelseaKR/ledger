"""The committed Accessibility Conformance Report still matches the code that renders it.

``docs/accessibility/ACR.md`` is a committed artifact standing in for a computation.
``make acr`` writes it from :mod:`ledger.acr_gen`, ``make acr`` was not part of ``make
verify``, and no test opened the file, so for the whole life of the report nothing
compared the committed bytes to what the generator produces. A conformance level edited
in ``_SECTIONS`` could ship while the document a procurement reviewer actually reads
still said the opposite, and every gate would have stayed green.

Two things close that, deliberately in two places: ``make acr-check`` (composed by
``make verify``) and this module (run by ``make test``, which ``verify`` also composes).
Both call the same :func:`ledger.acr_gen.check`, so there is one comparison, not two
implementations that can drift apart.

The gate compares. It never regenerates into the working tree: an artifact check that
rewrites its own subject heals drift silently on every local run while the committed
bytes stay stale, which is how this class of staleness hides in the first place.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ledger.acr_gen import check, main, render

pytestmark = pytest.mark.accessibility

REPO_ROOT = Path(__file__).resolve().parent.parent
ACR = REPO_ROOT / "docs" / "accessibility" / "ACR.md"


def test_committed_acr_is_byte_identical_to_the_generator() -> None:
    """The whole point: the document equals what the code renders, byte for byte."""
    assert ACR.read_text(encoding="utf-8") == render(), (
        "docs/accessibility/ACR.md has drifted from ledger.acr_gen. "
        "Run `make acr` and commit the result."
    )


def test_check_passes_on_the_committed_report() -> None:
    assert check(ACR) == 0


def test_check_fails_on_a_drifted_report(tmp_path: Path) -> None:
    """A single altered character must go red, or the gate is decorative."""
    drifted = tmp_path / "ACR.md"
    drifted.write_text(render().replace("Partially Supports", "Supports", 1), encoding="utf-8")
    assert check(drifted) == 1


def test_check_fails_on_a_missing_report(tmp_path: Path) -> None:
    """Absence is drift too: a deleted artifact must not read as "nothing to compare"."""
    assert check(tmp_path / "gone.md") == 1


def test_check_writes_nothing(tmp_path: Path) -> None:
    """The gate must not repair what it is judging (see the module docstring)."""
    drifted = tmp_path / "ACR.md"
    stale = render().replace("Partially Supports", "Supports", 1)
    drifted.write_text(stale, encoding="utf-8")
    assert check(drifted) == 1
    assert drifted.read_text(encoding="utf-8") == stale
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ACR.md"]


def test_cli_check_mode_mirrors_the_function() -> None:
    assert main(["--check", str(ACR)]) == 0


def test_cli_default_mode_prints_the_report(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert capsys.readouterr().out == render()
