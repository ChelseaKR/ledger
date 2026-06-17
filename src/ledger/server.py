"""The accessible, framework-free browse/search server — ledger's public face.

This is the only surface most people ever touch, so two qualities dominate every
line: **accessibility** (WCAG 2.2 AA) and **safety** (the no-outing rule).

Framework-free, standard-library only (:mod:`http.server`) -> portability,
affordability, no lock-in: a community runs the whole public site on one
inexpensive box with no web framework, no build step, and no paid service.

Every record that reaches a response is produced by :meth:`Archive.browse` or
:meth:`Archive.disclose`, i.e. by :func:`ledger.access.disclose` — the single
disclosure chokepoint. There is **no** code path here that constructs a record
view from anything but a :class:`~ledger.models.DisclosedRecord`, and a
``DisclosedRecord`` structurally cannot carry a contributor identity: it has no
``identity_ref`` field. Consequently no route, query parameter, response header,
JSON field, HTML attribute, log line, health summary, or error page in this
module can ever expose ``identity_ref`` or any contributor identity (safety,
confidentiality — the no-outing rule, confirmed in depth below):

* Reads go only through ``disclose``/``browse`` (the safe shape).
* The request log is overridden to emit a method + status + scrubbed path only —
  never headers, never a grant subject, never a query string echo of identity.
* Every interpolated string is passed through :func:`html.escape` (and JSON is
  serialized with the standard encoder), so untrusted text cannot break the page
  structure or inject script (security — no XSS) and renders correctly.
* The static handler resolves and bounds every path under ``web/static`` so a
  ``../`` cannot escape the document root (securability — no path traversal).

Grant resolution is deny-by-default: requests are anonymous unless an
``X-Ledger-Grant: <subject>`` header names a *pre-provisioned* subject in the
grants file. An unknown subject falls back to anonymous; the header is never
trusted beyond looking up an existing grant (least privilege, securability).
"""

from __future__ import annotations

import html
import http.server
import json
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

from ledger.access import anonymous
from ledger.access.grants import load_grants
from ledger.errors import AccessDenied, LedgerError, ObjectNotFound
from ledger.ingest import Archive
from ledger.models import DisclosedRecord, Grant

# Where the bundled, framework-free web assets live, resolved relative to this
# module so the server works from any working directory (portability). The static
# root is the canonical boundary the traversal guard enforces.
_WEB_ROOT: Path = Path(__file__).resolve().parent.parent.parent / "web"
_STATIC_ROOT: Path = (_WEB_ROOT / "static").resolve()

# Header a reverse proxy or an authenticated session may set to name a subject.
# It is only ever used as a *lookup key* into a pre-provisioned grants file; the
# header itself confers nothing (deny by default, least privilege).
_GRANT_HEADER: str = "X-Ledger-Grant"

# A conservative allowlist of static file suffixes to content types. Anything not
# listed is served as ``application/octet-stream`` rather than guessed, so the
# server never advertises an executable or active type it did not intend to
# (securability). Kept tiny on purpose — this site ships only CSS.
_STATIC_CONTENT_TYPES: dict[str, str] = {
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".ico": "image/vnd.microsoft.icon",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
}

# The site's one stylesheet, linked from every page.
_STYLESHEET_HREF: str = "/static/app.css"


# --- HTML rendering ---------------------------------------------------------
#
# HTML is rendered in plain Python (no template engine -> standard-library only,
# no lock-in). Every interpolated value goes through `_esc`, which is the only
# way text reaches the page, so escaping cannot be forgotten per call site
# (security — no XSS, by construction).


def _esc(value: object) -> str:
    """HTML-escape ``value`` for safe interpolation, quotes included.

    This is the single text-to-HTML boundary in the module: every dynamic string
    (titles, descriptions, field values, ids, queries, redaction notes) passes
    through here, so an attacker-controlled value cannot break out of its element
    or attribute context (security — no cross-site scripting).
    """
    return html.escape(str(value), quote=True)


def _page(title: str, *, lang: str, main_html: str, nav_html: str = "") -> str:
    """Wrap ``main_html`` in the shared, accessible page shell.

    The shell encodes the WCAG 2.2 AA structure every page shares (accessibility):
    a declared document type and ``lang``; a unique, descriptive ``<title>``; a
    visible "skip to main content" link as the *first* focusable element; the
    ``header``/``nav``/``main``/``footer`` landmarks; and a single ``<main>``
    target the skip link jumps to. ``title`` is escaped because record titles flow
    into it (security).

    Colour is never the sole signal anywhere in the shell, and no positive
    ``tabindex`` is used, so keyboard focus order follows source order
    (accessibility).
    """
    nav_block = f'\n    <nav aria-label="Site">{nav_html}</nav>' if nav_html else ""
    return (
        "<!doctype html>\n"
        f'<html lang="{_esc(lang)}">\n'
        "<head>\n"
        '  <meta charset="utf-8">\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"  <title>{_esc(title)} — ledger</title>\n"
        f'  <link rel="stylesheet" href="{_STYLESHEET_HREF}">\n'
        "</head>\n"
        "<body>\n"
        '  <a class="skip-link" href="#main">Skip to main content</a>\n'
        "  <header>\n"
        '    <p class="brand"><a href="/">ledger — community archive</a></p>'
        f"{nav_block}\n"
        "  </header>\n"
        '  <main id="main" tabindex="-1">\n'
        f"{main_html}\n"
        "  </main>\n"
        "  <footer>\n"
        "    <p>A privacy-first community archive. Contributor identities are never "
        "shown here.</p>\n"
        "  </footer>\n"
        "</body>\n"
        "</html>\n"
    )


def _search_form(query: str = "") -> str:
    """Render the search form with a programmatically associated label.

    The ``<label for>`` is tied to the input's ``id`` so assistive technology
    announces the field's purpose, and the current ``query`` is escaped back into
    ``value`` so a search term cannot inject markup (accessibility, security).
    """
    return (
        '<form class="search" role="search" method="get" action="/search">\n'
        '  <label for="q">Search titles and descriptions</label>\n'
        f'  <input id="q" name="q" type="search" value="{_esc(query)}" '
        'autocomplete="off">\n'
        '  <button type="submit">Search</button>\n'
        "</form>\n"
    )


def _summary_text(record: DisclosedRecord) -> str:
    """A short, identity-free description for a record, drawn from Dublin Core.

    Uses the first ``description`` element if present (collection-level metadata,
    never identity), else an empty string. Returned raw; callers escape at the
    point of interpolation.
    """
    descriptions = record.dublin_core.get("description", [])
    return descriptions[0] if descriptions else ""


def _records_list_html(records: Iterable[DisclosedRecord]) -> str:
    """Render the records as a semantic list — one accessible equivalent view.

    The list and the table (:func:`_records_table_html`) present the same data in
    two equally complete forms, so a user of either a screen reader or a small
    screen gets the full content (accessibility — documented non-visual
    equivalent). Link text is the record title (descriptive links, never "click
    here"). All text is escaped (security).
    """
    items: list[str] = []
    for record in records:
        summary = _summary_text(record)
        warn = ' <span class="badge">Content warning</span>' if record.content_warnings else ""
        summary_html = f"<p>{_esc(summary)}</p>" if summary else ""
        items.append(
            "    <li>\n"
            f'      <h3><a href="/record/{quote(record.record_id)}">'
            f"{_esc(record.title)}</a>{warn}</h3>\n"
            f"      {summary_html}\n"
            "    </li>"
        )
    if not items:
        return '<p class="empty">No records are available to you yet.</p>'
    body = "\n".join(items)
    return f'<ul class="record-list">\n{body}\n</ul>'


def _records_table_html(records: Iterable[DisclosedRecord]) -> str:
    """Render the records as a data table — the documented non-visual equivalent.

    The table carries a ``<caption>`` describing its purpose and ``<th scope>`` on
    every header so assistive technology can associate each cell with its column
    (accessibility). The "Content warning" column uses the literal word, never a
    colour or icon alone, so the signal survives for colour-blind and
    text-only users (accessibility — colour is not the only signal). All cells are
    escaped (security).
    """
    rows: list[str] = []
    for record in records:
        warn = "Yes" if record.content_warnings else "No"
        summary = _summary_text(record)
        rows.append(
            "      <tr>\n"
            f'        <td><a href="/record/{quote(record.record_id)}">'
            f"{_esc(record.title)}</a></td>\n"
            f"        <td>{_esc(summary)}</td>\n"
            f"        <td>{warn}</td>\n"
            "      </tr>"
        )
    body = (
        "\n".join(rows)
        if rows
        else ('      <tr><td colspan="3">No records are available to you yet.</td></tr>')
    )
    return (
        '<table class="record-table">\n'
        "  <caption>All records you may view, with their titles, summaries, and "
        "whether each carries a content warning.</caption>\n"
        "  <thead>\n"
        "    <tr>\n"
        '      <th scope="col">Title</th>\n'
        '      <th scope="col">Summary</th>\n'
        '      <th scope="col">Content warning</th>\n'
        "    </tr>\n"
        "  </thead>\n"
        "  <tbody>\n"
        f"{body}\n"
        "  </tbody>\n"
        "</table>"
    )


def _browse_main_html(
    records: list[DisclosedRecord],
    *,
    heading: str,
    query: str = "",
) -> str:
    """Compose the browse/search ``<main>``: one ``<h1>``, the form, then both views.

    Renders the list and the table as two complete, equivalent presentations of
    the same records (accessibility — equivalent list and table views). Heading
    order is ``h1`` (page) then ``h2`` (each view) then ``h3`` (list items), with
    no levels skipped (accessibility — sane heading order).
    """
    count = len(records)
    return (
        f"    <h1>{_esc(heading)}</h1>\n"
        f"    {_search_form(query)}"
        f'    <p class="count">{count} record(s) shown.</p>\n'
        '    <section aria-labelledby="list-heading">\n'
        '      <h2 id="list-heading">Records (list view)</h2>\n'
        f"      {_records_list_html(records)}\n"
        "    </section>\n"
        '    <section aria-labelledby="table-heading">\n'
        '      <h2 id="table-heading">Records (table view)</h2>\n'
        f"      {_records_table_html(records)}\n"
        "    </section>"
    )


def _record_main_html(record: DisclosedRecord, *, proceed: bool) -> str:
    """Compose the single-record ``<main>``, with a content-warning interstitial.

    If the record carries content warnings and the viewer has not yet chosen to
    proceed (``proceed`` is false), only the title and a *text* interstitial are
    rendered: the warnings are listed as words, headed "Content warnings", with a
    link to proceed to the content (accessibility — the warning is programmatic and
    textual, never colour- or icon-only; safety — warnings surface before any
    render of the underlying material).

    Once proceeding (or when there are no warnings) the disclosed fields, payload
    list, and Dublin Core are shown, and any withheld field/payload is named in a
    plain-text "Withheld" note so the lossy view is honest about being lossy
    (honesty, fidelity). Identity never appears: the source is a
    :class:`~ledger.models.DisclosedRecord`, which has no identity field.
    """
    rid = record.record_id
    if record.content_warnings and not proceed:
        warnings = "\n".join(f"      <li>{_esc(w)}</li>" for w in record.content_warnings)
        proceed_href = f"/record/{quote(rid)}?proceed=1#content"
        return (
            f"    <h1>{_esc(record.title)}</h1>\n"
            '    <section class="interstitial" role="region" '
            'aria-labelledby="cw-heading">\n'
            '      <h2 id="cw-heading">Content warnings</h2>\n'
            "      <p>This record carries the following content warnings. "
            "Review them before continuing.</p>\n"
            "      <ul>\n"
            f"{warnings}\n"
            "      </ul>\n"
            f'      <p><a class="proceed" href="{_esc(proceed_href)}">'
            "Proceed to the content</a></p>\n"
            "    </section>"
        )

    parts: list[str] = [f"    <h1>{_esc(record.title)}</h1>"]

    if record.content_warnings:
        # Even after proceeding, restate the warnings as text above the content so
        # the signal is never lost (safety, accessibility).
        warnings = ", ".join(_esc(w) for w in record.content_warnings)
        parts.append(
            f'    <p class="cw-note" id="content"><strong>Content warnings:</strong> {warnings}</p>'
        )
    else:
        parts.append('    <p id="content" class="visually-hidden">Record content.</p>')

    # Disclosed descriptive fields.
    if record.fields:
        rows = "\n".join(
            f'      <div class="field"><dt>{_esc(name)}</dt><dd>{_esc(value)}</dd></div>'
            for name, value in record.fields.items()
        )
        parts.append(
            '    <section aria-labelledby="fields-heading">\n'
            '      <h2 id="fields-heading">Details</h2>\n'
            "      <dl>\n"
            f"{rows}\n"
            "      </dl>\n"
            "    </section>"
        )

    # Dublin Core descriptive metadata (collection-level; never identity).
    dc_rows = [
        f'      <div class="field"><dt>{_esc(element)}</dt><dd>{_esc("; ".join(values))}</dd></div>'
        for element, values in record.dublin_core.items()
        if values
    ]
    if dc_rows:
        parts.append(
            '    <section aria-labelledby="meta-heading">\n'
            '      <h2 id="meta-heading">Catalogue metadata</h2>\n'
            "      <dl>\n" + "\n".join(dc_rows) + "\n      </dl>\n"
            "    </section>"
        )

    # Payload files the viewer may see.
    if record.payloads:
        files = "\n".join(
            f"      <li>{_esc(p.filename)} "
            f'<span class="muted">({_esc(p.media_type)}, '
            f"{p.size_bytes} bytes)</span></li>"
            for p in record.payloads
        )
        parts.append(
            '    <section aria-labelledby="files-heading">\n'
            '      <h2 id="files-heading">Files</h2>\n'
            "      <ul>\n"
            f"{files}\n"
            "      </ul>\n"
            "    </section>"
        )

    # Redactions, stated plainly in text so the partial view is honest.
    if record.redactions:
        withheld = ", ".join(_esc(name) for name in record.redactions)
        parts.append(
            '    <section aria-labelledby="redactions-heading">\n'
            '      <h2 id="redactions-heading">Withheld</h2>\n'
            "      <p>The following parts of this record are not available under "
            f"your current access: {withheld}.</p>\n"
            "    </section>"
        )

    parts.append('    <p><a href="/">Back to all records</a></p>')
    return "\n".join(parts)


def _error_main_html(heading: str, message: str) -> str:
    """Render an accessible error ``<main>``.

    The ``message`` names only the condition and, at most, an object id — never a
    sealed value or any identity (no-outing rule; error pages disclose nothing).
    """
    return (
        f"    <h1>{_esc(heading)}</h1>\n"
        f"    <p>{_esc(message)}</p>\n"
        '    <p><a href="/">Back to all records</a></p>'
    )


# --- the request handler ----------------------------------------------------


class ArchiveRequestHandler(http.server.BaseHTTPRequestHandler):
    """Serve the accessible browse/search site over the standard-library server.

    The handler is read-only and disclosure-gated: every record-bearing response
    is built from a :class:`~ledger.models.DisclosedRecord` produced by
    :meth:`Archive.browse`/:meth:`Archive.disclose`, so no route can emit a
    contributor identity or an ``identity_ref`` (safety — the no-outing rule).

    Instances are created per request by :class:`http.server.HTTPServer`; the
    :class:`Archive` and grants mapping are bound once on the server object (see
    :func:`make_server`) and read here, so no per-request wiring is needed
    (simplicity).
    """

    # Bound onto the server in `make_server`; mirrored here for type-checked access.
    server_version = "ledger/0.1"
    protocol_version = "HTTP/1.1"

    # --- safety: a scrubbed access log -------------------------------------

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        """Emit a minimal, identity-free access line with the *real* status.

        The default would log ``self.requestline`` verbatim — which includes the
        query string (e.g. ``/search?q=...``) and so could record a search term.
        We log only the method, the query-stripped path, and the response ``code``
        the framework passes in, so a grant subject or search term never reaches the
        log (no-outing rule — logs disclose nothing).
        """
        path = urlsplit(self.path).path
        super().log_message("%s %s %s", self.command or "?", path, code)

    def log_error(self, format: str, *args: object) -> None:
        """Log errors without echoing any request data.

        Error logging never formats the (possibly sensitive) request line or args;
        it records a fixed, scrubbed line keyed on the query-stripped path only.
        """
        super().log_message("error handling %s", urlsplit(self.path).path)

    # --- grant resolution (deny by default) --------------------------------

    def _resolve_grant(self) -> Grant:
        """Resolve the viewer's grant — anonymous unless a known subject is named.

        Reads the ``X-Ledger-Grant`` header and looks the subject up in the
        pre-provisioned grants mapping bound to the server. An unknown (or absent)
        subject yields the anonymous public grant, so trust is never conferred by
        the header itself — only by a grant a steward provisioned ahead of time
        (deny by default, least privilege, securability).
        """
        grants = self._grants()
        subject = self.headers.get(_GRANT_HEADER)
        if subject is not None:
            grant = grants.get(subject)
            if grant is not None:
                return grant
        return anonymous()

    # --- typed access to the server-bound dependencies ---------------------

    def _archive(self) -> Archive:
        archive = getattr(self.server, "archive", None)
        if not isinstance(archive, Archive):  # pragma: no cover - misconfiguration
            raise LedgerError("server is not bound to an Archive")
        return archive

    def _grants(self) -> dict[str, Grant]:
        grants = getattr(self.server, "grants", None)
        return grants if isinstance(grants, dict) else {}

    # --- response helpers ---------------------------------------------------

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        """Write a complete response with an explicit length and safe headers.

        Sets a conservative ``Content-Security-Policy`` and ``X-Content-Type-
        Options: nosniff`` so a browser will not execute inline script or sniff a
        served file into an active type (security, defense in depth). No header
        carries any request-derived value, so headers cannot leak identity.
        """
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'self'; img-src 'self'; "
            "base-uri 'none'; form-action 'self'",
        )
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_html(self, status: int, page: str) -> None:
        self._send(status, page.encode("utf-8"), "text/html; charset=utf-8")

    def _send_json(self, status: int, obj: object) -> None:
        """Serialize ``obj`` as JSON. Only DisclosedRecord-derived data is passed in,
        so the JSON cannot contain an identity field (no-outing rule)."""
        body = json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _lang(self) -> str:
        """The archive's primary configured language, defaulting to English."""
        languages = self._archive().config.languages
        return languages[0] if languages else "en"

    # --- routing ------------------------------------------------------------

    def do_HEAD(self) -> None:
        """Handle HEAD identically to GET but without a body (handled in `_send`)."""
        self.do_GET()

    def do_GET(self) -> None:
        """Route a GET request to the matching read-only handler.

        Routing is a small, explicit dispatch (predictability). Unmatched paths
        get a 404; any :class:`~ledger.errors.LedgerError` is rendered as a safe
        error page or JSON error whose message names no protected content
        (no-outing rule).
        """
        parts = urlsplit(self.path)
        path = parts.path
        params = parse_qs(parts.query)
        try:
            if path == "/":
                self._handle_browse()
            elif path == "/search":
                self._handle_search(params)
            elif path == "/healthz":
                self._handle_healthz()
            elif path.startswith("/record/"):
                self._handle_record(path[len("/record/") :], params)
            elif path == "/api/records":
                self._handle_api_records()
            elif path.startswith("/api/record/"):
                self._handle_api_record(path[len("/api/record/") :])
            elif path.startswith("/static/"):
                self._handle_static(path[len("/static/") :])
            else:
                self._handle_not_found()
        except BrokenPipeError:  # pragma: no cover - client disconnected
            pass

    # --- HTML routes --------------------------------------------------------

    def _handle_browse(self) -> None:
        """``GET /`` — the accessible browse page (list + table equivalents)."""
        grant = self._resolve_grant()
        records = self._archive().browse(grant)
        main_html = _browse_main_html(records, heading="Browse the archive")
        self._send_html(
            200,
            _page("Browse", lang=self._lang(), main_html=main_html, nav_html=_nav_html()),
        )

    def _handle_search(self, params: dict[str, list[str]]) -> None:
        """``GET /search?q=`` — filter disclosed titles/descriptions by ``q``.

        The filter runs over already-disclosed records, so it can never surface a
        field the grant may not see (safety — search respects disclosure). The
        query is echoed back only through :func:`html.escape` (security).
        """
        grant = self._resolve_grant()
        query = (params.get("q", [""])[0]).strip()
        disclosed = self._archive().browse(grant)
        if query:
            needle = query.casefold()
            matched = [r for r in disclosed if _matches(r, needle)]
        else:
            matched = disclosed
        heading = f"Search results for “{query}”" if query else "Search"
        main_html = _browse_main_html(matched, heading=heading, query=query)
        self._send_html(
            200,
            _page(
                f"Search — {query}" if query else "Search",
                lang=self._lang(),
                main_html=main_html,
                nav_html=_nav_html(),
            ),
        )

    def _handle_record(self, raw_id: str, params: dict[str, list[str]]) -> None:
        """``GET /record/{id}`` — a single record view with a CW interstitial."""
        record_id = _decode_id(raw_id)
        grant = self._resolve_grant()
        proceed = params.get("proceed", ["0"])[0] == "1"
        try:
            record = self._archive().disclose(record_id, grant)
        except (AccessDenied, ObjectNotFound):
            # Both "not found" and "not permitted to list" render the same neutral
            # page, so the response never reveals whether a sealed record exists
            # (confidentiality — the absence of a record leaks nothing).
            self._handle_not_found()
            return
        main_html = _record_main_html(record, proceed=proceed)
        self._send_html(
            200,
            _page(
                record.title,
                lang=self._lang(),
                main_html=main_html,
                nav_html=_nav_html(),
            ),
        )

    def _handle_not_found(self) -> None:
        """Render the shared, neutral 404 page (reveals nothing about existence)."""
        main_html = _error_main_html(
            "Not found",
            "We could not find anything at that address, or it is not available to you.",
        )
        self._send_html(
            404,
            _page("Not found", lang=self._lang(), main_html=main_html, nav_html=_nav_html()),
        )

    # --- JSON routes (same disclosure gate) ---------------------------------

    def _handle_api_records(self) -> None:
        """``GET /api/records`` — JSON of every listable record's disclosed shape."""
        grant = self._resolve_grant()
        records = self._archive().browse(grant)
        self._send_json(200, {"records": [r.to_dict() for r in records]})

    def _handle_api_record(self, raw_id: str) -> None:
        """``GET /api/record/{id}`` — JSON of one record's disclosed shape."""
        record_id = _decode_id(raw_id)
        grant = self._resolve_grant()
        try:
            record = self._archive().disclose(record_id, grant)
        except (AccessDenied, ObjectNotFound):
            self._send_json(404, {"error": "not found"})
            return
        self._send_json(200, record.to_dict())

    # --- health -------------------------------------------------------------

    def _handle_healthz(self) -> None:
        """``GET /healthz`` — JSON health plus an identity-free fixity summary.

        Reports ``status`` and counts only — bags audited, how many passed, how
        many showed fixity drift — drawn from :meth:`Archive.audit_fixity`. No bag
        path, file name, digest, record id, or identity appears, so the health
        endpoint is safe to expose to a monitor (no-outing rule; observability).
        """
        archive = self._archive()
        try:
            reports = archive.audit_fixity()
        except LedgerError:
            # A structurally broken bag must not take down health reporting, and
            # its details must not leak: report degraded with counts only.
            self._send_json(
                503,
                {"status": "degraded", "fixity": {"error": "audit failed"}},
            )
            return
        passed = sum(1 for _name, r in reports if r.ok)
        failed = len(reports) - passed
        files_checked = sum(r.checked for _name, r in reports)
        status = "ok" if failed == 0 else "degraded"
        self._send_json(
            200 if failed == 0 else 503,
            {
                "status": status,
                "fixity": {
                    "bags_audited": len(reports),
                    "bags_passed": passed,
                    "bags_failed": failed,
                    "files_checked": files_checked,
                },
            },
        )

    # --- static files (path-traversal safe) --------------------------------

    def _handle_static(self, rel: str) -> None:
        """Serve a file from ``web/static``, refusing any escape from the root.

        The requested path is decoded, joined under the canonical static root, and
        resolved; if the result is not *inside* that root (a ``../`` attempt, an
        absolute path, or a symlink out), the request is refused with a 404 rather
        than served (securability — no path traversal). Only files (never
        directories) with a known suffix are served; an unknown suffix falls back
        to ``application/octet-stream`` and is never treated as active content.
        """
        candidate = _STATIC_ROOT / _decode_id(rel)
        try:
            # resolve() itself raises ValueError on an embedded NUL byte, so it must
            # be inside the guard or a crafted path crashes the handler with no
            # response (robustness, securability). relative_to() rejects any escape.
            candidate = candidate.resolve()
            candidate.relative_to(_STATIC_ROOT)
        except ValueError:
            # The path was malformed or escaped the static root — refuse, no detail.
            self._handle_not_found()
            return
        if not candidate.is_file():
            self._handle_not_found()
            return
        content_type = _STATIC_CONTENT_TYPES.get(
            candidate.suffix.lower(), "application/octet-stream"
        )
        self._send(200, candidate.read_bytes(), content_type)


# --- module-level render helpers (shared by routes) -------------------------


def _nav_html() -> str:
    """The site navigation: descriptive links only, no positive tabindex."""
    return (
        '\n      <a href="/">Browse</a>\n'
        '      <a href="/search">Search</a>\n'
        '      <a href="/healthz">Status</a>\n    '
    )


def _matches(record: DisclosedRecord, needle: str) -> bool:
    """True if ``needle`` (already case-folded) appears in the disclosed text.

    Searches only the *disclosed* title and Dublin Core description, so a match
    can never depend on a field the grant may not see (safety — search respects
    the disclosure boundary).
    """
    haystack = [record.title, *record.dublin_core.get("description", [])]
    return any(needle in text.casefold() for text in haystack)


def _decode_id(raw: str) -> str:
    """Percent-decode a single path segment for matching against record ids.

    Path traversal is handled separately in :meth:`_handle_static`; for record
    ids the value is only ever used as a dictionary/file lookup key by the
    disclosure-gated archive, never interpolated into a path here.
    """
    return unquote(raw)


# --- server construction ----------------------------------------------------


def make_server(
    archive: Archive,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    grants_path: Path | None = None,
) -> http.server.HTTPServer:
    """Build (but do not start) the browse server bound to ``archive``.

    Binds to ``127.0.0.1`` by default rather than ``0.0.0.0`` so a freshly stood-up
    archive is reachable only from the local box until an operator deliberately
    exposes it behind a vetted reverse proxy (securability — do not bind the world
    by default). The pre-provisioned grants mapping is loaded once from
    ``grants_path`` (an absent file yields no grants, so everyone is anonymous —
    deny by default) and attached to the server, where the handler reads it.

    Dependencies are attached to the server instance rather than to module
    globals, so several archives can be served from one process without
    interfering (modularity, testability).
    """
    grants = load_grants(grants_path) if grants_path is not None else {}
    httpd = http.server.HTTPServer((host, port), ArchiveRequestHandler)
    # Attach the dependencies the handler reads per request.
    httpd.archive = archive  # type: ignore[attr-defined]
    httpd.grants = grants  # type: ignore[attr-defined]
    return httpd


def serve(
    archive: Archive,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    grants_path: Path | None = None,
) -> None:
    """Build and run the browse server until interrupted (blocking).

    A convenience over :func:`make_server` for a CLI or a ``python -m`` entry
    point. Binds to loopback by default (securability) and shuts down cleanly on
    ``KeyboardInterrupt`` so a steward can stop it without a traceback (usability).
    """
    httpd = make_server(archive, host=host, port=port, grants_path=grants_path)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover - interactive shutdown
        pass
    finally:
        httpd.server_close()
