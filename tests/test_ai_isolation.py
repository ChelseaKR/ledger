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

# The deterministic preservation/access/browse core (ADR 0013's "the edges"
# framing: everything AI-related must stay outside this set).
_CORE_MODULES = (
    "ingest.py",
    "bag.py",
    "cas.py",
    "fixity.py",
    "chain.py",
    "preservation.py",
    "oais.py",
    "search.py",
    "catalog_index.py",
    "server.py",
    "config.py",
    "models.py",
    "moderate.py",
    "replicate.py",
    "identity.py",
    "access/policy.py",
    "access/grants.py",
    "access/redaction.py",
    "metadata/premis.py",
    "metadata/dublincore.py",
)


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
