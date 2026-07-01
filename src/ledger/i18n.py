"""Gettext localization seam for ledger's end-user-facing browse/contribute UI.

The accessible browse server (:mod:`ledger.render`, :mod:`ledger.server`) and the
contribution write path (:mod:`ledger.contribute`) are where ledger emits
end-user-facing, natural-language text, in English or Spanish. This module is the
single migration seam onto GNU gettext catalogs (INTERNATIONALIZATION-STANDARD
§3/§4): the *source string is the English text itself*, extracted by ``pybabel``
into ``locales/messages.pot`` and translated in
``locales/<lang>/LC_MESSAGES/messages.po``. It replaces the previous bespoke
``_CATALOG``/``_CW_GLOSSES`` Python dicts (no extraction tooling, no plural
handling, no key-parity check).

The public API is unchanged so no call site moves: ``t(lang, key, **kw)`` looks up
a UI string by its stable key, ``gloss_cw(lang, tag)`` glosses a content-warning
tag, ``language_name(code)`` returns an autonym, and ``negotiate(...)`` resolves an
``Accept-Language`` header (INTERNATIONALIZATION-STANDARD §6). Internally these now
resolve through gettext instead of a hand-maintained dict.

Design qualities deliberately preserved:

* **Fail soft, never blank.** ``get_translation`` uses ``fallback=True``: an
  unknown language, a missing ``.mo``, or an unknown key degrades to the English
  source text (or, for an unknown key/tag, the key/humanized tag) rather than
  raising — a half-translated catalog can never take a page down (robustness).
* **Determinism.** Lookups are pure functions of their arguments; no clock, no
  ambient locale, no mutable global state, so the same request renders the same
  string.

No-outing rule: this module holds only generic UI chrome and plain-language glosses
for a *controlled* content-warning vocabulary. It never contains, logs, or formats a
contributor identity or a sealed value; callers pass only UI keys and CW tags. The
non-coercive verb at the trauma-decision point stays "Continue"/"Continuar", never
"Proceed" (user research T9).
"""

from __future__ import annotations

import gettext
from collections.abc import Sequence
from functools import cache
from pathlib import Path

#: gettext domain — the ``messages`` in ``messages.po`` / ``messages.mo``.
DOMAIN = "messages"

#: Compiled catalogs live beside this module (inside the package) so a checkout or
#: an installed wheel resolves them with no separate install step. See docs/I18N.md
#: for the decision to commit the compiled ``.mo`` files.
LOCALEDIR = Path(__file__).resolve().parent / "locales"

# The fallback language. Every msgid is the English source, so any lookup can fall
# back to English and still return a real string (safety: never blank).
DEFAULT_LANG = "en"

# The languages this build ships a catalog for, in preference order. A steward's
# config may serve a subset; ``negotiate`` is told the available set explicitly.
SUPPORTED: tuple[str, ...] = ("en", "es")

# Human-readable language names, in the language's own script (autonym), so a
# language picker reads naturally to a native speaker. Autonyms are invariant across
# the UI language, so they are NOT gettext-translated — "Español" is always "Español".
_LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "es": "Español",
}


def get_translation(lang: str) -> gettext.NullTranslations:
    """Return the gettext catalog for ``lang``, falling back to English text.

    ``fallback=True`` means an unknown tag (or a missing ``.mo``) yields a
    :class:`gettext.NullTranslations` whose ``gettext``/``ngettext`` return the
    English source msgid unchanged — never an exception, never a blank string.
    """
    return gettext.translation(DOMAIN, localedir=str(LOCALEDIR), languages=[lang], fallback=True)


def _messages(translation: gettext.NullTranslations) -> dict[str, str]:
    """Map every singular UI key to its translated string for ``translation``.

    The values are literal ``_("English source")`` calls so ``pybabel extract``
    picks up the English text as the msgid. Building the map from a translation
    object means the same key structure yields English, Spanish, or (for an unknown
    language) the English fallback with no branching at the call site.
    """
    _ = translation.gettext
    return {
        "nav_browse": _("Browse"),
        "nav_search": _("Search"),
        "nav_overview": _("Overview"),
        "nav_status": _("Status"),
        "nav_about": _("About"),
        "overview_heading": _("Collection overview"),
        "overview_intro": _(
            "An at-a-glance summary of the public records in this archive — the topics, kinds, and languages they cover. Every number counts only what is publicly visible."
        ),
        "overview_total": _("{count} public record(s)."),
        "overview_date_range": _("Spanning {earliest} to {latest}."),
        "overview_empty": _("There are no public records yet."),
        "skip_link": _("Skip to main content"),
        "search_label": _("Search the archive"),
        "search_button": _("Search"),
        "withheld_heading": _("Some parts of this record are not shown"),
        "withheld_intro": _(
            "Parts of this record have been kept private by the contributor or a steward. You are seeing everything you are allowed to see."
        ),
        "proceed": _("Continue"),
        "back_to_records": _("Back to records"),
        "empty_no_matches": _("No records match your search."),
        "restricted_notice": _("This record has access restrictions."),
        "content_warning_heading": _("Content warning"),
        "results_list_heading": _("Records (list view)"),
        "results_table_heading": _("Records (table view)"),
        "results_showing": _("Showing {start}-{end} of {total} record(s)."),
        "table_caption": _(
            "All records you may view, with their titles, summaries, and whether each carries a content warning."
        ),
        "col_title": _("Title"),
        "col_summary": _("Summary"),
        "no_records_available": _("No records are available to you yet."),
        "facet_subjects": _("Subjects"),
        "facet_types": _("Types"),
        "facet_languages": _("Languages"),
        "clear_filters": _("Clear filters"),
        "download_csv": _("Download results (CSV)"),
        "sort_label": _("Sort by:"),
        "sort_relevance": _("Relevance"),
        "sort_newest": _("Newest"),
        "sort_oldest": _("Oldest"),
        "date_from_label": _("From"),
        "date_to_label": _("To"),
        "date_apply": _("Apply dates"),
        "answer_yes": _("Yes"),
        "answer_no": _("No"),
        "pager_label": _("Pagination"),
        "pager_prev": _("Previous"),
        "pager_next": _("Next"),
        "pager_position": _("Page {number} of {pages}"),
        "back_to_archive": _("Back to the archive"),
        "sw_console_heading": _("Steward console"),
        "sw_submissions_heading": _("Submissions awaiting review"),
        "sw_submissions_intro": _(
            "Contributions arrive sealed — nothing is visible until you publish it. Publishing opens a record to the visibility the contributor asked for; withholding holds it for revision. Every choice is recorded."
        ),
        "sw_no_submissions": _("No submissions awaiting review."),
        "sw_would_publish_as": _("Would publish as:"),
        "sw_open_to_read": _("Open the record to read it before deciding."),
        "sw_submitted": _("submitted {when}"),
        "sw_publish_button": _("Publish (as requested)"),
        "sw_withhold_button": _("Withhold"),
        "sw_select_label": _("Select this submission"),
        "sw_bulk_withhold": _("Withhold selected"),
        "sw_vis_public": _("Public — anyone may read it"),
        "sw_vis_community": _("Community only — vetted members"),
        "sw_vis_sealed": _("Sealed — kept private for now"),
        "sw_requests_heading": _("Open consent & takedown requests"),
        "sw_on_record": _("on record"),
        "sw_request_meta": _("{when}, ref {ref}"),
        "sw_mark_resolved": _("Mark resolved"),
        "sw_no_requests": _("No open requests."),
        "sw_before_heading": _("Before you act"),
        "sw_before_access": _(
            "You can read access-restricted content to do your work, but content sealed with the 'sealed' policy — and every contributor's identity — is restricted even from you. Some records may be sealed above your access; their absence here does not mean they do not exist."
        ),
        "sw_cli_intro": _("Action a request with the audited CLI:"),
        "sw_cli_policy_note": _("(change access),"),
        "sw_cli_cw_note": _("(add a content warning) — each records who acted and why."),
        "sw_view_audit": _("View the audit log"),
        "sw_view_audit_note": _("— every recorded action across the archive."),
        "req_kind_withdraw": _("withdraw / take down"),
        "req_kind_tighten": _("tighten access"),
        "req_kind_correct": _("correct the record"),
        "req_kind_contact": _("ask a steward to make contact"),
        "req_kind_object": _("objection from a person named in the record"),
        "audit_heading": _("Audit log"),
        "audit_intro": _(
            "Every recorded action across the archive, newest first. This log carries no contributor identity or sealed value — only what happened, who acted, and the outcome."
        ),
        "audit_caption": _("Recorded actions, newest first"),
        "audit_col_when": _("When"),
        "audit_col_event": _("Event"),
        "audit_col_outcome": _("Outcome"),
        "audit_col_agent": _("Agent"),
        "audit_col_object": _("Object"),
        "audit_col_detail": _("Detail"),
        "audit_no_events": _("No recorded events yet."),
        "audit_back": _("Back to the steward console"),
        "cs_heading": _("Check a request"),
        "cs_intro": _(
            "Enter the reference code you were given when you filed a consent or takedown request to see whether a steward has acted on it."
        ),
        "cs_not_found": _(
            "We could not find a request with that reference. Check it and try again — it is the code shown when you filed the request."
        ),
        "cs_status_aria": _("Request status"),
        "cs_request_label": _("Request: {kind}"),
        "cs_filed_label": _("Filed: {when}"),
        "cs_status_label": _("Status: {status}"),
        "cs_status_open": _("Received — a steward has not acted on it yet."),
        "cs_status_acknowledged": _("Seen by a steward and under consideration."),
        "cs_status_resolved": _("Resolved — a steward has acted on it."),
        "cs_ref_label": _("Your request reference"),
        "cs_button": _("Check status"),
        "rec_cw_review": _(
            "This record carries the following content warnings. Review them before continuing."
        ),
        "rec_cw_note": _("Content warnings:"),
        "rec_content_sr": _("Record content."),
        "rec_fields_heading": _("Details"),
        "rec_catalogue_heading": _("Catalogue metadata"),
        "rec_files_heading": _("Files"),
        "rec_withheld_heading": _("Withheld"),
        "rec_withheld_insider": _(
            "Some parts of this record are not available under your current access:"
        ),
        "related_heading": _("Related records"),
        "cite_heading": _("Cite this record"),
        "cite_available_at": _("Available at"),
        "cite_permalink": _("Permanent link"),
        "cite_download": _("Download metadata (JSON)"),
        "rec_consent_link": _("Are you the contributor? Manage or withdraw your consent"),
        "rec_object_link": _("Are you named in this record and object to it? Tell a steward"),
        "payload_transcript": _("Transcript"),
        "payload_no_transcript": _("No transcript provided for this audio/video."),
        "contribute_heading": _("Contribute to the archive"),
        "contribute_intro": _(
            'Share a story, an account, or knowledge worth keeping. A steward reviews every submission before anything is published — your contribution is kept sealed until then. Use "Preview" to see exactly what a stranger would see before you submit.'
        ),
        "label_title": _("Title"),
        "label_summary": _("Summary (optional)"),
        "summary_hint": _(
            "One line shown in listings, search results, and the feed. Anyone who can see this record will see this summary."
        ),
        "details_legend": _("Details (optional)"),
        "details_hint": _(
            "Descriptive details make this record findable by topic and browsable by facet. Like the summary, they are shown to anyone who can see the record."
        ),
        "label_subject": _("Subjects"),
        "subject_hint": _("Comma-separated topics, e.g. mutual aid, housing."),
        "label_type": _("Type"),
        "type_hint": _("What kind of material this is, e.g. photograph, oral history, flyer."),
        "label_date": _("Date"),
        "date_hint": _("When the material is from, e.g. 1994 or 2021-05-01."),
        "label_language": _("Language"),
        "language_hint": _("The language of the material, e.g. English or Spanish."),
        "label_account": _("Your account"),
        "cw_legend": _("Content warnings (optional)"),
        "cw_hint": _("Tick anything a reader should be warned about before this is shown."),
        "share_legend": _("How should this be shared?"),
        "share_hint": _(
            "A steward reviews every submission before anything becomes visible — nothing is published automatically."
        ),
        "file_legend": _("Attach a file (optional)"),
        "file_hint": _(
            "You can attach one image, audio file, or PDF (up to {max} MB). Accepted: {accepted}. The file is reviewed with the rest of your submission and is not public until a steward publishes it."
        ),
        "label_file": _("File"),
        "contact_legend": _("Contact (optional, sealed)"),
        "contact_hint": _(
            "Only a steward with explicit permission can ever see this. It is encrypted, never shown publicly, and never reveals who contributed a record."
        ),
        "label_name": _("Name"),
        "label_reach": _("How to reach you"),
        "button_preview": _("Preview what a stranger sees"),
        "button_submit": _("Submit for review"),
        "preview_heading": _("Preview — what a stranger would see"),
        "preview_aria_label": _("What a stranger sees"),
        "preview_if_published": _(
            "If a steward publishes this, a stranger who is not signed in would see:"
        ),
        "preview_content_warnings": _("Content warnings: {list}"),
        "preview_stranger_nothing_lead": _("A stranger sees nothing."),
        "preview_stranger_nothing_detail": _(
            " Published as {visibility}, this record would be visible to {audience} — it would not appear in public browse or search."
        ),
        "preview_audience_community": _("community members only"),
        "preview_audience_none": _("no one yet"),
        "preview_sealed_hint": _(
            "Your name and contact are never shown to any reader — they are sealed. They are not in this preview."
        ),
        "thanks_heading": _("Thank you — your contribution was received"),
        "thanks_status": _(
            "It is sealed and waiting for a steward to review it. Nothing you submitted is public yet, and any contact details you gave are encrypted and will never be shown."
        ),
        "thanks_claim_heading": _("Keep this if you might change your mind"),
        "thanks_claim_intro": _(
            "While your submission is still waiting for review you can withdraw it yourself. To do that you will need both of these — keep them private, as together they let someone withdraw this submission:"
        ),
        "label_reference": _("Reference"),
        "label_withdrawal_code": _("Withdrawal code"),
        "thanks_withdraw_before": _("To withdraw it, go to"),
        "withdrawal_page_link_text": _("the withdrawal page"),
        "thanks_withdraw_after": _("and enter both."),
        "thanks_edit_before": _("You can also correct it on"),
        "edit_page_link_text": _("the edit page"),
        "withdraw_heading": _("Withdraw a submission"),
        "withdraw_intro": _(
            "If you contributed something and it is still waiting for review, you can withdraw it here using the reference and withdrawal code from your confirmation page. Withdrawing permanently removes the submission and erases any contact details you sealed with it. Once a steward has published a record, request a change from its page instead."
        ),
        "withdraw_button": _("Withdraw this submission"),
        "err_title_required": _("A title is required."),
        "err_account_required": _("An account is required."),
        "err_submission_too_long": _("That submission is too long."),
        "err_contact_too_long": _("Those contact details are too long."),
        "err_save_failed": _("Your contribution could not be saved right now. Please try again."),
        "err_file_too_large": _("That file is too large. The limit is {max} MB."),
        "err_file_type": _("That file type isn't accepted. Allowed types are: {types}."),
        "err_withdraw_failed": _(
            "We could not withdraw a pending submission with that reference and code. Check both and try again."
        ),
        "err_edit_failed": _(
            "We could not find a pending submission with that reference and code. Check both and try again."
        ),
        "edit_heading": _("Edit a pending submission"),
        "edit_intro": _(
            "If your submission is still waiting for review, you can correct it here. Enter the reference and code from your confirmation, choose “Load my submission”, make your changes, then save. Once a steward has published a record, this no longer applies — request a change from its page instead."
        ),
        "label_code": _("Your code"),
        "edit_load_button": _("Load my submission"),
        "edit_save_button": _("Save changes"),
        "edit_done_heading": _("Your changes were saved"),
        "edit_done_status": _(
            "Your pending submission has been updated. It is still sealed and waiting for a steward to review it."
        ),
        "withdraw_done_heading": _("Your submission was withdrawn"),
        "withdraw_done_status": _(
            "It has been permanently removed, along with any contact details you had sealed with it. Nothing from it remains in the archive."
        ),
        "footer_privacy": _("Your identity is never shown. Contributors control what is public."),
        "language_label": _("Language"),
        "visibility_public": _("Public — anyone may read it once a steward approves"),
        "visibility_community": _("Community only — vetted members of this community"),
        "visibility_sealed": _("Sealed — keep it withheld for now"),
    }


# Keys whose message has genuine singular/plural forms and is resolved via
# ``ngettext`` (INTERNATIONALIZATION-STANDARD §4 G5 plural categories). The count is
# taken from the ``count`` keyword the caller passes. The two "record(s)" display
# strings (overview_total, results_showing) keep their established ``(s)`` form as
# single msgids — see docs/I18N.md for that scope decision.
_PLURAL_KEYS: frozenset[str] = frozenset({"badge_edited", "rec_withheld_outsider"})


def _plural_message(translation: gettext.NullTranslations, key: str, n: int) -> str | None:
    """Return the plural-correct template for ``key`` given count ``n``.

    Literal ``ngettext(singular, plural, n)`` calls so ``pybabel`` extracts both
    forms; gettext selects the right one per the locale's ``Plural-Forms`` header.
    """
    ngettext = translation.ngettext
    if key == "badge_edited":
        return ngettext("Edited ({count} time)", "Edited ({count} times)", n)
    if key == "rec_withheld_outsider":
        return ngettext(
            "{count} detail is restricted under your current access. If you are a "
            "community member or steward, sign in to see what is withheld and why.",
            "{count} details are restricted under your current access. If you are a "
            "community member or steward, sign in to see what is withheld and why.",
            n,
        )
    return None


def _cw_glosses(translation: gettext.NullTranslations) -> dict[str, str]:
    """Map each content-warning tag to its plain-language gloss for ``translation``.

    Same literal-``_()`` pattern as :func:`_messages`, so the English glosses are the
    extracted msgids and the Spanish glosses come from the catalog (user research T9).
    """
    _ = translation.gettext
    return {
        "violence": _("Violence — describes physical harm or attack"),
        "sexual-violence": _("Sexual violence — describes sexual assault or coercion"),
        "abuse": _("Abuse — describes ongoing harm or mistreatment"),
        "self-harm": _("Self-harm — describes a person hurting themselves"),
        "suicide": _("Suicide — describes suicide or suicidal thoughts"),
        "medical": _("Medical — describes injury, illness, or clinical procedures"),
        "death": _("Death — describes the death of a person"),
        "incarceration": _("Incarceration — describes jail, prison, or detention"),
        "deportation": _("Deportation — describes removal from a country or its threat"),
        "outing": _("Outing — reveals someone's identity without their consent"),
        "hate-speech": _("Hate speech — contains slurs or attacks on a group"),
        "substance-use": _("Substance use — describes drug or alcohol use"),
        "police-violence": _("Police violence — describes harm or force by police"),
        "deadnaming": _("Deadnaming — refers to a name a person no longer uses"),
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

    Implements the INTERNATIONALIZATION-STANDARD §6 fallback chain
    (``<requested> → <primary subtag> → site default``). Returns the highest-
    preference tag the client asked for that ``available`` can serve, matching
    either exactly (``es-MX`` -> ``es-MX``) or by primary subtag (``es-MX`` -> ``es``),
    case-insensitively. A ``*`` wildcard selects the first available language. If
    nothing matches — including ``None`` or an empty header — returns
    :data:`DEFAULT_LANG` (safety: a known-good fallback, never blank).
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


@cache
def _messages_for(lang: str) -> dict[str, str]:
    """Cache the resolved singular-message map per language (pure, read-only)."""
    return _messages(get_translation(lang))


@cache
def _cw_for(lang: str) -> dict[str, str]:
    """Cache the resolved content-warning gloss map per language (pure, read-only)."""
    return _cw_glosses(get_translation(lang))


def _coerce_count(value: object) -> int:
    """Best-effort integer for plural selection; a bad value degrades to 0."""
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def t(lang: str, key: str, /, **kw: object) -> str:
    """Look up a UI string by ``key`` in ``lang``, formatting it with ``kw``.

    Resolution order: the requested language's catalog, then the English source
    (via gettext ``fallback=True``), then — if the key is unknown — the key itself.
    A plural key (:data:`_PLURAL_KEYS`) is resolved with ``ngettext`` on ``count``.
    Formatting is forgiving: a template that references a placeholder not supplied in
    ``kw`` is returned unformatted rather than raising, so a catalog/caller mismatch
    can never take a page down. This function never raises (safety, robustness).
    """
    if key in _PLURAL_KEYS:
        template = _plural_message(get_translation(lang), key, _coerce_count(kw.get("count", 0)))
    else:
        template = _messages_for(lang).get(key)
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

    Resolution order: the requested language, then English (gettext fallback), then a
    humanized form of the tag itself (hyphens/underscores to spaces, capitalized).
    This means a tag the catalog has never seen still renders as a readable label
    instead of a raw slug, so the controlled vocabulary can grow without code changes
    (robustness).
    """
    glosses = _cw_for(lang)
    if tag in glosses:
        return glosses[tag]
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
