#!/usr/bin/env python3
"""Idempotent SYNTHETIC demo seed for the public ledger showcase deployment.

This script stands a handful of clearly-fake records into an otherwise empty
archive so the public AWS demo (a single EC2 box running ``docker compose``) has
something to browse. It is run inside the container by ``infra/entrypoint.sh`` as
``python /app/seed_demo.py`` when ``LEDGER_DEMO_SEED=1``, and is equally runnable
locally for development.

Two properties are designed in on purpose:

* **Synthetic only.** Every name, body, and contact is explicitly marked
  ``(synthetic)``. This is a public showcase, never a real archive — no real
  person's history is ingested here.
* **Idempotent.** If the archive already holds any records (either ``browse`` as
  the anonymous public returns something, or any manifest exists under
  ``store/records``), the script does nothing and exits 0. Re-running it — on a
  container restart, or by hand — never duplicates or mutates anything.

Like every ledger read/ingest path, the no-outing rule is honoured: the script
NEVER prints a contributor identity or any sealed value. It prints only a short,
identity-free summary (counts plus record ids). The one synthetic identity it
seals (record 1's contributor) goes only into the encrypted vault via the public
``Archive.ingest`` path — never into stdout or any clear-text manifest.

The script uses only ledger's public API:

* :class:`ledger.config.Config`
* :class:`ledger.ingest.Archive`
* :class:`ledger.models.Record`, :class:`~ledger.models.Field`,
  :class:`~ledger.models.DublinCore`, :class:`~ledger.models.AccessPolicy`
* :class:`ledger.identity.ContributorIdentity`

It reads two environment variables: ``LEDGER_ROOT`` (default ``/data``) for the
archive root, and ``LEDGER_VAULT_KEY`` for sealing the one synthetic identity (a
Fernet key; forwarded to ledger, never printed).
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from ledger.config import Config
from ledger.identity import ContributorIdentity
from ledger.ingest import Archive
from ledger.models import AccessPolicy, DublinCore, Field, Grant, Record

# A fixed instant for every ingest, so a freshly seeded demo box is byte-for-byte
# reproducible across restarts and machines (determinism). Every ingest below
# passes this as ``now`` rather than relying on the wall clock.
_NOW = "2026-06-01T00:00:00Z"

# The anonymous public grant: sees only PUBLIC, unsealed content. This is the
# narrowest grant (deny by default) and is exactly what a read path uses when no
# one has authenticated. Built from the public model rather than imported as a
# helper so this script stays within the documented public surface.
_PUBLIC_LEVELS = frozenset({AccessPolicy.PUBLIC})


def anonymous() -> Grant:
    """Return the anonymous public grant (sees only PUBLIC, unsealed content)."""
    return Grant(subject="anonymous", levels=_PUBLIC_LEVELS)


def _resolve_root() -> Path:
    """The archive root, from ``LEDGER_ROOT`` (default ``/data``)."""
    return Path(os.environ.get("LEDGER_ROOT", "/data"))


def _load_or_init_config(root: Path) -> Config:
    """Load the archive config if it exists, else create defaults and init on disk.

    The on-disk config lives at ``root/store/config.json`` (see
    :meth:`Config.default` / :meth:`Archive.init`). If it is already there we load
    it — the entrypoint may have run ``ledger init`` first — otherwise we build a
    secure single-box default and stand the archive's directory tree up so the
    subsequent :class:`Archive` has somewhere to write.
    """
    config_path = root / "store" / "config.json"
    if config_path.exists():
        return Config.load(config_path)
    config = Config.default("Rosewater Community Archive (demo)", root)
    Archive.init(config)
    return config


def _already_seeded(archive: Archive, root: Path) -> bool:
    """Whether the archive already holds records (so seeding must be a no-op).

    Two independent checks, either of which means "do nothing": the anonymous
    public ``browse`` returns at least one record, OR any manifest file already
    exists under ``store/records``. The second catches records that exist but are
    not publicly listable, so a non-empty archive is never re-seeded (idempotency).
    """
    if archive.browse(anonymous(), now=_NOW):
        return True
    records_dir = root / "store" / "records"
    if records_dir.is_dir() and any(records_dir.iterdir()):
        return True
    return False


def _write_payload(work: Path, filename: str, text: str) -> dict[str, Path]:
    """Write one synthetic payload to a temp file and return ledger's payload map."""
    path = work / filename
    path.write_text(text, encoding="utf-8")
    return {filename: path}


def _seed(archive: Archive, work: Path) -> list[str]:
    """Ingest ~5 diverse SYNTHETIC records and return their record ids.

    Records span the access levels (PUBLIC, COMMUNITY, STEWARDS, SEALED_UNTIL) and
    exercise per-field selective disclosure, content warnings, rich Dublin Core,
    and one sealed contributor identity. Each is ingested through the one public
    ``Archive.ingest`` path with the fixed ``now`` for reproducibility.
    """
    ids: list[str] = []

    # (1) PUBLIC oral history with a sealed contributor identity. The "story" field
    #     is PUBLIC; the contributor's real names and the location are sealed per
    #     field (selective disclosure). The identity object goes only to the vault.
    identity = ContributorIdentity(
        name="(synthetic) Dana Okonkwo",
        contact="(synthetic) dana@example.invalid",
        pronouns="they/them",
        notes="(synthetic) demo contributor — not a real person",
    )
    rec1 = Record(
        title="(synthetic) Oral history: organizing after the 1987 clinic raid",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(
            title=["(synthetic) Oral history: organizing after the 1987 clinic raid"],
            creator=["Rosewater Community Archive (demo)"],
            subject=["oral history", "community organizing", "harm reduction"],
            description=[
                "(synthetic) A recorded recollection of mutual-aid organizing in "
                "the months after a 1987 clinic raid. All names and details are "
                "fictional and used only for this demo."
            ],
            type=["Sound", "Text"],
            date=["1987"],
            language=["en"],
        ),
        content_warnings=["police-violence", "medical"],
        fields=[
            Field(
                name="story",
                value=(
                    "(synthetic) We kept the phone tree on index cards and met in "
                    "the back of the laundromat. When the clinic was raided we "
                    "moved supplies house to house for a month. Nobody got "
                    "turned away."
                ),
                policy=AccessPolicy.PUBLIC,
            ),
            Field(
                name="real_names",
                value="(synthetic) [the organizers named in the recording]",
                policy=AccessPolicy.SEALED_UNTIL,
            ),
            Field(
                name="location",
                value="(synthetic) [the laundromat and safehouse addresses]",
                policy=AccessPolicy.SEALED_UNTIL,
            ),
        ],
    )
    payload1 = _write_payload(
        work,
        "oral-history-1987.txt",
        "(synthetic) Transcript of an oral-history recording. Fictional content for "
        "the ledger public demo. Body intentionally brief.\n",
    )
    aip1 = archive.ingest(payload1, rec1, identity=identity, now=_NOW)
    ids.append(aip1.record.record_id)

    # (2) COMMUNITY zine — visible to community members, not the anonymous public.
    rec2 = Record(
        title="(synthetic) Mutual-Aid Pantry Runbook",
        default_policy=AccessPolicy.COMMUNITY,
        dublin_core=DublinCore(
            title=["(synthetic) Mutual-Aid Pantry Runbook"],
            creator=["Rosewater Community Archive (demo)"],
            subject=["mutual aid", "zine", "food security"],
            description=[
                "(synthetic) A community zine documenting how to run a small "
                "neighbourhood food pantry. Demo content only."
            ],
            type=["Text"],
            date=["2021"],
            language=["en"],
        ),
        fields=[
            Field(
                name="summary",
                value=(
                    "(synthetic) Stocking, rotation, and a no-questions-asked "
                    "intake flow for a volunteer-run pantry."
                ),
                policy=AccessPolicy.COMMUNITY,
            ),
        ],
    )
    payload2 = _write_payload(
        work,
        "pantry-runbook.txt",
        "(synthetic) Mutual-Aid Pantry Runbook — community zine. Fictional demo "
        "content.\n",
    )
    aip2 = archive.ingest(payload2, rec2, now=_NOW)
    ids.append(aip2.record.record_id)

    # (3) STEWARDS protocol — visible only to stewards.
    rec3 = Record(
        title="(synthetic) Steward Protocol: handling a consent withdrawal",
        default_policy=AccessPolicy.STEWARDS,
        dublin_core=DublinCore(
            title=["(synthetic) Steward Protocol: handling a consent withdrawal"],
            creator=["Rosewater Community Archive (demo)"],
            subject=["governance", "consent", "stewardship"],
            description=[
                "(synthetic) Internal steward-only protocol describing how to "
                "process a contributor's request to withdraw consent. Demo only."
            ],
            type=["Text"],
            date=["2023"],
            language=["en"],
        ),
        fields=[
            Field(
                name="procedure",
                value=(
                    "(synthetic) Verify the request, revoke the identity ref, run a "
                    "fixity audit, and record a PREMIS consent-change event."
                ),
                policy=AccessPolicy.STEWARDS,
            ),
        ],
    )
    payload3 = _write_payload(
        work,
        "steward-protocol.txt",
        "(synthetic) Steward-only protocol. Fictional demo content.\n",
    )
    aip3 = archive.ingest(payload3, rec3, now=_NOW)
    ids.append(aip3.record.record_id)

    # (4) SEALED_UNTIL testimony — sealed by default at the record level, with a
    #     field that unseals on a fixed future date.
    rec4 = Record(
        title="(synthetic) Sealed testimony (opens 2030)",
        default_policy=AccessPolicy.SEALED_UNTIL,
        dublin_core=DublinCore(
            title=["(synthetic) Sealed testimony (opens 2030)"],
            creator=["Rosewater Community Archive (demo)"],
            subject=["testimony", "embargoed"],
            description=[
                "(synthetic) A testimony embargoed until 2030 to protect the people "
                "named in it. Demo content only; nothing real is sealed here."
            ],
            type=["Text"],
            date=["2024"],
            language=["en"],
        ),
        fields=[
            Field(
                name="testimony",
                value=(
                    "(synthetic) An account the contributor asked to keep sealed "
                    "until 2030. Fictional demo text."
                ),
                policy=AccessPolicy.SEALED_UNTIL,
                unseal_at="2030-01-01T00:00:00Z",
            ),
        ],
    )
    payload4 = _write_payload(
        work,
        "sealed-testimony.txt",
        "(synthetic) Embargoed testimony. Fictional demo content.\n",
    )
    aip4 = archive.ingest(payload4, rec4, now=_NOW)
    ids.append(aip4.record.record_id)

    # (5) PUBLIC 1991 Pride flyer.
    rec5 = Record(
        title="(synthetic) Pride march flyer, 1991",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(
            title=["(synthetic) Pride march flyer, 1991"],
            creator=["Rosewater Community Archive (demo)"],
            subject=["pride", "flyer", "ephemera", "1991"],
            description=[
                "(synthetic) A photocopied flyer advertising a 1991 Pride march and "
                "after-party. Fictional demo ephemera."
            ],
            type=["Image", "Text"],
            date=["1991"],
            language=["en"],
        ),
        fields=[
            Field(
                name="caption",
                value=(
                    "(synthetic) Two-color photocopy: 'MARCH WITH US — Saturday, "
                    "noon, the fountain.' Demo ephemera."
                ),
                policy=AccessPolicy.PUBLIC,
            ),
        ],
    )
    payload5 = _write_payload(
        work,
        "pride-flyer-1991.txt",
        "(synthetic) OCR text of a 1991 Pride march flyer. Fictional demo "
        "content.\n",
    )
    aip5 = archive.ingest(payload5, rec5, now=_NOW)
    ids.append(aip5.record.record_id)

    return ids


def main() -> int:
    """Seed the demo archive idempotently; print an identity-free summary."""
    root = _resolve_root()
    config = _load_or_init_config(root)
    archive = Archive(config)

    if _already_seeded(archive, root):
        print(f"ledger demo seed: archive at {root} already has records; nothing to do.")
        return 0

    with tempfile.TemporaryDirectory(prefix="ledger-demo-seed-") as tmp:
        ids = _seed(archive, Path(tmp))

    # Identity-free summary only: counts plus record ids. NEVER an identity or a
    # sealed value.
    print(f"ledger demo seed: ingested {len(ids)} synthetic records into {root}.")
    for record_id in ids:
        print(f"  - {record_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
