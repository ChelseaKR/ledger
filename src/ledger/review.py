"""The steward review queue for contributor submissions.

A record submitted through the public ``/contribute`` form lands *sealed-pending*:
it is stored and sealed, but nothing is listable until a steward reviews it
(Hard Rule 2 — narrowest disclosure, nothing published by inaction). This module is
the small, durable queue that tells a steward which records are awaiting that
review, so the decision happens in the accountable console rather than only at a CLI.

No-outing: a queue entry carries an opaque ``record_id`` and a submission timestamp
— never a title, an account body, a contributor name, or any sealed value. The
title a steward sees in the console is read from the disclosure-gated record at
render time, not stored here. The store is append/remove only and written atomically
(temp file + ``os.replace``), so a reader never sees a half-written queue.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

__all__ = ["PendingSubmission", "SubmissionQueue"]


@dataclass(frozen=True)
class PendingSubmission:
    """One record awaiting steward review: an opaque id and when it was submitted."""

    record_id: str
    submitted_at: str

    def to_dict(self) -> dict[str, str]:
        return {"record_id": self.record_id, "submitted_at": self.submitted_at}

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> PendingSubmission:
        return cls(
            record_id=str(data.get("record_id", "")),
            submitted_at=str(data.get("submitted_at", "")),
        )


class SubmissionQueue:
    """A durable, identity-free queue of records awaiting steward review."""

    def __init__(self, path: Path) -> None:
        """Bind the queue to its on-disk JSON ``path`` (created on first write)."""
        self._path = Path(path)

    def pending(self) -> list[PendingSubmission]:
        """Every record awaiting review, in submission order (oldest first)."""
        return self._read()

    def add(self, record_id: str, *, now: str) -> None:
        """Enqueue ``record_id`` for review; idempotent (a re-add is a no-op)."""
        items = self._read()
        if any(item.record_id == record_id for item in items):
            return
        items.append(PendingSubmission(record_id=record_id, submitted_at=now))
        self._write(items)

    def remove(self, record_id: str) -> None:
        """Drop ``record_id`` from the queue once a steward has decided. Idempotent."""
        items = [item for item in self._read() if item.record_id != record_id]
        self._write(items)

    def contains(self, record_id: str) -> bool:
        """Whether ``record_id`` is currently awaiting review."""
        return any(item.record_id == record_id for item in self._read())

    # --- persistence --------------------------------------------------------

    def _read(self) -> list[PendingSubmission]:
        if not self._path.exists():
            return []
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(raw, list):
            return []
        return [PendingSubmission.from_dict(item) for item in raw if isinstance(item, dict)]

    def _write(self, items: list[PendingSubmission]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps([item.to_dict() for item in items], ensure_ascii=False, indent=2)
        tmp = self._path.with_name(f"{self._path.name}.{os.getpid()}.tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, self._path)
