#!/usr/bin/env python3
"""G3 BCP 47 / RFC 5646 language-tag validity gate (merge-blocking).

Every language tag ledger authors must be well-formed *and* resolvable against the
IANA/CLDR registry, so a typo'd or invented locale ("sp", "en_US" with the wrong
separator, "esp") never ships. We validate:

* the locale directory names under ``src/ledger/locales`` (each is a real gettext
  catalog we compile and load), and
* the ``SUPPORTED`` tuple and ``DEFAULT_LANG`` declared in ``ledger.i18n``,

by parsing each through ``babel.Locale.parse`` (CLDR-backed). Anything Babel cannot
resolve fails the build.
"""

from __future__ import annotations

import sys

from babel import Locale, UnknownLocaleError

# ledger is installed (editable) in the environment `make verify`/CI runs in.
from ledger.i18n import DEFAULT_LANG, LOCALEDIR, SUPPORTED


def _catalog_dirs() -> list[str]:
    if not LOCALEDIR.is_dir():
        return []
    return sorted(p.name for p in LOCALEDIR.iterdir() if (p / "LC_MESSAGES").is_dir())


def main() -> int:
    tags = set(SUPPORTED) | {DEFAULT_LANG} | set(_catalog_dirs())
    errors: list[str] = []
    for tag in sorted(tags):
        try:
            Locale.parse(tag)
        except (UnknownLocaleError, ValueError) as exc:
            errors.append(f"{tag!r}: {exc}")

    if DEFAULT_LANG not in SUPPORTED:
        errors.append(f"DEFAULT_LANG {DEFAULT_LANG!r} is not in SUPPORTED {SUPPORTED!r}")

    if errors:
        print("BCP 47 tag validation FAILED:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    print(f"BCP 47 OK: {sorted(tags)} are well-formed, registry-valid tags.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
