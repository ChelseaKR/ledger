"""A small, hand-maintained localization layer (user research P2-1 / T9).

English-only UI blocks safety comprehension for the people ledger most needs to
protect, and jargon at the trauma-decision point ("Proceed", an untranslated
content-warning tag) is exactly where a contributor most needs plain, native
language. This module gives the browse server a tiny, dependency-free string
catalog plus a correct ``Accept-Language`` negotiation so the right language is
chosen automatically, with a safe English fallback that never raises.

Design qualities deliberately built in here:

* **No new dependency.** A plain dict catalog and a small parser, standard library
  only, so a community can keep running ledger on one inexpensive box.
* **Fail soft, never blank.** A missing key, a missing language, or a missing
  interpolation argument degrades to English or to the key/tag itself rather than
  raising — a half-translated catalog can never take a page down (robustness).
* **Determinism.** Lookups are pure functions of their arguments; no clock, no
  locale, no global state, so the same request always renders the same string.

No-outing rule: this module holds only generic UI chrome and plain-language glosses
for a *controlled* content-warning vocabulary. It never contains, logs, or formats a
contributor identity or a sealed value; callers pass only UI keys and CW tags.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

# The fallback language. Every key is guaranteed to exist here, so any lookup can
# fall back to English and still return a real string (safety: never blank).
DEFAULT_LANG = "en"

# The languages this build ships a catalog for, in preference order. A steward's
# config may serve a subset; ``negotiate`` is told the available set explicitly.
SUPPORTED: tuple[str, ...] = ("en", "es")

# Human-readable language names, in the language's own script (autonym), so a
# language picker reads naturally to a native speaker.
_LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "es": "Español",
}

# The UI string catalog: language -> key -> template. Every key present in ``en``
# should also exist in every other language; where a translation is genuinely
# missing, ``t`` falls back to the English template so the page still renders.
# "proceed" is intentionally "Continue"/"Continuar", never "Proceed" — at the
# content-warning decision point a neutral, non-coercive verb is part of the safety
# design (user research T9).
_CATALOG: dict[str, dict[str, str]] = {
    "en": {
        "nav_browse": "Browse",
        "nav_search": "Search",
        "nav_status": "Status",
        "nav_about": "About",
        "skip_link": "Skip to main content",
        "search_label": "Search the archive",
        "search_button": "Search",
        "withheld_heading": "Some parts of this record are not shown",
        "withheld_intro": (
            "Parts of this record have been kept private by the contributor or a "
            "steward. You are seeing everything you are allowed to see."
        ),
        "proceed": "Continue",
        "back_to_records": "Back to records",
        "empty_no_matches": "No records match your search.",
        "restricted_notice": "This record has access restrictions.",
        "content_warning_heading": "Content warning",
        "results_list_heading": "Records (list view)",
        "results_table_heading": "Records (table view)",
        "results_showing": "Showing {start}-{end} of {total} record(s).",
        "table_caption": (
            "All records you may view, with their titles, summaries, and whether each "
            "carries a content warning."
        ),
        "col_title": "Title",
        "col_summary": "Summary",
        "no_records_available": "No records are available to you yet.",
        "answer_yes": "Yes",
        "answer_no": "No",
        "pager_label": "Pagination",
        "pager_prev": "Previous",
        "pager_next": "Next",
        "pager_position": "Page {number} of {pages}",
        "footer_privacy": "Your identity is never shown. Contributors control what is public.",
        "language_label": "Language",
        "visibility_public": "Public — anyone may read it once a steward approves",
        "visibility_community": "Community only — vetted members of this community",
        "visibility_sealed": "Sealed — keep it withheld for now",
    },
    "es": {
        "nav_browse": "Explorar",
        "nav_search": "Buscar",
        "nav_status": "Estado",
        "nav_about": "Acerca de",
        "skip_link": "Saltar al contenido principal",
        "search_label": "Buscar en el archivo",
        "search_button": "Buscar",
        "withheld_heading": "Algunas partes de este registro no se muestran",
        "withheld_intro": (
            "El colaborador o una persona responsable han mantenido privadas algunas "
            "partes de este registro. Está viendo todo lo que tiene permitido ver."
        ),
        "proceed": "Continuar",
        "back_to_records": "Volver a los registros",
        "empty_no_matches": "Ningún registro coincide con su búsqueda.",
        "restricted_notice": "Este registro tiene restricciones de acceso.",
        "content_warning_heading": "Advertencia de contenido",
        "results_list_heading": "Registros (vista de lista)",
        "results_table_heading": "Registros (vista de tabla)",
        "results_showing": "Mostrando {start}-{end} de {total} registro(s).",
        "table_caption": (
            "Todos los registros que puede ver, con sus títulos, resúmenes y si cada uno "
            "lleva una advertencia de contenido."
        ),
        "col_title": "Título",
        "col_summary": "Resumen",
        "no_records_available": "Aún no hay registros disponibles para usted.",
        "answer_yes": "Sí",
        "answer_no": "No",
        "pager_label": "Paginación",
        "pager_prev": "Anterior",
        "pager_next": "Siguiente",
        "pager_position": "Página {number} de {pages}",
        "footer_privacy": (
            "Su identidad nunca se muestra. Los colaboradores controlan qué es público."
        ),
        "language_label": "Idioma",
        "visibility_public": "Público — cualquiera podrá leerlo cuando una persona responsable lo apruebe",
        "visibility_community": "Solo para la comunidad — miembros verificados de esta comunidad",
        "visibility_sealed": "Sellado — manténgalo reservado por ahora",
    },
}

# Plain-language glosses for the controlled content-warning vocabulary: language ->
# tag -> a one-line, non-clinical explanation of what the tag means, so a reader
# decides from understanding rather than from a bare jargon label (T9). The em dash
# separates the short label from its explanation. An unknown tag is humanized rather
# than looked up, so the catalog need not be exhaustive to stay safe.
_CW_GLOSSES: dict[str, dict[str, str]] = {
    "en": {
        # The default starter vocabulary (config._STARTER_CONTENT_WARNINGS), so every
        # tag a fresh archive ships with has a plain, native gloss at the decision point.
        "violence": "Violence — describes physical harm or attack",
        "sexual-violence": "Sexual violence — describes sexual assault or coercion",
        "abuse": "Abuse — describes ongoing harm or mistreatment",
        "self-harm": "Self-harm — describes a person hurting themselves",
        "suicide": "Suicide — describes suicide or suicidal thoughts",
        "medical": "Medical — describes injury, illness, or clinical procedures",
        "death": "Death — describes the death of a person",
        "incarceration": "Incarceration — describes jail, prison, or detention",
        "deportation": "Deportation — describes removal from a country or its threat",
        "outing": "Outing — reveals someone's identity without their consent",
        "hate-speech": "Hate speech — contains slurs or attacks on a group",
        "substance-use": "Substance use — describes drug or alcohol use",
        # Additional tags a steward may add to the vocabulary.
        "police-violence": "Police violence — describes harm or force by police",
        "deadnaming": "Deadnaming — refers to a name a person no longer uses",
    },
    "es": {
        "violence": "Violencia — describe daño físico o un ataque",
        "sexual-violence": "Violencia sexual — describe agresión o coerción sexual",
        "abuse": "Abuso — describe daño o maltrato continuo",
        "self-harm": "Autolesión — describe que una persona se hace daño a sí misma",
        "suicide": "Suicidio — describe el suicidio o pensamientos suicidas",
        "medical": "Médico — describe lesiones, enfermedades o procedimientos clínicos",
        "death": "Muerte — describe la muerte de una persona",
        "incarceration": "Encarcelamiento — describe cárcel, prisión o detención",
        "deportation": "Deportación — describe la expulsión de un país o su amenaza",
        "outing": "Exposición — revela la identidad de alguien sin su consentimiento",
        "hate-speech": "Discurso de odio — contiene insultos o ataques a un grupo",
        "substance-use": "Uso de sustancias — describe el uso de drogas o alcohol",
        "police-violence": "Violencia policial — describe daño o fuerza por parte de la policía",
        "deadnaming": "Nombre muerto — se refiere a un nombre que una persona ya no usa",
    },
}


def _humanize(tag: str) -> str:
    """Turn a raw tag like ``police-violence`` into ``Police violence``.

    Used as the safe fallback for an unknown content-warning tag and for any value
    the catalog does not gloss, so the UI shows a readable label rather than a slug.
    """
    return tag.replace("-", " ").replace("_", " ").strip().capitalize()


def _parse_accept_language(header: str) -> list[str]:
    """Parse an ``Accept-Language`` header into language tags, best preference first.

    Handles q-values (``en;q=0.8``), whitespace, and casing per RFC 9110. Entries
    with ``q=0`` are dropped (the client explicitly refuses them). A malformed or
    out-of-range q-value defaults that entry to the lowest acceptable weight rather
    than raising, so a bad header degrades to the default language instead of an
    error (robustness). Sorting is stable, so equal-q tags keep header order.
    """
    scored: list[tuple[int, float, str]] = []
    for index, raw_part in enumerate(header.split(",")):
        part = raw_part.strip()
        if not part:
            continue
        token, _, params = part.partition(";")
        tag = token.strip().lower()
        if not tag:
            continue
        quality = 1.0
        param = params.strip()
        if param.lower().startswith("q="):
            try:
                quality = float(param[2:].strip())
            except ValueError:
                quality = 0.0
        if not 0.0 < quality <= 1.0:
            # q=0 (or out of range) means "not acceptable" / unusable: drop it.
            continue
        # Negate quality and use the original index so a stable ascending sort
        # yields highest-quality first, ties broken by header order.
        scored.append((index, -quality, tag))
    scored.sort(key=lambda item: (item[1], item[0]))
    return [tag for _, _, tag in scored]


def negotiate(accept_language: str | None, available: Sequence[str] = SUPPORTED) -> str:
    """Pick the best available language for an ``Accept-Language`` header.

    Returns the highest-preference tag the client asked for that ``available`` can
    serve, matching either exactly (``es-MX`` -> ``es-MX``) or by primary subtag
    (``es-MX`` -> ``es``), case-insensitively. A ``*`` wildcard selects the first
    available language. If nothing matches — including ``None`` or an empty header —
    returns :data:`DEFAULT_LANG` (safety: a known-good fallback, never blank).
    """
    available_list = list(available)
    if not available_list:
        return DEFAULT_LANG
    # Map lower-cased available tags back to their canonical form for the result.
    canonical: dict[str, str] = {}
    for code in available_list:
        canonical.setdefault(code.lower(), code)
    primary_index: dict[str, str] = {}
    for code in available_list:
        primary_index.setdefault(code.lower().partition("-")[0], code)

    if accept_language is None:
        return DEFAULT_LANG
    for tag in _parse_accept_language(accept_language):
        if tag == "*":
            return available_list[0]
        if tag in canonical:
            return canonical[tag]
        primary = tag.partition("-")[0]
        if primary in primary_index:
            return primary_index[primary]
    return DEFAULT_LANG


def _lookup(lang: str, key: str) -> str | None:
    """Return the catalog template for ``(lang, key)``, falling back to English.

    Returns ``None`` only when the key is unknown in every language, so callers can
    distinguish "no such key" from "key exists, untranslated".
    """
    lang_table = _CATALOG.get(lang)
    if lang_table is not None and key in lang_table:
        return lang_table[key]
    default_table = _CATALOG.get(DEFAULT_LANG, {})
    return default_table.get(key)


def t(lang: str, key: str, /, **kw: object) -> str:
    """Look up a UI string by ``key`` in ``lang``, formatting it with ``kw``.

    Resolution order: the requested language, then English, then — if the key is
    unknown everywhere — the key itself. Formatting is forgiving: a template that
    references a placeholder not supplied in ``kw`` is returned unformatted rather
    than raising, so a catalog/caller mismatch can never take a page down. This
    function never raises (safety, robustness).
    """
    template = _lookup(lang, key)
    if template is None:
        return key
    if not kw:
        return template
    try:
        return template.format(**kw)
    except (KeyError, IndexError, ValueError):
        return template


def gloss_cw(lang: str, tag: str) -> str:
    """Return a plain-language gloss for a content-warning ``tag`` (user research T9).

    Resolution order: the requested language, then English, then a humanized form of
    the tag itself (hyphens/underscores to spaces, capitalized). This means a tag the
    catalog has never seen still renders as a readable label instead of a raw slug,
    so the controlled vocabulary can grow without code changes (robustness).
    """
    lang_glosses = _CW_GLOSSES.get(lang)
    if lang_glosses is not None and tag in lang_glosses:
        return lang_glosses[tag]
    default_glosses = _CW_GLOSSES.get(DEFAULT_LANG, {})
    if tag in default_glosses:
        return default_glosses[tag]
    return _humanize(tag)


def language_name(code: str) -> str:
    """Return the human-readable autonym for a language ``code``.

    ``en`` -> ``English``, ``es`` -> ``Español``. An unknown code matched only by its
    primary subtag (``es-MX`` -> ``Español``) is still named; a truly unknown code is
    returned humanized so a language picker never shows a blank entry.
    """
    if code in _LANGUAGE_NAMES:
        return _LANGUAGE_NAMES[code]
    lowered = code.lower()
    if lowered in _LANGUAGE_NAMES:
        return _LANGUAGE_NAMES[lowered]
    primary = lowered.partition("-")[0]
    if primary in _LANGUAGE_NAMES:
        return _LANGUAGE_NAMES[primary]
    return _humanize(code)


def _catalog_view() -> Mapping[str, Mapping[str, str]]:
    """Return a read-only view of the catalog (for tests/introspection).

    Exposed so callers can verify coverage without importing the private dict; the
    returned mapping must not be mutated.
    """
    return _CATALOG
