"""Tests for CSV export of disclosed records (``ledger.export``).

The export serialises only the safe ``DisclosedRecord`` shape (no identity), and it
defuses spreadsheet formula injection — a value starting with ``=``/``+``/``-``/``@``
is imported as text, never run.
"""

from __future__ import annotations

from ledger.export import records_csv
from ledger.models import DisclosedRecord


def _rec(rid: str, title: str, **dc: list[str]) -> DisclosedRecord:
    return DisclosedRecord(
        record_id=rid,
        title=title,
        dublin_core=dc,
        fields={},
        payloads=(),
        content_warnings=(),
        withheld=(),
    )


def test_records_csv_header_and_row() -> None:
    csv = records_csv(
        [_rec("a", "A march", subject=["protest", "housing"], date=["1990"])],
        base_url="https://archive.example/",
    )
    lines = csv.splitlines()
    assert lines[0] == "record_id,title,date,subjects,types,languages,url"
    assert "a,A march,1990,protest; housing,,,https://archive.example/record/a" in csv


def test_records_csv_quotes_commas() -> None:
    csv = records_csv([_rec("a", "Title, with comma")], base_url="https://h")
    assert '"Title, with comma"' in csv


def test_csv_injection_is_defused() -> None:
    """A formula-leading title is prefixed so a spreadsheet imports it as text."""
    csv = records_csv([_rec("a", "=SUM(A1:A2)")], base_url="https://h")
    assert "'=SUM(A1:A2)" in csv
    # No cell begins the formula unguarded.
    assert ",=SUM" not in csv
