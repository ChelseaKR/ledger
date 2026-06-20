"""Tests for dual-control: propose → approve → execute (backlog D1).

When ``config.dual_control_threshold`` is above 1, the most dangerous actions
(takedown, identity-unseal, publish-to-public) must be approved by that many
*distinct* stewards before they run, so no one steward can act alone. These tests
pin the proposal mechanism and the end-to-end CLI loop, including that one steward
cannot satisfy a threshold of two by approving twice.
"""

from __future__ import annotations

import glob
import json
from pathlib import Path

import pytest

from ledger import cli
from ledger.dualcontrol import ActionProposal, ProposalStore
from ledger.errors import LedgerError

_KEY = "0123456789abcdef0123456789abcdef0123456789a="
_NOW = "2026-01-01T00:00:00Z"


# --- unit: the proposal & store ---------------------------------------------


@pytest.mark.disclosure
def test_proposer_counts_as_first_approval() -> None:
    p = ActionProposal(action="takedown", target="rec-1", reason="r", proposer="A")
    assert p.approved_count() == 1
    assert p.is_ready(1)
    assert not p.is_ready(2)


@pytest.mark.disclosure
def test_distinct_approvals_reach_threshold(tmp_path: Path) -> None:
    store = ProposalStore(tmp_path / "proposals.json")
    p = store.add(ActionProposal(action="takedown", target="rec-1", reason="r", proposer="A"))
    # Same steward approving again does not double-count.
    p = store.approve(p.proposal_id, "A")
    assert p.approved_count() == 1
    assert not p.is_ready(2)
    # A distinct steward does.
    p = store.approve(p.proposal_id, "B")
    assert p.approved_count() == 2
    assert p.is_ready(2)


@pytest.mark.disclosure
def test_unknown_action_is_rejected() -> None:
    with pytest.raises(LedgerError):
        ActionProposal(action="nuke", target="rec-1", reason="r", proposer="A")


@pytest.mark.disclosure
def test_approving_a_closed_proposal_raises(tmp_path: Path) -> None:
    store = ProposalStore(tmp_path / "proposals.json")
    p = store.add(ActionProposal(action="publish", target="rec-1", reason="r", proposer="A"))
    store.mark(p.proposal_id, "executed")
    with pytest.raises(LedgerError):
        store.approve(p.proposal_id, "B")


# --- integration: the CLI loop ----------------------------------------------


def _init_archive(tmp_path: Path, *, threshold: int) -> tuple[Path, str]:
    root = tmp_path / "arc"
    assert cli.main(["init", "--root", str(root), "--name", "DC"]) == 0
    cfg_path = root / "store" / "config.json"
    cfg = json.loads(cfg_path.read_text())
    cfg["dual_control_threshold"] = threshold
    cfg_path.write_text(json.dumps(cfg))
    assert (
        cli.main(
            [
                "ingest",
                "--root",
                str(root),
                "--title",
                "Rec",
                "--public-field",
                "s=x",
                "--actor",
                "s",
                "--now",
                _NOW,
            ]
        )
        == 0
    )
    rid = Path(glob.glob(str(root / "store" / "records" / "*.json"))[0]).stem
    return root, rid


def _proposals(root: Path) -> list[dict]:
    path = root / "store" / "logs" / "proposals.json"
    return json.loads(path.read_text()) if path.exists() else []


@pytest.mark.disclosure
def test_takedown_under_threshold_one_executes_immediately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LEDGER_VAULT_KEY", _KEY)
    root, rid = _init_archive(tmp_path, threshold=1)
    assert (
        cli.main(["takedown", "--root", str(root), "--id", rid, "--actor", "A", "--reason", "x"])
        == 0
    )
    assert not (root / "store" / "records" / f"{rid}.json").exists()  # gone immediately
    assert _proposals(root) == []  # no proposal needed


@pytest.mark.disclosure
def test_takedown_under_threshold_two_needs_two_stewards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LEDGER_VAULT_KEY", _KEY)
    root, rid = _init_archive(tmp_path, threshold=2)

    # First steward's takedown only PROPOSES; the record stays.
    assert (
        cli.main(["takedown", "--root", str(root), "--id", rid, "--actor", "A", "--reason", "x"])
        == 0
    )
    assert (root / "store" / "records" / f"{rid}.json").exists()
    props = _proposals(root)
    assert len(props) == 1 and props[0]["status"] == "open"
    pid = props[0]["proposal_id"]

    # The same steward approving again does NOT execute it.
    assert cli.main(["approve", "--root", str(root), "--id", pid, "--actor", "A"]) == 0
    assert (root / "store" / "records" / f"{rid}.json").exists()

    # A second, distinct steward's approval executes the takedown.
    assert cli.main(["approve", "--root", str(root), "--id", pid, "--actor", "B"]) == 0
    assert not (root / "store" / "records" / f"{rid}.json").exists()
    assert _proposals(root)[0]["status"] == "executed"


@pytest.mark.disclosure
def test_publish_proposal_opens_record_when_approved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LEDGER_VAULT_KEY", _KEY)
    root, rid = _init_archive(tmp_path, threshold=2)
    # Tighten the record so "publish" is a meaningful change.
    assert (
        cli.main(
            [
                "policy",
                "--root",
                str(root),
                "--id",
                rid,
                "--level",
                "stewards",
                "--actor",
                "A",
                "--reason",
                "hold",
            ]
        )
        == 0
    )
    # Propose publish; one approval is not enough, a second executes it.
    assert (
        cli.main(
            [
                "propose",
                "--root",
                str(root),
                "--action",
                "publish",
                "--id",
                rid,
                "--actor",
                "A",
                "--reason",
                "approved",
            ]
        )
        == 0
    )
    pid = _proposals(root)[0]["proposal_id"]
    assert cli.main(["approve", "--root", str(root), "--id", pid, "--actor", "B"]) == 0
    # The record's default policy is now public.
    from ledger.config import Config
    from ledger.ingest import Archive

    archive = Archive(Config.load(root / "store" / "config.json"))
    from ledger.models import AccessPolicy

    assert archive.get(rid).default_policy is AccessPolicy.PUBLIC
    assert _proposals(root)[0]["status"] == "executed"


@pytest.mark.disclosure
def test_unseal_proposal_records_authorization_without_printing_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An approved unseal authorizes (records) but the CLI never prints an identity."""
    monkeypatch.setenv("LEDGER_VAULT_KEY", _KEY)
    root, rid = _init_archive(tmp_path, threshold=2)
    assert (
        cli.main(
            [
                "propose",
                "--root",
                str(root),
                "--action",
                "unseal",
                "--id",
                rid,
                "--actor",
                "A",
                "--reason",
                "court order",
            ]
        )
        == 0
    )
    pid = _proposals(root)[0]["proposal_id"]
    assert cli.main(["approve", "--root", str(root), "--id", pid, "--actor", "B"]) == 0
    out = capsys.readouterr().out
    assert "authorized" in out
    # The CLI never emits an identity; it points at the audited grant path.
    assert "identity_unseal grant" in out
