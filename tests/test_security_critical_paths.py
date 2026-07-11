"""Edge-path tests for the security-critical access/consent/dual-control modules.

These pin the failure and refusal branches of the modules that carry the
per-module coverage floor (CODE-QUALITY-STANDARD: security/crypto-critical paths
hold >=95% branch coverage, above the 85% baseline). The floor is enforced by the
scoped ``coverage report --fail-under=95`` step in the Makefile ``cov`` target and
in CI, over ``src/ledger/access/*``, ``consent.py``, and ``dualcontrol.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from ledger.access.grants import community_member, load_grants, steward
from ledger.access.policy import is_visible, withheld_reason
from ledger.consent import ConsentRequest, ConsentRequestStore
from ledger.dualcontrol import ActionProposal, ProposalStore
from ledger.errors import LedgerError
from ledger.models import AccessPolicy

_NOW = "2026-01-01T00:00:00Z"


# --- access policy: deny-by-default and withheld labels -----------------------


@pytest.mark.disclosure
def test_sealed_conditional_needs_the_named_condition() -> None:
    grant = community_member("member-1")
    assert not is_visible(AccessPolicy.SEALED_CONDITIONAL, grant, _NOW)
    assert not is_visible(
        AccessPolicy.SEALED_CONDITIONAL,
        grant,
        _NOW,
        unseal_condition="estate-settled",
        conditions_met=frozenset({"other"}),
    )
    assert is_visible(
        AccessPolicy.SEALED_CONDITIONAL,
        grant,
        _NOW,
        unseal_condition="estate-settled",
        conditions_met=frozenset({"estate-settled"}),
    )


@pytest.mark.disclosure
def test_absolute_seal_binds_even_stewards() -> None:
    assert not is_visible(AccessPolicy.SEALED, steward("stew-1"), _NOW)


@pytest.mark.disclosure
def test_withheld_reason_covers_every_tier() -> None:
    assert withheld_reason(AccessPolicy.SEALED_UNTIL, None) == "sealed (no opening date set)"
    assert withheld_reason(AccessPolicy.SEALED_CONDITIONAL, None) == (
        "sealed until a condition is met"
    )
    assert withheld_reason(AccessPolicy.SEALED, None) == (
        "sealed from everyone, including stewards"
    )
    # An unrecognized policy value falls through to the safe generic label.
    assert withheld_reason(cast(AccessPolicy, "not-a-policy"), None) == "restricted"


@pytest.mark.disclosure
def test_embargo_countdown_phrasing() -> None:
    # Opens today (date reached at the instant of asking).
    assert withheld_reason(AccessPolicy.SEALED_UNTIL, "2026-01-01", now=_NOW) == (
        "sealed until 2026-01-01 (opens today)"
    )
    # Opens tomorrow; a naive ``now`` is normalized to UTC rather than crashing.
    assert (
        withheld_reason(AccessPolicy.SEALED_UNTIL, "2026-01-02", now="2026-01-01T00:00:00")
        == "sealed until 2026-01-02 (opens tomorrow)"
    )
    assert withheld_reason(AccessPolicy.SEALED_UNTIL, "2026-01-11", now=_NOW) == (
        "sealed until 2026-01-11 (opens in 10 days)"
    )
    # An unparseable date yields no countdown -- fail closed on the label too.
    assert withheld_reason(AccessPolicy.SEALED_UNTIL, "not-a-date", now=_NOW) == (
        "sealed until not-a-date"
    )


# --- grants: absence of a grant file means no one is privileged ---------------


@pytest.mark.disclosure
def test_missing_grant_file_grants_nothing(tmp_path: Path) -> None:
    assert load_grants(tmp_path / "no-such-grants.json") == {}


# --- consent store: corrupt queues fail loudly, writes fail atomically --------


@pytest.mark.disclosure
def test_consent_store_rejects_non_mapping_entries(tmp_path: Path) -> None:
    path = tmp_path / "consent.json"
    path.write_text(json.dumps([42]), encoding="utf-8")
    with pytest.raises(LedgerError, match="mappings"):
        ConsentRequestStore(path).all()


@pytest.mark.disclosure
def test_consent_store_write_failure_raises_without_leaking(tmp_path: Path) -> None:
    # A read-only queue directory makes the temp-file write fail; the error names
    # the store path, never the message content.
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    queue_dir.chmod(0o500)
    try:
        store = ConsentRequestStore(queue_dir / "consent.json")
        with pytest.raises(LedgerError, match="could not be written") as excinfo:
            store.add(ConsentRequest(record_id="rec-1", kind="withdraw", message="private ask"))
        assert "private ask" not in str(excinfo.value)
    finally:
        queue_dir.chmod(0o700)


# --- dual control: refusal paths and durable-store robustness -----------------


@pytest.mark.disclosure
def test_unknown_proposal_status_is_rejected() -> None:
    with pytest.raises(LedgerError, match="status"):
        ActionProposal(action="takedown", target="rec-1", reason="r", proposer="A", status="odd")


@pytest.mark.disclosure
def test_store_lists_and_fetches_proposals(tmp_path: Path) -> None:
    store = ProposalStore(tmp_path / "proposals.json")
    first = store.add(ActionProposal(action="takedown", target="rec-1", reason="r", proposer="A"))
    second = store.add(ActionProposal(action="publish", target="rec-2", reason="r", proposer="B"))
    assert [p.proposal_id for p in store.all()] == [first.proposal_id, second.proposal_id]
    store.mark(first.proposal_id, "executed")
    assert [p.proposal_id for p in store.open_proposals()] == [second.proposal_id]
    fetched = store.get(second.proposal_id)
    assert fetched is not None
    assert fetched.action == "publish"
    assert store.get("no-such-id") is None


@pytest.mark.disclosure
def test_approve_and_mark_skip_non_matching_proposals(tmp_path: Path) -> None:
    store = ProposalStore(tmp_path / "proposals.json")
    store.add(ActionProposal(action="takedown", target="rec-1", reason="r", proposer="A"))
    second = store.add(ActionProposal(action="unseal", target="ref-2", reason="r", proposer="B"))
    approved = store.approve(second.proposal_id, "C")
    assert approved.approved_count() == 2
    store.mark(second.proposal_id, "cancelled")
    fetched = store.get(second.proposal_id)
    assert fetched is not None
    assert fetched.status == "cancelled"


@pytest.mark.disclosure
def test_approve_unknown_proposal_raises(tmp_path: Path) -> None:
    store = ProposalStore(tmp_path / "proposals.json")
    with pytest.raises(LedgerError, match="no proposal"):
        store.approve("missing", "A")


@pytest.mark.disclosure
def test_mark_rejects_unknown_status_and_unknown_id(tmp_path: Path) -> None:
    store = ProposalStore(tmp_path / "proposals.json")
    p = store.add(ActionProposal(action="takedown", target="rec-1", reason="r", proposer="A"))
    with pytest.raises(LedgerError, match="unknown proposal status"):
        store.mark(p.proposal_id, "odd")
    with pytest.raises(LedgerError, match="no proposal"):
        store.mark("missing", "executed")


@pytest.mark.disclosure
def test_corrupt_proposal_file_reads_as_empty(tmp_path: Path) -> None:
    path = tmp_path / "proposals.json"
    path.write_text("not json at all", encoding="utf-8")
    assert ProposalStore(path).all() == []
    path.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
    assert ProposalStore(path).all() == []
