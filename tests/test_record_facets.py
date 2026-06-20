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
