"""Group continuity: the "if this group folds" hand-off (EX1).

Community archives are precarious. Mutual-aid groups routinely disband; volunteer
collectives lose the one person who knew where the files were; the Lesbian Herstory
Archives and the Queer Zine Archive Project are sustained by individuals, not
institutions. The research basis for ledger names continuity as *the* mission risk.
This module turns "what happens if we fold?" from a panic into a procedure.

It produces a **hand-off manifest**: a single, no-outing-safe document a folding
collective can hand to a designated successor so they can stand the archive back up
and *prove* it arrived intact. It deliberately leans on what already exists rather
than inventing a parallel mechanism:

* the archive is plain, open BagIt + PREMIS + Dublin Core, readable without ledger
  (`docs/CONTINUITY.md` §2), so the manifest is an *inventory and a runbook*, not a
  new container format;
* fixity is re-verified per bag at hand-off time (:meth:`Archive.audit_fixity`), so
  a successor inherits a *checked* archive, not a hopeful copy;
* the designated successor is granted stewardship through the ordinary grant model
  (:func:`ledger.access.grants.designated_successor`) — steward access, never an
  automatic power to out contributors.

No-outing rule, enforced by construction: the manifest lists opaque ``record_id``
values, per-bag fixity outcomes, file counts, and storage locations — never a
contributor identity, a sealed value, or a vault key. The encrypted identity vault
is referenced only as a *file to copy*; its key must travel out-of-band and is
never written into the manifest. The manifest discloses nothing a stolen copy could
use to out anyone.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ledger.config import StorageLocation
from ledger.ingest import Archive
from ledger.models import canonical_json

# Schema version for the hand-off manifest, so a successor's tooling can tell which
# shape it is reading and evolve it later without misreading an older file.
HANDOFF_SCHEMA_VERSION: int = 1


@dataclass(frozen=True)
class RecordInventory:
    """One record's continuity line: opaque id, fixity outcome, file count.

    Carries no title, description, or identity — only what a successor needs to
    confirm the item arrived and verifies (no-outing rule, default to narrowest).
    """

    record_id: str
    fixity_ok: bool
    files_checked: int

    def to_dict(self) -> dict[str, object]:
        return {
            "record_id": self.record_id,
            "fixity_ok": self.fixity_ok,
            "files_checked": self.files_checked,
        }


@dataclass(frozen=True)
class HandoffManifest:
    """A no-outing-safe continuity package for handing an archive to a successor.

    Bundles everything a designated successor needs to re-establish the archive and
    prove it is intact: the per-record fixity inventory, where the bytes live, the
    fact (not the contents) of the encrypted vault, and a plain-language runbook —
    while structurally carrying no identity, sealed value, or key.
    """

    schema_version: int
    archive_name: str
    generated_at: str
    successor: str | None
    total_records: int
    all_fixity_ok: bool
    records: tuple[RecordInventory, ...]
    vault_present: bool
    store_root: str
    vault_path: str
    locations: tuple[StorageLocation, ...]

    def to_dict(self) -> dict[str, object]:
        """Serialize to a JSON-ready mapping, with the runbook embedded.

        Deterministic for given inputs (no clock consulted here; ``generated_at`` is
        supplied), so the same archive state hands off to a byte-identical manifest.
        """
        return {
            "schema_version": self.schema_version,
            "archive_name": self.archive_name,
            "generated_at": self.generated_at,
            "successor": self.successor,
            "total_records": self.total_records,
            "all_fixity_ok": self.all_fixity_ok,
            "records": [r.to_dict() for r in self.records],
            "vault": {
                "present": self.vault_present,
                "path": self.vault_path,
                "key_handling": (
                    "ENCRYPTED. Copy this file as-is. Its key is NOT in this manifest "
                    "and must be transferred to the successor out-of-band; without the "
                    "key the vault is useless and no contributor can be identified."
                ),
            },
            "store_root": self.store_root,
            "locations": [loc.to_dict() for loc in self.locations],
            "runbook": self.runbook(),
        }

    def to_json(self) -> str:
        """Canonical JSON for the manifest (byte-stable for a given archive state)."""
        return canonical_json(self.to_dict())

    def runbook(self) -> str:
        """A plain-language, step-by-step hand-off runbook for the successor.

        Written for a non-ops volunteer inheriting the archive: copy the bytes, move
        the key safely, verify fixity, take over stewardship. It states the one thing
        that must never go in the same channel as the data — the vault key — and
        points at the existing, audited tools (`ledger verify-backup`) so the
        successor confirms intactness rather than assuming it.
        """
        fixity_line = (
            "All bags verified intact at hand-off time."
            if self.all_fixity_ok
            else "WARNING: one or more bags failed fixity — investigate before relying on this copy."
        )
        successor_line = (
            f"Designated successor: {self.successor}."
            if self.successor
            else "No successor named yet — record who is taking over before proceeding."
        )
        return "\n".join(
            [
                f"# Archive hand-off runbook — {self.archive_name}",
                "",
                f"Generated: {self.generated_at}",
                successor_line,
                f"Records: {self.total_records}. {fixity_line}",
                "",
                "This archive is plain, open BagIt + PREMIS + Dublin Core. It does not",
                "depend on ledger to be readable — any standard tool can verify a bag.",
                "",
                "## Steps",
                "",
                "1. Copy the entire store directory (all bags and records) to the",
                f"   successor's machine: {self.store_root}",
                "2. Copy any off-box replicas listed below so redundancy is preserved.",
                "3. Copy the encrypted identity vault file as-is:",
                f"   {self.vault_path}",
                "   Transfer its key SEPARATELY and out-of-band (not by the same channel,",
                "   not in this manifest). Without the key the vault is useless — that is",
                "   by design, and it is what keeps a stolen copy from outing anyone.",
                "4. On the successor's machine, re-point the config's store_root and",
                "   vault_path at the new locations, then run `ledger verify-backup",
                "   --backup <restored-root>` to confirm every bag verifies. Do not go",
                "   live until it reports PASS.",
                "5. Grant the successor stewardship via a designated-successor grant",
                "   (steward access only; identity-unseal is NOT included and must be",
                "   decided separately under your governance).",
                "6. Re-establish the scheduled fixity audit and off-box backups on the",
                "   new host so the archive stays checked and replicated.",
                "",
                "## Record inventory",
                "",
                *[
                    f"- {r.record_id}\t{'ok' if r.fixity_ok else 'FAIL'}\t"
                    f"({r.files_checked} file(s))"
                    for r in self.records
                ],
            ]
        )


def build_handoff(archive: Archive, *, now: str, successor: str | None = None) -> HandoffManifest:
    """Build a :class:`HandoffManifest` for ``archive`` as of instant ``now``.

    Re-verifies every bag's fixity (so the successor inherits a *checked* archive),
    inventories the records by opaque id, and records where the bytes and the
    encrypted vault live — without ever reading an identity, a sealed value, or the
    vault key (no-outing rule). ``now`` is injected rather than read from the clock,
    so a hand-off is reproducible. ``successor`` optionally names who is taking over.
    """
    inventory: list[RecordInventory] = []
    all_ok = True
    for bag_name, report in archive.audit_fixity():
        ok = report.ok
        all_ok = all_ok and ok
        inventory.append(
            RecordInventory(record_id=bag_name, fixity_ok=ok, files_checked=report.checked)
        )
    return HandoffManifest(
        schema_version=HANDOFF_SCHEMA_VERSION,
        archive_name=archive.config.archive_name,
        generated_at=now,
        successor=successor,
        total_records=len(inventory),
        all_fixity_ok=all_ok,
        records=tuple(inventory),
        vault_present=Path(archive.vault_path).exists(),
        store_root=str(archive.store_root),
        vault_path=str(archive.vault_path),
        locations=tuple(archive.config.locations),
    )
