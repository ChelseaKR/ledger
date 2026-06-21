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
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

from ledger import i18n, pagination, search
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
        '  <link rel="alternate" type="application/atom+xml" title="Recently published '
        'records" href="/feed.atom">\n'
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


def _search_form(
    query: str = "", *, lang: str = "en", active_facets: list[tuple[str, str]] | None = None
) -> str:
    """Render the search form with a programmatically associated label.

    The ``<label for>`` is tied to the input's ``id`` so assistive technology
    announces the field's purpose, and the current ``query`` is escaped back into
    ``value`` so a search term cannot inject markup (accessibility, security). The
    label and button are localized (user research I2). Any ``active_facets`` ride
    along as hidden inputs, so submitting a search *keeps* the current facet filters
    — search and faceted browse compose rather than replacing each other."""
    hidden = "".join(
        f'  <input type="hidden" name="{_esc(field)}" value="{_esc(value)}">\n'
        for field, value in (active_facets or [])
    )
    return (
        '<form class="search" role="search" method="get" action="/search">\n'
        f'  <label for="q">{_esc(i18n.t(lang, "search_label"))}</label>\n'
        f'  <input id="q" name="q" type="search" value="{_esc(query)}" '
        'autocomplete="off">\n'
        f"{hidden}"
        f'  <button type="submit">{_esc(i18n.t(lang, "search_button"))}</button>\n'
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


def _result_detail(record: DisclosedRecord, query: str) -> str:
    """The per-record detail line, escaped and ready to interpolate.

    When the viewer is searching and a query term appears in the record's disclosed
    text, this is a highlighted match snippet (``<mark>`` around each hit) showing
    *why* the record matched (user research E3); otherwise it is the plain Dublin
    Core summary. Every text piece passes through :func:`_esc`, so escaping is never
    skipped even though the snippet interleaves literal ``<mark>`` tags.
    """
    if query.strip():
        snip = search.snippet(record, query)
        if snip is not None:
            return "".join(
                f"<mark>{_esc(text)}</mark>" if matched else _esc(text)
                for text, matched in snip.runs
            )
    return _esc(_summary_text(record))


def _records_list_html(
    records: Iterable[DisclosedRecord], *, query: str = "", lang: str = "en"
) -> str:
    """Render the records as a semantic list — one accessible equivalent view.

    The list and the table (:func:`_records_table_html`) present the same data in
    two equally complete forms, so a user of either a screen reader or a small
    screen gets the full content (accessibility — documented non-visual
    equivalent). Link text is the record title (descriptive links, never "click
    here"). When ``query`` is set, each item shows a highlighted match snippet
    instead of the generic summary (user research E3). User-facing chrome (the
    content-warning badge, the empty note) is localized. All text is escaped (security).
    """
    badge = _esc(i18n.t(lang, "content_warning_heading"))
    items: list[str] = []
    for record in records:
        detail = _result_detail(record, query)
        warn = f' <span class="badge">{badge}</span>' if record.content_warnings else ""
        summary_html = f'<p class="result-detail">{detail}</p>' if detail else ""
        items.append(
            "    <li>\n"
            f'      <h3><a href="/record/{quote(record.record_id)}">'
            f"{_esc(record.title)}</a>{warn}</h3>\n"
            f"      {summary_html}\n"
            "    </li>"
        )
    if not items:
        return f'<p class="empty">{_esc(i18n.t(lang, "no_records_available"))}</p>'
    body = "\n".join(items)
    return f'<ul class="record-list">\n{body}\n</ul>'


def _records_table_html(
    records: Iterable[DisclosedRecord], *, query: str = "", lang: str = "en"
) -> str:
    """Render the records as a data table — the documented non-visual equivalent.

    The table carries a ``<caption>`` describing its purpose and ``<th scope>`` on
    every header so assistive technology can associate each cell with its column
    (accessibility). The content-warning column uses the literal word, never a
    colour or icon alone, so the signal survives for colour-blind and
    text-only users (accessibility — colour is not the only signal). When ``query``
    is set the summary cell shows the same highlighted match snippet as the list, so
    the two views stay equivalent (user research E3). Caption, headers, and the
    yes/no signal are localized. All cells are escaped (security).
    """
    yes, no = _esc(i18n.t(lang, "answer_yes")), _esc(i18n.t(lang, "answer_no"))
    rows: list[str] = []
    for record in records:
        warn = yes if record.content_warnings else no
        detail = _result_detail(record, query)
        rows.append(
            "      <tr>\n"
            f'        <td><a href="/record/{quote(record.record_id)}">'
            f"{_esc(record.title)}</a></td>\n"
            f"        <td>{detail}</td>\n"
            f"        <td>{warn}</td>\n"
            "      </tr>"
        )
    empty_cell = _esc(i18n.t(lang, "no_records_available"))
    body = "\n".join(rows) if rows else (f'      <tr><td colspan="3">{empty_cell}</td></tr>')
    return (
        '<table class="record-table">\n'
        f"  <caption>{_esc(i18n.t(lang, 'table_caption'))}</caption>\n"
        "  <thead>\n"
        "    <tr>\n"
        f'      <th scope="col">{_esc(i18n.t(lang, "col_title"))}</th>\n'
        f'      <th scope="col">{_esc(i18n.t(lang, "col_summary"))}</th>\n'
        f'      <th scope="col">{_esc(i18n.t(lang, "content_warning_heading"))}</th>\n'
        "    </tr>\n"
        "  </thead>\n"
        "  <tbody>\n"
        f"{body}\n"
        "  </tbody>\n"
        "</table>"
    )


def _facet_href(current_path: str, field: str, value: str, *, active: bool) -> str:
    """The browse URL that toggles facet ``field=value`` on the current path.

    Preserves the existing query (the search term ``q`` and any *other* active
    facets) and only ever touches ``field``: an inactive value is *set* (replacing any
    prior value of that field), an active one is *removed* (toggled off). ``page`` is
    always dropped so toggling a filter resets to the first page. This is what makes
    search and facets compose — clicking a facet narrows the current results rather
    than starting a fresh browse."""
    split = urlsplit(current_path)
    kept = [(k, v) for k, v in parse_qsl(split.query) if k != "page"]
    if active:
        kept = [(k, v) for k, v in kept if not (k == field and v == value)]
    else:
        kept = [(k, v) for k, v in kept if k != field]
        kept.append((field, value))
    path = split.path or "/"
    return path + ("?" + urlencode(kept) if kept else "")


def _facets_html(
    records: list[DisclosedRecord],
    *,
    current_path: str = "/",
    active: list[tuple[str, str]] | None = None,
    lang: str = "en",
) -> str:
    """Browsable subject/type/language facets that compose with search.

    Each facet value links (via :func:`_facet_href`) to the current view *narrowed* by
    that value, keeping the search term and other facets, turning the careful Dublin
    Core into a finding aid rather than decoration (user research P1-4). An already-
    active value is marked ``aria-current`` and its link removes it (a toggle). Built
    from the already-disclosed, already-matched ``records``, so a facet never reveals a
    value a viewer may not see and the counts describe the current results.
    """
    active_set = set(active or [])
    blocks: list[str] = []
    for field_name, label_key in (
        ("subject", "facet_subjects"),
        ("type", "facet_types"),
        ("language", "facet_languages"),
    ):
        items = search.facets(records, field_name)
        if not items:
            continue
        rows: list[str] = []
        for f in items:
            is_active = (field_name, f.value) in active_set
            href = _facet_href(current_path, field_name, f.value, active=is_active)
            mark = ' aria-current="true"' if is_active else ""
            rows.append(
                f'        <li><a href="{_esc(href)}"{mark}>{_esc(f.value)}</a> '
                f'<span class="muted">({f.count})</span></li>'
            )
        blocks.append(
            f'    <section aria-labelledby="facet-{field_name}">\n'
            f'      <h2 id="facet-{field_name}">{_esc(i18n.t(lang, label_key))}</h2>\n'
            f'      <ul class="facets">\n{chr(10).join(rows)}\n      </ul>\n'
            "    </section>"
        )
    return "\n".join(blocks)


def _pager_html(
    page: pagination.Page[DisclosedRecord], current_path: str, *, lang: str = "en"
) -> str:
    """An accessible Previous/Next pager that preserves the current query.

    Rendered as a labelled ``<nav>`` so assistive tech announces it as a distinct
    navigation landmark, with a plain "Page X of Y" so a reader always knows where
    they are. Each link reuses the current path and query (facet, search term,
    language) with only ``page`` swapped, so paging never drops a filter. The label
    and link text are localized. Returns an empty string when everything fits on one
    page — no pager clutter when unneeded.
    """
    if page.pages <= 1:
        return ""
    split = urlsplit(current_path)
    kept = [(k, v) for k, v in parse_qsl(split.query) if k != "page"]

    def href(number: int) -> str:
        return (split.path or "/") + "?" + urlencode([*kept, ("page", str(number))])

    prev_label = _esc(i18n.t(lang, "pager_prev"))
    next_label = _esc(i18n.t(lang, "pager_next"))
    position = _esc(i18n.t(lang, "pager_position", number=page.number, pages=page.pages))
    prev_html = (
        f'<a rel="prev" href="{_esc(href(page.number - 1))}">{prev_label}</a>'
        if page.has_prev
        else ""
    )
    next_html = (
        f'<a rel="next" href="{_esc(href(page.number + 1))}">{next_label}</a>'
        if page.has_next
        else ""
    )
    return (
        f'    <nav class="pager" aria-label="{_esc(i18n.t(lang, "pager_label"))}">\n'
        f"{'      ' + prev_html + chr(10) if prev_html else ''}"
        f"      <span>{position}</span>\n"
        f"{'      ' + next_html + chr(10) if next_html else ''}"
        "    </nav>\n"
    )


def _sort_html(current_path: str, *, query: str, sort: str, lang: str) -> str:
    """A small sort control: order results by relevance (search only), newest, oldest.

    Rendered as a labelled group of links built from the current path (preserving the
    query and facets, dropping ``page``), so changing the order never drops a filter —
    sort composes with search and facets like every other control. The active order is
    plain ``aria-current`` text, not a link. "Relevance" appears only with a query (it
    is the natural default order of a search); choosing it clears ``sort``."""

    def href(value: str) -> str:
        split = urlsplit(current_path)
        kept = [(k, v) for k, v in parse_qsl(split.query) if k not in {"sort", "page"}]
        if value:
            kept.append(("sort", value))
        return (split.path or "/") + ("?" + urlencode(kept) if kept else "")

    options: list[tuple[str, str]] = []
    if query:
        options.append(("", "sort_relevance"))  # relevance == no explicit sort
    options.append(("newest", "sort_newest"))
    options.append(("oldest", "sort_oldest"))
    items: list[str] = []
    for value, key in options:
        label = _esc(i18n.t(lang, key))
        if value == sort:
            items.append(f'<span aria-current="true">{label}</span>')
        else:
            items.append(f'<a href="{_esc(href(value))}">{label}</a>')
    return (
        f'    <p class="sort"><span>{_esc(i18n.t(lang, "sort_label"))}</span> '
        + " ".join(items)
        + "</p>\n"
    )


def _browse_main_html(
    records: list[DisclosedRecord],
    *,
    heading: str,
    query: str = "",
    lang: str = "en",
    active_facets: list[tuple[str, str]] | None = None,
    sort: str = "",
    page: int = 1,
    per_page: int = pagination.DEFAULT_PER_PAGE,
    current_path: str = "/",
) -> str:
    """Compose the browse/search ``<main>``: one ``<h1>``, the form, then both views.

    Renders the list and the table as two complete, equivalent presentations of
    the same records (accessibility — equivalent list and table views), plus
    browsable subject/type/language facets that *compose* with the search box.
    Heading order is ``h1`` (page) then ``h2`` (each view) then ``h3`` (list items),
    with no levels skipped (accessibility).

    The result set is paginated (:mod:`ledger.pagination`): only the current page's
    records are rendered into each view, with a pager below. ``records`` is the full
    matched set (query + facets already applied), so the facet sidebar and counts
    describe *these* results — clicking a facet narrows the current search rather than
    starting over. ``active_facets`` are the filters in force, so the sidebar can mark
    them and offer a clear-filters link, and the search form carries them as it posts.
    """
    active = active_facets or []
    window = pagination.paginate(records, page, per_page)
    shown = list(window.items)
    # The empty state distinguishes "no matches" from a permission problem, in
    # plain language (user research T5/P1-3) — without revealing that anything is
    # hidden (the public list simply omits non-listable records).
    if window.total == 0:
        status_line = f'    <p class="empty">{_esc(i18n.t(lang, "empty_no_matches"))}</p>\n'
    else:
        showing = i18n.t(
            lang,
            "results_showing",
            start=window.start_index,
            end=window.end_index,
            total=window.total,
        )
        status_line = f'      <p class="count">{_esc(showing)}</p>\n'
    # A "clear filters" link when any query or facet is active, so a reader is never
    # stuck inside a narrowed view (escapability).
    if query or active:
        split = urlsplit(current_path)
        clear = (
            f'    <p class="clear-filters"><a href="{_esc(split.path or "/")}">'
            f"{_esc(i18n.t(lang, 'clear_filters'))}</a></p>\n"
        )
    else:
        clear = ""
    # Offer a sort control only when there is more than one record to reorder.
    sort_control = (
        _sort_html(current_path, query=query, sort=sort, lang=lang) if window.total > 1 else ""
    )
    facets = _facets_html(records, current_path=current_path, active=active, lang=lang)
    pager = _pager_html(window, current_path, lang=lang)
    list_heading = _esc(i18n.t(lang, "results_list_heading"))
    table_heading = _esc(i18n.t(lang, "results_table_heading"))
    # The count/empty state lives in a polite status region so a screen reader
    # announces "Showing X-Y of N" after a search or page change without the user
    # hunting for it — the dynamic status message WCAG 4.1.3 asks for. The region is
    # present on every render so the announcement fires as the results load.
    return (
        f"    <h1>{_esc(heading)}</h1>\n"
        f"    {_search_form(query, lang=lang, active_facets=active)}"
        '    <div class="results-status" role="status" aria-live="polite">\n'
        f"{status_line}"
        "    </div>\n"
        f"{clear}"
        f"{sort_control}"
        '    <section aria-labelledby="list-heading">\n'
        f'      <h2 id="list-heading">{list_heading}</h2>\n'
        f"      {_records_list_html(shown, query=query, lang=lang)}\n"
        "    </section>\n"
        '    <section aria-labelledby="table-heading">\n'
        f'      <h2 id="table-heading">{table_heading}</h2>\n'
        f"      {_records_table_html(shown, query=query, lang=lang)}\n"
        "    </section>\n"
        f"{pager}"
        f"{facets}"
    )


def _payload_li(rid: str, p: PayloadFile, *, lang: str = "en") -> str:
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
            f"          <summary>{_esc(i18n.t(lang, 'payload_transcript'))}</summary>\n"
            f"          <p>{_esc(p.transcript)}</p>\n"
            "        </details>"
        )
    elif p.media_type.startswith(("audio/", "video/")):
        base += f'\n        <p class="muted">{_esc(i18n.t(lang, "payload_no_transcript"))}</p>'
    return base + "</li>"


# The Dublin Core elements the browse facets can filter on, whose values are rendered
# as links into the faceted browse on a record page (kept in sync with the server's
# facet routing and ``search.facets``).
_FACET_FIELDS: tuple[str, ...] = ("subject", "type", "language")


def _dc_value_html(element: str, values: list[str]) -> str:
    """Render a Dublin Core element's values, linking facetable ones to browse.

    A ``subject``/``type``/``language`` value becomes ``<a href="/?<field>=<value>">``
    so a reader can pivot to every other record carrying it; any other element's
    values are joined as plain escaped text. Every value passes through :func:`_esc`,
    and the query value is URL-quoted, so a crafted metadata value cannot break out of
    the attribute or inject markup (security)."""
    if element in _FACET_FIELDS:
        return ", ".join(
            f'<a href="/?{element}={quote(value)}">{_esc(value)}</a>' for value in values
        )
    return _esc("; ".join(values))


def _citation_html(record: DisclosedRecord, *, base_url: str, archive_name: str, lang: str) -> str:
    """A "Cite this record" block: a formatted citation, a permalink, a metadata link.

    Scholarship needs a stable, quotable reference (user research P2-3). The citation
    is ``Title. [Date.] Archive. URL`` built from already-disclosed metadata (the
    archive name falls back to the record's Dublin Core ``publisher``), so it carries
    no identity. The permalink and the ``Available at`` URL are the record's public
    address; a "download metadata" link points at the JSON API for machine reuse.
    Everything is escaped, and the URL is quoted, so no value can break the markup."""
    root = base_url.rstrip("/")
    permalink = f"{root}/record/{quote(record.record_id)}"
    publisher = record.dublin_core.get("publisher") or []
    archive = archive_name or (publisher[0] if publisher else "")
    dates = record.dublin_core.get("date") or []
    date_part = f" {_esc(dates[0])}." if dates and dates[0] else ""
    archive_part = f" {_esc(archive)}." if archive else ""
    citation = (
        f"{_esc(record.title)}.{date_part}{archive_part} "
        f"{_esc(i18n.t(lang, 'cite_available_at'))} {_esc(permalink)}"
    )
    return (
        '    <section aria-labelledby="cite-heading">\n'
        f'      <h2 id="cite-heading">{_esc(i18n.t(lang, "cite_heading"))}</h2>\n'
        f'      <p class="citation">{citation}</p>\n'
        f"      <p>{_esc(i18n.t(lang, 'cite_permalink'))}: "
        f'<a href="{_esc(permalink)}">{_esc(permalink)}</a></p>\n'
        f'      <p><a href="/api/record/{quote(record.record_id)}">'
        f"{_esc(i18n.t(lang, 'cite_download'))}</a></p>\n"
        "    </section>"
    )


def _record_main_html(
    record: DisclosedRecord,
    *,
    proceed: bool,
    insider: bool = False,
    lang: str = "en",
    base_url: str = "",
    archive_name: str = "",
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
            f"      <p>{_esc(i18n.t(lang, 'rec_cw_review'))}</p>\n"
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
        note_label = _esc(i18n.t(lang, "rec_cw_note"))
        parts.append(
            f'    <p class="cw-note" id="content"><strong>{note_label}</strong> {warnings}</p>'
        )
    else:
        parts.append(
            f'    <p id="content" class="visually-hidden">{_esc(i18n.t(lang, "rec_content_sr"))}</p>'
        )

    # Disclosed descriptive fields.
    if record.fields:
        rows = "\n".join(
            f'      <div class="field"><dt>{_esc(name)}</dt><dd>{_esc(value)}</dd></div>'
            for name, value in record.fields.items()
        )
        parts.append(
            '    <section aria-labelledby="fields-heading">\n'
            f'      <h2 id="fields-heading">{_esc(i18n.t(lang, "rec_fields_heading"))}</h2>\n'
            "      <dl>\n"
            f"{rows}\n"
            "      </dl>\n"
            "    </section>"
        )

    # Dublin Core descriptive metadata (collection-level; never identity). For the
    # facetable elements (subject/type/language) each value is a link into the faceted
    # browse, so a reader on one record can discover related records by topic, kind, or
    # language — connecting a contributor's descriptive metadata to discovery (P1-4).
    dc_rows = [
        f'      <div class="field"><dt>{_esc(element)}</dt>'
        f"<dd>{_dc_value_html(element, values)}</dd></div>"
        for element, values in record.dublin_core.items()
        if values
    ]
    if dc_rows:
        parts.append(
            '    <section aria-labelledby="meta-heading">\n'
            f'      <h2 id="meta-heading">{_esc(i18n.t(lang, "rec_catalogue_heading"))}</h2>\n'
            "      <dl>\n" + "\n".join(dc_rows) + "\n      </dl>\n"
            "    </section>"
        )

    # Payload files the viewer may see — each is a real, fixity-verified download
    # link (user research C4: the filename was previously an inert false affordance).
    if record.payloads:
        files = "\n".join(_payload_li(rid, p, lang=lang) for p in record.payloads)
        parts.append(
            '    <section aria-labelledby="files-heading">\n'
            f'      <h2 id="files-heading">{_esc(i18n.t(lang, "rec_files_heading"))}</h2>\n'
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
                f"      <p>{_esc(i18n.t(lang, 'rec_withheld_insider'))}</p>\n"
                f'      <ul class="withheld-list">\n{rows}\n      </ul>'
            )
        else:
            n = len(record.withheld)
            key = "rec_withheld_outsider_one" if n == 1 else "rec_withheld_outsider_many"
            body = f"      <p>{_esc(i18n.t(lang, key, count=n))}</p>"
        parts.append(
            '    <section aria-labelledby="redactions-heading">\n'
            f'      <h2 id="redactions-heading">{_esc(i18n.t(lang, "rec_withheld_heading"))}</h2>\n'
            f"{body}\n"
            "    </section>"
        )

    # A stable, quotable citation for scholarship, plus a machine-readable metadata
    # link (user research P2-3). Drawn only from disclosed metadata, so no identity.
    parts.append(_citation_html(record, base_url=base_url, archive_name=archive_name, lang=lang))

    # The contributor's front door: act on the promise that consent is revocable
    # (user research P0-2). Shown to everyone — only the claim token (issued at
    # ingest) lets the actual contributor file a request.
    parts.append(
        f'    <p class="consent-link"><a href="/record/{quote(rid)}/consent">'
        f"{_esc(i18n.t(lang, 'rec_consent_link'))}</a></p>"
    )
    # A person *named* in a record they did not contribute can object (user research
    # B3 — subjects have agency too, not only the contributor).
    parts.append(
        f'    <p class="object-link"><a href="/record/{quote(rid)}/object">'
        f"{_esc(i18n.t(lang, 'rec_object_link'))}</a></p>"
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


def _language_switch_html(lang: str, current_path: str) -> str:
    """A localized language picker that keeps the reader on the current page.

    Language was previously chosen *only* from the browser's ``Accept-Language``
    header, which a reader on a shared or mislocalized machine cannot change (user
    research P2-1, I2). This renders one link per supported language, each pointing
    at the *current* path with ``?lang=<code>`` appended (any existing ``lang`` query
    is dropped first so the choices do not stack). The active language is plain text
    marked ``aria-current`` rather than a link, so a screen reader announces which
    language is in effect. Each alternative carries ``hreflang`` and its autonym
    (e.g. "Español"), so it reads naturally to a native speaker. The choice carries
    no identity or record reference — only a UI language (no-outing rule).
    """
    if len(i18n.SUPPORTED) < 2:
        return ""
    split = urlsplit(current_path)
    kept = [(k, v) for k, v in parse_qsl(split.query) if k != "lang"]
    items: list[str] = []
    for code in i18n.SUPPORTED:
        name = i18n.language_name(code)
        if code == lang:
            items.append(f'<span aria-current="true">{_esc(name)}</span>')
        else:
            href = (split.path or "/") + "?" + urlencode([*kept, ("lang", code)])
            items.append(f'<a href="{_esc(href)}" hreflang="{_esc(code)}">{_esc(name)}</a>')
    label = i18n.t(lang, "language_label")
    return (
        f'      <span class="lang-switch" role="group" aria-label="{_esc(label)}">'
        f"{' '.join(items)}</span>\n"
    )


def _nav_html(lang: str = "en", *, contribute: bool = False, current_path: str = "/") -> str:
    """The site navigation: descriptive links only, no positive tabindex.

    Labels are localized (i18n), and the "Status" link points at the human-readable
    ``/status`` page rather than the raw-JSON ``/healthz`` endpoint, which alarmed
    non-technical users and was unreadable to a screen reader (user research P1-1).
    The Contribute link appears only when the submission surface is enabled on the
    server, so a read-only deployment never advertises a write path it does not have.
    A language picker (:func:`_language_switch_html`) lets a reader switch language
    explicitly and stay on ``current_path`` (user research P2-1, I2).
    """
    contribute_link = '      <a href="/contribute">Contribute</a>\n' if contribute else ""
    switch = _language_switch_html(lang, current_path)
    return (
        f'\n      <a href="/">{_esc(i18n.t(lang, "nav_browse"))}</a>\n'
        f'      <a href="/search">{_esc(i18n.t(lang, "nav_search"))}</a>\n'
        f'      <a href="/about">{_esc(i18n.t(lang, "nav_about"))}</a>\n'
        f'      <a href="/status">{_esc(i18n.t(lang, "nav_status"))}</a>\n'
        f"{contribute_link}{switch}    "
    )
