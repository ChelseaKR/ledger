"""Locally-minted persistent identifiers (ARK-style PIDs).

Scholarship needs a *stable, quotable* handle for a record that does not change if
the archive moves hosts or reorganizes its URLs (user research P2-3, D1). This
module mints an `ARK <https://arks.org/>`_-style identifier deterministically from
a record's opaque id, so:

* it is **pure and offline** — no network call, no external minting authority, no
  clock; the same record id always yields the same PID (reproducibility);
* it is **archive-local** — the archive is its own naming authority under a
  placeholder NAAN and shoulder, so a collective can stand one up "in an
  afternoon" without registering with a global resolver (affordability); and
* it is **discoverable** — the PID is added to the record's Dublin Core
  ``identifier`` element, the standard home for a resource's identifiers, so a
  general-purpose catalogue indexes it (interoperability, standards-compliance).

No-outing rule: a PID is derived only from the record's already-opaque id, which
is itself never an identity. This module introduces no contributor information.
"""

from __future__ import annotations

import re

__all__ = ["ARK_PREFIX", "DEFAULT_NAAN", "DEFAULT_SHOULDER", "is_ark", "mint_ark"]

# The ARK scheme prefix. An ARK looks like ``ark:/<NAAN>/<name>`` (some registries
# also accept ``ark:<NAAN>/<name>``); we emit the widely-used slashed form.
ARK_PREFIX = "ark:"

# Placeholder Name Assigning Authority Number. ``99999`` is the reserved
# "example/test" NAAN in the ARK ecosystem, so an archive that has not registered a
# real NAAN still mints well-formed, obviously-local ARKs rather than colliding with
# a registered authority (honesty, safety). A deployment overrides this with its own
# assigned NAAN when it has one.
DEFAULT_NAAN = "99999"

# An archive-local shoulder namespaces this archive's minted names beneath the NAAN,
# following ARK "shoulder" convention (a short opaque prefix on the name).
DEFAULT_SHOULDER = "l"

# Characters ARK names must not carry (ARK reserves ``. / ? # @`` and requires no
# whitespace). A record id is an opaque hex/token string, so this only ever fires on
# a crafted id; we strip rather than raise so minting never takes a path down.
_ARK_UNSAFE = re.compile(r"[\s./?#@%]")


def mint_ark(
    record_id: str,
    *,
    naan: str = DEFAULT_NAAN,
    shoulder: str = DEFAULT_SHOULDER,
) -> str:
    """Mint a deterministic, archive-local ARK PID for ``record_id``.

    Pure function: the identifier is a direct, reproducible transform of
    ``record_id`` (no hashing surprise, no network, no clock), so the *same* record
    always resolves to the *same* PID on every machine and every run
    (determinism). The form is ``ark:/<naan>/<shoulder><record_id>``.

    ``record_id`` is sanitized to the ARK name character set (reserved punctuation
    and whitespace removed); a real opaque record id passes through unchanged, and a
    crafted id can never break the identifier's structure (robustness, safety).

    Raises :class:`ValueError` on an empty ``record_id`` — there is no meaningful
    persistent identifier for an unnamed record (fail closed).
    """
    name = _ARK_UNSAFE.sub("", record_id).strip()
    if not name:
        raise ValueError("cannot mint an ARK for an empty record id")
    return f"{ARK_PREFIX}/{naan}/{shoulder}{name}"


def is_ark(value: str) -> bool:
    """Whether ``value`` is one of *this archive's* minted ARK PIDs.

    A cheap, prefix-based check (``ark:/<naan-or-any>/…``) used by the citation
    block to pick the PID out of the Dublin Core ``identifier`` list without having
    to re-derive it. It accepts any ARK, not only the default NAAN, so a deployment
    that has registered its own NAAN still has its PIDs recognised.
    """
    return value.startswith(f"{ARK_PREFIX}/") and value.count("/") >= 2
