"""Adoption-readiness checkup: verify a live deployment against ``ADOPTING.md`` (EX6/EXP-03).

``docs/ADOPTING.md`` is a one-page operational checklist: full-disk encryption, a
vault key kept off the archive disk, TLS in front of the plain-HTTP server, and
off-box replicas. Those controls are *operational, not code* — ledger holds the
application-layer line but cannot make a steward turn on FileVault. This module is
the bridge: an **advisory** checkup that inspects the deployment it is run on and
reports, in plain language, which of those controls it can see are in place, which
are missing, and which it honestly cannot verify.

Design commitments:

* **Never fake a pass.** Every check returns one of three verdicts — ``pass``,
  ``fail``, or ``could-not-verify`` — and the third is used liberally. A heuristic
  that cannot positively confirm a control says so rather than guessing green; a
  yellow "check this yourself" is safer than a false all-clear on a control that,
  if absent, can get a contributor hurt.
* **Advisory only (EX6 scoped down).** This is a read-only report. It changes no
  config, sets no defaults, and has no ``--fix``; it does not build an OS-level
  installer. It tells a steward what to do; the steward does it.
* **No-outing rule.** The rendered report records only operational facts — paths,
  device numbers, replica counts, a bound host — never a contributor identity, a
  sealed value, or the vault key. The report is safe to keep in the archive's
  ``audits/`` directory and to share with a co-steward.

The one public entry point is :func:`run_checkup`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from ledger.ingest import Archive
from ledger.models import now_iso
from ledger.replicate import verify_replicas

# Environment variables the checkup reads. The key itself travels in
# ``LEDGER_VAULT_KEY`` (confidentiality — never argv or config); a deployment that
# holds the key in a file instead points ``LEDGER_VAULT_KEY_FILE`` at it, which lets
# the provenance check compare the key file's disk against the store's disk.
_VAULT_KEY_ENV = "LEDGER_VAULT_KEY"
_VAULT_KEY_FILE_ENV = "LEDGER_VAULT_KEY_FILE"
# The built-in server binds a host at runtime, not in config, so the TLS-exposure
# heuristic reads the host a deployment intends to serve on and whether it has
# declared a TLS-terminating proxy in front of it.
_SERVE_HOST_ENV = "LEDGER_SERVE_HOST"
_TLS_PROXY_ENV = "LEDGER_BEHIND_TLS_PROXY"
_OFFBOX_ATTESTED_ENV = "LEDGER_OFFBOX_REPLICA_ATTESTED"

# Hosts that keep the archive on loopback — safe without TLS because nothing off the
# box can reach them.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", ""})
_TRUTHY = frozenset({"1", "true", "yes", "on"})


class CheckStatus(StrEnum):
    """The verdict of one check. ``UNVERIFIED`` is a first-class, honest outcome."""

    PASS = "pass"  # noqa: S105 -- a verdict label, not a secret
    FAIL = "fail"
    UNVERIFIED = "could-not-verify"


class Readiness(StrEnum):
    """The archive's overall readiness, rolled up from every check's verdict."""

    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


@dataclass(frozen=True)
class CheckResult:
    """One check's outcome: a machine slug, a title, a verdict, and a reason.

    ``explanation`` is plain language a non-specialist steward can act on, and (by
    the no-outing rule) names only operational facts — never an identity.
    """

    check_id: str
    title: str
    status: CheckStatus
    explanation: str

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.check_id,
            "title": self.title,
            "status": self.status.value,
            "explanation": self.explanation,
        }


@dataclass
class CheckupReport:
    """The full readiness report: a dated set of check results plus a roll-up.

    ``report_path`` is populated once :func:`run_checkup` writes the Markdown copy
    into the archive's ``audits/`` directory; it is ``None`` if writing was skipped.
    """

    generated_date: str
    archive_name: str
    results: tuple[CheckResult, ...]
    report_path: Path | None = field(default=None)

    @property
    def readiness(self) -> Readiness:
        """Roll up: any failure is red; else any unverifiable check is yellow; else green."""
        statuses = {r.status for r in self.results}
        if CheckStatus.FAIL in statuses:
            return Readiness.RED
        if CheckStatus.UNVERIFIED in statuses:
            return Readiness.YELLOW
        return Readiness.GREEN

    @property
    def exit_code(self) -> int:
        """``1`` only when red (a real, seen failure); green and yellow both exit ``0``.

        A yellow is "could not verify", not "failed", so it must not break a script
        the way a genuine red control failure should (operability).
        """
        return 1 if self.readiness is Readiness.RED else 0

    def to_dict(self) -> dict[str, object]:
        return {
            "generated_date": self.generated_date,
            "archive_name": self.archive_name,
            "readiness": self.readiness.value,
            "checks": [r.to_dict() for r in self.results],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    def to_markdown(self) -> str:
        """Render the identity-free Markdown report a steward keeps and shares."""
        symbol = {
            CheckStatus.PASS: "PASS",
            CheckStatus.FAIL: "FAIL",
            CheckStatus.UNVERIFIED: "CHECK",
        }
        intro = " ".join(
            [
                "This report checks a live deployment against the operational controls in",
                "`docs/ADOPTING.md`. It is advisory: it changes nothing and sets no defaults,",
                "and it records only operational facts (paths, device ids, counts, a bound",
                "host) — never a contributor identity, a sealed value, or the vault key",
                "(no-outing rule). A `CHECK` result is a control this tool could not verify",
                "from the box it ran on; confirm it by hand rather than assuming it is fine.",
            ]
        )
        lines = [
            f"# ledger readiness checkup — {self.generated_date}",
            "",
            f"Archive: **{self.archive_name}**",
            f"Overall readiness: **{self.readiness.value.upper()}**",
            "",
            intro,
            "",
            "| Check | Result | What it means |",
            "|---|---|---|",
        ]
        for r in self.results:
            # Keep each explanation on one table cell: collapse any incidental newlines.
            cell = " ".join(r.explanation.split())
            lines.append(f"| {r.title} | {symbol[r.status]} | {cell} |")
        closing = " ".join(
            [
                "Most residual risk in the threat model is reduced by these operational",
                "choices. ledger holds the line it can hold in code; this checklist is the",
                "line you hold in deployment. Consult `docs/ADOPTING.md` and",
                "`docs/THREAT-MODEL.md` in full before trusting the system with records",
                "that can endanger the people who made them.",
            ]
        )
        lines.extend(
            [
                "",
                "---",
                "",
                closing,
                "",
            ]
        )
        return "\n".join(lines)


# --- individual checks ------------------------------------------------------


def _check_structural(archive: Archive) -> CheckResult:
    """Structural readiness: can the archive serve at all (the ``/healthz`` probe)?"""
    ready, reason = archive.check_readiness()
    if ready:
        return CheckResult(
            "structural-readiness",
            "Archive is structurally serviceable",
            CheckStatus.PASS,
            "The store and records directories are readable and any provisioned vault "
            "opens with the supplied key.",
        )
    reasons = {
        "store-unreadable": "the store root is missing or not readable",
        "records-unreadable": "the records directory is missing or not readable",
        "vault-unopenable": "a vault key is set and a vault exists, but it will not open "
        "with that key (wrong key or tampering)",
    }
    detail = reasons.get(reason, reason)
    return CheckResult(
        "structural-readiness",
        "Archive is structurally serviceable",
        CheckStatus.FAIL,
        f"The archive cannot serve: {detail}. Fix this before relying on any other check.",
    )


def _check_vault_key_provenance(store_root: Path, env: Mapping[str, str]) -> CheckResult:
    """Vault key provenance: is the key kept OFF the archive disk (ADOPTING §Host and disk)?"""
    title = "Vault key kept off the archive disk"
    check_id = "vault-key-provenance"
    key_file = env.get(_VAULT_KEY_FILE_ENV)
    if key_file:
        try:
            key_dev = os.stat(key_file).st_dev
            store_dev = os.stat(store_root).st_dev
        except OSError:
            return CheckResult(
                check_id,
                title,
                CheckStatus.UNVERIFIED,
                f"{_VAULT_KEY_FILE_ENV} is set but its path could not be inspected; "
                "confirm by hand that the key file lives on a different disk than the store.",
            )
        if key_dev == store_dev:
            return CheckResult(
                check_id,
                title,
                CheckStatus.FAIL,
                "The vault key file sits on the same disk as the archive store. An "
                "attacker who seizes the box gets the vault ciphertext AND its key — total "
                "compromise. Move the key to an external keystore, a runtime-entered "
                "passphrase, or a device you can detach.",
            )
        return CheckResult(
            check_id,
            title,
            CheckStatus.PASS,
            "The vault key file is on a different device than the archive store, so "
            "seizing the store alone does not yield the key.",
        )
    if env.get(_VAULT_KEY_ENV):
        return CheckResult(
            check_id,
            title,
            CheckStatus.PASS,
            "The vault key is supplied via the environment, not stored on disk by ledger. "
            "Confirm it is not sourced from an unencrypted env file sitting next to the "
            "vault — that would put it back on the archive disk.",
        )
    return CheckResult(
        check_id,
        title,
        CheckStatus.UNVERIFIED,
        f"No vault key is visible ({_VAULT_KEY_ENV} and {_VAULT_KEY_FILE_ENV} are both "
        "unset). A key entered at runtime or held in an external keystore cannot be seen "
        "from here; if that is your setup this is fine. If no contributors are sealed yet, "
        "no key is needed until the first identity is stored.",
    )


def _unescape_mount_field(value: str) -> str:
    """Decode the octal escapes ``/proc/mounts`` uses for spaces and tabs in paths."""
    for code, char in (("\\040", " "), ("\\011", "\t"), ("\\012", "\n"), ("\\134", "\\")):
        value = value.replace(code, char)
    return value


def _read_mount_table(platform: str) -> list[tuple[str, str]]:
    """Return ``(device, mountpoint)`` rows for the running host.

    Reads ``/proc/mounts`` on linux and parses ``mount`` output on darwin. Raises
    ``OSError`` (or a ``subprocess`` error) on any platform it cannot read, which the
    caller turns into a ``could-not-verify`` verdict rather than a fake pass. This is
    the seam tests stub to exercise that path.
    """
    if platform.startswith("linux"):
        text = Path("/proc/mounts").read_text(encoding="utf-8")
        rows: list[tuple[str, str]] = []
        for line in text.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                rows.append((parts[0], _unescape_mount_field(parts[1])))
        return rows
    if platform == "darwin":
        mount_bin = shutil.which("mount")
        if mount_bin is None:
            raise OSError("the `mount` utility was not found on PATH")
        completed = subprocess.run(  # noqa: S603 -- fixed, resolved binary; no shell, no user input
            [mount_bin],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        rows = []
        for line in completed.stdout.splitlines():
            # Format: "/dev/disk1s1 on / (apfs, local, journaled)"
            if " on " not in line:
                continue
            device, rest = line.split(" on ", 1)
            mountpoint = rest.split(" (", 1)[0]
            rows.append((device.strip(), mountpoint.strip()))
        return rows
    raise OSError(f"full-disk-encryption heuristic is not supported on {platform!r}")


def _best_mount(store_root: Path, rows: list[tuple[str, str]]) -> tuple[str, str] | None:
    """The mount whose mountpoint is the longest prefix of ``store_root``'s real path."""
    resolved = os.path.realpath(str(store_root))
    best: tuple[str, str] | None = None
    for device, mountpoint in rows:
        normalized = mountpoint.rstrip("/") or "/"
        is_prefix = resolved == normalized or resolved.startswith(
            normalized + ("" if normalized == "/" else "/")
        )
        if normalized == "/":
            is_prefix = True
        if is_prefix and (best is None or len(mountpoint) > len(best[1])):
            best = (device, mountpoint)
    return best


def _check_full_disk_encryption(store_root: Path, platform: str) -> CheckResult:
    """FDE heuristic: does the store sit on a visibly encrypted volume (ADOPTING §Host and disk)?"""
    title = "Full-disk encryption on the store host"
    check_id = "full-disk-encryption"
    try:
        rows = _read_mount_table(platform)
    except (OSError, ValueError, subprocess.SubprocessError):
        return CheckResult(
            check_id,
            title,
            CheckStatus.UNVERIFIED,
            "The mount table could not be read on this host, so FDE cannot be checked "
            "automatically. Confirm by hand that every disk holding store/, bags/, or the "
            "vault has full-disk encryption enabled — it is the floor control.",
        )
    best = _best_mount(store_root, rows)
    if best is None:
        return CheckResult(
            check_id,
            title,
            CheckStatus.UNVERIFIED,
            "No mount entry could be matched to the store path, so FDE cannot be inferred. "
            "Verify disk encryption on the store host by hand.",
        )
    device = best[0]
    lowered = device.lower()
    if platform.startswith("linux") and any(
        marker in lowered for marker in ("mapper", "crypt", "luks", "dm-")
    ):
        return CheckResult(
            check_id,
            title,
            CheckStatus.PASS,
            f"The store is on {device}, a device-mapper/LUKS volume, which indicates the "
            "underlying disk is encrypted at rest.",
        )
    if platform == "darwin":
        return CheckResult(
            check_id,
            title,
            CheckStatus.UNVERIFIED,
            f"The store is on {device}. The macOS mount table does not reveal FileVault "
            "status, so FDE cannot be confirmed from here — run `fdesetup status` to check.",
        )
    return CheckResult(
        check_id,
        title,
        CheckStatus.UNVERIFIED,
        f"The store is on {device}, which shows no visible encryption layer in the mount "
        "table. That does not prove the disk is unencrypted (encryption may sit below this "
        "layer), but it cannot be confirmed here — verify FDE on the store host by hand.",
    )


def _check_off_box_replicas(archive: Archive, env: Mapping[str, str]) -> CheckResult:
    """Verify replica bytes, then require explicit physical-independence evidence."""
    title = "Off-box replica in an independent location"
    check_id = "off-box-replicas"
    mirrors = [loc for loc in archive.config.locations if loc.kind == "mirror"]
    if not mirrors:
        return CheckResult(
            check_id,
            title,
            CheckStatus.FAIL,
            "No mirror locations are registered. Register and populate at least one "
            "independent replica before relying on this archive.",
        )
    bag_names = sorted(path.name for path in archive.bags_dir.iterdir() if path.is_dir())
    if not bag_names:
        return CheckResult(
            check_id,
            title,
            CheckStatus.UNVERIFIED,
            "Mirror locations are configured, but the archive has no bags whose replica "
            "bytes can be verified yet. Physical independence also needs confirmation.",
        )
    failing: list[str] = []
    for bag_name in bag_names:
        for status in verify_replicas(bag_name, mirrors):
            if not status.ok:
                failing.append(f"{status.location}/{bag_name}")
    if failing:
        sample = ", ".join(failing[:3])
        return CheckResult(
            check_id,
            title,
            CheckStatus.FAIL,
            f"Configured mirror copies are missing or fail fixity ({sample}). A configured "
            "path is not a backup until its bytes verify.",
        )
    names = ", ".join(sorted(loc.name for loc in mirrors))
    if env.get(_OFFBOX_ATTESTED_ENV, "").strip().lower() in _TRUTHY:
        return CheckResult(
            check_id,
            title,
            CheckStatus.PASS,
            f"Every bag verifies at mirror location(s) {names}, and physical independence "
            f"was explicitly attested with {_OFFBOX_ATTESTED_ENV}.",
        )
    return CheckResult(
        check_id,
        title,
        CheckStatus.UNVERIFIED,
        f"Every bag verifies at configured mirror location(s) {names}, but software cannot "
        "prove those paths are on another box or disaster boundary. Confirm that physical "
        f"fact, then set {_OFFBOX_ATTESTED_ENV}=true for a recorded pass.",
    )


def _check_tls_exposure(env: Mapping[str, str]) -> CheckResult:
    """TLS-exposure hint: is the server exposed off loopback without TLS (ADOPTING §Network)?"""
    title = "Off-loopback exposure only behind TLS"
    check_id = "tls-exposure"
    host = env.get(_SERVE_HOST_ENV, "127.0.0.1").strip()
    behind_tls = env.get(_TLS_PROXY_ENV, "").strip().lower() in _TRUTHY
    if host.lower() in _LOOPBACK_HOSTS:
        return CheckResult(
            check_id,
            title,
            CheckStatus.PASS,
            f"The server is bound to loopback ({host or '127.0.0.1'}), so nothing off the "
            "box can reach it. Keep it here until a TLS-terminating reverse proxy is in "
            "place, then expose deliberately.",
        )
    if behind_tls:
        return CheckResult(
            check_id,
            title,
            CheckStatus.PASS,
            f"The server binds {host} but declares a TLS-terminating reverse proxy in "
            f"front of it ({_TLS_PROXY_ENV} is set). Confirm the proxy actually terminates "
            "TLS and forwards to loopback.",
        )
    return CheckResult(
        check_id,
        title,
        CheckStatus.FAIL,
        f"The server is set to bind {host} (off loopback) but no TLS-terminating proxy is "
        f"declared ({_TLS_PROXY_ENV} is unset). The built-in server speaks plain HTTP; a "
        "network observer would read everything a legitimate viewer sees. Put a vetted TLS "
        "reverse proxy in front before exposing beyond loopback.",
    )


# --- orchestration ----------------------------------------------------------


def run_checkup(
    archive: Archive,
    *,
    env: Mapping[str, str] = os.environ,
    platform: str | None = None,
    now: str | None = None,
    write_report: bool = True,
) -> CheckupReport:
    """Run every readiness check against ``archive`` and return a :class:`CheckupReport`.

    Reads deployment facts from ``env`` (the vault-key and TLS-exposure signals) and
    the running platform (the FDE heuristic); both are injectable so the checkup is
    testable without mutating the real process environment. When ``write_report`` is
    true (the default) a dated Markdown copy is written into ``<store_root>/audits/``
    — created if absent — as ``readiness-YYYY-MM-DD.md``, and its path is recorded on
    the returned report. No check ever fakes a pass: a control that cannot be
    positively confirmed is reported ``could-not-verify`` (no-outing rule applies to
    the report itself — it names only operational facts).
    """
    plat = platform if platform is not None else sys.platform
    stamp = now if now is not None else now_iso()
    generated_date = stamp[:10]
    store_root = archive.store_root

    results = (
        _check_structural(archive),
        _check_vault_key_provenance(store_root, env),
        _check_full_disk_encryption(store_root, plat),
        _check_off_box_replicas(archive, env),
        _check_tls_exposure(env),
    )
    report = CheckupReport(
        generated_date=generated_date,
        archive_name=archive.config.archive_name,
        results=results,
    )

    if write_report:
        audits_dir = store_root / "audits"
        audits_dir.mkdir(parents=True, exist_ok=True)
        report_path = audits_dir / f"readiness-{generated_date}.md"
        report_path.write_text(report.to_markdown(), encoding="utf-8")
        report.report_path = report_path

    return report
