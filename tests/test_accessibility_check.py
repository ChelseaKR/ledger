"""Tests for the static accessibility gate (:mod:`ledger.accessibility_check`).

Four things matter here:

* a known-good, fully marked-up page passes ``check_html`` with no problems;
* a page that is missing structure (``lang``, a table ``<caption>``, an ``alt``)
  fails, and the problem messages are *clear* — they name the specific WCAG
  requirement and the source label, never page content (so a steward can act on
  them, and they cannot leak content — the no-outing rule applies to tooling too);
* ``check_dir`` run against the real bundled ``web/`` directory passes. This last
  one is the actual CI gate: if it ever fails, the shipped site has regressed.
* issue #122's fix holds: ``check_dir`` refuses to report a clean pass having
  examined zero HTML documents, ``_render_sample_pages`` degrades silently only
  for ``OSError`` (letting a genuine renderer defect fail loudly), and a pass
  states how many documents it checked and which.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from ledger.accessibility_check import (
    _render_sample_pages,
    audit_css_contrast,
    check_dir,
    check_html,
    contrast_ratio,
    main,
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


def test_contrast_audit_checks_a_dark_theme_override() -> None:
    """A dark-mode override that fails AA is caught — both themes are audited."""
    good_base = (
        ":root{--ink:#1a1a1a;--bg:#ffffff;--muted:#595959;--surface:#f4f4f6;"
        "--link:#0b5cab;--link-visited:#6a1b9a;--accent:#6a1b9a;--bg:#ffffff;"
        "--warn-ink:#7a1d1d;--warn-bg:#fff4f4;--border:#767676;"
        "--mark-ink:#1a1a1a;--mark-bg:#fce8b2;}"
    )
    # The dark override makes body text nearly invisible on the dark background.
    dark = "@media (prefers-color-scheme: dark){:root{--bg:#121212;--ink:#202020;}}"
    problems = audit_css_contrast(good_base + dark, label="themed.css")
    assert any("below WCAG AA" in p and "theme 1" in p for p in problems)
    # The default (light) theme is fine, so the only failures name the dark theme.
    assert all("theme 1" in p for p in problems if "below WCAG AA" in p)


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

    ``web/`` itself ships no static ``.html`` (confirmed empty by
    ``test_web_has_no_static_html_so_the_gate_rests_on_rendered_samples`` below), so
    a non-empty ``html_documents`` here is proof that the rendered samples are the
    thing actually carrying this gate, not an assumption about them.
    """
    web_root = Path(__file__).resolve().parent.parent / "web"
    assert web_root.is_dir(), f"web/ not found at {web_root}"
    report = check_dir(web_root)
    assert report.problems == [], (
        "the bundled web/ surface must pass the accessibility gate:\n" + "\n".join(report.problems)
    )
    assert report.html_documents, (
        "check_dir examined zero HTML documents -- a pass with nothing checked is "
        "not evidence the site is accessible (#122)"
    )


def test_web_has_no_static_html_so_the_gate_rests_on_rendered_samples() -> None:
    """The measured fact issue #122 is built on: ``web/`` ships no ``.html`` at all.

    This is *why* ``_render_sample_pages`` degrading silently was able to make the
    whole structural floor disappear without ``check_dir`` noticing: the static
    file scan alone has nothing to scan. If this test ever starts failing because
    ``web/`` gained real markup, that is good news, but it means the "rests
    entirely on rendered samples" framing below needs revisiting too.
    """
    web_root = Path(__file__).resolve().parent.parent / "web"
    assert list(web_root.rglob("*.html")) == []


def test_render_sample_pages_returns_the_nine_documented_routes() -> None:
    """The exact set of routes the static gate's rendered-sample coverage reaches.

    Pinned so a change here is a conscious, reviewed edit (e.g. adding coverage
    for one of the routes named in docs/accessibility/ROUTE-COVERAGE.md) rather
    than a silent drift that the coverage doc quietly stops matching.
    """
    assert set(_render_sample_pages()) == {
        "rendered:/",
        "rendered:/record/{id}",
        "rendered:/places",
        "rendered:/timeline",
        "rendered:/contribute",
        "rendered:/transparency",
        "rendered:/overview",
        "rendered:/withdraw",
        "rendered:/edit",
    }


def test_the_three_newly_covered_routes_pass_the_structural_check_individually() -> None:
    """``/overview``, ``/withdraw``, and ``/edit`` close three of the eleven gaps
    named in ``docs/accessibility/ROUTE-COVERAGE.md``, each by calling a pure
    render function ``server.py`` already calls unmodified (``_overview_main_html``,
    ``contribute.render_withdraw_main``, ``contribute.render_edit_main``) — no
    change to ``server.py`` was needed. Checked individually, not just as part of
    the aggregate ``web/`` pass, so a regression in one names itself precisely.
    """
    pages = _render_sample_pages()
    for route in ("rendered:/overview", "rendered:/withdraw", "rendered:/edit"):
        assert route in pages, f"{route} missing from _render_sample_pages()"
        assert check_html(pages[route], label=route) == []


def test_render_sample_pages_degrades_to_empty_on_oserror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sandbox with no writable temp directory degrades gracefully, as before.

    ``mkdtemp`` raising ``OSError`` is the named, legitimate case from #122: an
    environment fact, not a defect. This must keep returning ``{}`` without
    raising, so a genuinely sandboxed run does not crash merely for lacking a
    writable temp directory.
    """

    def _no_writable_tmp(*args: object, **kwargs: object) -> str:
        raise OSError("no writable temp directory in this sandbox")

    monkeypatch.setattr(tempfile, "mkdtemp", _no_writable_tmp)
    assert _render_sample_pages() == {}


def test_render_sample_pages_reraises_a_non_oserror_defect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A renderer that raises for its own reasons must fail loudly, not degrade.

    Simulates the exact failure #122 measured: substituting a renderer that
    raises (standing in for an import error, a ``render.py`` signature drift, or
    a real bug) used to be swallowed by the old bare ``except Exception`` and
    silently produce ``{}``. It must now propagate.
    """
    from ledger import contribute

    def _signature_drifted(*args: object, **kwargs: object) -> str:
        raise TypeError("render_contribute_main() signature drifted")

    monkeypatch.setattr(contribute, "render_contribute_main", _signature_drifted)
    with pytest.raises(TypeError, match="signature drifted"):
        _render_sample_pages()


def test_check_dir_fails_loudly_when_the_renderer_has_a_real_defect(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """End to end: a renderer defect must fail the whole gate, not pass silently.

    Before #122's fix, this exact scenario printed "accessibility check passed"
    and exited 0, having examined zero documents. It must now propagate the
    renderer's own exception instead of swallowing it into a false pass.
    """
    from ledger import contribute

    def _broken(*args: object, **kwargs: object) -> str:
        raise TypeError("render_contribute_main() signature drifted")

    monkeypatch.setattr(contribute, "render_contribute_main", _broken)
    with pytest.raises(TypeError, match="signature drifted"):
        check_dir(tmp_path)


def test_check_dir_reports_zero_documents_as_a_problem_not_a_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The core #122 regression test: zero documents examined must never look
    like "no problems found".

    ``tmp_path`` is empty (no static ``.html``) and the renderer is monkeypatched
    to return no samples at all (standing in for any degrade path, OSError
    included) — the exact "0 static files and a renderer that produced nothing"
    combination the issue measured against a real, un-monkeypatched ``web/``.
    """
    monkeypatch.setattr("ledger.accessibility_check._render_sample_pages", lambda: {})

    report = check_dir(tmp_path)

    assert report.html_documents == ()
    assert report.problems, "zero documents examined must be reported as a problem"
    assert any("no HTML documents were examined" in p for p in report.problems)


def test_check_dir_oserror_degrade_is_not_a_spurious_failure_when_static_files_exist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """OSError degrading the rendered samples must not fail a run that still has
    real static HTML to check — the "legitimately sandboxed" case must not become
    a spurious failure just because the render step could not run.
    """
    (tmp_path / "index.html").write_text(_GOOD_HTML, encoding="utf-8")

    def _no_writable_tmp(*args: object, **kwargs: object) -> str:
        raise OSError("no writable temp directory in this sandbox")

    monkeypatch.setattr(tempfile, "mkdtemp", _no_writable_tmp)

    report = check_dir(tmp_path)

    assert report.problems == []
    assert report.html_documents == (str(tmp_path / "index.html"),)


def test_main_reports_document_count_and_names_on_a_pass(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """A pass names how many documents it checked and which — the log is
    self-evidencing, not just a bare "passed" a reader has to take on faith.
    """
    (tmp_path / "index.html").write_text(_GOOD_HTML, encoding="utf-8")

    exit_code = main([str(tmp_path)])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "passed" in out
    # Nine rendered samples plus the one static file just written.
    assert "10 HTML document(s)" in out
    assert str(tmp_path / "index.html") in out
    assert "rendered:/contribute" in out


def test_main_fails_loudly_instead_of_a_false_pass_when_nothing_was_examined(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """The measured #122 bug, end to end through the CLI entry point: a renderer
    that produces nothing over an otherwise-empty directory must exit non-zero and
    say so, never print "accessibility check passed".
    """
    monkeypatch.setattr("ledger.accessibility_check._render_sample_pages", lambda: {})

    exit_code = main([str(tmp_path)])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "passed" not in out
    assert "FAILED" in out
    assert "no HTML documents were examined" in out
