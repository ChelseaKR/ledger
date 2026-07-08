"""Lockdown mode — a one-command duress posture, safely reversible.

A community archive can face a moment where continuing to *disclose* is dangerous:
a raid, a seizure, a coercion attempt. Lockdown is the deliberate, dual-controlled
response. It does two separable things, narrowest-first:

* **Stop non-PUBLIC disclosure immediately.** A ``lockdown.flag`` marker is written
  into the archive's ``logs/`` state dir; the reading-room server checks it on every
  request and, while it is present, discloses *only* PUBLIC material — every
  community-, steward-, or sealed-tier field, and every privileged grant, is refused
  (fail-closed). This is cheap, reversible, and loses nothing.
* **Shred the local identity vault — but only after proving an off-box replica.**
  Destroying the on-box vault is what protects contributors if the disk is seized,
  but it is irreversible, so it is *never* done on faith. Shredding is disabled
  unless a steward has configured it, and even then it runs only after
  :func:`verify_backup_location` confirms at least ``min_verified_replicas`` of the
  configured off-box replica locations restore clean (full RFC 8493 fixity + a
  present vault). If the replicas cannot be verified, disclosure is still stopped but
  the local vault is kept — the archive never destroys its only copy (safety).

:func:`execute_stand_up` is the exact inverse: it verifies a replica, restores the
vault from it if the local one was shredded, removes the flag, and records the event
— so a false alarm is fully recoverable.

Every step is a PREMIS event (accountability), and nothing here ever reads, logs, or
returns a contributor identity or a sealed value — it operates on the *vault file* as
opaque bytes and reports only counts and locations (the no-outing rule).
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ledger.errors import ConfigError, LedgerError
from ledger.metadata.premis import PremisLog
from ledger.models import PremisEvent, PremisEventType

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids an import cycle
    from ledger.ingest import Archive

__all__ = [
    "BackupVerification",
    "LockdownConfig",
    "LockdownResult",
    "execute_lockdown",
    "execute_stand_up",
    "is_locked_down",
    "lockdown_flag_path",
    "plan_lockdown",
    "verify_backup_location",
]

# The marker the server checks; lives in the archive's logs/ state dir so it travels
# with the archive and is trivially inspectable (a steward can see the posture).
_FLAG_FILENAME = "lockdown.flag"
# The append-only PREMIS log for lockdown/stand-up decisions, kept beside the other
# archive-level logs so the duress history outlives the data it protected.
_LOCKDOWN_PREMIS = "lockdown.premis.json"
_CONFIG_FILENAME = "config.json"
# Overwrite the vault in fixed-size chunks so shredding a large vault never pulls it
# all into memory (efficiency, minimal computing).
_SHRED_CHUNK = 65536


@dataclass(frozen=True)
class LockdownConfig:
    """How this archive behaves under lockdown (declarative, off by default).

    ``stop_disclosure`` gates the disclosure freeze; ``shred_vault`` gates the
    irreversible local-vault destruction and is **off unless a steward turns it on**,
    so a misread config can never destroy a vault by default (safety: default to
    narrowest). ``required_replica_locations`` are paths to off-box *backup roots*
    (each holding ``store/`` + ``identity.vault``); ``min_verified_replicas`` of them
    must restore clean before any shred proceeds.
    """

    stop_disclosure: bool = True
    shred_vault: bool = False
    required_replica_locations: list[str] = field(default_factory=list)
    min_verified_replicas: int = 1

    def validate(self, *, archive_locations: tuple[str | Path, ...] = ()) -> None:
        """Raise :class:`LedgerError` if the lockdown policy is self-contradictory.

        Correctness: a ``shred_vault`` posture with no replica to verify against, or a
        replica threshold that can never be met, is caught here rather than at the
        dangerous moment a steward triggers a duress shred.

        ``archive_locations`` are the live archive's own on-box paths (its root,
        ``store_root``, ``vault_path``, ...) when known to the caller. A
        ``required_replica_locations`` entry that resolves to one of them is not an
        off-box replica at all — it is the archive pointing at itself — so
        ``min_verified_replicas`` could be satisfied while providing *no* real
        redundancy (a duress shred could proceed having "verified" nothing but the
        copy it is about to destroy). Checked unconditionally, not just when
        ``shred_vault`` is on, since a self-referential replica is equally useless
        for stand-up's restore path.
        """
        if self.min_verified_replicas < 1:
            raise ConfigError("lockdown.min_verified_replicas must be at least 1")
        if self.shred_vault:
            if not self.required_replica_locations:
                raise ConfigError(
                    "lockdown.shred_vault requires at least one required_replica_locations "
                    "entry to verify before destroying the local vault"
                )
            if self.min_verified_replicas > len(self.required_replica_locations):
                raise ConfigError(
                    "lockdown.min_verified_replicas exceeds the number of configured "
                    "required_replica_locations; the shred could never be authorized"
                )
        if archive_locations and self.required_replica_locations:
            live = {Path(loc).expanduser().resolve() for loc in archive_locations}
            for location in self.required_replica_locations:
                if Path(location).expanduser().resolve() in live:
                    raise ConfigError(
                        f"lockdown.required_replica_locations entry {location!r} is the "
                        "live archive's own location, not an off-box replica — this "
                        "provides no real redundancy; point it at a genuinely separate copy"
                    )

    def to_dict(self) -> dict[str, object]:
        """Serialize to a plain JSON-/TOML-ready mapping (deterministic order)."""
        return {
            "stop_disclosure": self.stop_disclosure,
            "shred_vault": self.shred_vault,
            "required_replica_locations": list(self.required_replica_locations),
            "min_verified_replicas": self.min_verified_replicas,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> LockdownConfig:
        """Rebuild from a mapping, coercing scalars and rejecting malformed shapes."""
        raw_locations = data.get("required_replica_locations", [])
        if not isinstance(raw_locations, list):
            raise ConfigError("lockdown.required_replica_locations must be a list")
        try:
            min_verified = int(str(data.get("min_verified_replicas", 1)))
        except ValueError as exc:
            raise ConfigError("lockdown.min_verified_replicas must be an integer") from exc
        config = cls(
            stop_disclosure=bool(data.get("stop_disclosure", True)),
            shred_vault=bool(data.get("shred_vault", False)),
            required_replica_locations=[str(loc) for loc in raw_locations],
            min_verified_replicas=min_verified,
        )
        config.validate()
        return config


@dataclass(frozen=True)
class BagFixity:
    """One replica bag's fixity outcome (name + pass/fail + files checked)."""

    name: str
    ok: bool
    checked: int


@dataclass(frozen=True)
class BackupVerification:
    """The result of verifying one off-box replica location in place.

    ``ok`` is true only when the replica is *readable* and every bag passes full
    fixity; ``reason`` is a non-identity-bearing code when it is not. ``has_vault``
    reports whether an encrypted vault is present to restore from (never its
    contents). Only bag names, counts, and the location path appear here — never a
    payload byte or an identity (no-outing rule).
    """

    location: str
    ok: bool
    reason: str
    bags: tuple[BagFixity, ...] = ()
    has_vault: bool = False

    @property
    def failures(self) -> int:
        """How many bags failed fixity."""
        return sum(1 for bag in self.bags if not bag.ok)


def verify_backup_location(backup: Path) -> BackupVerification:
    """Verify a restored archive root at ``backup`` in place (RFC 8493 fixity).

    The shared verification core behind ``ledger verify-backup`` and the lockdown
    shred gate: it loads the replica's own config, re-points the stored (original-box)
    paths at ``backup`` so the copy on disk is what is checked, confirms the store is
    readable, then runs full fixity over every bag. Pure of side effects and of
    identity — it reports only readability, per-bag fixity, and whether a vault file
    exists (no-outing rule).
    """
    # Local imports break the config -> lockdown -> ingest -> config import cycle:
    # LockdownConfig (used by config.py) needs none of these, so they load lazily.
    from ledger.config import Config
    from ledger.ingest import Archive

    backup = Path(backup)
    config_path = backup / "store" / _CONFIG_FILENAME
    if not config_path.exists():
        return BackupVerification(str(backup), ok=False, reason="config-missing")
    try:
        config = Config.load(config_path)
    except LedgerError:
        return BackupVerification(str(backup), ok=False, reason="config-unreadable")
    # The stored config records the ORIGINAL box's absolute paths; re-point it at the
    # backup so we verify the copy on disk, not wherever it was first written.
    config.store_root = str(backup / "store")
    config.vault_path = str(backup / "identity.vault")
    archive = Archive(config)

    ready, reason = archive.check_readiness()
    has_vault = (backup / "identity.vault").exists()
    if not ready:
        return BackupVerification(str(backup), ok=False, reason=reason, has_vault=has_vault)

    reports = archive.audit_fixity()
    bags = tuple(BagFixity(name, report.ok, report.checked) for name, report in reports)
    all_ok = all(bag.ok for bag in bags)
    return BackupVerification(
        str(backup),
        ok=all_ok,
        reason="" if all_ok else "fixity-failed",
        bags=bags,
        has_vault=has_vault,
    )


@dataclass(frozen=True)
class LockdownResult:
    """A no-outing-safe summary of a lockdown or stand-up run.

    ``steps`` are the human-readable lines actually performed (or, in a dry run, the
    lines that *would* be performed). ``runbook`` is the recovery guidance printed
    after an execute. Nothing here carries an identity or a sealed value.
    """

    action: str
    dry_run: bool
    disclosure_stopped: bool
    vault_shredded: bool
    verified_replicas: int
    steps: tuple[str, ...]
    runbook: str = ""

    def summary(self) -> str:
        """A single no-outing-safe status line for the CLI/audit surface."""
        if self.dry_run:
            return f"{self.action} DRY-RUN — {len(self.steps)} step(s) planned; nothing changed"
        bits = [f"disclosure {'stopped' if self.disclosure_stopped else 'unchanged'}"]
        if self.vault_shredded:
            bits.append(f"local vault shredded after {self.verified_replicas} verified replica(s)")
        elif self.action == "lockdown":
            bits.append("local vault kept")
        return f"{self.action} executed — " + "; ".join(bits)


def lockdown_flag_path(archive: Archive) -> Path:
    """Where the lockdown marker lives for ``archive`` (its ``logs/`` state dir)."""
    return archive.logs_dir / _FLAG_FILENAME


def is_locked_down(archive: Archive) -> bool:
    """Whether ``archive`` is currently in lockdown (the marker is present).

    Cheap and side-effect-free so the server can call it on every request; a missing
    or unreadable marker is treated as *not* locked down (fail-open only for the
    check itself — the marker's presence is the authoritative freeze signal).
    """
    return lockdown_flag_path(archive).exists()


def _lockdown_config(archive: Archive) -> LockdownConfig:
    """The archive's configured lockdown policy, or the safe (no-shred) default."""
    configured = getattr(archive.config, "lockdown", None)
    if isinstance(configured, LockdownConfig):
        configured.validate(
            archive_locations=(archive.store_root.parent, archive.store_root, archive.vault_path)
        )
        return configured
    return LockdownConfig()


def _record_event(
    archive: Archive,
    *,
    event_type: PremisEventType,
    actor: str,
    outcome: str,
    detail: str,
    now: str,
) -> None:
    """Append one lockdown/stand-up PREMIS event to the archive-level log.

    Kept in ``logs/lockdown.premis.json`` (append-only) so the duress decision is
    provable after the fact. The detail carries only counts and posture — never an
    identity or a vault byte (no-outing rule).
    """
    archive.logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = archive.logs_dir / _LOCKDOWN_PREMIS
    log = PremisLog.read(log_path) if log_path.exists() else PremisLog()
    log.record(
        PremisEvent(
            event_type=event_type,
            agent=actor,
            outcome=outcome,
            detail=detail,
            event_datetime=now,
        )
    )
    log.write(log_path)


def _verify_replicas(config: LockdownConfig) -> list[BackupVerification]:
    """Verify every configured off-box replica location, in order."""
    return [verify_backup_location(Path(loc)) for loc in config.required_replica_locations]


def _clean_replicas(
    results: list[BackupVerification], *, need_vault: bool
) -> list[BackupVerification]:
    """The replicas that verified clean (and, when required, carry a vault to restore)."""
    return [r for r in results if r.ok and (r.has_vault or not need_vault)]


def _shred_file(path: Path) -> None:
    """Overwrite ``path`` with random bytes, then unlink it.

    A single random-overwrite pass before unlinking makes casual on-disk recovery of
    the ciphertext meaningfully harder on the common case; it is not a guarantee
    against a forensic adversary with the raw device (that is what keeping the key
    off-box and the replica elsewhere is for), but it is the right, honest local step
    (defense in depth). The file's bytes are never read or logged (no-outing rule).
    """
    size = path.stat().st_size
    with open(path, "r+b") as handle:
        remaining = size
        while remaining > 0:
            chunk = min(_SHRED_CHUNK, remaining)
            handle.write(secrets.token_bytes(chunk))
            remaining -= chunk
        handle.flush()
        os.fsync(handle.fileno())
    path.unlink()


def plan_lockdown(archive: Archive) -> list[str]:
    """The human-readable steps a lockdown *would* perform (dry-run, no side effects).

    Reads the archive's lockdown policy and reports, in order, what stopping
    disclosure and (if configured) shredding the vault would do — including which
    off-box replicas must verify first and how many are required. Purely descriptive:
    it touches nothing (safety — a dry run is the default and it changes no state).
    """
    config = _lockdown_config(archive)
    steps: list[str] = []
    if is_locked_down(archive):
        steps.append("NOTE: archive is ALREADY in lockdown (lockdown.flag present).")
    if config.stop_disclosure:
        steps.append(
            "Write lockdown.flag into logs/ — the server will then refuse all "
            "non-PUBLIC disclosure (community/steward/sealed tiers and all privileged grants)."
        )
    else:
        steps.append("stop_disclosure is off — the disclosure freeze would be skipped.")
    if config.shred_vault:
        locs = ", ".join(config.required_replica_locations) or "(none configured!)"
        steps.append(
            f"Verify off-box replicas [{locs}] — require >= {config.min_verified_replicas} "
            "to restore clean (full fixity + present vault) BEFORE any shred."
        )
        steps.append(
            f"If verified: overwrite and unlink the local vault at {archive.vault_path}; "
            "if not verified: keep the vault (disclosure stays stopped)."
        )
    else:
        steps.append(
            "shred_vault is off — the local vault would be LEFT IN PLACE "
            "(configure lockdown.shred_vault to enable duress destruction)."
        )
    steps.append("Record a PREMIS lockdown event and print the recovery runbook.")
    return steps


def _recovery_runbook(archive: Archive, config: LockdownConfig, *, vault_shredded: bool) -> str:
    """The plain-language stand-up guidance printed after an executed lockdown."""
    lines = [
        "RECOVERY RUNBOOK — to stand this archive back up once it is safe:",
        f"  1. Confirm an off-box replica is intact: ledger verify-backup --backup <{'|'.join(config.required_replica_locations) or 'replica-root'}>",
        "  2. Propose + approve a 'stand-up' dual-control action, then run:",
        f"       ledger stand-up --root {archive.store_root.parent} --actor <steward> --execute",
    ]
    if vault_shredded:
        lines.append(
            "     Stand-up restores the local vault from a verified replica, so keep the "
            "vault KEY (LEDGER_VAULT_KEY) held apart and available."
        )
    else:
        lines.append("     The local vault was kept in place; stand-up simply lifts the freeze.")
    return "\n".join(lines)


def execute_lockdown(archive: Archive, *, actor: str, now: str) -> LockdownResult:
    """Execute the duress posture: stop disclosure, then conditionally shred the vault.

    Order is deliberate and fail-safe. The disclosure freeze is applied *first* (write
    the ``lockdown.flag`` marker) because it is instant, reversible, and loses nothing.
    Only then, and only if ``shred_vault`` is configured, is the irreversible local
    vault destruction considered — and it proceeds solely when at least
    ``min_verified_replicas`` off-box replicas verify clean *and* carry a vault to
    restore from. If they do not, the vault is kept and that refusal is itself
    recorded, so a duress trigger can never leave the archive with no identity copy at
    all (safety). Every branch records a PREMIS event and returns a no-outing-safe
    :class:`LockdownResult`.
    """
    config = _lockdown_config(archive)
    steps: list[str] = []
    disclosure_stopped = False
    vault_shredded = False
    verified = 0

    if config.stop_disclosure:
        flag = lockdown_flag_path(archive)
        archive.logs_dir.mkdir(parents=True, exist_ok=True)
        marker = json.dumps(
            {"locked_down_by": actor, "at": now, "shred_requested": config.shred_vault},
            ensure_ascii=False,
        )
        tmp = flag.with_name(f"{flag.name}.{os.getpid()}.tmp")
        tmp.write_text(marker + "\n", encoding="utf-8")
        os.replace(tmp, flag)
        disclosure_stopped = True
        steps.append("stopped non-PUBLIC disclosure (lockdown.flag written)")

    if config.shred_vault:
        results = _verify_replicas(config)
        clean = _clean_replicas(results, need_vault=archive.vault_path.exists())
        verified = len(clean)
        if verified >= config.min_verified_replicas:
            if archive.vault_path.exists():
                _shred_file(archive.vault_path)
                vault_shredded = True
                steps.append(f"shredded local vault after {verified} verified off-box replica(s)")
            else:
                steps.append("local vault already absent; nothing to shred")
        else:
            steps.append(
                f"REFUSED to shred: only {verified} of {config.min_verified_replicas} "
                "required replicas verified clean — local vault KEPT"
            )
            _record_event(
                archive,
                event_type=PremisEventType.LOCKDOWN,
                actor=actor,
                outcome="failure",
                detail=(
                    f"shred refused; {verified}/{config.min_verified_replicas} replicas verified; "
                    "disclosure stopped; vault kept"
                ),
                now=now,
            )
            raise LedgerError(
                f"lockdown stopped disclosure but REFUSED to shred: only {verified} of "
                f"{config.min_verified_replicas} required off-box replicas verified clean "
                "(the vault was kept — never destroy the only copy)"
            )

    _record_event(
        archive,
        event_type=PremisEventType.LOCKDOWN,
        actor=actor,
        outcome="success",
        detail=(
            f"disclosure_stopped={disclosure_stopped}; vault_shredded={vault_shredded}; "
            f"verified_replicas={verified}"
        ),
        now=now,
    )
    return LockdownResult(
        action="lockdown",
        dry_run=False,
        disclosure_stopped=disclosure_stopped,
        vault_shredded=vault_shredded,
        verified_replicas=verified,
        steps=tuple(steps),
        runbook=_recovery_runbook(archive, config, vault_shredded=vault_shredded),
    )


def execute_stand_up(archive: Archive, *, actor: str, now: str) -> LockdownResult:
    """Lift the duress posture: restore the vault from a verified replica, drop the flag.

    The exact inverse of :func:`execute_lockdown`. If the local vault was shredded, it
    is restored by copying an *verified-clean* off-box replica's encrypted vault back
    into place — so a false-alarm lockdown is fully recoverable — and only then is the
    ``lockdown.flag`` removed and disclosure resumed. If no replica can be verified but
    the local vault is still present (a shred-less freeze), the freeze is simply
    lifted. Records a PREMIS stand-up event; never reads or logs a vault byte
    (no-outing rule).
    """
    config = _lockdown_config(archive)
    steps: list[str] = []
    restored = False

    if not archive.vault_path.exists() and config.required_replica_locations:
        results = _verify_replicas(config)
        clean = _clean_replicas(results, need_vault=True)
        if len(clean) < config.min_verified_replicas:
            _record_event(
                archive,
                event_type=PremisEventType.STANDUP,
                actor=actor,
                outcome="failure",
                detail=(
                    f"restore refused; {len(clean)}/{config.min_verified_replicas} replicas "
                    "verified with a vault present"
                ),
                now=now,
            )
            raise LedgerError(
                f"stand-up cannot restore the vault: only {len(clean)} of "
                f"{config.min_verified_replicas} off-box replicas verified with a vault present"
            )
        source = Path(clean[0].location) / "identity.vault"
        archive.vault_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, archive.vault_path)
        restored = True
        steps.append(f"restored local vault from verified replica at {clean[0].location}")

    flag = lockdown_flag_path(archive)
    if flag.exists():
        flag.unlink()
        steps.append("removed lockdown.flag — non-PUBLIC disclosure resumes")
    else:
        steps.append("no lockdown.flag present; disclosure was already open")

    _record_event(
        archive,
        event_type=PremisEventType.STANDUP,
        actor=actor,
        outcome="success",
        detail=f"vault_restored={restored}; flag_removed=True",
        now=now,
    )
    return LockdownResult(
        action="stand-up",
        dry_run=False,
        disclosure_stopped=False,
        vault_shredded=False,
        verified_replicas=0,
        steps=tuple(steps),
        runbook="Archive stood back up: disclosure resumed"
        + ("; vault restored from an off-box replica." if restored else "."),
    )
