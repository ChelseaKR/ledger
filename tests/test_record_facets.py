"""Tests for facet links on a record page (descriptive metadata -> discovery).

A record's Dublin Core subject/type/language values are rendered as links into the
faceted browse, so a reader on one record can pivot to every other record that shares
a topic, kind, or language. Other elements (e.g. date) stay plain text. Values are
escaped and query-quoted, so a crafted metadata value cannot inject markup.
"""

from __future__ import annotations

from ledger.models import DisclosedRecord
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
