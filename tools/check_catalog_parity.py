#!/usr/bin/env python3
"""G6 key-parity + G5 completeness/placeholder gate (merge-blocking).

Enforces, over every shipped locale in ``src/ledger/locales``
(``ledger.i18n.SUPPORTED`` — currently en, es, fr, ar):

* **G6 key-parity** — the msgid set of every locale is identical to every other
  and covers every msgid in ``messages.pot``. A key present in one catalog but not
  another fails the build.
* **G5 completeness** — every msgstr (each plural form, including Arabic's six) is
  non-empty. ledger's translations are real, human-authored (Spanish migrated from
  the retired bespoke ``_CATALOG``/``_CW_GLOSSES`` dicts; French and Arabic authored
  for RM7), so completeness is enforced as a hard gate here rather than deferred:
  there is no untranslated backlog to wave through.
* **G5 placeholder parity** — the set of ``{...}`` fields is identical between each
  msgid and its translation, in every plural form (so a rename or dropped ``{name}``
  cannot ship).

Pure standard library + Babel's PO reader; no network, deterministic.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from babel.messages.catalog import Catalog, Message
from babel.messages.pofile import read_po

from ledger.i18n import SUPPORTED

LOCALES = Path(__file__).resolve().parent.parent / "src" / "ledger" / "locales"
POT = LOCALES / "messages.pot"

_FIELD = re.compile(r"\{[^{}]*\}")


def _load(path: Path, locale: str | None) -> Catalog:
    with path.open("rb") as fh:
        return read_po(fh, locale=locale)


def _key(message: Message) -> str:
    """A hashable identity for a message (the singular msgid for plurals)."""
    return message.id[0] if isinstance(message.id, (tuple, list)) else message.id


def _ids(catalog: Catalog) -> set[str]:
    return {_key(m) for m in catalog if m.id}


def _fields(text: str) -> set[str]:
    return set(_FIELD.findall(text))


def main() -> int:
    errors: list[str] = []

    pot = _load(POT, None)
    pot_ids = _ids(pot)

    catalogs: dict[str, Catalog] = {
        loc: _load(LOCALES / loc / "LC_MESSAGES" / "messages.po", loc) for loc in SUPPORTED
    }
    ids: dict[str, set[str]] = {loc: _ids(cat) for loc, cat in catalogs.items()}

    # G6: key-parity across every shipped locale (each identical to the template's
    # msgid set, hence identical to one another).
    for loc, loc_ids in ids.items():
        extra = loc_ids - pot_ids
        missing = pot_ids - loc_ids
        if extra:
            errors.append(f"G6: {loc} has msgids not in the template: {sorted(extra)}")
        if missing:
            errors.append(
                f"G5: {loc} is missing msgids present in the template: {sorted(missing)}"
            )

    # G5: every msgstr (each plural form) non-empty, placeholders preserved.
    for name, catalog in catalogs.items():
        for message in catalog:
            if not message.id:
                continue
            src_fields = _fields(_key(message))
            if isinstance(message.id, (tuple, list)):
                src_fields |= _fields(message.id[1])
                forms = message.string if isinstance(message.string, (tuple, list)) else ()
                if not forms or any(not s for s in forms):
                    errors.append(f"G5: {name} has an empty plural form for {_key(message)!r}")
                    continue
                for form in forms:
                    if _fields(form) != src_fields:
                        errors.append(
                            f"G5: {name} placeholder mismatch in plural {_key(message)!r}: "
                            f"{_fields(form)} != {src_fields}"
                        )
            else:
                target = message.string
                if not target:
                    errors.append(f"G5: {name} has an empty msgstr for {message.id!r}")
                    continue
                if _fields(target) != src_fields:
                    errors.append(
                        f"G5: {name} placeholder mismatch in {message.id!r}: "
                        f"{_fields(target)} != {src_fields}"
                    )

    if errors:
        print("catalog parity FAILED:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    print(
        f"catalog parity OK: {len(pot_ids)} msgids across {', '.join(SUPPORTED)}; "
        "key-parity + completeness + placeholder parity hold."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
