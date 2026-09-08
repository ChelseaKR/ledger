"""RM7 tests: RTL plumbing (G10), the G11 language headers, and the G9 pseudolocale.

These pin the newly closed i18n gates end to end:

* **G10 RTL** — a page negotiated to Arabic sets ``<html lang="ar" dir="rtl">``, while
  an LTR language sets ``dir="ltr"``, so an Arabic reader gets a correctly laid-out
  page.
* **G11 headers** — *every* response (HTML, plain text, JSON) carries
  ``Content-Language`` (the negotiated language) and ``Vary: Accept-Language``, so a
  shared cache is language-correct.
* **G9 pseudolocale** — rendering the chrome through a pseudolocalized gettext seam
  wraps every *localized* string in accent markers; any string that reached the page
  without going through the seam (a hardcoded English label) would stay plain ASCII
  and is caught here.
"""

from __future__ import annotations

import re
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import pytest

from ledger import i18n
from ledger.config import Config
from ledger.ingest import Archive
from ledger.render import _esc, _nav_html, _page
from ledger.server import make_server

_VAULT_KEY = "0123456789abcdef0123456789abcdef0123456789a="


def _server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setenv("LEDGER_VAULT_KEY", _VAULT_KEY)
    archive = Archive.init(Config.default("RTL Archive", tmp_path / "arc"))
    httpd = make_server(archive, host="127.0.0.1", port=0)
    base = f"http://127.0.0.1:{int(httpd.server_address[1])}"
    sink = StringIO()
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    with redirect_stderr(sink), redirect_stdout(sink):
        thread.start()
        try:
            yield base
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()


@pytest.fixture
def base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    yield from _server(tmp_path, monkeypatch)


def _request(url: str, *, accept_language: str | None = None) -> tuple[int, str, dict[str, str]]:
    req = urllib.request.Request(url)  # noqa: S310 - loopback
    if accept_language is not None:
        req.add_header("Accept-Language", accept_language)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - loopback URL we constructed for the in-process test server
            return int(resp.status), resp.read().decode("utf-8"), dict(resp.headers)
    except urllib.error.HTTPError as exc:  # pragma: no cover - not expected here
        return int(exc.code), exc.read().decode("utf-8"), dict(exc.headers)


# --- G10: RTL direction on the rendered page --------------------------------


def test_arabic_page_is_rtl_and_lang_ar(base: str) -> None:
    _status, body, _headers = _request(f"{base}/?lang=ar")
    assert '<html lang="ar" dir="rtl">' in body
    # The chrome is actually Arabic, not the English fallback.
    assert i18n.t("ar", "nav_browse") in body


@pytest.mark.parametrize("lang", ["en", "es", "fr"])
def test_ltr_languages_render_dir_ltr(base: str, lang: str) -> None:
    _status, body, _headers = _request(f"{base}/?lang={lang}")
    assert f'<html lang="{lang}" dir="ltr">' in body


def test_page_shell_sets_dir_from_text_direction() -> None:
    # Unit-level: the shell threads text_direction into <html dir=…>.
    assert '<html lang="ar" dir="rtl">' in _page("t", lang="ar", main_html="<p>x</p>")
    assert '<html lang="en" dir="ltr">' in _page("t", lang="en", main_html="<p>x</p>")


# --- G11: Content-Language + Vary on every response -------------------------


@pytest.mark.parametrize(
    ("path", "accept", "expected_lang"),
    [
        ("/", "ar", "ar"),
        ("/?lang=fr", "en", "fr"),
        ("/healthz", "en", "en"),
    ],
)
def test_every_response_carries_language_headers(
    base: str, path: str, accept: str, expected_lang: str
) -> None:
    _status, _body, headers = _request(f"{base}{path}", accept_language=accept)
    assert headers.get("Content-Language") == expected_lang
    assert "Accept-Language" in headers.get("Vary", "")


@pytest.mark.parametrize("path", ["/robots.txt", "/feed.atom", "/sitemap.xml"])
def test_machine_feeds_do_not_vary_by_language(base: str, path: str) -> None:
    """Feeds are always the anonymous-public view; they carry no language headers.

    Mirrors main's G11 decision (test_language_switch): OAI-PMH, the sitemap,
    robots.txt, and the Atom feed never vary with Accept-Language, so marking
    them Content-Language would be a cache-correctness lie."""
    _status, _body, headers = _request(f"{base}{path}", accept_language="es")
    assert "Content-Language" not in headers


def test_content_language_defaults_to_english(base: str) -> None:
    _status, _body, headers = _request(f"{base}/")
    assert headers.get("Content-Language") == "en"
    assert headers.get("Vary") == "Accept-Language"


# --- G9: pseudolocale round-trip catches hardcoded chrome -------------------


def _install_pseudolocale(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wrap the real gettext seam so every localized string is pseudolocalized."""
    real_t = i18n.t
    real_gloss = i18n.gloss_cw
    monkeypatch.setattr(
        i18n, "t", lambda lang, key, /, **kw: i18n.pseudolocalize(real_t(lang, key, **kw))
    )
    monkeypatch.setattr(
        i18n, "gloss_cw", lambda lang, tag: i18n.pseudolocalize(real_gloss(lang, tag))
    )


def test_pseudolocale_wraps_all_chrome_and_flags_hardcoded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_pseudolocale(monkeypatch)
    nav = _nav_html("en", contribute=True, current_path="/")
    page = _page(i18n.t("en", "nav_browse"), lang="en", main_html="<p>x</p>", nav_html=nav)

    # Every localized label went through the seam, so it is accent-wrapped …
    assert i18n.PSEUDO_PREFIX in page and i18n.PSEUDO_SUFFIX in page
    # … and its plain-English form is therefore absent. If any of these labels were
    # ever hardcoded instead of routed through i18n.t, the plain string would reappear
    # here and fail the test (that is the point of the pseudolocale round-trip).
    for plain in ("Browse", "Search", "Overview", "Skip to main content"):
        assert plain not in page, f"un-pseudolocalized (hardcoded?) chrome: {plain!r}"


def test_pseudolocale_render_is_not_wired_into_production(base: str) -> None:
    # Sanity: a normal request (no monkeypatch) shows real chrome, never pseudo markers.
    _status, body, _headers = _request(f"{base}/?lang=en")
    assert i18n.PSEUDO_PREFIX not in body
    assert "Browse" in body


# --- translation review: the gate cannot see it, so the page says it ---------
#
# `make i18n` enforces key parity, non-empty msgstr and placeholder parity. A
# Spanish msgstr that is verbatim English satisfies all three, so a green i18n run
# says nothing about whether a translation is right or whether anyone who speaks
# the language has read it. The status is therefore declared in
# `ledger.i18n.REVIEWED_LOCALES`, restated in each catalog's PO header, checked to
# agree by `tools/check_catalog_parity.py`, and disclosed on every page served
# from an unreviewed catalog. These tests pin the last of those.


@pytest.mark.parametrize("lang", ["es", "fr", "ar"])
def test_a_page_in_an_unreviewed_language_says_so_in_that_language(base: str, lang: str) -> None:
    """Not in English: a reader who cannot read the source text is the one being told."""
    _status, body, _headers = _request(f"{base}/?lang={lang}")
    notice = i18n.t(lang, "translation_notice")
    # Escaped, because the shell escapes it: the French string carries an
    # apostrophe, and comparing the raw text would pass for es/ar and fail for fr.
    assert _esc(notice) in body
    # The notice really is translated, not the English fallback leaking through.
    assert notice != i18n.t("en", "translation_notice")


def test_the_english_pages_carry_no_translation_notice(base: str) -> None:
    """English is the source text, so there is no translation to disclose."""
    _status, body, _headers = _request(f"{base}/?lang=en")
    assert _esc(i18n.t("en", "translation_notice")) not in body


def test_every_shipped_non_source_locale_is_covered(base: str) -> None:
    """Derived from SUPPORTED, so a locale added without a review status fails here.

    A list of language codes written down in this test would go stale the moment a
    fifth catalog shipped, and would still pass.
    """
    unreviewed = [
        lang
        for lang in i18n.SUPPORTED
        if i18n.translation_review(lang) is i18n.TranslationReview.DRAFTED
    ]
    assert unreviewed, "no unreviewed locale to exercise — has REVIEWED_LOCALES changed?"
    for lang in unreviewed:
        _status, body, _headers = _request(f"{base}/?lang={lang}")
        assert _esc(i18n.t(lang, "translation_notice")) in body, lang


def test_review_status_is_three_states_and_an_unknown_tag_is_source() -> None:
    """An unknown tag is served the English msgids, so it is SOURCE, not DRAFTED.

    Calling it "unreviewed" would be the mirror of the defect this disclosure
    exists for: reporting a translation nobody made as one nobody checked.
    """
    assert i18n.translation_review("en") is i18n.TranslationReview.SOURCE
    assert i18n.translation_review("zz") is i18n.TranslationReview.SOURCE
    assert i18n.translation_review("es") is i18n.TranslationReview.DRAFTED


# --- G9, widened so it can actually fail -------------------------------------
#
# `docs/I18N.md` describes G9 as asserting "no un-wrapped (hardcoded) English
# leaks". The test above it does not do that: it asserts four specific words are
# absent, and all four already go through the seam. **The gate could only find
# hardcoded English in the set of strings that are not hardcoded** — a fixture
# sitting where the failure is impossible. Measured on origin/main before this
# change, five real leaks had been green in the page shell since `ar` shipped:
# `Contribute` (one of nine nav links, and the only one not localized),
# `Governance`, `How it works`, the reference-implementation banner, and the brand
# tagline.

#: Latin-script words allowed to survive un-wrapped in a pseudolocalized page, each
#: for a stated reason. This list is the design work: a blanket rule fires on `href`
#: values and `lang="ar"` immediately, and a gate with false positives gets deleted.
#: Attribute values are excluded structurally instead (tags are stripped whole), so
#: only *visible text* reaches this allowlist.
_INVARIANT_IN_EVERY_LANGUAGE = {
    # Autonyms. `docs/I18N.md`: "an autonym is invariant across the UI language, so a
    # language picker always reads naturally to a native speaker." Deliberately NOT
    # gettext-translated, so deliberately not wrapped.
    "English",
    "Espa",  # Español — the ñ splits the word for a Latin-letter regex
    "ol",
    "Fran",  # Français — same, for the ç
    "ais",
    # The product name. The tagline beside it ("community archive") is translated;
    # the name is not, the way "Wikipedia" is not.
    "ledger",
}


def _visible_unwrapped_words(page: str) -> set[str]:
    """Latin-script words in a pseudolocalized page that never went through the seam.

    Every string resolved through `i18n.t`/`gloss_cw` comes back bracketed by
    `PSEUDO_PREFIX`/`PSEUDO_SUFFIX`, so removing those spans leaves exactly the text
    that reached the page some other way. Tags are then stripped **whole**, which
    drops `href`, `lang` and `hreflang` values — they are Latin by necessity and are
    not prose.

    Deliberately not written as "assert every word is accented": `pseudolocalize`'s
    map leaves `m`, `q`, `v`, `w` and `x` as themselves, so accent-detection would
    quietly pass a hardcoded word built only from those. Bracket-stripping does not
    depend on which letters a word happens to contain.
    """
    without_wrapped = re.sub(
        re.escape(i18n.PSEUDO_PREFIX) + r".*?" + re.escape(i18n.PSEUDO_SUFFIX),
        " ",
        page,
        flags=re.DOTALL,
    )
    text = re.sub(r"<[^>]+>", " ", without_wrapped)
    return set(re.findall(r"[A-Za-z][A-Za-z'’\-]*", text))


def test_no_prose_reaches_the_page_shell_without_going_through_the_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gate `docs/I18N.md` always described, finally able to fail.

    Renders the whole shared shell — banner, brand, nav, footer — through the
    pseudolocale and asserts that the only un-wrapped Latin-script text left is the
    handful of things that are invariant by design.
    """
    _install_pseudolocale(monkeypatch)
    nav = _nav_html("en", contribute=True, current_path="/")
    # An empty <main>: the claim under test is about the *shell* — banner, brand, nav,
    # footer — which every page renders. Page bodies are a larger surface and some of
    # them are steward-authored config text ledger cannot translate at all.
    page = _page(i18n.t("en", "nav_browse"), lang="en", main_html="", nav_html=nav)

    leaked = _visible_unwrapped_words(page) - _INVARIANT_IN_EVERY_LANGUAGE
    assert leaked == set(), (
        f"prose reached the page without going through i18n.t: {sorted(leaked)}. "
        "Route it through the gettext seam, or — if it is genuinely invariant across "
        "every language — add it to _INVARIANT_IN_EVERY_LANGUAGE with a reason."
    )


def test_the_widened_gate_is_not_vacuous(monkeypatch: pytest.MonkeyPatch) -> None:
    """It must be looking at a page with real text in it.

    A shell that rendered nothing, or a bracket regex that ate everything, would make
    the assertion above pass over an empty set. This pins that the page really does
    carry wrapped chrome and that the extractor really does see words.
    """
    _install_pseudolocale(monkeypatch)
    nav = _nav_html("en", contribute=True, current_path="/")
    page = _page(i18n.t("en", "nav_browse"), lang="en", main_html="", nav_html=nav)

    assert page.count(i18n.PSEUDO_PREFIX) >= 10, "the shell is not routing chrome through the seam"
    assert _visible_unwrapped_words(page) >= _INVARIANT_IN_EVERY_LANGUAGE, (
        "the extractor found none of the invariants, so it is not reading the page"
    )


@pytest.mark.parametrize("lang", ["es", "fr", "ar"])
def test_the_shell_renders_no_english_prose_in_a_translated_page(base: str, lang: str) -> None:
    """The same claim from the other end, against a real served page.

    The pseudolocale test above proves every shell string goes through the seam; this
    proves the catalogs actually answer for them, in the language a reader asked for.
    """
    _status, body, _headers = _request(f"{base}/?lang={lang}")
    for english in ("Contribute", "Governance", "How it works", "Reference implementation"):
        assert english not in body, f"{english!r} was served untranslated on a {lang} page"
