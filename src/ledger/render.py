"""Pure HTML rendering for the browse/search server.

Extracted from :mod:`ledger.server` so the server module is the request/routing
layer and this module is the *rendering* layer — the auditable text-to-HTML
surface. Every function here is pure (no I/O, no request state): it turns a
:class:`~ledger.models.DisclosedRecord` (the safe shape, which structurally cannot
carry a contributor identity) into markup. `_esc` is the single text-to-HTML
boundary, so escaping cannot be forgotten per call site (security — no XSS, by
construction). The server imports these names, so they remain reachable as
``ledger.server`` attributes for callers that already reference them.
"""

from __future__ import annotations

import html
from collections.abc import Iterable
from urllib.parse import quote

from ledger import i18n, search
from ledger.models import AccessPolicy, DisclosedRecord, Grant, PayloadFile

# The site's one stylesheet, linked from every page.
_STYLESHEET_HREF: str = "/static/app.css"


def _is_insider(grant: Grant) -> bool:
    """Whether a viewer is a trusted insider (community member or steward).

    An insider is shown *why* each part is withheld (honesty, P1-3); an outsider
    gets only a count, so the set of redaction reasons cannot be scraped as
    targeting metadata about what a record hides (P2-2)."""
    return grant.is_steward or AccessPolicy.COMMUNITY in grant.levels


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


def _payload_li(rid: str, p: PayloadFile) -> str:
    """One payload list item: the download link plus its transcript, if any.

    A transcript/caption is surfaced in a ``<details>`` so audio or video content is
    available to a Deaf or hard-of-hearing reader and to anyone on a silent or slow
    connection (user research H3). An audio/video payload with *no* transcript is
    marked as such, so a missing transcript is visible rather than silent."""
    base = (
        f'      <li><a href="/record/{quote(rid)}/file/{quote(p.filename)}">'
        f"{_esc(p.filename)}</a> "
        f'<span class="muted">({_esc(p.media_type)}, {p.size_bytes} bytes)</span>'
    )
    if p.transcript:
        base += (
            '\n        <details class="transcript">\n'
            "          <summary>Transcript</summary>\n"
            f"          <p>{_esc(p.transcript)}</p>\n"
            "        </details>"
        )
    elif p.media_type.startswith(("audio/", "video/")):
        base += '\n        <p class="muted">No transcript provided for this audio/video.</p>'
    return base + "</li>"


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
        files = "\n".join(_payload_li(rid, p) for p in record.payloads)
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
    # A person *named* in a record they did not contribute can object (user research
    # B3 — subjects have agency too, not only the contributor).
    parts.append(
        f'    <p class="object-link"><a href="/record/{quote(rid)}/object">'
        "Are you named in this record and object to it? Tell a steward</a></p>"
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
