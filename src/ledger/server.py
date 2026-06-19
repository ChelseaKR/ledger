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

import gzip
import hashlib
import html
import http.server
import json
import os
import time
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

from ledger import consent, contribute, i18n, oai, search
from ledger.access import anonymous
from ledger.access.grants import load_grants
from ledger.errors import AccessDenied, LedgerError, ObjectNotFound
from ledger.ingest import Archive
from ledger.models import AccessPolicy, DisclosedRecord, Grant, now_iso


def _is_insider(grant: Grant) -> bool:
    """Whether a viewer is a trusted insider (community member or steward).

    An insider is shown *why* each part is withheld (honesty, P1-3); an outsider
    gets only a count, so the set of redaction reasons cannot be scraped as
    targeting metadata about what a record hides (P2-2)."""
    return grant.is_steward or AccessPolicy.COMMUNITY in grant.levels


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
        f'  <a class="skip-link" href="#main">{_esc(i18n.t(lang, "skip_link"))}</a>\n'
        '  <p class="banner" role="note">Reference implementation — sample data is '
        "synthetic.</p>\n"
        "  <header>\n"
        '    <p class="brand"><a href="/">ledger — community archive</a></p>'
        f"{nav_block}\n"
        "  </header>\n"
        '  <main id="main" tabindex="-1">\n'
        f"{main_html}\n"
        "  </main>\n"
        "  <footer>\n"
        f"    <p>{_esc(i18n.t(lang, 'footer_privacy'))}</p>\n"
        f'    <p class="meta"><a href="/about">{_esc(i18n.t(lang, "nav_about"))}</a> · '
        '<a href="/governance">Governance</a> · '
        '<a href="/how-it-works">How it works</a></p>\n'
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


def _facets_html(records: list[DisclosedRecord]) -> str:
    """Browsable subject/type facets so a topic is reachable, not just a title.

    Each facet value links to a filtered browse (``/?subject=...``), turning the
    careful Dublin Core into a finding aid rather than decoration (user research
    P1-4). Built from already-disclosed records, so a facet never reveals a value
    a viewer may not see.
    """
    blocks: list[str] = []
    for field_name, label in (("subject", "Subjects"), ("type", "Types")):
        items = search.facets(records, field_name)
        if not items:
            continue
        links = "\n".join(
            f'        <li><a href="/?{field_name}={quote(f.value)}">{_esc(f.value)}</a> '
            f'<span class="muted">({f.count})</span></li>'
            for f in items
        )
        blocks.append(
            f'    <section aria-labelledby="facet-{field_name}">\n'
            f'      <h2 id="facet-{field_name}">{label}</h2>\n'
            f'      <ul class="facets">\n{links}\n      </ul>\n'
            "    </section>"
        )
    return "\n".join(blocks)


def _browse_main_html(
    records: list[DisclosedRecord],
    *,
    heading: str,
    query: str = "",
    lang: str = "en",
    all_records: list[DisclosedRecord] | None = None,
) -> str:
    """Compose the browse/search ``<main>``: one ``<h1>``, the form, then both views.

    Renders the list and the table as two complete, equivalent presentations of
    the same records (accessibility — equivalent list and table views), plus
    browsable subject/type facets. Heading order is ``h1`` (page) then ``h2`` (each
    view) then ``h3`` (list items), with no levels skipped (accessibility).
    """
    count = len(records)
    # The empty state distinguishes "no matches" from a permission problem, in
    # plain language (user research T5/P1-3) — without revealing that anything is
    # hidden (the public list simply omits non-listable records).
    if count == 0:
        empty = f'    <p class="empty">{_esc(i18n.t(lang, "empty_no_matches"))}</p>\n'
    else:
        empty = ""
    facets = _facets_html(all_records if all_records is not None else records)
    # The result count and empty state live in a polite status region so a screen
    # reader announces "N record(s) shown" after a search without the user hunting
    # for it — the dynamic status message WCAG 4.1.3 asks for. The region is present
    # on every render so the announcement fires on the results page as it loads.
    return (
        f"    <h1>{_esc(heading)}</h1>\n"
        f"    {_search_form(query)}"
        '    <div class="results-status" role="status" aria-live="polite">\n'
        f'      <p class="count">{count} record(s) shown.</p>\n'
        f"{empty}"
        "    </div>\n"
        '    <section aria-labelledby="list-heading">\n'
        '      <h2 id="list-heading">Records (list view)</h2>\n'
        f"      {_records_list_html(records)}\n"
        "    </section>\n"
        '    <section aria-labelledby="table-heading">\n'
        '      <h2 id="table-heading">Records (table view)</h2>\n'
        f"      {_records_table_html(records)}\n"
        "    </section>\n"
        f"{facets}"
    )


def _record_main_html(
    record: DisclosedRecord, *, proceed: bool, insider: bool = False, lang: str = "en"
) -> str:
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
        # Each tag is glossed in plain language so a non-native or low-literacy
        # reader can actually decide whether to view it (user research T9/P2-1).
        warnings = "\n".join(
            f"      <li>{_esc(i18n.gloss_cw(lang, w))}</li>" for w in record.content_warnings
        )
        proceed_href = f"/record/{quote(rid)}?proceed=1#content"
        # role="alert" + an h1 that IS the warning means a screen reader announces the
        # warning the instant the page loads and lands on it first, instead of a user
        # reading into gated content unawares (user research T13/P1-2; WCAG 4.1.3).
        return (
            '    <section class="interstitial" role="alert" aria-labelledby="cw-heading">\n'
            f'      <h1 id="cw-heading" tabindex="-1">{_esc(i18n.t(lang, "content_warning_heading"))}'
            f": {_esc(record.title)}</h1>\n"
            "      <p>This record carries the following content warnings. Review them "
            "before continuing.</p>\n"
            "      <ul>\n"
            f"{warnings}\n"
            "      </ul>\n"
            f'      <p><a class="proceed" href="{_esc(proceed_href)}">'
            f"{_esc(i18n.t(lang, 'proceed'))}</a></p>\n"
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

    # Payload files the viewer may see — each is a real, fixity-verified download
    # link (user research C4: the filename was previously an inert false affordance).
    if record.payloads:
        files = "\n".join(
            f'      <li><a href="/record/{quote(rid)}/file/{quote(p.filename)}">'
            f"{_esc(p.filename)}</a> "
            f'<span class="muted">({_esc(p.media_type)}, {p.size_bytes} bytes)</span></li>'
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

    # Withheld parts, stated plainly so the partial view is honest. An insider sees
    # each part and WHY (e.g. "sealed until 2030-01-01"); an outsider sees only a
    # count, so the redaction reasons can't be scraped as targeting metadata (P2-2).
    if record.withheld:
        if insider:
            rows = "\n".join(
                f"      <li>{_esc(r.name)} — {_esc(r.reason)}</li>" for r in record.withheld
            )
            body = (
                "      <p>Some parts of this record are not available under your current "
                "access:</p>\n"
                f'      <ul class="withheld-list">\n{rows}\n      </ul>'
            )
        else:
            n = len(record.withheld)
            noun = "detail is" if n == 1 else "details are"
            body = (
                f"      <p>{n} {noun} restricted under your current access. If you are "
                "a community member or steward, sign in to see what is withheld and why.</p>"
            )
        parts.append(
            '    <section aria-labelledby="redactions-heading">\n'
            '      <h2 id="redactions-heading">Withheld</h2>\n'
            f"{body}\n"
            "    </section>"
        )

    # The contributor's front door: act on the promise that consent is revocable
    # (user research P0-2). Shown to everyone — only the claim token (issued at
    # ingest) lets the actual contributor file a request.
    parts.append(
        f'    <p class="consent-link"><a href="/record/{quote(rid)}/consent">'
        "Are you the contributor? Manage or withdraw your consent</a></p>"
    )
    parts.append(f'    <p><a href="/">{_esc(i18n.t(lang, "back_to_records"))}</a></p>')
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
    # A generic Server header with no version, and an empty sys_version, so the
    # response does not advertise the exact runtime to an attacker (user research
    # P2-2: suppress the version side-channel).
    server_version = "ledger"
    sys_version = ""
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

    def _allow_contributions(self) -> bool:
        """Whether the contributor submission surface is enabled on this server.

        Off by default: an existing read-only deployment never grows a write path by
        surprise. A steward opts in explicitly (``serve --allow-contributions``), so
        the closed default is the safe one (least privilege, least surprise).
        """
        return bool(getattr(self.server, "allow_contributions", False))

    def _nav(self) -> str:
        """Site navigation for the current request, including Contribute when enabled."""
        return _nav_html(self._lang(), contribute=self._allow_contributions())

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
        """Negotiate the response language from the viewer's ``Accept-Language``.

        A non-native reader gets localized UI strings and content-warning glosses
        where available, falling back to English (user research P2-1). Negotiation
        is against the languages ledger actually has strings for (``i18n.SUPPORTED``).
        """
        return i18n.negotiate(self.headers.get("Accept-Language"))

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
        self._t0 = time.monotonic()
        parts = urlsplit(self.path)
        path = parts.path
        params = parse_qs(parts.query)
        try:
            if path == "/":
                self._handle_browse(params)
            elif path == "/search":
                self._handle_search(params)
            elif path == "/healthz":
                self._handle_healthz()
            elif path == "/status":
                self._handle_status()
            elif path == "/about":
                self._handle_about()
            elif path == "/governance":
                self._handle_governance()
            elif path == "/how-it-works":
                self._handle_how_it_works()
            elif path == "/proof":
                self._handle_proof()
            elif path == "/oai":
                self._handle_oai(params)
            elif path == "/sitemap.xml":
                self._handle_sitemap()
            elif path == "/steward":
                self._handle_steward_console()
            elif path == "/contribute":
                self._handle_contribute_form()
            elif path.startswith("/record/") and "/file/" in path:
                rid, _, name = path[len("/record/") :].partition("/file/")
                self._handle_file(rid, name)
            elif path.startswith("/record/") and path.endswith("/consent"):
                self._handle_consent_form(path[len("/record/") : -len("/consent")])
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

    def do_POST(self) -> None:
        """Route a POST: the contributor consent form and steward request actions.

        These are the only writes the site accepts. Consent submission is open (a
        claim token, not an account, proves authorship); steward actions are gated
        to a steward grant. Unmatched POSTs 404. Any error renders a safe page.
        """
        self._t0 = time.monotonic()
        path = urlsplit(self.path).path
        try:
            if path == "/contribute":
                self._post_contribute()
            elif path.startswith("/record/") and path.endswith("/consent"):
                self._post_consent(path[len("/record/") : -len("/consent")])
            elif path.startswith("/steward/requests/") and path.endswith("/resolve"):
                rid = path[len("/steward/requests/") : -len("/resolve")]
                self._post_resolve_request(rid)
            else:
                self._handle_not_found()
        except BrokenPipeError:  # pragma: no cover - client disconnected
            pass

    def _read_form(self) -> dict[str, str]:
        """Read and parse a urlencoded POST body into a flat string mapping.

        Bounded by Content-Length; a missing/oversized length yields an empty form
        rather than reading unbounded input (robustness)."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return {}
        if length <= 0 or length > 64 * 1024:
            return {}
        raw = self.rfile.read(length).decode("utf-8", "replace")
        return {k: v[0] for k, v in parse_qs(raw).items() if v}

    def _consent_store(self) -> consent.ConsentRequestStore:
        return consent.ConsentRequestStore(self._archive().logs_dir / "consent-requests.json")

    def _post_consent(self, raw_id: str) -> None:
        """``POST /record/{id}/consent`` — file a contributor consent request.

        The record must be listable to the viewer (else a neutral 404 that never
        confirms a sealed record exists). A claim token issued at ingest is verified
        when a server claim secret is configured (LEDGER_CLAIM_SECRET); the request
        is queued for a steward either way and no automatic action is taken. The
        contributor's message is stored for the steward but never logged or echoed
        in an error (no-outing rule)."""
        record_id = _decode_id(raw_id)
        grant = self._resolve_grant()
        lang = self._lang()
        try:
            self._archive().disclose(record_id, grant)
        except (AccessDenied, ObjectNotFound):
            self._handle_not_found()
            return
        form = self._read_form()
        kind = form.get("kind", "")
        if kind not in consent.VALID_KINDS:
            self._send_html(
                400,
                _page(
                    "Invalid request",
                    lang=lang,
                    main_html=_error_main_html("Invalid request", "Please choose a valid action."),
                    nav_html=self._nav(),
                ),
            )
            return
        secret = os.environ.get("LEDGER_CLAIM_SECRET", "").encode("utf-8")
        verified = bool(secret) and consent.verify_claim_token(
            record_id, form.get("claim", ""), secret
        )
        note = "" if not secret else (" (verified)" if verified else " (claim token not verified)")
        req = consent.ConsentRequest(
            record_id=record_id, kind=kind, message=form.get("message", "")
        )
        self._consent_store().add(req)
        rt = self._archive().config.consent_response_time or "A steward will review your request."
        main_html = (
            "    <h1>Request received</h1>\n"
            f"    <p>Your request to {_esc(kind)} this record has been recorded{_esc(note)}. "
            f"A steward will review it. {_esc(rt)}</p>\n"
            f"    <p>Your reference is <code>{_esc(req.request_id)}</code>.</p>\n"
            '    <p><a href="/">Back to all records</a></p>'
        )
        self._send_html(
            200, _page("Request received", lang=lang, main_html=main_html, nav_html=self._nav())
        )

    def _post_resolve_request(self, raw_id: str) -> None:
        """``POST /steward/requests/{id}/resolve`` — a steward closes a request."""
        grant = self._resolve_grant()
        if not grant.is_steward:
            self._handle_not_found()
            return
        status = self._read_form().get("status", "acknowledged")
        if status not in consent.VALID_STATUSES:
            status = "acknowledged"
        self._consent_store().resolve(_decode_id(raw_id), status)
        self.send_response(303)
        self.send_header("Location", "/steward")
        self.end_headers()

    def _handle_steward_console(self) -> None:
        """``GET /steward`` — a steward's accountable console (gated).

        Shows the open consent/takedown requests a steward must act on, and is
        candid that some material may be sealed above the steward's own access
        (user research P1-5/T7). Actioning consent changes and takedowns is done
        with the audited CLI (``ledger policy`` / ``takedown`` / ``cw``); this
        console lets a steward see and close incoming requests."""
        grant = self._resolve_grant()
        lang = self._lang()
        if not grant.is_steward:
            self._handle_not_found()
            return
        open_reqs = self._consent_store().open_requests()
        if open_reqs:
            rows = "\n".join(
                "      <li>\n"
                f"        <strong>{_esc(r.kind)}</strong> on record "
                f'<a href="/record/{quote(r.record_id)}">{_esc(r.record_id)}</a> '
                f'<span class="muted">({_esc(r.created_at)}, ref {_esc(r.request_id)})</span>\n'
                f'        <form method="post" action="/steward/requests/{quote(r.request_id)}/resolve">\n'
                '          <input type="hidden" name="status" value="resolved">\n'
                '          <button type="submit">Mark resolved</button>\n'
                "        </form>\n"
                "      </li>"
                for r in open_reqs
            )
            requests_html = f'    <ul class="requests">\n{rows}\n    </ul>'
        else:
            requests_html = "    <p>No open requests.</p>"
        main_html = (
            "    <h1>Steward console</h1>\n"
            '    <section aria-labelledby="req-heading">\n'
            '      <h2 id="req-heading">Open consent &amp; takedown requests</h2>\n'
            f"{requests_html}\n"
            "    </section>\n"
            '    <section aria-labelledby="note-heading">\n'
            '      <h2 id="note-heading">Before you act</h2>\n'
            "      <p>You can read access-restricted content to do your work, but content "
            "sealed with the 'sealed' policy — and every contributor's identity — is "
            "restricted even from you. Some records may be sealed above your access; their "
            "absence here does not mean they do not exist.</p>\n"
            "      <p>Action a request with the audited CLI: <code>ledger policy</code> "
            "(change access), <code>ledger takedown</code>, or <code>ledger cw</code> "
            "(add a content warning) — each records who acted and why.</p>\n"
            "    </section>"
        )
        self._send_html(
            200, _page("Steward console", lang=lang, main_html=main_html, nav_html=self._nav())
        )

    # --- HTML routes --------------------------------------------------------

    def _handle_browse(self, params: dict[str, list[str]]) -> None:
        """``GET /`` — the accessible browse page (list + table equivalents).

        Supports faceted browse: ``?subject=`` / ``?type=`` filter by a Dublin Core
        facet so a topic is reachable, not just an exact title (user research P1-4).
        """
        lang = self._lang()
        grant = self._resolve_grant()
        records = self._archive().browse(grant)
        facet_field, facet_value = self._facet_from(params)
        if facet_field and facet_value:
            records = search.filter_by_facet(records, facet_field, facet_value)
            heading = f"{facet_field.capitalize()}: {facet_value}"
        else:
            heading = "Browse the archive"
        main_html = _browse_main_html(
            records, heading=heading, lang=lang, all_records=self._all_for_facets(grant)
        )
        self._send_html(200, _page("Browse", lang=lang, main_html=main_html, nav_html=self._nav()))

    @staticmethod
    def _facet_from(params: dict[str, list[str]]) -> tuple[str, str]:
        for fld in ("subject", "type"):
            if params.get(fld):
                return fld, params[fld][0]
        return "", ""

    def _all_for_facets(self, grant: Grant) -> list[DisclosedRecord]:
        return self._archive().browse(grant)

    def _handle_search(self, params: dict[str, list[str]]) -> None:
        """``GET /search?q=`` — search disclosed records over their Dublin Core.

        Search runs over already-disclosed records (so it can never surface a field
        the grant may not see) and now indexes subjects, descriptions, and types —
        not just titles — so a topic search actually finds records (user research
        P1-4). A non-Latin query shows a plain hint that search is English-biased.
        """
        lang = self._lang()
        grant = self._resolve_grant()
        query = (params.get("q", [""])[0]).strip()
        disclosed = self._archive().browse(grant)
        matched = search.search(disclosed, query)
        heading = f"Search results for “{query}”" if query else "Search"
        hint = (
            '<p class="hint">Search currently matches Latin-script text; results may '
            "be incomplete for other scripts.</p>"
            if query and search.looks_non_latin(query)
            else ""
        )
        main_html = hint + _browse_main_html(
            matched, heading=heading, query=query, lang=lang, all_records=disclosed
        )
        self._send_html(
            200,
            _page(
                f"Search — {query}" if query else "Search",
                lang=lang,
                main_html=main_html,
                nav_html=self._nav(),
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
        main_html = _record_main_html(
            record, proceed=proceed, insider=_is_insider(grant), lang=self._lang()
        )
        self._send_html(
            200,
            _page(
                record.title,
                lang=self._lang(),
                main_html=main_html,
                nav_html=self._nav(),
            ),
        )

    # A response-time floor for neutral 404s. A record that EXISTS but is not
    # listable returns 404 only after disclose() reads its manifest; a nonexistent
    # id returns 404 after a quick miss. Holding every neutral 404 to a fixed
    # minimum makes the two indistinguishable by timing for normal-sized records,
    # so an observer cannot confirm a sealed record exists by how fast it is denied
    # (user research P2-2). Best-effort, not a cryptographic guarantee for very
    # large manifests; the threaded server keeps the wait from blocking others.
    _NOT_FOUND_FLOOR_S = 0.02

    def _floor_not_found(self) -> None:
        elapsed = time.monotonic() - getattr(self, "_t0", time.monotonic())
        remaining = self._NOT_FOUND_FLOOR_S - elapsed
        if remaining > 0:
            time.sleep(remaining)

    def _handle_not_found(self) -> None:
        """Render the shared, neutral 404 page (reveals nothing about existence)."""
        self._floor_not_found()
        main_html = _error_main_html(
            "Not found",
            "We could not find anything at that address, or it is not available to you.",
        )
        self._send_html(
            404,
            _page(
                "Not found",
                lang=self._lang(),
                main_html=main_html,
                nav_html=self._nav(),
            ),
        )

    # --- JSON routes (same disclosure gate) ---------------------------------

    def _handle_api_records(self) -> None:
        """``GET /api/records`` — JSON of every listable record's disclosed shape."""
        grant = self._resolve_grant()
        records = self._archive().browse(grant)
        reasons = _is_insider(grant)
        self._send_json(200, {"records": [r.to_dict(withheld_reasons=reasons) for r in records]})

    def _handle_api_record(self, raw_id: str) -> None:
        """``GET /api/record/{id}`` — JSON of one record's disclosed shape."""
        record_id = _decode_id(raw_id)
        grant = self._resolve_grant()
        try:
            record = self._archive().disclose(record_id, grant)
        except (AccessDenied, ObjectNotFound):
            self._floor_not_found()  # equalize not-found vs not-authorized timing
            self._send_json(404, {"error": "not found"})
            return
        self._send_json(200, record.to_dict(withheld_reasons=_is_insider(grant)))

    # --- contributor submission (opt-in write path) -------------------------

    def _handle_contribute_form(self, *, error: str | None = None, status: int = 200) -> None:
        """``GET /contribute`` — the accessible contribution form, when enabled.

        Returns a neutral 404 when the submission surface is off, so a read-only
        deployment never advertises a write path (least privilege). The form itself
        is plain about review-before-publish and sealed contact (no-outing rule)."""
        if not self._allow_contributions():
            self._handle_not_found()
            return
        lang = self._lang()
        main_html = contribute.render_contribute_main(self._archive().config, error=error)
        self._send_html(
            status, _page("Contribute", lang=lang, main_html=main_html, nav_html=self._nav())
        )

    def _post_contribute(self) -> None:
        """``POST /contribute`` — accept a submission through the one ingest path.

        The submission is built into a *sealed-pending* record (nothing is published
        by inaction) and any contributor contact is sealed into the identity vault by
        :meth:`Archive.ingest`; the confirmation page echoes back none of it (the
        no-outing rule, by construction). Invalid input re-renders the form with a
        generic message and a 400; a sealing failure (e.g. a missing vault key)
        renders a safe error that names no submitted value."""
        if not self._allow_contributions():
            self._handle_not_found()
            return
        form = self._read_form()
        try:
            submission = contribute.parse_submission(form, self._archive().config)
        except LedgerError as exc:
            self._handle_contribute_form(error=str(exc), status=400)
            return
        try:
            self._archive().ingest(
                {},
                submission.record,
                identity=submission.identity,
                agent="contribution",
                now=now_iso(),
            )
        except LedgerError:
            # Name nothing the contributor submitted; just decline cleanly.
            self._handle_contribute_form(
                error="Your contribution could not be saved right now. Please try again.",
                status=503,
            )
            return
        lang = self._lang()
        self._send_html(
            200,
            _page(
                "Thank you",
                lang=lang,
                main_html=contribute.render_thanks_main(),
                nav_html=self._nav(),
            ),
        )

    # --- health -------------------------------------------------------------

    def _handle_healthz(self) -> None:
        """``GET /healthz`` — JSON health, with counts gated to stewards.

        An outsider gets only ``status`` and an ``all_verified`` boolean. The
        absolute counts (bags audited / files checked) include sealed and
        community records, so revealing them to the public would leak the TOTAL
        size of the archive — letting an observer learn that sealed records exist
        and poll for when one is added (user research P2-2). Only a steward grant
        sees the numbers; a monitor uses a provisioned grant. No path, digest, id,
        or identity ever appears (no-outing rule).
        """
        archive = self._archive()
        grant = self._resolve_grant()
        # Structural readiness first: a liveness probe must fail when the store or
        # vault is unreachable, not just when a checksum drifts. The reason code is
        # generic infrastructure state — never a path, id, or identity (no-outing).
        ready, reason = archive.check_readiness()
        if not ready:
            self._send_json(
                503, {"status": "degraded", "all_verified": False, "ready": False, "reason": reason}
            )
            return
        try:
            reports = archive.audit_fixity()
        except LedgerError:
            self._send_json(503, {"status": "degraded", "all_verified": False, "ready": True})
            return
        passed = sum(1 for _name, r in reports if r.ok)
        failed = len(reports) - passed
        status = "ok" if failed == 0 else "degraded"
        code = 200 if failed == 0 else 503
        body: dict[str, object] = {
            "status": status,
            "all_verified": failed == 0,
            "ready": True,
        }
        if grant.is_steward:
            body["fixity"] = {
                "bags_audited": len(reports),
                "bags_passed": passed,
                "bags_failed": failed,
                "files_checked": sum(r.checked for _name, r in reports),
            }
        self._send_json(code, body)

    def _handle_status(self) -> None:
        """``GET /status`` — a human-readable health page (not raw JSON).

        The old "Status" nav target served raw JSON, which alarmed non-technical
        users ("have I broken it?") and was an unreadable wall of punctuation to a
        screen reader (user research P1-1). This renders the same fixity summary as
        a plain, accessible page; the JSON stays at ``/healthz`` for monitors.
        """
        lang = self._lang()
        archive = self._archive()
        grant = self._resolve_grant()
        try:
            reports = archive.audit_fixity()
            passed = sum(1 for _n, r in reports if r.ok)
            total = len(reports)
            files = sum(r.checked for _n, r in reports)
            healthy = passed == total
            headline = (
                "Everything is healthy." if healthy else "Some records need a steward's attention."
            )
            # Absolute counts include sealed records, so the exact numbers are shown
            # only to a steward; everyone else gets the qualitative headline (P2-2).
            if grant.is_steward and total:
                detail = (
                    f"{passed} of {total} record package(s) passed every integrity check "
                    f"({files} file checksum(s) verified)."
                )
            elif healthy:
                detail = "Every stored record passed its most recent integrity check."
            else:
                detail = "One or more records did not pass their integrity check."
        except LedgerError:
            headline, detail = "Status check failed.", "An integrity check could not be completed."
        main_html = (
            f"    <h1>Archive status</h1>\n"
            f"    <p><strong>{_esc(headline)}</strong></p>\n"
            f"    <p>{_esc(detail)}</p>\n"
            '    <p class="muted">Machine-readable health is at '
            '<a href="/healthz">/healthz</a>.</p>'
        )
        self._send_html(200, _page("Status", lang=lang, main_html=main_html, nav_html=self._nav()))

    # --- plain-language safety surface (user research P0-4) -----------------

    def _info_page(self, title: str, heading: str, paragraphs: list[str]) -> None:
        lang = self._lang()
        body = "\n".join(f"    <p>{_esc(p)}</p>" for p in paragraphs if p)
        main_html = f"    <h1>{_esc(heading)}</h1>\n{body}"
        self._send_html(200, _page(title, lang=lang, main_html=main_html, nav_html=self._nav()))

    def _handle_about(self) -> None:
        """``GET /about`` — who runs the archive and how it protects people."""
        cfg = self._archive().config
        self._info_page(
            "About",
            f"About {cfg.archive_name}",
            [
                cfg.about,
                "Who runs this archive: " + cfg.operators if cfg.operators else "",
                "How to reach us: " + cfg.contact if cfg.contact else "",
            ],
        )

    def _handle_governance(self) -> None:
        """``GET /governance`` — how stewards are chosen and held accountable."""
        cfg = self._archive().config
        self._info_page(
            "Governance",
            "Governance",
            [
                cfg.steward_vetting,
                "Stewards can read access-restricted content in order to do their work, "
                "but they can never see a contributor's sealed identity, and content "
                "sealed with the 'sealed' policy is restricted from everyone — including "
                "stewards. Every steward action records who acted and why.",
                "Consent and takedown requests: " + cfg.consent_response_time
                if cfg.consent_response_time
                else "",
            ],
        )

    def _handle_how_it_works(self) -> None:
        """``GET /how-it-works`` — plain-language explanation + how to contribute."""
        self._info_page(
            "How it works",
            "How this protects you, and how to contribute",
            [
                "You can publish a story while sealing the names, the location, or your "
                "own identity. Sealed parts are shown to you as 'withheld', never exposed.",
                "Your identity as a contributor is stored separately and encrypted, and is "
                "shown on no page here — not even to a steward — unless you explicitly grant it.",
                "You stay in control: from any record you can ask a steward to tighten access "
                "or take it down (see the 'Manage or withdraw consent' link on each record).",
                "Contributing currently happens with a steward's help so your choices about "
                "what to seal are made deliberately. See the proof that we keep these promises "
                "at /proof.",
            ],
        )

    def _handle_proof(self) -> None:
        """``GET /proof`` — explain the verifiable no-outing guarantee (show, don't tell)."""
        self._info_page(
            "Our promise, proven",
            "We prove the promise, we don't just state it",
            [
                "The claim 'contributor identities are never shown here' is not an honour-system "
                "promise — it is a test the software must pass on every build.",
                "A contributor's identity is stored only as an opaque token plus encrypted data "
                "in a separate vault. The record a page is built from has no place to put an "
                "identity, so there is nothing to leak.",
                "The project's audit ingests a sentinel identity and then checks that it appears "
                "on no page, in no data file, in no backup, and in no log — and that a sealed "
                "record cannot even be confirmed to exist by an outsider.",
            ],
        )

    # --- content retrieval (user research P0-4 / C4) -----------------------

    def _handle_file(self, raw_id: str, raw_name: str) -> None:
        """``GET /record/{id}/file/{name}`` — download a permitted payload file.

        Access is enforced by disclosure: only a payload present in the viewer's
        DisclosedRecord (i.e. one their grant may see) can be fetched; anything else
        is an indistinguishable 404. Bytes are read from the content-addressed store
        by their address, so what is served is fixity-verified by construction
        (integrity). Records were previously unreadable — the filename was an inert
        false affordance (user research C4)."""
        record_id = _decode_id(raw_id)
        filename = _decode_id(raw_name)
        grant = self._resolve_grant()
        try:
            record = self._archive().disclose(record_id, grant)
        except (AccessDenied, ObjectNotFound):
            self._handle_not_found()
            return
        payload = next((p for p in record.payloads if p.filename == filename), None)
        if payload is None:
            self._handle_not_found()
            return
        try:
            data = self._archive().store.read_bytes(payload.address)
        except (ObjectNotFound, LedgerError):
            self._handle_not_found()
            return
        self.send_response(200)
        self.send_header("Content-Type", payload.media_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Disposition", f'inline; filename="{_safe_filename(filename)}"')
        self.send_header("Content-Security-Policy", "default-src 'none'; sandbox")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    # --- consent (user research P0-2): the contributor's front door ---------

    def _handle_consent_form(self, raw_id: str) -> None:
        """``GET /record/{id}/consent`` — the contributor's consent/withdrawal form.

        Lets the *contributor* (not only a steward) act on the promise that consent
        is revocable. Submitting requires a claim token issued at ingest, proving
        authorship without an account. The record must at least be listable to the
        viewer or this is a neutral 404 (it never confirms a sealed record exists).
        """
        record_id = _decode_id(raw_id)
        grant = self._resolve_grant()
        lang = self._lang()
        try:
            self._archive().disclose(record_id, grant)
        except (AccessDenied, ObjectNotFound):
            self._handle_not_found()
            return
        cfg = self._archive().config
        rt = cfg.consent_response_time or "We will respond as soon as we can."
        main_html = (
            "    <h1>Manage or withdraw your consent</h1>\n"
            "    <p>If you contributed this record, you can ask a steward to tighten its "
            "access, correct it, or take it down. You will need the claim token you were "
            "given when you contributed.</p>\n"
            f"    <p>{_esc(rt)}</p>\n"
            f'    <form method="post" action="/record/{quote(record_id)}/consent">\n'
            '      <p><label for="kind">What would you like to do?</label><br>\n'
            '      <select id="kind" name="kind">\n'
            '        <option value="withdraw">Take this record down</option>\n'
            '        <option value="tighten">Tighten who can see it</option>\n'
            '        <option value="correct">Correct something</option>\n'
            '        <option value="contact">Contact a steward</option>\n'
            "      </select></p>\n"
            '      <p><label for="claim">Your claim token</label><br>\n'
            '      <input id="claim" name="claim" type="text" autocomplete="off" required></p>\n'
            '      <p><label for="message">Message (optional)</label><br>\n'
            '      <textarea id="message" name="message" rows="4"></textarea></p>\n'
            '      <p><button type="submit">Send request</button></p>\n'
            "    </form>"
        )
        self._send_html(
            200, _page("Manage consent", lang=lang, main_html=main_html, nav_html=self._nav())
        )

    # --- interoperability (user research P2-3): OAI-PMH + sitemap ----------

    def _public_records(self) -> list[DisclosedRecord]:
        """Records disclosed to the anonymous public — the only set harvest exposes."""
        return self._archive().browse(anonymous())

    def _base_url(self) -> str:
        host = self.headers.get("Host", "localhost")
        return f"http://{host}"

    def _handle_oai(self, params: dict[str, list[str]]) -> None:
        """``GET /oai`` — a minimal OAI-PMH provider over public records only."""
        cfg = self._archive().config
        flat = {k: v[0] for k, v in params.items() if v}
        status, xml = oai.oai_response(
            flat.get("verb", ""),
            flat,
            records=self._public_records(),
            archive_name=cfg.archive_name,
            base_url=self._base_url() + "/oai",
            admin_email=cfg.contact or "",
            now=now_iso(),
        )
        self._send(status, xml.encode("utf-8"), "text/xml; charset=utf-8")

    def _handle_sitemap(self) -> None:
        """``GET /sitemap.xml`` — public record URLs for crawlers (discoverability)."""
        ids = [r.record_id for r in self._public_records()]
        xml = oai.sitemap_xml(ids, self._base_url())
        self._send(200, xml.encode("utf-8"), "application/xml; charset=utf-8")

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
        raw = candidate.read_bytes()
        # Cache + conditional GET so a metered, intermittent mobile connection does
        # not re-download the stylesheet every visit (user research P3-1). The ETag
        # is a content hash, so it changes only when the file does.
        etag = '"' + hashlib.sha256(raw).hexdigest()[:16] + '"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            return
        accepts_gzip = "gzip" in (self.headers.get("Accept-Encoding") or "")
        body = gzip.compress(raw) if accepts_gzip else raw
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "public, max-age=86400")
        self.send_header("ETag", etag)
        if accepts_gzip:
            self.send_header("Content-Encoding", "gzip")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


# --- module-level render helpers (shared by routes) -------------------------


def _nav_html(lang: str = "en", *, contribute: bool = False) -> str:
    """The site navigation: descriptive links only, no positive tabindex.

    Labels are localized (i18n), and the "Status" link points at the human-readable
    ``/status`` page rather than the raw-JSON ``/healthz`` endpoint, which alarmed
    non-technical users and was unreadable to a screen reader (user research P1-1).
    The Contribute link appears only when the submission surface is enabled on the
    server, so a read-only deployment never advertises a write path it does not have.
    """
    contribute_link = '      <a href="/contribute">Contribute</a>\n' if contribute else ""
    return (
        f'\n      <a href="/">{_esc(i18n.t(lang, "nav_browse"))}</a>\n'
        f'      <a href="/search">{_esc(i18n.t(lang, "nav_search"))}</a>\n'
        f'      <a href="/about">{_esc(i18n.t(lang, "nav_about"))}</a>\n'
        f'      <a href="/status">{_esc(i18n.t(lang, "nav_status"))}</a>\n'
        f"{contribute_link}    "
    )


def _safe_filename(name: str) -> str:
    """A filename safe to place in a ``Content-Disposition`` header.

    Strips path separators, quotes, and control characters so a crafted payload
    filename cannot inject a header or escape the field (securability)."""
    cleaned = "".join(c for c in name if c.isprintable() and c not in '"\\/\r\n')
    return cleaned or "file"


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
    allow_contributions: bool = False,
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
    # Threaded so the per-request response-time floor (which equalizes the timing of
    # a not-found vs a not-authorized record) never serializes other requests
    # (availability, responsiveness) — and so the site serves several readers at once.
    httpd = http.server.ThreadingHTTPServer((host, port), ArchiveRequestHandler)
    # Attach the dependencies the handler reads per request.
    httpd.archive = archive  # type: ignore[attr-defined]
    httpd.grants = grants  # type: ignore[attr-defined]
    httpd.allow_contributions = allow_contributions  # type: ignore[attr-defined]
    return httpd


def serve(
    archive: Archive,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    grants_path: Path | None = None,
    allow_contributions: bool = False,
) -> None:
    """Build and run the browse server until interrupted (blocking).

    A convenience over :func:`make_server` for a CLI or a ``python -m`` entry
    point. Binds to loopback by default (securability) and shuts down cleanly on
    ``KeyboardInterrupt`` so a steward can stop it without a traceback (usability).
    """
    httpd = make_server(
        archive,
        host=host,
        port=port,
        grants_path=grants_path,
        allow_contributions=allow_contributions,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover - interactive shutdown
        pass
    finally:
        httpd.server_close()
