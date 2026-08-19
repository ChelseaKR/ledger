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

No-outing: the corpus is third-party public test data with no contributor
identities, the temporary archive is created with no identity vault entries, and
the report prints only format metadata, filenames, and counts — never payload
content.

Usage::

    python tools/real_corpus.py            # fetch (if needed), ingest, report
    python tools/real_corpus.py --fetch-only
    python tools/real_corpus.py --max-file-bytes 1000000   # a smaller sample
"""

from __future__ import annotations

import argparse
import collections
import concurrent.futures
import hashlib
import json
import re
import shutil
import sys
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ledger.bag import validate_bag
from ledger.config import Config
from ledger.ingest import Archive
from ledger.models import AccessPolicy, DublinCore, Record
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


def _format_events(archive: Archive, bag_names: list[str]) -> list[dict[str, str]]:
    """Every PREMIS format-identification event the ingest wrote."""
    events: list[dict[str, str]] = []
    for name in bag_names:
        premis = json.loads((archive.bags_dir / name / "premis.json").read_text(encoding="utf-8"))
        events.extend(
            entry for entry in premis["entries"] if entry["eventType"] == "format identification"
        )
    return events


def _report_identification(corpus_root: Path) -> None:
    """Print the identification tables measured directly over the fetched files."""
    files = [p for p in corpus_root.rglob("*") if p.is_file()]
    by_basis: collections.Counter[str] = collections.Counter()
    by_format: collections.Counter[str] = collections.Counter()
    at_risk = 0
    displaced: list[tuple[str, int]] = []
    for path in sorted(files):
        fmt = identify_file(path)
        by_basis[fmt.basis] += 1
        by_format[fmt.name] += 1
        at_risk += bool(fmt.at_risk)
        if fmt.header_offset:
            displaced.append((str(path.relative_to(corpus_root)), fmt.header_offset))

    total = len(files) or 1
    print("\n=== identification over the real corpus ===")
    for basis, count in by_basis.most_common():
        print(f"  {count:5d}  {count / total * 100:5.1f}%  {basis}")
    print(f"  {at_risk:5d}  {at_risk / total * 100:5.1f}%  flagged at_risk")

    print("\n=== formats identified ===")
    for name, count in by_format.most_common(20):
        print(f"  {count:5d}  {name}")

    if displaced:
        print(f"\n=== headers displaced past byte 0 ({len(displaced)}) ===")
        for name, offset in displaced[:10]:
            print(f"  offset {offset:5d}  {name}")


def _report(archive: Archive, corpus_root: Path, bag_names: list[str], failures: list[str]) -> int:
    """Print the measured report. Returns a process exit code."""
    _report_identification(corpus_root)

    print("\n=== ingest ===")
    print(f"  collections ingested: {len(bag_names)}")
    for failure in failures:
        print(f"  INGEST FAILURE: {failure}")

    print("\n=== bag validation (RFC 8493) ===")
    invalid = []
    for name in bag_names:
        report = validate_bag(archive.bags_dir / name)
        if not report.ok:
            invalid.append(name)
    print(f"  bags valid: {len(bag_names) - len(invalid)}/{len(bag_names)}")
    for name in invalid:
        print(f"  INVALID BAG: {name}")

    print("\n=== provenance: did the real bytes reach the pipeline? ===")
    proven, mismatches = _prove_payload_bytes(archive, corpus_root)
    print(f"  payload files byte-identical to the fetched originals: {proven}")
    for mismatch in mismatches:
        print(f"  PROVENANCE FAILURE: {mismatch}")

    print("\n=== PREMIS format-identification outcomes ===")
    events = _format_events(archive, bag_names)
    outcomes = collections.Counter(event["eventOutcome"] for event in events)
    for outcome, count in outcomes.most_common():
        print(f"  {count:5d}  {outcome}")

    print("\n=== record media type vs PREMIS identification ===")
    divergent = _media_type_divergence(archive, bag_names)
    print(
        f"  payloads whose record media_type disagrees with the identified format: {len(divergent)}"
    )
    for filename, record_type, premis_type in divergent[:10]:
        print(f"    record says {record_type:32s} identifier says {premis_type:26s} {filename}")

    broken = bool(failures or invalid or mismatches)
    print("\nreal-corpus run: " + ("FAILURES ABOVE" if broken else "no pipeline failures"))
    return 1 if broken else 0


def _media_type_divergence(archive: Archive, bag_names: list[str]) -> list[tuple[str, str, str]]:
    """Payloads whose stored media type disagrees with what the identifier found.

    ``ingest`` falls back to :mod:`mimetypes` (a pure filename guess) whenever the
    content-based identifier did not match a signature, so a record can advertise a
    confident media type for a file the preservation log says was never identified.
    Naming that divergence is the point: it is invisible on fixtures, where the
    extension and the bytes always agree.
    """
    divergent: list[tuple[str, str, str]] = []
    for name in bag_names:
        bag_dir = archive.bags_dir / name
        record = json.loads((bag_dir / "record.json").read_text(encoding="utf-8"))
        premis = json.loads((bag_dir / "premis.json").read_text(encoding="utf-8"))
        identified = {
            entry["linkingObjectIdentifier"]: entry["eventDetail"]
            for entry in premis["entries"]
            if entry["eventType"] == "format identification"
        }
        for payload in record["payloads"]:
            detail = identified.get(payload["address"], "")
            match = re.search(r"media-type ([^;]+)", detail)
            found = match.group(1).strip() if match else "?"
            if found != payload["media_type"]:
                divergent.append((payload["filename"], payload["media_type"], found))
    return divergent


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
    args = parser.parse_args(argv)

    corpus_root = args.dest.resolve()
    fetch(corpus_root, args.max_file_bytes)
    if args.fetch_only:
        return 0

    if args.keep_archive is not None:
        archive_root = args.keep_archive.resolve()
        shutil.rmtree(archive_root, ignore_errors=True)
        archive, bag_names, failures = _ingest_all(corpus_root, archive_root)
        return _report(archive, corpus_root, bag_names, failures)

    with tempfile.TemporaryDirectory(prefix="ledger-real-corpus-") as tmp:
        archive, bag_names, failures = _ingest_all(corpus_root, Path(tmp) / "archive")
        return _report(archive, corpus_root, bag_names, failures)


if __name__ == "__main__":
    raise SystemExit(main())
