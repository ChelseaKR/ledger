"""Coverage for :func:`ledger.access.grants.load_grants` and `designated_successor`.

Mutation testing (`make mutation`, CQ-47) flagged `load_grants` as having **zero**
existing coverage — a real gap on the exact surface that decides who gets what
access from an on-disk grants file (least privilege, the no-outing rule). A
concrete bug this gap let through: a stray typo in the JSON spec's `levels` key
(e.g. `"level"` instead of `"levels"`) would silently fall back to the
`("public",)` default via `dict.get`'s fallback, instead of failing loudly —
quietly under-provisioning *or*, if the typo instead duplicated a working key,
over-provisioning a subject's access with no test anywhere to catch it.

All tests are marked ``disclosure`` — this is access-grant construction, the same
safety-critical class of surface `test_policy.py` covers.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ledger.access.grants import designated_successor, load_grants
from ledger.models import AccessPolicy

pytestmark = pytest.mark.disclosure


def test_load_grants_missing_file_returns_empty(tmp_path: Path) -> None:
    # Deny by default: absence of a grants file means no one is privileged.
    assert load_grants(tmp_path / "does-not-exist.json") == {}


def test_load_grants_reads_full_spec(tmp_path: Path) -> None:
    path = tmp_path / "grants.json"
    path.write_text(
        json.dumps(
            {
                "steward-a": {
                    "levels": ["public", "community", "stewards"],
                    "is_steward": True,
                    "identity_unseal": ["contributor-x"],
                    "expires_at": "2099-01-01T00:00:00Z",
                }
            }
        ),
        encoding="utf-8",
    )

    grants = load_grants(path)

    assert set(grants) == {"steward-a"}
    grant = grants["steward-a"]
    assert grant.subject == "steward-a"
    assert grant.levels == frozenset(
        {AccessPolicy.PUBLIC, AccessPolicy.COMMUNITY, AccessPolicy.STEWARDS}
    )
    assert grant.is_steward is True
    assert grant.identity_unseal == frozenset({"contributor-x"})
    assert grant.expires_at == "2099-01-01T00:00:00Z"


def test_load_grants_defaults_omitted_fields_to_least_privilege(tmp_path: Path) -> None:
    # A spec naming only the subject (no levels/is_steward/identity_unseal/expiry)
    # must resolve to the narrowest possible grant, never an implicit escalation.
    path = tmp_path / "grants.json"
    path.write_text(json.dumps({"anon-ish": {}}), encoding="utf-8")

    grants = load_grants(path)

    grant = grants["anon-ish"]
    assert grant.levels == frozenset({AccessPolicy.PUBLIC})
    assert grant.is_steward is False
    assert grant.identity_unseal == frozenset()
    assert grant.expires_at is None


def test_load_grants_keeps_each_subject_independent(tmp_path: Path) -> None:
    # One subject's elevated grant must never leak onto another's spec merely by
    # sharing a file — each entry is rebuilt from its own dict only.
    path = tmp_path / "grants.json"
    path.write_text(
        json.dumps(
            {
                "priv": {"levels": ["public", "community", "stewards"], "is_steward": True},
                "plain": {"levels": ["public"]},
            }
        ),
        encoding="utf-8",
    )

    grants = load_grants(path)

    assert grants["priv"].is_steward is True
    assert grants["plain"].is_steward is False
    assert grants["plain"].levels == frozenset({AccessPolicy.PUBLIC})


def test_designated_successor_inherits_stewardship_without_identity_unseal() -> None:
    # A folding group's successor gets full steward-level read/administer access
    # but, exactly like an ordinary steward, NEVER an implicit identity-unseal
    # token — inheriting the archive is not inheriting the power to out anyone.
    grant = designated_successor("new-caretaker", expires_at="2027-01-01T00:00:00Z")

    assert grant.subject == "new-caretaker"
    assert grant.levels == frozenset(
        {AccessPolicy.PUBLIC, AccessPolicy.COMMUNITY, AccessPolicy.STEWARDS}
    )
    assert grant.is_steward is True
    assert grant.identity_unseal == frozenset()
    assert grant.expires_at == "2027-01-01T00:00:00Z"


def test_designated_successor_expires_at_defaults_to_none() -> None:
    grant = designated_successor("caretaker")
    assert grant.expires_at is None
