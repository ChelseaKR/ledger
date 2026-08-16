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

Examining zero documents is itself treated as a failure, not a vacuous pass
(:class:`AccessibilityReport`, :func:`check_dir`) — a structural check that ran
over nothing proves nothing, and must say so rather than print the same
"passed" a real, clean scan would (#122).
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
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
    ("mark-ink", "mark-bg", 4.5, "search match highlight"),
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


def _root_tokens(block_text: str) -> dict[str, str]:
    """The ``--token: #hex`` colour map declared in one ``:root { … }`` block."""
    return dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{3,6})\b", block_text))


def audit_css_contrast(css_text: str, *, label: str) -> list[str]:
    """Check the ``--token: #hex`` colour pairs in ``css_text`` against WCAG AA.

    Returns a problem for any declared pair below its threshold (4.5:1 for text,
    3:1 for UI components). A token referenced by a pair but missing from the base
    palette is itself a problem, so renaming a token cannot silently drop a check.

    Multiple ``:root`` blocks are each a *theme*: the first is the base palette and
    every later one (e.g. a ``@media (prefers-color-scheme: dark)`` override) is the
    base updated with its overrides. Every theme is audited, so a dark mode cannot
    ship a colour pair that fails AA — the gate covers what a reader can actually see,
    not just the default theme."""
    roots = re.findall(r":root\s*\{([^}]*)\}", css_text)
    base = _root_tokens(roots[0]) if roots else {}
    themes: list[tuple[str, dict[str, str]]] = [("default", base)]
    for index, block in enumerate(roots[1:], start=1):
        themes.append((f"theme {index}", {**base, **_root_tokens(block)}))

    problems: list[str] = []
    for fg, bg, threshold, desc in _CONTRAST_PAIRS:
        if fg not in base or bg not in base:
            problems.append(f"{label}: contrast pair {desc!r} references a missing colour token")
            continue
        for theme_name, tokens in themes:
            ratio = contrast_ratio(tokens[fg], tokens[bg])
            if ratio + 1e-9 < threshold:
                suffix = "" if theme_name == "default" else f" [{theme_name}]"
                problems.append(
                    f"{label}: {desc} contrast {ratio:.2f}:1 is below WCAG AA {threshold:.1f}:1 "
                    f"(--{fg} on --{bg}){suffix}"
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

    # Pre-existing complexity (many checks fan out from one dispatch point); surfaced
    # 2026-07-05 when CQ-05's complexity gate was enabled. Waived, not re-muted:
    # tracked for a follow-up split rather than refactored under audit time
    # pressure on safety-adjacent code. Tracked in issue #83.
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:  # noqa: C901 - one branch per element type the WCAG checks care about; splitting it would scatter the rule set (#83)
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


# Pre-existing complexity (one function surveys every WCAG structural check);
# surfaced 2026-07-05 when CQ-05's complexity gate was enabled. Waived, not
# re-muted: tracked for a follow-up split in issue #83.
def check_html(markup: str, *, label: str) -> list[str]:  # noqa: C901 - a flat sequence of independent WCAG checks that all report into one list (#83)
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

    The server-rendered HTML is the surface real users see, so the gate checks it
    directly when possible — and today it is the *only* source of structural
    coverage, since ``web/`` ships no static ``.html`` of its own (see
    :func:`check_dir`).

    Degrades to an empty mapping — the static file scan in :func:`check_dir`
    still runs, and :func:`check_dir` itself asserts that *something* was
    ultimately examined — only for :exc:`OSError`. A sandbox with no writable
    temp directory (:func:`tempfile.mkdtemp` raising) is a fact about the
    environment this happens to run in, not a defect in this code or the
    renderer it calls (robustness). Any other exception — a missing import, a
    ``render.py`` signature drift, a real bug reachable from this call path —
    means the renderer cannot render, which *is* a defect: it must propagate and
    fail the gate loudly rather than degrade into "0 pages, 0 problems, pass",
    which is the bug this function used to have (#122).
    """
    try:
        from tempfile import mkdtemp

        from ledger import contribute, i18n
        from ledger.config import Config
        from ledger.ingest import Archive
        from ledger.models import AccessPolicy, DublinCore, Field, Record
        from ledger.render import (
            _browse_main_html,
            _overview_main_html,
            _page,
            _places_html,
            _record_main_html,
            _timeline_html,
            transparency_main_html,
        )
        from ledger.transparency import TransparencyLog

        root = Path(mkdtemp(prefix="ledger-a11y-"))
        config = Config.default("a11y-sample", root)
        archive = Archive.init(config)
        record = Record(
            title="Sample record",
            default_policy=AccessPolicy.PUBLIC,
            dublin_core=DublinCore(
                title=["Sample record"],
                description=["A sample record used only to render the accessibility surface."],
                coverage=["Sample City"],
                date=["1994"],
            ),
            fields=[Field(name="story", value="A sample story.", policy=AccessPolicy.PUBLIC)],
        )
        archive.ingest({}, record, now="2026-01-01T00:00:00Z")
        from ledger.access.grants import anonymous

        disclosed = archive.browse(anonymous(), now="2026-01-01T00:00:00Z")
        one = archive.disclose(record.record_id, anonymous(), now="2026-01-01T00:00:00Z")

        transparency_log = TransparencyLog(root / "transparency.json")
        latest_attestation = transparency_log.append(
            attested_date="2026-01-01",
            attested_by="a11y-sample",
            statement_text="Sample statement used only to render the accessibility surface.",
            demand_counts={"subpoena": 0},
        )
        transparency_html = transparency_main_html(
            heading="Legal-process transparency",
            latest=latest_attestation,
            entries=transparency_log.all(),
            cadence_days=90,
        )

        return {
            "rendered:/": _page(
                "Browse", lang="en", main_html=_browse_main_html(disclosed, heading="Browse")
            ),
            "rendered:/record/{id}": _page(
                one.title, lang="en", main_html=_record_main_html(one, proceed=True)
            ),
            "rendered:/places": _page(
                "Browse by place", lang="en", main_html=_places_html(disclosed)
            ),
            "rendered:/timeline": _page(
                "Browse by time", lang="en", main_html=_timeline_html(disclosed)
            ),
            "rendered:/contribute": _page(
                "Contribute", lang="en", main_html=contribute.render_contribute_main(config)
            ),
            "rendered:/transparency": _page(
                "Legal-process transparency", lang="en", main_html=transparency_html
            ),
            # These three reuse a pure render function server.py already calls
            # unmodified -- no server.py change was needed to add them (#122). The
            # remaining uncovered routes build their <main> HTML inline inside a
            # ServerHandler method instead of through a function like these, so
            # reaching them would mean extracting one first: separate, real work,
            # tracked and reasoned about route by route in
            # docs/accessibility/ROUTE-COVERAGE.md rather than done here.
            "rendered:/overview": _page(
                i18n.t("en", "overview_heading"),
                lang="en",
                main_html=_overview_main_html(disclosed, lang="en"),
            ),
            "rendered:/withdraw": _page(
                "Withdraw", lang="en", main_html=contribute.render_withdraw_main(lang="en")
            ),
            "rendered:/edit": _page(
                "Edit", lang="en", main_html=contribute.render_edit_main(config, lang="en")
            ),
        }
    except OSError:
        # A sandbox with no writable temp directory is an environment fact, not a
        # defect -- degrade to no rendered samples so the static file scan in
        # check_dir still runs (robustness). Everything else (ImportError,
        # AttributeError/TypeError from a signature drift, a genuine bug in the
        # render path) is deliberately NOT caught here: check_dir's own
        # zero-documents assertion is the backstop for "nothing to render", but a
        # renderer that raises for its own reasons must fail loudly, not be
        # folded into that same silent path (#122).
        return {}


@dataclass(frozen=True)
class AccessibilityReport:
    """Everything :func:`check_dir` found, and everything it actually examined.

    ``html_documents`` and ``css_files`` name every artifact scanned — a file path
    for a static file, a ``rendered:/path`` label for a server-rendered sample —
    in the order each was checked, so a caller (or a human reading the CI log) can
    see what a pass covered instead of only being told "passed" (#122).

    An empty ``html_documents`` is not silently "no problems": :func:`check_dir`
    appends its own entry to ``problems`` in that case, because the structural
    checks are vacuously satisfied over zero documents and that is not evidence
    the site is accessible — it is evidence nothing was checked. The two must
    never look the same in this tool's own output.
    """

    problems: list[str]
    html_documents: tuple[str, ...]
    css_files: tuple[str, ...]


def check_dir(path: Path) -> AccessibilityReport:
    """Scan every ``.html`` file under ``path`` (plus rendered samples) for problems.

    Returns an :class:`AccessibilityReport` naming every problem found and every
    document/stylesheet actually examined. Files are visited in sorted order so
    two runs over the same tree report identically (reproducibility).

    Examining zero HTML documents — no static ``.html`` under ``path`` *and* no
    server-rendered sample (:func:`_render_sample_pages` returned nothing, e.g.
    because it hit a defect rather than the one exception it degrades for) — is
    itself appended to ``problems`` rather than left as a quiet empty result: the
    whole structural floor (``lang``, ``<title>``, a single ``<h1>``, ``<main>``,
    the skip link, ``alt``, ``<label for>``, table ``<caption>``/``<th scope>``,
    ``tabindex``) previously rested entirely on the rendered samples, and a
    renderer that failed made this function report a clean pass having verified
    nothing (#122).
    """
    problems: list[str] = []
    html_documents: list[str] = []
    css_files: list[str] = []
    if path.exists():
        for html_file in sorted(path.rglob("*.html")):
            markup = html_file.read_text(encoding="utf-8", errors="replace")
            problems.extend(check_html(markup, label=str(html_file)))
            html_documents.append(str(html_file))
        # Colour-contrast audit over every stylesheet found (WCAG 1.4.3 / 1.4.11).
        for css_file in sorted(path.rglob("*.css")):
            css = css_file.read_text(encoding="utf-8", errors="replace")
            problems.extend(audit_css_contrast(css, label=str(css_file)))
            css_files.append(str(css_file))

    for label, markup in _render_sample_pages().items():
        problems.extend(check_html(markup, label=label))
        html_documents.append(label)

    if not html_documents:
        problems.append(
            f"no HTML documents were examined under {path}: 0 static .html files, and "
            "the server-rendered sample pages returned none. A pass with zero "
            "documents checked is not evidence the site is accessible (#122)"
        )

    return AccessibilityReport(
        problems=problems, html_documents=tuple(html_documents), css_files=tuple(css_files)
    )


def main(argv: list[str] | None = None) -> int:
    """Run the accessibility check, print any problems, and return an exit code.

    The directory to scan is ``argv[0]`` if given, else ``web`` (the bundled
    site). Returns ``0`` when no problems are found and ``1`` otherwise, so a CI
    gate can branch on the exit code (operability). A failure lists every problem,
    one per line. A pass names exactly what was examined — every HTML document
    and every stylesheet, one per line — so the log is self-evidencing rather than
    asking a reader to trust "passed" on faith (#122).
    """
    args = sys.argv[1:] if argv is None else argv
    target = Path(args[0]) if args else Path("web")
    report = check_dir(target)
    if report.problems:
        print(f"accessibility check FAILED for {target}: {len(report.problems)} problem(s)")
        for problem in report.problems:
            print(f"  - {problem}")
        return 1
    print(
        f"accessibility check passed for {target}: {len(report.html_documents)} HTML "
        f"document(s) and {len(report.css_files)} stylesheet(s) checked"
    )
    for doc in report.html_documents:
        print(f"  - {doc}")
    for css in report.css_files:
        print(f"  - {css}")
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
