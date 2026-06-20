"""Contributor-facing submission — the one place a contributor, not a steward at a
CLI, can add a record to the archive.

Two of ledger's hard rules shape every line:

* **Narrowest disclosure by default (Hard Rule 2).** A submission is *never*
  published by the act of submitting. The record is created sealed-pending, so a
  steward must review it before it becomes listable — nothing goes public by
  inaction. The contributor's *requested* visibility is recorded on the account
  field, but it only takes effect once a steward opens the record up.
* **No-outing (Hard Rule 1).** A contributor's name and contact are optional and,
  when given, are sealed into the identity vault by the one ingest path. They are
  never echoed back on the confirmation page, in a log, or in an error.

A contribution carries a title, a descriptive account, content warnings drawn from
the archive's controlled vocabulary, a requested visibility, an optional sealed
contact, and — since backlog A2 — an optional single binary file (image, audio, or
PDF). The file is validated by its *bytes*, not its filename or declared type: the
server sniffs the leading magic bytes against a small allowlist
(:mod:`ledger.upload`) and refuses anything it does not recognise, so the form is a
safe upload surface rather than an arbitrary-file sink.

This module is HTTP-agnostic: it renders the form markup (including the file input)
and parses the posted text fields into a :class:`~ledger.models.Record` (plus an
optional identity). The server reads any attached file, validates it, and hands the
bytes to the *one* ingest path, which does the sealing, hashing, and bagging. Like
every other part of a submission, an attached file lands sealed-pending — invisible
until a steward reviews it.
"""

from __future__ import annotations

import html
from collections.abc import Mapping
from dataclasses import dataclass, replace

from ledger import i18n, upload
from ledger.config import Config
from ledger.errors import ValidationError
from ledger.identity import ContributorIdentity
from ledger.models import AccessPolicy, DisclosedRecord, DublinCore, Field, Record

# Requested visibility -> the policy of the contributor's account field. The record
# itself always defaults to sealed-pending regardless, so this choice only matters
# once a steward reviews the record and opens it up. The human label for each is
# localized (i18n key ``visibility_<value>``), since "sealed / community / public"
# are the safety-critical words a contributor most needs in their own language.
_VISIBILITY_TO_POLICY: dict[str, AccessPolicy] = {
    "public": AccessPolicy.PUBLIC,
    "community": AccessPolicy.COMMUNITY,
    "sealed": AccessPolicy.SEALED_UNTIL,
}
_DEFAULT_VISIBILITY = "community"

# Bounds so an oversized submission cannot exhaust memory or storage (robustness).
# The server also caps the raw POST body; these bound the meaningful fields.
_MAX_TITLE = 200
_MAX_ACCOUNT = 20_000
_MAX_CONTACT = 1_000
_MAX_SUMMARY = 500
_MAX_DC_VALUE = 200  # one subject/type/date/language value
_MAX_SUBJECTS = 12  # distinct subjects per record


@dataclass(frozen=True)
class Submission:
    """A parsed contribution: the record to ingest and an optional sealed identity."""

    record: Record
    identity: ContributorIdentity | None


def _esc(value: object) -> str:
    """The single text-to-HTML boundary for this module's markup (no XSS)."""
    return html.escape(str(value), quote=True)


def parse_submission(form: dict[str, str], config: Config) -> Submission:
    """Build a ``(record, identity)`` pair from a posted contribution form.

    Validates and bounds the inputs and constructs a record that is *sealed-pending*
    by default — invisible to the public until a steward reviews it. Content
    warnings are filtered against the archive's controlled vocabulary, so a crafted
    form cannot inject an arbitrary tag. Raises :class:`~ledger.errors.ValidationError`
    with a content-free, *localizable* reason code on invalid input — it never echoes a
    submitted value (no-outing rule).
    """
    title = (form.get("title") or "").strip()
    account = (form.get("account") or "").strip()
    if not title:
        raise ValidationError("a title is required", code="err_title_required")
    if not account:
        raise ValidationError("an account is required", code="err_account_required")
    summary = (form.get("summary") or "").strip()
    if len(title) > _MAX_TITLE or len(account) > _MAX_ACCOUNT or len(summary) > _MAX_SUMMARY:
        raise ValidationError("the submission is too long", code="err_submission_too_long")

    visibility = (form.get("visibility") or _DEFAULT_VISIBILITY).strip()
    field_policy = _VISIBILITY_TO_POLICY.get(visibility, AccessPolicy.SEALED_UNTIL)

    # Only values in the archive's controlled vocabulary are kept, in vocabulary
    # order, so the set is deterministic and a crafted key cannot inject a tag.
    warnings = [w for w in config.content_warnings if form.get(f"cw_{w}")]

    # Optional descriptive Dublin Core, so a contributed record is findable by topic,
    # browsable by facet (subject/type/language), datable for scholarship, and richer
    # in the feed/OAI — not just a bare title (user research P1-4/P2-3). Subjects are
    # comma-separated into a list; the rest are single values. Like the summary, these
    # are record-level descriptive metadata disclosed to whoever may list the record,
    # so they carry no contributor identity and follow the record's own visibility.
    subjects = [s.strip() for s in (form.get("subject") or "").split(",") if s.strip()]
    dc_type = (form.get("type") or "").strip()
    dc_date = (form.get("date") or "").strip()
    dc_language = (form.get("language") or "").strip()
    if (
        len(subjects) > _MAX_SUBJECTS
        or any(len(s) > _MAX_DC_VALUE for s in subjects)
        or len(dc_type) > _MAX_DC_VALUE
        or len(dc_date) > _MAX_DC_VALUE
        or len(dc_language) > _MAX_DC_VALUE
    ):
        raise ValidationError("the submission is too long", code="err_submission_too_long")

    # An optional one-line summary becomes the Dublin Core ``description`` — the
    # listing/feed/OAI teaser. It is record-level descriptive metadata, disclosed to
    # whoever may list the record (i.e. the same audience the requested visibility
    # opens it to once published), so a contributor's account stays in its own
    # policy-gated field while listings get a real summary instead of a bare title
    # (user research P2-3, minimum metadata).
    dublin_core = DublinCore(title=[title], publisher=[config.archive_name])
    if summary:
        dublin_core.description = [summary]
    if subjects:
        dublin_core.subject = subjects
    if dc_type:
        dublin_core.type = [dc_type]
    if dc_date:
        dublin_core.date = [dc_date]
    if dc_language:
        dublin_core.language = [dc_language]

    record = Record(
        title=title,
        default_policy=AccessPolicy.SEALED_UNTIL,  # sealed-pending steward review
        dublin_core=dublin_core,
        fields=[Field(name="account", value=account, policy=field_policy)],
        content_warnings=warnings,
    )

    name = (form.get("contributor_name") or "").strip()
    contact = (form.get("contributor_contact") or "").strip()
    if len(name) > _MAX_CONTACT or len(contact) > _MAX_CONTACT:
        raise ValidationError("the contact details are too long", code="err_contact_too_long")
    identity = ContributorIdentity(name=name, contact=contact) if (name or contact) else None
    return Submission(record=record, identity=identity)


_POLICY_TO_VISIBILITY: dict[AccessPolicy, str] = {
    policy: visibility for visibility, policy in _VISIBILITY_TO_POLICY.items()
}


def current_visibility(record: Record) -> str:
    """The requested-visibility keyword (public/community/sealed) a record carries.

    Read back from the account field's policy so an edit form can pre-select the
    contributor's existing choice. Falls back to the default if absent."""
    field = record.field_named("account")
    if field is None:
        return _DEFAULT_VISIBILITY
    return _POLICY_TO_VISIBILITY.get(field.policy, _DEFAULT_VISIBILITY)


def apply_edit(existing: Record, form: dict[str, str], config: Config) -> Record:
    """Return ``existing`` updated from an edit ``form``, preserving its identity.

    A contributor correcting a *still-pending* submission may change the title, the
    account, the requested visibility, and the content warnings — exactly the fields
    :func:`parse_submission` validates — so this reuses that validation and then
    transplants the validated values onto the existing record, keeping its
    ``record_id``, its sealed ``identity_ref``, any payloads, its creation time, and
    its sealed-pending ``default_policy`` unchanged. The contributor's sealed contact
    is deliberately *not* editable here (changing it would mean re-keying the vault);
    a contributor who needs to change contact withdraws and resubmits. Raises
    :class:`~ledger.errors.ValidationError` on invalid input, naming no value.
    """
    validated = parse_submission(form, config).record
    v = validated.dublin_core
    updated_dc = replace(
        existing.dublin_core,
        title=[validated.title],
        description=list(v.description),
        subject=list(v.subject),
        type=list(v.type),
        date=list(v.date),
        language=list(v.language),
    )
    return replace(
        existing,
        title=validated.title,
        dublin_core=updated_dc,
        fields=validated.fields,
        content_warnings=validated.content_warnings,
    )


def _checkbox(warning: str, *, checked: bool = False) -> str:
    """One labelled content-warning checkbox (id + matching ``<label for>``)."""
    cid = f"cw-{warning}"
    mark = " checked" if checked else ""
    return (
        f'        <div class="cw-option">\n'
        f'          <input type="checkbox" id="{_esc(cid)}" name="cw_{_esc(warning)}" '
        f'value="1"{mark}>\n'
        f'          <label for="{_esc(cid)}">{_esc(warning)}</label>\n'
        f"        </div>\n"
    )


def _details_fieldset(lang: str, vals: Mapping[str, str]) -> str:
    """The optional descriptive-metadata fieldset shared by the contribute/edit forms.

    Renders labelled, hinted single-line inputs for the Dublin Core ``subject`` (comma
    separated), ``type``, ``date``, and ``language`` so a contributor can make their
    record findable by topic and browsable by facet. Each input is associated with its
    hint via ``aria-describedby`` (accessibility); every value is escaped (security)."""

    def field(name: str, label_key: str, hint_key: str, *, maxlength: int) -> str:
        hint_id = f"{name}-hint"
        return (
            "        <p>\n"
            f'          <label for="{name}">{_esc(i18n.t(lang, label_key))}</label>\n'
            f'          <span class="hint" id="{hint_id}">{_esc(i18n.t(lang, hint_key))}</span>\n'
            f'          <input type="text" id="{name}" name="{name}" maxlength="{maxlength}" '
            f'aria-describedby="{hint_id}" value="{_esc(vals.get(name, ""))}">\n'
            "        </p>\n"
        )

    return (
        "      <fieldset>\n"
        f"        <legend>{_esc(i18n.t(lang, 'details_legend'))}</legend>\n"
        f'        <p class="hint">{_esc(i18n.t(lang, "details_hint"))}</p>\n'
        f"{field('subject', 'label_subject', 'subject_hint', maxlength=1000)}"
        f"{field('type', 'label_type', 'type_hint', maxlength=200)}"
        f"{field('date', 'label_date', 'date_hint', maxlength=200)}"
        f"{field('language', 'label_language', 'language_hint', maxlength=200)}"
        "      </fieldset>\n"
    )


def preview_record(submission: Submission) -> Record:
    """The record as it would exist *once published* at the requested visibility.

    A submission is stored sealed-pending; for an honest "what a stranger sees"
    preview we simulate the published state by opening the default policy to the
    visibility the contributor asked for (carried on the account field). Pure — it
    builds a copy and stores nothing.
    """
    requested = next(
        (f.policy for f in submission.record.fields if f.name == "account"),
        AccessPolicy.SEALED_UNTIL,
    )
    return replace(submission.record, default_policy=requested)


def _visibility_radio(value: str, *, checked: bool, lang: str) -> str:
    """One labelled visibility radio (id + matching ``<label for>``), label localized."""
    rid = f"vis-{value}"
    mark = " checked" if checked else ""
    label = i18n.t(lang, f"visibility_{value}")
    return (
        f'        <div class="vis-option">\n'
        f'          <input type="radio" id="{_esc(rid)}" name="visibility" '
        f'value="{_esc(value)}"{mark}>\n'
        f'          <label for="{_esc(rid)}">{_esc(label)}</label>\n'
        f"        </div>\n"
    )


def render_contribute_main(
    config: Config,
    *,
    lang: str = i18n.DEFAULT_LANG,
    error: str | None = None,
    values: Mapping[str, str] | None = None,
    preview_html: str | None = None,
) -> str:
    """Render the ``<main>`` for the accessible contribution form.

    Every control is labelled (``<label for>``), grouped choices sit in a
    ``<fieldset>`` with a ``<legend>``, and the page is plain about what happens to a
    submission: it is reviewed before publishing, and any contact details are sealed
    and never shown. The markup is authored to pass the structural accessibility
    gate (one ``<h1>``, labelled inputs, no positive tabindex).

    ``values`` re-fills the form after a preview or a validation error, so a
    contributor never loses what they typed. ``preview_html`` is an optional panel
    (built by :func:`render_preview_panel`) shown above the form after a *Preview*,
    so the contributor sees exactly what a stranger would see, on the same page,
    right next to the entries they can still edit. Both **Preview** and **Submit**
    buttons are always present — preview is encouraged, never a forced extra page
    carrying their sealed contact.
    """
    vals = values or {}
    selected_vis = vals.get("visibility") or _DEFAULT_VISIBILITY
    cw_options = "".join(
        _checkbox(w, checked=bool(vals.get(f"cw_{w}"))) for w in config.content_warnings
    )
    cw_fieldset = (
        "      <fieldset>\n"
        f"        <legend>{_esc(i18n.t(lang, 'cw_legend'))}</legend>\n"
        f'        <p class="hint">{_esc(i18n.t(lang, "cw_hint"))}</p>\n'
        f"{cw_options}"
        "      </fieldset>\n"
        if config.content_warnings
        else ""
    )
    vis_fieldset = (
        "      <fieldset>\n"
        f"        <legend>{_esc(i18n.t(lang, 'share_legend'))}</legend>\n"
        f'        <p class="hint">{_esc(i18n.t(lang, "share_hint"))}</p>\n'
        f"{_visibility_radio('public', checked=selected_vis == 'public', lang=lang)}"
        f"{_visibility_radio('community', checked=selected_vis == 'community', lang=lang)}"
        f"{_visibility_radio('sealed', checked=selected_vis == 'sealed', lang=lang)}"
        "      </fieldset>\n"
    )
    accepted = ", ".join(t.split("/", 1)[1].upper() for t in upload.ALLOWED_TYPES)
    max_mb = upload.MAX_UPLOAD_BYTES // (1024 * 1024)
    file_fieldset = (
        "      <fieldset>\n"
        f"        <legend>{_esc(i18n.t(lang, 'file_legend'))}</legend>\n"
        f'        <p class="hint">{_esc(i18n.t(lang, "file_hint", max=max_mb, accepted=accepted))}'
        "</p>\n"
        "        <p>\n"
        f'          <label for="upload">{_esc(i18n.t(lang, "label_file"))}</label>\n'
        '          <input type="file" id="upload" name="upload">\n'
        "        </p>\n"
        "      </fieldset>\n"
    )
    error_html = f'    <p class="error" role="alert">{_esc(error)}</p>\n' if error else ""
    preview_panel = preview_html or ""
    return (
        f"    <h1>{_esc(i18n.t(lang, 'contribute_heading'))}</h1>\n"
        f"    <p>{_esc(i18n.t(lang, 'contribute_intro'))}</p>\n"
        f"{error_html}"
        f"{preview_panel}"
        '    <form class="contribute" method="post" action="/contribute" '
        'enctype="multipart/form-data">\n'
        "      <p>\n"
        f'        <label for="title">{_esc(i18n.t(lang, "label_title"))}</label>\n'
        f'        <input type="text" id="title" name="title" required maxlength="200" '
        f'value="{_esc(vals.get("title", ""))}">\n'
        "      </p>\n"
        "      <p>\n"
        f'        <label for="summary">{_esc(i18n.t(lang, "label_summary"))}</label>\n'
        f'        <span class="hint" id="summary-hint">{_esc(i18n.t(lang, "summary_hint"))}</span>\n'
        '        <input type="text" id="summary" name="summary" maxlength="500" '
        f'aria-describedby="summary-hint" value="{_esc(vals.get("summary", ""))}">\n'
        "      </p>\n"
        "      <p>\n"
        f'        <label for="account">{_esc(i18n.t(lang, "label_account"))}</label>\n'
        '        <textarea id="account" name="account" rows="10" required '
        f'maxlength="20000">{_esc(vals.get("account", ""))}</textarea>\n'
        "      </p>\n"
        f"{_details_fieldset(lang, vals)}"
        f"{file_fieldset}"
        f"{vis_fieldset}"
        f"{cw_fieldset}"
        "      <fieldset>\n"
        f"        <legend>{_esc(i18n.t(lang, 'contact_legend'))}</legend>\n"
        f'        <p class="hint">{_esc(i18n.t(lang, "contact_hint"))}</p>\n'
        "        <p>\n"
        f'          <label for="contributor_name">{_esc(i18n.t(lang, "label_name"))}</label>\n'
        '          <input type="text" id="contributor_name" name="contributor_name" '
        f'maxlength="1000" value="{_esc(vals.get("contributor_name", ""))}">\n'
        "        </p>\n"
        "        <p>\n"
        '          <label for="contributor_contact">'
        f"{_esc(i18n.t(lang, 'label_reach'))}</label>\n"
        '          <input type="text" id="contributor_contact" '
        f'name="contributor_contact" maxlength="1000" '
        f'value="{_esc(vals.get("contributor_contact", ""))}">\n'
        "        </p>\n"
        "      </fieldset>\n"
        '      <p><button type="submit" name="action" value="preview">'
        f"{_esc(i18n.t(lang, 'button_preview'))}</button>\n"
        '      <button type="submit" name="action" value="submit">'
        f"{_esc(i18n.t(lang, 'button_submit'))}</button></p>\n"
        "    </form>\n"
    )


def render_preview_panel(
    stranger_view: DisclosedRecord | None,
    *,
    visibility: str,
    lang: str = i18n.DEFAULT_LANG,
) -> str:
    """Render the "what a stranger would see" panel shown above the form on Preview.

    ``stranger_view`` is the record disclosed to the anonymous public *if it were
    published at the requested visibility*, or ``None`` when a stranger could not see
    it at all (community/sealed). The panel is honest about exactly what is and is
    not exposed; it contains only the stranger's view, so the contributor's sealed
    contact never appears in it. The contributor's own entries live in the form
    below (prefilled), exactly as they typed them — not reflected to anyone else. All
    chrome is localized so a non-English contributor can trust what it tells them."""
    if stranger_view is not None:
        cw = (
            "      <p>"
            + _esc(
                i18n.t(
                    lang,
                    "preview_content_warnings",
                    list=", ".join(stranger_view.content_warnings),
                )
            )
            + "</p>\n"
            if stranger_view.content_warnings
            else ""
        )
        account = stranger_view.fields.get("account", "")
        inner = (
            f"      <p>{_esc(i18n.t(lang, 'preview_if_published'))}</p>\n"
            f"      <h3>{_esc(stranger_view.title)}</h3>\n"
            f"{cw}"
            f"      <p>{_esc(account)}</p>\n"
        )
    else:
        audience = i18n.t(
            lang,
            "preview_audience_community" if visibility == "community" else "preview_audience_none",
        )
        detail = i18n.t(
            lang, "preview_stranger_nothing_detail", visibility=visibility, audience=audience
        )
        inner = (
            f"      <p><strong>{_esc(i18n.t(lang, 'preview_stranger_nothing_lead'))}</strong>"
            f"{_esc(detail)}</p>\n"
        )
    return (
        '    <section class="preview" role="status" '
        f'aria-label="{_esc(i18n.t(lang, "preview_aria_label"))}">\n'
        f"      <h2>{_esc(i18n.t(lang, 'preview_heading'))}</h2>\n"
        f"{inner}"
        f'      <p class="hint">{_esc(i18n.t(lang, "preview_sealed_hint"))}</p>\n'
        "    </section>\n"
    )


def render_thanks_main(
    *,
    reference: str | None = None,
    claim_token: str | None = None,
    lang: str = i18n.DEFAULT_LANG,
) -> str:
    """Render the ``<main>`` confirmation shown after a submission.

    Deliberately generic about *content*: it confirms receipt and review without
    echoing the title, the account, or any contact detail back, so nothing a
    contributor typed — least of all their identity — is reflected onto a page or
    into a log (no-outing rule).

    When the server can issue one, it also shows a ``reference`` (the record id) and a
    ``claim_token`` (a *capability*, never an identity) the contributor can keep to
    **withdraw** the submission themselves before a steward publishes it. Showing
    these does not breach no-outing: neither says who contributed — they only prove
    authorship of this one record. The page tells the contributor to keep them
    private, since together they authorise withdrawal."""
    if reference and claim_token:
        link = f'<a href="/withdraw">{_esc(i18n.t(lang, "withdrawal_page_link_text"))}</a>'
        edit_link = f'<a href="/edit">{_esc(i18n.t(lang, "edit_page_link_text"))}</a>'
        withdraw_block = (
            '    <section class="claim" aria-labelledby="claim-heading">\n'
            f'      <h2 id="claim-heading">{_esc(i18n.t(lang, "thanks_claim_heading"))}</h2>\n'
            f"      <p>{_esc(i18n.t(lang, 'thanks_claim_intro'))}</p>\n"
            "      <dl>\n"
            f"        <dt>{_esc(i18n.t(lang, 'label_reference'))}</dt>\n"
            f"        <dd><code>{_esc(reference)}</code></dd>\n"
            f"        <dt>{_esc(i18n.t(lang, 'label_withdrawal_code'))}</dt>\n"
            f"        <dd><code>{_esc(claim_token)}</code></dd>\n"
            "      </dl>\n"
            f"      <p>{_esc(i18n.t(lang, 'thanks_withdraw_before'))} {link} "
            f"{_esc(i18n.t(lang, 'thanks_withdraw_after'))}</p>\n"
            f"      <p>{_esc(i18n.t(lang, 'thanks_edit_before'))} {edit_link}.</p>\n"
            "    </section>\n"
        )
    else:
        withdraw_block = ""
    return (
        f"    <h1>{_esc(i18n.t(lang, 'thanks_heading'))}</h1>\n"
        f'    <p role="status">{_esc(i18n.t(lang, "thanks_status"))}</p>\n'
        f"{withdraw_block}"
        f'    <p><a href="/">{_esc(i18n.t(lang, "back_to_archive"))}</a></p>\n'
    )


def render_withdraw_main(
    *, error: str | None = None, reference: str = "", lang: str = i18n.DEFAULT_LANG
) -> str:
    """Render the ``<main>`` for the self-service withdrawal form.

    A contributor who kept the reference and withdrawal code from their confirmation
    can withdraw a submission that *is still pending review* — honouring "I changed my
    mind before it went live" without a steward in the loop, because nothing is public
    yet and it is their own content (consent is revocable). The form is accessible
    (labelled inputs) and plain about what withdrawal does, in the reader's language.
    The reference is re-filled on error so a mistyped code does not lose it; the code
    itself is never echoed back.
    """
    error_html = f'    <p class="error" role="alert">{_esc(error)}</p>\n' if error else ""
    return (
        f"    <h1>{_esc(i18n.t(lang, 'withdraw_heading'))}</h1>\n"
        f"    <p>{_esc(i18n.t(lang, 'withdraw_intro'))}</p>\n"
        f"{error_html}"
        '    <form class="withdraw" method="post" action="/withdraw">\n'
        "      <p>\n"
        f'        <label for="ref">{_esc(i18n.t(lang, "label_reference"))}</label>\n'
        f'        <input type="text" id="ref" name="ref" required '
        f'value="{_esc(reference)}">\n'
        "      </p>\n"
        "      <p>\n"
        f'        <label for="claim">{_esc(i18n.t(lang, "label_withdrawal_code"))}</label>\n'
        '        <input type="text" id="claim" name="claim" required '
        'autocomplete="off">\n'
        "      </p>\n"
        f'      <p><button type="submit">{_esc(i18n.t(lang, "withdraw_button"))}</button></p>\n'
        "    </form>\n"
    )


def render_withdraw_done_main(*, lang: str = i18n.DEFAULT_LANG) -> str:
    """Render the ``<main>`` shown after a successful withdrawal.

    Generic by design: it confirms removal without naming the record's title or any
    detail of what was withdrawn, so the confirmation reflects nothing back
    (no-outing rule)."""
    return (
        f"    <h1>{_esc(i18n.t(lang, 'withdraw_done_heading'))}</h1>\n"
        f'    <p role="status">{_esc(i18n.t(lang, "withdraw_done_status"))}</p>\n'
        f'    <p><a href="/">{_esc(i18n.t(lang, "back_to_archive"))}</a></p>\n'
    )


def render_edit_main(
    config: Config,
    *,
    lang: str = i18n.DEFAULT_LANG,
    values: Mapping[str, str] | None = None,
    error: str | None = None,
) -> str:
    """Render the ``<main>`` for editing a *pending* submission.

    One form gates and edits in two steps: the contributor enters their reference and
    code, chooses **Load my submission** to pull the current values in, edits, then
    chooses **Save changes**. The reference and code ride along as form fields so each
    POST re-proves authorship; they are the contributor's own capability, shown back
    only to them after they supplied them. The contact section and file upload are
    intentionally absent — only the title, account, visibility, and content warnings
    are editable (changing the sealed contact would mean re-keying the vault). All
    chrome is localized; every value is escaped (security).
    """
    vals = values or {}
    selected_vis = vals.get("visibility") or _DEFAULT_VISIBILITY
    cw_options = "".join(
        _checkbox(w, checked=bool(vals.get(f"cw_{w}"))) for w in config.content_warnings
    )
    cw_fieldset = (
        "      <fieldset>\n"
        f"        <legend>{_esc(i18n.t(lang, 'cw_legend'))}</legend>\n"
        f'        <p class="hint">{_esc(i18n.t(lang, "cw_hint"))}</p>\n'
        f"{cw_options}"
        "      </fieldset>\n"
        if config.content_warnings
        else ""
    )
    vis_fieldset = (
        "      <fieldset>\n"
        f"        <legend>{_esc(i18n.t(lang, 'share_legend'))}</legend>\n"
        f'        <p class="hint">{_esc(i18n.t(lang, "share_hint"))}</p>\n'
        f"{_visibility_radio('public', checked=selected_vis == 'public', lang=lang)}"
        f"{_visibility_radio('community', checked=selected_vis == 'community', lang=lang)}"
        f"{_visibility_radio('sealed', checked=selected_vis == 'sealed', lang=lang)}"
        "      </fieldset>\n"
    )
    error_html = f'    <p class="error" role="alert">{_esc(error)}</p>\n' if error else ""
    return (
        f"    <h1>{_esc(i18n.t(lang, 'edit_heading'))}</h1>\n"
        f"    <p>{_esc(i18n.t(lang, 'edit_intro'))}</p>\n"
        f"{error_html}"
        '    <form class="edit" method="post" action="/edit">\n'
        "      <p>\n"
        f'        <label for="ref">{_esc(i18n.t(lang, "label_reference"))}</label>\n'
        f'        <input type="text" id="ref" name="ref" required value="{_esc(vals.get("ref", ""))}">\n'
        "      </p>\n"
        "      <p>\n"
        f'        <label for="claim">{_esc(i18n.t(lang, "label_code"))}</label>\n'
        f'        <input type="text" id="claim" name="claim" required '
        f'value="{_esc(vals.get("claim", ""))}">\n'
        "      </p>\n"
        "      <p>\n"
        f'        <label for="title">{_esc(i18n.t(lang, "label_title"))}</label>\n'
        f'        <input type="text" id="title" name="title" maxlength="200" '
        f'value="{_esc(vals.get("title", ""))}">\n'
        "      </p>\n"
        "      <p>\n"
        f'        <label for="summary">{_esc(i18n.t(lang, "label_summary"))}</label>\n'
        '        <input type="text" id="summary" name="summary" maxlength="500" '
        f'value="{_esc(vals.get("summary", ""))}">\n'
        "      </p>\n"
        "      <p>\n"
        f'        <label for="account">{_esc(i18n.t(lang, "label_account"))}</label>\n'
        '        <textarea id="account" name="account" rows="10" '
        f'maxlength="20000">{_esc(vals.get("account", ""))}</textarea>\n'
        "      </p>\n"
        f"{_details_fieldset(lang, vals)}"
        f"{vis_fieldset}"
        f"{cw_fieldset}"
        '      <p><button type="submit" name="action" value="load">'
        f"{_esc(i18n.t(lang, 'edit_load_button'))}</button>\n"
        '      <button type="submit" name="action" value="save">'
        f"{_esc(i18n.t(lang, 'edit_save_button'))}</button></p>\n"
        "    </form>\n"
    )


def render_edit_done_main(*, lang: str = i18n.DEFAULT_LANG) -> str:
    """Render the ``<main>`` shown after a pending submission is successfully edited.

    Confirms the update without echoing the new title or account back, so the
    confirmation reflects nothing (no-outing rule)."""
    return (
        f"    <h1>{_esc(i18n.t(lang, 'edit_done_heading'))}</h1>\n"
        f'    <p role="status">{_esc(i18n.t(lang, "edit_done_status"))}</p>\n'
        f'    <p><a href="/">{_esc(i18n.t(lang, "back_to_archive"))}</a></p>\n'
    )
