"""Cost controls enforced BEFORE any model call (mission requirement)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ledger.ai.limits import (
    AIDailyCapExceeded,
    AIRateLimitError,
    AISpendStateUnreadable,
    RateLimitConfig,
    RateLimiter,
)
from ledger.errors import LedgerError

pytestmark = pytest.mark.disclosure


def test_per_client_rate_limit_blocks_the_next_call(tmp_path: Path) -> None:
    limiter = RateLimiter(
        RateLimitConfig(per_client_per_minute=2, daily_cap=100), tmp_path / "u.json"
    )
    limiter.check_and_record("alice", now=1000.0)
    limiter.check_and_record("alice", now=1001.0)
    with pytest.raises(AIRateLimitError):
        limiter.check_and_record("alice", now=1002.0)


def test_rate_limit_is_per_subject(tmp_path: Path) -> None:
    limiter = RateLimiter(
        RateLimitConfig(per_client_per_minute=1, daily_cap=100), tmp_path / "u.json"
    )
    limiter.check_and_record("alice", now=1000.0)
    # bob is unaffected by alice's usage.
    limiter.check_and_record("bob", now=1000.5)


def test_rate_limit_window_slides(tmp_path: Path) -> None:
    limiter = RateLimiter(
        RateLimitConfig(per_client_per_minute=1, daily_cap=100), tmp_path / "u.json"
    )
    limiter.check_and_record("alice", now=1000.0)
    with pytest.raises(AIRateLimitError):
        limiter.check_and_record("alice", now=1030.0)
    # 61 seconds later the first request has aged out of the window.
    limiter.check_and_record("alice", now=1061.0)


def test_daily_cap_blocks_across_subjects(tmp_path: Path) -> None:
    limiter = RateLimiter(
        RateLimitConfig(per_client_per_minute=100, daily_cap=2), tmp_path / "u.json"
    )
    limiter.check_and_record("alice", now=1000.0)
    limiter.check_and_record("bob", now=1001.0)
    with pytest.raises(AIDailyCapExceeded):
        limiter.check_and_record("carol", now=1002.0)


def test_daily_cap_persists_across_instances(tmp_path: Path) -> None:
    """The daily cap is cross-process (a fresh `RateLimiter` reads the same state)."""
    state = tmp_path / "u.json"
    RateLimiter(RateLimitConfig(per_client_per_minute=100, daily_cap=1), state).check_and_record(
        "alice", now=1000.0
    )
    fresh = RateLimiter(RateLimitConfig(per_client_per_minute=100, daily_cap=1), state)
    with pytest.raises(AIDailyCapExceeded):
        fresh.check_and_record("bob", now=1001.0)


def test_daily_cap_resets_on_a_new_utc_day(tmp_path: Path) -> None:
    limiter = RateLimiter(
        RateLimitConfig(per_client_per_minute=100, daily_cap=1), tmp_path / "u.json"
    )
    limiter.check_and_record("alice", now=1_700_000_000.0)
    # ~24 hours later, a new UTC calendar date.
    limiter.check_and_record("alice", now=1_700_000_000.0 + 90_000)


def test_a_refused_request_does_not_consume_a_rate_limit_slot(tmp_path: Path) -> None:
    """A request that fails the daily cap must not also burn the caller's
    per-minute allowance -- exceeding one limit is not a reason to also count
    against the other."""
    limiter = RateLimiter(
        RateLimitConfig(per_client_per_minute=5, daily_cap=1), tmp_path / "u.json"
    )
    limiter.check_and_record("alice", now=1000.0)
    with pytest.raises(AIDailyCapExceeded):
        limiter.check_and_record("alice", now=1001.0)
    assert limiter.usage_today(now=1001.0) == 1


def test_usage_today_reports_zero_before_any_call(tmp_path: Path) -> None:
    limiter = RateLimiter(RateLimitConfig(), tmp_path / "u.json")
    assert limiter.usage_today(now=1000.0) == 0


@pytest.mark.parametrize(
    "config",
    [RateLimitConfig(per_client_per_minute=0), RateLimitConfig(daily_cap=0)],
)
def test_invalid_config_is_rejected(tmp_path: Path, config: RateLimitConfig) -> None:
    with pytest.raises(LedgerError):
        RateLimiter(config, tmp_path / "u.json")


# --- the counter fails closed on damage, not open (#152 re-triage) -----------
#
# A damaged spend counter read as `{}` reports "no requests today" and restores
# the entire archive-wide daily budget, and the next `check_and_record` writes
# that zero back and makes it true. Absence and damage are different facts.


@pytest.mark.parametrize(
    "contents",
    [
        "not json at all",
        '["a", "list"]',
        '{"2026-09-01": "12"}',
        '{"2026-09-01": true}',
        '{"2026-09-01": -3}',
    ],
    ids=["unparseable", "not-an-object", "string-count", "boolean-count", "negative-count"],
)
def test_a_damaged_spend_counter_refuses_rather_than_restoring_the_budget(
    tmp_path: Path, contents: str
) -> None:
    state = tmp_path / "ai-usage.json"
    state.write_text(contents, encoding="utf-8")
    limiter = RateLimiter(RateLimitConfig(per_client_per_minute=5, daily_cap=200), state)
    with pytest.raises(AISpendStateUnreadable):
        limiter.check_and_record("someone")
    with pytest.raises(AISpendStateUnreadable):
        limiter.usage_today()


def test_a_damaged_counter_is_not_overwritten_by_the_refused_call(tmp_path: Path) -> None:
    """The second half of the bug: reading damage as empty is only fatal because
    the next write turns it into the truth. Refusing must also leave the damaged
    file intact, so a steward can still recover what it held."""
    state = tmp_path / "ai-usage.json"
    state.write_text('{"2026-09-01": "12"}', encoding="utf-8")
    limiter = RateLimiter(RateLimitConfig(per_client_per_minute=5, daily_cap=200), state)
    with pytest.raises(AISpendStateUnreadable):
        limiter.check_and_record("someone")
    assert state.read_text(encoding="utf-8") == '{"2026-09-01": "12"}'


def test_an_absent_counter_is_still_an_unspent_budget(tmp_path: Path) -> None:
    """The positive control: absence is not damage, and must not be refused."""
    state = tmp_path / "never-written.json"
    limiter = RateLimiter(RateLimitConfig(per_client_per_minute=5, daily_cap=200), state)
    assert limiter.usage_today() == 0
    limiter.check_and_record("someone")
    assert limiter.usage_today() == 1


def test_an_unreadable_counter_refuses(tmp_path: Path) -> None:
    """A directory where the counter should be: `read_text` raises `OSError`."""
    state = tmp_path / "ai-usage.json"
    state.mkdir()
    limiter = RateLimiter(RateLimitConfig(per_client_per_minute=5, daily_cap=200), state)
    with pytest.raises(AISpendStateUnreadable):
        limiter.usage_today()
