"""Public transparency attestations — the archive proves its own health (EXP-01).

``/proof`` used to be prose: a page telling a visitor the no-outing guarantee is
tested, without anything a visitor could independently check. This module turns
that into a small, dated, optionally *signed* document a contributor, a partner,
or a rival fork can fetch and verify themselves — "you can check" instead of "we
audit" (docs/ideation/03-expansions.md, EXP-01).

Two things this deliberately is **not**, both by design:

* **Not a live computation on every request.** Fixity audits re-hash every byte of
  every stored payload (:meth:`~ledger.ingest.Archive.audit_fixity`); doing that on
  an unauthenticated GET would make ``/proof`` an expensive, unauthenticated lever
  on the archive's disk and CPU. Instead ``ledger attest-health`` (a steward-run
  command, meant for a cron job) computes and signs one attestation; the server
  only ever serves the most recently published file (see :mod:`ledger.server`).
* **Not a bag/record count.** The archive's own anti-enumeration convention
  (no-outing rule; see the ``P2-2`` references throughout :mod:`ledger.server`)
  keeps absolute counts steward-only everywhere else, because a public counter
  ticking up over time lets an outsider infer *when* a record — possibly a sealed
  one — was added, and correlate that against a contributor's real-world timeline.
  A per-bag or per-log breakdown has the same shape of leak. This module instead
  publishes a single opaque :func:`chain_head_summary`: it changes the instant any
  log anywhere in the archive is rewritten, so two dated attestations are still
  enough to catch a rollback (the "excellent" bar in the ideation note), without
  ever revealing how many bags or logs exist.

Signing uses ``ssh-keygen -Y sign`` (OpenSSH's signature format) so a steward signs
attestations with a key they already have, and a verifier checks them with
``ssh-keygen -Y verify`` plus an "allowed signers" line naming the steward's public
key — no new runtime dependency, no bespoke crypto (see ``docs/VERIFYING-ATTESTATIONS.md``).
Signing is optional: an archive with no configured key still publishes an
attestation, just an unsigned one, so this never blocks a fresh install.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ledger import __version__ as _LEDGER_VERSION
from ledger.errors import LedgerError
from ledger.ingest import Archive
from ledger.metadata.premis import PremisLog
from ledger.models import PremisEvent, canonical_json

__all__ = [
    "ATTESTATION_SCHEMA_VERSION",
    "SIGNATURE_NAMESPACE",
    "HealthAttestation",
    "build_attestation",
    "chain_head_summary",
    "sign_attestation",
]

# Bumped whenever the published shape changes, so a third party's verifier can tell
# which fields to expect (evolvability, the same convention as Config/HandoffManifest).
ATTESTATION_SCHEMA_VERSION: int = 1

# The ``-n`` namespace ``ssh-keygen -Y sign``/``verify`` is scoped to. Binding it
# stops a health-attestation signature from being replayed as, say, a git commit
# signature or vice versa (the namespace is folded into what is actually signed).
SIGNATURE_NAMESPACE: str = "ledger-health-attestation"

# The sentinel "start of history" hash folded into the first entry of any log this
# module chains — a fixed, well-known value rather than a magic empty string.
_GENESIS: str = "0" * 64

_ATTESTATIONS_DIRNAME = "attestations"
_LATEST_FILENAME = "latest.json"


def _log_head(events: list[PremisEvent]) -> str:
    """A single hash committing to one append-only PREMIS log's full history.

    Each step folds the previous step's hash into the next entry before hashing
    (the same shape as a git commit or a blockchain block), so changing, removing,
    or reordering *any* past entry changes the final head — this is computed fresh
    from current content every time, nothing is persisted, so it works whether or
    not the log itself stores a chain link. An empty log's head is the genesis
    sentinel, distinguishing "no history yet" from any real history.
    """
    head = _GENESIS
    for event in events:
        payload = canonical_json({**event.to_dict(), "prevHead": head})
        head = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return head


def _every_log_head(archive: Archive) -> dict[str, str]:
    """Every log's current head, keyed by bag id or archive-level log filename.

    Internal: this per-source breakdown names bags and log files, which is exactly
    the enumeration :func:`chain_head_summary` exists to avoid publishing. Kept
    private to this module rather than exported for a steward view, so there is
    only one path (the summary) a caller can reach for (least surprise).
    """
    heads: dict[str, str] = {}
    if archive.bags_dir.exists():
        for bag_path in sorted(p for p in archive.bags_dir.iterdir() if p.is_dir()):
            heads[bag_path.name] = _log_head(archive.record_events(bag_path.name))
    if archive.logs_dir.exists():
        for log_path in sorted(archive.logs_dir.glob("*.premis.json")):
            try:
                events = PremisLog.read(log_path).events
            except (LedgerError, ValueError, OSError):
                continue
            heads[log_path.name] = _log_head(events)
    return heads


def chain_head_summary(archive: Archive) -> str:
    """One opaque hash committing to every log's history in ``archive``.

    Safe to publish to anyone: it changes the instant any bag's or archive-level
    log's history is added to or rewritten, but — unlike the per-log heads it is
    built from — it reveals neither how many bags or logs exist nor which ones
    they are (no-outing / anti-enumeration; see the module docstring). Comparing
    this value across two dated attestations is how a third party who trusts
    nothing but the signature detects a rolled-back archive.
    """
    heads = sorted(_every_log_head(archive).values())
    return hashlib.sha256(canonical_json(heads).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class HealthAttestation:
    """A dated, publishable statement of archive health (EXP-01).

    Deliberately narrow: every field here is safe to hand to an anonymous visitor
    (see the module docstring for what was left out, and why). ``signature`` and
    ``signature_format`` are ``None`` for an attestation nobody has signed yet.
    """

    schema_version: int
    archive_name: str
    generated_at: str
    software_version: str
    fixity_ok: bool
    chain_head_summary: str
    signature: str | None = None
    signature_format: str | None = None

    def _unsigned_dict(self) -> dict[str, object]:
        """The fields a signature covers — everything except the signature itself."""
        return {
            "schema_version": self.schema_version,
            "archive_name": self.archive_name,
            "generated_at": self.generated_at,
            "software_version": self.software_version,
            "fixity_ok": self.fixity_ok,
            "chain_head_summary": self.chain_head_summary,
        }

    def signing_payload(self) -> bytes:
        """The exact bytes a signature is computed over (canonical JSON, UTF-8).

        Signing and verifying must hash identically, so this is the single source
        both :func:`sign_attestation` and a third party's verifier use.
        """
        return canonical_json(self._unsigned_dict()).encode("utf-8")

    def to_dict(self) -> dict[str, object]:
        """The full JSON-ready mapping, signature included when present."""
        body = self._unsigned_dict()
        if self.signature is not None:
            body["signature"] = {"format": self.signature_format, "value": self.signature}
        return body

    def to_json(self) -> str:
        """Canonical JSON — the exact bytes written to disk and served at ``/proof``."""
        return canonical_json(self.to_dict())

    def signed(self, *, signature: str, signature_format: str) -> HealthAttestation:
        """Return a copy of this attestation carrying a signature."""
        return HealthAttestation(
            schema_version=self.schema_version,
            archive_name=self.archive_name,
            generated_at=self.generated_at,
            software_version=self.software_version,
            fixity_ok=self.fixity_ok,
            chain_head_summary=self.chain_head_summary,
            signature=signature,
            signature_format=signature_format,
        )

    @classmethod
    def from_json(cls, text: str) -> HealthAttestation:
        """Reconstruct an attestation from :meth:`to_json` output."""
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("attestation JSON must be an object")
        sig = data.get("signature")
        signature = None
        signature_format = None
        if isinstance(sig, dict):
            signature = sig.get("value")
            signature_format = sig.get("format")
        return cls(
            schema_version=int(data["schema_version"]),
            archive_name=str(data["archive_name"]),
            generated_at=str(data["generated_at"]),
            software_version=str(data["software_version"]),
            fixity_ok=bool(data["fixity_ok"]),
            chain_head_summary=str(data["chain_head_summary"]),
            signature=str(signature) if signature is not None else None,
            signature_format=str(signature_format) if signature_format is not None else None,
        )


def build_attestation(archive: Archive, *, now: str) -> HealthAttestation:
    """Compute an unsigned :class:`HealthAttestation` for ``archive`` as of ``now``.

    Runs a full fixity audit (:meth:`Archive.audit_fixity`), so — like
    ``ledger audit`` — this re-hashes every stored payload and is meant to be run
    on a schedule, not per HTTP request (see the module docstring).
    """
    reports = archive.audit_fixity()
    fixity_ok = all(report.ok for _name, report in reports)
    return HealthAttestation(
        schema_version=ATTESTATION_SCHEMA_VERSION,
        archive_name=archive.config.archive_name,
        generated_at=now,
        software_version=_LEDGER_VERSION,
        fixity_ok=fixity_ok,
        chain_head_summary=chain_head_summary(archive),
    )


def sign_attestation(attestation: HealthAttestation, key_path: Path) -> HealthAttestation:
    """Sign ``attestation`` with the SSH private key at ``key_path``.

    Shells out to ``ssh-keygen -Y sign`` (OpenSSH >= 8.2): no new runtime
    dependency, and a steward signs with a key they can already generate, back up,
    and rotate the way they would any other SSH key. Raises :class:`LedgerError`
    naming the failure (never the key's contents) if signing fails — a
    misconfigured or passphrase-locked key must not silently publish an unsigned
    attestation as if it were signed.
    """
    ssh_keygen = shutil.which("ssh-keygen")
    if ssh_keygen is None:
        raise LedgerError("ssh-keygen was not found on PATH; cannot sign the attestation")
    payload = attestation.signing_payload()
    with tempfile.TemporaryDirectory() as tmp_dir:
        data_path = Path(tmp_dir) / "attestation.json"
        data_path.write_bytes(payload)
        result = subprocess.run(  # noqa: S603 - resolved executable, fixed argv, no shell
            [
                ssh_keygen,
                "-Y",
                "sign",
                "-f",
                str(key_path),
                "-n",
                SIGNATURE_NAMESPACE,
                str(data_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise LedgerError(f"ssh-keygen signing failed: {detail}")
        sig_path = Path(str(data_path) + ".sig")
        try:
            signature = sig_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise LedgerError(f"ssh-keygen did not produce a signature: {exc}") from exc
    return attestation.signed(signature=signature, signature_format="ssh")


def attestations_dir(archive: Archive) -> Path:
    """Where published attestations live under ``archive``'s store root."""
    return archive.store_root / _ATTESTATIONS_DIRNAME


def latest_attestation_path(archive: Archive) -> Path:
    """The well-known path :mod:`ledger.server` reads to serve ``/proof``'s JSON."""
    return attestations_dir(archive) / _LATEST_FILENAME


def publish_attestation(archive: Archive, attestation: HealthAttestation) -> Path:
    """Write ``attestation`` to disk: a dated file plus the ``latest.json`` pointer.

    Keeping every dated attestation (not just the latest) is what lets a third
    party who saved a copy compare two of them later and catch a rollback even if
    the archive's own history no longer shows one (the "excellent" bar in the
    ideation note) — ``latest.json`` alone could be silently regenerated to hide
    that a rollback ever happened. Both writes are atomic (temp file + rename), so
    a reader never observes a half-written attestation.
    """
    out_dir = attestations_dir(archive)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = attestation.generated_at.replace(":", "-")
    dated_path = out_dir / f"{stamp}.json"
    data = attestation.to_json().encode("utf-8")
    for path in (dated_path, latest_attestation_path(archive)):
        tmp = path.with_name(f"{path.name}.tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
    return latest_attestation_path(archive)
