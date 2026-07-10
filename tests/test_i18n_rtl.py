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
from ledger.render import _nav_html, _page
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
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
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
