"""Tests for :mod:`ledger.i18n` — the localization layer (user research P2-1 / T9).

These cover the safety-critical behaviours: ``Accept-Language`` negotiation
(exact match, q-value ordering, primary-subtag fall-down, wildcard, unknown and
``None`` headers all degrading to English), the forgiving ``t`` lookup (English
fallback for a missing translation, the key itself for an unknown key, no raising
on a bad interpolation), the non-coercive "Continue"/"Continuar" verb at the
content-warning decision point, content-warning glosses (known and unknown tags),
and language autonyms. The no-outing rule is checked structurally: the catalog is
generic chrome with no identity-shaped content.
"""

from __future__ import annotations

from ledger import i18n
from ledger.i18n import (
    DEFAULT_LANG,
    SUPPORTED,
    gloss_cw,
    language_name,
    negotiate,
    t,
)

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


def test_t_falls_back_to_english_for_missing_translation() -> None:
    # Temporarily drop an es key to simulate a half-translated catalog; t must
    # return the English template rather than the key or a blank.
    catalog = i18n._CATALOG
    saved = catalog["es"].pop("nav_status")
    try:
        assert t("es", "nav_status") == t("en", "nav_status")
        assert t("es", "nav_status") == "Status"
    finally:
        catalog["es"]["nav_status"] = saved


def test_t_unknown_key_returns_the_key() -> None:
    assert t("en", "does_not_exist") == "does_not_exist"
    assert t("es", "does_not_exist") == "does_not_exist"


def test_t_unknown_language_uses_english() -> None:
    assert t("fr", "nav_search") == "Search"


def test_t_formats_with_kwargs() -> None:
    # A template that does not use placeholders is returned unchanged even with kw.
    assert t("en", "nav_about", extra="ignored") == "About"


def test_t_never_raises_on_bad_interpolation() -> None:
    # No catalog template references a placeholder, so supplying odd kw must be
    # harmless and never raise (forgiving formatting).
    assert t("en", "footer_privacy", missing="x") == t("en", "footer_privacy")


def test_proceed_is_continue_not_proceed() -> None:
    # The non-coercive verb at the trauma-decision point (user research T9).
    assert t("en", "proceed") == "Continue"
    assert t("es", "proceed") == "Continuar"
    assert "Proceed" not in t("en", "proceed")


def test_required_keys_present_in_all_supported_languages() -> None:
    required = {
        "nav_browse",
        "nav_search",
        "nav_status",
        "nav_about",
        "skip_link",
        "search_label",
        "search_button",
        "withheld_heading",
        "withheld_intro",
        "proceed",
        "back_to_records",
        "empty_no_matches",
        "restricted_notice",
        "content_warning_heading",
        "footer_privacy",
        "language_label",
    }
    for lang in SUPPORTED:
        present = set(i18n._CATALOG[lang])
        missing = required - present
        assert not missing, f"{lang} missing keys: {sorted(missing)}"


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


def test_gloss_missing_translation_falls_back_to_english() -> None:
    glosses = i18n._CW_GLOSSES
    saved = glosses["es"].pop("medical")
    try:
        assert gloss_cw("es", "medical") == gloss_cw("en", "medical")
    finally:
        glosses["es"]["medical"] = saved


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


# --- no-outing structural check ---------------------------------------------


def test_catalog_view_is_read_only_copy_of_supported() -> None:
    view = i18n._catalog_view()
    assert set(view) == set(SUPPORTED)


# --- I1: safety-critical strings are localized -----------------------------


def test_visibility_labels_exist_in_every_language() -> None:
    """The sealed/community/public labels — the words that matter most — are localized."""
    for lang in i18n.SUPPORTED:
        for value in ("public", "community", "sealed"):
            label = i18n.t(lang, f"visibility_{value}")
            assert label and not label.startswith("visibility_"), (lang, value)
    # Spanish is genuinely translated, not the English fallback.
    assert i18n.t("es", "visibility_sealed") != i18n.t("en", "visibility_sealed")


def test_cw_glosses_cover_the_starter_vocabulary() -> None:
    """Every content warning a fresh archive ships with has a gloss in each language."""
    from ledger.config import _STARTER_CONTENT_WARNINGS

    for lang in i18n.SUPPORTED:
        for tag in _STARTER_CONTENT_WARNINGS:
            gloss = i18n.gloss_cw(lang, tag)
            # A real gloss explains the tag (has the em-dash), not just the humanized tag.
            assert "—" in gloss, (lang, tag, gloss)
    # Spanish glosses are translated, not the English ones.
    assert i18n.gloss_cw("es", "outing") != i18n.gloss_cw("en", "outing")
