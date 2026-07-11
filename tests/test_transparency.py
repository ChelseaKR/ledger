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
