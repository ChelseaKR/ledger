"""Run ledger's ingest pipeline over a real, openly-licensed archival corpus.

Every other proof in this repository runs on fixtures ledger wrote itself. That
is a closed loop: the fixtures embody the same assumptions as the code, so they
can only confirm them. Real archival files break preservation pipelines in ways
fixtures never reproduce — headers displaced by a wrapper, formats nobody
budgeted for, filenames the manifest grammar did not anticipate, files that are
simply truncated — and none of that is visible until real bytes go through.

This script closes that loop against the **Open Preservation Foundation
format-corpus** (https://github.com/openpreserve/format-corpus), the digital
preservation community's own reference collection of awkward files: JHOVE error
cases, a "PDF cabinet of horrors", real government PDFs that broke real
harvesters, legacy office and spreadsheet formats, JPEG 2000 masters, and a
directory of deliberately hostile filenames. It is CC0 unless otherwise stated
(see the corpus README), so it can be fetched and redistributed freely.

Design choices, and the quality attributes they serve:

* **Stdlib only, no new dependency** (ADR 0005) -> the corpus run costs the
  project nothing in dependency surface.
* **Pinned to one commit, and every file verified against its git blob SHA-1**
  -> reproducibility, and *provenance*: a run cannot quietly succeed against
  something other than the corpus it claims to have used.
* **The corpus is never committed** (it lands in a gitignored directory) ->
  the repository stays small and carries no third-party binaries.
* **Byte-identity is asserted, not assumed** -> the report proves the real bytes
  reached the pipeline by re-hashing every bagged payload against the file that
  was fetched. A preservation report that looks plausible while the data never
  actually flowed through the code is worse than no report at all.
* **What it measured is committed as evidence, and re-checked on every run.**
  The per-file verdicts and the counts derived from them are written to
  ``docs/data/real-corpus/`` (metadata and hashes only — never the files), so
  every number the write-up states is re-derived by a test from that file rather
  than typed in, and a run whose results drift from the committed evidence fails
  instead of silently measuring something new under an old heading.

No-outing: the corpus is third-party public test data with no contributor
identities, the temporary archive is created with no identity vault entries, and
the report prints only format metadata, filenames, and counts — never payload
content.

Usage::

    python tools/real_corpus.py                  # fetch (if needed), ingest, report,
                                                 # and check against committed evidence
    python tools/real_corpus.py --write-evidence # ...and rewrite the committed evidence
    python tools/real_corpus.py --fetch-only
    python tools/real_corpus.py --max-file-bytes 1000000   # a smaller sample (no
                                                           # evidence check)
"""

from __future__ import annotations

import argparse
import collections
import concurrent.futures
import datetime as _dt
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ledger.bag import validate_bag
from ledger.config import Config
from ledger.ingest import Archive
from ledger.metadata.premis import IdentificationContradiction, PremisLog
from ledger.models import OBJECT_TYPE_PAYLOAD, AccessPolicy, DublinCore, Record
from ledger.preservation import identify_file

#: The corpus, pinned. Changing this pin changes what the report measures, so it
#: is a deliberate edit with a re-run, never a floating "latest".
CORPUS_REPO = "openpreserve/format-corpus"
CORPUS_COMMIT = "366f068cec399d0cdfd61fa473de3ab6dc858098"
CORPUS_LICENCE = "CC0 unless otherwise stated (see the corpus README)"

#: Default per-file ceiling. The corpus holds ~800 MB in full, most of it a
#: handful of large video files that exercise nothing the smaller ones do not.
#: 5 MB keeps a full run around 302 MB and 679 files — a bounded sample that
#: still covers every format family in the collection.
DEFAULT_MAX_FILE_BYTES = 5 * 1024 * 1024

#: Repository furniture that is not corpus content.
SKIP_PATHS = frozenset(
    {
        ".DS_Store",
        ".gitattributes",
        ".gitignore",
        ".opf.yml",
        ".project",
        ".pydevproject",
        "README.md",
        "metadata-template.ext.md",
    }
)

DEFAULT_DEST = Path("real-corpus")

#: Where the measured evidence of the default run is committed (metadata and
#: hashes only). One file per corpus pin, so a re-pin is a new file beside the
#: old one rather than a silent overwrite of what an earlier write-up cited.
EVIDENCE_DIR = Path("docs/data/real-corpus")
DEFAULT_EVIDENCE = EVIDENCE_DIR / f"opf-format-corpus-{CORPUS_COMMIT[:8]}.json"
EVIDENCE_SCHEMA = "ledger-real-corpus-evidence/1"

#: The corpus files that are in unambiguously obsolete formats, by extension — the
#: ground truth for the at-risk advisory's *recall*. This is a property of the OPF
#: corpus, not of ledger: these are dead 1990s desktop applications (Lotus 1-2-3,
#: Quattro Pro, Access, Windows Write), a discontinued proprietary catalogue system
#: (Inmagic DB/TextWorks), and ebook formats whose readers no longer exist. Written
#: down so recall is a number this harness reports on every run rather than a claim,
#: and so a regression in identification shows up as a falling count instead of as
#: silence. Precision has never been the problem; recall was 25 caught / 66 missed.
OBSOLETE_EXTENSIONS = frozenset(
    {
        # Lotus 1-2-3
        "wk1", "wk3", "wk4", "wks", "123",
        # Quattro Pro
        "wq1", "wq2", "wb1", "wb2",
        # Microsoft Access
        "mdb",
        # Windows Write
        "wri",
        # Inmagic DB/TextWorks catalogue components
        "acf", "btx", "dbo", "dbr", "dbs", "ixl", "occ", "sdo", "tba", "tbu",
        # Discontinued ebook formats
        "lit", "mobi", "azw3", "lrf", "pdb", "rb", "snb",
        # Other dead formats
        "arj", "indd", "fft", "rft", "cdd", "mmp", "stg", "scr", "sta",
    }
)  # fmt: skip

#: The shape of every format-identification event detail ``FormatId.summary`` writes.
#: Parsed back out so the log can be compared with the record it sits beside.
_DETAIL_RE = re.compile(
    r"^identified as .+ \[[^\]]+\] via (?P<basis>[^;]+); media-type (?P<media>[^;]+)"
)


@dataclass(frozen=True)
class CorpusFile:
    """One file in the pinned corpus tree: its path, size, and git blob SHA-1."""

    path: str
    size: int
    blob_sha1: str


def _tree(commit: str) -> list[CorpusFile]:
    """List every blob in the pinned commit, via the GitHub trees API."""
    url = f"https://api.github.com/repos/{CORPUS_REPO}/git/trees/{commit}?recursive=1"
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 - fixed https URL
        payload = json.load(response)
    if payload.get("truncated"):
        raise RuntimeError("corpus tree listing was truncated; cannot pin a complete sample")
    return [
        CorpusFile(path=entry["path"], size=entry.get("size", 0), blob_sha1=entry["sha"])
        for entry in payload["tree"]
        if entry["type"] == "blob"
    ]


def _git_blob_sha1(data: bytes) -> str:
    """The git object id of ``data`` as a blob — the corpus's own integrity check."""
    return hashlib.sha1(b"blob %d\x00" % len(data) + data).hexdigest()  # noqa: S324 - git's own hash, not a security decision


def _download(entry: CorpusFile, dest_root: Path) -> str:
    """Fetch one corpus file, verify its blob SHA-1, and write it under ``dest_root``."""
    dest = dest_root / entry.path
    if dest.exists() and dest.stat().st_size == entry.size:
        return "cached"
    url = f"https://raw.githubusercontent.com/{CORPUS_REPO}/{CORPUS_COMMIT}/" + urllib.parse.quote(
        entry.path
    )
    with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310 - fixed https URL
        data = response.read()
    if _git_blob_sha1(data) != entry.blob_sha1:
        raise RuntimeError(f"corpus file failed its blob SHA-1 check: {entry.path}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return "fetched"


def fetch(dest_root: Path, max_file_bytes: int) -> list[CorpusFile]:
    """Fetch the bounded corpus sample into ``dest_root``, verifying every file."""
    entries = [
        entry
        for entry in _tree(CORPUS_COMMIT)
        if entry.size <= max_file_bytes and entry.path not in SKIP_PATHS
    ]
    total = sum(entry.size for entry in entries)
    print(
        f"corpus {CORPUS_REPO}@{CORPUS_COMMIT[:12]} — "
        f"{len(entries)} files, {total / 1e6:.1f} MB (<= {max_file_bytes / 1e6:.1f} MB each)"
    )
    print(f"licence: {CORPUS_LICENCE}")
    dest_root.mkdir(parents=True, exist_ok=True)
    outcomes: collections.Counter[str] = collections.Counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for outcome in pool.map(lambda e: _download(e, dest_root), entries):
            outcomes[outcome] += 1
    print(
        f"fetched {outcomes['fetched']}, already present {outcomes['cached']} — all SHA-1 verified"
    )
    return entries


def _collections_of(corpus_root: Path) -> dict[str, dict[str, Path]]:
    """Group the corpus into one payload set per top-level directory (one AIP each)."""
    groups: dict[str, dict[str, Path]] = collections.defaultdict(dict)
    for path in sorted(corpus_root.rglob("*")):
        if path.is_file():
            relative = path.relative_to(corpus_root)
            groups[relative.parts[0]][relative.as_posix()] = path
    return dict(groups)


def _ingest_all(corpus_root: Path, archive_root: Path) -> tuple[Archive, list[str], list[str]]:
    """Ingest every collection; return the archive, the bag names, and any failures."""
    archive_root.mkdir(parents=True, exist_ok=True)
    archive = Archive.init(Config.default("real-corpus trial archive", archive_root))
    bag_names: list[str] = []
    failures: list[str] = []
    for name, payload in sorted(_collections_of(corpus_root).items()):
        record = Record(
            title=f"OPF format-corpus: {name}",
            default_policy=AccessPolicy.PUBLIC,
            dublin_core=DublinCore(title=[name], publisher=["Open Preservation Foundation"]),
        )
        try:
            aip = archive.ingest(payload, record, now="2026-01-01T00:00:00Z")
        except Exception as exc:  # the report's job is to name every failure, not stop at one
            failures.append(f"{name} ({len(payload)} files): {type(exc).__name__}: {exc}")
            continue
        bag_names.append(aip.bag.path.name)
    return archive, bag_names, failures


def _prove_payload_bytes(archive: Archive, corpus_root: Path) -> tuple[int, list[str]]:
    """Re-hash every bagged payload against the fetched original.

    This is the guard against the failure mode where a report looks entirely
    plausible while the real files never actually reached the pipeline. Returns
    the number of files proven identical and a list of any that were not.
    """
    proven = 0
    mismatches: list[str] = []
    for bag_dir in sorted(archive.bags_dir.iterdir()):
        data_dir = bag_dir / "data"
        if not data_dir.is_dir():
            continue
        for stored in data_dir.rglob("*"):
            if not stored.is_file():
                continue
            relative = stored.relative_to(data_dir)
            source = corpus_root / relative
            if not source.exists():
                mismatches.append(f"no source for bagged payload {relative}")
                continue
            if (
                hashlib.sha256(source.read_bytes()).digest()
                != hashlib.sha256(stored.read_bytes()).digest()
            ):
                mismatches.append(f"bagged bytes differ from source: {relative}")
                continue
            proven += 1
    return proven, mismatches


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _format_events(archive: Archive, bag_names: list[str]) -> list[dict[str, str]]:
    """Every PREMIS format-identification event the ingest wrote."""
    events: list[dict[str, str]] = []
    for name in bag_names:
        premis = _read_json(archive.bags_dir / name / "premis.json")
        events.extend(
            entry for entry in premis["entries"] if entry["eventType"] == "format identification"
        )
    return events


# --- per-file verdicts and the aggregates derived from them --------------------


def file_rows(corpus_root: Path, entries: list[CorpusFile] | None = None) -> list[dict[str, Any]]:
    """One row per corpus file: provenance hashes plus the identifier's verdict.

    This is the evidence the write-up's numbers are derived from, so it carries
    everything a re-derivation needs and nothing else: the path, the corpus's own
    git blob SHA-1 (provenance), the SHA-256 (the content address ledger stores it
    under), the size, and the :class:`~ledger.preservation.FormatId` fields. No file
    content.
    """
    blob_by_path = {entry.path: entry.blob_sha1 for entry in entries or ()}
    rows: list[dict[str, Any]] = []
    # Ordered by the POSIX relative path as a string, not by ``Path`` (which compares
    # part-wise and differs by platform), so the committed rows are byte-stable.
    files = [p for p in corpus_root.rglob("*") if p.is_file()]
    for path in sorted(files, key=lambda p: p.relative_to(corpus_root).as_posix()):
        relative = path.relative_to(corpus_root).as_posix()
        data = path.read_bytes()
        fmt = identify_file(path)
        rows.append(
            {
                "path": relative,
                "blob_sha1": blob_by_path.get(relative) or _git_blob_sha1(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
                "format": fmt.name,
                "puid": fmt.puid,
                "media_type": fmt.media_type,
                "basis": fmt.basis,
                "at_risk": fmt.at_risk,
                "unassessable": fmt.unassessable,
                "header_offset": fmt.header_offset,
                "obsolete": path.suffix[1:].lower() in OBSOLETE_EXTENSIONS,
            }
        )
    return rows


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The counts the write-up states, derived from the per-file rows — and only from them.

    Shared by the evidence writer and by ``tests/test_real_corpus_evidence.py``, so
    a number in the docs and the number in the evidence can only ever come from the
    same derivation.
    """
    by_basis: collections.Counter[str] = collections.Counter(row["basis"] for row in rows)
    by_format: collections.Counter[str] = collections.Counter(row["format"] for row in rows)
    obsolete = [row for row in rows if row["obsolete"]]
    by_address: dict[str, list[str]] = collections.defaultdict(list)
    for row in rows:
        by_address[row["sha256"]].append(row["path"])
    shared = {address: paths for address, paths in by_address.items() if len(paths) > 1}
    return {
        "files": len(rows),
        "bytes": sum(row["size"] for row in rows),
        "by_basis": dict(sorted(by_basis.items())),
        "by_format": dict(sorted(by_format.items())),
        "at_risk": sum(1 for row in rows if row["at_risk"]),
        "unassessable": sum(1 for row in rows if row["unassessable"]),
        "displaced_headers": sum(1 for row in rows if row["header_offset"]),
        "obsolete": len(obsolete),
        "obsolete_flagged": sum(1 for row in obsolete if row["at_risk"]),
        "distinct_addresses": len(by_address),
        "shared_addresses": len(shared),
        "payloads_on_shared_addresses": sum(len(paths) for paths in shared.values()),
    }


# --- what the PREMIS log says, checked against the record it sits beside -------


def _identification_contradictions(
    archive: Archive, bag_names: list[str]
) -> list[tuple[str, IdentificationContradiction]]:
    """Every object any bag's log asserts two identification verdicts for.

    Read through :meth:`~ledger.metadata.premis.PremisLog.contradictions`, the
    same reader a steward's tooling would use, so the harness cannot pass on a log
    the product would read as contradictory. Since ADR 0012 an ingest *refuses* to
    write the second verdict, so this is an invariant: any hit fails the run.
    """
    found: list[tuple[str, IdentificationContradiction]] = []
    for name in bag_names:
        log = PremisLog.read(archive.bags_dir / name / "premis.json")
        found.extend((name, contradiction) for contradiction in log.contradictions())
    return found


def _success_while_unidentified(events: list[dict[str, str]]) -> list[dict[str, str]]:
    """Format-identification events logged as ``success`` over a file nothing identified.

    The lead defect of the first corpus run: 156 of 679 files, every one filed under
    the same green outcome as a confident content match. The outcome ladder in
    ``ingest_sip`` makes it unreachable; this is the independent check that it
    stays unreachable, read off the log rather than off the code.
    """
    return [
        event
        for event in events
        if event["eventOutcome"] == "success"
        and ("via unknown" in event["eventDetail"] or "UNASSESSABLE" in event["eventDetail"])
    ]


def _event_record_agreement(archive: Archive, bag_names: list[str]) -> tuple[int, list[str]]:
    """Each payload's record entry against the one event that is *about that payload*.

    Since ADR 0012 a format-identification event is keyed to the payload within its
    record (``ledger-payload``) and carries the address it examined, so the log and
    the record can be joined exactly — which the address-keyed log could not do once
    the store had deduplicated two payloads onto one address. For every payload
    there must be exactly one such event, it must name the bytes the record stores,
    and its media type and basis must be the record's (unless a steward *declared*
    the type, which the record says). Returns ``(payloads checked, problems)``; any
    problem fails the run.
    """
    checked = 0
    problems: list[str] = []
    for name in bag_names:
        record = _read_json(archive.bags_dir / name / "record.json")
        premis = _read_json(archive.bags_dir / name / "premis.json")
        by_object: dict[str, list[dict[str, str]]] = collections.defaultdict(list)
        for entry in premis["entries"]:
            if (
                entry["eventType"] == "format identification"
                and entry.get("linkingObjectIdentifierType") == OBJECT_TYPE_PAYLOAD
            ):
                by_object[entry["linkingObjectIdentifier"]].append(entry)
        for payload in record["payloads"]:
            checked += 1
            object_id = f"{record['record_id']}/{payload['filename']}"
            problem = _payload_problem(object_id, payload, by_object.get(object_id, []))
            if problem is not None:
                problems.append(problem)
    return checked, problems


def _payload_problem(
    object_id: str, payload: dict[str, Any], events: list[dict[str, str]]
) -> str | None:
    """Why one payload's record entry and its identification event disagree, or ``None``."""
    if len(events) != 1:
        return f"{object_id}: {len(events)} identification events about it (expected exactly 1)"
    event = events[0]
    if event.get("linkingObjectContentAddress") != payload["address"]:
        return (
            f"{object_id}: the event examined {event.get('linkingObjectContentAddress')} "
            f"but the record stores {payload['address']}"
        )
    match = _DETAIL_RE.match(event["eventDetail"])
    if match is None:
        return f"{object_id}: unparseable identification detail {event['eventDetail']!r}"
    basis = payload.get("media_type_basis", "")
    if basis == "declared":
        return None
    if match["media"].strip() != payload["media_type"] or match["basis"].strip() != basis:
        return (
            f"{object_id}: record says {payload['media_type']} via {basis or '(unrecorded)'}; "
            f"log says {match['media'].strip()} via {match['basis'].strip()}"
        )
    return None


def _shared_address_groups(archive: Archive, bag_names: list[str]) -> list[dict[str, Any]]:
    """Content addresses held by more than one payload, with each payload's verdict.

    This is the population #149 could fire on: identical bytes, several names. It
    is *reported*, not failed — two payloads are two PREMIS objects, and a
    ``.txt`` and a ``.md`` with identical bytes legitimately get different
    name-derived types — but it is listed in full so the number of groups whose
    verdicts differ by name is a measured fact on every run rather than a surprise.
    """
    by_address: dict[str, list[tuple[str, str]]] = collections.defaultdict(list)
    for name in bag_names:
        record = _read_json(archive.bags_dir / name / "record.json")
        for payload in record["payloads"]:
            verdict = f"{payload['media_type']} via {payload.get('media_type_basis') or '?'}"
            # Listed by bag-relative filename — the corpus path — not by payload object
            # id: a record id is minted per run, and the evidence must be byte-stable.
            by_address[payload["address"]].append((payload["filename"], verdict))
    groups: list[dict[str, Any]] = []
    for address, payloads in sorted(by_address.items()):
        if len(payloads) > 1:
            groups.append(
                {
                    "address": address,
                    "payloads": [object_id for object_id, _ in payloads],
                    "verdicts": sorted({verdict for _, verdict in payloads}),
                }
            )
    return groups


# --- the printed report ---------------------------------------------------------


def _report_obsolete_recall(rows: list[dict[str, Any]]) -> None:
    """Print the at-risk advisory's recall over the corpus's known-obsolete files.

    Recall, not precision, is the number this advisory was failing on: it caught 25
    endangered files and missed 66. Precision is reported as the miss list rather
    than as a false-positive rate, because an at_risk flag on a file outside
    :data:`OBSOLETE_EXTENSIONS` is not automatically wrong — legacy OLE2 Office is
    genuinely at risk and is deliberately not in that set.
    """
    obsolete = [row for row in rows if row["obsolete"]]
    flagged = [row for row in obsolete if row["at_risk"]]
    missed = [row["path"] for row in obsolete if not row["at_risk"]]

    print("\n=== at-risk recall over the corpus's known-obsolete files ===")
    print(f"  obsolete files in the corpus:        {len(obsolete)}")
    print(f"  flagged at_risk:                     {len(flagged)}")
    print(
        f"  recall:                              {len(flagged) / (len(obsolete) or 1) * 100:.0f}%"
    )
    if missed:
        print(f"  still unflagged ({len(missed)}) — reported as unassessable, not as safe:")
        for name in missed:
            print(f"    {name}")


def _report_identification(rows: list[dict[str, Any]]) -> None:
    """Print the identification tables measured directly over the fetched files."""
    summary = aggregate(rows)
    total = summary["files"] or 1
    print("\n=== identification over the real corpus ===")
    for basis, count in sorted(summary["by_basis"].items(), key=lambda kv: -kv[1]):
        print(f"  {count:5d}  {count / total * 100:5.1f}%  {basis}")
    at_risk, unassessable = summary["at_risk"], summary["unassessable"]
    print(f"  {at_risk:5d}  {at_risk / total * 100:5.1f}%  flagged at_risk (known-obsolete)")
    print(
        f"  {unassessable:5d}  {unassessable / total * 100:5.1f}%  unassessable (no risk verdict possible)"
    )

    _report_obsolete_recall(rows)

    print("\n=== formats identified ===")
    for name, count in sorted(summary["by_format"].items(), key=lambda kv: (-kv[1], kv[0]))[:20]:
        print(f"  {count:5d}  {name}")

    displaced = [(row["path"], row["header_offset"]) for row in rows if row["header_offset"]]
    if displaced:
        print(f"\n=== headers displaced past byte 0 ({len(displaced)}) ===")
        for name, offset in displaced[:10]:
            print(f"  offset {offset:5d}  {name}")


def _report_bags(
    archive: Archive, corpus_root: Path, bag_names: list[str], failures: list[str]
) -> dict[str, Any]:
    """Print ingest, bag-validation, and byte-provenance results; return them."""
    print("\n=== ingest ===")
    print(f"  collections ingested: {len(bag_names)}")
    for failure in failures:
        print(f"  INGEST FAILURE: {failure}")

    print("\n=== bag validation (RFC 8493) ===")
    invalid = [name for name in bag_names if not validate_bag(archive.bags_dir / name).ok]
    print(f"  bags valid: {len(bag_names) - len(invalid)}/{len(bag_names)}")
    for name in invalid:
        print(f"  INVALID BAG: {name}")

    print("\n=== provenance: did the real bytes reach the pipeline? ===")
    proven, mismatches = _prove_payload_bytes(archive, corpus_root)
    print(f"  payload files byte-identical to the fetched originals: {proven}")
    for mismatch in mismatches:
        print(f"  PROVENANCE FAILURE: {mismatch}")

    return {
        "collections": len(bag_names),
        "ingest_failures": failures,
        "bags_valid": len(bag_names) - len(invalid),
        "invalid_bags": invalid,
        "payloads_proven": proven,
        "provenance_failures": mismatches,
    }


def _report_premis(archive: Archive, corpus_root: Path, bag_names: list[str]) -> dict[str, Any]:
    """Print what the PREMIS log says and whether it agrees with itself and the record."""
    print("\n=== PREMIS format-identification outcomes ===")
    events = _format_events(archive, bag_names)
    outcomes = collections.Counter(event["eventOutcome"] for event in events)
    for outcome, count in outcomes.most_common():
        print(f"  {count:5d}  {outcome}")
    green_but_unidentified = _success_while_unidentified(events)
    print(f"  logged as success while unidentified: {len(green_but_unidentified)}")
    for event in green_but_unidentified[:5]:
        print(f"    {event['linkingObjectIdentifier']}  {event['eventDetail']}")

    print("\n=== record media type vs PREMIS identification ===")
    divergent = _media_type_divergence(archive, corpus_root, bag_names)
    print(
        f"  payloads whose record media_type disagrees with the identified format: {len(divergent)}"
    )
    for filename, record_type, premis_type in divergent[:10]:
        print(f"    record says {record_type:32s} identifier says {premis_type:26s} {filename}")

    print("\n=== one object, one verdict (ADR 0012) ===")
    checked, disagreements = _event_record_agreement(archive, bag_names)
    print(
        f"  payloads with exactly one identification event about them: {checked - len(disagreements)}/{checked}"
    )
    for problem in disagreements[:10]:
        print(f"    {problem}")
    contradictions = _identification_contradictions(archive, bag_names)
    print(f"  objects whose log carries more than one verdict: {len(contradictions)}")
    for name, contradiction in contradictions[:10]:
        print(
            f"    {name}: {contradiction.object_id[:48]} ({contradiction.object_type}) "
            f"{contradiction.events} events -> {contradiction.verdicts}"
        )
    groups = _shared_address_groups(archive, bag_names)
    name_dependent = [group for group in groups if len(group["verdicts"]) > 1]
    payloads_shared = sum(len(group["payloads"]) for group in groups)
    print(
        f"  content addresses shared by more than one payload: {len(groups)} "
        f"({payloads_shared} payloads) — each payload is its own PREMIS object"
    )
    print(
        f"  of those, groups whose verdict differs by payload name: {len(name_dependent)} "
        "(reported, not a contradiction: different objects, different names)"
    )
    for group in name_dependent[:10]:
        print(f"    {group['address'][:24]}…  {group['verdicts']}  {group['payloads']}")

    return {
        "format_events": len(events),
        "outcomes": dict(sorted(outcomes.items())),
        "success_while_unidentified": len(green_but_unidentified),
        "record_log_divergence": len(divergent),
        "payloads_checked": checked,
        "event_record_disagreements": len(disagreements),
        "contradictions": len(contradictions),
        "shared_addresses": len(groups),
        "payloads_on_shared_addresses": payloads_shared,
        "name_dependent_verdict_groups": len(name_dependent),
        "shared_address_groups": groups,
    }


def _report(
    archive: Archive,
    corpus_root: Path,
    bag_names: list[str],
    failures: list[str],
    entries: list[CorpusFile] | None = None,
) -> tuple[int, dict[str, Any]]:
    """Print the measured report. Returns a process exit code and the evidence."""
    rows = file_rows(corpus_root, entries)
    _report_identification(rows)
    bags = _report_bags(archive, corpus_root, bag_names, failures)
    premis = _report_premis(archive, corpus_root, bag_names)

    # Every one of these is an invariant since ADR 0010/0012, not a statistic:
    # the record's media type is the identifier's verdict; a file nothing could
    # identify is never a success; one object carries one verdict; and every
    # payload's event is about that payload. Failing the run on any hit is what
    # stops the old behaviour creeping back in through a new code path.
    broken = bool(
        failures
        or bags["invalid_bags"]
        or bags["provenance_failures"]
        or premis["record_log_divergence"]
        or premis["success_while_unidentified"]
        or premis["event_record_disagreements"]
        or premis["contradictions"]
    )
    print("\nreal-corpus run: " + ("FAILURES ABOVE" if broken else "no pipeline failures"))
    evidence = {
        "schema": EVIDENCE_SCHEMA,
        "corpus": {
            "repo": CORPUS_REPO,
            "commit": CORPUS_COMMIT,
            "licence": CORPUS_LICENCE,
        },
        "aggregates": aggregate(rows),
        "bags": bags,
        "premis": premis,
        "files": rows,
    }
    return (1 if broken else 0), evidence


def _media_type_divergence(
    archive: Archive, corpus_root: Path, bag_names: list[str]
) -> list[tuple[str, str, str]]:
    """Payloads whose stored media type disagrees with what the identifier finds now.

    ``ingest`` used to fall back to :mod:`mimetypes` — a pure filename guess —
    whenever the content-based identifier did not match a signature, so a record
    could advertise a confident media type for a file the preservation log said was
    never identified. That was 100 payloads here. Since ADR 0010 the record's media
    type *is* the identifier's verdict, so this is an invariant rather than a
    statistic and any hit fails the run.

    Compared against a fresh identification of the corpus file — the same function
    the ingest called — so it cross-checks :func:`_event_record_agreement`, which
    compares the record against the *log* instead.
    """
    divergent: list[tuple[str, str, str]] = []
    for name in bag_names:
        record = _read_json(archive.bags_dir / name / "record.json")
        for payload in record["payloads"]:
            source = corpus_root / payload["filename"]
            if not source.is_file():
                continue
            found = identify_file(source).media_type
            if found != payload["media_type"]:
                divergent.append((payload["filename"], payload["media_type"], found))
    return divergent


# --- committed evidence -----------------------------------------------------------


def _comparable(evidence: dict[str, Any]) -> str:
    """The evidence with its run-specific metadata removed, as canonical JSON."""
    stripped = {key: value for key, value in evidence.items() if key != "run"}
    return json.dumps(stripped, sort_keys=True, indent=1, ensure_ascii=False)


def _ledger_commit() -> str:
    """The ledger checkout that produced the run, or ``"unknown"`` outside git.

    ``git describe --always --dirty``: the commit, with ``-dirty`` appended when the
    working tree had uncommitted changes — which it always does for the run whose
    evidence is about to be committed, since the evidence lands in the *next*
    commit. The stamp is provenance for a reader, not a value anything compares.
    """
    try:
        return subprocess.run(
            ["git", "describe", "--always", "--dirty"],  # noqa: S607 - git resolved on PATH on purpose
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def write_evidence(evidence: dict[str, Any], path: Path) -> None:
    """Write the evidence file the write-up and its tests derive every number from."""
    path.parent.mkdir(parents=True, exist_ok=True)
    stamped = {
        **evidence,
        "run": {
            "date": _dt.datetime.now(tz=_dt.UTC).strftime("%Y-%m-%d"),
            "ledger_commit": _ledger_commit(),
        },
    }
    path.write_text(
        json.dumps(stamped, sort_keys=True, indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"evidence written: {path} ({len(evidence['files'])} file rows)")


def check_evidence(evidence: dict[str, Any], path: Path) -> list[str]:
    """Compare a run against the committed evidence; return what drifted.

    Drift is any difference outside the run-stamp: a changed verdict, count, or
    hash. It is reported per top-level section so the cause is nameable, and it
    fails the run — a measurement that no longer matches what the write-up cites
    is a write-up that has silently stopped being true.
    """
    if not path.is_file():
        return [f"no committed evidence at {path}; run with --write-evidence to create it"]
    committed = _read_json(path)
    if _comparable(committed) == _comparable(evidence):
        return []
    drift: list[str] = []
    for key in sorted(set(committed) | set(evidence)):
        if key == "run":
            continue
        if committed.get(key) != evidence.get(key):
            drift.append(f"section {key!r} differs from the committed evidence")
    return drift or ["evidence differs in a way the section comparison could not localise"]


def main(argv: list[str] | None = None) -> int:
    """Fetch the pinned corpus, ingest it, and report what the real files did."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=DEFAULT_DEST,
        help="where to keep the fetched corpus (gitignored; default: ./real-corpus)",
    )
    parser.add_argument(
        "--max-file-bytes",
        type=int,
        default=DEFAULT_MAX_FILE_BYTES,
        help="per-file ceiling for the bounded sample (default: 5 MB)",
    )
    parser.add_argument("--fetch-only", action="store_true", help="download the corpus and stop")
    parser.add_argument(
        "--keep-archive",
        type=Path,
        default=None,
        help="keep the trial archive at this path instead of a temporary directory",
    )
    parser.add_argument(
        "--evidence",
        type=Path,
        default=DEFAULT_EVIDENCE,
        help=f"the committed evidence file to check against (default: {DEFAULT_EVIDENCE})",
    )
    parser.add_argument(
        "--write-evidence",
        action="store_true",
        help="rewrite the evidence file from this run instead of checking against it",
    )
    args = parser.parse_args(argv)

    corpus_root = args.dest.resolve()
    entries = fetch(corpus_root, args.max_file_bytes)
    if args.fetch_only:
        return 0

    if args.keep_archive is not None:
        archive_root = args.keep_archive.resolve()
        shutil.rmtree(archive_root, ignore_errors=True)
        archive, bag_names, failures = _ingest_all(corpus_root, archive_root)
        code, evidence = _report(archive, corpus_root, bag_names, failures, entries)
    else:
        with tempfile.TemporaryDirectory(prefix="ledger-real-corpus-") as tmp:
            archive, bag_names, failures = _ingest_all(corpus_root, Path(tmp) / "archive")
            code, evidence = _report(archive, corpus_root, bag_names, failures, entries)

    # The committed evidence describes the default sample; a smaller sample is an
    # exploration, not a measurement of record, so it is neither checked nor written.
    if args.max_file_bytes != DEFAULT_MAX_FILE_BYTES:
        return code
    if args.write_evidence:
        write_evidence(evidence, args.evidence)
        return code
    drift = check_evidence(evidence, args.evidence)
    for line in drift:
        print(f"EVIDENCE DRIFT: {line}")
    if drift:
        print(
            "the run no longer matches the committed evidence; re-run with --write-evidence "
            "only after updating every document that cites it"
        )
    return 1 if (code or drift) else 0


if __name__ == "__main__":
    raise SystemExit(main())
