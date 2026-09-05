"""WCAG 2.2 **4.1.3 Status Messages** — every status message reaches a reader.

A status message tells you the outcome of what you just did: how many records the
search matched, that a filter matched nothing, that a submission was rejected, that
the steward queue could not be read. A sighted reader sees it appear. A screen-reader
user hears it only if it sits inside an ARIA live region, because nothing moved focus
there — so a message rendered outside one is, for them, simply not there.

This is the class the rest of the accessibility evidence cannot reach. The static
gate checks structure; axe evaluates the DOM it is handed and has no way to know
which paragraph is *about* an outcome; the browser job drives real Chromium but does
not listen. So the rule is enforced from the one mechanical fact available — ledger
renders every status message with one of a fixed set of classes — and the tests below
pin it from three directions:

* the rule itself (:func:`ledger.accessibility_check.check_html`), including the
  opposite failure it must also catch: an over-broad live region, which announces
  page structure on every change until the reader learns to ignore it;
* every dynamic state of every rendered surface — the empty and populated browse,
  search with no matches, the overview/places/timeline empty states, the
  content-warning interstitial, contribute and its error and confirmation, withdraw,
  edit, and a stale transparency attestation;
* the two surfaces that only exist behind a live server: the steward console with an
  empty queue, and ``/consent-status`` with a reference that matches nothing.

The surface tests carry a negative control. A test that renders a page and finds no
problems proves nothing unless the same checker, on the same page with the live
region removed, does find one — otherwise a checker that had quietly stopped looking
would read exactly like a clean surface.
"""

from __future__ import annotations

import json
import threading
import urllib.request
from collections.abc import Iterator
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import pytest

from ledger import contribute
from ledger.access.grants import anonymous, issue_grant_token
from ledger.accessibility_check import check_html
from ledger.config import Config
from ledger.ingest import Archive
from ledger.models import AccessPolicy, DublinCore, Field, Record
from ledger.render import (
    _browse_main_html,
    _overview_main_html,
    _page,
    _places_html,
    _record_main_html,
    _timeline_html,
    transparency_main_html,
)
from ledger.server import make_server
from ledger.transparency import TransparencyLog

_NOW = "2026-01-01T00:00:00Z"
_GRANT_SECRET = b"aria-live-test-grant-secret"


def _status_problems(markup: str, *, label: str = "doc") -> list[str]:
    """The 4.1.3 problems :func:`check_html` reports for ``markup``.

    The other structural checks are exercised by ``tests/test_accessibility_check.py``;
    filtering to the criterion under test keeps a failure here readable and stops an
    unrelated regression (a missing ``<caption>``, say) from being reported as a
    status-message defect.
    """
    return [problem for problem in check_html(markup, label=label) if "4.1.3" in problem]


# --- the rule ---------------------------------------------------------------


@pytest.mark.accessibility
def test_status_message_outside_a_live_region_is_a_problem() -> None:
    """A result count with no live region anywhere above it fails the check."""
    problems = _status_problems('<p class="count">Showing 1-10 of 42</p>')
    assert len(problems) == 1
    assert "outside any live region" in problems[0]
    assert 'class="count"' in problems[0]


@pytest.mark.accessibility
@pytest.mark.parametrize(
    "region",
    [
        '<div role="status">',
        '<div aria-live="polite">',
        '<div aria-live="assertive">',
        '<div role="alert">',
        '<div role="log">',
        # The element carrying the message may be the region itself.
        None,
    ],
)
def test_a_status_message_inside_a_live_region_passes(region: str | None) -> None:
    """Every ARIA spelling of "announce this" satisfies the rule."""
    markup = (
        '<p class="count" role="status">Showing 1-10 of 42</p>'
        if region is None
        else f'{region}<p class="count">Showing 1-10 of 42</p></div>'
    )
    assert _status_problems(markup) == []


@pytest.mark.accessibility
def test_the_region_ends_where_its_element_ends() -> None:
    """A message *after* a closed live region is outside it, not inside it.

    The nesting has to be tracked, not merely detected: a document that contains a
    live region somewhere must not thereby excuse every status message on the page.
    """
    problems = _status_problems(
        '<div role="status"><p class="count">Showing 1-10 of 42</p></div>'
        '<p class="empty">No records match your search.</p>'
    )
    assert len(problems) == 1
    assert 'class="empty"' in problems[0]


@pytest.mark.accessibility
def test_a_void_element_does_not_swallow_the_rest_of_the_document() -> None:
    """``<img>`` has no end tag, so it must never be pushed onto the nesting stack.

    If it were, everything after an image inside a live region would keep counting as
    inside it, and a real defect further down the page would pass.
    """
    problems = _status_problems(
        '<div role="status"><img src="x.png" alt="x"></div>'
        '<p class="empty">No records match your search.</p>'
    )
    assert len(problems) == 1
    assert 'class="empty"' in problems[0]


@pytest.mark.accessibility
def test_a_stray_end_tag_does_not_unwind_open_regions() -> None:
    """Unbalanced markup degrades to a tolerated quirk, not a page of phantom problems."""
    assert (
        _status_problems(
            '<div role="status"></section><p class="count">Showing 1-10 of 42</p></div>'
        )
        == []
    )


@pytest.mark.accessibility
@pytest.mark.parametrize("tag", ["body", "main", "nav", "header", "footer", "form"])
def test_a_live_region_on_page_structure_is_a_problem(tag: str) -> None:
    """An over-broad region is a defect even though every message is "announced".

    A live region around the page re-speaks the whole page on every change. This is
    the failure mode that makes a well-meant ``aria-live`` worse than none, so it is
    a gate failure and not a lint note.
    """
    problems = _status_problems(f'<{tag} aria-live="polite"><p class="count">42</p></{tag}>')
    assert len(problems) == 1
    assert "scoped wider than the message" in problems[0]


@pytest.mark.accessibility
@pytest.mark.parametrize("swallowed", ["form", "nav"])
def test_a_live_region_that_grew_to_swallow_a_form_or_the_nav_is_a_problem(
    swallowed: str,
) -> None:
    """The same defect from the inside out: a correct region that expanded too far."""
    problems = _status_problems(
        f'<div role="status"><p class="count">42</p><{swallowed}></{swallowed}></div>'
    )
    assert len(problems) == 1
    assert "scoped wider than the message" in problems[0]


@pytest.mark.accessibility
def test_the_checker_never_repeats_page_content() -> None:
    """A problem message names markup, never the words the page said (no-outing)."""
    # A sentinel standing in for page prose, not a credential.
    sentinel = "SENTINEL-ARIA-DO-NOT-LEAK-4K7P"
    problems = _status_problems(f'<p class="empty">{sentinel}</p>')
    assert len(problems) == 1
    assert sentinel not in problems[0]


# --- every dynamic state of every rendered surface --------------------------


def _sample_archive(tmp_path: Path) -> tuple[Archive, Config, Path]:
    """An archive holding one public, content-warned, dated, placed record."""
    root = tmp_path / "arc"
    config = Config.default("Aria Live Archive", root)
    archive = Archive.init(config)
    record = Record(
        title="Sample record",
        default_policy=AccessPolicy.PUBLIC,
        content_warnings=["violence"],
        dublin_core=DublinCore(
            title=["Sample record"],
            description=["A sample record used only to render the surface."],
            coverage=["Sample City"],
            date=["1994"],
        ),
        fields=[Field(name="story", value="A sample story.", policy=AccessPolicy.PUBLIC)],
    )
    archive.ingest({}, record, now=_NOW)
    return archive, config, root


def _dynamic_states(tmp_path: Path) -> dict[str, str]:
    """Render every state a reader can reach where a status message may appear.

    Populated *and* empty is the point: most of these messages only exist in the
    branch a page takes when it has nothing to show, which is exactly the branch a
    sample-page gate over a seeded archive never renders.
    """
    archive, config, root = _sample_archive(tmp_path)
    disclosed = archive.browse(anonymous(), now=_NOW)
    one = archive.disclose(disclosed[0].record_id, anonymous(), now=_NOW)

    log = TransparencyLog(root / "transparency.json")
    stale = log.append(
        attested_date="2020-01-01",
        attested_by="aria-live-test",
        statement_text="A sample statement used only to render the surface.",
        demand_counts={"subpoena": 0},
    )

    def page(main_html: str) -> str:
        return _page("Sample", lang="en", main_html=main_html)

    return {
        "browse:populated": page(_browse_main_html(disclosed, heading="Browse")),
        "browse:empty-archive": page(_browse_main_html([], heading="Browse")),
        "browse:no-match": page(_browse_main_html([], heading="Browse", query="zzzz")),
        "browse:filtered": page(
            _browse_main_html([], heading="Browse", active_facets=[("subject", "none")])
        ),
        "overview:populated": page(_overview_main_html(disclosed, lang="en")),
        "overview:empty": page(_overview_main_html([], lang="en")),
        "places:populated": page(_places_html(disclosed)),
        "places:empty": page(_places_html([])),
        "timeline:populated": page(_timeline_html(disclosed)),
        "timeline:empty": page(_timeline_html([])),
        "record:interstitial": page(_record_main_html(one, proceed=False)),
        "record:proceeded": page(_record_main_html(one, proceed=True)),
        "contribute:clean": page(contribute.render_contribute_main(config)),
        "contribute:rejected": page(
            contribute.render_contribute_main(config, error="A title is required.")
        ),
        "contribute:preview": page(
            contribute.render_contribute_main(
                config,
                preview_html=contribute.render_preview_panel(one, visibility="public"),
            )
        ),
        "contribute:thanks": page(
            contribute.render_thanks_main(
                reference="rec-1",
                claim_token="tok-1",  # noqa: S106 - a render fixture, not a real capability
            )
        ),
        "withdraw:clean": page(contribute.render_withdraw_main()),
        "withdraw:rejected": page(contribute.render_withdraw_main(error="That code is wrong.")),
        "withdraw:done": page(contribute.render_withdraw_done_main()),
        "edit:clean": page(contribute.render_edit_main(config)),
        "edit:rejected": page(contribute.render_edit_main(config, error="That code is wrong.")),
        "edit:done": page(contribute.render_edit_done_main()),
        "transparency:stale": page(
            transparency_main_html(
                heading="Legal-process transparency",
                latest=stale,
                entries=log.all(),
                cadence_days=90,
            )
        ),
    }


@pytest.mark.accessibility
def test_every_dynamic_state_announces_its_status_messages(tmp_path: Path) -> None:
    """No rendered state puts a status message outside a live region, or over-scopes one."""
    problems = [
        problem
        for label, markup in sorted(_dynamic_states(tmp_path).items())
        for problem in _status_problems(markup, label=label)
    ]
    assert problems == []


@pytest.mark.accessibility
def test_the_dynamic_state_matrix_covers_the_states_that_carry_messages(
    tmp_path: Path,
) -> None:
    """The matrix above must actually render the empty branches, not just the full ones.

    Without this, deleting a case from the matrix would make the previous test pass
    for the wrong reason — nothing rendered, nothing found.
    """
    states = _dynamic_states(tmp_path)
    assert len(states) >= 20
    # An empty state and a rejected submission are the two shapes the criterion is
    # really about; both must appear somewhere in what was rendered.
    assert any('class="empty"' in markup for markup in states.values())
    assert any('role="alert"' in markup for markup in states.values())


@pytest.mark.accessibility
def test_removing_the_live_region_is_caught(tmp_path: Path) -> None:
    """Negative control: the same page, minus its live region, must fail the check.

    The sabotage is asserted to have landed before its effect is measured — a
    mutation that silently no-ops would otherwise read as a passing control.
    """
    clean = _dynamic_states(tmp_path)["browse:no-match"]
    assert _status_problems(clean) == []

    region = '<div class="results-status" role="status" aria-live="polite">'
    assert region in clean  # the sabotage below has something to remove
    sabotaged = clean.replace(region, '<div class="results-status">')
    assert sabotaged != clean

    problems = _status_problems(sabotaged)
    assert len(problems) == 1
    assert "outside any live region" in problems[0]


@pytest.mark.accessibility
def test_widening_the_live_region_is_caught(tmp_path: Path) -> None:
    """Negative control, the other way: a region grown over the page must fail too."""
    clean = _dynamic_states(tmp_path)["browse:populated"]
    assert _status_problems(clean) == []

    landmark = '<main id="main"'
    assert landmark in clean
    sabotaged = clean.replace(landmark, f'{landmark} aria-live="polite"', 1)
    assert sabotaged != clean

    problems = _status_problems(sabotaged)
    assert any("scoped wider than the message" in problem for problem in problems)


# --- the surfaces that only exist behind a live server ----------------------


@pytest.fixture
def steward_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A running server with an empty submission queue and a steward grant."""
    monkeypatch.setenv("LEDGER_GRANT_SECRET", _GRANT_SECRET.decode())
    grants = tmp_path / "grants.json"
    grants.write_text(
        json.dumps(
            {"steward-1": {"levels": ["public", "community", "stewards"], "is_steward": True}}
        ),
        encoding="utf-8",
    )
    archive, _config, _root = _sample_archive(tmp_path)
    httpd = make_server(
        archive, host="127.0.0.1", port=0, grants_path=grants, allow_contributions=True
    )
    host, port = httpd.server_address[0], httpd.server_address[1]
    host_s = host.decode("ascii") if isinstance(host, (bytes, bytearray)) else str(host)
    sink = StringIO()
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    with redirect_stderr(sink), redirect_stdout(sink):
        thread.start()
        try:
            yield f"http://{host_s}:{int(port)}"
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()


def _get(base: str, path: str, *, headers: dict[str, str] | None = None) -> str:
    """GET ``base + path`` over loopback and return the body."""
    request = urllib.request.Request(f"{base}{path}", headers=headers or {})  # noqa: S310 - loopback URL we constructed
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - loopback URL we constructed for the in-process test server
        return str(response.read().decode("utf-8"))


@pytest.mark.accessibility
def test_the_steward_console_announces_an_empty_queue(steward_server: str) -> None:
    """ "Nothing is waiting" is what a steward opened the console to learn."""
    body = _get(
        steward_server,
        "/steward",
        headers={"X-Ledger-Grant": issue_grant_token("steward-1", _GRANT_SECRET)},
    )
    assert _status_problems(body, label="rendered:/steward") == []
    assert 'role="status"' in body


@pytest.mark.accessibility
def test_consent_status_announces_a_reference_that_matches_nothing(
    steward_server: str,
) -> None:
    """A lookup that found nothing is announced politely, without moving focus."""
    body = _get(steward_server, "/consent-status?ref=no-such-reference")
    assert _status_problems(body, label="rendered:/consent-status") == []
    assert 'role="status"' in body
