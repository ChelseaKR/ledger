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
from collections.abc import Callable, Iterator
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import dataclass
from io import StringIO
from pathlib import Path

import pytest

from ledger import i18n
from ledger.config import Config
from ledger.ingest import Archive
from ledger.render import _esc, _nav_html, _page
from ledger.server import make_server

from .conftest import record_body_gate_census

_VAULT_KEY = "0123456789abcdef0123456789abcdef0123456789a="


def _server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    allow_contributions: bool = False,
    config: Config | None = None,
) -> Iterator[str]:
    monkeypatch.setenv("LEDGER_VAULT_KEY", _VAULT_KEY)
    archive = Archive.init(config or Config.default("RTL Archive", tmp_path / "arc"))
    httpd = make_server(archive, host="127.0.0.1", port=0, allow_contributions=allow_contributions)
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


@pytest.fixture
def contributing_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A server with the submission surface on, so the Contribute link is rendered.

    `make_server` defaults `allow_contributions=False`, which means the nav link this
    file most needs to check does not appear on the `base` fixture at all — a test
    asserting it is translated there would pass over a page that does not contain it.
    """
    yield from _server(tmp_path, monkeypatch, allow_contributions=True)


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
_INVARIANT_IN_THE_SHELL = {
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

#: The same set plus the words that are invariant everywhere but appear only in a
#: page *body*. Kept separate from `_INVARIANT_IN_THE_SHELL` above because
#: `test_the_widened_gate_is_not_vacuous` asserts every shell invariant really is
#: found in the shell, and a body-only entry would make that check pass over a
#: word the shell never renders -- the same "allowance for a string the page never
#: contained" the body gate is careful about one level down.
_INVARIANT_IN_EVERY_LANGUAGE = _INVARIANT_IN_THE_SHELL | {
    # A CLI subcommand `/proof` tells a steward to run. It is typed at a shell, so
    # it is the same characters in every language -- a translated `attest-health`
    # is an instruction that does not work.
    "attest-health",
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
    # \u2019 is the typographic apostrophe French uses ("d\u2019exemple"); spelled as an
    # escape because ruff's RUF001 rejects the literal character in source.
    return set(re.findall("[A-Za-z][A-Za-z'\u2019\\-]*", text))


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
    assert _visible_unwrapped_words(page) >= _INVARIANT_IN_THE_SHELL, (
        "the extractor found none of the invariants, so it is not reading the page"
    )


@pytest.mark.parametrize("lang", ["es", "fr", "ar"])
def test_the_shell_renders_no_english_prose_in_a_translated_page(
    contributing_base: str, lang: str
) -> None:
    """The same claim from the other end, against a real served page.

    The pseudolocale test above proves every shell string goes through the seam; this
    proves the catalogs actually answer for them, in the language a reader asked for.

    On `contributing_base` rather than `base`, and the link's presence is asserted
    first: with the submission surface off — `make_server`'s default — the Contribute
    link is not rendered at all, and every assertion below it about `"Contribute"`
    would hold over a page that never contained the word.
    """
    _status, body, _headers = _request(f"{contributing_base}/?lang={lang}")
    assert 'href="/contribute"' in body, "the Contribute link is absent; this proves nothing"
    for english in ("Contribute", "Governance", "How it works", "Reference implementation"):
        assert english not in body, f"{english!r} was served untranslated on a {lang} page"


# --- the same gate, one route further in: the safety-surface page bodies -----
#
# `/about`, `/governance` and `/how-it-works` are the plain-language pages an
# at-risk contributor is sent to for *who runs this archive* and *how it protects
# them* (user research P0-4, `config.Config`'s own comment on these fields). Every
# sentence on all three used to be an English literal in `server.py`, so a reader
# who asked for Arabic got the archive's safety promises in English under a
# correctly translated footer.
#
# The gate above could not see it: its scope selector is the page **shell**, and
# a shell rendered with `main_html=""` is blind to every page body by
# construction. That is the same hole `/status` sat in until #208, and this is the
# rest of #216.
#
# What may legitimately survive un-wrapped here is the steward-authored config
# text: `Config.about` and `Config.steward_vetting` are passed through as
# paragraphs, and they are a particular archive's own words, which this project
# cannot translate. The fixture sets each to a distinct nonsense token so the
# assertion can name exactly what is allowed to leak instead of subtracting a
# paragraph of English prose and hoping. `operators`, `contact` and
# `consent_response_time` are interpolated *into* seam strings, so they come back
# inside the wrapper and never reach this set at all.

#: config field -> the token this fixture puts in it.
_CONFIG_SENTINELS = {
    "about": "Zzqabouttext",
    "steward_vetting": "Zzqvettingtext",
    "operators": "Zzqoperatorstext",
    "contact": "Zzqcontacttext",
    "consent_response_time": "Zzqwindowtext",
}

#: route -> the sentinels that must appear un-wrapped in its `<main>`, verbatim.
#: A route with an empty tuple renders no steward-authored config text at all, so
#: its body must leak nothing.
#:
#: Widened 2026-09-13 from the three safety pages to every served HTML route whose
#: body the gate can already judge. `test_the_body_gate_says_how_many_routes_it_judges`
#: below is what keeps this honest: the three-route version of this dict was judging
#: **3 of 17** served HTML routes and nothing said so.
#: Widened again 2026-09-13 (#225) by `/proof` and `/transparency`, whose bodies
#: now go through the seam. **8 of 17 -> 10 of 17.** Both are *stateful* routes:
#: what this fixture reaches is one branch of several, so being in this dict is
#: necessary and not sufficient for them. `_STATEFUL_BODY_STATES` below judges
#: every branch either handler can render, and is what keeps "the route is
#: covered" from meaning "the branch a default fixture happens to reach is
#: covered" -- the same defect as #226's, one level further in.
_BODY_GATE_CONFIG_TEXT = {
    "/about": ("Zzqabouttext",),
    "/consent-status": (),
    "/governance": ("Zzqvettingtext",),
    "/how-it-works": (),
    "/overview": (),
    "/places": (),
    "/proof": (),
    "/status": (),
    "/timeline": (),
    "/transparency": (),
}

#: route -> how many seam-resolved strings its `<main>` must carry. A body that
#: rendered nothing would satisfy the leak assertion over an empty set.
_BODY_GATE_SEAM_STRINGS = {
    "/about": 3,
    "/consent-status": 4,
    "/governance": 3,
    "/how-it-works": 5,
    "/overview": 3,
    "/places": 3,
    # The branch `sentinel_base` reaches: no health attestation published. The
    # other branch is pinned in `_STATEFUL_BODY_STATES`.
    "/proof": 7,
    "/status": 4,
    "/timeline": 3,
    # Likewise: transparency unconfigured. Three further branches are pinned below.
    "/transparency": 3,
}

#: Routes in the server's GET tables that answer with something other than an HTML
#: page body — feeds, APIs and health. They are outside a prose gate by nature, and
#: `test_machine_feeds_do_not_vary_by_language` already holds them to not varying by
#: language at all. Named here so the census denominator cannot shrink by a page
#: being quietly reclassified: the census asserts this set against what the running
#: server actually serves.
_MACHINE_ROUTES = frozenset(
    {
        "/api/records",
        "/api/search",
        "/api/search.csv",
        "/feed.atom",
        "/healthz",
        "/oai",
        "/proof/attestation.json",
        "/robots.txt",
        "/sitemap.xml",
    }
)

#: Served HTML routes the body gate does NOT judge, each with the reason, measured
#: on 2026-09-13 against this fixture. Self-limiting in both directions, which is
#: the whole point: `test_every_route_named_unjudged_still_needs_to_be`
#: re-runs the gate's own predicate over each one and fails when an entry stops
#: being necessary, and the census fails on an HTML route that is in neither this
#: dict nor `_BODY_GATE_CONFIG_TEXT`. An exemption has to earn its place on every
#: run, or it is a hole with a comment over it.
#:
#: These are findings, not decisions. Each leak count is English prose reaching a
#: reader who asked for es, fr or ar.
_BODY_GATE_UNJUDGED = {
    "/": (
        "3 un-seamed words: the `<h1>` is the literal 'Browse the archive'. The "
        "rest of the body is seam-routed, so an ar reader gets an English heading "
        "over an Arabic page. Issue #225."
    ),
    "/search": (
        "3 un-seamed words — the same literal `<h1>` as `/`, from the same handler. Issue #225."
    ),
}

#: Served HTML routes this fixture cannot reach, so the gate cannot judge them
#: here. All five answer the shared 404 body, which itself carries 19 un-seamed
#: English words — that body is a surface no route-level gate covers either.
#: Self-limiting: the test below fails if any of these starts answering 200 on this
#: fixture, because then it is judgeable and belongs above.
_BODY_GATE_UNREACHABLE_IN_FIXTURE = {
    "/contribute": "needs allow_contributions=True, which this fixture does not set",
    "/edit": "needs an existing record and an edit token",
    "/steward": "needs steward credentials",
    "/steward/audit": "needs steward credentials",
    "/withdraw": "needs an existing record",
}


@pytest.fixture
def sentinel_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A server whose steward-authored config text is a set of unique tokens."""
    config = Config.default("RTL Archive", tmp_path / "arc")
    for name, token in _CONFIG_SENTINELS.items():
        setattr(config, name, token)
    yield from _server(tmp_path, monkeypatch, config=config)


def _main_section(page: str) -> str:
    """Just the `<main>` element: the shell is already gated by the tests above."""
    start = page.index('<main id="main"')
    return page[start : page.index("</main>", start)]


@pytest.mark.parametrize("path", sorted(_BODY_GATE_CONFIG_TEXT))
def test_no_prose_reaches_a_safety_page_body_without_going_through_the_seam(
    sentinel_base: str, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    _install_pseudolocale(monkeypatch)
    _status, page, _headers = _request(f"{sentinel_base}{path}?lang=en")
    main = _main_section(page)

    expected = set(_BODY_GATE_CONFIG_TEXT[path])
    words = _visible_unwrapped_words(main)
    assert expected <= words, (
        f"{path} does not carry the config text it is supposed to: expected "
        f"{sorted(expected)}, found {sorted(words)}. An absence assertion over a "
        "page that never contained the string proves nothing."
    )

    leaked = words - _INVARIANT_IN_EVERY_LANGUAGE - expected
    assert leaked == set(), (
        f"prose reached {path} without going through i18n.t: {sorted(leaked)}. "
        "Route it through the gettext seam. Steward-authored config text is the "
        "only thing this project may not translate, and it is named above."
    )


@pytest.mark.parametrize("path", sorted(_BODY_GATE_SEAM_STRINGS))
def test_the_safety_page_gate_is_looking_at_a_body_with_text_in_it(
    sentinel_base: str, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    """A body that rendered nothing would pass the assertion above over an empty set."""
    _install_pseudolocale(monkeypatch)
    _status, page, _headers = _request(f"{sentinel_base}{path}?lang=en")
    main = _main_section(page)

    expected = _BODY_GATE_SEAM_STRINGS[path]
    assert main.count(i18n.PSEUDO_PREFIX) == expected, (
        f"{path} rendered {main.count(i18n.PSEUDO_PREFIX)} seam-resolved strings in "
        f"<main>, expected {expected}"
    )


#: route -> (heading key, one body key), both of which must be answered by the
#: catalog a reader asked for rather than falling back to the English source.
_SAFETY_PAGE_KEYS = {
    "/about": ("about_heading", "about_operators"),
    "/governance": ("nav_governance", "governance_steward_powers"),
    "/how-it-works": ("how_it_works_heading", "how_it_works_control"),
}


@pytest.mark.parametrize("path", sorted(_SAFETY_PAGE_KEYS))
@pytest.mark.parametrize("lang", ["es", "fr", "ar"])
def test_a_safety_page_is_answered_by_the_catalog_the_reader_asked_for(
    sentinel_base: str, lang: str, path: str
) -> None:
    """The claim from the other end, against a real served page.

    The pseudolocale tests prove every sentence goes through the seam; this proves
    the catalogs answer for them. Both halves are needed: a msgid with no entry in
    a catalog resolves to its English source through gettext's `fallback=True`, so
    a page can be fully seam-routed and still entirely English.
    """
    _status, body, _headers = _request(f"{sentinel_base}{path}?lang={lang}")
    heading_key, body_key = _SAFETY_PAGE_KEYS[path]
    for key in (heading_key, body_key):
        translated = i18n.t(
            lang, key, archive="RTL Archive", operators=_CONFIG_SENTINELS["operators"]
        )
        english = i18n.t("en", key, archive="RTL Archive", operators=_CONFIG_SENTINELS["operators"])
        assert translated != english, f"{key!r} is not translated in the {lang} catalog"
        assert _esc(translated) in body, f"{key!r} was not served on the {lang} {path} page"
        assert _esc(english) not in body, f"{key!r} was served in English on a {lang} page"


# --- the census: how much of the site does the body gate actually judge? -----


def _served_html_routes(base: str) -> set[str]:
    """The routes in the server's own GET tables that answer with an HTML body.

    Read from the handler tables rather than listed here, so a page added to the
    server lands in this census on the day it is routed rather than on the day
    somebody remembers to add it to a test.
    """
    from ledger.server import ArchiveRequestHandler

    routed = set(ArchiveRequestHandler._GET_PAGES) | set(ArchiveRequestHandler._GET_QUERY_PAGES)
    html = set()
    for route in routed:
        _status, body, headers = _request(f"{base}{route}?lang=en")
        if "text/html" in headers.get("Content-Type", "") and '<main id="main"' in body:
            html.add(route)
    return html


def test_the_body_gate_says_how_many_routes_it_judges(sentinel_base: str) -> None:
    """Every served HTML route is judged, or named as unjudged with a reason.

    The gate this file added in #221 was a hand-written set of three routes. The
    server serves seventeen HTML pages. Nothing in the suite compared those two
    numbers, so `/proof` and `/transparency` -- the pages an at-risk contributor
    reads before deciding whether to hand this archive their material -- sat
    outside it, fully English in every locale, with every required check green.

    This is not a relaxation of the rule. It is the denominator the rule was
    missing: an HTML route that is neither judged nor named here fails, so the
    next page cannot join them silently.
    """
    served = _served_html_routes(sentinel_base)
    judged = set(_BODY_GATE_CONFIG_TEXT)
    named = set(_BODY_GATE_UNJUDGED) | set(_BODY_GATE_UNREACHABLE_IN_FIXTURE)

    # Recorded, not printed. `print` here would go into the fixture's
    # `redirect_stdout` sink and then into pytest's capture, so the census would
    # exist and nobody would ever see it -- which is the defect this test is about,
    # one level up. `pytest_terminal_summary` in tests/conftest.py reads this and
    # puts the two numbers in every run's summary, pass or fail.
    record_body_gate_census(
        judged=len(judged),
        served=len(served),
        leaking=len(_BODY_GATE_UNJUDGED),
        unreachable=len(_BODY_GATE_UNREACHABLE_IN_FIXTURE),
        branches=len(_STATEFUL_BODY_STATES),
        stateful=len(_STATEFUL_ROUTE_SOURCES),
    )

    assert judged <= served, f"judged routes the server does not serve: {sorted(judged - served)}"
    assert named <= served, f"named routes the server does not serve: {sorted(named - served)}"
    assert judged.isdisjoint(named), f"a route both judged and excused: {sorted(judged & named)}"
    assert served - judged - named == set(), (
        f"served HTML route(s) this gate neither judges nor accounts for: "
        f"{sorted(served - judged - named)}. Add the route to _BODY_GATE_CONFIG_TEXT "
        "and _BODY_GATE_SEAM_STRINGS if its body is seam-routed, or to "
        "_BODY_GATE_UNJUDGED with what it leaks and the issue that will close it."
    )
    assert set(_BODY_GATE_CONFIG_TEXT) == set(_BODY_GATE_SEAM_STRINGS), (
        "a judged route must declare both its allowed config text and its seam count"
    )


def test_the_machine_route_set_is_what_the_server_actually_serves(sentinel_base: str) -> None:
    """Guard the census's denominator.

    Every route the census subtracts as "not a prose surface" is listed by hand in
    `_MACHINE_ROUTES`. If a page were added to that list by mistake, the census
    would shrink and report a better ratio over a smaller site -- a coverage number
    that improves by looking at less is the failure this file is about.
    """
    from ledger.server import ArchiveRequestHandler

    routed = set(ArchiveRequestHandler._GET_PAGES) | set(ArchiveRequestHandler._GET_QUERY_PAGES)
    assert routed >= _MACHINE_ROUTES, f"not routed at all: {sorted(_MACHINE_ROUTES - routed)}"
    assert routed - _MACHINE_ROUTES == _served_html_routes(sentinel_base), (
        "_MACHINE_ROUTES no longer partitions the GET tables into feeds and pages"
    )


@pytest.mark.parametrize("path", sorted(_BODY_GATE_UNJUDGED))
def test_every_route_named_unjudged_still_needs_to_be(
    sentinel_base: str, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    """Self-limiting exemptions: an entry that has stopped being needed fails.

    Deliberately not an assertion that the page is broken -- it is an assertion
    that the excuse is still load-bearing. The day `/proof` is routed through the
    seam, this fails and whoever did the work moves it into the judged set, which
    is where the gate then holds it forever.
    """
    _install_pseudolocale(monkeypatch)
    status, page, _headers = _request(f"{sentinel_base}{path}?lang=en")
    assert status == 200, f"{path} no longer answers 200 on this fixture"
    leaked = (
        _visible_unwrapped_words(_main_section(page))
        - _INVARIANT_IN_EVERY_LANGUAGE
        - set(_CONFIG_SENTINELS.values())
    )
    assert leaked, (
        f"{path} no longer leaks un-seamed prose, so its entry in "
        "_BODY_GATE_UNJUDGED is obsolete. Delete it and add the route to "
        "_BODY_GATE_CONFIG_TEXT and _BODY_GATE_SEAM_STRINGS."
    )


@pytest.mark.parametrize("path", sorted(_BODY_GATE_UNREACHABLE_IN_FIXTURE))
def test_every_route_named_unreachable_still_is(sentinel_base: str, path: str) -> None:
    """The other half of the same rule.

    A route excused as unreachable that starts answering 200 here is judgeable,
    and leaving it excused would hide it behind a stale reason.
    """
    status, _page, _headers = _request(f"{sentinel_base}{path}?lang=en")
    assert status != 200, (
        f"{path} now answers 200 on this fixture, so the gate can judge it. Delete "
        "its _BODY_GATE_UNREACHABLE_IN_FIXTURE entry and judge it."
    )


# --- the second denominator: branches, not just routes ----------------------
#
# #226 moved this gate from 3 of 17 routes to 8 of 17 and named the rest. Its own
# measurement was taken on one fixture, and for the eight routes it judged that
# was enough -- each renders a single body. `/proof` and `/transparency` do not.
# `_handle_proof` branches on whether a health attestation has been published;
# `/transparency` has four distinct bodies (unconfigured, log unreadable,
# configured but never attested, attested). A gate that renders the default
# fixture and calls the route covered would have judged 2 of those 6 branches and
# reported both routes green -- the same shape of blindness as judging 3 of 17
# routes, one level further in.
#
# So these two routes are judged per *state*, and the states are checked for
# completeness against the handlers' own source: every `i18n` key either handler
# resolves has to be reached by at least one state below.


@dataclass(frozen=True)
class _BodyState:
    """One branch a stateful route can render, and what the gate expects of it.

    `invariant` is the un-seamed visible text this branch may still render, and it
    is asserted *present* before it is subtracted -- an allowance for a string the
    page never contained would quietly widen the gate.
    """

    seam_strings: int
    invariant: frozenset[str]
    why_invariant: str


#: The fixture's own attestation values, as recognizable tokens. Same discipline
#: as `_CONFIG_SENTINELS`: the gate names exactly what may survive un-wrapped
#: rather than subtracting real-looking prose and hoping.
_ATTESTATION_SENTINELS = {
    "chain_head": "beefcafe" * 8,
    "statement": "Zzqstatementtext",
    "attested_by": "Zzqattestedbytext",
    "counsel_note": "Zzqcounselnotetext",
}

#: The demand type the attested `/transparency` fixture records. A real member of
#: `transparency.DEMAND_TYPES` -- the handler renders the vocabulary key itself,
#: and the point of the state is that it is rendered untranslated on purpose.
_FIXTURE_DEMAND_TYPE = "national_security_letter"

#: How each state sets its route up. Read by `_stateful_server`; keeping it data
#: rather than a chain of `if state ==` is what makes adding the state for a new
#: branch a two-line edit when `test_every_branch_of_a_stateful_route_is_reached_by_some_state`
#: demands one.
#: `/proof`'s attested body renders two source paths and one JSON field name,
#: quoted so a reader can go and open them. They are identifiers, not prose: a
#: translated `chain_head_summary` names no field. Plus the fixture's own digest.
_PROOF_ATTESTED_INVARIANT = frozenset(
    {
        "proof",
        "attestation",
        "json",
        "docs",
        "VERIFYING-ATTESTATIONS",
        "md",
        "chain",
        "head",
        "summary",
        _ATTESTATION_SENTINELS["chain_head"],
    }
)


@dataclass(frozen=True)
class _Attest:
    """One transparency attestation for a fixture to publish.

    `attested_date` may be the literal `"TODAY"`, resolved at fixture time: a fixed
    recent date would drift into staleness and silently move the state from the
    fresh branch to the stale one as the calendar advances.
    """

    attested_date: str
    counsel_reviewed: bool
    counsel_review_note: str
    demand_counts: dict[str, int]


@dataclass(frozen=True)
class _StateSetup:
    """What a state needs set up before the server starts."""

    #: `/proof`: publish a health attestation with these properties. None = publish none.
    health: tuple[bool, bool] | None = None  # (fixity_ok, signed)
    #: `/transparency`: point the config at a log file at all.
    configured: bool = False
    #: `/transparency`: write a log file that is not a valid log.
    corrupt: bool = False
    #: `/transparency`: publish this attestation.
    attest: _Attest | None = None
    #: `/transparency`: then break the published log's digest chain.
    break_chain: bool = False


_STATE_SETUP: dict[tuple[str, str], _StateSetup] = {
    ("/proof", "no-attestation-published"): _StateSetup(),
    ("/proof", "attested-healthy-and-signed"): _StateSetup(health=(True, True)),
    ("/proof", "attested-failed-and-unsigned"): _StateSetup(health=(False, False)),
    ("/transparency", "not-configured"): _StateSetup(),
    ("/transparency", "log-unreadable"): _StateSetup(configured=True, corrupt=True),
    ("/transparency", "never-attested"): _StateSetup(configured=True),
    ("/transparency", "attested"): _StateSetup(
        configured=True,
        attest=_Attest(
            attested_date="TODAY",
            counsel_reviewed=True,
            counsel_review_note=_ATTESTATION_SENTINELS["counsel_note"],
            demand_counts={_FIXTURE_DEMAND_TYPE: 0},
        ),
    ),
    ("/transparency", "attested-stale-uncounselled-no-demands"): _StateSetup(
        configured=True,
        attest=_Attest(
            attested_date="2020-01-01",
            counsel_reviewed=False,
            counsel_review_note="",
            demand_counts={},
        ),
    ),
    ("/transparency", "attested-future-dated-broken-chain"): _StateSetup(
        configured=True,
        attest=_Attest(
            attested_date="2099-01-01",
            counsel_reviewed=True,
            counsel_review_note=_ATTESTATION_SENTINELS["counsel_note"],
            demand_counts={_FIXTURE_DEMAND_TYPE: 1},
        ),
        break_chain=True,
    ),
}

#: The canary's own words and the counsel note, which this project must not
#: restate in another language, plus the demand-type vocabulary key split into
#: words by the extractor.
_TRANSPARENCY_ATTESTED_INVARIANT = frozenset(
    {
        _ATTESTATION_SENTINELS["statement"],
        "national",
        "security",
        "letter",
    }
)

_STATEFUL_BODY_STATES: dict[tuple[str, str], _BodyState] = {
    ("/proof", "no-attestation-published"): _BodyState(
        seam_strings=7,
        invariant=frozenset(),
        why_invariant="nothing: this branch renders no data and no identifier.",
    ),
    ("/proof", "attested-healthy-and-signed"): _BodyState(
        seam_strings=12,
        invariant=_PROOF_ATTESTED_INVARIANT,
        why_invariant=(
            "/proof/attestation.json, docs/VERIFYING-ATTESTATIONS.md, the "
            "chain_head_summary field name, and the fixture's chain-head digest."
        ),
    ),
    ("/proof", "attested-failed-and-unsigned"): _BodyState(
        seam_strings=12,
        invariant=_PROOF_ATTESTED_INVARIANT,
        why_invariant="the same identifiers and digest as the healthy branch.",
    ),
    ("/transparency", "not-configured"): _BodyState(
        seam_strings=3,
        invariant=frozenset(),
        why_invariant="nothing: the archive has published no statement to reproduce.",
    ),
    ("/transparency", "log-unreadable"): _BodyState(
        seam_strings=3,
        invariant=frozenset(),
        why_invariant="nothing: the log could not be read, so nothing of its is shown.",
    ),
    ("/transparency", "never-attested"): _BodyState(
        seam_strings=3,
        invariant=frozenset(),
        why_invariant="nothing: there is no first attestation to reproduce yet.",
    ),
    ("/transparency", "attested"): _BodyState(
        seam_strings=17,
        invariant=_TRANSPARENCY_ATTESTED_INVARIANT | {_ATTESTATION_SENTINELS["counsel_note"]},
        why_invariant=(
            "the canary statement and the counsel note — a legal instrument this "
            "project must not restate — and the demand-type vocabulary key."
        ),
    ),
    ("/transparency", "attested-stale-uncounselled-no-demands"): _BodyState(
        # 14, not 17: this branch drops the counsel note and the whole demand
        # table (caption + two column headings + the "not translated" note) and
        # gains the "no demands" line and the second counsel-warning paragraph.
        seam_strings=14,
        invariant=frozenset({_ATTESTATION_SENTINELS["statement"]}),
        why_invariant="the canary statement. No counsel note and no demand table here.",
    ),
    ("/transparency", "attested-future-dated-broken-chain"): _BodyState(
        seam_strings=17,
        invariant=_TRANSPARENCY_ATTESTED_INVARIANT | {_ATTESTATION_SENTINELS["counsel_note"]},
        why_invariant="as the attested state; this one differs only in date and chain.",
    ),
}


def _publish_health_attestation(archive: Archive, *, fixity_ok: bool, signed: bool) -> None:
    """Put `/proof` into one of its attested branches."""
    from ledger.attestation import HealthAttestation, publish_attestation

    publish_attestation(
        archive,
        HealthAttestation(
            schema_version=1,
            archive_name="RTL Archive",
            generated_at="2026-09-01T00:00:00Z",
            software_version="0.1.0",
            fixity_ok=fixity_ok,
            chain_head_summary=_ATTESTATION_SENTINELS["chain_head"],
            # "ssh" is the only format `HealthAttestation.from_json` accepts, so it
            # is the only signed state a served page can ever be in — which is why
            # `_handle_proof` has two signature branches and not three.
            signature="Zzqsignaturevalue" if signed else None,
            signature_format="ssh" if signed else None,
        ),
    )


def _write_transparency_log(log_path: Path, spec: _Attest, *, break_chain: bool) -> None:
    """Append one attestation, optionally breaking the chain afterwards."""
    import json as _json
    from datetime import UTC, datetime

    from ledger import transparency

    attested_date = spec.attested_date
    if attested_date == "TODAY":
        attested_date = datetime.now(UTC).strftime("%Y-%m-%d")
    transparency.TransparencyLog(log_path).append(
        attested_date=attested_date,
        attested_by=_ATTESTATION_SENTINELS["attested_by"],
        statement_text=_ATTESTATION_SENTINELS["statement"],
        demand_counts=spec.demand_counts,
        counsel_reviewed=spec.counsel_reviewed,
        counsel_review_note=spec.counsel_review_note,
    )
    if break_chain:
        # A first entry must chain to "". Point it at a real-looking digest instead:
        # still a valid SHA-256 hex string, so the log parses, and `verify_chain`
        # returns False, which is the branch under test.
        entries = _json.loads(log_path.read_text(encoding="utf-8"))
        entries[0]["prev_digest"] = "0" * 64
        log_path.write_text(_json.dumps(entries, indent=2), encoding="utf-8")


@contextmanager
def _stateful_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, route: str, state: str
) -> Iterator[str]:
    """A server holding `route` in exactly one of the branches it can render.

    Each state gets its **own** root under `tmp_path`. They shared one while this
    was being written, and the states then contaminated each other: the attested
    `/proof` fixture published into the archive the "no attestation" state was
    about to read, so that state silently judged the wrong branch and
    `proof_not_attested` showed up as an unreached key.
    """
    setup = _STATE_SETUP[route, state]
    root = tmp_path / f"{route.strip('/').replace('/', '-')}--{state}"
    root.mkdir(parents=True, exist_ok=True)

    config = Config.default("RTL Archive", root / "arc")
    for name, token in _CONFIG_SENTINELS.items():
        setattr(config, name, token)
    log_path = root / "transparency.json"
    if setup.configured:
        config.transparency_log_path = str(log_path)

    monkeypatch.setenv("LEDGER_VAULT_KEY", _VAULT_KEY)
    archive = Archive.init(config)
    if setup.health is not None:
        fixity_ok, signed = setup.health
        _publish_health_attestation(archive, fixity_ok=fixity_ok, signed=signed)
    if setup.corrupt:
        log_path.write_text("this is not a transparency log\n", encoding="utf-8")
    if setup.attest is not None:
        _write_transparency_log(log_path, setup.attest, break_chain=setup.break_chain)

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


def _install_recording_pseudolocale() -> tuple[
    list[tuple[str, dict[str, object]]], Callable[[], None]
]:
    """Pseudolocalize as usual, and record every `(key, kwargs)` resolved.

    Recording the key rather than matching the rendered text back to a msgid is
    what makes the completeness check below exact: a msgid with placeholders in it
    does not appear on the page in a form any substring search can find.

    Returns its own `restore` rather than going through `monkeypatch`, because the
    callers need the seam back *inside* a running server fixture and
    `monkeypatch.undo()` would also drop the `LEDGER_VAULT_KEY` that fixture set.
    """
    calls: list[tuple[str, dict[str, object]]] = []
    real_t = i18n.t
    real_gloss = i18n.gloss_cw

    def recording_t(lang: str, key: str, /, **kw: object) -> str:
        calls.append((key, dict(kw)))
        return i18n.pseudolocalize(real_t(lang, key, **kw))

    def restore() -> None:
        i18n.t = real_t
        i18n.gloss_cw = real_gloss

    def pseudo_gloss(lang: str, tag: str) -> str:
        return i18n.pseudolocalize(real_gloss(lang, tag))

    # type: ignore[assignment] — rebinding a module-level function, which is what
    # the pseudolocale seam has always done here; mypy cannot narrow a module
    # attribute to a compatible callable. `restore` puts the originals back.
    i18n.t = recording_t  # type: ignore[assignment]  # see the note above
    i18n.gloss_cw = pseudo_gloss  # type: ignore[assignment]  # see the note above
    return calls, restore


#: route -> the source functions that build its `<main>`. Read by AST so the
#: declared-key set comes from the handlers themselves, not from a list here that
#: somebody has to remember to update.
_STATEFUL_ROUTE_SOURCES = {
    "/proof": (("ledger.server", "_handle_proof"),),
    "/transparency": (
        ("ledger.server", "_handle_transparency"),
        ("ledger.render", "transparency_main_html"),
        ("ledger.render", "transparency_unattested_main_html"),
    ),
}


def _declared_seam_keys(route: str) -> set[str]:
    """Every `i18n` message key named as a literal inside `route`'s handlers.

    Matched against the seam's own key set rather than by looking for `i18n.t(`
    call sites, because two of `_handle_proof`'s keys are chosen into a variable
    first (`health_key`, `signature_key`) and a call-site scan would miss exactly
    the branches most worth checking.
    """
    import ast
    import importlib
    import inspect

    known = set(i18n._messages(i18n.get_translation("en")))
    found: set[str] = set()
    for module_name, func_name in _STATEFUL_ROUTE_SOURCES[route]:
        module = importlib.import_module(module_name)
        source = inspect.getsource(module)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == func_name:
                found |= {
                    child.value
                    for child in ast.walk(node)
                    if isinstance(child, ast.Constant)
                    and isinstance(child.value, str)
                    and child.value in known
                }
    return found


@pytest.mark.parametrize(("route", "state"), sorted(_STATEFUL_BODY_STATES))
def test_no_prose_reaches_a_stateful_route_body_in_any_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, route: str, state: str
) -> None:
    """Every branch of `/proof` and `/transparency`, not just the one a fixture hits."""
    expected = _STATEFUL_BODY_STATES[route, state]
    _install_pseudolocale(monkeypatch)
    with _stateful_server(tmp_path, monkeypatch, route, state) as base:
        status, page, _headers = _request(f"{base}{route}?lang=en")
    assert status == 200, f"{route} [{state}] did not answer 200"
    main = _main_section(page)
    words = _visible_unwrapped_words(main)

    assert expected.invariant <= words, (
        f"{route} [{state}] does not render the un-seamed text its entry allows: "
        f"missing {sorted(expected.invariant - words)}. An allowance for a string "
        "the page never contained silently widens this gate."
    )
    leaked = words - _INVARIANT_IN_EVERY_LANGUAGE - expected.invariant
    assert leaked == set(), (
        f"prose reached {route} [{state}] without going through i18n.t: "
        f"{sorted(leaked)}. Route it through the gettext seam, or — if it is an "
        "identifier, a command, or text this project must not restate — add it to "
        f"that state's `invariant` with a reason. Allowed here today: "
        f"{expected.why_invariant}"
    )


@pytest.mark.parametrize(("route", "state"), sorted(_STATEFUL_BODY_STATES))
def test_each_stateful_body_really_is_the_branch_it_claims(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, route: str, state: str
) -> None:
    """A branch that did not render would satisfy the leak assertion over nothing.

    This is what caught the fixture bug while this gate was being written: an
    attestation whose `signature_format` was not `"ssh"` is rejected by
    `HealthAttestation.from_json`, so `/proof` fell back to its *unattested* body
    and the "attested" state was judging the wrong branch with everything green.
    """
    expected = _STATEFUL_BODY_STATES[route, state]
    _install_pseudolocale(monkeypatch)
    with _stateful_server(tmp_path, monkeypatch, route, state) as base:
        _status, page, _headers = _request(f"{base}{route}?lang=en")
    main = _main_section(page)
    assert main.count(i18n.PSEUDO_PREFIX) == expected.seam_strings, (
        f"{route} [{state}] rendered {main.count(i18n.PSEUDO_PREFIX)} seam-resolved "
        f"strings in <main>, expected {expected.seam_strings}. If the body changed "
        "on purpose, update the count; if it did not, the fixture is not putting "
        "this route in the state it says."
    )


@pytest.mark.parametrize(("route", "state"), sorted(_STATEFUL_BODY_STATES))
@pytest.mark.parametrize("lang", ["es", "fr", "ar"])
def test_a_stateful_route_is_answered_by_the_catalog_the_reader_asked_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, route: str, state: str, lang: str
) -> None:
    """The claim from the reader's end: the catalog answers, in their language.

    Seam-routing and translation are separate facts and the suite needs both.
    gettext's `fallback=True` means a msgid missing from a catalog resolves to its
    English source, so a body can be entirely seam-routed and entirely English —
    and a `msgstr` that is *verbatim* the English source satisfies every catalog
    gate there is (key parity, non-empty, placeholder parity). So this asserts the
    served text actually differs from the English, and consults
    `locales/identical_by_design.json` for the strings where being identical is
    the right answer rather than making "must differ" the rule.
    """
    declared = _declared_seam_keys(route)
    with _stateful_server(tmp_path, monkeypatch, route, state) as base:
        calls, restore = _install_recording_pseudolocale()
        try:
            # One pseudolocalized render to learn which keys this branch resolves,
            # and with what placeholder values.
            _request(f"{base}{route}?lang=en")
        finally:
            restore()
        _status, body, _headers = _request(f"{base}{route}?lang={lang}")

    rendered = [(key, kw) for key, kw in calls if key in declared]
    assert rendered, f"{route} [{state}] resolved none of its own seam keys"
    identical_by_design = _identical_by_design()
    for key, kw in rendered:
        english = i18n.t("en", key, **kw)
        translated = i18n.t(lang, key, **kw)
        if (lang, english) in identical_by_design:
            continue
        assert translated != english, (
            f"{key!r} is served verbatim English on a {lang} {route} page. Translate "
            f"it in src/ledger/locales/{lang}/LC_MESSAGES/messages.po, or — if it is "
            "genuinely the same string in this language — add it to "
            "src/ledger/locales/identical_by_design.json with a written reason."
        )
        assert _esc(translated) in body, f"{key!r} was not served on the {lang} {route} page"


def _identical_by_design() -> set[tuple[str, str]]:
    """`(locale, msgid)` pairs whose translation is legitimately the English source."""
    import json

    path = Path(i18n.__file__).resolve().parent / "locales" / "identical_by_design.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return {(row["locale"], row["msgid"]) for row in data["identical_by_design"]}


@pytest.mark.parametrize("route", sorted(_STATEFUL_ROUTE_SOURCES))
def test_every_branch_of_a_stateful_route_is_reached_by_some_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    """The states registry is complete against the handlers' own source.

    This is the self-limiting half. Add a branch to `_handle_proof` or to
    `transparency_main_html` with a new seam string in it and this fails until a
    state in `_STATEFUL_BODY_STATES` reaches it — so a new branch cannot join the
    two this PR is about, silently English, behind a route that is already marked
    judged.
    """
    declared = _declared_seam_keys(route)
    assert declared, f"no seam keys found in {route}'s handlers; the AST scan is broken"

    reached: set[str] = set()
    for a_route, state in sorted(_STATEFUL_BODY_STATES):
        if a_route != route:
            continue
        with _stateful_server(tmp_path, monkeypatch, route, state) as base:
            calls, restore = _install_recording_pseudolocale()
            try:
                _request(f"{base}{route}?lang=en")
            finally:
                restore()
        reached |= {key for key, _kw in calls}

    assert declared <= reached, (
        f"{route} resolves seam key(s) no state in _STATEFUL_BODY_STATES reaches: "
        f"{sorted(declared - reached)}. Add a state that renders that branch — an "
        "unreached branch is exactly how /proof and /transparency stayed English "
        "under a green gate."
    )
