"""Tests for the static accessibility gate (:mod:`ledger.accessibility_check`).

Three things matter here:

* a known-good, fully marked-up page passes ``check_html`` with no problems;
* a page that is missing structure (``lang``, a table ``<caption>``, an ``alt``)
  fails, and the problem messages are *clear* — they name the specific WCAG
  requirement and the source label, never page content (so a steward can act on
  them, and they cannot leak content — the no-outing rule applies to tooling too);
* ``check_dir`` run against the real bundled ``web/`` directory passes. This last
  one is the actual CI gate: if it ever fails, the shipped site has regressed.
"""

from __future__ import annotations

from pathlib import Path

from ledger.accessibility_check import (
    audit_css_contrast,
    check_dir,
    check_html,
    contrast_ratio,
)

# A minimal, fully accessible document: declared lang, non-empty title, exactly one
# h1, a main landmark, a skip link, a labelled input, and a captioned, scoped table.
_GOOD_HTML = """<!doctype html>
<html lang="en">
<head><title>Good page</title></head>
<body>
  <a class="skip-link" href="#main">Skip to main content</a>
  <main id="main" tabindex="-1">
    <h1>Records</h1>
    <form role="search">
      <label for="q">Search</label>
      <input id="q" name="q" type="search">
    </form>
    <table>
      <caption>All records, with titles and content-warning status.</caption>
      <thead>
        <tr><th scope="col">Title</th><th scope="col">Content warning</th></tr>
      </thead>
      <tbody>
        <tr><td>A record</td><td>No</td></tr>
      </tbody>
    </table>
    <img src="logo.png" alt="The community archive logo">
  </main>
</body>
</html>
"""

# A broken document: no lang on <html>, an <img> without alt, and a <table> with no
# <caption> and no <th scope>. Each of these is a distinct WCAG failure.
_BAD_HTML = """<!doctype html>
<html>
<head><title>Broken page</title></head>
<body>
  <main id="main">
    <h1>Records</h1>
    <img src="photo.jpg">
    <table>
      <tr><td>A record</td></tr>
    </table>
  </main>
</body>
</html>
"""


def test_known_good_html_passes() -> None:
    """A fully marked-up page produces no accessibility problems."""
    problems = check_html(_GOOD_HTML, label="good.html")
    assert problems == [], f"expected no problems, got: {problems}"


def test_missing_lang_alt_caption_fails_with_clear_messages() -> None:
    """A page missing lang, alt, and a table caption fails with named WCAG problems."""
    problems = check_html(_BAD_HTML, label="bad.html")
    assert problems, "expected the broken page to fail the accessibility check"
    joined = "\n".join(problems)

    # Every reported problem is labelled with its source.
    assert all(p.startswith("bad.html:") for p in problems)

    # Each specific failure is named clearly, with its WCAG reference.
    assert "lang attribute" in joined and "3.1.1" in joined
    assert "alt attribute" in joined and "1.1.1" in joined
    assert "<caption>" in joined and "1.3.1" in joined
    assert "th scope" in joined or "scope" in joined

    # The messages name structure only — never any page content (no-outing).
    assert "A record" not in joined


def test_missing_skip_link_and_main_fail() -> None:
    """A page lacking a skip link and a <main> landmark is flagged for both."""
    markup = (
        '<!doctype html><html lang="en"><head><title>T</title></head>'
        "<body><h1>Only a heading</h1></body></html>"
    )
    problems = check_html(markup, label="bare.html")
    joined = "\n".join(problems)
    assert "skip-to-content link" in joined
    assert "<main> landmark" in joined


def test_unlabelled_input_and_positive_tabindex_fail() -> None:
    """An input with no associated label and a positive tabindex are both flagged."""
    markup = (
        '<!doctype html><html lang="en"><head><title>T</title></head>'
        '<body><a href="#main">Skip</a><main id="main"><h1>H</h1>'
        '<input id="q" type="text"><a href="/x" tabindex="3">link</a>'
        "</main></body></html>"
    )
    problems = check_html(markup, label="form.html")
    joined = "\n".join(problems)
    assert "<label for>" in joined or "associated <label" in joined
    assert "positive tabindex" in joined


def test_contrast_ratio_known_values() -> None:
    """Black on white is the maximum 21:1; identical colours are 1:1."""
    assert round(contrast_ratio("#000000", "#ffffff"), 1) == 21.0
    assert round(contrast_ratio("#777777", "#777777"), 1) == 1.0


def test_contrast_audit_passes_real_stylesheet() -> None:
    """Every colour pair in the shipped stylesheet meets WCAG AA (verified, not owed)."""
    css = (Path(__file__).resolve().parent.parent / "web" / "static" / "app.css").read_text()
    assert audit_css_contrast(css, label="app.css") == []


def test_contrast_audit_flags_a_failing_pair() -> None:
    """A low-contrast token is caught, so the gate enforces AA rather than trusting it."""
    bad = ":root{--ink:#bbbbbb;--bg:#ffffff;--muted:#cccccc;--surface:#ffffff;"
    bad += "--link:#bbbbbb;--link-visited:#bbbbbb;--accent:#bbbbbb;"
    bad += "--warn-ink:#bbbbbb;--warn-bg:#ffffff;--border:#eeeeee;}"
    problems = audit_css_contrast(bad, label="bad.css")
    assert any("below WCAG AA" in p for p in problems)


def test_check_dir_against_real_web_passes() -> None:
    """The bundled ``web/`` directory passes the gate — this is the real CI gate.

    Scans the actual shipped site (plus the server's rendered sample pages, which
    ``check_dir`` renders internally). If this regresses, the public surface has
    lost a WCAG-required structure and must be fixed before release.
    """
    web_root = Path(__file__).resolve().parent.parent / "web"
    assert web_root.is_dir(), f"web/ not found at {web_root}"
    problems = check_dir(web_root)
    assert problems == [], (
        "the bundled web/ surface must pass the accessibility gate:\n" + "\n".join(problems)
    )
