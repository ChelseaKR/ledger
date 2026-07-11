#!/usr/bin/env python3
"""G1 UTF-8 byte-encoding gate (merge-blocking).

INTERNATIONALIZATION-STANDARD §2 requires every localized artifact to be valid
UTF-8 end to end, so an accented French word or an Arabic sentence can never be
mangled by a stray Latin-1 byte. This gate verifies:

* every committed catalog under ``src/ledger/locales`` (each ``.po`` and the
  ``.pot`` template) decodes as **strict** UTF-8, and each declares
  ``charset=utf-8`` in its header; and
* a page rendered in every shipped locale (:data:`ledger.i18n.SUPPORTED`) round-trips
  through UTF-8 unchanged and declares ``<meta charset="utf-8">`` — so the bytes a
  browser receives are UTF-8, matching the ``text/html; charset=utf-8`` the server
  sends.

Pure standard library + ledger's own render seam; deterministic, no network.
"""

from __future__ import annotations

import sys
from pathlib import Path

from ledger import i18n
from ledger.render import _page

LOCALES = Path(__file__).resolve().parent.parent / "src" / "ledger" / "locales"


def _check_catalog_bytes(errors: list[str]) -> None:
    for path in sorted(LOCALES.rglob("*.po")) + [LOCALES / "messages.pot"]:
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            errors.append(f"G1: {path} is not valid UTF-8: {exc}")
            continue
        if "charset=utf-8" not in text.lower():
            errors.append(f"G1: {path} does not declare charset=utf-8 in its header")


def _check_rendered_pages(errors: list[str]) -> None:
    for lang in i18n.SUPPORTED:
        page = _page(
            i18n.t(lang, "nav_browse"),
            lang=lang,
            main_html=f"<h1>{i18n.t(lang, 'overview_heading')}</h1>",
        )
        try:
            encoded = page.encode("utf-8")
        except UnicodeEncodeError as exc:  # pragma: no cover - str is always encodable
            errors.append(f"G1: rendered {lang} page is not UTF-8 encodable: {exc}")
            continue
        if encoded.decode("utf-8") != page:
            errors.append(f"G1: rendered {lang} page did not round-trip through UTF-8")
        if '<meta charset="utf-8">' not in page:
            errors.append(f"G1: rendered {lang} page does not declare <meta charset=utf-8>")


def main() -> int:
    errors: list[str] = []
    _check_catalog_bytes(errors)
    _check_rendered_pages(errors)
    if errors:
        print("UTF-8 gate FAILED:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    print(
        f"G1 OK: every catalog and every rendered page in {list(i18n.SUPPORTED)} is valid UTF-8."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
