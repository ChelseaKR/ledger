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
* **identity translation** (repo-local, not one of the numbered standard gates) —
  ``en`` is the source language, so its msgstr must *equal* its msgid; every other
  catalog's must *differ* from it, unless the msgid is untranslatable by
  construction or the pair is listed in ``identical_by_design.json`` with a written
  reason. See :func:`_identity_errors`.
* **Review status is declared** — every non-source catalog's PO header carries an
  ``X-Translation-Review`` field equal to :func:`ledger.i18n.translation_review`
  for that locale, and this script prints the status of every locale it passes.

**Why the identity check exists, and what it still cannot see.** Key parity,
completeness and placeholder parity are all satisfied by a Spanish ``msgstr`` that
is verbatim English: it is non-empty, its key matches, and its placeholder set is
trivially identical. Until this check landed, ``make i18n`` printed ``catalog
parity OK`` over a catalog nobody had translated.

A blanket "a non-English msgstr must differ from its msgid" rule is the wrong
repair, and wrong in the direction that gets a gate switched off. Measured on this
repository: 2 of 273 Spanish and 6 of 273 French msgstrs are byte-identical to
their msgid, and every one of them is correct — ``No`` in Spanish; ``Agent``,
``Date``, ``Description``, ``Pagination``, ``Type`` and ``Types`` in French. So the
rule has three ways out, and only the third is a judgement:

1. **Nothing to translate.** With ``{placeholders}`` removed the msgid has no
   alphabetic character — a bare number, a lone ``{when}``.
2. **A code token, not prose.** A single all-caps token (``OK``, ``CSV``, ``PDF``,
   ``WCAG``) or a bare URL. Upper case is required deliberately, so ``Help``,
   ``Date`` and ``Senior`` are short words rather than acronyms and stay in scope.
3. **A written reason** in ``identical_by_design.json``, for anything else
   genuinely identical in the target language — a cognate, a product name, a proper
   noun. A pattern cannot make that call, so a person writes it down, and the list
   is checked for staleness so it describes the catalogs rather than the project's
   history.

It still cannot tell good Spanish from bad Spanish. It fails a catalog that was
never translated; it says nothing about how a translation reads to a speaker of the
language. That is what the ``X-Translation-Review`` declaration is for, and
``REVIEWED_LOCALES`` is still empty.

Pure standard library + Babel's PO reader; no network, deterministic.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from babel.messages.catalog import Catalog, Message
from babel.messages.pofile import read_po

from ledger.i18n import PO_REVIEW_HEADER, SOURCE_LANG, SUPPORTED, translation_review

LOCALES = Path(__file__).resolve().parent.parent / "src" / "ledger" / "locales"
POT = LOCALES / "messages.pot"

#: Reasoned exemptions from the differ-from-source rule, for msgids that are
#: legitimately identical in a target language and that the two mechanical classes
#: below do not cover — a cognate, a product name, a proper noun.
EXEMPTIONS = LOCALES / "identical_by_design.json"

_FIELD = re.compile(r"\{[^{}]*\}")

#: A single token of capitals, digits and code punctuation: ``OK``, ``CSV``,
#: ``PDF``, ``WCAG``, ``TK-12``. Deliberately requires upper case, so an ordinary
#: short word (``Help``, ``Date``, ``Type``) is NOT exempt and has to be translated
#: or listed with a reason. Deliberately caps the length, so a shouted sentence
#: fragment does not slip through.
_CODE_TOKEN = re.compile(r"[A-Z0-9][A-Z0-9./+-]{1,7}")


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


def _strings(message: Message) -> tuple[str, ...]:
    """Every msgstr this message carries, as a tuple (one entry if singular).

    A missing msgstr becomes ``""``, which no msgid equals, so an untranslated row
    is reported by the completeness check above and not a second time here.
    """
    if isinstance(message.string, (tuple, list)):
        return tuple(form or "" for form in message.string)
    return (message.string or "",)


def _sources(message: Message) -> tuple[str, ...]:
    """Every source form this message carries: (singular,) or (singular, plural)."""
    if isinstance(message.id, (tuple, list)):
        return tuple(message.id)
    return (message.id,)


def _is_identical_to_source(message: Message) -> bool:
    """True when the translation adds no text the source did not already have.

    For a plural message this is deliberately "every form is one of the source
    forms" rather than a positional comparison: Arabic declares six plural forms
    where English declares two, and a catalog that fills all six with English is
    untranslated whichever way round they landed.
    """
    return set(_strings(message)) <= set(_sources(message))


def untranslatable_reason(msgid: str) -> str | None:
    """Why this msgid is legitimately the same in every language, or ``None``.

    Two mechanical classes only. Both are about the msgid having no prose in it to
    translate, which is checkable; neither is about a *word* being the same in two
    languages, which is not. An over-wide exemption is worse than no gate, because
    it reads as coverage — so anything outside these two classes takes a written
    reason in :data:`EXEMPTIONS` instead of a pattern.
    """
    bare = _FIELD.sub(" ", msgid).strip()
    if not any(char.isalpha() for char in bare):
        return "no alphabetic content once the placeholders are removed"
    if len(bare.split()) == 1:
        if "://" in bare:
            return "a bare URL"
        if _CODE_TOKEN.fullmatch(bare):
            return "a single all-caps code token (acronym, format name, or identifier)"
    return None


def _load_exemptions(path: Path) -> tuple[dict[tuple[str, str], str], list[str]]:
    """Read the reasoned exemption list, or report why it cannot be read.

    A missing file is fine and means "no exemptions"; a malformed one is an error
    rather than a silent empty list, because an exemption file that fails open turns
    the check it guards into one that cannot fail.
    """
    if not path.is_file():
        return {}, []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {}, [f"identity: {path.name} could not be read as JSON: {exc}"]
    if not isinstance(payload, dict) or not isinstance(payload.get("identical_by_design"), list):
        return {}, [f"identity: {path.name} must be an object with an identical_by_design list"]

    exemptions: dict[tuple[str, str], str] = {}
    errors: list[str] = []
    for index, entry in enumerate(payload["identical_by_design"]):
        if not isinstance(entry, dict):
            errors.append(f"identity: {path.name} entry {index} is not an object")
            continue
        locale, msgid, reason = entry.get("locale"), entry.get("msgid"), entry.get("reason")
        if not isinstance(locale, str) or not isinstance(msgid, str):
            errors.append(f"identity: {path.name} entry {index} needs a locale and a msgid")
            continue
        if not isinstance(reason, str) or not reason.strip():
            errors.append(
                f"identity: {path.name} exempts {locale}/{msgid!r} with no reason. "
                "An exemption without a written reason is the gate switched off for that row"
            )
            continue
        exemptions[(locale, msgid)] = reason
    return exemptions, errors


def _identity_errors(
    catalogs: dict[str, Catalog],
    exemptions: dict[tuple[str, str], str],
) -> list[str]:
    """The source catalog must match its msgids; the others must not.

    The gap this closes, stated plainly: key parity, completeness and placeholder
    parity are all satisfied by a Spanish catalog that is verbatim English. Its keys
    match, its msgstrs are non-empty, and its placeholders are trivially identical.
    Nothing else in ``make i18n`` looks at whether any translating happened.
    """
    errors: list[str] = []
    targets = [name for name in catalogs if name != SOURCE_LANG]
    if not targets:
        return [
            f"identity: no catalog other than {SOURCE_LANG!r} was checked, so the "
            "differ-from-source rule ran against nothing. Add the target locales to "
            "ledger.i18n.SUPPORTED or remove this check with them"
        ]

    source = catalogs.get(SOURCE_LANG)
    if source is not None:
        for message in source:
            if not message.id or _is_identical_to_source(message):
                continue
            errors.append(
                f"identity: the {SOURCE_LANG} msgstr for {_key(message)!r} differs from its "
                "msgid. The source catalog is an identity map by construction (docs/I18N.md: "
                "'the source string is the English text itself'); edit the source string and "
                "re-extract instead"
            )

    for name in targets:
        for message in catalogs[name]:
            if not message.id or not _is_identical_to_source(message):
                continue
            key = _key(message)
            if untranslatable_reason(key) is not None:
                continue
            if (name, key) in exemptions:
                continue
            errors.append(
                f"identity: {name}'s msgstr for {key!r} is byte-identical to the English "
                "msgid, which every other check in this gate accepts. Translate it, or -- if "
                f"it is genuinely the same in {name} -- add it to {EXEMPTIONS.name} with a reason"
            )
    return errors


def _stale_exemption_errors(
    catalogs: dict[str, Catalog],
    pot_ids: set[str],
    exemptions: dict[tuple[str, str], str],
) -> list[str]:
    """An exemption that is not currently doing anything must be deleted.

    Without this the list only ever grows, and a list that only grows stops
    describing the catalogs and starts describing the project's history.
    """
    errors: list[str] = []
    for locale, msgid in sorted(exemptions):
        if locale not in catalogs:
            errors.append(
                f"identity: {EXEMPTIONS.name} exempts locale {locale!r}, which is not a "
                f"checked catalog ({', '.join(catalogs)})"
            )
            continue
        if locale == SOURCE_LANG:
            errors.append(
                f"identity: {EXEMPTIONS.name} exempts the source locale {locale!r}, where "
                "identity is required rather than excused"
            )
            continue
        if msgid not in pot_ids:
            errors.append(
                f"identity: {EXEMPTIONS.name} exempts {locale}/{msgid!r}, which the template "
                "no longer declares. Remove the entry"
            )
            continue
        message = catalogs[locale].get(msgid)
        if message is None or not _is_identical_to_source(message):
            errors.append(
                f"identity: {EXEMPTIONS.name} exempts {locale}/{msgid!r}, which is now "
                "translated. Remove the entry"
            )
    return errors


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
            errors.append(f"G5: {loc} is missing msgids present in the template: {sorted(missing)}")

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
        found = _po_header_field(LOCALES / name / "LC_MESSAGES" / "messages.po", PO_REVIEW_HEADER)
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

    exemptions, exemption_errors = _load_exemptions(EXEMPTIONS)
    errors += exemption_errors
    errors += _identity_errors(catalogs, exemptions)
    errors += _stale_exemption_errors(catalogs, pot_ids, exemptions)

    if errors:
        print("catalog parity FAILED:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    statuses = ", ".join(f"{loc}={translation_review(loc).value}" for loc in SUPPORTED)
    targets = [loc for loc in SUPPORTED if loc != SOURCE_LANG]
    print(
        f"catalog parity OK: {len(pot_ids)} msgids across {', '.join(SUPPORTED)}; "
        "key-parity + completeness + placeholder parity hold; "
        f"{', '.join(targets)} differ from the {SOURCE_LANG} source "
        f"({len(exemptions)} reasoned exemptions)."
    )
    print(f"translation review status: {statuses}")
    print(
        "  The identity check catches a catalog nobody translated; it cannot tell a "
        "good translation from a bad one. Review status is declared, not measured."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
