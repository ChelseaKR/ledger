"""Tests for :mod:`ledger.i18n` — the gettext localization seam (user research P2-1 / T9).

These cover the safety-critical behaviours after the migration from the bespoke
``_CATALOG``/``_CW_GLOSSES`` dicts to GNU gettext catalogs
(INTERNATIONALIZATION-STANDARD §3): ``Accept-Language`` negotiation (exact match,
q-value ordering, primary-subtag fall-down, wildcard, unknown and ``None`` headers
all degrading to English), the forgiving ``t`` lookup (English fallback for an
unknown language, the key itself for an unknown key, no raising on a bad
interpolation), plural-correct ``ngettext`` selection, the non-coercive
"Continue"/"Continuar" verb at the content-warning decision point, content-warning
glosses (known and unknown tags), and language autonyms. The no-outing rule is
checked structurally: the catalog is generic chrome with no identity-shaped content.
"""

from __future__ import annotations

import pytest

from ledger import i18n
from ledger.i18n import (
    DEFAULT_LANG,
    SUPPORTED,
    get_translation,
    gloss_cw,
    language_name,
    negotiate,
    t,
)

# --- get_translation (the gettext seam) -------------------------------------


def test_get_translation_loads_spanish_catalog() -> None:
    assert get_translation("es").gettext("Browse") == "Explorar"


def test_get_translation_english_is_source_text() -> None:
    assert get_translation("en").gettext("Browse") == "Browse"


def test_get_translation_unknown_tag_falls_back_to_source() -> None:
    # fallback=True -> NullTranslations returns the English msgid unchanged.
    assert get_translation("xx").gettext("Browse") == "Browse"


# --- negotiate --------------------------------------------------------------


def test_negotiate_exact_match() -> None:
    assert negotiate("es") == "es"
    assert negotiate("en") == "en"


def test_negotiate_respects_q_values() -> None:
    # Spanish is preferred over English by q-value, regardless of header order.
    assert negotiate("en;q=0.5, es;q=0.9") == "es"
    assert negotiate("es;q=0.2, en;q=0.8") == "en"


def test_negotiate_default_quality_outranks_explicit_lower_q() -> None:
    # A tag with no q-value defaults to q=1.0 and beats an explicit lower q.
    assert negotiate("es, en;q=0.9") == "es"


def test_negotiate_primary_subtag_fall_down() -> None:
    # A regional variant the catalog does not have falls down to its primary subtag.
    assert negotiate("es-MX") == "es"
    assert negotiate("en-US,en;q=0.9") == "en"


def test_negotiate_wildcard_picks_first_available() -> None:
    assert negotiate("*") == "en"
    assert negotiate("*", available=("es", "en")) == "es"


def test_negotiate_unknown_language_falls_back_to_default() -> None:
    assert negotiate("fr") == DEFAULT_LANG
    assert negotiate("de-DE, zh;q=0.8") == DEFAULT_LANG


def test_negotiate_none_falls_back_to_default() -> None:
    assert negotiate(None) == DEFAULT_LANG


def test_negotiate_empty_or_malformed_header_falls_back() -> None:
    assert negotiate("") == DEFAULT_LANG
    assert negotiate("   ") == DEFAULT_LANG
    # q=0 means "not acceptable"; with nothing else usable we get the default.
    assert negotiate("es;q=0") == DEFAULT_LANG


def test_negotiate_skips_unacceptable_then_matches_next() -> None:
    # Spanish refused (q=0), English acceptable -> English.
    assert negotiate("es;q=0, en;q=0.7") == "en"


def test_negotiate_malformed_q_value_is_dropped() -> None:
    # A non-numeric q is treated as unacceptable and skipped, falling to the next.
    assert negotiate("es;q=high, en") == "en"


def test_negotiate_is_case_insensitive() -> None:
    assert negotiate("ES-mx") == "es"


def test_negotiate_empty_available_returns_default() -> None:
    assert negotiate("es", available=()) == DEFAULT_LANG


# --- t ----------------------------------------------------------------------


def test_t_returns_translated_string() -> None:
    assert t("es", "nav_browse") == "Explorar"
    assert t("en", "nav_browse") == "Browse"


def test_t_unknown_language_uses_english_source() -> None:
    # An unknown language loads NullTranslations; gettext returns the English msgid.
    assert t("fr", "nav_search") == "Search"


def test_t_unknown_key_returns_the_key() -> None:
    assert t("en", "does_not_exist") == "does_not_exist"
    assert t("es", "does_not_exist") == "does_not_exist"


def test_t_formats_with_kwargs() -> None:
    # A template that does not use placeholders is returned unchanged even with kw.
    assert t("en", "nav_about", extra="ignored") == "About"


def test_t_interpolates_placeholders() -> None:
    assert t("en", "overview_total", count=3) == "3 public record(s)."
    assert t("es", "overview_total", count=3) == "3 registro(s) público(s)."


def test_t_never_raises_on_bad_interpolation() -> None:
    # A template with placeholders, called with none, returns the template unformatted
    # rather than raising (forgiving formatting).
    unformatted = t("en", "overview_total")
    assert unformatted == "{count} public record(s)."
    # And a no-placeholder template tolerates stray kw.
    assert t("en", "footer_privacy", missing="x") == t("en", "footer_privacy")


def test_proceed_is_continue_not_proceed() -> None:
    # The non-coercive verb at the trauma-decision point (user research T9).
    assert t("en", "proceed") == "Continue"
    assert t("es", "proceed") == "Continuar"
    assert "Proceed" not in t("en", "proceed")


# --- plural-correct ngettext ------------------------------------------------


@pytest.mark.parametrize(
    ("lang", "count", "expected"),
    [
        ("en", 1, "Edited (1 time)"),
        ("en", 3, "Edited (3 times)"),
        ("es", 1, "Editado (1 vez)"),
        ("es", 4, "Editado (4 veces)"),
    ],
)
def test_badge_edited_plural(lang: str, count: int, expected: str) -> None:
    assert t(lang, "badge_edited", count=count) == expected


def test_rec_withheld_outsider_plural_selects_singular_and_plural() -> None:
    assert t("en", "rec_withheld_outsider", count=1).startswith("1 detail is restricted")
    assert t("en", "rec_withheld_outsider", count=2).startswith("2 details are restricted")
    assert t("es", "rec_withheld_outsider", count=1).startswith("1 detalle está restringido")
    assert t("es", "rec_withheld_outsider", count=2).startswith("2 detalles están restringidos")


def test_plural_missing_count_degrades_to_singular() -> None:
    # No count supplied: _coerce_count -> 0, which is plural in en/es (n != 1), and
    # the unformatted template is returned rather than raising.
    assert t("en", "badge_edited") == "Edited ({count} times)"


# --- gloss_cw ---------------------------------------------------------------


def test_gloss_known_tag_english() -> None:
    assert gloss_cw("en", "medical") == (
        "Medical — describes injury, illness, or clinical procedures"
    )


def test_gloss_known_tag_spanish() -> None:
    gloss = gloss_cw("es", "deadnaming")
    assert gloss != gloss_cw("en", "deadnaming")
    assert gloss.startswith("Nombre muerto")


def test_gloss_all_required_tags_covered() -> None:
    for tag in ("medical", "police-violence", "death", "deadnaming", "incarceration"):
        for lang in ("en", "es"):
            gloss = gloss_cw(lang, tag)
            assert "—" in gloss
            assert gloss != tag


def test_gloss_unknown_tag_is_humanized() -> None:
    assert gloss_cw("en", "natural-disaster") == "Natural disaster"
    assert gloss_cw("es", "house_fire") == "House fire"


def test_gloss_unknown_language_falls_back_to_english_source() -> None:
    assert gloss_cw("fr", "medical") == gloss_cw("en", "medical")


# --- language_name ----------------------------------------------------------


def test_language_name_known_codes() -> None:
    assert language_name("en") == "English"
    assert language_name("es") == "Español"


def test_language_name_uses_proper_accent() -> None:
    # Must be the proper autonym with the accent, not "Espanol".
    assert language_name("es") == "Español"
    assert "Espanol" not in language_name("es")


def test_language_name_primary_subtag() -> None:
    assert language_name("es-MX") == "Español"
    assert language_name("EN-GB") == "English"


def test_language_name_unknown_is_humanized() -> None:
    assert language_name("fr") == "Fr"
    assert language_name("klingon-tlh") == "Klingon tlh"


# --- I1: safety-critical strings are localized ------------------------------


def test_visibility_labels_exist_in_every_language() -> None:
    """The sealed/community/public labels — the words that matter most — are localized."""
    for lang in SUPPORTED:
        for value in ("public", "community", "sealed"):
            label = i18n.t(lang, f"visibility_{value}")
            assert label and not label.startswith("visibility_"), (lang, value)
    # Spanish is genuinely translated, not the English fallback.
    assert i18n.t("es", "visibility_sealed") != i18n.t("en", "visibility_sealed")


def test_cw_glosses_cover_the_starter_vocabulary() -> None:
    """Every content warning a fresh archive ships with has a gloss in each language."""
    from ledger.config import _STARTER_CONTENT_WARNINGS

    for lang in SUPPORTED:
        for tag in _STARTER_CONTENT_WARNINGS:
            gloss = i18n.gloss_cw(lang, tag)
            # A real gloss explains the tag (has the em-dash), not just the humanized tag.
            assert "—" in gloss, (lang, tag, gloss)
    # Spanish glosses are translated, not the English ones.
    assert i18n.gloss_cw("es", "outing") != i18n.gloss_cw("en", "outing")
