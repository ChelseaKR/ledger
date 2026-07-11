"""Tests for record-to-record relationships — roadmap EX4.

A record's Dublin Core ``relation`` values, resolved against the records the viewer
may list, become an accessible list-equivalent "graph": outgoing links to other
records, the reciprocal set (records that reference this one), and external
identifiers shown as plain text. The safety-critical rule is that a relation pointing
at a *sealed* record resolves to nothing — the page never confirms that a hidden
record exists (no-outing rule) — while an external, non-record identifier is shown
honestly as text rather than a broken link.
"""

from __future__ import annotations

import threading
import urllib.request
from collections.abc import Iterator
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import pytest

from ledger import search
from ledger.config import Config
from ledger.ingest import Archive
from ledger.models import AccessPolicy, DisclosedRecord, DublinCore, Field, Record
from ledger.render import _record_main_html, _relations_html
from ledger.server import make_server


def _disclosed(rid: str, title: str, **dc: list[str]) -> DisclosedRecord:
    return DisclosedRecord(
        record_id=rid,
        title=title,
        dublin_core=dc,
        fields={},
        payloads=(),
        content_warnings=(),
        withheld=(),
    )


# --- resolve_relations (pure) -----------------------------------------------


def test_relations_resolve_bidirectionally() -> None:
    a = _disclosed("a" * 32, "Alpha", relation=["b" * 32])
    b = _disclosed("b" * 32, "Beta")
    # From A's side, B is an outgoing link; from B's side, A is incoming (reciprocal).
    from_a = search.resolve_relations(a, [a, b])
    assert [r.record_id for r in from_a.outgoing] == ["b" * 32]
    from_b = search.resolve_relations(b, [a, b])
    assert [r.record_id for r in from_b.incoming] == ["a" * 32]


def test_relation_to_a_non_disclosed_record_is_dropped() -> None:
    """An id-shaped relation to a record the viewer cannot list resolves to nothing."""
    sealed_id = "f" * 32  # a record id NOT present in the disclosed candidate set
    a = _disclosed("a" * 32, "Alpha", relation=[sealed_id])
    resolved = search.resolve_relations(a, [a])  # only A is disclosed
    assert resolved.outgoing == ()
    assert resolved.external == ()  # id-shaped -> never surfaced as text either
    assert not resolved  # nothing to render at all


def test_custom_internal_id_is_not_misclassified_as_external() -> None:
    """Hidden records with stable non-UUID ids remain invisible too."""
    a = _disclosed("public", "Alpha", relation=["private-oral-history"])
    resolved = search.resolve_relations(
        a, [a], known_internal_ids={"public", "private-oral-history"}
    )
    assert resolved.outgoing == ()
    assert resolved.external == ()


def test_unknown_relation_string_is_external_plain_text() -> None:
    a = _disclosed("a" * 32, "Alpha", relation=["urn:isbn:9780000000000", "https://ex.org/x"])
    resolved = search.resolve_relations(a, [a])
    assert resolved.outgoing == ()
    assert resolved.external == ("urn:isbn:9780000000000", "https://ex.org/x")


def test_relations_dedupe_and_ignore_self() -> None:
    a = _disclosed("a" * 32, "Alpha", relation=["a" * 32, "b" * 32, "b" * 32])
    b = _disclosed("b" * 32, "Beta")
    resolved = search.resolve_relations(a, [a, b])
    # Self-reference dropped; duplicate outgoing collapses to one.
    assert [r.record_id for r in resolved.outgoing] == ["b" * 32]


# --- _relations_html / record page (pure) -----------------------------------


def test_relations_section_renders_links_and_external_text() -> None:
    a = _disclosed("a" * 32, "Alpha", relation=["b" * 32, "https://ex.org/x"])
    b = _disclosed("b" * 32, "Beta")
    resolved = search.resolve_relations(a, [a, b])
    html = _relations_html(resolved, lang="en")
    assert "Linked records" in html
    assert f'<a href="/record/{"b" * 32}">Beta</a>' in html
    # The external identifier is plain text, never a link.
    assert "<li>https://ex.org/x</li>" in html


def test_record_page_shows_relations_section() -> None:
    a = _disclosed("a" * 32, "Alpha", relation=["b" * 32])
    b = _disclosed("b" * 32, "Beta")
    resolved = search.resolve_relations(a, [a, b])
    html = _record_main_html(a, proceed=True, relations=resolved)
    assert 'id="relations-heading"' in html
    assert f'href="/record/{"b" * 32}">Beta</a>' in html


def test_record_page_omits_relations_when_none() -> None:
    a = _disclosed("a" * 32, "Alpha")
    html = _record_main_html(a, proceed=True, relations=search.resolve_relations(a, [a]))
    assert 'id="relations-heading"' not in html


def test_relations_section_escapes_external_identifier() -> None:
    a = _disclosed("a" * 32, "Alpha", relation=['"><script>'])
    resolved = search.resolve_relations(a, [a])
    html = _relations_html(resolved, lang="en")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


# --- end to end: a sealed relation never leaks over the server --------------


@pytest.fixture
def server_env(tmp_path: Path) -> Iterator[tuple[str, dict[str, str]]]:
    """A running server plus the ids of a public source, a public target, and a sealed
    record the source also relates to (which must never surface)."""
    config = Config.default("Relations Archive", tmp_path / "arc")
    archive = Archive.init(config)

    target = Record(
        title="The maker's zine",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(title=["The maker's zine"], publisher=[config.archive_name]),
        fields=[Field(name="account", value="a public zine", policy=AccessPolicy.PUBLIC)],
    )
    # Sealed indefinitely (SEALED_UNTIL with no unseal date): not listable to the
    # anonymous public, so it must never surface as a resolved relation target.
    sealed = Record(
        title="Sealed oral history",
        default_policy=AccessPolicy.SEALED_UNTIL,
        dublin_core=DublinCore(title=["Sealed oral history"]),
        fields=[Field(name="account", value="hidden", policy=AccessPolicy.SEALED_UNTIL)],
    )
    archive.ingest({}, target, agent="t", now="2026-06-20T00:00:00Z")
    archive.ingest({}, sealed, agent="t", now="2026-06-20T00:00:00Z")

    source = Record(
        title="An oral history",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(
            title=["An oral history"],
            publisher=[config.archive_name],
            # One relation to a public record, one to a sealed one, one external.
            relation=[target.record_id, sealed.record_id, "https://external.example/ref"],
        ),
        fields=[Field(name="account", value="a public account", policy=AccessPolicy.PUBLIC)],
    )
    archive.ingest({}, source, agent="t", now="2026-06-20T00:00:00Z")

    httpd = make_server(archive, host="127.0.0.1", port=0)
    base = f"http://127.0.0.1:{int(httpd.server_address[1])}"
    ids = {
        "source": source.record_id,
        "target": target.record_id,
        "sealed": sealed.record_id,
    }
    sink = StringIO()
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    with redirect_stderr(sink), redirect_stdout(sink):
        thread.start()
        try:
            yield base, ids
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()


def _get(base: str, path: str) -> str:
    with urllib.request.urlopen(f"{base}{path}", timeout=10) as resp:  # noqa: S310 - loopback
        return resp.read().decode("utf-8")


def test_public_relation_renders_but_sealed_relation_never_leaks(
    server_env: tuple[str, dict[str, str]],
) -> None:
    base, ids = server_env
    body = _get(base, f"/record/{ids['source']}")
    # The public relation is shown as a link, and the external identifier as text.
    assert f'href="/record/{ids["target"]}">The maker&#x27;s zine</a>' in body
    assert "https://external.example/ref" in body
    # The sealed record's id and title never appear — its existence does not leak.
    assert ids["sealed"] not in body
    assert "Sealed oral history" not in body


def test_reciprocal_link_appears_on_the_target(
    server_env: tuple[str, dict[str, str]],
) -> None:
    base, ids = server_env
    body = _get(base, f"/record/{ids['target']}")
    # The target shows "records that link here" pointing back at the source.
    assert "Records that link here" in body
    assert f'href="/record/{ids["source"]}">An oral history</a>' in body
