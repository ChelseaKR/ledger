"""Tests for facet links on a record page (descriptive metadata -> discovery).

A record's Dublin Core subject/type/language values are rendered as links into the
faceted browse, so a reader on one record can pivot to every other record that shares
a topic, kind, or language. Other elements (e.g. date) stay plain text. Values are
escaped and query-quoted, so a crafted metadata value cannot inject markup.
"""

from __future__ import annotations

from ledger.models import DisclosedRecord, Redaction
from ledger.render import _record_main_html


def _disclosed(**dc: list[str]) -> DisclosedRecord:
    return DisclosedRecord(
        record_id="rec-1",
        title="A record",
        dublin_core=dc,
        fields={},
        payloads=(),
        content_warnings=(),
        withheld=(),
    )


def test_facetable_metadata_links_into_browse() -> None:
    record = _disclosed(
        subject=["mutual aid", "housing"],
        type=["photograph"],
        language=["en"],
        date=["1994"],
    )
    html = _record_main_html(record, proceed=True)
    # Subject/type/language values are links into the faceted browse.
    assert '<a href="/?subject=mutual%20aid">mutual aid</a>' in html
    assert '<a href="/?subject=housing">housing</a>' in html
    assert '<a href="/?type=photograph">photograph</a>' in html
    assert '<a href="/?language=en">en</a>' in html
    # A non-facetable element (date) stays plain text, not a link.
    assert "1994" in html
    assert "/?date=" not in html


def test_facet_value_is_escaped_and_quoted() -> None:
    """A crafted subject cannot break out of the href or inject markup."""
    record = _disclosed(subject=['x"><script>'])
    html = _record_main_html(record, proceed=True)
    assert "<script>" not in html  # never rendered as live markup
    assert "&lt;script&gt;" in html  # shown as escaped text


def test_record_page_offers_a_citation_and_metadata_link() -> None:
    """A record page shows a stable citation, a permalink, and a metadata download."""
    record = DisclosedRecord(
        record_id="rec-9",
        title="The May march",
        dublin_core={"date": ["1994"], "publisher": ["Ignored Publisher"]},
        fields={},
        payloads=(),
        content_warnings=(),
        withheld=(),
    )
    html = _record_main_html(
        record,
        proceed=True,
        base_url="https://archive.example/",
        archive_name="People's Archive",
    )
    assert "Cite this record" in html
    # The citation carries title, date, the archive name, and the permalink.
    assert "The May march. 1994. People&#x27;s Archive." in html
    assert "https://archive.example/record/rec-9" in html
    # A machine-readable metadata link points at the JSON API.
    assert 'href="/api/record/rec-9"' in html


def test_citation_surfaces_the_persistent_identifier() -> None:
    """The minted ARK PID shows on its own line and inside the formatted citation."""
    record = DisclosedRecord(
        record_id="rec-9",
        title="The May march",
        dublin_core={
            "date": ["1994"],
            "publisher": ["People's Archive"],
            "identifier": ["ark:/99999/lrec-9"],
        },
        fields={},
        payloads=(),
        content_warnings=(),
        withheld=(),
    )
    html = _record_main_html(
        record,
        proceed=True,
        base_url="https://archive.example/",
        archive_name="People's Archive",
    )
    # A dedicated persistent-identifier line carries the ARK.
    assert "Persistent identifier" in html
    assert '<span class="pid">ark:/99999/lrec-9</span>' in html
    # The ARK is also woven into the formatted citation string, before the URL.
    assert "People&#x27;s Archive. ark:/99999/lrec-9." in html


def test_citation_without_a_pid_omits_the_pid_line() -> None:
    """A record with no ARK identifier shows no persistent-identifier line."""
    record = DisclosedRecord(
        record_id="rec-8",
        title="No PID here",
        dublin_core={"identifier": ["rec-8"]},  # a bare id is not an ARK
        fields={},
        payloads=(),
        content_warnings=(),
        withheld=(),
    )
    html = _record_main_html(record, proceed=True, base_url="https://x.org", archive_name="A")
    assert "Persistent identifier" not in html
    assert '<span class="pid">' not in html


def test_citation_escapes_a_crafted_title() -> None:
    """A title with markup cannot break out of the citation."""
    record = DisclosedRecord(
        record_id="r",
        title='<script>"x"',
        dublin_core={},
        fields={},
        payloads=(),
        content_warnings=(),
        withheld=(),
    )
    html = _record_main_html(record, proceed=True, base_url="https://x.org", archive_name="A")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_record_page_lists_related_records() -> None:
    """Records passed as related render as links under a Related heading."""
    record = _disclosed(subject=["protest"])
    related = [
        DisclosedRecord(
            record_id="rel-1",
            title="A related march",
            dublin_core={"subject": ["protest"]},
            fields={},
            payloads=(),
            content_warnings=(),
            withheld=(),
        )
    ]
    html = _record_main_html(record, proceed=True, related=related)
    assert "Related records" in html
    assert 'href="/record/rel-1">A related march</a>' in html


def test_record_page_omits_related_section_when_none() -> None:
    html = _record_main_html(_disclosed(subject=["protest"]), proceed=True, related=[])
    assert "Related records" not in html


def test_record_page_is_localized() -> None:
    """The record page chrome (headings, links, withheld note) renders in Spanish."""
    record = DisclosedRecord(
        record_id="rec-1",
        title="Un registro",
        dublin_core={"subject": ["vivienda"]},
        fields={"account": "El relato."},
        payloads=(),
        content_warnings=(),
        withheld=(Redaction(name="contact", reason="sealed", category="sealed"),),
    )
    html = _record_main_html(record, proceed=True, lang="es")
    assert "Metadatos de catálogo" in html  # the Dublin Core section heading
    assert "Detalles" in html  # the fields section heading
    assert "Retenido" in html  # the withheld section heading
    assert "consentimiento" in html  # the contributor consent link
    # The English equivalents are gone.
    assert "Catalogue metadata" not in html
    assert "Withheld" not in html
