"""Cost controls, enforced BEFORE any model call (mission requirement).

Two independent limits, checked in order (cheapest/most-local first):

1. an in-process sliding-window per-subject rate — no disk I/O, resets on
   restart. Deliberately approximate rather than exact: the goal is "no one
   client can hammer the model", not perfectly precise accounting.
2. a persisted, cross-process daily cap keyed by UTC calendar date, so the
   whole archive cannot exceed a fixed daily spend regardless of how many
   server workers or CLI invocations are running. Read-modify-write happens
   under :func:`ledger._filelock.file_lock`, the same advisory lock every
   other JSON store in this repo uses for its critical section.

Exceeding *either* raises, and every caller's contract is exactly what a
provider's HTTP 429 would be: refuse the AI call, leave the deterministic
preservation/access/browse path completely untouched. Nothing here is
reachable from any non-AI code path.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

from ledger._filelock import file_lock
from ledger.errors import LedgerError

__all__ = [
    "AIDailyCapExceeded",
    "AIRateLimitError",
    "AISpendStateUnreadable",
    "RateLimitConfig",
    "RateLimiter",
]


class AIRateLimitError(LedgerError):
    """A per-client request rate was exceeded.

    Callers must treat this exactly like a provider 429: refuse the AI
    feature for this request, leave everything else untouched.
    """


class AIDailyCapExceeded(LedgerError):
    """The archive-wide daily AI request cap was reached.

    Same fallback contract as :class:`AIRateLimitError`.
    """


class AISpendStateUnreadable(LedgerError):
    """The persisted daily-spend counter exists but could not be read.

    Distinct from "the file is absent", which genuinely means no spend has
    been recorded today. A damaged counter read as ``{}`` would report zero
    requests so far and silently reset the archive-wide daily cap — the whole
    budget, restored by a corrupt file — and the very next call would write
    that zero back and make it true. Every other JSON store in this repo
    stopped doing that (#154); a *budget* is the last place it would be
    acceptable, so this raises and the caller refuses the AI request exactly
    as it would for a provider outage (fail closed).
    """


@dataclass(frozen=True)
class RateLimitConfig:
    """The two thresholds :class:`RateLimiter` enforces."""

    per_client_per_minute: int = 5
    daily_cap: int = 200

    def validate(self) -> None:
        if self.per_client_per_minute < 1:
            raise LedgerError("per_client_per_minute must be at least 1")
        if self.daily_cap < 1:
            raise LedgerError("daily_cap must be at least 1")


class RateLimiter:
    """Enforces :class:`RateLimitConfig` for the AI layer only.

    ``state_path`` is a small JSON counter file the caller places outside the
    archive's bags/records tree (it is bookkeeping, never preservation
    content, and is never part of a bag or a disclosed record).
    """

    def __init__(self, config: RateLimitConfig, state_path: Path) -> None:
        config.validate()
        self._config = config
        self._state_path = Path(state_path)
        self._lock = Lock()
        self._windows: dict[str, list[float]] = {}

    def check_and_record(self, subject: str, *, now: float | None = None) -> None:
        """Raise if ``subject`` is over either limit; otherwise record this request.

        The per-minute check runs first (cheap, in-memory) and, on a pass,
        recording is deferred until *after* the daily-cap check also passes —
        a request that is about to be refused never consumes a rate-limit
        slot.
        """
        instant = time.time() if now is None else now
        with self._lock:
            window = self._windows.setdefault(subject, [])
            cutoff = instant - 60.0
            while window and window[0] < cutoff:
                window.pop(0)
            if len(window) >= self._config.per_client_per_minute:
                raise AIRateLimitError(
                    f"AI rate limit exceeded for {subject!r}: max "
                    f"{self._config.per_client_per_minute} requests/minute per client"
                )
        self._check_daily_cap(instant)
        with self._lock:
            self._windows.setdefault(subject, []).append(instant)

    def usage_today(self, *, now: float | None = None) -> int:
        """Today's recorded count, for a status view.

        Raises :class:`AISpendStateUnreadable` rather than returning ``0`` for a
        damaged counter: a status view that says "0 requests today" when the
        truth is unknown is worse than one that says it cannot tell.
        """
        instant = time.time() if now is None else now
        return self._read().get(self._today(instant), 0)

    @staticmethod
    def _today(instant: float) -> str:
        return datetime.fromtimestamp(instant, tz=UTC).strftime("%Y-%m-%d")

    def _check_daily_cap(self, instant: float) -> None:
        today = self._today(instant)
        with file_lock(self._state_path):
            counts = self._read()
            count = counts.get(today, 0)
            if count >= self._config.daily_cap:
                raise AIDailyCapExceeded(
                    f"AI daily request cap of {self._config.daily_cap} reached for {today}"
                )
            counts[today] = count + 1
            # Keep the file small: entries older than two days are noise.
            cutoff_day = self._today(instant - 172800)
            trimmed = {day: value for day, value in counts.items() if day >= cutoff_day}
            self._write(trimmed)

    def _read(self) -> dict[str, int]:
        """Today's counts, or ``{}`` for a counter that has genuinely never been written.

        Absence and damage are different facts and are kept apart here. Every
        failure mode below leaves by the same door,
        :class:`AISpendStateUnreadable`, so a caller catches one type and can
        never mistake a corrupt budget file for an unspent budget.
        """
        try:
            text = self._state_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise AISpendStateUnreadable(
                f"AI daily-spend counter could not be read: {self._state_path}"
            ) from exc
        try:
            data = json.loads(text)
        except ValueError as exc:  # JSONDecodeError is a ValueError
            raise AISpendStateUnreadable(
                f"AI daily-spend counter is not valid JSON: {self._state_path}"
            ) from exc
        if not isinstance(data, dict):
            raise AISpendStateUnreadable(
                f"AI daily-spend counter {self._state_path} must contain a JSON object"
            )
        counts: dict[str, int] = {}
        for key, value in data.items():
            # `type(value) is int` rather than `isinstance`: `True` is an `int`,
            # and a boolean posing as a count would be silently read as 1.
            # Dropping an entry that fails this check would be the same defect
            # in quieter clothing -- today's count would read as 0 -- so it raises.
            if type(value) is not int or value < 0:
                raise AISpendStateUnreadable(
                    f"AI daily-spend counter {self._state_path} has a non-integer or "
                    f"negative count for {key!r}; refusing rather than reading it as no spend"
                )
            counts[str(key)] = value
        return counts

    def _write(self, counts: dict[str, int]) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._state_path.with_name(f"{self._state_path.name}.tmp")
        tmp.write_text(json.dumps(counts, sort_keys=True), encoding="utf-8")
        tmp.replace(self._state_path)
