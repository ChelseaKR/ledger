"""Durable takedown tombstones and per-location propagation receipts.

A takedown is a *decision plus a propagated effect* (GOVERNANCE §5). The decision
outlives the data in ``logs/takedowns.premis.json``; this module makes the *effect*
outlive a temporarily offline replica. When a steward removes a record while one
mirror is unreachable, the removal cannot be pushed there and then. A tombstone is
the small, durable "this id must not exist anywhere" marker that survives the gap:
when the offline replica reattaches, the replication sweep reads the pending
tombstone, deletes the stale copy it still holds, writes a per-location PREMIS
``TAKEDOWN`` receipt, and marks that location confirmed — so a copy can never quietly
resurrect and a contributor can be told honestly *which* locations have applied the
removal and which are still pending.

Retention note (deliberate, per the item's risk analysis): a tombstone is *retained
removal metadata*. It records that an opaque ``record_id`` was taken down and when
each location confirmed the removal — but nothing about *what* the record was. This
is the same trade every deletion-tracking system makes: to guarantee a takedown
propagates you must remember that the takedown happened. The tombstone is engineered
to hold the minimum that guarantees propagation and nothing more.

No-outing rule, enforced here by construction: a tombstone stores only the opaque
``record_id``, a fixed ``action``, timestamps, and storage-location *names*. It never
holds a title, a field value, a contributor identity, or any sealed value — there is
no place in the shape to put one. A location name and a record id are the only
identifiers, and both are already public, non-identity strings.

Integrity/fault-tolerance: the store is a single JSON document written atomically
(temp file in the same directory, then :func:`os.replace`), mirroring
``logs/takedowns.premis.json`` and the other archive logs — a reader never sees a
half-written file and a crash mid-write leaves the prior state intact.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from ledger.errors import LedgerError

__all__ = ["PRIMARY_LOCATION", "Tombstone", "TombstoneStore"]

#: The reserved location name standing for the archive's authoritative primary
#: store (``bags/``), which is not one of the configured mirror
#: :class:`~ledger.config.StorageLocation` entries but is still a copy location a
#: takedown must clear. Kept distinct so per-location status can report the primary
#: honestly alongside the mirrors.
PRIMARY_LOCATION = "primary"

#: The only action a tombstone records. A tombstone exists to guarantee *removal*
#: propagates; it is never a vehicle for any other state.
_TAKEDOWN_ACTION = "takedown"

#: The on-disk file, kept beside the other archive logs.
_FILENAME = "tombstones.json"


@dataclass(frozen=True)
class Tombstone:
    """One durable takedown marker for an opaque ``record_id``.

    ``confirmed`` maps a storage-location name to the ISO timestamp at which that
    location's copy was verified removed (its receipt time). A location absent from
    the map is *pending*: the removal has not yet been confirmed there, typically
    because it was offline when the takedown was issued.
    """

    record_id: str
    issued_at: str
    action: str = _TAKEDOWN_ACTION
    confirmed: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        """The canonical on-disk shape (opaque id + timestamps + location names)."""
        return {
            "record_id": self.record_id,
            "action": self.action,
            "issued_at": self.issued_at,
            "confirmed": dict(sorted(self.confirmed.items())),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Tombstone:
        """Rebuild a tombstone from :meth:`to_dict`; a malformed entry is rejected."""
        try:
            record_id = str(data["record_id"])
            issued_at = str(data["issued_at"])
        except (KeyError, TypeError) as exc:
            raise LedgerError(f"malformed tombstone entry: {exc}") from exc
        action = str(data.get("action", _TAKEDOWN_ACTION))
        raw_confirmed = data.get("confirmed", {})
        if not isinstance(raw_confirmed, dict):
            raise LedgerError("tombstone 'confirmed' must be a JSON object")
        confirmed = {str(loc): str(ts) for loc, ts in raw_confirmed.items()}
        return cls(record_id=record_id, issued_at=issued_at, action=action, confirmed=confirmed)

    def is_confirmed_at(self, location_name: str) -> bool:
        """True if ``location_name`` has confirmed the removal (receipt recorded)."""
        return location_name in self.confirmed


class TombstoneStore:
    """An append-and-confirm store of :class:`Tombstone` over ``logs/tombstones.json``.

    Constructed from an archive's ``logs`` directory (so a caller with an
    :class:`~ledger.ingest.Archive` passes ``archive.logs_dir``), it exposes just
    enough to record a takedown and drive propagation to reattaching replicas:
    :meth:`add` a tombstone, list :meth:`all`, find what is :meth:`pending_for` a
    location, :meth:`confirm` a location's removal, and read a record's per-location
    :meth:`status`. A missing file reads as an empty store, so a fresh archive needs
    no setup (installability).
    """

    def __init__(self, logs_dir: Path | str) -> None:
        self.logs_dir = Path(logs_dir)
        self._path = self.logs_dir / _FILENAME

    @property
    def path(self) -> Path:
        """The backing JSON file (``logs/tombstones.json``)."""
        return self._path

    def all(self) -> list[Tombstone]:
        """Every tombstone, in file order. A missing file is an empty store."""
        return self._read()

    def get(self, record_id: str) -> Tombstone | None:
        """The tombstone for ``record_id``, or ``None`` if the id was never taken down."""
        for tomb in self._read():
            if tomb.record_id == record_id:
                return tomb
        return None

    def is_tombstoned(self, record_id: str) -> bool:
        """True if ``record_id`` has been taken down (a tombstone exists for it)."""
        return self.get(record_id) is not None

    def add(self, record_id: str, issued_at: str) -> None:
        """Record a takedown of ``record_id`` (idempotent).

        A first takedown appends a tombstone with no confirmations yet. A repeated
        takedown of the same id is a no-op that preserves the existing confirmations
        and the original ``issued_at`` — so retrying a takedown never loses the
        receipts already collected (idempotence, fault tolerance).
        """
        tombs = self._read()
        for tomb in tombs:
            if tomb.record_id == record_id:
                return
        tombs.append(Tombstone(record_id=record_id, issued_at=issued_at))
        self._write(tombs)

    def pending_for(self, location_name: str) -> list[str]:
        """Record ids taken down but not yet confirmed removed at ``location_name``.

        This is what a reattaching replica reads to learn which stale copies it must
        still delete: every tombstone whose ``confirmed`` map lacks this location.
        """
        return [tomb.record_id for tomb in self._read() if not tomb.is_confirmed_at(location_name)]

    def confirm(self, record_id: str, location_name: str, when: str) -> None:
        """Mark ``location_name`` as having applied the takedown of ``record_id``.

        Idempotent and first-writer-wins on the receipt time: once a location has a
        receipt it is not overwritten, so the recorded moment is when the removal was
        *first* confirmed there. A ``record_id`` with no tombstone raises
        :class:`~ledger.errors.LedgerError` (the id is opaque, not identity) so a
        confirm against an unknown takedown fails loudly rather than silently.
        """
        tombs = self._read()
        found = False
        updated: list[Tombstone] = []
        for tomb in tombs:
            if tomb.record_id == record_id:
                found = True
                if location_name in tomb.confirmed:
                    updated.append(tomb)
                else:
                    merged = dict(tomb.confirmed)
                    merged[location_name] = when
                    updated.append(
                        Tombstone(
                            record_id=tomb.record_id,
                            issued_at=tomb.issued_at,
                            action=tomb.action,
                            confirmed=merged,
                        )
                    )
            else:
                updated.append(tomb)
        if not found:
            raise LedgerError(f"no tombstone for record id {record_id!r}")
        self._write(updated)

    def status(self, record_id: str) -> dict[str, str] | None:
        """Per-location confirmation for ``record_id``: ``{location: receipt_ts}``.

        Returns a copy of the confirmed map, or ``None`` if the id was never taken
        down. The caller supplies the full set of expected locations to derive which
        remain *pending* (a location present here is confirmed; any expected location
        absent here is pending), so this method never needs to know the fleet.
        """
        tomb = self.get(record_id)
        if tomb is None:
            return None
        return dict(tomb.confirmed)

    # --- persistence --------------------------------------------------------

    def _read(self) -> list[Tombstone]:
        """Load and parse the JSON list; a missing file is an empty store."""
        try:
            text = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        try:
            raw: object = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LedgerError(f"tombstone store {self._path} is not valid JSON: {exc}") from exc
        if not isinstance(raw, list):
            raise LedgerError(f"tombstone store {self._path} must contain a JSON list")
        return [Tombstone.from_dict(_as_dict(item)) for item in raw]

    def _write(self, tombs: list[Tombstone]) -> None:
        """Write the whole store atomically (temp file in the same dir, then rename).

        The temp file lives beside the target so :func:`os.replace` is an atomic
        rename on the same filesystem; a crash mid-write leaves the prior store
        intact and a reader never sees a half-written list (integrity, fault
        tolerance).
        """
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps([tomb.to_dict() for tomb in tombs], indent=2, ensure_ascii=False)
        tmp = self._path.with_name(f"{self._path.name}.{os.getpid()}.tmp")
        try:
            tmp.write_text(payload + "\n", encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise LedgerError(f"tombstone store could not be written: {self._path}") from exc


def _as_dict(item: object) -> dict[str, object]:
    """Coerce a decoded JSON element to a dict or reject it (defensive parsing)."""
    if not isinstance(item, dict):
        raise LedgerError("each tombstone entry must be a JSON object")
    return item
