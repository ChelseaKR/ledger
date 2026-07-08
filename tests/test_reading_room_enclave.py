"""Tests for EXP-14: the reading-room enclave (aggregate research access).

These pin the three structural safety properties the module claims:

1. a query never answers without meeting `config.dual_control_threshold`
   (never interactive, always human-approved);
2. a bucket below the k-anonymity floor is suppressed, and the query's total is
   suppressed too whenever any bucket is (closes the within-query differencing
   attack via `total - sum(visible cells)`);
3. two queries whose matching-record sets are a near-miss of each other are
   refused (the cross-query differencing attack) — and the refusal, not a
   partial answer, is what gets returned and logged.

And the structural guarantee underneath all three: nothing this module returns
or logs ever carries a record id or a field/payload value.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ledger.config import Config
from ledger.dualcontrol import ProposalStore
from ledger.errors import AggregationRefused, LedgerError
from ledger.ingest import Archive
from ledger.metadata.premis import PremisLog
from ledger.models import AccessPolicy, DublinCore, Record
from ledger.reading_room_enclave import AggregateQuery, ReadingRoomEnclave

pytestmark = pytest.mark.disclosure

_NOW = "2026-01-01T00:00:00Z"

# A loud sentinel: if it ever appeared in a logged detail or a result, that would
# be the enclave leaking record content instead of an aggregate count.
_SENTINEL_TITLE = "SENTINEL-TITLE-DO-NOT-LEAK-4KQ9"


def _archive(tmp_path: Path, *, k_floor: int = 2, threshold: int = 1) -> Archive:
    config = Config.default("Enclave Test", tmp_path / "arc")
    config.reading_room_k_floor = k_floor
    config.dual_control_threshold = threshold
    return Archive.init(config)


def _seed(
    archive: Archive,
    *,
    n: int,
    year: str,
    subject: str,
    type_: str = "oral-history",
    policy: AccessPolicy = AccessPolicy.SEALED_UNTIL,
) -> None:
    """Ingest ``n`` records, entirely sealed by default, sharing one dimension value.

    ``policy=SEALED_UNTIL`` (the archive's own narrowest default) means none of
    these records is listable to any ordinary grant — proving the enclave counts
    across material a normal disclosure path would hide entirely.
    """
    for i in range(n):
        record = Record(
            title=_SENTINEL_TITLE,
            default_policy=policy,
            dublin_core=DublinCore(
                title=[_SENTINEL_TITLE],
                subject=[subject],
                date=[f"{year}-01-01"],
                type=[type_],
                description=[f"a {subject} account, item {i}"],
            ),
        )
        archive.ingest({}, record, agent="seed", now=_NOW)


def _events(archive: Archive) -> list:
    path = archive.logs_dir / "reading-room-queries.premis.json"
    if not path.exists():
        return []
    return PremisLog.read(path).events


# --- AggregateQuery: closed vocabulary --------------------------------------


def test_query_rejects_unknown_dimension() -> None:
    with pytest.raises(LedgerError):
        AggregateQuery(dimension="record_id", reason="r")


def test_query_rejects_unknown_match_field() -> None:
    with pytest.raises(LedgerError):
        AggregateQuery(dimension="year", reason="r", match_field="title", match_term="x")


def test_query_requires_reason() -> None:
    with pytest.raises(LedgerError):
        AggregateQuery(dimension="year", reason="   ")


def test_query_requires_match_term_with_match_field() -> None:
    with pytest.raises(LedgerError):
        AggregateQuery(dimension="year", reason="r", match_field="subject")


def test_query_signature_never_carries_free_text_beyond_its_own_term() -> None:
    q = AggregateQuery(dimension="year", reason="r", match_field="subject", match_term="Eviction")
    assert q.signature() == "year:subject:eviction"


# --- dual-control gate -------------------------------------------------------


def test_execute_refuses_before_threshold_met(tmp_path: Path) -> None:
    archive = _archive(tmp_path, threshold=2)
    _seed(archive, n=5, year="2020", subject="eviction")
    enclave = ReadingRoomEnclave(archive)
    query = AggregateQuery(dimension="year", reason="D1 research request")
    prop = enclave.propose(query, proposer="steward-a", now=_NOW)
    with pytest.raises(LedgerError):
        enclave.execute(prop.proposal_id, actor="steward-a", now=_NOW)
    # A distinct second steward's approval is what unblocks it.
    enclave.approve(prop.proposal_id, "steward-b")
    result = enclave.execute(prop.proposal_id, actor="steward-b", now=_NOW)
    assert result.total == 5


def test_same_steward_approving_twice_never_satisfies_threshold(tmp_path: Path) -> None:
    archive = _archive(tmp_path, threshold=2)
    _seed(archive, n=5, year="2020", subject="eviction")
    enclave = ReadingRoomEnclave(archive)
    prop = enclave.propose(AggregateQuery(dimension="year", reason="r"), proposer="steward-a")
    enclave.approve(prop.proposal_id, "steward-a")  # re-approval, not a distinct steward
    with pytest.raises(LedgerError):
        enclave.execute(prop.proposal_id, actor="steward-a", now=_NOW)


# --- k-anonymity suppression -------------------------------------------------


def test_bucket_below_k_floor_is_suppressed(tmp_path: Path) -> None:
    archive = _archive(tmp_path, k_floor=3)
    _seed(archive, n=5, year="2020", subject="eviction")
    _seed(archive, n=2, year="2021", subject="eviction")  # below k=3
    enclave = ReadingRoomEnclave(archive)
    query = AggregateQuery(
        dimension="year", reason="r", match_field="subject", match_term="eviction"
    )
    prop = enclave.propose(query, proposer="steward-a", now=_NOW)
    result = enclave.execute(prop.proposal_id, actor="steward-a", now=_NOW)

    by_label = {b.label: b.count for b in result.buckets}
    assert by_label["2020"] == 5
    assert by_label["2021"] is None  # suppressed, not omitted (honest about existing)
    assert result.suppressed_buckets == 1


def test_total_is_suppressed_whenever_any_bucket_is(tmp_path: Path) -> None:
    """The classic differencing attack: total - visible cells must not recover a cell."""
    archive = _archive(tmp_path, k_floor=3)
    _seed(archive, n=5, year="2020", subject="eviction")
    _seed(archive, n=2, year="2021", subject="eviction")
    enclave = ReadingRoomEnclave(archive)
    query = AggregateQuery(
        dimension="year", reason="r", match_field="subject", match_term="eviction"
    )
    prop = enclave.propose(query, proposer="steward-a", now=_NOW)
    result = enclave.execute(prop.proposal_id, actor="steward-a", now=_NOW)
    assert result.total is None  # NOT 7 — that would leak "2021 has 2" by subtraction


def test_all_buckets_at_or_above_floor_are_never_suppressed(tmp_path: Path) -> None:
    archive = _archive(tmp_path, k_floor=3)
    _seed(archive, n=3, year="2020", subject="eviction")
    _seed(archive, n=4, year="2021", subject="eviction")
    enclave = ReadingRoomEnclave(archive)
    query = AggregateQuery(
        dimension="year", reason="r", match_field="subject", match_term="eviction"
    )
    prop = enclave.propose(query, proposer="steward-a", now=_NOW)
    result = enclave.execute(prop.proposal_id, actor="steward-a", now=_NOW)
    assert result.suppressed_buckets == 0
    assert result.total == 7


def test_k_floor_may_be_raised_but_never_lowered_below_config(tmp_path: Path) -> None:
    archive = _archive(tmp_path, k_floor=5)
    _seed(archive, n=10, year="2020", subject="eviction")
    enclave = ReadingRoomEnclave(archive)
    prop = enclave.propose(AggregateQuery(dimension="year", reason="r"), proposer="a", now=_NOW)
    with pytest.raises(LedgerError):
        enclave.execute(prop.proposal_id, actor="a", k_floor=2, now=_NOW)  # weaker than config
    result = enclave.execute(prop.proposal_id, actor="a", k_floor=9, now=_NOW)  # stricter: ok
    assert result.k_floor == 9


def test_aggregation_never_touches_records_a_normal_grant_could_not_even_list(
    tmp_path: Path,
) -> None:
    """Every seeded record defaults to SEALED_UNTIL — invisible to any ordinary grant.

    The enclave must still count them: that is EXP-14's whole point (aggregate
    access to the sealed 90%), and it is exercised structurally here via
    `Archive.all_records_for_aggregation`, never via `browse`/`disclose`.
    """
    archive = _archive(tmp_path, k_floor=2)
    _seed(archive, n=4, year="2020", subject="eviction", policy=AccessPolicy.SEALED_UNTIL)
    from ledger.access.grants import anonymous

    assert archive.browse(anonymous(), now=_NOW) == []  # nothing listable to the public
    enclave = ReadingRoomEnclave(archive)
    prop = enclave.propose(AggregateQuery(dimension="year", reason="r"), proposer="a", now=_NOW)
    result = enclave.execute(prop.proposal_id, actor="a", now=_NOW)
    assert result.total == 4  # counted anyway, as an aggregate only


# --- differencing-attack guard (fails closed) --------------------------------


def test_differencing_guard_refuses_a_near_miss_of_a_prior_answer(tmp_path: Path) -> None:
    archive = _archive(tmp_path, k_floor=3)
    _seed(archive, n=10, year="2020", subject="eviction", type_="oral-history")
    _seed(archive, n=1, year="2020", subject="eviction", type_="court-filing")
    enclave = ReadingRoomEnclave(archive)

    # One bucket ("2020"), so this answers cleanly: 11 records, nothing suppressed.
    broad = AggregateQuery(
        dimension="year", reason="r", match_field="subject", match_term="eviction"
    )
    prop1 = enclave.propose(broad, proposer="a", now=_NOW)
    result1 = enclave.execute(prop1.proposal_id, actor="a", now=_NOW)
    assert result1.total == 11
    assert result1.suppressed_buckets == 0

    # A second query whose matching set differs from the first by just 1 record
    # (drop the lone court-filing) would let a reader isolate that one record by
    # comparing totals (11 vs. 10) — refused, not answered.
    narrow = AggregateQuery(
        dimension="year", reason="r", match_field="type", match_term="oral-history"
    )
    prop2 = enclave.propose(narrow, proposer="a", now=_NOW)
    with pytest.raises(AggregationRefused):
        enclave.execute(prop2.proposal_id, actor="a", now=_NOW)

    # The refused proposal stays open (not silently marked executed) so a steward
    # can reconsider it rather than the system pretending it never happened.
    reloaded = ProposalStore(archive.logs_dir / "proposals.json").get(prop2.proposal_id)
    assert reloaded is not None
    assert reloaded.status == "open"


def test_identical_query_answered_twice_is_not_a_differencing_risk(tmp_path: Path) -> None:
    archive = _archive(tmp_path, k_floor=2)
    _seed(archive, n=5, year="2020", subject="eviction")
    enclave = ReadingRoomEnclave(archive)
    query = AggregateQuery(dimension="year", reason="r")

    prop1 = enclave.propose(query, proposer="a", now=_NOW)
    result1 = enclave.execute(prop1.proposal_id, actor="a", now=_NOW)

    prop2 = enclave.propose(query, proposer="a", now=_NOW)
    result2 = enclave.execute(prop2.proposal_id, actor="a", now=_NOW)  # must not raise

    assert result1.total == result2.total == 5


def test_differencing_guard_allows_a_safely_distant_second_query(tmp_path: Path) -> None:
    archive = _archive(tmp_path, k_floor=2)
    _seed(archive, n=5, year="2020", subject="eviction", type_="oral-history")
    _seed(archive, n=5, year="2021", subject="eviction", type_="court-filing")
    enclave = ReadingRoomEnclave(archive)

    q_all = AggregateQuery(dimension="year", reason="r")
    p1 = enclave.propose(q_all, proposer="a", now=_NOW)
    r1 = enclave.execute(p1.proposal_id, actor="a", now=_NOW)
    assert r1.total == 10

    # Matches only the 5 oral-history records — differs from the first answer's
    # matching set by 5 records, comfortably at/above k=2, so this is not a
    # near-miss and answers normally.
    q_type = AggregateQuery(
        dimension="year", reason="r", match_field="type", match_term="oral-history"
    )
    p2 = enclave.propose(q_type, proposer="a", now=_NOW)
    r2 = enclave.execute(p2.proposal_id, actor="a", now=_NOW)  # must not raise
    assert r2.total == 5


# --- PREMIS audit trail: results and refusals both logged --------------------


def test_answered_and_refused_queries_are_both_premis_logged(tmp_path: Path) -> None:
    archive = _archive(tmp_path, k_floor=3)
    _seed(archive, n=10, year="2020", subject="eviction", type_="oral-history")
    _seed(archive, n=1, year="2020", subject="eviction", type_="court-filing")
    enclave = ReadingRoomEnclave(archive)

    broad = AggregateQuery(
        dimension="year", reason="r", match_field="subject", match_term="eviction"
    )
    p1 = enclave.propose(broad, proposer="a", now=_NOW)
    enclave.execute(p1.proposal_id, actor="a", now=_NOW)

    narrow = AggregateQuery(
        dimension="year", reason="r", match_field="type", match_term="oral-history"
    )
    p2 = enclave.propose(narrow, proposer="a", now=_NOW)
    with pytest.raises(AggregationRefused):
        enclave.execute(p2.proposal_id, actor="a", now=_NOW)

    events = _events(archive)
    outcomes = {e.outcome for e in events}
    assert outcomes == {"success", "failure"}
    for event in events:
        assert event.agent == "a"
        assert event.linked_object in {p1.proposal_id, p2.proposal_id}
        # The audit trail carries counts and closed-vocabulary labels, never a
        # record id or the sentinel content of any seeded record.
        assert _SENTINEL_TITLE not in event.detail


def test_log_never_contains_a_record_id(tmp_path: Path) -> None:
    archive = _archive(tmp_path, k_floor=2)
    _seed(archive, n=5, year="2020", subject="eviction")
    enclave = ReadingRoomEnclave(archive)
    prop = enclave.propose(AggregateQuery(dimension="year", reason="r"), proposer="a", now=_NOW)
    enclave.execute(prop.proposal_id, actor="a", now=_NOW)

    record_ids = {p.stem for p in archive.records_dir.glob("*.json")}
    assert len(record_ids) == 5
    raw_log = (archive.logs_dir / "reading-room-queries.premis.json").read_text()
    for rid in record_ids:
        assert rid not in raw_log


# --- the manifest/history files are steward-side bookkeeping, not a read path ---


def test_manifest_and_history_files_round_trip_across_enclave_instances(tmp_path: Path) -> None:
    """A fresh `ReadingRoomEnclave(archive)` must see prior proposals/history.

    This is what makes the propose/approve/execute steps usable as three separate
    CLI invocations (three separate processes) rather than requiring one Python
    object to live across all three.
    """
    archive = _archive(tmp_path, k_floor=2)
    _seed(archive, n=5, year="2020", subject="eviction")
    prop = ReadingRoomEnclave(archive).propose(
        AggregateQuery(dimension="year", reason="r"), proposer="a", now=_NOW
    )
    # A brand-new enclave instance, as a second CLI invocation would construct.
    result = ReadingRoomEnclave(archive).execute(prop.proposal_id, actor="a", now=_NOW)
    assert result.total == 5

    manifest_path = archive.logs_dir / "reading-room-manifests.json"
    history_path = archive.logs_dir / "reading-room-history.json"
    assert json.loads(manifest_path.read_text())[prop.proposal_id]["dimension"] == "year"
    assert len(json.loads(history_path.read_text())) == 1


# --- CLI wiring: query-propose / approve, exactly as a steward would run it -----


def _cfg_path(root: Path) -> Path:
    return root / "store" / "config.json"


def test_cli_query_propose_needs_a_second_distinct_steward_to_answer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from ledger import cli

    root = tmp_path / "arc"
    assert cli.main(["init", "--root", str(root), "--name", "CLI Enclave Test"]) == 0
    cfg_path = _cfg_path(root)
    cfg = json.loads(cfg_path.read_text())
    cfg["dual_control_threshold"] = 2
    cfg["reading_room_k_floor"] = 2
    cfg_path.write_text(json.dumps(cfg))
    capsys.readouterr()

    assert (
        cli.main(
            [
                "query-propose",
                "--root",
                str(root),
                "--dimension",
                "type",
                "--actor",
                "steward-a",
                "--reason",
                "D1 historian research request",
                "--now",
                _NOW,
            ]
        )
        == 0
    )
    propose_out = capsys.readouterr().out
    assert "PROPOSED" in propose_out
    proposal_id = propose_out.split("proposal ")[1].split(";")[0]
    # Not yet answered: only one steward has acted.
    assert "answered" not in propose_out

    assert (
        cli.main(
            [
                "approve",
                "--root",
                str(root),
                "--id",
                proposal_id,
                "--actor",
                "steward-b",
                "--now",
                _NOW,
            ]
        )
        == 0
    )
    approve_out = capsys.readouterr().out
    assert "answered" in approve_out
    assert "suppressed" in approve_out or "total=" in approve_out


def test_cli_generic_propose_rejects_aggregate_query_action(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`ledger propose --action aggregate-query` is refused: it has no query text.

    A steward must use `ledger query-propose`, which files both the dual-control
    proposal and the query manifest together — the generic `propose --id` has no
    field for a structured query. Argparse's own ``choices=`` rejects the action
    before `_cmd_propose` even runs (an unknown-flag exit, not a `LedgerError`).
    """
    from ledger import cli

    root = tmp_path / "arc"
    assert cli.main(["init", "--root", str(root), "--name", "CLI Enclave Test 2"]) == 0
    capsys.readouterr()
    with pytest.raises(SystemExit) as exc_info:
        cli.main(
            [
                "propose",
                "--root",
                str(root),
                "--action",
                "aggregate-query",
                "--id",
                "whatever",
                "--actor",
                "a",
                "--reason",
                "r",
            ]
        )
    assert exc_info.value.code != 0
    assert "invalid choice" in capsys.readouterr().err
