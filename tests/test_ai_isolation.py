"""Proves the AI layer is strictly additive at the edges (ADR 0013).

Two things must both hold for "zero AI, byte-for-byte the pre-AI system" to be
true rather than merely asserted:

1. The deterministic preservation/access/browse core never imports
   :mod:`ledger.ai` — there is no path by which adding this package could have
   changed what ``ingest``, ``bag``, ``fixity``, ``access.policy``, ``search``,
   or the browse ``server`` compute.
2. :mod:`ledger.ai` itself never requires the optional ``anthropic`` SDK
   installed just to import it — the same guarded pattern
   :mod:`ledger.print_edition` already uses for the optional ``segno`` package.

Both are checked structurally (AST inspection, a real import), not by trusting
a docstring.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.disclosure

_SRC = Path(__file__).resolve().parent.parent / "src" / "ledger"

#: The one module documented as allowed to import `ledger.ai`: it wires the
#: opt-in `ai-describe`/`ai-ask` subcommands and nothing else does.
_DOCUMENTED_AI_ENTRY_POINT = "cli.py"


def _core_modules() -> list[str]:
    """Every module in the deterministic core, DERIVED from the tree.

    This used to be a hand-written tuple of twenty entries, which meant roughly
    thirty-five modules -- `export.py`, `attestation.py`, `consent.py`,
    `transparency.py`, `reading_room_enclave.py` among them -- were never
    checked at all, and a new `ledger.ai` import in any of them would not have
    failed the build. A gate standing in for "all modules" has to actually be
    all modules, or it is an allowlist wearing a gate's name.

    The derivation has no allowlist: the core is *everything* under
    `src/ledger/` except the AI package itself and the one documented entry
    point. A module added tomorrow is covered the day it is added, without
    anyone remembering to list it.
    """
    modules = []
    for path in sorted(_SRC.rglob("*.py")):
        rel = path.relative_to(_SRC).as_posix()
        if rel.startswith("ai/") or rel == _DOCUMENTED_AI_ENTRY_POINT:
            continue
        modules.append(rel)
    return modules


_CORE_MODULES = tuple(_core_modules())


def _imported_module_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
    return names


@pytest.mark.parametrize("rel", _CORE_MODULES)
def test_core_module_does_not_import_ai_package(rel: str) -> None:
    """No preservation/access/browse module imports `ledger.ai` or `anthropic`."""
    imported = _imported_module_names(_SRC / rel)
    offending = {name for name in imported if name == "anthropic" or name.startswith("ledger.ai")}
    assert not offending, f"{rel} imports {offending}, which must stay confined to the AI edge"


def test_the_core_module_set_is_derived_and_not_a_short_list() -> None:
    """Anti-vacuity guard, of the same shape the repo's other AST gates carry.

    An `rglob` that silently matches nothing -- a renamed package, a moved test
    file -- would turn every parametrized case above into a pass. The floor is
    set well below the current count so ordinary growth does not trip it, and
    well above the twenty entries the hand-written list held.
    """
    assert len(_CORE_MODULES) >= 40, (
        f"only {len(_CORE_MODULES)} core modules found under {_SRC}; the derivation is "
        "matching far less than the tree holds"
    )
    # Modules the hand-written list omitted, named explicitly so a future
    # narrowing of the derivation is caught by name rather than by count alone.
    for previously_unchecked in (
        "export.py",
        "attestation.py",
        "consent.py",
        "transparency.py",
        "reading_room_enclave.py",
        "checkup.py",
        "oai.py",
        "render.py",
        "review.py",
        "contribute.py",
        "upload.py",
    ):
        assert previously_unchecked in _CORE_MODULES, (
            f"{previously_unchecked} is in the core and must be checked for AI imports"
        )


def test_the_derivation_excludes_only_the_ai_package_and_the_entry_point() -> None:
    """The two exclusions are the whole of the exception list, and both are real."""
    assert _DOCUMENTED_AI_ENTRY_POINT not in _CORE_MODULES
    assert not [rel for rel in _CORE_MODULES if rel.startswith("ai/")]
    on_disk = {p.relative_to(_SRC).as_posix() for p in _SRC.rglob("*.py")}
    excluded = on_disk - set(_CORE_MODULES)
    assert excluded == {rel for rel in on_disk if rel.startswith("ai/")} | {
        _DOCUMENTED_AI_ENTRY_POINT
    }, f"the derivation excludes more than it should: {sorted(excluded)}"


def test_cli_is_the_documented_ai_entry_point() -> None:
    """`cli.py` is the one documented core-adjacent module allowed to import
    `ledger.ai` (it wires the opt-in `ai-describe`/`ai-ask` subcommands)."""
    imported = _imported_module_names(_SRC / "cli.py")
    assert any(name.startswith("ledger.ai") for name in imported)


def test_ai_package_importable_without_anthropic_installed() -> None:
    """Importing `ledger.ai` never requires the optional `anthropic` SDK.

    Whether or not `anthropic` happens to be installed in THIS environment
    (the `ai` extra), importing the package must not add it to `sys.modules`
    as a side effect of the import itself — mirroring how importing
    `ledger.print_edition` never requires `segno`.
    """
    before = "anthropic" in sys.modules
    import ledger.ai  # noqa: F401 -- imported only for its side effect (populating sys.modules)

    after = "anthropic" in sys.modules
    assert before == after, "importing ledger.ai must not import anthropic as a side effect"


def test_ai_submodules_importable_without_anthropic_installed() -> None:
    """Every submodule in the package imports cleanly on its own, too."""
    import ledger.ai.ask
    import ledger.ai.client
    import ledger.ai.context
    import ledger.ai.describe
    import ledger.ai.fixity_honesty
    import ledger.ai.grounding
    import ledger.ai.limits
    import ledger.ai.prompts

    # Exercised only for its side effect (a successful, anthropic-free import).
    import ledger.ai.provenance  # noqa: F401
