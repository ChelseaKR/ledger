"""Pure pagination maths for the browse and search views (backlog: scale + a11y).

The browse page rendered *every* listable record into one response. That does not
scale as a collection grows, and a single enormous page is itself an accessibility
problem: a screen-reader user must wade through hundreds of items, and a reader on a
slow or metered connection pays for all of them at once. This module slices a result
set into bounded pages.

It is deliberately tiny and pure — no I/O, no request state, no clock — so the paging
maths is trivially testable and deterministic. :func:`paginate` *clamps* an
out-of-range page rather than raising, so a hand-edited or stale ``?page=`` can never
500 the server or reveal anything; it simply lands on the nearest real page. The
slice is taken from whatever the caller already disclosed, so pagination can neither
add nor remove access — it only windows a list the caller was already allowed to see.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")

#: Records per page. Small enough to keep a page light for assistive tech and slow
#: links, large enough that a modest collection is one or two pages.
DEFAULT_PER_PAGE: int = 20


@dataclass(frozen=True)
class Page(Generic[T]):
    """One windowed slice of a result set, plus the facts a pager needs to render.

    ``number`` is the 1-based current page (always within ``1..pages``), ``total`` is
    the full result count, and ``items`` is just this page's slice. The derived
    properties answer the questions a pager and a status line ask, so the view never
    recomputes paging arithmetic itself.
    """

    items: tuple[T, ...]
    number: int
    per_page: int
    total: int

    @property
    def pages(self) -> int:
        """Total number of pages — at least 1, even when the result set is empty."""
        if self.per_page <= 0 or self.total <= 0:
            return 1
        return (self.total + self.per_page - 1) // self.per_page

    @property
    def has_prev(self) -> bool:
        return self.number > 1

    @property
    def has_next(self) -> bool:
        return self.number < self.pages

    @property
    def start_index(self) -> int:
        """1-based index of the first item on this page (0 when the set is empty)."""
        if self.total == 0:
            return 0
        return (self.number - 1) * self.per_page + 1

    @property
    def end_index(self) -> int:
        """1-based index of the last item on this page (0 when the set is empty)."""
        if self.total == 0:
            return 0
        return self.start_index + len(self.items) - 1


def paginate(items: Sequence[T], page: int, per_page: int = DEFAULT_PER_PAGE) -> Page[T]:
    """Return the :class:`Page` for ``items`` at the requested 1-based ``page``.

    The page number is clamped into ``1..pages`` so an out-of-range or non-positive
    request (a stale link, a hand-typed ``?page=999``) resolves to the nearest real
    page instead of erroring or returning an empty slice misleadingly. ``per_page`` is
    floored at 1. Pure: it copies a slice and computes counts, touching nothing else.
    """
    size = max(1, per_page)
    total = len(items)
    pages = max(1, (total + size - 1) // size)
    number = min(max(1, page), pages)
    start = (number - 1) * size
    return Page(items=tuple(items[start : start + size]), number=number, per_page=size, total=total)
