"""The translation-review half of ``tools/check_catalog_parity.py``.

The parity gate checks key parity, non-empty ``msgstr`` and placeholder parity.
**A Spanish ``msgstr`` that is verbatim English satisfies all three**, so a green
run proves the catalogs are structurally sound and proves nothing at all about
whether a qualified speaker has read them. Review status is therefore *declared* —
in :data:`ledger.i18n.REVIEWED_LOCALES` and again in each catalog's PO header —
and the gate's job is to make the two agree, so the disclosure cannot rot.

These tests pin the gate itself: that it passes as committed, and that it goes red
for each way the declaration can go wrong. A gate nobody tests is a gate that
silently stops gating.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tools.check_catalog_parity import _po_header_field, main

from ledger.i18n import PO_REVIEW_HEADER, SOURCE_LANG, SUPPORTED, TranslationReview

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCALES = REPO_ROOT / "src" / "ledger" / "locales"


def _po(locale: str) -> Path:
    return LOCALES / locale / "LC_MESSAGES" / "messages.po"


def test_gate_passes_on_the_committed_tree() -> None:
    assert main() == 0


def test_every_shipped_non_source_catalog_declares_its_review_status() -> None:
    """Derived from ``SUPPORTED``: a fifth catalog with no declaration fails here.

    A list of locale codes written into this test would go stale the moment one was
    added and would still pass — the fixture has to move with the registry.
    """
    others = [lang for lang in SUPPORTED if lang != SOURCE_LANG]
    assert others, "no non-source locale ships; this test would be vacuous"
    for lang in others:
        declared = _po_header_field(_po(lang), PO_REVIEW_HEADER)
        assert declared in {status.value for status in TranslationReview}, (
            f"{lang}: {PO_REVIEW_HEADER} is {declared!r}"
        )


def test_the_header_field_is_read_from_the_file_not_through_babel() -> None:
    """Babel's ``read_po`` drops unknown ``X-`` header fields silently.

    That is why the gate reads the bytes. If this ever regressed to asking the
    parsed catalog, every locale would read as undeclared and the gate would fail
    closed — noisily, but for the wrong reason. Pinned so the reason is recorded.
    """
    from babel.messages.pofile import read_po

    with _po("es").open("rb") as fh:
        catalog = read_po(fh, locale="es")
    parsed = {key.lower() for key, _value in catalog.mime_headers}
    assert PO_REVIEW_HEADER.lower() not in parsed, (
        "Babel now preserves the field; the gate could read it from the catalog"
    )
    assert _po_header_field(_po("es"), PO_REVIEW_HEADER) == "drafted"


def test_a_missing_declaration_is_a_failure(tmp_path: Path) -> None:
    """The absent case, which is the one a header-field check most easily fumbles."""
    stripped = "\n".join(
        line
        for line in _po("es").read_text(encoding="utf-8").splitlines()
        if not line.startswith(f'"{PO_REVIEW_HEADER}:')
    )
    target = tmp_path / "messages.po"
    target.write_text(stripped, encoding="utf-8")
    assert _po_header_field(target, PO_REVIEW_HEADER) is None


def test_a_header_field_after_the_header_entry_is_not_read(tmp_path: Path) -> None:
    """A quoted line further down the file is a message, not a header field."""
    source = _po("es").read_text(encoding="utf-8")
    stripped = "\n".join(
        line for line in source.splitlines() if not line.startswith(f'"{PO_REVIEW_HEADER}:')
    )
    target = tmp_path / "messages.po"
    target.write_text(
        stripped + f'\n\nmsgid "x"\nmsgstr "{PO_REVIEW_HEADER}: reviewed"\n', encoding="utf-8"
    )
    assert _po_header_field(target, PO_REVIEW_HEADER) is None


@pytest.mark.parametrize("lang", ["es", "fr", "ar"])
def test_the_catalog_comment_states_the_status_a_translator_would_read(lang: str) -> None:
    """The header field is for the gate; the comment is for the person opening the file."""
    head = _po(lang).read_text(encoding="utf-8").split('msgid ""', 1)[0]
    assert "TRANSLATION REVIEW: drafted" in head
