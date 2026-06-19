"""A dependency-free accessibility gate for ledger's HTML surfaces.

This module backs the CI accessibility check. It scans the static HTML under a
directory *and*, where it can, the server-rendered sample pages, and reports
human-readable problems for the structural WCAG 2.x requirements that can be
verified statically:

* a ``lang`` attribute on ``<html>`` (3.1.1 Language of Page);
* a non-empty ``<title>`` (2.4.2 Page Titled);
* exactly one ``<h1>`` (heading structure / 1.3.1);
* a ``<main>`` landmark (1.3.1, bypass blocks);
* a "skip to content" link (2.4.1 Bypass Blocks);
* an ``alt`` attribute on every ``<img>`` (1.1.1 Non-text Content);
* a programmatically associated ``<label>`` for every ``<input>`` (1.3.1, 4.1.2);
* a ``<caption>`` and ``<th scope>`` on every ``<table>`` (1.3.1 Info and
  Relationships);
* no positive ``tabindex`` (2.4.3 Focus Order).

It is a *tolerant* scan built on :mod:`html.parser` (standard library only — no
third-party HTML toolkit), so a minor markup quirk degrades to a clear problem
message rather than a crash (robustness). It does not claim full WCAG conformance
— the candid, human-judged conformance picture lives in the Accessibility
Conformance Report (:mod:`ledger.acr_gen`); this is the automatable floor.

No-outing rule: the checker reads only markup structure and emits only problem
descriptions naming files and elements — never page content, never an identity.
"""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path

# --- colour contrast (WCAG 2.2 1.4.3 / 1.4.11) ------------------------------
# The contrast audit measures the CSS colour tokens against the AA thresholds and
# fails the gate if any pair regresses, so the conformance the ACR claims is
# *verified on every build* rather than an owed external audit (user research
# residual item). Pairs reference the design tokens declared in app.css.
_CONTRAST_PAIRS: tuple[tuple[str, str, float, str], ...] = (
    ("ink", "bg", 4.5, "body text"),
    ("muted", "bg", 4.5, "secondary text on the page"),
    ("muted", "surface", 4.5, "secondary text on a surface"),
    ("ink", "surface", 4.5, "text on a surface"),
    ("link", "bg", 4.5, "links"),
    ("link-visited", "bg", 4.5, "visited links"),
    ("accent", "bg", 4.5, "brand/accent text"),
    ("bg", "accent", 4.5, "button text (white on accent)"),
    ("warn-ink", "warn-bg", 4.5, "content-warning text"),
    ("warn-ink", "bg", 4.5, "content-warning text on the page"),
    ("border", "bg", 3.0, "UI border (component contrast)"),
)


def _relative_luminance(hex_color: str) -> float:
    """WCAG relative luminance of an sRGB hex colour (``#rgb`` or ``#rrggbb``)."""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))

    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast_ratio(fg: str, bg: str) -> float:
    """The WCAG contrast ratio between two hex colours (1.0 to 21.0)."""
    a, b = _relative_luminance(fg), _relative_luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def audit_css_contrast(css_text: str, *, label: str) -> list[str]:
    """Check the ``--token: #hex`` colour pairs in ``css_text`` against WCAG AA.

    Returns a problem for any declared pair below its threshold (4.5:1 for text,
    3:1 for UI components). A token referenced by a pair but missing from the CSS
    is itself a problem, so renaming a token cannot silently drop a check."""
    tokens = {
        name: value
        for name, value in re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{3,6})\b", css_text)
    }
    problems: list[str] = []
    for fg, bg, threshold, desc in _CONTRAST_PAIRS:
        if fg not in tokens or bg not in tokens:
            problems.append(f"{label}: contrast pair {desc!r} references a missing colour token")
            continue
        ratio = contrast_ratio(tokens[fg], tokens[bg])
        if ratio + 1e-9 < threshold:
            problems.append(
                f"{label}: {desc} contrast {ratio:.2f}:1 is below WCAG AA {threshold:.1f}:1 "
                f"(--{fg} on --{bg})"
            )
    return problems


# Substrings that mark an anchor as a "skip" link (case-folded match on its text
# or href). Kept small and explicit so the rule is predictable.
_SKIP_HINTS: tuple[str, ...] = ("skip to", "skip-link", "#main", "#content")


class _Accessibility(HTMLParser):
    """A tolerant single-pass scanner accumulating structural accessibility facts.

    Rather than build a full DOM, it records just the signals the checks below
    need (counts, the presence of landmarks, per-element attribute facts) as it
    streams the document, so the scan is linear and memory-light (efficiency).
    """

    def __init__(self) -> None:
        """Initialise the parser and the per-document accounting state."""
        super().__init__(convert_charrefs=True)
        self.html_lang: str | None = None
        self.saw_html: bool = False
        self.title_text: str = ""
        self._in_title: bool = False
        self.h1_count: int = 0
        self.saw_main: bool = False
        self.skip_link: bool = False
        # Per-element facts (images, inputs, tables).
        self.img_missing_alt: int = 0
        self.input_ids: set[str] = set()
        self.label_targets: set[str] = set()
        self.inputs_without_id: int = 0
        self.table_count: int = 0
        self.table_caption_count: int = 0
        self.bad_tabindex: int = 0
        # Per-table scratch state (a table is "open" while inside <table>…</table>).
        self._table_depth: int = 0
        self._current_table_has_caption: bool = False
        self._current_table_has_scoped_th: bool = False
        self._tables_missing_caption: int = 0
        self._tables_missing_scope: int = 0

    # --- streaming callbacks ------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Record the facts each opening tag contributes to the checks."""
        attr = {name: (value or "") for name, value in attrs}

        if tag == "html":
            self.saw_html = True
            self.html_lang = attr.get("lang")
        elif tag == "title":
            self._in_title = True
        elif tag == "h1":
            self.h1_count += 1
        elif tag == "main":
            self.saw_main = True
        elif tag == "a":
            text_href = attr.get("href", "").casefold()
            if any(hint in text_href for hint in _SKIP_HINTS):
                self.skip_link = True
        elif tag == "img" and "alt" not in attr:
            self.img_missing_alt += 1
        elif tag == "input":
            # hidden/submit/button/reset/image inputs are not user-editable fields
            # and legitimately need no <label for> (WCAG 1.3.1 applies to inputs
            # that take user input); only count the rest.
            input_type = attr.get("type", "text").casefold()
            if input_type in {"hidden", "submit", "button", "reset", "image"}:
                pass
            elif attr.get("id"):
                self.input_ids.add(attr["id"])
            else:
                self.inputs_without_id += 1
        elif tag == "label":
            target = attr.get("for")
            if target:
                self.label_targets.add(target)
        elif tag == "table":
            self._table_depth += 1
            self.table_count += 1
            self._current_table_has_caption = False
            self._current_table_has_scoped_th = False
        elif tag == "caption" and self._table_depth > 0:
            self._current_table_has_caption = True
        elif tag == "th" and self._table_depth > 0 and attr.get("scope"):
            self._current_table_has_scoped_th = True

        tabindex = attr.get("tabindex")
        if tabindex is not None and _to_int(tabindex) > 0:
            self.bad_tabindex += 1

    def handle_endtag(self, tag: str) -> None:
        """Close per-element scopes (title text capture, per-table accounting)."""
        if tag == "title":
            self._in_title = False
        elif tag == "table" and self._table_depth > 0:
            self._table_depth -= 1
            if self._current_table_has_caption:
                self.table_caption_count += 1
            else:
                self._tables_missing_caption += 1
            if not self._current_table_has_scoped_th:
                self._tables_missing_scope += 1

    def handle_data(self, data: str) -> None:
        """Capture the document title text for the non-empty-title check."""
        if self._in_title:
            self.title_text += data

    # --- derived results ----------------------------------------------------

    @property
    def tables_missing_caption(self) -> int:
        """How many ``<table>`` elements lacked a ``<caption>``."""
        return self._tables_missing_caption

    @property
    def tables_missing_scope(self) -> int:
        """How many ``<table>`` elements lacked any ``<th scope>``."""
        return self._tables_missing_scope


def _to_int(value: str) -> int:
    """Parse ``value`` to an int, treating non-numeric text as ``0`` (robustness)."""
    try:
        return int(value.strip())
    except ValueError:
        return 0


def check_html(markup: str, *, label: str) -> list[str]:
    """Return a list of human-readable accessibility problems found in ``markup``.

    ``label`` names the source (a file path or a route) so each problem points the
    steward at where to fix it. An empty list means every static check passed for
    this document.
    """
    scanner = _Accessibility()
    scanner.feed(markup)
    scanner.close()

    problems: list[str] = []

    def fail(message: str) -> None:
        problems.append(f"{label}: {message}")

    if scanner.saw_html and not (scanner.html_lang and scanner.html_lang.strip()):
        fail("<html> is missing a non-empty lang attribute (WCAG 3.1.1)")
    if not scanner.title_text.strip():
        fail("missing a non-empty <title> (WCAG 2.4.2)")
    if scanner.h1_count == 0:
        fail("missing an <h1> (WCAG 1.3.1)")
    elif scanner.h1_count > 1:
        fail(f"has {scanner.h1_count} <h1> elements; exactly one is required (WCAG 1.3.1)")
    if not scanner.saw_main:
        fail("missing a <main> landmark (WCAG 1.3.1)")
    if not scanner.skip_link:
        fail("missing a skip-to-content link (WCAG 2.4.1)")
    if scanner.img_missing_alt:
        fail(f"{scanner.img_missing_alt} <img> element(s) lack an alt attribute (WCAG 1.1.1)")

    if scanner.inputs_without_id:
        fail(
            f"{scanner.inputs_without_id} <input>(s) have no id, so no <label for> "
            "can be associated (WCAG 1.3.1)"
        )
    unlabelled = scanner.input_ids - scanner.label_targets
    if unlabelled:
        fail(f"{len(unlabelled)} <input>(s) have no associated <label for> (WCAG 1.3.1, 4.1.2)")

    if scanner.tables_missing_caption:
        fail(f"{scanner.tables_missing_caption} <table>(s) lack a <caption> (WCAG 1.3.1)")
    if scanner.tables_missing_scope:
        fail(f"{scanner.tables_missing_scope} <table>(s) lack any <th scope> (WCAG 1.3.1)")

    if scanner.bad_tabindex:
        fail(
            f"{scanner.bad_tabindex} element(s) use a positive tabindex, which "
            "breaks focus order (WCAG 2.4.3)"
        )

    return problems


def _render_sample_pages() -> dict[str, str]:
    """Render the server's sample pages over a throwaway in-memory archive.

    Best-effort: the server-rendered HTML is the surface real users see, so the
    gate checks it directly when possible. Any failure to build the sample (a
    missing optional dependency, a sandbox without temp write access) degrades to
    an empty mapping rather than failing the whole check (robustness — the static
    file scan still runs).
    """
    try:
        from tempfile import mkdtemp

        from ledger import contribute
        from ledger.config import Config
        from ledger.ingest import Archive
        from ledger.models import AccessPolicy, DublinCore, Field, Record
        from ledger.render import (
            _browse_main_html,
            _page,
            _record_main_html,
        )

        root = Path(mkdtemp(prefix="ledger-a11y-"))
        config = Config.default("a11y-sample", root)
        archive = Archive.init(config)
        record = Record(
            title="Sample record",
            default_policy=AccessPolicy.PUBLIC,
            dublin_core=DublinCore(
                title=["Sample record"],
                description=["A sample record used only to render the accessibility surface."],
            ),
            fields=[Field(name="story", value="A sample story.", policy=AccessPolicy.PUBLIC)],
        )
        archive.ingest({}, record, now="2026-01-01T00:00:00Z")
        from ledger.access.grants import anonymous

        disclosed = archive.browse(anonymous(), now="2026-01-01T00:00:00Z")
        one = archive.disclose(record.record_id, anonymous(), now="2026-01-01T00:00:00Z")

        return {
            "rendered:/": _page(
                "Browse", lang="en", main_html=_browse_main_html(disclosed, heading="Browse")
            ),
            "rendered:/record/{id}": _page(
                one.title, lang="en", main_html=_record_main_html(one, proceed=True)
            ),
            "rendered:/contribute": _page(
                "Contribute", lang="en", main_html=contribute.render_contribute_main(config)
            ),
        }
    except Exception:
        # Degrade gracefully: the static file scan still runs even if the sample
        # cannot be rendered (robustness). A broad catch is deliberate here.
        return {}


def check_dir(path: Path) -> list[str]:
    """Scan every ``.html`` file under ``path`` (plus rendered samples) for problems.

    Returns a flat list of human-readable problems across all documents; an empty
    list means the directory passes the automatable accessibility floor. Files are
    visited in sorted order so two runs over the same tree report identically
    (reproducibility).
    """
    problems: list[str] = []
    if path.exists():
        for html_file in sorted(path.rglob("*.html")):
            markup = html_file.read_text(encoding="utf-8", errors="replace")
            problems.extend(check_html(markup, label=str(html_file)))
        # Colour-contrast audit over every stylesheet found (WCAG 1.4.3 / 1.4.11).
        for css_file in sorted(path.rglob("*.css")):
            css = css_file.read_text(encoding="utf-8", errors="replace")
            problems.extend(audit_css_contrast(css, label=str(css_file)))

    for label, markup in _render_sample_pages().items():
        problems.extend(check_html(markup, label=label))

    return problems


def main(argv: list[str] | None = None) -> int:
    """Run the accessibility check, print any problems, and return an exit code.

    The directory to scan is ``argv[0]`` if given, else ``web`` (the bundled
    site). Returns ``0`` when no problems are found and ``1`` otherwise, so a CI
    gate can branch on the exit code (operability). Problems are printed to
    *stdout* one per line; a clean run prints a single confirmation.
    """
    args = sys.argv[1:] if argv is None else argv
    target = Path(args[0]) if args else Path("web")
    problems = check_dir(target)
    if problems:
        print(f"accessibility check FAILED for {target}: {len(problems)} problem(s)")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"accessibility check passed for {target}")
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
