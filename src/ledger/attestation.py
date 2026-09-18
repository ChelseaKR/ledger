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
from enum import StrEnum
from pathlib import Path

from ledger import __version__ as _LEDGER_VERSION
from ledger.errors import LedgerError
from ledger.fixity import FixityStatus
from ledger.ingest import Archive
from ledger.metadata.premis import PremisLog
from ledger.models import PremisEvent, canonical_json

__all__ = [
    "ATTESTATION_SCHEMA_VERSION",
    "SIGNATURE_NAMESPACE",
    "FixityDisclosure",
    "HealthAttestation",
    "build_attestation",
    "chain_head_summary",
    "sign_attestation",
]

# Bumped whenever the published shape changes, so a third party's verifier can tell
# which fields to expect (evolvability, the same convention as Config/HandoffManifest).
#
# **2 adds ``fixity`` and redefines ``fixity_ok``** (#205). See
# :class:`FixityDisclosure` for what is now said, and
# ``docs/VERIFYING-ATTESTATIONS.md`` for what a verifier written against 1 should do.
ATTESTATION_SCHEMA_VERSION: int = 2


class FixityDisclosure(StrEnum):
    """What a published attestation says about the archive's integrity.

    Four words, because a reader needs four. :class:`ledger.fixity.FixityStatus`
    is right to hold three states, and ``/status`` already found that an archive
    with no bags and an archive with one unverifiable bag are both ``UNVERIFIED``
    and are not the same sentence: the first has nothing to check, the second has
    something it could not check. This is the same split, in a published
    vocabulary.

    Until schema 2 an attestation said one thing, ``fixity_ok``, and over an
    archive holding **no bags at all** it said :data:`True` — vacuously, since
    ``all(...)`` is true over an empty sequence — which ``/proof`` rendered to an
    anonymous visitor as *"this archive passed every integrity check"* (#205).
    """

    #: At least one bag was checked, and every checked bag passed.
    VERIFIED = "verified"
    #: At least one bag failed its checksums.
    FAILED = "failed"
    #: Bags exist, and at least one declared no files to check, so it could not be
    #: verified. Not a failure and not a pass — the state #206 added the vocabulary
    #: for, and the state an emptied manifest produces.
    COULD_NOT_VERIFY = "could-not-verify"
    #: The archive holds no bags at all. There was nothing to check, so nothing was
    #: checked, and this attestation makes no claim about any stored byte.
    NOTHING_TO_VERIFY = "nothing-to-verify"
    #: Read back from a schema-1 document, which had no field that could say which
    #: of the four this was. Never produced by :func:`build_attestation`; see
    #: :meth:`HealthAttestation.from_json`.
    UNSTATED = "unstated"


# The ``-n`` namespace ``ssh-keygen -Y sign``/``verify`` is scoped to. Binding it
# stops a health-attestation signature from being replayed as, say, a git commit
# signature or vice versa (the namespace is folded into what is actually signed).
SIGNATURE_NAMESPACE: str = "ledger-health-attestation"

# The sentinel "start of history" hash folded into the first entry of any log this
# module chains — a fixed, well-known value rather than a magic empty string.
_GENESIS: str = "0" * 64

#: A bag's own PREMIS log filename. Mirrors ``ledger.ingest._PREMIS_FILENAME``;
#: named here rather than importing a private so this module keeps its own
#: dependency surface.
_PREMIS_FILENAME = "premis.json"

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


def _read_or_refuse(log_path: Path) -> list[PremisEvent]:
    """Read one PREMIS log's events, or raise rather than let damage go unstated.

    The strict counterpart to :meth:`~ledger.ingest.Archive.record_events`. That reader
    is deliberately lenient because it feeds a browse surface where one damaged bag must
    not blank the page; this one feeds a signed public claim, where a damaged bag must
    not be summarized at all.
    """
    try:
        return list(PremisLog.read(log_path).events)
    except (LedgerError, ValueError, OSError) as exc:
        raise LedgerError(
            f"cannot attest: PREMIS log is present but unreadable: {log_path.name}. "
            "An attestation states what every log's history is; an unreadable log "
            "makes that unknown, and publishing the genesis head for it would claim "
            "the log was empty. Repair or restore the log, then re-run."
        ) from exc


def _every_log_head(archive: Archive) -> dict[str, str]:
    """Every log's current head, keyed by bag id or archive-level log filename.

    Internal: this per-source breakdown names bags and log files, which is exactly
    the enumeration :func:`chain_head_summary` exists to avoid publishing. Kept
    private to this module rather than exported for a steward view, so there is
    only one path (the summary) a caller can reach for (least surprise).

    **Fails closed on a log it cannot read.** A log that is *absent* genuinely means
    "no history yet", and :func:`_log_head` returns the genesis sentinel for it. A log
    that is *present but unreadable* means the history is **unknown**, which is not the
    same statement -- and every way of carrying on says something false:

    * reading it through :meth:`~ledger.ingest.Archive.record_events` (which swallows a
      damaged log and returns no events, correctly, for the lenient browse surface)
      would attest the **genesis head** for it, affirmatively publishing "this bag has
      no history" over a bag whose history could not be read; and
    * skipping it would silently drop it from the summary instead.

    Either way the damage is laundered into a signed, published document whose entire
    stated purpose is that "two dated attestations are enough to catch a rollback".
    Corrupting one ``premis.json`` must not be a way to get an archive to sign a
    statement that the log was empty. So this raises, and
    :func:`build_attestation` produces nothing, which is the one honest outcome: an
    archive that cannot read its own history does not get to attest to it.
    """
    heads: dict[str, str] = {}
    if archive.bags_dir.exists():
        for bag_path in sorted(p for p in archive.bags_dir.iterdir() if p.is_dir()):
            premis_path = bag_path / _PREMIS_FILENAME
            if not premis_path.exists():
                heads[bag_path.name] = _log_head([])
                continue
            heads[bag_path.name] = _log_head(_read_or_refuse(premis_path))
    if archive.logs_dir.exists():
        for log_path in sorted(archive.logs_dir.glob("*.premis.json")):
            heads[log_path.name] = _log_head(_read_or_refuse(log_path))
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


def _required_string(data: dict[str, object], field: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"attestation field {field!r} must be a non-empty string")
    return value


def _parse_signature(value: object) -> tuple[str | None, str | None]:
    if value is None:
        return None, None
    if not isinstance(value, dict):
        raise ValueError("attestation signature must be an object")
    signature = value.get("value")
    signature_format = value.get("format")
    if not isinstance(signature, str) or not signature:
        raise ValueError("attestation signature value must be a non-empty string")
    if signature_format != "ssh":
        raise ValueError("unsupported attestation signature format")
    return signature, signature_format


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
    fixity: FixityDisclosure = FixityDisclosure.UNSTATED
    signature: str | None = None
    signature_format: str | None = None

    def _unsigned_dict(self) -> dict[str, object]:
        """The fields a signature covers — everything except the signature itself.

        ``fixity`` appears only at schema 2 and above. This is not a style choice:
        a schema-1 attestation read back off disk was signed over bytes that had
        no such key, so emitting one here would change the payload and make every
        previously published signature fail to verify. The version decides the
        shape, and the shape decides the bytes.
        """
        body: dict[str, object] = {
            "schema_version": self.schema_version,
            "archive_name": self.archive_name,
            "generated_at": self.generated_at,
            "software_version": self.software_version,
            "fixity_ok": self.fixity_ok,
            "chain_head_summary": self.chain_head_summary,
        }
        if self.schema_version >= 2:
            body["fixity"] = str(self.fixity)
        return body

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
            fixity=self.fixity,
            signature=signature,
            signature_format=signature_format,
        )

    @classmethod
    def from_json(cls, text: str) -> HealthAttestation:
        """Reconstruct an attestation from :meth:`to_json` output.

        **Both published schema versions are accepted**, and the reason is not
        politeness to old files. A steward upgrades ledger between two runs of the
        ``ledger attest-health`` cron, so the attestation on disk is schema 1 for
        as long as that cadence lasts. Rejecting it would make ``/proof`` say *"not
        yet attested"* about an archive that has published an attestation — a
        second false statement, introduced by the change meant to remove one, on
        the page an at-risk contributor reads before deciding.

        A schema-1 document carries no field that can say which of
        :class:`FixityDisclosure`'s four cases it was, so it is read as
        :data:`FixityDisclosure.UNSTATED` rather than being upgraded by guesswork.
        ``fixity_ok: true`` in a v1 document means "no stored payload failed",
        which is exactly the sentence that cannot tell a verified archive from an
        empty one; inventing ``verified`` from it here would launder the vacuity
        this version exists to end.
        """
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("attestation JSON must be an object")
        if type(data.get("schema_version")) is not int or data["schema_version"] not in (1, 2):
            raise ValueError("unsupported attestation schema_version")
        if type(data.get("fixity_ok")) is not bool:
            raise ValueError("attestation field 'fixity_ok' must be a boolean")
        chain_head = _required_string(data, "chain_head_summary")
        if len(chain_head) != 64 or any(c not in "0123456789abcdef" for c in chain_head):
            raise ValueError("attestation chain_head_summary must be a SHA-256 hex digest")
        fixity = FixityDisclosure.UNSTATED
        if data["schema_version"] >= 2:
            raw = data.get("fixity")
            # An unknown word is refused rather than degraded to UNSTATED: a schema
            # this build does not understand must not be rendered as though it had
            # merely omitted the field.
            if not isinstance(raw, str) or raw not in tuple(FixityDisclosure):
                raise ValueError("attestation field 'fixity' must be a known disclosure")
            fixity = FixityDisclosure(raw)
            if fixity is FixityDisclosure.UNSTATED:
                raise ValueError("schema 2 attestations must state a fixity disclosure")
        signature, signature_format = _parse_signature(data.get("signature"))
        return cls(
            schema_version=data["schema_version"],
            archive_name=_required_string(data, "archive_name"),
            generated_at=_required_string(data, "generated_at"),
            software_version=_required_string(data, "software_version"),
            fixity_ok=data["fixity_ok"],
            chain_head_summary=chain_head,
            fixity=fixity,
            signature=signature,
            signature_format=signature_format,
        )


def build_attestation(archive: Archive, *, now: str) -> HealthAttestation:
    """Compute an unsigned :class:`HealthAttestation` for ``archive`` as of ``now``.

    Runs a full fixity audit (:meth:`Archive.audit_fixity`), so — like
    ``ledger audit`` — this re-hashes every stored payload and is meant to be run
    on a schedule, not per HTTP request (see the module docstring).

    **An archive with nothing in it no longer attests that it passed (#205).**
    ``fixity`` is the four-state :class:`FixityDisclosure`, and ``fixity_ok`` is
    now ``fixity is VERIFIED`` — so an archive holding no bags publishes
    ``nothing-to-verify`` with ``fixity_ok: false``, rather than the ``true`` that
    ``all(...)`` returns over an empty sequence and ``/proof`` rendered as *"this
    archive passed every integrity check"*.

    The objection this overturns was a disclosure, not a contract: saying "there
    was nothing to check" states that the archive holds zero records, and absolute
    counts are steward-only (P2-2) because a public counter dates each deposit.
    **This document already gives that away.** ``chain_head_summary`` over an
    archive with no logs is the SHA-256 of ``[]`` —
    ``4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945`` — an
    identical constant for every empty archive, in an attestation that is
    published to anyone and signed. Anyone who can read this open-source
    repository can already compute it and compare. ``fixity_ok: true`` therefore
    bought no privacy at all; it only cost a false statement in a signed document,
    on the page an at-risk contributor reads before deciding whether to hand this
    archive their material.

    Refusing to attest an empty archive (the third option weighed on #205) does
    not avoid the disclosure either: an archive that publishes no attestation
    until its first record has dated that record to the attestation cadence by
    the attestation's *absence*, exactly as precisely, while also handing a fresh
    install a failing cron job.
    """
    statuses = [report.status for _name, report in archive.audit_fixity()]
    if not statuses:
        fixity = FixityDisclosure.NOTHING_TO_VERIFY
    elif any(status is FixityStatus.FAILED for status in statuses):
        fixity = FixityDisclosure.FAILED
    elif all(status is FixityStatus.VERIFIED for status in statuses):
        fixity = FixityDisclosure.VERIFIED
    else:
        fixity = FixityDisclosure.COULD_NOT_VERIFY
    return HealthAttestation(
        schema_version=ATTESTATION_SCHEMA_VERSION,
        archive_name=archive.config.archive_name,
        generated_at=now,
        software_version=_LEDGER_VERSION,
        # Redefined at schema 2, deliberately and under a moved version: in schema
        # 1 this was `all(status is VERIFIED for ...)`, which is the fold that is
        # vacuously true over no bags. A verifier written against schema 1 that
        # ignores `schema_version` now reads `false` over an empty archive and may
        # alarm. That is the correct direction to be wrong: alarming that nothing
        # was verified is safe, and reassuring someone that everything was is not.
        fixity_ok=fixity is FixityDisclosure.VERIFIED,
        fixity=fixity,
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
        try:
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
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
        except subprocess.TimeoutExpired as exc:
            raise LedgerError("ssh-keygen signing timed out") from exc
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
