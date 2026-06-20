"""Tests for :mod:`ledger.search` — search and faceted browse over disclosed records.

These cover the P1-4 fix (subject/description are searchable, not just the title),
multi-term AND matching, faceted counts and their deterministic ordering, facet
click-through filtering, the non-Latin query hint, and the empty-query browse-all
behaviour. Every test builds plain :class:`~ledger.models.DisclosedRecord` objects —
the only shape a read path emits — so the suite never touches sealed values or any
identity, mirroring the access boundary search relies on.
"""

from __future__ import annotations

from ledger.models import DisclosedRecord
from ledger.search import (
    Facet,
    facets,
    filter_by_facet,
    index_text,
    looks_non_latin,
    search,
)


def _disclosed(
    record_id: str,
    title: str,
    *,
    dublin_core: dict[str, list[str]] | None = None,
    fields: dict[str, str] | None = None,
) -> DisclosedRecord:
    """Build a minimal, identity-free :class:`DisclosedRecord` for search tests."""
    return DisclosedRecord(
        record_id=record_id,
        title=title,
        dublin_core=dublin_core or {},
        fields=fields or {},
        payloads=(),
        content_warnings=(),
        withheld=(),
    )


def test_search_finds_record_by_subject_not_title() -> None:
    """A term appearing only in ``subject`` (never the title) still matches (P1-4)."""
    record = _disclosed(
        "rec-1",
        title="An untitled photograph",
        dublin_core={"subject": ["mutual aid", "deportation defense"]},
    )
    other = _disclosed("rec-2", title="A pride banner", dublin_core={"subject": ["protest"]})

    results = search([record, other], "deportation")

    assert [r.record_id for r in results] == ["rec-1"]


def test_search_matches_description_and_visible_field_values() -> None:
    """Description text and visible field values are indexed, not just Dublin Core."""
    by_description = _disclosed(
        "rec-desc",
        title="Oral history",
        dublin_core={"description": ["a recording about tenant organizing"]},
    )
    by_field = _disclosed(
        "rec-field",
        title="Flyer",
        fields={"story": "notes on tenant organizing in 1989"},
    )

    assert {r.record_id for r in search([by_description, by_field], "tenant")} == {
        "rec-desc",
        "rec-field",
    }


def test_search_is_multi_term_and() -> None:
    """All whitespace-split terms must match; a record missing one is excluded."""
    both = _disclosed(
        "rec-both",
        title="Community archive",
        dublin_core={"subject": ["mutual aid", "queer history"]},
    )
    only_one = _disclosed("rec-one", title="Newsletter", dublin_core={"subject": ["mutual aid"]})

    results = search([both, only_one], "mutual queer")

    assert [r.record_id for r in results] == ["rec-both"]


def test_search_is_case_insensitive() -> None:
    """Matching folds case on both the query and the indexed text."""
    record = _disclosed("rec-1", title="PRIDE March", dublin_core={"subject": ["Mutual Aid"]})

    assert search([record], "pride")[0].record_id == "rec-1"
    assert search([record], "MUTUAL")[0].record_id == "rec-1"


def test_search_empty_query_returns_all_in_input_order() -> None:
    """An empty or whitespace-only query browses the whole collection, in order."""
    a = _disclosed("rec-a", title="First")
    b = _disclosed("rec-b", title="Second")

    assert [r.record_id for r in search([a, b], "")] == ["rec-a", "rec-b"]
    assert [r.record_id for r in search([a, b], "   ")] == ["rec-a", "rec-b"]


def test_search_preserves_input_order() -> None:
    """Search filters but never re-sorts; the caller's ordering survives."""
    records = [
        _disclosed("rec-3", title="march", dublin_core={"subject": ["mutual aid"]}),
        _disclosed("rec-1", title="rally", dublin_core={"subject": ["mutual aid"]}),
        _disclosed("rec-2", title="vigil", dublin_core={"subject": ["mutual aid"]}),
    ]

    assert [r.record_id for r in search(records, "mutual")] == ["rec-3", "rec-1", "rec-2"]


def test_index_text_concatenates_title_dc_and_fields_lowercased() -> None:
    """The index is the lowercased title + all DC values + all visible field values."""
    record = _disclosed(
        "rec-1",
        title="Pride March",
        dublin_core={"subject": ["Mutual Aid"], "type": ["Image"]},
        fields={"story": "The Public Account"},
    )

    text = index_text(record)

    assert text == text.lower()
    for token in ("pride march", "mutual aid", "image", "the public account"):
        assert token in text


def test_facets_count_distinct_values_sorted_by_count_then_value() -> None:
    """Facets count records per distinct value, ordered by count desc then value asc."""
    records = [
        _disclosed("r1", title="a", dublin_core={"subject": ["mutual aid"]}),
        _disclosed("r2", title="b", dublin_core={"subject": ["mutual aid", "protest"]}),
        _disclosed("r3", title="c", dublin_core={"subject": ["protest"]}),
        _disclosed("r4", title="d", dublin_core={"subject": ["protest"]}),
    ]

    result = facets(records, "subject")

    assert result == [
        Facet(field="subject", value="protest", count=3),
        Facet(field="subject", value="mutual aid", count=2),
    ]


def test_facets_count_a_repeated_value_once_per_record() -> None:
    """A value repeated within one record's field counts once for that record."""
    records = [
        _disclosed("r1", title="a", dublin_core={"subject": ["mutual aid", "mutual aid"]}),
    ]

    assert facets(records, "subject") == [Facet(field="subject", value="mutual aid", count=1)]


def test_facets_of_absent_field_is_empty() -> None:
    """A field no record carries yields no facets (no crash, no phantom values)."""
    records = [_disclosed("r1", title="a", dublin_core={"subject": ["x"]})]

    assert facets(records, "coverage") == []


def test_filter_by_facet_returns_matching_records_in_order() -> None:
    """Selecting a facet narrows to records carrying that exact value, order kept."""
    records = [
        _disclosed("r1", title="a", dublin_core={"subject": ["mutual aid"]}),
        _disclosed("r2", title="b", dublin_core={"subject": ["protest"]}),
        _disclosed("r3", title="c", dublin_core={"subject": ["mutual aid", "protest"]}),
    ]

    result = filter_by_facet(records, "subject", "mutual aid")

    assert [r.record_id for r in result] == ["r1", "r3"]


def test_filter_by_facet_is_exact_not_substring() -> None:
    """Facet filtering matches a whole value, not a substring of one."""
    records = [
        _disclosed("r1", title="a", dublin_core={"subject": ["mutual aid network"]}),
        _disclosed("r2", title="b", dublin_core={"subject": ["mutual aid"]}),
    ]

    assert [r.record_id for r in filter_by_facet(records, "subject", "mutual aid")] == ["r2"]


def test_looks_non_latin_detects_non_ascii_letters() -> None:
    """A query with non-Latin letters is flagged so a UI can warn about the bias."""
    assert looks_non_latin("протест") is True
    assert looks_non_latin("組織") is True
    assert looks_non_latin("café") is True


def test_looks_non_latin_false_for_ascii_query() -> None:
    """A plain ASCII query (even with digits and punctuation) is not flagged."""
    assert looks_non_latin("mutual aid") is False
    assert looks_non_latin("1987!") is False
    assert looks_non_latin("") is False


# --- E2: relevance ranking + language facet ---------------------------------


def test_search_ranks_title_hits_above_body_hits() -> None:
    """A query that matches a record's title ranks it above a description-only match."""
    title_hit = _disclosed("r-title", "Mutual aid pantry", dublin_core={"description": ["x"]})
    body_hit = _disclosed(
        "r-body", "Thursday notes", dublin_core={"description": ["the mutual aid roster"]}
    )
    # Input order puts the body hit first; relevance must surface the title hit first.
    results = search([body_hit, title_hit], "mutual aid")
    assert [r.record_id for r in results] == ["r-title", "r-body"]


def test_search_ties_keep_input_order() -> None:
    """Equal-scoring matches preserve the caller's order (stable, reproducible)."""
    a = _disclosed("a", "aid", dublin_core={})
    b = _disclosed("b", "aid", dublin_core={})
    assert [r.record_id for r in search([a, b], "aid")] == ["a", "b"]
    assert [r.record_id for r in search([b, a], "aid")] == ["b", "a"]


def test_language_is_a_facet() -> None:
    """Language is now a browsable facet, filterable like subject/type."""
    en = _disclosed("en", "One", dublin_core={"language": ["en"]})
    es = _disclosed("es", "Dos", dublin_core={"language": ["es"]})
    langs = {f.value: f.count for f in facets([en, es], "language")}
    assert langs == {"en": 1, "es": 1}
    assert [r.record_id for r in filter_by_facet([en, es], "language", "es")] == ["es"]
