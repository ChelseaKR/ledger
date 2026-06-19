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

Text-only by design for now: a contribution carries a title, a descriptive account,
content warnings drawn from the archive's controlled vocabulary, a requested
visibility, and an optional sealed contact. Binary payload upload (audio, image,
PDF) is a deliberate follow-on — it needs multipart parsing and stricter abuse
controls than this first, safe slice, and the steward CLI already covers it.

This module is HTTP-agnostic: it renders the form markup and parses a posted form
into a :class:`~ledger.models.Record` (plus an optional identity). The server wires
it to a request, and the *one* ingest path does the sealing and bagging.
"""

from __future__ import annotations

import html
from dataclasses import dataclass

from ledger.config import Config
from ledger.errors import LedgerError
from ledger.identity import ContributorIdentity
from ledger.models import AccessPolicy, DublinCore, Field, Record

# Requested visibility -> the policy of the contributor's account field. The record
# itself always defaults to sealed-pending regardless, so this choice only matters
# once a steward reviews the record and opens it up.
_VISIBILITY_TO_POLICY: dict[str, AccessPolicy] = {
    "public": AccessPolicy.PUBLIC,
    "community": AccessPolicy.COMMUNITY,
    "sealed": AccessPolicy.SEALED_UNTIL,
}
_VISIBILITY_LABELS: dict[str, str] = {
    "public": "Public — anyone may read it once a steward approves",
    "community": "Community only — vetted members of this community",
    "sealed": "Sealed — keep it withheld for now",
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


def _checkbox(warning: str) -> str:
    """One labelled content-warning checkbox (id + matching ``<label for>``)."""
    cid = f"cw-{warning}"
    return (
        f'        <div class="cw-option">\n'
        f'          <input type="checkbox" id="{_esc(cid)}" name="cw_{_esc(warning)}" value="1">\n'
        f'          <label for="{_esc(cid)}">{_esc(warning)}</label>\n'
        f"        </div>\n"
    )


def _visibility_radio(value: str, *, checked: bool) -> str:
    """One labelled visibility radio (id + matching ``<label for>``)."""
    rid = f"vis-{value}"
    mark = " checked" if checked else ""
    return (
        f'        <div class="vis-option">\n'
        f'          <input type="radio" id="{_esc(rid)}" name="visibility" '
        f'value="{_esc(value)}"{mark}>\n'
        f'          <label for="{_esc(rid)}">{_esc(_VISIBILITY_LABELS[value])}</label>\n'
        f"        </div>\n"
    )


def render_contribute_main(config: Config, *, error: str | None = None) -> str:
    """Render the ``<main>`` for the accessible contribution form.

    Every control is labelled (``<label for>``), grouped choices sit in a
    ``<fieldset>`` with a ``<legend>``, and the page is plain about what happens to a
    submission: it is reviewed before publishing, and any contact details are sealed
    and never shown. The markup is authored to pass the structural accessibility
    gate (one ``<h1>``, labelled inputs, no positive tabindex).
    """
    cw_options = "".join(_checkbox(w) for w in config.content_warnings)
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
        f"{_visibility_radio('public', checked=False)}"
        f"{_visibility_radio('community', checked=True)}"
        f"{_visibility_radio('sealed', checked=False)}"
        "      </fieldset>\n"
    )
    error_html = f'    <p class="error" role="alert">{_esc(error)}</p>\n' if error else ""
    return (
        "    <h1>Contribute to the archive</h1>\n"
        "    <p>Share a story, an account, or knowledge worth keeping. A steward "
        "reviews every submission before anything is published — your contribution "
        "is kept sealed until then.</p>\n"
        f"{error_html}"
        '    <form class="contribute" method="post" action="/contribute">\n'
        "      <p>\n"
        '        <label for="title">Title</label>\n'
        '        <input type="text" id="title" name="title" required maxlength="200">\n'
        "      </p>\n"
        "      <p>\n"
        '        <label for="account">Your account</label>\n'
        '        <textarea id="account" name="account" rows="10" required '
        'maxlength="20000"></textarea>\n'
        "      </p>\n"
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
        'maxlength="1000">\n'
        "        </p>\n"
        "        <p>\n"
        '          <label for="contributor_contact">How to reach you</label>\n'
        '          <input type="text" id="contributor_contact" '
        'name="contributor_contact" maxlength="1000">\n'
        "        </p>\n"
        "      </fieldset>\n"
        '      <p><button type="submit">Submit for review</button></p>\n'
        "    </form>\n"
    )


def render_thanks_main() -> str:
    """Render the ``<main>`` confirmation shown after a submission.

    Deliberately generic: it confirms receipt and review without echoing the title,
    the account, or any contact detail back, so nothing a contributor typed — least
    of all their identity — is reflected onto a page or into a log (no-outing rule).
    """
    return (
        "    <h1>Thank you — your contribution was received</h1>\n"
        '    <p role="status">It is sealed and waiting for a steward to review it. '
        "Nothing you submitted is public yet, and any contact details you gave are "
        "encrypted and will never be shown.</p>\n"
        '    <p><a href="/">Back to the archive</a></p>\n'
    )
