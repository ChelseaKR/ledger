"""Applying the committed ruleset must not lock the repository owner out.

`.github/rulesets/main.json` is a mirror of the live `protect-main` ruleset, and its
README's "Keeping the mirror honest" section is right that a mirror which silently stops
matching is worse than no mirror. It named two guards. Neither read `bypass_actors`.

That field is the one where a stale mirror is not merely inaccurate but dangerous. A
mirror is a file people re-apply: `gh api -X POST .../rulesets --input
.github/rulesets/main.json` posts it exactly as it stands. Until this test landed the
file carried `"bypass_actors": []`, which GitHub accepts with a 201 like any other apply
and which leaves the repository with no break-glass path at all: the owner cannot merge
past a wedged check, cannot force-push, and cannot delete the ruleset that is blocking
them. It is not hypothetical. An agent applied a no-bypass ruleset elsewhere in this
portfolio and restoring access took a sweep across eighteen repositories.

The empty list was also, on 2026-08-21, an accurate mirror of the live ruleset. That is
the trap: mirroring faithfully is not the same as being safe to re-apply, and this file
is both a record and an instrument. The live ruleset gained the owner's standing bypass
on 2026-08-26 and the mirror did not follow it.

Correcting the field once is not the fix, because a field can regress and the next
person to re-export the live ruleset by hand can drop it again. This module is the fix.

Every check here fails closed. `lockout_risk` is a pure function of a parsed document, so
it is run against documents it must reject as well as against the committed one, and
`load_ruleset` refuses a missing or unparseable file rather than returning an empty
document that the assertions would then read as "nothing wrong". A guard that passes when
its subject is absent is the defect it exists to catch. The parse is load-bearing for a
second reason: a truncated JSON file still contains the literal string `bypass_actors`,
so a grep would pass it.

What this module still does not check: whether the committed mirror matches the live
ruleset today. That needs a network call, which the repository's gates deliberately do
not make, and `tools/check_claims.py` already records it as uncovered.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
RULESET = ROOT / ".github" / "rulesets" / "main.json"
RULESET_DOC = ROOT / ".github" / "rulesets" / "README.md"

OWNER_BYPASS = {
    "actor_id": 5,
    "actor_type": "RepositoryRole",
    "bypass_mode": "always",
}
"""The repository owner's standing bypass, and the only entry this file may carry.

`RepositoryRole` 5 is admin. `bypass_mode: "always"` rather than `"pull_request"` because
a bypass that only works inside a pull request is no use when the thing that is wedged is
the pull request itself, which is precisely the case this repository's profile makes
likely: it requires thirteen contexts, a strict up-to-date policy, signatures and linear
history, all reachable only through a pull request.
"""


def load_ruleset() -> dict[str, Any]:
    """The committed ruleset, or a failure. Never a silently empty document."""
    if not RULESET.is_file():
        pytest.fail(f"{RULESET} is missing; the committed ruleset is what this checks")
    # Bound before the try, so a parse failure cannot leave the name unset. `pytest.fail`
    # raises, so the `isinstance` below is not reached after a JSONDecodeError; binding
    # `None` first means that even if it somehow were, the next check refuses rather than
    # reading an unset name. CodeQL's py/uninitialized-local-variable found the earlier
    # shape, and "unreachable in practice" is the reasoning this module exists to distrust.
    loaded: Any = None
    try:
        loaded = json.loads(RULESET.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        pytest.fail(f"{RULESET} is not parseable JSON, so nothing can vouch for it: {exc}")
    if not isinstance(loaded, dict):
        pytest.fail(f"{RULESET} is not a JSON object, so nothing can vouch for it: {loaded!r}")
    return loaded


def lockout_risk(ruleset: dict[str, Any]) -> str | None:
    """Why applying this document would lock the owner out, or ``None`` if it would not.

    A pure function of a parsed document, so it can be run against the documents it must
    reject rather than only against the one in the tree.
    """
    if "bypass_actors" not in ruleset:
        return "no bypass_actors key at all, which GitHub reads as an empty list"
    actors = ruleset["bypass_actors"]
    if not isinstance(actors, list):
        return f"bypass_actors is {type(actors).__name__}, not a list"
    if not actors:
        return (
            "bypass_actors is empty, so applying this leaves no break-glass path and the "
            "owner cannot merge, push or delete the ruleset that is blocking them"
        )
    if OWNER_BYPASS not in actors:
        return (
            f"bypass_actors does not carry the owner's standing bypass {OWNER_BYPASS}; "
            f"it carries {actors}"
        )
    return None


def test_applying_the_committed_ruleset_would_not_lock_the_owner_out() -> None:
    """The assertion the empty list has to fail."""
    risk = lockout_risk(load_ruleset())
    assert risk is None, (
        "applying .github/rulesets/main.json as committed would lock the repository owner "
        f"out: {risk}. See .github/rulesets/README.md, 'The owner's standing bypass'."
    )


def test_the_owner_is_the_only_bypass_actor() -> None:
    """One actor. A team, an app or a second role would be a real widening; this is not."""
    actors = load_ruleset()["bypass_actors"]
    assert actors == [OWNER_BYPASS], (
        "the owner's standing bypass is the only entry this mirror may carry, and a second "
        f"one is a widening of who can skip all thirteen required checks: {actors}"
    )


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        ({"bypass_actors": []}, "empty"),
        ({}, "no bypass_actors key"),
        ({"bypass_actors": {}}, "not a list"),
        (
            {
                "bypass_actors": [
                    {"actor_id": 1, "actor_type": "Integration", "bypass_mode": "always"}
                ]
            },
            "does not carry the owner",
        ),
        (
            {"bypass_actors": [dict(OWNER_BYPASS, bypass_mode="pull_request")]},
            "does not carry the owner",
        ),
    ],
    ids=["empty", "absent", "wrong-type", "wrong-actor", "wrong-mode"],
)
def test_the_lockout_check_rejects_the_documents_it_must_reject(
    document: dict[str, Any], expected: str
) -> None:
    """Five ways to lose the bypass, each of which GitHub answers with a 201.

    The empty list is the one that was committed. `pull_request` mode is the one an
    internal standard actually asks for, and it is the subtle one: it looks like a bypass
    and is not one when the pull request is what has wedged.
    """
    risk = lockout_risk(document)
    assert risk is not None, f"{document} should be refused"
    assert expected in risk


def test_the_lockout_check_accepts_the_shape_it_should() -> None:
    """A positive control, so the check above is not passing by refusing everything."""
    assert lockout_risk({"bypass_actors": [OWNER_BYPASS]}) is None


def test_the_mirror_readme_names_the_bypass_the_file_carries() -> None:
    """The README is what a person reads before re-applying. It must name the same actor."""
    doc = RULESET_DOC.read_text(encoding="utf-8")
    for fragment in ('"actor_id": 5', "RepositoryRole", "always"):
        assert fragment in doc, f"{RULESET_DOC} does not name {fragment!r}"
