#!/usr/bin/env python3
"""G13 — the shipped ``.mo`` says what the reviewed ``.po`` says (merge-blocking).

The compiled ``messages.mo`` catalogs are committed (``docs/I18N.md`` explains why), and
``make i18n-compile`` is the only thing that writes them. It is not part of ``make
verify``, so the catalog that actually reaches a reader was a committed artifact standing
in for a computation nothing re-ran: edit a ``msgstr``, skip ``make i18n-compile``, and
every other gate stays green while the running program serves the old translation. G5 and
G6 read the ``.po`` and never open the ``.mo``.

Measured on this repository before this gate existed: one changed Spanish ``msgstr``, no
recompile, and G1, G5, G6, G7 all passed alongside 1295 green tests. ``docs/I18N.md``
claimed the render/server tests guarded this. They assert a handful of specific strings
(``"Browse"`` -> ``"Explorar"``), so they guard those strings and nothing else.

**Why this compares meaning and not bytes.** The first version of this gate compiled each
``.po`` into a temp dir with ``msgfmt`` and compared byte for byte. That passed on two
toolchains and failed on a third, on an unmodified tree:

    GNU gettext-tools 1.0    (macOS, Homebrew)     byte-identical
    GNU gettext-tools 0.23.1 (Debian trixie)       byte-identical
    GNU gettext-tools 0.21   (Debian bullseye)     same length, different bytes
    the CI runner's msgfmt                         different bytes

The MO format carries a hash table whose layout msgfmt has changed between releases, so
byte equality is a property of *which msgfmt ran*, not of the catalog. Forcing byte
equality on that would mean pinning every contributor and every runner to one gettext
build, or quietly weakening the gate later when it went red for no reason. Neither is
acceptable, and the sweep rule this gate came from is explicit: do not force byte equality
on a moving target, gate the stable subset and say what was excluded.

The stable subset is the whole point of the artifact: **every message a reader can be
shown**. This module reads the committed ``.mo`` with :mod:`gettext` — the same reader
:func:`ledger.i18n.get_translation` uses at runtime, so what is compared is literally what
the program will serve — and the ``.po`` with Babel, and requires the two message maps to
be equal in both directions. Excluded, and only these: the byte layout of the MO hash
table, and the header fields other than ``Plural-Forms`` (creation dates and generator
strings, which say nothing about what a reader sees). ``Plural-Forms`` *is* compared,
because a stale one selects the wrong form at runtime.

Pure standard library + Babel's PO reader, matching ``tools/check_catalog_parity.py``;
no network, deterministic. Nothing here writes: a gate that recompiles the artifact it is
judging heals drift on the contributor's disk while the committed bytes stay stale.
"""

from __future__ import annotations

import gettext
import sys
from pathlib import Path

from babel.messages.catalog import Catalog
from babel.messages.pofile import read_po

from ledger.i18n import DOMAIN, SUPPORTED

LOCALES = Path(__file__).resolve().parent.parent / "src" / "ledger" / "locales"

#: A MO entry key: a plain msgid, or ``(singular, plural_index)`` for a plural message.
Key = str | tuple[str, int]


def _po_path(locale: str) -> Path:
    return LOCALES / locale / "LC_MESSAGES" / f"{DOMAIN}.po"


def _mo_path(locale: str) -> Path:
    return LOCALES / locale / "LC_MESSAGES" / f"{DOMAIN}.mo"


def _read_po(path: Path) -> Catalog:
    with path.open("rb") as fh:
        return read_po(fh)


def expected_from_po(catalog: Catalog) -> dict[Key, str]:
    """Build the message map ``msgfmt`` would produce from this PO catalog.

    ``msgfmt`` writes an entry for every message with a non-empty translation, skipping
    fuzzy and obsolete ones and the header. Those are the same rules applied here, so a
    disagreement means the catalogs differ, not that the two tools disagree about what
    belongs in a MO file.
    """
    expected: dict[Key, str] = {}
    for message in catalog:
        if not message.id or message.fuzzy:
            continue
        if isinstance(message.id, (list, tuple)):
            singular = message.id[0]
            forms = message.string if isinstance(message.string, (list, tuple)) else ()
            if not all(forms):
                continue
            for index, form in enumerate(forms):
                expected[(singular, index)] = form
        elif message.string:
            expected[message.id] = str(message.string)
    return expected


def actual_from_mo(path: Path) -> tuple[dict[Key, str], str]:
    """Read the committed MO with :mod:`gettext` and return its map and Plural-Forms.

    Reading through :mod:`gettext` rather than parsing the file by hand is deliberate:
    it is the reader :func:`ledger.i18n.get_translation` uses, so this gate compares what
    the running program will actually serve, not an independent interpretation of the
    bytes.
    """
    with path.open("rb") as fh:
        translations = gettext.GNUTranslations(fh)
    catalog: dict[Key, str] = dict(translations._catalog)
    header = str(catalog.pop("", ""))
    plural_forms = ""
    for line in header.splitlines():
        if line.lower().startswith("plural-forms:"):
            plural_forms = line.split(":", 1)[1].strip()
    return catalog, plural_forms


def _describe(key: Key) -> str:
    if isinstance(key, tuple):
        return f"{key[0]!r} (plural form {key[1]})"
    return repr(key)


def compare(locale: str) -> list[str]:
    """Return the problems found for one locale; empty means the catalogs agree."""
    po_path, mo_path = _po_path(locale), _mo_path(locale)
    if not mo_path.is_file():
        return [f"{locale}: {mo_path} is missing — compile it with `make i18n-compile`"]
    if not po_path.is_file():
        return [f"{locale}: {po_path} is missing"]

    po_catalog = _read_po(po_path)
    expected = expected_from_po(po_catalog)
    actual, mo_plural_forms = actual_from_mo(mo_path)

    problems: list[str] = []

    # A comparison over an empty map would pass for any pair of catalogs, which is the
    # failure mode this whole file exists to prevent. Refuse to report success on one.
    if not expected:
        problems.append(f"{locale}: {po_path} yielded no translatable messages")
        return problems

    for key in sorted(expected.keys() - actual.keys(), key=str):
        problems.append(f"{locale}: {_describe(key)} is in the .po but missing from the .mo")
    for key in sorted(actual.keys() - expected.keys(), key=str):
        problems.append(f"{locale}: {_describe(key)} is in the .mo but not in the .po")
    for key in sorted(expected.keys() & actual.keys(), key=str):
        if expected[key] != actual[key]:
            problems.append(
                f"{locale}: {_describe(key)} differs — "
                f".po says {expected[key]!r}, the shipped .mo says {actual[key]!r}"
            )

    po_plural_forms = (po_catalog.plural_forms or "").strip()
    if po_plural_forms and mo_plural_forms and po_plural_forms != mo_plural_forms:
        problems.append(
            f"{locale}: Plural-Forms differs — .po says {po_plural_forms!r}, "
            f"the shipped .mo says {mo_plural_forms!r}; the wrong form would be selected"
        )
    return problems


def main() -> int:
    """Check every shipped locale; return ``1`` and name every disagreement on failure."""
    problems: list[str] = []
    checked = 0
    for locale in sorted(SUPPORTED):
        found = compare(locale)
        problems.extend(found)
        if not found:
            checked += len(expected_from_po(_read_po(_po_path(locale))))

    if problems:
        print(
            "G13 FAILED — a shipped .mo does not say what its reviewed .po says.\n"
            "  Recompile with: make i18n-compile",
            file=sys.stderr,
        )
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print(
        f"G13 OK: {', '.join(sorted(SUPPORTED))} messages.mo carry exactly the messages "
        f"their messages.po declares ({checked} entries compared)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
