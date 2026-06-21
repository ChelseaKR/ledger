"""Tabular (CSV) export of disclosed records, for analysis in a spreadsheet.

A researcher narrowing the collection (search + facets + date range) often wants the
result set in a spreadsheet, not one page at a time. This module renders a
:class:`~ledger.models.DisclosedRecord` sequence as CSV.

Two properties matter here:

* **No-outing by construction.** It serializes only ``DisclosedRecord`` — the safe
  shape, which structurally cannot carry a contributor identity or a sealed value — so
  an export can never leak more than a read path already showed. Only the public
  descriptive fields (title, date, subjects, types, languages) and the record's public
  URL are written.
* **CSV-injection safe.** A cell whose text begins with ``=``, ``+``, ``-``, or ``@``
  is a formula to a spreadsheet; such a value is prefixed with a single quote so it is
  imported as text, never executed (a stored-data-to-spreadsheet attack a community
  archive must not enable).
"""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence

from ledger.models import DisclosedRecord

_HEADER = ("record_id", "title", "date", "subjects", "types", "languages", "url")

# Leading characters a spreadsheet may interpret as the start of a formula.
_FORMULA_LEADERS = ("=", "+", "-", "@")


def _csv_safe(value: str) -> str:
    """Defuse a value a spreadsheet might run as a formula (CSV injection)."""
    return "'" + value if value[:1] in _FORMULA_LEADERS else value


def records_csv(records: Sequence[DisclosedRecord], *, base_url: str) -> str:
    """Render ``records`` as CSV with a header row, one record per line.

    Columns: ``record_id``, ``title``, the first Dublin Core ``date``, ``subjects`` /
    ``types`` / ``languages`` (semicolon-joined), and the record's public ``url``. The
    standard library's :mod:`csv` writer handles quoting, and :func:`_csv_safe` guards
    each text cell against formula injection. Deterministic for given input."""
    root = base_url.rstrip("/")
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(_HEADER)
    for record in records:
        dc = record.dublin_core
        writer.writerow(
            [
                _csv_safe(record.record_id),
                _csv_safe(record.title),
                _csv_safe((dc.get("date") or [""])[0]),
                _csv_safe("; ".join(dc.get("subject") or [])),
                _csv_safe("; ".join(dc.get("type") or [])),
                _csv_safe("; ".join(dc.get("language") or [])),
                _csv_safe(f"{root}/record/{record.record_id}"),
            ]
        )
    return out.getvalue()
