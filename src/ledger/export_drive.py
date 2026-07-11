"""Sneakernet replication: a self-verifying USB courier package (EXP-08).

Minimal-computing communities (the roadmap's own design constraint) often have
no reliable connectivity. This module builds a **courier package**: a directory
tree meant to be copied to a USB drive (or any removable media) and handed to
someone offline, that verifies itself on a machine with *no* ledger installed —
only the coreutils ``sha256sum`` (or macOS ``shasum -a 256``) every general-purpose
OS already ships.

Three properties matter here:

* **Disclosure-filtered, not a raw copy.** A courier package is built from the
  ONE disclosure boundary (:meth:`~ledger.ingest.Archive.disclose`), exactly like
  the browse server and the CSV export — never by copying a bag directly off
  disk. A bag on disk holds plaintext for every policy except absolute
  ``SEALED`` (temporal seals and community/steward tiers are enforced only at
  read time), so copying bags verbatim onto portable media handed to a courier
  would defeat the archive's whole access-control model. Each record is
  re-bagged from its :class:`~ledger.models.DisclosedRecord` view under the
  caller's chosen :class:`~ledger.models.Grant` (default: anonymous/PUBLIC, the
  narrowest — least privilege), so a package built for wide distribution can
  never carry more than that viewer could already see live.
* **Self-verifying without ledger.** Every record is a standard RFC 8493 bag
  (:func:`ledger.bag.write_bag`), and the whole drive additionally carries one
  top-level ``CHECKSUMS.sha256`` (every payload and tag file, one line each,
  coreutils ``sha256sum -c`` format) plus a plain POSIX ``verify.sh`` that runs
  it — so a recipient with a bare machine and no Python can confirm nothing bit-
  rotted or was tampered with in transit.
* **A static, no-server browse page.** ``index.html`` and one page per record are
  written as plain files with relative links, openable directly from the drive
  in any browser via ``file://`` — no HTTP server, no ledger, no network.
"""

from __future__ import annotations

import html
import json
import stat
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ledger.bag import validate_bag, write_bag
from ledger.export import records_csv
from ledger.fixity import hash_file
from ledger.ingest import Archive
from ledger.models import DisclosedRecord, Grant, HashAlgo, now_iso

_BAGS_DIRNAME = "bags"
_CHECKSUMS_FILENAME = "CHECKSUMS.sha256"
_VERIFY_SCRIPT_NAME = "verify.sh"


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


@dataclass(frozen=True)
class ExportDriveResult:
    """What a courier-package build produced, for a no-outing-safe CLI summary.

    Carries only counts and the output path — never a title, a field value, or an
    identity (no-outing rule); the package on disk is the artifact, this is just a
    receipt.
    """

    out_dir: Path
    records_packaged: int
    files_packaged: int
    all_bags_valid: bool


def _record_page_html(record: DisclosedRecord, *, lang: str) -> str:
    """A single record's static, accessible page (no server, relative links only)."""
    warnings = "".join(f"<li>{_esc(w)}</li>" for w in record.content_warnings)
    cw_block = (
        f'<section aria-labelledby="cw-heading">\n'
        f'<h2 id="cw-heading">Content warnings</h2>\n<ul>{warnings}</ul>\n</section>\n'
        if record.content_warnings
        else ""
    )
    dc_rows = "".join(
        f'<tr><th scope="row">{_esc(k)}</th><td>{_esc("; ".join(v))}</td></tr>'
        for k, v in sorted(record.dublin_core.items())
        if v
    )
    field_rows = "".join(
        f'<tr><th scope="row">{_esc(k)}</th><td>{_esc(v)}</td></tr>'
        for k, v in sorted(record.fields.items())
    )
    payload_items = "".join(
        f"<li>{_esc(p.filename)} ({_esc(p.media_type)}, {p.size_bytes} bytes) — "
        f"see <code>bags/{_esc(record.record_id)}/data/{_esc(p.filename)}</code></li>"
        for p in record.payloads
    )
    withheld_items = "".join(f"<li>{_esc(r.name)}: {_esc(r.reason)}</li>" for r in record.withheld)
    withheld_block = (
        f'<section aria-labelledby="withheld-heading">\n'
        f'<h2 id="withheld-heading">Withheld</h2>\n<ul>{withheld_items}</ul>\n</section>\n'
        if withheld_items
        else ""
    )
    return (
        "<!doctype html>\n"
        f'<html lang="{_esc(lang)}">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_esc(record.title)} — ledger courier package</title>\n"
        '<link rel="stylesheet" href="../static/drive.css">\n'
        "</head>\n<body>\n"
        '<a class="skip-link" href="#main">Skip to content</a>\n'
        '<header><p><a href="../index.html">&larr; Back to index</a></p></header>\n'
        f'<main id="main" tabindex="-1">\n<h1>{_esc(record.title)}</h1>\n'
        f"{cw_block}"
        f"<table><caption>Descriptive metadata</caption><tbody>{dc_rows}{field_rows}"
        f"</tbody></table>\n"
        f'<section aria-labelledby="files-heading"><h2 id="files-heading">Files</h2>'
        f"<ul>{payload_items or '<li>No files disclosed.</li>'}</ul></section>\n"
        f"{withheld_block}"
        "</main>\n</body>\n</html>\n"
    )


def _index_html(records: Sequence[DisclosedRecord], *, archive_name: str, lang: str) -> str:
    items = "".join(
        f'<li><a href="records/{_esc(r.record_id)}.html">{_esc(r.title)}</a>'
        f"{' <em>(content warning)</em>' if r.content_warnings else ''}</li>"
        for r in records
    )
    return (
        "<!doctype html>\n"
        f'<html lang="{_esc(lang)}">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_esc(archive_name or 'ledger')} — courier package</title>\n"
        '<link rel="stylesheet" href="static/drive.css">\n'
        "</head>\n<body>\n"
        '<a class="skip-link" href="#main">Skip to content</a>\n'
        f'<main id="main" tabindex="-1">\n<h1>{_esc(archive_name or "ledger")} — courier package'
        "</h1>\n"
        "<p>This is a self-contained, offline archive export. Open any record below, or "
        f"run <code>./{_VERIFY_SCRIPT_NAME}</code> from a terminal to confirm every file's "
        "fixity before trusting its contents.</p>\n"
        f'<ul class="record-list">{items or "<li>No records disclosed.</li>"}</ul>\n'
        '<p><a href="manifest.csv">Download the record list as CSV</a></p>\n'
        "</main>\n</body>\n</html>\n"
    )


_DRIVE_CSS = (
    ":root{color-scheme:light dark}"
    "body{font-family:system-ui,sans-serif;max-width:60rem;margin:2rem auto;padding:0 1rem;"
    "line-height:1.5}"
    ".skip-link{position:absolute;left:-999px}"
    ".skip-link:focus{position:static;display:inline-block;margin:0.5rem}"
    "table{border-collapse:collapse;width:100%;margin:1rem 0}"
    "th,td{border:1px solid currentColor;padding:0.4rem;text-align:left;vertical-align:top}"
)

_VERIFY_SH = f"""#!/bin/sh
# Verify every file on this courier package without ledger installed.
# Works with plain coreutils/BSD tools: sha256sum (Linux) or shasum -a 256 (macOS).
set -eu
cd "$(dirname "$0")"
if command -v sha256sum >/dev/null 2>&1; then
    sha256sum -c "{_CHECKSUMS_FILENAME}"
elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 -c "{_CHECKSUMS_FILENAME}"
else
    echo "no sha256sum or shasum found on this machine" >&2
    exit 1
fi
echo "OK: every file on this drive matches its recorded checksum."
"""


def build_export_drive(
    archive: Archive,
    out_dir: Path,
    *,
    grant: Grant,
    archive_name: str = "",
    base_url: str = "",
    lang: str = "en",
    now: str | None = None,
) -> ExportDriveResult:
    """Build a self-verifying courier package at ``out_dir`` for ``grant``.

    ``out_dir`` must not already exist (refuses to merge into or overwrite an
    unrelated directory). Every record ``grant`` may browse is disclosed, its
    disclosed payload bytes are copied out of the content store by content
    address, and a fresh RFC 8493 bag is written per record under
    ``bags/<record_id>/`` — then immediately re-validated with
    :func:`~ledger.bag.validate_bag` before it is trusted onto the package
    (defense in depth: a package is never handed out unverified). A top-level
    ``CHECKSUMS.sha256`` covers every payload and tag file on the whole drive, a
    ``verify.sh`` runs it with coreutils alone, and a static ``index.html`` plus
    one page per record give a no-server browse experience.

    No-outing rule: only what ``grant`` may already see through
    :meth:`~ledger.ingest.Archive.disclose` is written — record titles, disclosed
    field values, disclosed payload bytes, and safe withheld-reason labels. No
    identity, no sealed value, ever reaches the package.
    """
    out_dir = Path(out_dir)
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FileExistsError(f"export-drive output directory is not empty: {out_dir}")
    stamp = now if now is not None else now_iso()

    records = archive.browse(grant, now=stamp)

    bags_dir = out_dir / _BAGS_DIRNAME
    records_dir = out_dir / "records"
    static_dir = out_dir / "static"
    for d in (bags_dir, records_dir, static_dir):
        d.mkdir(parents=True, exist_ok=True)
    (static_dir / "drive.css").write_text(_DRIVE_CSS, encoding="utf-8")

    files_packaged = 0
    all_valid = True
    for record in records:
        payload_sources: dict[str, Path] = {}
        for p in record.payloads:
            source = archive.store.path_for(p.address)
            if source.exists():
                payload_sources[p.filename] = source
        bag_dir = bags_dir / record.record_id
        bag_dir.mkdir(parents=True, exist_ok=True)
        write_bag(
            bag_dir,
            payload_sources,
            algos=(HashAlgo.SHA256, HashAlgo.BLAKE2B),
            bag_info={
                "Source-Organization": archive_name or "ledger community archive",
                "External-Identifier": record.record_id,
                "Bagging-Date": stamp[:10],
            },
            extra_tag_files={
                "record.json": json.dumps(
                    record.to_dict(), indent=2, sort_keys=True, ensure_ascii=False
                ).encode("utf-8")
            },
        )
        report = validate_bag(bag_dir)
        all_valid = all_valid and report.ok
        files_packaged += len(payload_sources)
        (records_dir / f"{record.record_id}.html").write_text(
            _record_page_html(record, lang=lang), encoding="utf-8"
        )

    (out_dir / "index.html").write_text(
        _index_html(records, archive_name=archive_name, lang=lang), encoding="utf-8"
    )
    (out_dir / "manifest.csv").write_text(
        records_csv(records, base_url=base_url or "https://example.invalid"), encoding="utf-8"
    )

    # Write verify.sh BEFORE the aggregate checksum sweep so the script itself is
    # covered by CHECKSUMS.sha256 too — tampering with the verifier is then just as
    # detectable (by running `sha256sum -c` directly, the primary command) as
    # tampering with any payload or bag file.
    verify_path = out_dir / _VERIFY_SCRIPT_NAME
    verify_path.write_text(_VERIFY_SH, encoding="utf-8", newline="\n")
    verify_path.chmod(verify_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # Aggregate checksums over every file now on the drive except the checksum
    # file itself, in coreutils `sha256sum -c` format, sorted for reproducibility.
    all_files = sorted(
        p for p in out_dir.rglob("*") if p.is_file() and p.name != _CHECKSUMS_FILENAME
    )
    lines = [
        f"{hash_file(p, HashAlgo.SHA256)}  {p.relative_to(out_dir).as_posix()}" for p in all_files
    ]
    (out_dir / _CHECKSUMS_FILENAME).write_text(
        "".join(f"{line}\n" for line in lines), encoding="utf-8"
    )

    return ExportDriveResult(
        out_dir=out_dir,
        records_packaged=len(records),
        files_packaged=files_packaged,
        all_bags_valid=all_valid,
    )
