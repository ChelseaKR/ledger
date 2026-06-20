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
from ledger.errors import LedgerError
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
    form cannot inject an arbitrary tag. Raises :class:`~ledger.errors.LedgerError`
    with a generic, content-free message on invalid input — it never echoes a
    submitted value (no-outing rule).
    """
    title = (form.get("title") or "").strip()
    account = (form.get("account") or "").strip()
    if not title:
        raise LedgerError("a title is required")
    if not account:
        raise LedgerError("an account is required")
    if len(title) > _MAX_TITLE or len(account) > _MAX_ACCOUNT:
        raise LedgerError("the submission is too long")

    visibility = (form.get("visibility") or _DEFAULT_VISIBILITY).strip()
    field_policy = _VISIBILITY_TO_POLICY.get(visibility, AccessPolicy.SEALED_UNTIL)

    # Only values in the archive's controlled vocabulary are kept, in vocabulary
    # order, so the set is deterministic and a crafted key cannot inject a tag.
    warnings = [w for w in config.content_warnings if form.get(f"cw_{w}")]

    record = Record(
        title=title,
        default_policy=AccessPolicy.SEALED_UNTIL,  # sealed-pending steward review
        dublin_core=DublinCore(title=[title], publisher=[config.archive_name]),
        fields=[Field(name="account", value=account, policy=field_policy)],
        content_warnings=warnings,
    )

    name = (form.get("contributor_name") or "").strip()
    contact = (form.get("contributor_contact") or "").strip()
    if len(name) > _MAX_CONTACT or len(contact) > _MAX_CONTACT:
        raise LedgerError("the contact details are too long")
    identity = ContributorIdentity(name=name, contact=contact) if (name or contact) else None
    return Submission(record=record, identity=identity)


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
        "        <legend>Content warnings (optional)</legend>\n"
        '        <p class="hint">Tick anything a reader should be warned about before '
        "this is shown.</p>\n"
        f"{cw_options}"
        "      </fieldset>\n"
        if config.content_warnings
        else ""
    )
    vis_fieldset = (
        "      <fieldset>\n"
        "        <legend>How should this be shared?</legend>\n"
        '        <p class="hint">A steward reviews every submission before anything '
        "becomes visible — nothing is published automatically.</p>\n"
        f"{_visibility_radio('public', checked=selected_vis == 'public', lang=lang)}"
        f"{_visibility_radio('community', checked=selected_vis == 'community', lang=lang)}"
        f"{_visibility_radio('sealed', checked=selected_vis == 'sealed', lang=lang)}"
        "      </fieldset>\n"
    )
    accepted = ", ".join(t.split("/", 1)[1].upper() for t in upload.ALLOWED_TYPES)
    max_mb = upload.MAX_UPLOAD_BYTES // (1024 * 1024)
    file_fieldset = (
        "      <fieldset>\n"
        "        <legend>Attach a file (optional)</legend>\n"
        '        <p class="hint">You can attach one image, audio file, or PDF '
        f"(up to {max_mb} MB). Accepted: {_esc(accepted)}. The file is reviewed with "
        "the rest of your submission and is not public until a steward publishes it.</p>\n"
        "        <p>\n"
        '          <label for="upload">File</label>\n'
        '          <input type="file" id="upload" name="upload">\n'
        "        </p>\n"
        "      </fieldset>\n"
    )
    error_html = f'    <p class="error" role="alert">{_esc(error)}</p>\n' if error else ""
    preview_panel = preview_html or ""
    return (
        "    <h1>Contribute to the archive</h1>\n"
        "    <p>Share a story, an account, or knowledge worth keeping. A steward "
        "reviews every submission before anything is published — your contribution "
        'is kept sealed until then. Use <em>"Preview"</em> to see exactly what a '
        "stranger would see before you submit.</p>\n"
        f"{error_html}"
        f"{preview_panel}"
        '    <form class="contribute" method="post" action="/contribute" '
        'enctype="multipart/form-data">\n'
        "      <p>\n"
        '        <label for="title">Title</label>\n'
        f'        <input type="text" id="title" name="title" required maxlength="200" '
        f'value="{_esc(vals.get("title", ""))}">\n'
        "      </p>\n"
        "      <p>\n"
        '        <label for="account">Your account</label>\n'
        '        <textarea id="account" name="account" rows="10" required '
        f'maxlength="20000">{_esc(vals.get("account", ""))}</textarea>\n'
        "      </p>\n"
        f"{file_fieldset}"
        f"{vis_fieldset}"
        f"{cw_fieldset}"
        "      <fieldset>\n"
        "        <legend>Contact (optional, sealed)</legend>\n"
        '        <p class="hint">Only a steward with explicit permission can ever see '
        "this. It is encrypted, never shown publicly, and never reveals who "
        "contributed a record.</p>\n"
        "        <p>\n"
        '          <label for="contributor_name">Name</label>\n'
        '          <input type="text" id="contributor_name" name="contributor_name" '
        f'maxlength="1000" value="{_esc(vals.get("contributor_name", ""))}">\n'
        "        </p>\n"
        "        <p>\n"
        '          <label for="contributor_contact">How to reach you</label>\n'
        '          <input type="text" id="contributor_contact" '
        f'name="contributor_contact" maxlength="1000" '
        f'value="{_esc(vals.get("contributor_contact", ""))}">\n'
        "        </p>\n"
        "      </fieldset>\n"
        '      <p><button type="submit" name="action" value="preview">'
        "Preview what a stranger sees</button>\n"
        '      <button type="submit" name="action" value="submit">'
        "Submit for review</button></p>\n"
        "    </form>\n"
    )


def render_preview_panel(stranger_view: DisclosedRecord | None, *, visibility: str) -> str:
    """Render the "what a stranger would see" panel shown above the form on Preview.

    ``stranger_view`` is the record disclosed to the anonymous public *if it were
    published at the requested visibility*, or ``None`` when a stranger could not see
    it at all (community/sealed). The panel is honest about exactly what is and is
    not exposed; it contains only the stranger's view, so the contributor's sealed
    contact never appears in it. The contributor's own entries live in the form
    below (prefilled), exactly as they typed them — not reflected to anyone else."""
    if stranger_view is not None:
        cw = (
            "      <p>Content warnings: "
            + _esc(", ".join(stranger_view.content_warnings))
            + "</p>\n"
            if stranger_view.content_warnings
            else ""
        )
        account = stranger_view.fields.get("account", "")
        inner = (
            "      <p>If a steward publishes this, a stranger who is not signed in "
            "would see:</p>\n"
            f"      <h3>{_esc(stranger_view.title)}</h3>\n"
            f"{cw}"
            f"      <p>{_esc(account)}</p>\n"
        )
    else:
        audience = "community members only" if visibility == "community" else "no one yet"
        inner = (
            "      <p><strong>A stranger sees nothing.</strong> Published as "
            f"{_esc(visibility)}, this record would be visible to {_esc(audience)} — "
            "it would not appear in public browse or search.</p>\n"
        )
    return (
        '    <section class="preview" role="status" aria-label="What a stranger sees">\n'
        "      <h2>Preview — what a stranger would see</h2>\n"
        f"{inner}"
        '      <p class="hint">Your name and contact are never shown to any reader — '
        "they are sealed. They are not in this preview.</p>\n"
        "    </section>\n"
    )


def render_thanks_main(*, reference: str | None = None, claim_token: str | None = None) -> str:
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
        withdraw_block = (
            '    <section class="claim" aria-labelledby="claim-heading">\n'
            '      <h2 id="claim-heading">Keep this if you might change your mind</h2>\n'
            "      <p>While your submission is still waiting for review you can withdraw "
            "it yourself. To do that you will need both of these — keep them private, "
            "as together they let someone withdraw this submission:</p>\n"
            "      <dl>\n"
            "        <dt>Reference</dt>\n"
            f"        <dd><code>{_esc(reference)}</code></dd>\n"
            "        <dt>Withdrawal code</dt>\n"
            f"        <dd><code>{_esc(claim_token)}</code></dd>\n"
            "      </dl>\n"
            '      <p>To withdraw it, go to <a href="/withdraw">the withdrawal page</a> '
            "and enter both.</p>\n"
            "    </section>\n"
        )
    else:
        withdraw_block = ""
    return (
        "    <h1>Thank you — your contribution was received</h1>\n"
        '    <p role="status">It is sealed and waiting for a steward to review it. '
        "Nothing you submitted is public yet, and any contact details you gave are "
        "encrypted and will never be shown.</p>\n"
        f"{withdraw_block}"
        '    <p><a href="/">Back to the archive</a></p>\n'
    )


def render_withdraw_main(*, error: str | None = None, reference: str = "") -> str:
    """Render the ``<main>`` for the self-service withdrawal form.

    A contributor who kept the reference and withdrawal code from their confirmation
    can withdraw a submission that *is still pending review* — honouring "I changed my
    mind before it went live" without a steward in the loop, because nothing is public
    yet and it is their own content (consent is revocable). The form is accessible
    (labelled inputs) and plain about what withdrawal does. The reference is re-filled
    on error so a mistyped code does not lose it; the code itself is never echoed back.
    """
    error_html = f'    <p class="error" role="alert">{_esc(error)}</p>\n' if error else ""
    return (
        "    <h1>Withdraw a submission</h1>\n"
        "    <p>If you contributed something and it is <em>still waiting for review</em>, "
        "you can withdraw it here using the reference and withdrawal code from your "
        "confirmation page. Withdrawing permanently removes the submission and erases "
        "any contact details you sealed with it. Once a steward has published a record, "
        'use the <a href="/">archive</a> to request a change instead.</p>\n'
        f"{error_html}"
        '    <form class="withdraw" method="post" action="/withdraw">\n'
        "      <p>\n"
        '        <label for="ref">Reference</label>\n'
        f'        <input type="text" id="ref" name="ref" required '
        f'value="{_esc(reference)}">\n'
        "      </p>\n"
        "      <p>\n"
        '        <label for="claim">Withdrawal code</label>\n'
        '        <input type="text" id="claim" name="claim" required '
        'autocomplete="off">\n'
        "      </p>\n"
        '      <p><button type="submit">Withdraw this submission</button></p>\n'
        "    </form>\n"
    )


def render_withdraw_done_main() -> str:
    """Render the ``<main>`` shown after a successful withdrawal.

    Generic by design: it confirms removal without naming the record's title or any
    detail of what was withdrawn, so the confirmation reflects nothing back
    (no-outing rule)."""
    return (
        "    <h1>Your submission was withdrawn</h1>\n"
        '    <p role="status">It has been permanently removed, along with any contact '
        "details you had sealed with it. Nothing from it remains in the archive.</p>\n"
        '    <p><a href="/">Back to the archive</a></p>\n'
    )
