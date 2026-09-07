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

import json
from pathlib import Path

import pytest
from tools.check_catalog_parity import (
    EXEMPTIONS,
    LOCALES,
    _identity_errors,
    _ids,
    _is_identical_to_source,
    _load,
    _load_exemptions,
    _po_header_field,
    _stale_exemption_errors,
    main,
    untranslatable_reason,
)

from ledger.i18n import PO_REVIEW_HEADER, SOURCE_LANG, SUPPORTED, TranslationReview

REPO_ROOT = Path(__file__).resolve().parent.parent


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


# --- the identity check: a catalog nobody translated -------------------------
#
# Ported from fare-policy-assistant, which measured the defect first: its gate
# printed "catalog parity OK" and exit 0 on a committed `es` catalog with one
# `msgstr` replaced by its own English `msgid`. ledger's copy had the same hole.


@pytest.mark.parametrize(
    "msgid",
    [
        "{count}",
        "{start}-{end}",
        "2026",
        "$2.50",
        "OK",
        "CSV",
        "PDF",
        "WCAG",
        "TK-12",
        "https://example.org/fares",
    ],
)
def test_a_msgid_with_nothing_to_translate_needs_no_exemption(msgid: str) -> None:
    """The positive control, and it is the half that keeps the gate switched on.

    A gate with false positives is a gate somebody disables. These are the msgids
    that are correctly identical in every language: nothing alphabetic once the
    placeholders come out, a bare number or price, a code token, a URL.
    """
    assert untranslatable_reason(msgid) is not None, msgid


@pytest.mark.parametrize("msgid", ["Help", "Date", "Type", "Senior", "Browse", "Ask a question"])
def test_an_ordinary_word_is_not_exempt_just_for_being_short(msgid: str) -> None:
    """Upper case is load-bearing, and this is where the two rules earn the line.

    `CSV` is a code token; `Date` is a four-letter English word that happens to be
    spelled the same in French. Both are single tokens and only one is mechanically
    exempt — the other takes a written reason, which is exactly the judgement a
    pattern cannot make. A blanket must-differ rule gets this backwards in both
    directions.
    """
    assert untranslatable_reason(msgid) is None, msgid


def test_every_committed_exemption_is_currently_doing_work() -> None:
    """The gate runs `_stale_exemption_errors`; this names the file it reads.

    An exemption list that only grows stops describing the catalogs and starts
    describing the project's history.
    """
    exemptions, errors = _load_exemptions(EXEMPTIONS)
    assert errors == []
    assert exemptions, "the exemption file is empty; ledger's catalogs need eight entries"
    catalogs = {loc: _load(LOCALES / loc / "LC_MESSAGES" / "messages.po", loc) for loc in SUPPORTED}
    pot_ids = _ids(_load(LOCALES / "messages.pot", None))
    assert _stale_exemption_errors(catalogs, pot_ids, exemptions) == []


def test_the_committed_catalogs_carry_no_untranslated_string() -> None:
    """The check itself, over the real catalogs, run from the suite as well as the gate."""
    exemptions, _errors = _load_exemptions(EXEMPTIONS)
    catalogs = {loc: _load(LOCALES / loc / "LC_MESSAGES" / "messages.po", loc) for loc in SUPPORTED}
    assert _identity_errors(catalogs, exemptions) == []


def test_an_exemption_without_a_reason_is_refused(tmp_path: Path) -> None:
    """An exemption with no written reason is the gate switched off for that row."""
    path = tmp_path / "identical_by_design.json"
    path.write_text(
        json.dumps({"identical_by_design": [{"locale": "fr", "msgid": "Date", "reason": "  "}]}),
        encoding="utf-8",
    )
    exemptions, errors = _load_exemptions(path)
    assert exemptions == {}
    assert any("no reason" in error for error in errors)


def test_a_malformed_exemption_file_fails_rather_than_reading_as_empty(tmp_path: Path) -> None:
    """A file that fails open turns the check it guards into one that cannot fail."""
    path = tmp_path / "identical_by_design.json"
    path.write_text("{not json", encoding="utf-8")
    exemptions, errors = _load_exemptions(path)
    assert exemptions == {}
    assert errors, "a malformed exemption file read as 'no exemptions'"


def test_an_exemption_that_has_stopped_applying_must_be_deleted() -> None:
    """A row that is now translated, exempted anyway: the list has to shrink too."""
    catalogs = {loc: _load(LOCALES / loc / "LC_MESSAGES" / "messages.po", loc) for loc in SUPPORTED}
    pot_ids = _ids(_load(LOCALES / "messages.pot", None))
    # "Browse" is translated in French ("Explorer"), so exempting it is stale.
    stale = {("fr", "Browse"): "a reason that no longer applies"}
    errors = _stale_exemption_errors(catalogs, pot_ids, stale)
    assert any("now translated" in error for error in errors)


def test_an_exemption_for_a_msgid_the_template_dropped_must_be_deleted() -> None:
    catalogs = {loc: _load(LOCALES / loc / "LC_MESSAGES" / "messages.po", loc) for loc in SUPPORTED}
    pot_ids = _ids(_load(LOCALES / "messages.pot", None))
    gone = {("fr", "a string this project never had"): "stale"}
    errors = _stale_exemption_errors(catalogs, pot_ids, gone)
    assert any("no longer declares" in error for error in errors)


def test_the_source_locale_cannot_be_exempted() -> None:
    """`en`'s msgstr must *equal* its msgid; there is nothing there to excuse."""
    catalogs = {loc: _load(LOCALES / loc / "LC_MESSAGES" / "messages.po", loc) for loc in SUPPORTED}
    pot_ids = _ids(_load(LOCALES / "messages.pot", None))
    errors = _stale_exemption_errors(catalogs, pot_ids, {("en", "Browse"): "no"})
    assert any("source locale" in error for error in errors)


def test_the_identity_check_refuses_to_run_against_no_target_locale() -> None:
    """A rule that iterates over nothing reports success over nothing."""
    source_only = {SOURCE_LANG: _load(LOCALES / SOURCE_LANG / "LC_MESSAGES" / "messages.po", "en")}
    errors = _identity_errors(source_only, {})
    assert any("ran against nothing" in error for error in errors)


def test_a_plural_message_filled_with_english_in_every_form_is_caught() -> None:
    """Arabic declares six forms where English declares two.

    A positional comparison would have to decide which English form each of the six
    should differ from; the set comparison asks the question that actually matters —
    did this catalog add any text the source did not already have.
    """
    from babel.messages.catalog import Message as _Message

    english = _Message(("{n} record", "{n} records"), ("{n} record", "{n} records"))
    arabic_untranslated = _Message(
        ("{n} record", "{n} records"),
        ("{n} record", "{n} records", "{n} records", "{n} record", "{n} records", "{n} record"),
    )
    arabic_translated = _Message(
        ("{n} record", "{n} records"), tuple(f"{{n}} سجل {i}" for i in range(6))
    )
    assert _is_identical_to_source(english)
    assert _is_identical_to_source(arabic_untranslated)
    assert not _is_identical_to_source(arabic_translated)
