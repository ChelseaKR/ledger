"""OAIS information packages (ISO 14721) — the three shapes content moves through.

The Open Archival Information System reference model names three packages, and
ledger keeps them as distinct, typed objects so each ingest and access step is
traceable to a standard stage (standards-compliance, traceability):

* :class:`SIP` — *Submission* Information Package: what a contributor hands in.
  It is the only shape that may carry a :class:`ContributorIdentity`, and only
  transiently, on its way into the encrypted vault.
* :class:`AIP` — *Archival* Information Package: what is actually stored — a
  BagIt bag plus the on-disk paths of the record manifest, the Dublin Core
  sidecar, and the PREMIS log. An AIP structurally cannot carry an identity.
* The *Dissemination* Information Package (DIP) — the safe read shape — is built
  by :func:`to_dip`, a thin wrapper over the one disclosure point
  (:func:`ledger.access.disclose`) so every read funnels through a single audited
  boundary (safety).

No-outing rule: identity appears only on a :class:`SIP`, never on an :class:`AIP`
or in a DIP. The ingest pipeline (:mod:`ledger.ingest`) moves it from the SIP
into the vault and replaces it with an opaque ``identity_ref`` before anything is
written to disk.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ledger.access import disclose
from ledger.bag import Bag
from ledger.identity import ContributorIdentity
from ledger.models import DisclosedRecord, Grant, Record


@dataclass
class SIP:
    """A Submission Information Package: a record plus its raw payload files.

    This is the *only* OAIS package permitted to carry a
    :class:`~ledger.identity.ContributorIdentity`, and only in transit: the ingest
    pipeline immediately seals it into the encrypted vault and substitutes an
    opaque ``identity_ref`` on the record before any byte is persisted. ``payload``
    maps a payload-relative filename to the source file to ingest.

    No-outing: hold a SIP only as long as needed to ingest it; never log it,
    serialize it, or copy ``identity`` anywhere but the vault -> safety.
    """

    record: Record
    payload: dict[str, Path]
    identity: ContributorIdentity | None = None


@dataclass
class AIP:
    """An Archival Information Package: exactly what is stored on disk.

    Bundles the BagIt :class:`~ledger.bag.Bag` (the preserved bytes plus its
    manifests) with the on-disk paths of the three tag artifacts written beside
    the payload — the record manifest, the Dublin Core sidecar, and the PREMIS
    event log — so a steward can locate every piece of an item from one handle
    (inspectability, traceability).

    No-outing: an AIP has no identity field by construction; every artifact it
    references is verified identity-free before the AIP is returned (safety).
    """

    bag: Bag
    record: Record
    premis_path: Path
    dc_path: Path
    record_path: Path


def to_dip(
    record: Record,
    grant: Grant,
    now: str,
    *,
    conditions_met: frozenset[str] = frozenset(),
) -> DisclosedRecord:
    """Build the Dissemination Information Package for ``grant`` at ``now``.

    A DIP is the safe read shape, so this is a deliberately thin wrapper over
    :func:`ledger.access.disclose` — the single disclosure point. Naming the OAIS
    stage keeps the package vocabulary complete (standards-compliance) while
    routing every dissemination through the one audited no-outing boundary
    (safety): the returned :class:`~ledger.models.DisclosedRecord` carries no
    ``identity_ref`` and only what the grant may see at this instant.
    """
    return disclose(record, grant, now, conditions_met=conditions_met)
