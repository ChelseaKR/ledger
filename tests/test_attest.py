"""Tests for the condition-attestation workflow for ``SEALED_CONDITIONAL`` (FIX-07).

The ``SEALED_CONDITIONAL`` tier promises "open when a condition is met" — but until
FIX-07 nothing populated the ``conditions_met`` set the access layer consults, so the
tier silently degraded to steward-only. These tests pin the missing machinery:

* a field sealed on ``death-of-contributor`` is withheld from a non-steward reader;
* one steward proposing the attestation alone changes *nothing* (2-of-N — no single
  steward can declare a contributor dead and spring their record open);
* a second, distinct steward's approval records the condition and the field discloses;
* the whole chain is in the PREMIS log as a ``POLICY_CHANGE`` event;
* a condition outside the config vocabulary is rejected at the boundary.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ledger import attest, cli
from ledger.access.grants import anonymous
from ledger.config import Config
from ledger.ingest import Archive
from ledger.models import (
    AccessPolicy,
    DublinCore,
    Field,
    PremisEventType,
    Record,
)

_NOW = "2026-01-01T00:00:00Z"
_CONDITION = "death-of-contributor"
_SEALED_NAME = "the sealed real name"


def _seed_archive(tmp_path: Path) -> tuple[Archive, str]:
    """Stand up an archive with one record whose ``real_name`` is sealed on a condition.

    The record's default policy is PUBLIC so a stranger may *list* it; the sealed
    field is what waits on the attested condition, isolating exactly the behaviour
    under test.
    """
    config = Config.default("Test Archive", tmp_path / "arc")
    archive = Archive.init(config)
    payload = tmp_path / "story.txt"
    payload.write_text("the public oral history\n", encoding="utf-8")
    record = Record(
        title="Oral history, 1989",
        record_id="rec-cond-000000000001",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(title=["Oral history, 1989"]),
        fields=[
            Field(name="story", value="the public account", policy=AccessPolicy.PUBLIC),
            Field(
                name="real_name",
                value=_SEALED_NAME,
                policy=AccessPolicy.SEALED_CONDITIONAL,
                unseal_condition=_CONDITION,
            ),
        ],
        payloads=[],
        content_warnings=[],
        identity_ref=None,
        created_at=_NOW,
    )
    archive.ingest({"story.txt": payload}, record, now=_NOW)
    return archive, record.record_id


@pytest.mark.disclosure
def test_sealed_conditional_opens_only_after_two_stewards_attest(tmp_path: Path) -> None:
    """End-to-end: sealed on death-of-contributor; 1 steward -> sealed, 2 -> discloses."""
    archive, record_id = _seed_archive(tmp_path)
    reader = anonymous()

    # Baseline: the conditional field is withheld from a non-steward reader.
    before = archive.disclose(record_id, reader, now=_NOW)
    assert "real_name" not in before.fields
    assert any(r.name == "real_name" for r in before.withheld)

    store = attest.AttestStore(archive.logs_dir)

    # One steward proposes the attestation. Alone, this changes NOTHING.
    prop = store.propose(_CONDITION, "steward-A", reason="obituary confirmed", now=_NOW)
    assert store.attested() == frozenset()
    still_sealed = archive.disclose(record_id, reader, now=_NOW)
    assert "real_name" not in still_sealed.fields

    # The same steward approving again does not reach the 2-of-N quorum.
    _, attested_now = store.approve(prop.proposal_id, "steward-A", now=_NOW)
    assert attested_now is False
    assert store.attested() == frozenset()
    assert "real_name" not in archive.disclose(record_id, reader, now=_NOW).fields

    # A second, DISTINCT steward's approval records the condition.
    _, attested_now = store.approve(prop.proposal_id, "steward-B", now=_NOW)
    assert attested_now is True
    assert _CONDITION in store.attested()

    # The field now discloses to the same non-steward reader — the seal has opened.
    after = archive.disclose(record_id, reader, now=_NOW)
    assert after.fields.get("real_name") == _SEALED_NAME

    # The whole chain is in the PREMIS log as a POLICY_CHANGE event.
    policy_events = [
        e for e in archive.audit_events() if e.event_type is PremisEventType.POLICY_CHANGE
    ]
    assert any(_CONDITION in e.detail for e in policy_events)
    # No-outing: the accountable event names the condition and stewards, never the
    # sealed value it unsealed.
    assert all(_SEALED_NAME not in e.detail for e in policy_events)


@pytest.mark.disclosure
def test_attested_set_survives_a_fresh_archive_handle(tmp_path: Path) -> None:
    """The attested set is durable: a new Archive over the same root still opens it."""
    archive, record_id = _seed_archive(tmp_path)
    store = attest.AttestStore(archive.logs_dir)
    prop = store.propose(_CONDITION, "steward-A", now=_NOW)
    store.approve(prop.proposal_id, "steward-B", now=_NOW)

    reopened = Archive(archive.config)
    assert _CONDITION in reopened.attested_conditions()
    assert reopened.disclose(record_id, anonymous(), now=_NOW).fields.get("real_name") == (
        _SEALED_NAME
    )


def test_record_attested_is_idempotent_across_retry(tmp_path: Path) -> None:
    """A retry after a partial workflow write never duplicates its PREMIS event."""
    logs_dir = tmp_path / "logs"
    store = attest.AttestStore(logs_dir)
    proposal = store.propose(_CONDITION, "steward-A", now=_NOW)
    proposal, attested_now = store.approve(proposal.proposal_id, "steward-B", now=_NOW)
    assert attested_now is True

    assert store._record_attested(proposal, agent="steward-B", now=_NOW) is False

    premis = attest.PremisLog.read(logs_dir / "attestations.premis.json")
    matching = [event for event in premis.events if _CONDITION in event.detail]
    assert len(matching) == 1


@pytest.mark.disclosure
def test_cli_attest_flow_and_invalid_condition_rejected(tmp_path: Path) -> None:
    """The CLI drives the same flow, and a condition outside the vocabulary is rejected."""
    root = tmp_path / "arc"
    assert cli.main(["init", "--root", str(root), "--name", "Test Archive"]) == 0

    # A condition outside config.conditions is rejected at the boundary (exit code 2).
    assert (
        cli.main(["attest", "propose", "--root", str(root), "not-a-real-condition", "--actor", "s"])
        == 2
    )

    # A valid condition proposes cleanly, then approves to quorum.
    assert (
        cli.main(
            [
                "attest",
                "propose",
                "--root",
                str(root),
                _CONDITION,
                "--actor",
                "steward-A",
                "--reason",
                "obituary confirmed",
                "--now",
                _NOW,
            ]
        )
        == 0
    )
    store = attest.AttestStore(Archive(Config.load(root / "store" / "config.json")).logs_dir)
    open_props = store.open_proposals()
    assert len(open_props) == 1
    assert store.attested() == frozenset()

    proposal_id = open_props[0].proposal_id
    assert (
        cli.main(
            [
                "attest",
                "approve",
                "--root",
                str(root),
                "--id",
                proposal_id,
                "--actor",
                "steward-B",
                "--now",
                _NOW,
            ]
        )
        == 0
    )
    assert _CONDITION in store.attested()
