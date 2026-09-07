#!/usr/bin/env python3
"""G6 key-parity + G5 completeness/placeholder gate (merge-blocking).

Enforces, over every shipped locale in ``src/ledger/locales``
(``ledger.i18n.SUPPORTED`` — currently en, es, fr, ar):

* **G6 key-parity** — the msgid set of every locale is identical to every other
  and covers every msgid in ``messages.pot``. A key present in one catalog but not
  another fails the build.
* **G5 completeness** — every msgstr (each plural form, including Arabic's six) is
  non-empty. Every shipped catalog is filled in, so completeness is enforced as a
  hard gate here rather than deferred: there is no untranslated backlog to wave
  through.
* **G5 placeholder parity** — the set of ``{...}`` fields is identical between each
  msgid and its translation, in every plural form (so a rename or dropped ``{name}``
  cannot ship).
* **Review status is declared** — every non-source catalog's PO header carries an
  ``X-Translation-Review`` field equal to :func:`ledger.i18n.translation_review`
  for that locale, and this script prints the status of every locale it passes.

**What these checks cannot see, stated because the gate's output would otherwise
imply otherwise.** Key parity, completeness and placeholder parity are all
satisfied by a Spanish ``msgstr`` that is verbatim English: it is non-empty, its
key matches, and its placeholder set is trivially identical. So a green run here
says the catalogs are *structurally* sound and says nothing whatever about whether
the translations are good, or whether anyone who speaks the language has read them.

A blanket "a non-English msgstr must differ from its msgid" rule would not fix
that and is deliberately **not** implemented: plenty of strings are legitimately
identical across these languages — ``No`` in Spanish, ``Agent``, ``Date``,
``Description``, ``Type`` in French, proper nouns, URLs, bare numbers. Measured on
this repository: 2 of 273 Spanish and 6 of 273 French msgstrs are identical to
their msgid, and every one of them is correct. A rule with a false-positive rate
like that gets suppressed or disabled, which is worse than no rule. An allowlist
would be needed, and building the allowlist is the design work; until someone does
it, the honest substitute is disclosure, which is what the review field gates.

Pure standard library + Babel's PO reader; no network, deterministic.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from babel.messages.catalog import Catalog, Message
from babel.messages.pofile import read_po

from ledger.i18n import PO_REVIEW_HEADER, SOURCE_LANG, SUPPORTED, translation_review

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


def _po_header_field(path: Path, field: str) -> str | None:
    """Read one PO header field from the file's own bytes.

    Deliberately not via Babel. ``read_po`` reconstructs the header from the fields
    it recognizes and **silently drops** an unknown ``X-`` field, so a check that
    asked the parsed catalog would report every catalog as undeclared no matter
    what the committed file says. What ships is the file, so the file is what is
    read. Stops at the blank line ending the header entry, so a later message
    cannot be mistaken for a header field.
    """
    prefix = f"{field.lower()}:"
    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        start = lines.index('msgid ""')
    except ValueError:
        return None
    for raw in lines[start + 2 :]:  # skip the `msgid ""` / `msgstr ""` pair
        line = raw.strip()
        if not line.startswith('"'):
            break  # the header entry ended
        text = line[1:].removesuffix('"')
        if text.lower().startswith(prefix):
            return text[len(prefix) :].removesuffix("\\n").strip()
    return None


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

    # Review status: declared in the code, restated in the catalog, checked to
    # agree. A catalog whose header says "reviewed" while the code says nobody has
    # read it -- or a new locale added to SUPPORTED with no declaration at all --
    # fails here rather than shipping a silent claim.
    for name in catalogs:
        if name == SOURCE_LANG:
            continue
        found = _po_header_field(
            LOCALES / name / "LC_MESSAGES" / "messages.po", PO_REVIEW_HEADER
        )
        expected = translation_review(name).value
        if found is None:
            errors.append(
                f"review: {name}'s PO header has no {PO_REVIEW_HEADER} field; it must "
                f"declare {expected!r} (see ledger.i18n.translation_review)"
            )
        elif found.strip() != expected:
            errors.append(
                f"review: {name}'s PO header says {PO_REVIEW_HEADER}: {found.strip()!r} "
                f"but ledger.i18n.translation_review says {expected!r}"
            )

    if errors:
        print("catalog parity FAILED:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    statuses = ", ".join(f"{loc}={translation_review(loc).value}" for loc in SUPPORTED)
    print(
        f"catalog parity OK: {len(pot_ids)} msgids across {', '.join(SUPPORTED)}; "
        "key-parity + completeness + placeholder parity hold."
    )
    print(f"translation review status: {statuses}")
    print(
        "  These checks cannot see whether a translation is correct, only whether it "
        "is present and structurally consistent; a verbatim-English msgstr passes all "
        "three. Review status is declared, not measured."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
