"""Pin issue #122's route-coverage scoping decision to reality.

The static gate (`ledger.accessibility_check`) and the browser gate
(`tools/a11y_browser/axe.spec.ts` / `reflow.spec.ts`) each cover a fixed,
hand-maintained list of routes. Neither list is derived from
`src/ledger/server.py`'s route table, so nothing stops them from silently
drifting apart from what the server actually serves, or from the coverage gap
recorded in `docs/accessibility/ROUTE-COVERAGE.md`.

These tests do not add coverage — they check that the *claim* in
ROUTE-COVERAGE.md ("these 13 routes are covered, these 8 are not") stays true:

* every route this file's inventory names still exists in `server.py`'s `do_GET`
  dispatch (a renamed or removed route is caught here, not discovered by someone
  reading stale prose);
* the static gate's real, live coverage (`_render_sample_pages()`, called
  directly rather than hand-copied) plus the browser gate's hand-maintained list
  covers exactly the 13 routes the doc claims;
* the remaining, uncovered routes are exactly the 8 the doc names — not a
  superset (an undocumented gap) and not a subset (a stale doc overclaiming a
  gap that coverage has since closed);
* every uncovered route's literal path actually appears in the committed doc,
  so the gap is a written fact, not just a fact this test happens to know;
* and the reverse of the first bullet — every literal route `do_GET` dispatches
  is classified as HTML-in-scope or explicitly out of scope, so a *new* page
  cannot appear with no accessibility decision and no red test.
"""

from __future__ import annotations

import re
from pathlib import Path

from ledger.accessibility_check import _render_sample_pages

_ROOT = Path(__file__).resolve().parent.parent
_SERVER_PY = _ROOT / "src" / "ledger" / "server.py"
_ROUTE_COVERAGE_DOC = _ROOT / "docs" / "accessibility" / "ROUTE-COVERAGE.md"

# Every HTML-emitting GET route in server.py's do_GET dispatch table, enumerated by
# reading it directly (issue #122's own count). Deliberately excludes routes that do
# not emit HTML for a person -- /healthz and /proof/attestation.json (JSON), /oai,
# /sitemap.xml, /feed.atom (XML/feed formats), /robots.txt (plain text),
# /record/{id}/file/{name} (the binary payload itself), /api/* (JSON), and /static/*
# (assets) -- those are not in scope for a WCAG structural/browser accessibility gate.
_ALL_HTML_ROUTES: tuple[str, ...] = (
    "/",
    "/search",
    "/status",
    "/consent-status",
    "/about",
    "/overview",
    "/places",
    "/timeline",
    "/governance",
    "/how-it-works",
    "/proof",
    "/transparency",
    "/steward",
    "/steward/audit",
    "/contribute",
    "/withdraw",
    "/edit",
    "/record/{id}",
    "/record/{id}/consent",
    "/record/{id}/object",
    "/record/{id}/history",
)

# Every route covered end to end by axe.spec.ts / reflow.spec.ts: each file's
# STATIC_PAGES array plus the explicit record (both content-warning states, counted
# once) and steward-console tests both files add. Hand-maintained in step with
# tools/a11y_browser/*.spec.ts; test_all_html_routes_are_still_present_in_dispatch
# below at least catches a route disappearing from server.py out from under it.
_PLAYWRIGHT_COVERED_ROUTES: tuple[str, ...] = (
    "/",
    "/search",
    "/contribute",
    "/about",
    "/how-it-works",
    "/record/{id}",
    "/steward",
)

# The 8 routes docs/accessibility/ROUTE-COVERAGE.md names as having no automated
# accessibility coverage from either engine.
_DOCUMENTED_UNCOVERED_ROUTES: tuple[str, ...] = (
    "/status",
    "/consent-status",
    "/governance",
    "/proof",
    "/steward/audit",
    "/record/{id}/consent",
    "/record/{id}/object",
    "/record/{id}/history",
)

# The record sub-routes are dispatched by startswith("/record/")/endswith(...) pairs
# rather than a literal path match; the plain path == "..." needle used for every
# other route does not apply to them.
_RECORD_SUBROUTE_SUFFIXES: dict[str, str] = {
    "/record/{id}": "",
    "/record/{id}/consent": "/consent",
    "/record/{id}/object": "/object",
    "/record/{id}/history": "/history",
}


def _do_get_dispatch_source() -> str:
    """The source text of ``ServerHandler.do_GET``, for a route-presence check."""
    text = _SERVER_PY.read_text(encoding="utf-8")
    start = text.index("def do_GET(self)")
    end = text.index("def do_POST(self)", start)
    return text[start:end]


def _static_gate_covered_routes() -> set[str]:
    """The routes the static gate actually renders right now.

    Derived by calling ``_render_sample_pages()`` directly rather than hand-copying
    its keys, so this stops matching the moment someone extends (or accidentally
    shrinks) the static gate's rendered-sample coverage without updating
    ROUTE-COVERAGE.md to match.
    """
    return {label.removeprefix("rendered:") for label in _render_sample_pages()}


# The literal `path == "..."` routes in do_GET that deliberately do not emit HTML for
# a person, and so are out of scope for a WCAG gate. Kept as data rather than prose so
# the inventory can be checked against the dispatch table in both directions: the
# reverse check below fails on any new literal route that is in neither list.
_NON_HTML_ROUTES: frozenset[str] = frozenset(
    {
        "/healthz",
        "/proof/attestation.json",
        "/oai",
        "/sitemap.xml",
        "/robots.txt",
        "/feed.atom",
        "/api/records",
        "/api/search",
        "/api/search.csv",
    }
)

_LITERAL_ROUTE_RE = re.compile(r'path == "([^"]+)"')


def test_a_new_html_route_cannot_be_added_without_an_accessibility_decision() -> None:
    """The direction the other checks do not cover.

    ``test_all_html_routes_are_still_present_in_dispatch`` catches a route that
    *disappears* from ``server.py``. Nothing caught a route that *appears*: a new page
    could be served with no automated accessibility coverage from either engine, and
    every count in ``ROUTE-COVERAGE.md`` would silently become wrong while the whole
    suite stayed green. A hand-maintained inventory that is only checked in the
    direction it cannot drift is not checked.

    Every literal route the dispatch table serves must therefore be classified: either
    in this file's HTML inventory (and so accounted for as covered or as a documented
    gap) or in ``_NON_HTML_ROUTES``. Adding a page is then a decision, not an omission.
    """
    dispatched = set(_LITERAL_ROUTE_RE.findall(_do_get_dispatch_source()))
    unclassified = dispatched - set(_ALL_HTML_ROUTES) - _NON_HTML_ROUTES
    assert not unclassified, (
        f"server.py dispatches {sorted(unclassified)}, which is in neither the HTML "
        "route inventory nor the non-HTML exclusion list. If it renders a page for a "
        "person, add it to _ALL_HTML_ROUTES and to docs/accessibility/ROUTE-COVERAGE.md "
        "as covered or as a named gap; if it does not, add it to _NON_HTML_ROUTES."
    )


def test_the_non_html_exclusion_list_does_not_name_routes_that_are_gone() -> None:
    """An exclusion for a route that no longer exists quietly widens the next one."""
    dispatched = set(_LITERAL_ROUTE_RE.findall(_do_get_dispatch_source()))
    stale = _NON_HTML_ROUTES - dispatched
    assert not stale, f"_NON_HTML_ROUTES excludes {sorted(stale)}, which do_GET no longer serves"


def test_all_html_routes_are_still_present_in_dispatch() -> None:
    """Every route this file's inventory names must still be dispatched in
    ``server.py``, so a rename or removal is caught here rather than leaving the
    coverage accounting silently describing routes that no longer exist."""
    do_get = _do_get_dispatch_source()
    for route in _ALL_HTML_ROUTES:
        if route in _RECORD_SUBROUTE_SUFFIXES:
            suffix = _RECORD_SUBROUTE_SUFFIXES[route]
            needle = f'path.endswith("{suffix}")' if suffix else 'path.startswith("/record/")'
        else:
            needle = f'path == "{route}"'
        assert needle in do_get, f"{route} no longer appears in do_GET's dispatch table"


def test_static_gate_and_playwright_union_covers_exactly_thirteen_routes() -> None:
    """The "13 of 21" figure in ROUTE-COVERAGE.md, re-derived.

    10 were already covered before this fix (6 static + 7 Playwright, with 3
    overlapping); this PR adds 3 more to the static gate (``/overview``,
    ``/withdraw``, ``/edit``) by calling pure render functions ``server.py``
    already called unmodified, with no `server.py` change required.
    """
    covered = _static_gate_covered_routes() | set(_PLAYWRIGHT_COVERED_ROUTES)
    assert covered <= set(_ALL_HTML_ROUTES), covered - set(_ALL_HTML_ROUTES)
    assert len(covered) == 13, covered


def test_uncovered_routes_match_the_documented_gap() -> None:
    """The scoping decision from issue #122: the 8 routes with no automated
    accessibility coverage from either engine are exactly the ones
    docs/accessibility/ROUTE-COVERAGE.md names -- not a superset (an undocumented
    gap) and not a subset (a stale doc overclaiming a gap coverage has closed)."""
    covered = _static_gate_covered_routes() | set(_PLAYWRIGHT_COVERED_ROUTES)
    uncovered = set(_ALL_HTML_ROUTES) - covered
    assert uncovered == set(_DOCUMENTED_UNCOVERED_ROUTES)
    assert len(uncovered) == 8


def test_route_coverage_doc_names_every_uncovered_route() -> None:
    """The documented gap must actually be written down, not just true in Python:
    every uncovered route's literal path string must appear in the committed doc."""
    doc = _ROUTE_COVERAGE_DOC.read_text(encoding="utf-8")
    for route in _DOCUMENTED_UNCOVERED_ROUTES:
        assert route in doc, f"{route} is not named in {_ROUTE_COVERAGE_DOC}"


def test_route_coverage_doc_dates_the_gap() -> None:
    """A gap recorded with no date invites the reader to assume it is current
    forever; the recheck-cadence convention this repo's other accessibility docs
    (STATEMENT.md, MANUAL-REVIEW-CADENCE.md) use applies here too."""
    doc = _ROUTE_COVERAGE_DOC.read_text(encoding="utf-8")
    assert "Last verified:" in doc
