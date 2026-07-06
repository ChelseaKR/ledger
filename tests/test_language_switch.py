"""Tests for the explicit language picker (backlog I2).

Language used to be chosen *only* from the browser's ``Accept-Language`` header,
which a reader on a shared or mislocalized machine cannot change. These tests pin the
follow-on: a visible picker that (1) renders the supported languages with the active
one marked ``aria-current`` and the others as ``?lang=`` links that keep the reader on
the current page, (2) honours an explicit ``?lang=`` and remembers it in a cookie, and
(3) falls back safely on an unknown value — never to a blank page. The cookie carries
only a UI language code, never an identity (no-outing rule).
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

from ledger.config import Config
from ledger.ingest import Archive
from ledger.render import _language_switch_html, _nav_html
from ledger.server import make_server

# --- unit: the pure picker markup ------------------------------------------


def test_switch_marks_active_language_and_links_the_others() -> None:
    """The current language is aria-current text; the alternative is a ?lang= link."""
    html = _language_switch_html("en", "/search?q=mutual+aid")
    # English is active: shown as non-link text a screen reader announces as current.
    assert '<span aria-current="true">English</span>' in html
    # Spanish is offered as a link that keeps the path and query, plus hreflang.
    assert 'href="/search?q=mutual+aid&amp;lang=es"' in html
    assert 'hreflang="es"' in html
    assert "Español" in html
    # The group is labelled for assistive tech.
    assert 'aria-label="Language"' in html


def test_switch_replaces_an_existing_lang_rather_than_stacking() -> None:
    """Switching from a ?lang= page drops the old value instead of appending."""
    html = _language_switch_html("es", "/?lang=es")
    # Now Spanish is active and English is the link — with a single lang param.
    assert '<span aria-current="true">Español</span>' in html
    assert 'href="/?lang=en"' in html
    # The old value is dropped, not stacked into a second query parameter.
    assert "&amp;lang=" not in html and "&lang=" not in html


def test_switch_label_is_localized() -> None:
    """The picker's group label is itself translated."""
    assert 'aria-label="Idioma"' in _language_switch_html("es", "/")


def test_nav_includes_the_language_switch() -> None:
    """The site nav embeds the picker so it appears on every page."""
    nav = _nav_html("en", contribute=False, current_path="/about")
    assert 'class="lang-switch"' in nav
    assert 'href="/about?lang=es"' in nav


# --- integration: the server honours the choice ----------------------------

_VAULT_KEY = "0123456789abcdef0123456789abcdef0123456789a="


def _server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setenv("LEDGER_VAULT_KEY", _VAULT_KEY)
    archive = Archive.init(Config.default("Lang Archive", tmp_path / "arc"))
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


def _request(
    url: str, *, accept_language: str | None = None, cookie: str | None = None
) -> tuple[int, str, dict[str, str]]:
    req = urllib.request.Request(url)  # noqa: S310 - loopback
    if accept_language is not None:
        req.add_header("Accept-Language", accept_language)
    if cookie is not None:
        req.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            return int(resp.status), resp.read().decode("utf-8"), dict(resp.headers)
    except urllib.error.HTTPError as exc:  # pragma: no cover - not expected here
        return int(exc.code), exc.read().decode("utf-8"), dict(exc.headers)


def test_explicit_lang_query_switches_and_sets_a_cookie(base: str) -> None:
    """``?lang=es`` renders Spanish and persists the choice in a lang cookie."""
    status, body, headers = _request(f"{base}/?lang=es", accept_language="en")
    assert status == 200
    assert "Explorar" in body  # nav_browse, localized to Spanish
    set_cookie = headers.get("Set-Cookie", "")
    assert "lang=es" in set_cookie
    assert "SameSite=Lax" in set_cookie and "HttpOnly" in set_cookie


def test_lang_cookie_is_honoured_without_a_query(base: str) -> None:
    """A remembered ``lang`` cookie selects Spanish even when the header says English."""
    status, body, _headers = _request(f"{base}/", accept_language="en", cookie="lang=es")
    assert status == 200
    assert "Explorar" in body


def test_query_overrides_cookie(base: str) -> None:
    """An explicit query beats a stale cookie, so the picker always wins."""
    _status, body, _headers = _request(f"{base}/?lang=en", cookie="lang=es")
    assert "Browse" in body  # back to English nav


def test_unknown_lang_falls_back_to_header_negotiation(base: str) -> None:
    """An unsupported ``?lang=`` is ignored, falling through to Accept-Language."""
    status, body, headers = _request(f"{base}/?lang=zz", accept_language="es")
    assert status == 200
    assert "Explorar" in body  # negotiated from the header, not the bad query
    # A rejected value is not written back as a remembered choice.
    assert "lang=" not in headers.get("Set-Cookie", "")


def test_switching_language_localizes_the_browse_chrome(base: str) -> None:
    """Choosing Spanish translates the browse UI itself, not just the nav (I2)."""
    _status, body, _headers = _request(f"{base}/?lang=es", accept_language="en")
    # The read-path chrome a switched-language reader sees is now Spanish.
    assert "Registros (vista de lista)" in body  # list-view heading
    assert "Registros (vista de tabla)" in body  # table-view heading
    assert "Buscar en el archivo" in body  # search label
    # And the English equivalents are gone on the Spanish page.
    assert "Records (list view)" not in body


# --- I18N-13 / G11: Content-Language + Vary on every negotiated response ----


def test_html_response_carries_content_language_and_vary(base: str) -> None:
    """A negotiated HTML page declares its language and varies on the header.

    Without ``Vary: Accept-Language`` a shared cache (browser, CDN, reverse proxy)
    could serve one reader's negotiated language to the next reader who hits the
    same URL with a different ``Accept-Language`` — a correctness bug, not just a
    cosmetic one, for any deployment sitting behind a cache.
    """
    _status, _body, headers = _request(f"{base}/?lang=es", accept_language="en")
    assert headers.get("Content-Language") == "es"
    assert headers.get("Vary") == "Accept-Language"


def test_json_response_carries_content_language_and_vary(base: str) -> None:
    """The JSON API is negotiated exactly like the HTML surface (same `_send` path)."""
    _status, _body, headers = _request(f"{base}/api/records?lang=en", accept_language="es")
    assert headers.get("Content-Language") == "en"
    assert headers.get("Vary") == "Accept-Language"


def test_feed_endpoints_are_not_marked_content_language(base: str) -> None:
    """Machine feeds are always the anonymous-public view; they never vary by language."""
    _status, _body, headers = _request(f"{base}/sitemap.xml")
    assert "Content-Language" not in headers
    assert "Vary" not in headers
