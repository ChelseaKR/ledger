#!/usr/bin/env python3
"""G12 CLDR / locale-data freshness pin (merge-blocking).

INTERNATIONALIZATION-STANDARD §8 asks that the CLDR/locale-data version ledger
relies on be *pinned and asserted*, not whatever happens to be installed, so plural
rules and locale metadata cannot drift silently between machines or CI runs. ledger
has no native ICU/tzdata dependency; its CLDR data is whatever the pinned **Babel**
bundles (see the ``babel>=2.16,<3`` pin in ``pyproject.toml``). This gate makes that
pin observable and enforced:

* the installed Babel is within the declared, reviewed range; and
* the bundled CLDR data actually resolves — Babel can parse a locale and read a
  known CLDR datum from it (a smoke test that the data files shipped and load).

Bumping past the upper bound is a deliberate, reviewed step (adopt the new CLDR),
never a silent transitive upgrade.
"""

from __future__ import annotations

import sys

import babel
from babel import Locale

# The reviewed Babel range (keep in lockstep with pyproject.toml's dev pin). The
# lower bound guarantees the API the gates use; the upper bound is the CLDR ceiling.
_MIN = (2, 16)
_MAX_EXCLUSIVE = (3, 0)


def _version_tuple(text: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in text.split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def main() -> int:
    errors: list[str] = []
    ver = _version_tuple(babel.__version__)
    if not (_MIN <= ver[:2] < _MAX_EXCLUSIVE):
        errors.append(
            f"babel {babel.__version__} is outside the reviewed CLDR range "
            f">={_MIN[0]}.{_MIN[1]},<{_MAX_EXCLUSIVE[0]}: bumping the CLDR data is a "
            "deliberate, reviewed step (update pyproject.toml and this pin together)."
        )

    # Smoke-test the bundled CLDR data: a known plural category and a locale display
    # name must resolve, proving the data files shipped and load on this machine.
    try:
        arabic = Locale.parse("ar")
        # Arabic has the full six CLDR plural categories — a stable CLDR fact.
        categories = set(arabic.plural_form.rules.keys()) | {"other"}
        if not {"zero", "one", "two", "few", "many", "other"} <= categories:
            errors.append(
                f"CLDR plural data for 'ar' looks wrong (categories={sorted(categories)}); "
                "the bundled CLDR may be incomplete."
            )
        if not Locale.parse("fr").get_display_name("en"):
            errors.append("CLDR display-name data for 'fr' failed to resolve.")
    except Exception as exc:  # noqa: BLE001 - any failure here is a hard gate failure
        errors.append(f"CLDR data failed to load via Babel: {exc!r}")

    if errors:
        print("i18n dependency/CLDR pin FAILED:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    print(f"G12 OK: babel {babel.__version__} in range; CLDR locale data resolves.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
