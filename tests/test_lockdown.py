"""Lockdown mode — the one-command duress posture and its inverse (EXP-02).

This exercises the whole duress cycle on a real on-disk archive, proving the two
promises that make lockdown safe to arm:

* the destructive step is **dual-controlled and replica-gated** — the local vault is
  shredded only after an off-box replica verifies clean, and only once two distinct
  stewards have approved a ``lockdown`` proposal; and
* it is **fully reversible** — ``stand-up`` restores the vault from the verified
  replica, lifts the disclosure freeze, and the sealed contributor identity resolves
  again with the separately-held key.

It also pins the three fail-safes: while locked down the reading-room refuses all
non-PUBLIC disclosure (a steward is forced to the public face), a shred is *refused*
when no replica verifies (the only copy is never destroyed), and a dry run mutates
nothing.
"""

from __future__ import annotations

import json
import shutil
import threading
import urllib.request
from collections.abc import Iterator
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import pytest

from ledger import cli, dualcontrol
from ledger.access.grants import build_grant, steward
from ledger.config import Config
from ledger.identity import ContributorIdentity
from ledger.ingest import Archive
from ledger.lockdown import LockdownConfig, is_locked_down, lockdown_flag_path
from ledger.metadata.premis import PremisLog
from ledger.models import AccessPolicy, DublinCore, Field, PremisEventType, Record
from ledger.server import make_server

# Loud sentinels: any appearance on a surface that should be frozen is a clear leak.
_IDENTITY = "SENTINEL-IDENTITY-DO-NOT-LEAK-7Q4X"
_COMMUNITY = "SENTINEL-COMMUNITY-FIELD-5B1P"
_VAULT_KEY = b"0123456789abcdef0123456789abcdef0123456789a="
_NOW = "2026-07-02T00:00:00Z"
_GRANT_HEADER = "X-Ledger-Grant"


def _build_archive(root: Path, *, replica: Path) -> tuple[Archive, str]:
    """Stand up an armed archive with one record (public + community + sealed identity).

    The archive requires two stewards to approve any dual-control action and is armed
    to shred its vault under lockdown, verifying ``replica`` first.
    """
    config = Config.default("Duress Community Archive", root)
    config.dual_control_threshold = 2
    config.lockdown = LockdownConfig(
        stop_disclosure=True,
        shred_vault=True,
        required_replica_locations=[str(replica)],
        min_verified_replicas=1,
    )
    archive = Archive.init(config)
    record = Record(
        title="Thursday gatherings",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(title=["Thursday gatherings"], publisher=[config.archive_name]),
        fields=[
            Field(name="story", value="A public account.", policy=AccessPolicy.PUBLIC),
            Field(name="roster", value=_COMMUNITY, policy=AccessPolicy.COMMUNITY),
        ],
    )
    archive.ingest(
        {},
        record,
        identity=ContributorIdentity(name=_IDENTITY),
        vault_key=_VAULT_KEY,
        agent="lockdown-test",
        now=_NOW,
    )
    return archive, record.record_id


def _run(*argv: str) -> int:
    """Run a CLI subcommand, swallowing its (noisy but no-outing-safe) output."""
    sink = StringIO()
    with redirect_stdout(sink), redirect_stderr(sink):
        return cli.main(list(argv))


def _proposal_store(root: Path) -> dualcontrol.ProposalStore:
    return dualcontrol.ProposalStore(root / "store" / "logs" / "proposals.json")


def _propose_approve(root: Path, action: str) -> str:
    """Propose ``action`` and gather the second approval; return the proposal id.

    Because lockdown/stand-up are *deferred* actions, reaching the threshold does NOT
    fire them — that is the separate ``--execute`` step — so this leaves an approved,
    still-open proposal ready to execute.
    """
    assert (
        _run(
            "propose",
            "--root",
            str(root),
            "--action",
            action,
            "--id",
            action,
            "--actor",
            "steward-1",
            "--reason",
            "duress drill",
            "--now",
            _NOW,
        )
        == 0
    )
    pid = _proposal_store(root).open_proposals()[-1].proposal_id
    assert (
        _run("approve", "--root", str(root), "--id", pid, "--actor", "steward-2", "--now", _NOW)
        == 0
    )
    # Still open (approved but not executed): the deferred action waits for --execute.
    assert any(p.proposal_id == pid for p in _proposal_store(root).open_proposals())
    return pid


def _premis_event_types(root: Path) -> list[PremisEventType]:
    log_path = root / "store" / "logs" / "lockdown.premis.json"
    if not log_path.exists():
        return []
    return [e.event_type for e in PremisLog.read(log_path).events]


@pytest.fixture
def served(tmp_path: Path) -> Iterator[tuple[str, str, Path, Archive]]:
    """A running reading-room over the armed archive; yields (base_url, rid, root, archive)."""
    root = tmp_path / "arc"
    replica = tmp_path / "replica"
    archive, rid = _build_archive(root, replica=replica)
    # A faithful off-box replica made AFTER ingest: it carries good bags + the vault.
    shutil.copytree(root, replica)

    httpd = make_server(archive, host="127.0.0.1", port=0)
    # Provision a steward grant the handler can resolve from the request header.
    httpd.grants = {"a-steward": steward("a-steward")}  # type: ignore[attr-defined]
    host, port = httpd.server_address[0], httpd.server_address[1]
    host_s = host.decode("ascii") if isinstance(host, (bytes, bytearray)) else str(host)
    base = f"http://{host_s}:{int(port)}"
    sink = StringIO()
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    with redirect_stderr(sink), redirect_stdout(sink):
        thread.start()
        try:
            yield base, rid, root, archive
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()


def _steward_fields(base: str, rid: str) -> dict[str, object]:
    """The steward-visible fields of one record's disclosed JSON."""
    req = urllib.request.Request(  # noqa: S310 - loopback URL we constructed
        f"{base}/api/record/{rid}", headers={_GRANT_HEADER: "a-steward"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
        return dict(json.loads(resp.read().decode("utf-8"))["fields"])


def test_lockdown_then_stand_up_round_trip(served: tuple[str, str, Path, Archive]) -> None:
    """The full cycle: dual-controlled shred, frozen disclosure, then a clean restore."""
    base, rid, root, archive = served
    vault = archive.vault_path

    # Sanity: before lockdown a steward sees the community-tier field, vault present.
    assert _steward_fields(base, rid).get("roster") == _COMMUNITY
    assert vault.exists()

    # --- lockdown: propose + approve (two stewards), then the deliberate --execute ---
    _propose_approve(root, "lockdown")
    assert (
        _run("lockdown", "--root", str(root), "--actor", "steward-2", "--execute", "--now", _NOW)
        == 0
    )

    # The local vault was shredded (replica verified) and the freeze is in effect.
    assert not vault.exists()
    assert is_locked_down(archive)
    assert lockdown_flag_path(archive).exists()
    assert PremisEventType.LOCKDOWN in _premis_event_types(root)

    # The reading-room now refuses non-PUBLIC disclosure: even the steward is forced to
    # the public face — the community sentinel is gone, the public field remains.
    frozen = _steward_fields(base, rid)
    assert "roster" not in frozen
    assert _COMMUNITY not in json.dumps(frozen)
    assert frozen.get("story") == "A public account."

    # --- stand-up: the inverse, also dual-controlled ---
    _propose_approve(root, "stand-up")
    assert (
        _run("stand-up", "--root", str(root), "--actor", "steward-2", "--execute", "--now", _NOW)
        == 0
    )

    # Vault restored from the verified replica, freeze lifted, event recorded.
    assert vault.exists()
    assert not is_locked_down(archive)
    assert PremisEventType.STANDUP in _premis_event_types(root)

    # Disclosure resumes: the steward sees the community field again...
    assert _steward_fields(base, rid).get("roster") == _COMMUNITY
    # ...and the sealed identity resolves with the separately-held key (full restore).
    restored = Archive(Config.load(root / "store" / "config.json"))
    restored._open_vault(_VAULT_KEY)
    unseal = build_grant("recovery-steward", identity_unseal=[archive.get(rid).identity_ref or ""])
    assert restored.resolve_identity(rid, unseal, now=_NOW).name == _IDENTITY


def test_shred_refused_without_a_verified_replica(tmp_path: Path) -> None:
    """With no replica that verifies, disclosure stops but the only vault is KEPT."""
    root = tmp_path / "arc"
    missing = tmp_path / "does-not-exist"  # never created -> nothing to verify against
    archive, _rid = _build_archive(root, replica=missing)
    vault = archive.vault_path
    assert vault.exists()

    _propose_approve(root, "lockdown")
    # execute_lockdown raises (refuses to shred) -> the CLI returns a non-zero code.
    assert (
        _run("lockdown", "--root", str(root), "--actor", "steward-2", "--execute", "--now", _NOW)
        == 2
    )

    # Fail-safe: the vault survived, disclosure is still frozen, and the refusal is logged.
    assert vault.exists()
    assert is_locked_down(archive)
    types = _premis_event_types(root)
    assert PremisEventType.LOCKDOWN in types  # a failure event was recorded


def test_dry_run_mutates_nothing(tmp_path: Path) -> None:
    """The default (no --execute) prints a plan and changes not one byte of state."""
    root = tmp_path / "arc"
    replica = tmp_path / "replica"
    archive, _rid = _build_archive(root, replica=replica)
    shutil.copytree(root, replica)
    vault = archive.vault_path

    # A dry run needs no proposal and performs no action.
    assert _run("lockdown", "--root", str(root), "--actor", "steward-1", "--now", _NOW) == 0

    assert vault.exists()
    assert not is_locked_down(archive)
    assert not lockdown_flag_path(archive).exists()
    assert _premis_event_types(root) == []
    assert _proposal_store(root).open_proposals() == []
