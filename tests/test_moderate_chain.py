"""Tests for :class:`ledger.moderate.ModerationLog`'s hash chain (FIX-06).

Mirrors ``tests/test_metadata.py``'s PREMIS chain coverage for the moderation
log: the two logs share the same :mod:`ledger.chain` machinery, so the same
tamper-evidence contract must hold for accountable moderation decisions
(warnings, takedowns, consent changes) as it does for preservation events.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ledger.chain import GENESIS_HASH
from ledger.moderate import ModerationAction, ModerationLog


def _sample_actions() -> list[ModerationAction]:
    return [
        ModerationAction(
            action="warn",
            actor="steward-1",
            reason="flagged for review",
            target_record="rec-0000000000000000",
            action_id="action-0001",
            at="2026-01-01T00:00:00Z",
        ),
        ModerationAction(
            action="takedown",
            actor="steward-2",
            reason="contributor withdrew consent",
            target_record="rec-0000000000000000",
            action_id="action-0002",
            at="2026-01-02T00:00:00Z",
        ),
    ]


@pytest.mark.preservation
def test_empty_moderation_log_head_is_genesis() -> None:
    assert ModerationLog().head == GENESIS_HASH


@pytest.mark.preservation
def test_moderation_log_head_changes_as_actions_are_recorded() -> None:
    log = ModerationLog()
    heads = [log.head]
    for action in _sample_actions():
        log.record(action)
        heads.append(log.head)
    assert len(set(heads)) == len(heads)


@pytest.mark.preservation
def test_moderation_log_verify_chain_ok_on_untouched_log() -> None:
    log = ModerationLog()
    for action in _sample_actions():
        log.record(action)
    result = log.verify_chain()
    assert result.ok
    assert result.broken_at is None
    assert result.head == log.head


@pytest.mark.preservation
def test_moderation_log_json_round_trip_preserves_chain(tmp_path: Path) -> None:
    log = ModerationLog()
    for action in _sample_actions():
        log.record(action)
    path = tmp_path / "moderation.json"
    log.write(path)
    restored = ModerationLog.read(path)
    assert restored.actions == log.actions
    assert restored.head == log.head
    assert restored.verify_chain().ok


@pytest.mark.preservation
def test_editing_a_moderation_entry_on_disk_breaks_the_chain(tmp_path: Path) -> None:
    """A steward-with-disk-access edit to a past decision is caught on read."""
    log = ModerationLog()
    for action in _sample_actions():
        log.record(action)
    path = tmp_path / "moderation.json"
    log.write(path)

    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["entries"][0]["reason"] = "quietly rewritten"
    path.write_text(json.dumps(raw), encoding="utf-8")

    tampered = ModerationLog.read(path)
    result = tampered.verify_chain()
    assert not result.ok


@pytest.mark.preservation
def test_legacy_bare_array_moderation_log_migrates_and_verifies() -> None:
    """A pre-FIX-06 log (bare array, no ``prevHash``) still loads and chains."""
    actions = _sample_actions()
    legacy_json = json.dumps([a.to_dict() for a in actions])
    migrated = ModerationLog.from_json(legacy_json)
    assert migrated.actions == actions
    assert migrated.verify_chain().ok
    assert migrated.head != GENESIS_HASH
