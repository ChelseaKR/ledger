"""Tests for the legal-process transparency log (EXP-10, warrant canary).

Pins the durable, hash-chained attestation store and its staleness math — the two
things the code layer is actually responsible for, since the canary's legal
substance is explicitly gated on counsel review (see ``docs/TRANSPARENCY.md``,
EXP-10).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ledger.errors import LedgerError
from ledger.transparency import (
    Attestation,
    TransparencyLog,
    days_since,
    is_stale,
    verify_chain,
)


@pytest.mark.disclosure
def test_unknown_demand_type_is_rejected() -> None:
    with pytest.raises(LedgerError):
        Attestation(
            attested_date="2026-01-01",
            attested_by="steward",
            statement_text="x",
            demand_counts={"secret_letter_of_marque": 1},
        )


@pytest.mark.disclosure
def test_negative_demand_count_is_rejected() -> None:
    with pytest.raises(LedgerError):
        Attestation(
            attested_date="2026-01-01",
            attested_by="steward",
            statement_text="x",
            demand_counts={"subpoena": -1},
        )


@pytest.mark.disclosure
def test_first_attestation_chains_from_empty_prev_digest(tmp_path: Path) -> None:
    log = TransparencyLog(tmp_path / "transparency.json")
    entry = log.append(
        attested_date="2026-01-01",
        attested_by="steward-a",
        statement_text="No legal demands received to date.",
        demand_counts={"subpoena": 0},
    )
    assert entry.prev_digest == ""
    assert entry.digest
    assert entry.digest == entry.content_digest()


@pytest.mark.disclosure
def test_successive_attestations_chain_and_verify(tmp_path: Path) -> None:
    log = TransparencyLog(tmp_path / "transparency.json")
    first = log.append(
        attested_date="2026-01-01",
        attested_by="steward-a",
        statement_text="No legal demands received to date.",
    )
    second = log.append(
        attested_date="2026-02-01",
        attested_by="steward-a",
        statement_text="One subpoena received and contested; see counsel note.",
        demand_counts={"subpoena": 1},
        counsel_reviewed=True,
        counsel_review_note="Reviewed 2026-01-30 by outside counsel.",
    )
    assert second.prev_digest == first.digest
    entries = log.all()
    assert [e.attested_date for e in entries] == ["2026-01-01", "2026-02-01"]
    assert verify_chain(entries)
    assert log.latest() == second


@pytest.mark.disclosure
def test_tampered_entry_breaks_chain_verification(tmp_path: Path) -> None:
    path = tmp_path / "transparency.json"
    log = TransparencyLog(path)
    log.append(attested_date="2026-01-01", attested_by="steward-a", statement_text="Statement A.")
    log.append(attested_date="2026-02-01", attested_by="steward-a", statement_text="Statement B.")

    entries = log.all()
    # Simulate a rewritten history: the first entry's statement is altered after
    # the fact without recomputing digests down the chain.
    tampered_first = Attestation(
        attested_date=entries[0].attested_date,
        attested_by=entries[0].attested_by,
        statement_text="A quietly rewritten statement.",
        prev_digest=entries[0].prev_digest,
        digest=entries[0].digest,
    )
    assert not verify_chain([tampered_first, entries[1]])


@pytest.mark.disclosure
def test_reloaded_log_round_trips_and_still_verifies(tmp_path: Path) -> None:
    path = tmp_path / "transparency.json"
    log = TransparencyLog(path)
    log.append(attested_date="2026-01-01", attested_by="steward-a", statement_text="A.")
    log.append(attested_date="2026-02-01", attested_by="steward-a", statement_text="B.")

    reloaded = TransparencyLog(path).all()
    assert len(reloaded) == 2
    assert verify_chain(reloaded)


@pytest.mark.disclosure
def test_corrupt_log_fails_closed_and_is_not_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "transparency.json"
    path.write_text("not-json", encoding="utf-8")
    log = TransparencyLog(path)

    with pytest.raises(LedgerError):
        log.all()
    with pytest.raises(LedgerError):
        log.append(attested_date="2026-01-01", attested_by="s", statement_text="x")
    assert path.read_text(encoding="utf-8") == "not-json"


@pytest.mark.disclosure
def test_loaded_counsel_flag_is_not_truthy_string_coercion(tmp_path: Path) -> None:
    path = tmp_path / "transparency.json"
    path.write_text(
        json.dumps(
            [
                {
                    "attested_date": "2026-01-01",
                    "attested_by": "s",
                    "statement_text": "x",
                    "counsel_reviewed": "false",
                }
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(LedgerError):
        TransparencyLog(path).all()


@pytest.mark.disclosure
def test_days_since_and_staleness() -> None:
    now = datetime(2026, 3, 1, tzinfo=UTC)
    assert days_since("2026-01-01", now=now) == 59
    assert days_since("not-a-date", now=now) is None
    assert days_since("2027-01-01", now=now) is None

    fresh = Attestation(attested_date="2026-02-20", attested_by="s", statement_text="x")
    stale = Attestation(attested_date="2025-01-01", attested_by="s", statement_text="x")

    assert not is_stale(fresh, 30, now=now)
    assert is_stale(stale, 30, now=now)
    # Never a current statement rendered from silence.
    assert is_stale(None, 30, now=now)


@pytest.mark.disclosure
def test_total_demands_sums_all_types() -> None:
    entry = Attestation(
        attested_date="2026-01-01",
        attested_by="s",
        statement_text="x",
        demand_counts={"subpoena": 2, "court_order": 1},
    )
    assert entry.total_demands() == 3


# --- rejection paths (#83) ---------------------------------------------------
#
# The canary's value is entirely in what it REFUSES to record. Every guard below
# was reachable-but-unexercised: `transparency.py` sat at 84% branch coverage with
# all eighteen uncovered lines being `raise` statements. An unexercised rejection
# path is indistinguishable from an absent one until the day it matters, and on
# this module the day it matters is a legal demand arriving under a gag order.


@pytest.mark.disclosure
def test_a_malformed_attested_date_is_rejected() -> None:
    with pytest.raises(LedgerError, match="attested_date"):
        Attestation(attested_date="20260101", attested_by="s", statement_text="x")


@pytest.mark.disclosure
def test_an_empty_attester_is_rejected() -> None:
    """A canary signed by nobody is not a canary."""
    with pytest.raises(LedgerError, match="attested_by"):
        Attestation(attested_date="2026-01-01", attested_by="   ", statement_text="x")


@pytest.mark.disclosure
def test_an_empty_statement_is_rejected() -> None:
    with pytest.raises(LedgerError, match="statement_text"):
        Attestation(attested_date="2026-01-01", attested_by="s", statement_text="  ")


@pytest.mark.disclosure
def test_a_non_boolean_counsel_review_flag_is_rejected() -> None:
    """`1` is truthy, and a truthy value here would read as "counsel reviewed this"
    in the rendered canary. The check is `type(...) is not bool` for that reason."""
    with pytest.raises(LedgerError, match="counsel_reviewed"):
        Attestation(
            attested_date="2026-01-01",
            attested_by="s",
            statement_text="x",
            counsel_reviewed=1,  # type: ignore[arg-type]  # deliberately wrong type: that is the test
        )


@pytest.mark.disclosure
def test_claiming_counsel_review_without_a_note_is_rejected() -> None:
    with pytest.raises(LedgerError, match="counsel_review_note"):
        Attestation(
            attested_date="2026-01-01",
            attested_by="s",
            statement_text="x",
            counsel_reviewed=True,
        )


@pytest.mark.disclosure
@pytest.mark.parametrize("field_name", ["prev_digest", "digest"])
@pytest.mark.parametrize("bad", ["deadbeef", "z" * 64, "A" * 64])
def test_a_digest_that_is_not_sha256_hex_is_rejected(field_name: str, bad: str) -> None:
    """Short, non-hex, and upper-case are each rejected; only lower-case hex of
    exactly 64 characters, or the empty string, is a digest here."""
    with pytest.raises(LedgerError, match=field_name):
        Attestation(
            attested_date="2026-01-01",
            attested_by="s",
            statement_text="x",
            **{field_name: bad},
        )


@pytest.mark.disclosure
def test_an_empty_digest_is_allowed_so_the_first_entry_can_chain_from_nothing() -> None:
    """The positive control for the test above: without it, `_validate_digest`
    could be made to pass by rejecting everything."""
    entry = Attestation(
        attested_date="2026-01-01", attested_by="s", statement_text="x", prev_digest=""
    )
    assert entry.prev_digest == ""


@pytest.mark.disclosure
def test_from_dict_rejects_demand_counts_that_are_not_an_object() -> None:
    with pytest.raises(LedgerError, match="demand_counts must be an object"):
        Attestation.from_dict({"attested_date": "2026-01-01", "demand_counts": []})


@pytest.mark.disclosure
@pytest.mark.parametrize("counts", [{"subpoena": "1"}, {"subpoena": True}, {1: 1}])
def test_from_dict_rejects_demand_counts_that_are_not_string_to_int(
    counts: dict[object, object],
) -> None:
    """`True` is an `int` to `isinstance`, so the check is `type(value) is not int`:
    a boolean must not be able to pose as a demand count."""
    with pytest.raises(LedgerError, match="demand_counts must map"):
        Attestation.from_dict({"attested_date": "2026-01-01", "demand_counts": counts})


@pytest.mark.disclosure
def test_from_dict_rejects_a_non_boolean_counsel_review_flag() -> None:
    with pytest.raises(LedgerError, match="counsel_reviewed"):
        Attestation.from_dict({"attested_date": "2026-01-01", "counsel_reviewed": "yes"})


@pytest.mark.disclosure
def test_from_dict_rejects_a_non_string_text_field() -> None:
    with pytest.raises(LedgerError, match="statement_text must be a string"):
        Attestation.from_dict({"attested_date": "2026-01-01", "statement_text": 7})


@pytest.mark.disclosure
def test_a_damaged_log_raises_rather_than_reading_as_no_attestations(tmp_path: Path) -> None:
    """The absence-vs-damage rule the JSON stores now hold (#154) applies here with
    more force: "no attestations" is exactly what a triggered canary looks like, so
    a log that cannot be parsed must never render as one."""
    path = tmp_path / "transparency.json"
    path.write_text("not json at all", encoding="utf-8")
    with pytest.raises(LedgerError, match="not valid JSON"):
        TransparencyLog(path).all()


@pytest.mark.disclosure
def test_a_log_that_is_not_a_list_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "transparency.json"
    path.write_text(json.dumps({"attested_date": "2026-01-01"}), encoding="utf-8")
    with pytest.raises(LedgerError, match="must contain a JSON list"):
        TransparencyLog(path).all()


@pytest.mark.disclosure
def test_a_log_whose_entries_are_not_objects_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "transparency.json"
    path.write_text(json.dumps(["2026-01-01"]), encoding="utf-8")
    with pytest.raises(LedgerError, match="every transparency log entry"):
        TransparencyLog(path).all()


@pytest.mark.disclosure
def test_an_unreadable_log_raises_rather_than_reading_as_empty(tmp_path: Path) -> None:
    """A directory where the log file should be: `read_text` raises `OSError`, and
    the store must not translate that into "nothing has been attested"."""
    path = tmp_path / "transparency.json"
    path.mkdir()
    with pytest.raises(LedgerError, match="could not be read"):
        TransparencyLog(path).all()


@pytest.mark.disclosure
def test_a_missing_log_is_still_an_empty_log(tmp_path: Path) -> None:
    """The positive control for the four tests above: absence is not damage."""
    assert TransparencyLog(tmp_path / "nothing-here.json").all() == []


@pytest.mark.disclosure
def test_append_reports_a_write_failure_rather_than_losing_the_attestation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An `OSError` during the write must surface as a `LedgerError`, never as a
    silent no-op that leaves the steward believing they re-attested."""
    log = TransparencyLog(tmp_path / "transparency.json")

    def boom(self: object, items: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(TransparencyLog, "_write", boom)
    with pytest.raises(LedgerError, match="could not be written"):
        log.append(attested_date="2026-01-01", attested_by="s", statement_text="x")


@pytest.mark.disclosure
def test_a_failed_write_leaves_no_temp_file_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_write` unlinks its temp file before re-raising, so a full disk does not
    leave a half-written attestation sitting next to the real log."""
    path = tmp_path / "transparency.json"
    log = TransparencyLog(path)

    def boom(src: object, dst: object) -> None:
        raise OSError("rename failed")

    monkeypatch.setattr("ledger.transparency.os.replace", boom)
    with pytest.raises(LedgerError, match="could not be written"):
        log.append(attested_date="2026-01-01", attested_by="s", statement_text="x")
    assert list(path.parent.glob("transparency.json.*.tmp")) == []


@pytest.mark.disclosure
def test_verify_chain_rejects_a_broken_link() -> None:
    """`verify_chain` has two ways to fail — a `prev_digest` that does not match the
    predecessor, and a `digest` that does not match its own content. Both are
    checked, because either alone would let a whole class of edit through."""
    first = Attestation(attested_date="2026-01-01", attested_by="s", statement_text="one")
    first = Attestation(
        attested_date=first.attested_date,
        attested_by=first.attested_by,
        statement_text=first.statement_text,
        digest=first.content_digest(),
    )
    second = Attestation(
        attested_date="2026-02-01",
        attested_by="s",
        statement_text="two",
        prev_digest="0" * 64,  # not `first.digest`
    )
    second = Attestation(
        attested_date=second.attested_date,
        attested_by=second.attested_by,
        statement_text=second.statement_text,
        prev_digest=second.prev_digest,
        digest=second.content_digest(),
    )
    assert verify_chain([first]) is True
    assert verify_chain([first, second]) is False
