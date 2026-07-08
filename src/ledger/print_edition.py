"""Printable archive editions: an accessible, zine-style booklet (EXP-08).

Paper is the oldest preservation tier and, per user research, culturally native
to the zine communities ledger serves — and it needs no battery, no reader, no
network. This module renders selected records as a single, tagged HTML booklet
that:

* is **PUBLIC-only by construction** — it is built exclusively from
  :func:`ledger.access.grants.anonymous`, the narrowest grant, so no caller
  parameter can widen what a paper artifact — which, once printed, cannot be
  revoked or redacted after the fact — is allowed to carry (the risk the
  ideation item itself names: print output must pass the same content-warning
  and disclosure rules as every other read path);
* renders every content warning **before** the record's content, as visible,
  plain-language text — never colour- or icon-only — so the booklet's HTML form
  passes the same structural accessibility gate
  (:mod:`ledger.accessibility_check`, FIX-12) as the live site;
* prints one **fixity digest** per record as visible text (a SHA-256 over the
  record's disclosed, canonical JSON) — so *anyone*, with no scanner, can later
  fetch the same record and confirm the printed page matches — and, when the
  optional ``segno`` package is installed, an inline SVG QR code encoding the
  same string for a quick phone-camera check. The digest text is never
  QR-only: a QR code is not readable by a screen reader or a photocopier that
  drops fine detail, so the same information always exists as plain text too
  (accessibility — no channel is the sole carrier of information).

Scope note: this renders **HTML only**, not a bespoke PDF. A genuinely
accessible (tagged) PDF is its own hard, separate problem — the ideation item
that specifies this feature (``docs/ideation/03-expansions.md``, EXP-08) names
that risk explicitly and asks to scope to tagged HTML first. The HTML carries a
print stylesheet (page-break rules per record, print-safe colours) so a reader
can produce a paper or PDF copy with any browser's built-in "Print" / "Print to
PDF" — which is the honest, already-accessible path, rather than a hand-rolled
PDF writer that would silently drop tag structure a screen reader depends on.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ledger.access.grants import anonymous
from ledger.fixity import hash_bytes
from ledger.ingest import Archive
from ledger.models import DisclosedRecord, HashAlgo, canonical_json, now_iso

try:  # pragma: no cover - exercised indirectly by whichever branch is installed
    import segno

    _HAVE_SEGNO = True
except ImportError:  # pragma: no cover - exercised when the optional extra is absent
    segno = None  # type: ignore[assignment]
    _HAVE_SEGNO = False


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


@dataclass(frozen=True)
class PrintEditionResult:
    """What a print-edition build produced, for a no-outing-safe CLI summary."""

    out_path: Path
    records_included: int
    qr_codes_rendered: bool


def record_fixity_digest(record: DisclosedRecord) -> str:
    """The SHA-256 hex digest of ``record``'s disclosed, canonical JSON.

    Deterministic for a given disclosed shape (same fixity everywhere ledger hashes
    anything: content-addressed, reproducible). This is what a reader re-derives,
    offline or online, to confirm a printed page still matches the live record —
    it is not a BagIt payload-manifest digest (this booklet carries descriptive
    metadata, not payload bytes).
    """
    return hash_bytes(
        canonical_json(record.to_dict(withheld_reasons=False)).encode("utf-8"), HashAlgo.SHA256
    )


def _fixity_verify_string(record: DisclosedRecord, *, base_url: str) -> str:
    root = base_url.rstrip("/")
    digest = record_fixity_digest(record)
    return f"{root}/record/{record.record_id} sha256:{digest}"


def _qr_svg(data: str, *, label: str) -> str:
    """An inline, accessible SVG QR code for ``data``, or ``""`` if segno is absent.

    The SVG itself is marked ``aria-hidden`` and wrapped in a labelled ``<span
    role="img">`` — a screen reader announces ``label`` once rather than trying to
    describe QR module geometry; the visible fixity-digest text next to every QR
    (rendered by the caller, not here) is the real accessible equivalent.
    """
    if not _HAVE_SEGNO:
        return ""
    qr = segno.make(data, error="m")
    svg = qr.svg_inline(scale=3, dark="#000000", light=None, border=2)
    svg = svg.replace("<svg ", '<svg aria-hidden="true" focusable="false" ', 1)
    return f'<span class="qr" role="img" aria-label="{_esc(label)}">{svg}</span>'


def _record_section_html(record: DisclosedRecord, *, base_url: str, index: int) -> str:
    verify_string = _fixity_verify_string(record, base_url=base_url)
    digest = record_fixity_digest(record)
    qr = _qr_svg(verify_string, label=f"Fixity QR code for {record.title}")

    cw_html = ""
    if record.content_warnings:
        items = "".join(f"<li>{_esc(w)}</li>" for w in record.content_warnings)
        cw_html = (
            '<div class="cw" role="note">\n'
            "<p><strong>Content warning</strong> — this entry discusses:</p>\n"
            f"<ul>{items}</ul>\n</div>\n"
        )

    dc_items = "".join(
        f"<li><strong>{_esc(k)}:</strong> {_esc('; '.join(v))}</li>"
        for k, v in sorted(record.dublin_core.items())
        if v
    )
    field_items = "".join(
        f"<li><strong>{_esc(k)}:</strong> {_esc(v)}</li>" for k, v in sorted(record.fields.items())
    )

    return (
        f'<article class="entry" aria-labelledby="entry-{index}-heading">\n'
        f'<h2 id="entry-{index}-heading">{index}. {_esc(record.title)}</h2>\n'
        f"{cw_html}"
        f'<ul class="dc">{dc_items}{field_items}</ul>\n'
        '<footer class="fixity">\n'
        f"<p>Verify: <code>{_esc(verify_string)}</code></p>\n"
        f"<p>SHA-256: <code>{_esc(digest)}</code></p>\n"
        f"{qr}\n"
        "</footer>\n"
        "</article>\n"
    )


_BOOKLET_CSS = (
    ":root{color-scheme:light dark}"
    "body{font-family:Georgia,'Times New Roman',serif;max-width:38rem;margin:2rem auto;"
    "padding:0 1rem;line-height:1.5}"
    ".skip-link{position:absolute;left:-999px}"
    ".skip-link:focus{position:static;display:inline-block;margin:0.5rem}"
    ".entry{margin:2rem 0 3rem}"
    ".cw{border:2px solid currentColor;padding:0.75rem 1rem;margin:1rem 0}"
    ".fixity code{word-break:break-all;font-size:0.85em}"
    ".qr{display:inline-block;margin-top:0.5rem}"
    "@media print{"
    ".entry{break-after:page}"
    ".skip-link,nav{display:none}"
    "}"
)


def build_print_edition(
    archive: Archive,
    out_path: Path,
    *,
    record_ids: Sequence[str] | None = None,
    archive_name: str = "",
    base_url: str = "",
    lang: str = "en",
    now: str | None = None,
) -> PrintEditionResult:
    """Render selected PUBLIC records as a single accessible HTML booklet at ``out_path``.

    Always disclosed under :func:`~ledger.access.grants.anonymous` — the narrowest
    grant — regardless of any caller state, so the booklet is PUBLIC-only *by
    construction* (safety: a printed page cannot later be redacted). When
    ``record_ids`` is given, the booklet includes only those ids that are actually
    PUBLIC-listable right now; an id that is not (unknown, withdrawn, or simply not
    public) is silently omitted rather than erroring, so the booklet's contents
    never confirm or deny the existence of a non-public record (no-outing rule).
    With no ``record_ids``, every PUBLIC-listable record is included, in the
    archive's stable browse order.
    """
    stamp = now if now is not None else now_iso()
    grant = anonymous()
    available = {r.record_id: r for r in archive.browse(grant, now=stamp)}
    if record_ids is None:
        records = list(available.values())
    else:
        records = [available[rid] for rid in record_ids if rid in available]

    sections = "\n".join(
        _record_section_html(r, base_url=base_url, index=i) for i, r in enumerate(records, start=1)
    )
    toc_items = "".join(
        f'<li><a href="#entry-{i}-heading">{_esc(r.title)}</a></li>'
        for i, r in enumerate(records, start=1)
    )
    qr_note = (
        ""
        if _HAVE_SEGNO
        else (
            "<p><em>Scannable QR codes were not rendered because the optional "
            '"segno" package is not installed (<code>pip install ledger-archive[print]</code>). '
            "The text fixity line under each entry still verifies without one.</em></p>\n"
        )
    )
    title = f"{archive_name or 'ledger'} — print edition"
    body = (
        "<!doctype html>\n"
        f'<html lang="{_esc(lang)}">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_esc(title)}</title>\n"
        f"<style>{_BOOKLET_CSS}</style>\n"
        "</head>\n<body>\n"
        '<a class="skip-link" href="#main">Skip to content</a>\n'
        '<p role="note">Generated for offline/print distribution; PUBLIC records only.</p>\n'
        f'<main id="main" tabindex="-1">\n<h1>{_esc(title)}</h1>\n'
        f"{qr_note}"
        f'<nav aria-label="Contents"><h2>Contents</h2><ol>{toc_items}</ol></nav>\n'
        f"{sections}"
        "</main>\n</body>\n</html>\n"
    )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(body, encoding="utf-8")

    return PrintEditionResult(
        out_path=out_path, records_included=len(records), qr_codes_rendered=_HAVE_SEGNO
    )
