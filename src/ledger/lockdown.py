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
  the local vault is kept — the archive never destroys its only copy (safety). A
  replica that is readable but holds *nothing to check* counts as unverified, not as
  clean: an empty copy is not evidence that the archive survived.

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
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ledger._filelock import file_lock
from ledger.config import Config, LockdownConfig
from ledger.errors import LedgerError
from ledger.fixity import FixityStatus
from ledger.ingest import Archive
from ledger.metadata.premis import PremisLog
from ledger.models import PremisEvent, PremisEventType

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
# One stable mutex for the *entire* lockdown/stand-up state transition. Locking
# only the PREMIS append still allowed flag/vault/log operations from opposite
# transitions to interleave into a contradictory terminal state.
_LOCKDOWN_WORKFLOW = "lockdown.workflow"
_CONFIG_FILENAME = "config.json"
# Overwrite the vault in fixed-size chunks so shredding a large vault never pulls it
# all into memory (efficiency, minimal computing).
_SHRED_CHUNK = 65536


class ArchiveLike(Protocol):
    """The slice of :class:`ledger.ingest.Archive` this module needs.

    A structural (``Protocol``) type instead of importing ``Archive`` directly:
    ``ledger.config`` imports this module for ``LockdownConfig``, and
    ``ledger.ingest`` imports ``ledger.config`` for ``Config``, so a real import of
    ``Archive`` here — even TYPE_CHECKING-guarded — completes a
    config -> lockdown -> ingest -> config cycle. Duck-typing the handful of
    attributes actually used breaks the cycle without weakening the type hints
    (``execute_lockdown``/``verify_backup_location`` still import the concrete
    ``Archive`` locally, function-scoped, where a real instance is constructed).
    ``config`` is a read-only property typed ``object``: every read of it here goes
    through ``getattr(archive.config, "lockdown", None)``, never a direct attribute
    access, so it never needs ``Config``'s own type — and a property (unlike a plain
    attribute) is covariant, so ``Archive.config: Config`` still satisfies it.
    """

    logs_dir: Path
    store_root: Path
    vault_path: Path

    @property
    def config(self) -> object:
        raise NotImplementedError


#: The ``reason`` code for a replica that was perfectly readable and held nothing to
#: check. Distinct from ``fixity-failed`` on purpose: a steward reading it must be
#: able to tell "your replica is corrupt" from "your replica is empty", because the
#: two call for opposite responses (restore from elsewhere vs. re-run the copy).
_NOTHING_VERIFIED = "nothing-verified"


@dataclass(frozen=True)
class BagFixity:
    """One replica bag's fixity outcome (name + verdict + files checked).

    ``status`` is the three-state verdict from :class:`ledger.fixity.FixityStatus`;
    ``ok`` is the narrow "this bag demonstrated integrity", true only for
    :data:`~ledger.fixity.FixityStatus.VERIFIED`. A bag that declared no files to
    check is ``UNVERIFIED`` with ``checked == 0`` — not a pass.
    """

    name: str
    ok: bool
    checked: int
    status: FixityStatus = FixityStatus.VERIFIED


@dataclass(frozen=True)
class BackupVerification:
    """The result of verifying one off-box replica location in place.

    ``ok`` is true only when the replica is *readable*, held at least one bag, and
    every bag passed full fixity; ``reason`` is a non-identity-bearing code when it is
    not. ``has_vault`` reports whether an encrypted vault is present to restore from
    (never its contents). Only bag names, counts, and the location path appear here —
    never a payload byte or an identity (no-outing rule).

    ``status`` carries the three-state verdict, because ``ok`` alone cannot say *why*
    a replica is unusable. The distinction is the point of this class:
    :data:`~ledger.fixity.FixityStatus.UNVERIFIED` means the replica was readable and
    there was **nothing in it to check** — a copy that stopped after the metadata, a
    location whose contents were wiped, a brand-new box. It is not corruption, and it
    is emphatically not proof that the archive survived. See
    :func:`verify_backup_location`.
    """

    location: str
    ok: bool
    reason: str
    bags: tuple[BagFixity, ...] = ()
    has_vault: bool = False
    status: FixityStatus = FixityStatus.FAILED

    @property
    def failures(self) -> int:
        """How many bags failed fixity."""
        return sum(1 for bag in self.bags if not bag.ok)

    @property
    def verified_bags(self) -> int:
        """How many bags actually demonstrated integrity (the load-bearing count).

        ``len(bags)`` counts what was *found*; this counts what was *proven*. A
        caller deciding whether a replica may stand in for the local copy must
        consult this (or :attr:`status`), never the length.
        """
        return sum(1 for bag in self.bags if bag.ok)

    @property
    def files_checked(self) -> int:
        """How many individual files were re-hashed across every bag."""
        return sum(bag.checked for bag in self.bags)


#: The ``reason`` code each replica verdict is reported with. ``VERIFIED`` carries no
#: reason (there is nothing to explain); the other two carry distinguishable codes so
#: a steward is never told "corrupt" about a replica that is merely empty.
_REPLICA_REASON: dict[FixityStatus, str] = {
    FixityStatus.VERIFIED: "",
    FixityStatus.FAILED: "fixity-failed",
    FixityStatus.UNVERIFIED: _NOTHING_VERIFIED,
}


def _replica_status(bags: tuple[BagFixity, ...]) -> FixityStatus:
    """Roll one replica's per-bag verdicts up into the location's own verdict.

    Failure dominates: one corrupt bag makes the replica ``FAILED`` however many
    others passed. Absence is next: a replica with no bags at all, or one whose every
    bag declared nothing to check, is ``UNVERIFIED`` — it proved nothing, which is a
    different statement from "it is broken". Only a replica that proved at least one
    bag and failed none is ``VERIFIED``.
    """
    if any(bag.status is FixityStatus.FAILED for bag in bags):
        return FixityStatus.FAILED
    if not any(bag.status is FixityStatus.VERIFIED for bag in bags):
        return FixityStatus.UNVERIFIED
    return FixityStatus.VERIFIED


def verify_backup_location(backup: Path) -> BackupVerification:
    """Verify a restored archive root at ``backup`` in place (RFC 8493 fixity).

    The shared verification core behind ``ledger verify-backup`` and the lockdown
    shred gate: it loads the replica's own config, re-points the stored (original-box)
    paths at ``backup`` so the copy on disk is what is checked, confirms the store is
    readable, then runs full fixity over every bag. Pure of side effects and of
    identity — it reports only readability, per-bag fixity, and whether a vault file
    exists (no-outing rule).

    **A replica with nothing in it is not a verified replica.** This function used to
    end in ``all_ok = all(bag.ok for bag in bags)``, and ``all([])`` is :data:`True`,
    so a location holding ``config.json`` and an ``identity.vault`` but **none of the
    archive's content** came back ``ok=True`` with an empty ``reason``. That is the
    worst possible place for a vacuous truth: this is the gate
    :func:`execute_lockdown` consults before it *irreversibly shreds the local
    identity vault*, so a partial rsync, an emptied replica disk, or a copy that
    stopped after the metadata read as "your archive survived" and authorised the
    destruction of the only real copy. It is also what ``ledger verify-backup``
    reports to a steward whose entire question is whether the backup is good.

    So the outcome is three-state (:class:`ledger.fixity.FixityStatus`), not two.
    ``UNVERIFIED``/``nothing-verified`` — readable, zero bags proven — is *not* ``ok``
    and therefore never counts toward ``min_verified_replicas``, but it is reported
    with its own reason code rather than as ``fixity-failed``, because an empty
    replica is an absence to investigate, not damage to repair. An archive that
    genuinely holds no records yet is a legitimate state; what it cannot be is
    *evidence*.
    """
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
    bags = tuple(
        BagFixity(name, report.ok, report.checked, report.status) for name, report in reports
    )
    status = _replica_status(bags)
    return BackupVerification(
        str(backup),
        ok=status is FixityStatus.VERIFIED,
        reason=_REPLICA_REASON[status],
        bags=bags,
        has_vault=has_vault,
        status=status,
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
        if self.action == "stand-up":
            # For stand-up, `disclosure_stopped` carries the inverse fact (did this
            # run actually lift a freeze that was in place), so the wording must be
            # its own, not "stopped"/"unchanged" (which describe a *lockdown*).
            bits = [f"disclosure {'resumed' if self.disclosure_stopped else 'was already open'}"]
        else:
            bits = [f"disclosure {'stopped' if self.disclosure_stopped else 'unchanged'}"]
        if self.vault_shredded:
            bits.append(f"local vault shredded after {self.verified_replicas} verified replica(s)")
        elif self.action == "lockdown":
            bits.append("local vault kept")
        return f"{self.action} executed — " + "; ".join(bits)


def lockdown_flag_path(archive: ArchiveLike) -> Path:
    """Where the lockdown marker lives for ``archive`` (its ``logs/`` state dir)."""
    return archive.logs_dir / _FLAG_FILENAME


def _workflow_lock_path(archive: ArchiveLike) -> Path:
    """Stable path whose sibling lock serializes duress state transitions."""
    return archive.logs_dir / _LOCKDOWN_WORKFLOW


def is_locked_down(archive: ArchiveLike) -> bool:
    """Whether ``archive`` is currently in lockdown (the marker is present).

    Cheap and side-effect-free so the server can call it on every request; a missing
    or unreadable marker is treated as *not* locked down (fail-open only for the
    check itself — the marker's presence is the authoritative freeze signal).
    """
    return lockdown_flag_path(archive).exists()


def _lockdown_config(archive: ArchiveLike) -> LockdownConfig:
    """The archive's configured lockdown policy, or the safe (no-shred) default."""
    configured = getattr(archive.config, "lockdown", None)
    if isinstance(configured, LockdownConfig):
        configured.validate(
            archive_locations=(archive.store_root.parent, archive.store_root, archive.vault_path)
        )
        return configured
    return LockdownConfig()


def _record_event(
    archive: ArchiveLike,
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

    Locked (:func:`ledger._filelock.file_lock`) around the read-modify-write so a
    lockdown/stand-up event can never be lost to a concurrent write to the same log
    (accountability -- this is the audit trail for the archive's duress posture).
    """
    archive.logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = archive.logs_dir / _LOCKDOWN_PREMIS
    with file_lock(log_path):
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
    """The replicas that verified clean (and, when required, carry a vault to restore).

    ``r.ok`` is now false for a replica that was readable but proved nothing (see
    :func:`verify_backup_location`), so an empty location can no longer be counted
    toward ``min_verified_replicas`` and can no longer authorise a shred.
    """
    return [r for r in results if r.ok and (r.has_vault or not need_vault)]


def _replica_diagnosis(results: list[BackupVerification]) -> str:
    """A no-outing-safe tally of why the replicas did not qualify, by reason code.

    A bare count ("0 of 1 verified") tells a steward under duress nothing about what
    to do next; ``nothing-verified`` and ``fixity-failed`` call for opposite
    responses. Emits only reason codes and counts — never a location path, a bag
    name, or an identity.
    """
    tally: dict[str, int] = {}
    for result in results:
        code = result.reason or "verified"
        tally[code] = tally.get(code, 0) + 1
    return ", ".join(f"{code}={count}" for code, count in sorted(tally.items()))


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


def plan_lockdown(archive: ArchiveLike) -> list[str]:
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


def _recovery_runbook(archive: ArchiveLike, config: LockdownConfig, *, vault_shredded: bool) -> str:
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


def execute_lockdown(archive: ArchiveLike, *, actor: str, now: str) -> LockdownResult:
    """Serialize and execute one complete lockdown transition.

    The stable workflow lock covers flag creation, replica verification, optional
    vault shredding, and PREMIS recording as one transition. A concurrent stand-up
    therefore runs wholly before or wholly after this operation, never through it.
    """
    with file_lock(_workflow_lock_path(archive)):
        return _execute_lockdown_locked(archive, actor=actor, now=now)


def _execute_lockdown_locked(archive: ArchiveLike, *, actor: str, now: str) -> LockdownResult:
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
            diagnosis = _replica_diagnosis(results)
            steps.append(
                f"REFUSED to shred: only {verified} of {config.min_verified_replicas} "
                f"required replicas verified clean ({diagnosis}) — local vault KEPT"
            )
            _record_event(
                archive,
                event_type=PremisEventType.LOCKDOWN,
                actor=actor,
                outcome="failure",
                detail=(
                    f"shred refused; {verified}/{config.min_verified_replicas} replicas verified "
                    f"({diagnosis}); disclosure stopped; vault kept"
                ),
                now=now,
            )
            raise LedgerError(
                f"lockdown stopped disclosure but REFUSED to shred: only {verified} of "
                f"{config.min_verified_replicas} required off-box replicas verified clean "
                f"({diagnosis}) — the vault was kept; never destroy the only copy"
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


def execute_stand_up(archive: ArchiveLike, *, actor: str, now: str) -> LockdownResult:
    """Serialize and execute one complete stand-up transition."""
    with file_lock(_workflow_lock_path(archive)):
        return _execute_stand_up_locked(archive, actor=actor, now=now)


def _execute_stand_up_locked(archive: ArchiveLike, *, actor: str, now: str) -> LockdownResult:
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
            diagnosis = _replica_diagnosis(results)
            _record_event(
                archive,
                event_type=PremisEventType.STANDUP,
                actor=actor,
                outcome="failure",
                detail=(
                    f"restore refused; {len(clean)}/{config.min_verified_replicas} replicas "
                    f"verified with a vault present ({diagnosis})"
                ),
                now=now,
            )
            raise LedgerError(
                f"stand-up cannot restore the vault: only {len(clean)} of "
                f"{config.min_verified_replicas} off-box replicas verified with a vault present "
                f"({diagnosis})"
            )
        source = Path(clean[0].location) / "identity.vault"
        archive.vault_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, archive.vault_path)
        restored = True
        steps.append(f"restored local vault from verified replica at {clean[0].location}")

    flag = lockdown_flag_path(archive)
    flag_removed = flag.exists()
    if flag_removed:
        flag.unlink()
        steps.append("removed lockdown.flag — non-PUBLIC disclosure resumes")
    else:
        steps.append("no lockdown.flag present; disclosure was already open")

    _record_event(
        archive,
        event_type=PremisEventType.STANDUP,
        actor=actor,
        outcome="success",
        detail=f"vault_restored={restored}; flag_removed={flag_removed}",
        now=now,
    )
    return LockdownResult(
        action="stand-up",
        dry_run=False,
        # Repurposed for this action: whether the freeze was actually lifted just
        # now (the flag existed and was removed), not "did this stop disclosure"
        # (that phrasing belongs to lockdown; see `summary()`, which branches on
        # `action` to render the right words for whichever fact this is).
        disclosure_stopped=flag_removed,
        vault_shredded=False,
        verified_replicas=0,
        steps=tuple(steps),
        runbook="Archive stood back up: disclosure resumed"
        + ("; vault restored from an off-box replica." if restored else "."),
    )
