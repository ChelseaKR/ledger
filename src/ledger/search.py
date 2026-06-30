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
    "Snippet",
    "facets",
    "filter_by_date_range",
    "filter_by_facet",
    "index_text",
    "looks_non_latin",
    "related_by_subject",
    "search",
    "snippet",
    "sort_by_date",
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


@dataclass(frozen=True)
class Snippet:
    """A short excerpt of a record's text showing *why* a search matched it.

    ``runs`` is an ordered sequence of ``(text, matched)`` pairs that, concatenated,
    form the excerpt: a ``matched`` run is a span a query term hit (a UI marks it,
    e.g. with ``<mark>``), a non-matched run is surrounding context. Splitting the
    excerpt this way keeps the *matching* logic here while leaving HTML escaping to
    the single render boundary — each run's text is escaped at the point it becomes
    markup. The excerpt is drawn only from a :class:`DisclosedRecord`, so it can never
    reveal a withheld value or an identity (no-outing rule).
    """

    runs: tuple[tuple[str, bool], ...]


def _display_text(record: DisclosedRecord) -> str:
    """The record's searchable text with original casing kept, for excerpting.

    Mirrors :func:`index_text` (title, then every Dublin Core value, then every
    visible field value) but does *not* lowercase, so a snippet reads naturally.
    Only disclosed content is included, so it carries nothing withheld."""
    parts: list[str] = [record.title]
    for values in record.dublin_core.values():
        parts.extend(values)
    parts.extend(record.fields.values())
    return "  ".join(part for part in parts if part)


def _highlight_runs(text: str, terms: Sequence[str]) -> list[tuple[str, bool]]:
    """Split ``text`` into ``(piece, matched)`` runs marking every term occurrence.

    Matching is case-insensitive; overlapping or adjacent hits are merged so a run
    is never split mid-highlight. Pure and deterministic."""
    low = text.lower()
    spans: list[tuple[int, int]] = []
    for term in terms:
        start = 0
        while term:
            index = low.find(term, start)
            if index == -1:
                break
            spans.append((index, index + len(term)))
            start = index + len(term)
    if not spans:
        return [(text, False)]
    spans.sort()
    merged: list[tuple[int, int]] = [spans[0]]
    for begin, end in spans[1:]:
        last_begin, last_end = merged[-1]
        if begin <= last_end:
            merged[-1] = (last_begin, max(last_end, end))
        else:
            merged.append((begin, end))
    runs: list[tuple[str, bool]] = []
    position = 0
    for begin, end in merged:
        if begin > position:
            runs.append((text[position:begin], False))
        runs.append((text[begin:end], True))
        position = end
    if position < len(text):
        runs.append((text[position:], False))
    return runs


def snippet(record: DisclosedRecord, query: str, *, width: int = 160) -> Snippet | None:
    """Return an excerpt of ``record`` around the first query match, or ``None``.

    The excerpt is a window of about ``width`` characters centred a little after the
    earliest matching term, trimmed back to whole words and bracketed with an
    ellipsis when text is dropped at either end. Every occurrence of any term within
    the window is flagged as a matched run (see :class:`Snippet`) so a UI can show a
    reader *why* the record matched (user research E2/E3). Returns ``None`` for an
    empty query or when no term is present in the disclosed text. Pure and
    deterministic — only disclosed content is consulted (no-outing rule)."""
    terms = [term for term in query.lower().split() if term]
    if not terms:
        return None
    text = _display_text(record)
    low = text.lower()
    first: int | None = None
    for term in terms:
        index = low.find(term)
        if index != -1 and (first is None or index < first):
            first = index
    if first is None:
        return None

    start = max(0, first - width // 3)
    end = min(len(text), start + width)
    # Trim the cut ends back/forward to a word boundary so no word is sliced.
    if start > 0:
        space = text.rfind(" ", 0, start)
        start = space + 1 if space != -1 else start
    if end < len(text):
        space = text.find(" ", end)
        end = space if space != -1 else end
    excerpt = text[start:end].strip()

    runs = _highlight_runs(excerpt, terms)
    if start > 0:
        runs.insert(0, ("… ", False))
    if end < len(text):
        runs.append((" …", False))
    return Snippet(runs=tuple(runs))


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


def sort_by_date(records: Sequence[DisclosedRecord], *, newest: bool) -> list[DisclosedRecord]:
    """Return ``records`` ordered by their Dublin Core ``date``, undated ones last.

    ``newest=True`` sorts most-recent first, ``False`` oldest first; a record with no
    date always sorts to the end either way, since an absent date should not masquerade
    as the earliest or latest. The first ``date`` value is compared as a plain string,
    which orders the ISO-ish forms ledger stores (``YYYY``, ``YYYY-MM``, ``YYYY-MM-DD``)
    correctly. The sort is stable, so records sharing a date keep the caller's order
    (a search's relevance ranking, or browse order) as a deterministic tie-break. Only
    disclosed dates are read, so this can never reflect a withheld value (no-outing)."""

    def date_of(record: DisclosedRecord) -> str:
        values = record.dublin_core.get("date") or []
        return values[0] if values and values[0] else ""

    dated = [r for r in records if date_of(r)]
    undated = [r for r in records if not date_of(r)]
    dated.sort(key=date_of, reverse=newest)
    return dated + undated


def related_by_subject(
    record: DisclosedRecord, candidates: Sequence[DisclosedRecord], *, limit: int = 5
) -> list[DisclosedRecord]:
    """Return the records most related to ``record`` by shared Dublin Core subject.

    A reader on one record can then follow it to others on the same topics — the
    record-level counterpart to the subject facet (user research P1-4). A candidate is
    related if it shares at least one subject; results are ordered by the number of
    shared subjects (most first), with the candidates' input order as a stable
    tie-break, and capped at ``limit``. The record itself is excluded by id.

    ``candidates`` is whatever the *viewer* may already list, and every record here is
    a :class:`DisclosedRecord`, so a related record can never be one the viewer may not
    see, and the link reveals nothing the subject facet would not (no-outing rule)."""
    subjects = set(record.dublin_core.get("subject") or ())
    if not subjects:
        return []
    scored: list[tuple[int, int, DisclosedRecord]] = []
    for index, candidate in enumerate(candidates):
        if candidate.record_id == record.record_id:
            continue
        shared = subjects & set(candidate.dublin_core.get("subject") or ())
        if shared:
            scored.append((len(shared), index, candidate))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [candidate for _shared, _index, candidate in scored[:limit]]


def filter_by_date_range(
    records: Sequence[DisclosedRecord], *, start: str = "", end: str = ""
) -> list[DisclosedRecord]:
    """Keep records whose first Dublin Core ``date`` falls within ``[start, end]``.

    Lets a reader narrow a collection to an era (user research P2-3). Bounds are the
    ISO-ish forms ledger stores (``YYYY``, ``YYYY-MM``, ``YYYY-MM-DD``); an empty bound
    is open on that side. Comparison is lexical, which orders those forms correctly,
    and ``end`` is *inclusive of the whole period* — a record dated ``1994-05-01`` is
    kept by ``end=1994`` (its date begins with the bound), so "through 1994" means all
    of 1994. A record with no date is excluded whenever a range is in force, since an
    absent date cannot be placed in time. Input order is preserved; only disclosed
    dates are read (no-outing rule)."""
    if not start and not end:
        return list(records)
    kept: list[DisclosedRecord] = []
    for record in records:
        values = record.dublin_core.get("date") or []
        date = values[0] if values and values[0] else ""
        if not date:
            continue
        if start and date < start:
            continue
        if end and not (date <= end or date.startswith(end)):
            continue
        kept.append(record)
    return kept
