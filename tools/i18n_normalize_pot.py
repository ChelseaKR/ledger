#!/usr/bin/env python3
"""Normalize a freshly extracted ``messages.pot`` into its committed form.

`pybabel extract` writes a few tokens that are volatile across machines, dates,
and Babel patch releases (the POT creation timestamp, the Babel version in
``Generated-By``, and the current year in the boilerplate comment block). It also
tags any string containing a literal ``%`` — e.g. ``95%`` or ``{pct}%`` — as
``python-format`` (printf style) *in addition to* ``python-brace-format``. That is
a false positive here: ledger's messages are rendered with ``str.format``, never
``%``-formatting, so ``msgfmt --check-format`` would wrongly reject the literal
percent signs. We drop the spurious ``python-format`` flag.

This runs both when the catalog is authored and inside the ``make i18n`` gate
(``pybabel extract`` → normalize → ``git diff --exit-code``), so the committed
template stays byte-stable and "POT is current" is a meaningful, non-flaky check.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def normalize(text: str) -> str:
    # 1. Freeze volatile header values to fixed tokens.
    text = re.sub(
        r'"POT-Creation-Date:.*?\\n"', '"POT-Creation-Date: 2026-06-30 00:00+0000\\\\n"', text
    )
    text = re.sub(r'"Generated-By:.*?\\n"', '"Generated-By: Babel\\\\n"', text)
    # 2. Freeze the auto-generated year tokens in the boilerplate comment block.
    text = re.sub(
        r"# Copyright \(C\) \d{4} ORGANIZATION", "# Copyright (C) YEAR ORGANIZATION", text
    )
    text = re.sub(
        r"# FIRST AUTHOR <EMAIL@ADDRESS>, \d{4}\.", "# FIRST AUTHOR <EMAIL@ADDRESS>, YEAR.", text
    )
    # 3. Drop the spurious ``python-format`` flag (keep every other flag/order).
    out_lines: list[str] = []
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\n")
        if stripped.startswith("#,"):
            flags = [f.strip() for f in stripped[2:].split(",") if f.strip()]
            flags = [f for f in flags if f != "python-format"]
            if not flags:
                continue  # nothing left — drop the comment line entirely
            newline = "\n" if line.endswith("\n") else ""
            out_lines.append("#, " + ", ".join(flags) + newline)
        else:
            out_lines.append(line)
    # 4. Collapse trailing blank lines to a single newline so the committed POT is
    #    byte-stable (Babel emits a trailing blank line after the last entry).
    return "".join(out_lines).rstrip("\n") + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: i18n_normalize_pot.py <messages.pot>", file=sys.stderr)
        return 2
    path = Path(argv[1])
    path.write_text(normalize(path.read_text(encoding="utf-8")), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
