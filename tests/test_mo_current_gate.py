"""The G13 gate itself (``tools/check_mo_current.py``).

A gate nobody tests is a gate that silently stops gating, which is the whole reason G13
exists: the committed ``.mo`` catalogs were a compiled artifact nothing re-derived, and
``docs/I18N.md`` said otherwise. These assert both halves — that the gate passes on the
tree as committed, and that it goes red for each way a shipped catalog can disagree with
the reviewed one.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from babel.messages.catalog import Catalog
from babel.messages.pofile import read_po
from tools.check_mo_current import actual_from_mo, compare, expected_from_po, main

from ledger.i18n import SUPPORTED

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCALES = REPO_ROOT / "src" / "ledger" / "locales"


def _po(locale: str) -> Catalog:
    with (LOCALES / locale / "LC_MESSAGES" / "messages.po").open("rb") as fh:
        return read_po(fh)


def test_every_shipped_catalog_agrees_with_its_source() -> None:
    """The gate's actual job, asserted from the suite as well as from `make i18n`."""
    for locale in sorted(SUPPORTED):
        assert compare(locale) == [], f"{locale}: shipped .mo disagrees with its .po"


def test_gate_returns_zero_on_the_committed_tree() -> None:
    assert main() == 0


def test_comparison_is_not_vacuous() -> None:
    """ "0 disagreements" over 0 messages would pass for any pair of catalogs."""
    for locale in sorted(SUPPORTED):
        assert len(expected_from_po(_po(locale))) > 200


def test_mo_and_po_maps_are_actually_equal_not_merely_subsets() -> None:
    """Both directions: a message only in the .mo is drift as much as one only in the .po."""
    for locale in sorted(SUPPORTED):
        expected = expected_from_po(_po(locale))
        actual, _ = actual_from_mo(LOCALES / locale / "LC_MESSAGES" / "messages.mo")
        assert expected.keys() == actual.keys()


def test_plural_forms_reach_the_shipped_catalog() -> None:
    """Arabic's six forms are the case a singular-only comparison would miss."""
    plural_keys = [key for key in expected_from_po(_po("ar")) if isinstance(key, tuple)]
    assert plural_keys, "no plural messages found in the Arabic catalog"
    assert max(index for _, index in plural_keys) >= 5, (
        "Arabic declares six plural forms; the gate is only seeing "
        f"{max(index for _, index in plural_keys) + 1}"
    )
    actual, plural_forms = actual_from_mo(LOCALES / "ar" / "LC_MESSAGES" / "messages.mo")
    assert "nplurals=6" in plural_forms.replace(" ", "")
    for key in plural_keys:
        assert key in actual


def test_a_missing_mo_is_a_failure_not_a_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An absent artifact must not read as "nothing to compare"."""
    import tools.check_mo_current as gate

    monkeypatch.setattr(gate, "LOCALES", tmp_path)
    problems = gate.compare("es")
    assert problems and "missing" in problems[0]
