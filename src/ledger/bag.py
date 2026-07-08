"""BagIt packaging per IETF RFC 8493 — the archive's hand-off format.

EXP-05 (docs/ideation/03-expansions.md): this module's implementation now lives
in the standalone, independently installable and independently tested
:mod:`ledger_preservation_core.bag` library, extracted so the BagIt/PREMIS
packaging is genuinely "usable on its own" (the claim the README already made).
This is a thin, behaviour-preserving re-export: every name below is the
identical object defined in the core library, so ``ledger.bag.Bag is
ledger_preservation_core.bag.Bag`` and every existing
``from ledger.bag import ...`` keeps working unchanged.

.. warning::
   ``bag-info.txt`` is human-readable metadata that travels with the payload in
   the clear. It MUST NEVER carry a contributor's identity, contact, or any
   sealed field value. Identity lives only in the encrypted vault
   (:mod:`ledger.identity`). :func:`write_bag` injects nothing of its own beyond
   ``Payload-Oxum``; every other ``bag-info.txt`` value is caller-controlled,
   and the caller bears the same duty.
"""

from __future__ import annotations

from ledger_preservation_core.bag import Bag, validate_bag, write_bag

__all__ = ["Bag", "validate_bag", "write_bag"]
