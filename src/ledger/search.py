"""Search and faceted browse over already-disclosed records.

User research P1-4: the existing browse path only matched on a record's ``title``,
so a contributor who described an item with a precise ``subject`` ("mutual aid",
"deportation defense") found that the description was invisible to search. The
result was a collection that *held* the right material but could not *surface* it.
This module fixes that by indexing the full Dublin Core description — subject,
description, type, provenance, and the rest — alongside every visible field value,
so a search term matches the words a contributor actually used.

The single safety invariant here is structural, not procedural: every function in
this module operates exclusively on :class:`~ledger.models.DisclosedRecord`, the
only record shape a read path may emit. A ``DisclosedRecord`` already carries *only*
what a given grant is allowed to see at a given instant and structurally cannot hold
a contributor identity or a sealed value (there is no ``identity_ref`` on it). So
search inherits access control for free: it can only ever index, match, count, or
return content that disclosure already cleared. There is no path by which a query
can reach a withheld field's value, because that value is not present in the input.

Determinism: ranking and ordering are stable and value-driven (no clock, no random
source), so the same records and query always yield the same result in the same
order — searchable archives must be reproducible, not surprising.

Honesty about a current limitation (P1-4 follow-up): matching is a case-insensitive
ASCII-lowercasing substring/term match. It is Latin/English-biased and does no
transliteration or Unicode case folding for non-Latin scripts. Rather than pretend
otherwise, :func:`looks_non_latin` lets a UI detect a non-Latin query and tell the
reader plainly that search may miss results in their script — care over false
confidence.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from ledger.models import DisclosedRecord

__all__ = [
    "Facet",
    "facets",
    "filter_by_facet",
    "index_text",
    "looks_non_latin",
    "search",
]


@dataclass(frozen=True)
class Facet:
    """One distinct value of a browse field together with how many records carry it.

    A faceted-browse UI renders these as clickable filters ("subject: mutual aid
    (12)"). It is value-only: ``field`` and ``value`` come straight from disclosed
    Dublin Core, so a ``Facet`` can never carry an identity or a sealed value.
    """

    field: str
    value: str
    count: int


def index_text(record: DisclosedRecord) -> str:
    """Return the lowercased, searchable text for ``record``.

    This is the heart of the P1-4 fix: the index is the concatenation of the
    record's ``title``, *all* Dublin Core element values (subject, description,
    type, creator, publisher, date, and the rest), and all visible field values.
    Indexing the Dublin Core is what makes a contributor's chosen subjects and
    descriptions findable, not just the title.

    Only disclosed content is consulted (``record`` is a
    :class:`~ledger.models.DisclosedRecord`), so a withheld field's value can never
    enter the index. Lowercasing is plain ASCII case folding via :meth:`str.lower`;
    see the module docstring on its Latin/English bias.
    """
    parts: list[str] = [record.title]
    for values in record.dublin_core.values():
        parts.extend(values)
    parts.extend(record.fields.values())
    return " ".join(parts).lower()


def _relevance(record: DisclosedRecord, terms: Sequence[str]) -> int:
    """Score how well ``record`` matches ``terms`` — higher is more relevant.

    A hit in the *title* weighs most, then a Dublin Core *subject*, then anywhere
    else in the indexed text, and a term that recurs scores each occurrence. This
    moves search past a flat boolean "matches / doesn't" toward an ordering a reader
    expects: the record whose title is the query leads (user research E2). Pure and
    deterministic — only disclosed text is scored, so the score can never reflect a
    withheld value (no-outing rule)."""
    title = record.title.lower()
    subjects = " ".join(record.dublin_core.get("subject", ())).lower()
    rest = index_text(record)
    return sum(3 * title.count(t) + 2 * subjects.count(t) + rest.count(t) for t in terms)


def search(records: Sequence[DisclosedRecord], query: str) -> list[DisclosedRecord]:
    """Return the records matching every query term, ordered by relevance.

    Matching is case-insensitive and is a logical AND over the whitespace-split
    query terms: a record matches only if *each* term is a substring of its
    :func:`index_text`. An empty (or whitespace-only) query returns every record in
    the caller's order, so a blank search box browses the whole collection.

    Matches are ranked by :func:`_relevance` (descending), with the caller's input
    order preserved as a stable tie-break, so the most relevant record leads while
    equal-scoring records keep a predictable, reproducible order.
    """
    terms = query.lower().split()
    if not terms:
        return list(records)
    scored: list[tuple[int, int, DisclosedRecord]] = []
    for index, record in enumerate(records):
        haystack = index_text(record)
        if all(term in haystack for term in terms):
            scored.append((_relevance(record, terms), index, record))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [record for _score, _index, record in scored]


def facets(records: Sequence[DisclosedRecord], field: str) -> list[Facet]:
    """Count the distinct values of a Dublin Core ``field`` across ``records``.

    For example ``facets(records, "subject")`` yields one :class:`Facet` per
    distinct subject with the number of records that carry it, powering a faceted
    browse sidebar. Only disclosed Dublin Core is counted, so the counts describe
    what is *visible*, never what is withheld.

    Results are sorted by count descending, then by value ascending, so the most
    common facets lead and ties break deterministically (stable, reproducible UI).
    A record that repeats a value under the same field still counts once for that
    record.
    """
    counter: Counter[str] = Counter()
    for record in records:
        for value in set(record.dublin_core.get(field, ())):
            counter[value] += 1
    return [
        Facet(field=field, value=value, count=count)
        for value, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    ]


def filter_by_facet(
    records: Sequence[DisclosedRecord], field: str, value: str
) -> list[DisclosedRecord]:
    """Return the records whose Dublin Core ``field`` contains ``value``.

    This is the click-through for a :class:`Facet`: selecting "subject: mutual aid"
    narrows the result set to records carrying that exact subject value. Matching is
    exact (not substring) on the disclosed Dublin Core values, and input order is
    preserved so the narrowed list keeps the caller's ordering.
    """
    return [record for record in records if value in record.dublin_core.get(field, ())]


def looks_non_latin(query: str) -> bool:
    """Return True if ``query`` contains a non-ASCII letter.

    Honesty about a limitation (module docstring): search is Latin/English-biased.
    A UI can call this to detect a query written in a non-Latin script (Cyrillic,
    Arabic, Han, Devanagari, accented Latin, etc.) and surface a plain-language hint
    that results in that script may be incomplete — surfacing the limitation rather
    than failing silently.

    Only *letters* count: digits, punctuation, and ASCII whitespace are ignored, so
    an all-ASCII query with stray symbols is not flagged.
    """
    return any(char.isalpha() and not char.isascii() for char in query)
