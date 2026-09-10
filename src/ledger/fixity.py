"""Hashing and checksum verification — the integrity floor of the archive.

Every other preservation layer (the content-addressed store, BagIt packaging,
replication audits) leans on the primitives here. Two design choices serve named
quality attributes:

* **Dual-algorithm support** (SHA-256 *and* BLAKE2b) -> integrity and redundancy:
  a single weakened or backdoored algorithm cannot mask tampering, because an
  independent digest must agree too.
* **Constant-memory streaming** (fixed-size chunks) -> efficiency and scalability:
  a multi-gigabyte oral-history video is hashed without ever being held in RAM.
  This holds for *hashing*, which is all this module does. It is not an end-to-end
  guarantee for the whole pipeline: at-rest encryption of a SEALED payload has no
  streaming path (Fernet cannot stream), costs about 7.4x the payload in peak RSS,
  and is size-capped for exactly that reason -- see ADR 0011.

No-outing: nothing here ever reads or emits file *contents*. It emits hex digests,
relative paths, and pass/fail outcomes only — never a byte of payload, never an
identity.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from ledger.models import FixityResult, HashAlgo, HoldingKind

if TYPE_CHECKING:  # pragma: no cover - typing only
    from hashlib import _Hash

# 1 MiB read window: large enough to amortize syscalls, small enough to keep
# memory flat regardless of file size (efficiency, scalability).
CHUNK_SIZE: int = 1024 * 1024


def _new_hasher(algo: HashAlgo) -> _Hash:
    """Construct a fresh hashlib object for ``algo``.

    Centralizing construction keeps the algorithm-to-constructor mapping in one
    place (analyzability) and guards against an unknown algorithm slipping through.
    """
    if algo is HashAlgo.SHA256:
        return hashlib.sha256()
    if algo is HashAlgo.BLAKE2B:
        # hashlib.new keeps the return type uniform (HASH) across algorithms.
        return hashlib.new("blake2b")
    raise ValueError(f"unsupported hash algorithm: {algo!r}")


def hash_bytes(data: bytes, algo: HashAlgo) -> str:
    """Return the hex digest of ``data`` under ``algo``."""
    hasher = _new_hasher(algo)
    hasher.update(data)
    return hasher.hexdigest()


def hash_file(path: Path, algo: HashAlgo) -> str:
    """Return the hex digest of the file at ``path`` under ``algo``.

    Streams the file in :data:`CHUNK_SIZE` windows so memory stays constant no
    matter the file size (efficiency, scalability).
    """
    hasher = _new_hasher(algo)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def hash_file_multi(path: Path, algos: Iterable[HashAlgo]) -> dict[HashAlgo, str]:
    """Return ``{algo: hex_digest}`` for every requested algorithm in one pass.

    The file is read exactly once and each chunk is fed to every hasher, so
    computing both manifests costs one disk read rather than two (efficiency).
    Deduplicates the requested algorithms while preserving first-seen order.
    """
    seen: dict[HashAlgo, _Hash] = {}
    for algo in algos:
        if algo not in seen:
            seen[algo] = _new_hasher(algo)
    if not seen:
        return {}
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            for hasher in seen.values():
                hasher.update(chunk)
    return {algo: hasher.hexdigest() for algo, hasher in seen.items()}


def verify_file(path: Path, algo: HashAlgo, expected: str) -> FixityResult:
    """Re-hash ``path`` and compare against ``expected``.

    Returns a :class:`~ledger.models.FixityResult` rather than raising, so a
    caller auditing many files can collect every outcome before deciding how to
    react (failure transparency). The ``path`` recorded is exactly what was
    passed in.
    """
    actual = hash_file(path, algo)
    return FixityResult(path=str(path), algo=algo, expected=expected, actual=actual)


class FixityStatus(StrEnum):
    """What a fixity audit ended in. Three states, and the third is load-bearing.

    ``all(result.ok for result in results)`` is :data:`True` over an empty list, so
    for as long as this report had only ``ok``, a set with **nothing in it** was
    indistinguishable from a set that was checked and passed. That is the whole
    defect: "every file matched" and "no file was looked at" are different facts and
    the second one must not be renderable as the first.

    Flipping the empty case to a *failure* would be the same defect wearing the other
    mask -- it would report damage where there is only absence, and an archive that
    genuinely holds nothing yet is not corrupt. So there are three outcomes, in the
    same shape :class:`ledger.checkup.CheckStatus` and
    :class:`ledger.drill.DrillOutcome` already use.
    """

    #: Files were checked and every one matched its expected digest.
    VERIFIED = "verified"
    #: Files were checked and at least one did not match.
    FAILED = "failed"
    #: Nothing was checked. Not a pass, not a failure, and never rendered as either.
    UNVERIFIED = "could-not-verify"
    #: There was nothing to check, **by declaration** — a physical holding, whose
    #: content is a box in somebody's flat and whose bytes ledger has never had
    #: (#188). Distinct from :data:`UNVERIFIED`, and the distinction is the whole
    #: point: "nothing was checked" is an alarm a steward must act on, and "there is
    #: nothing here to check" is the expected, healthy state of a catalogue entry.
    #: Folding them together would either alarm on every shoebox forever, which
    #: retires the alarm, or silence the alarm where it matters.
    #:
    #: **Only** :func:`holding_status` and :func:`overall_holding_status` return
    #: this. :attr:`AuditReport.status` and :func:`overall_status` cannot: they
    #: answer "did the bytes on disk match their manifest", which is a question
    #: about stored bytes and has no not-applicable answer.
    #: ``tests/test_physical_holdings.py`` pins that separation, because the value
    #: of a fourth state is entirely in nothing else learning to return it.
    NOT_APPLICABLE = "not-applicable"


@dataclass(frozen=True)
class AuditReport:
    """The aggregate outcome of verifying a set of files against a manifest.

    Carries every individual :class:`~ledger.models.FixityResult` so a steward
    can see exactly which objects drifted (inspectability), not merely a count.

    :attr:`status` is the honest verdict and :attr:`ok` is the narrow question "did
    this demonstrate integrity" -- true only for :data:`FixityStatus.VERIFIED`. A
    caller that needs to tell "nothing was checked" apart from "something broke"
    reads ``status`` (or :attr:`checked`); a caller that only needs "may I rely on
    this" reads ``ok`` and gets the safe answer for an empty report without having to
    know the trap exists.
    """

    results: list[FixityResult]

    @property
    def status(self) -> FixityStatus:
        """Verified, failed, or -- over an empty result set -- could-not-verify."""
        if not self.results:
            return FixityStatus.UNVERIFIED
        if all(result.ok for result in self.results):
            return FixityStatus.VERIFIED
        return FixityStatus.FAILED

    @property
    def ok(self) -> bool:
        """True only if files were checked *and* every one matched its digest.

        An empty report is deliberately **not** ``ok``: nothing was demonstrated, and
        every caller in this codebase reads ``ok`` as "this copy is good". Use
        :attr:`status` to tell an empty audit apart from a failing one -- ``not ok``
        alone does not mean damage.
        """
        return self.status is FixityStatus.VERIFIED

    @property
    def failed(self) -> list[FixityResult]:
        """The subset of results whose digest did not match (the corrupt ones)."""
        return [result for result in self.results if not result.ok]

    @property
    def checked(self) -> int:
        """How many files were verified."""
        return len(self.results)


def audit_files(base_dir: Path, manifest: Mapping[str, str], algo: HashAlgo) -> AuditReport:
    """Verify each file named in ``manifest`` under ``base_dir``.

    ``manifest`` maps a relative path to its expected hex digest. Every entry is
    verified and an :class:`AuditReport` returned; results are ordered by relative
    path so two runs over the same tree produce identical reports (reproducibility,
    inspectability).

    An empty ``manifest`` yields a report whose :attr:`AuditReport.status` is
    :data:`FixityStatus.UNVERIFIED`, never a passing one -- a manifest that declares
    nothing proves nothing about ``base_dir``.
    """
    results = [
        verify_file(base_dir / relpath, algo, expected)
        for relpath, expected in sorted(manifest.items())
    ]
    return AuditReport(results=results)


def overall_status(reports: Iterable[AuditReport]) -> FixityStatus:
    """Fold a set of per-bag audits into one verdict for a whole archive.

    :class:`AuditReport` closes the vacuous-pass hole for the files inside *one*
    bag. This closes it for the *set* of bags, which is a separate hole with the
    same shape: every caller that summarised a sweep wrote ``all(report.ok ...)``
    or ``failed == 0``, and both are vacuously true over an empty sequence -- so an
    archive nobody looked at rendered as an archive that passed.

    Three outcomes, and which one wins is deliberate:

    * an **empty** sweep is :data:`FixityStatus.UNVERIFIED`. Nothing was checked;
      that is neither a pass nor damage, and an archive that genuinely holds
      nothing yet is not corrupt.
    * a **failure dominates**, because damage is the fact a reader must act on
      first, and one intact bag does not make a corrupt one intact.
    * an **unverifiable** bag among otherwise-passing ones is
      :data:`FixityStatus.UNVERIFIED`, never a pass -- the same rule one level down,
      applied one level up.

    The whole iterable is consumed rather than short-circuited on the first
    failure, so a caller passing a generator that is also doing the I/O gets every
    bag audited whatever the verdict turns out to be.
    """
    statuses = [report.status for report in reports]
    if not statuses:
        return FixityStatus.UNVERIFIED
    if any(status is FixityStatus.FAILED for status in statuses):
        return FixityStatus.FAILED
    if any(status is FixityStatus.UNVERIFIED for status in statuses):
        return FixityStatus.UNVERIFIED
    return FixityStatus.VERIFIED


def holding_status(kind: HoldingKind, report: AuditReport) -> FixityStatus:
    """The verdict about one record's **content**, given what kind of holding it is.

    :attr:`AuditReport.status` answers a narrower question than a steward is
    asking. It says whether the bytes in a bag matched their manifest — and a
    physical holding's bag is full of bytes: ``record.json``, ``premis.json``, the
    Dublin Core sidecar, the manifests themselves. Those verify, so the bag
    verifies, so before #188 an undigitized shoebox printed ``PASS`` in the same
    column as a fully re-hashed video, which is exactly the reading the issue was
    filed to prevent.

    So the content verdict is a function of two things, not one:

    * a **failure always dominates**. A physical record whose ``record.json`` has
      been altered is a real, actionable failure and must not be filed under "not
      applicable" — that would make ``not_applicable`` a place to hide damage,
      which is the same defect wearing the opposite mask.
    * otherwise a :attr:`~ledger.models.HoldingKind.is_physical` holding is
      :data:`FixityStatus.NOT_APPLICABLE`. Including
      :data:`~ledger.models.HoldingKind.PHYSICAL_WITH_SURROGATE`: a verified phone
      photo of a zine says the *photo* is intact and says nothing whatever about
      the zine, and "the surrogate verified, so the holding verified" is the
      inference this whole state exists to refuse.
    * a digital record keeps exactly the verdict it had before this function
      existed.

    Pure in its arguments, like everything else in this module.
    """
    if report.status is FixityStatus.FAILED:
        return FixityStatus.FAILED
    if kind.is_physical:
        return FixityStatus.NOT_APPLICABLE
    return report.status


def overall_holding_status(pairs: Iterable[tuple[HoldingKind, AuditReport]]) -> FixityStatus:
    """Fold per-record content verdicts into one verdict for a whole archive.

    The precedence is the one :func:`overall_status` uses, with the fourth state
    slotted in where it belongs rather than at either end:

    * an **empty** archive is :data:`FixityStatus.UNVERIFIED` — nothing was
      checked, as before.
    * a **failure dominates**: damage is what a reader must act on first.
    * an **unverifiable** record beats a verified one: a bag that proved nothing
      is not made healthy by a neighbour that did.
    * if anything was actually verified, and nothing failed or went unverified,
      the archive is :data:`FixityStatus.VERIFIED`. Physical records do not drag
      that down — there was never anything of theirs to verify, and an archive of
      forty zines plus one intact scan is not less healthy than one with no zines
      in it.
    * an archive where **every** record is a physical holding is
      :data:`FixityStatus.NOT_APPLICABLE`. That is the honest word for a shoebox
      catalogue: nothing is broken and nothing was demonstrated. Reporting it as
      ``VERIFIED`` is the vacuous pass this project keeps finding; reporting it as
      ``UNVERIFIED`` would tell a steward to go and repair something that is
      working exactly as designed.

    The whole iterable is consumed rather than short-circuited, so a caller
    passing a generator that is also doing the I/O gets every record audited.
    """
    statuses = [holding_status(kind, report) for kind, report in pairs]
    if not statuses:
        return FixityStatus.UNVERIFIED
    if any(status is FixityStatus.FAILED for status in statuses):
        return FixityStatus.FAILED
    if any(status is FixityStatus.UNVERIFIED for status in statuses):
        return FixityStatus.UNVERIFIED
    if any(status is FixityStatus.VERIFIED for status in statuses):
        return FixityStatus.VERIFIED
    return FixityStatus.NOT_APPLICABLE
