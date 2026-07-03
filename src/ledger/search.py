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

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from ledger.models import DisclosedRecord, parse_iso

__all__ = [
    "Facet",
    "RecordRelations",
    "Snippet",
    "facet_by_coverage",
    "facets",
    "filter_by_date_range",
    "filter_by_facet",
    "group_by_year",
    "index_text",
    "looks_non_latin",
    "related_by_subject",
    "resolve_relations",
    "search",
    "snippet",
    "sort_by_date",
]

# A record id is a uuid4 hex string (see ``Record.record_id``): 32 lowercase hex
# digits. The relation resolver uses this shape to tell an *internal* reference (a
# value that names a record id) from an *external* identifier (a DOI, handle, or
# URL) — see :func:`resolve_relations` for why the distinction is safety-critical.
_RECORD_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
# A four-digit year at the start of an ISO-ish DC date (``YYYY``, ``YYYY-MM``, …).
_YEAR_RE = re.compile(r"\s*(\d{4})")


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


def facet_by_coverage(records: Sequence[DisclosedRecord]) -> list[Facet]:
    """Count the distinct Dublin Core ``coverage`` (place) values across ``records``.

    The place-browse counterpart to the subject/type/language facets: it powers the
    ``/places`` view, where each place links into the composed ``?coverage=`` facet
    query so a reader can pivot from a place to every record that names it (roadmap
    EX3). This is a thin, named alias over :func:`facets` so the place browse reads by
    intent at the call site and stays in lock-step with how every other facet is
    counted. Only disclosed Dublin Core is consulted, so a place never surfaces a
    value a viewer may not see (no-outing rule), and the ordering is the same stable
    count-descending, value-ascending order as every other facet.
    """
    return facets(records, "coverage")


def group_by_year(records: Sequence[DisclosedRecord]) -> list[tuple[str, list[DisclosedRecord]]]:
    """Group ``records`` by the year of their first Dublin Core ``date``, oldest first.

    The timeline-browse counterpart to the facets (roadmap EX3): each group is a
    ``(year, records)`` pair, years ascending so the timeline reads chronologically,
    with the caller's input order preserved *within* a year as a stable tie-break
    (reproducible ordering). The year is the four-digit lead of the disclosed ``date``
    (``1994``, ``1994-05``, ``1994-05-01`` all group under ``1994``); a full ISO
    timestamp is parsed via :func:`~ledger.models.parse_iso`, and anything else falls
    back to a lenient leading-year match.

    A record with no date — or a date with no extractable year — is **omitted**
    entirely rather than bucketed, so an undated record never masquerades as belonging
    to a year; the caller reports the omitted count as a plain note. Only disclosed
    dates are read, so grouping can never reflect a withheld value (no-outing rule).
    """
    buckets: dict[str, list[DisclosedRecord]] = {}
    for record in records:
        year = _year_of(record)
        if year is not None:
            buckets.setdefault(year, []).append(record)
    return [(year, buckets[year]) for year in sorted(buckets)]


def _year_of(record: DisclosedRecord) -> str | None:
    """The four-digit year of ``record``'s first Dublin Core ``date``, or ``None``.

    Tries a strict ISO parse first (so a full timestamp yields its calendar year),
    then a lenient leading four-digit match for the bare ``YYYY``/``YYYY-MM`` forms
    ledger stores. Returns ``None`` when there is no date or no year can be read, so
    the caller can omit the record from the timeline rather than mis-dating it."""
    values = record.dublin_core.get("date") or []
    raw = values[0] if values and values[0] else ""
    if not raw:
        return None
    try:
        return f"{parse_iso(raw).year:04d}"
    except (ValueError, TypeError):
        match = _YEAR_RE.match(raw)
        return match.group(1) if match else None


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


@dataclass(frozen=True)
class RecordRelations:
    """The record-to-record relationships of one record, resolved for display.

    Powers the accessible "related records" graph (roadmap EX4): a record's Dublin
    Core ``relation`` values and the records that name *it* in theirs, presented as a
    plain list rather than a visual node graph (the documented non-visual equivalent).

    ``outgoing`` are the disclosed records this record points at; ``incoming`` are the
    disclosed records that point back at it (the reciprocal); ``external`` are relation
    values that name something outside the archive (a DOI, handle, or URL) and so
    render as plain text, not a link. Every record here is a
    :class:`DisclosedRecord`, so a relationship can never surface a record the viewer
    may not already list, and — critically — a relation that names a *sealed* or
    withheld record resolves to nothing at all, never leaking that it exists.
    """

    outgoing: tuple[DisclosedRecord, ...]
    incoming: tuple[DisclosedRecord, ...]
    external: tuple[str, ...]

    def __bool__(self) -> bool:
        """True when there is any relationship worth rendering."""
        return bool(self.outgoing or self.incoming or self.external)


def _looks_like_record_id(value: str) -> bool:
    """Whether ``value`` has the shape of an internal record id (a uuid4 hex).

    The relation resolver uses this to decide how an *unresolvable* relation value is
    treated: an id-shaped value that is not in the disclosed set is a record the viewer
    may not see (sealed, withheld, or gone) and must render as **nothing** so the page
    never confirms it exists; a value that is *not* id-shaped is an external identifier
    and renders as plain text. The distinction is what lets the archive show honest
    external references without ever leaking a hidden internal record (no-outing rule).
    """
    return bool(_RECORD_ID_RE.fullmatch(value))


def resolve_relations(
    record: DisclosedRecord, candidates: Sequence[DisclosedRecord]
) -> RecordRelations:
    """Resolve ``record``'s Dublin Core ``relation`` links against disclosed records.

    ``candidates`` is the set the *viewer* may list (the ``Archive.browse`` output), so
    resolution is closed over exactly what the viewer can already see. For each
    ``relation`` value on ``record``:

    * if it names a disclosed record's id -> that record is an **outgoing** link;
    * else if it does not even look like a record id -> it is an **external**
      identifier, shown as plain text (no link);
    * else (id-shaped but not disclosed) -> it is dropped silently, so a relation
      pointing at a sealed/withheld/absent record leaks nothing (no-outing rule).

    The **incoming** (reciprocal) set is every disclosed candidate whose own
    ``relation`` names *this* record's id — "records that reference this one". A record
    never relates to itself, duplicate values collapse, and input order is preserved,
    so the rendered graph is stable and reproducible.
    """
    by_id = {c.record_id: c for c in candidates}
    outgoing: list[DisclosedRecord] = []
    external: list[str] = []
    seen_out: set[str] = set()
    for value in record.dublin_core.get("relation") or ():
        if value == record.record_id or value in seen_out:
            continue
        target = by_id.get(value)
        if target is not None:
            outgoing.append(target)
            seen_out.add(value)
        elif not _looks_like_record_id(value):
            if value not in external:
                external.append(value)
        # else: an id-shaped value we cannot see -> render nothing (no-outing).

    incoming: list[DisclosedRecord] = []
    for candidate in candidates:
        if candidate.record_id == record.record_id:
            continue
        if record.record_id in (candidate.dublin_core.get("relation") or ()):
            incoming.append(candidate)

    return RecordRelations(
        outgoing=tuple(outgoing), incoming=tuple(incoming), external=tuple(external)
    )


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
