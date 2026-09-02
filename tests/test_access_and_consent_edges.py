"""The refusal and corruption edges of the security core (MP-04).

`docs/MULTIYEAR-PLAN.md` MP-04. The `Makefile` gates `access/*`, `consent.py` and
`dualcontrol.py` at 95% with one pooled `coverage report --include=... --fail-under`.
That flag gates the report's TOTAL row, not each module in it, so the line passed at
95% while `grants.py` sat at 92% and `consent.py` at 91%: two modules were below the
floor their own gate advertised, held up by neighbours at 100%.

The fix has two halves, and this file is the first. Rather than lower the published
figure to match reality, it covers the paths that were missing so the 95% claim
becomes true of each module on its own. The second half replaces the pooled report
with a per-module gate (`tools/check_coverage_floors.py`) that cannot average one
module up on another's score.

What was uncovered was not incidental. In `grants.py` it was every refusal path of the
bearer-capability verifier: a token whose base64 is malformed, and one whose expiry is
not a parseable timestamp. A verifier that crashes instead of returning `None` on
attacker-supplied bytes is an availability bug at best. In `consent.py` it was the
`SubjectTokenStore`'s corruption handling and the empty-input guards -- the same
fail-closed family as `tests/test_silent_loss_stores.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ledger.access.grants import (
    issue_grant_token,
    load_revocations,
    revoke_subject,
    unrevoke_subject,
    verify_grant_token,
)
from ledger.consent import (
    ConsentRequest,
    ConsentRequestStore,
    SubjectTokenStore,
    _optional_str,
    _require_str,
    issue_subject_token,
    subject_token_hash,
)
from ledger.errors import LedgerError

_SECRET = b"a-grant-secret-for-this-test-only"
_NOW = "2026-08-27T00:00:00Z"


# --- grants.py: every refusal path of the token verifier ---------------------


def test_a_valid_grant_token_verifies(_unused: None = None) -> None:
    """The control: the verifier accepts what it minted, so refusals mean something."""
    token = issue_grant_token("steward-1", _SECRET)
    assert verify_grant_token(token, _SECRET, now=_NOW) == "steward-1"


@pytest.mark.parametrize(
    ("token", "why"),
    [
        ("", "an empty string"),
        ("only-one-part", "too few colon-separated parts"),
        ("a:b:c:d", "too many colon-separated parts"),
        ("!!!:!!!:mac", "a subject that is not valid base64"),
        ("__4=:__4=:mac", "base64 that decodes to bytes which are not UTF-8"),
    ],
)
def test_a_malformed_grant_token_is_refused_not_raised(token: str, why: str) -> None:
    """A structurally broken token returns None; it never propagates an exception.

    The token arrives from an untrusted `X-Ledger-Grant` header, so every decoding
    step is attacker-controlled. `binascii.Error`, `ValueError` and
    `UnicodeDecodeError` all have to leave as a refusal, or a malformed header is a
    500 on a public route.
    """
    assert verify_grant_token(token, _SECRET, now=_NOW) is None


def test_a_grant_token_with_an_unparseable_expiry_is_refused() -> None:
    """A token whose expiry is not an ISO timestamp is refused, not honoured.

    The expiry is inside the MAC, so reaching this branch means the holder of the
    secret minted it. Refusing anyway is the fail-closed reading: an expiry that
    cannot be compared cannot be shown to be in the future.
    """
    token = issue_grant_token("steward-1", _SECRET, expires_at="not-a-timestamp")
    assert verify_grant_token(token, _SECRET, now=_NOW) is None


def test_a_grant_token_signed_with_another_secret_is_refused() -> None:
    """The MAC is the whole capability: another secret's token must not verify."""
    token = issue_grant_token("steward-1", b"a-different-secret-entirely")
    assert verify_grant_token(token, _SECRET, now=_NOW) is None


def test_unrevoking_restores_a_subject_and_is_idempotent(tmp_path: Path) -> None:
    """Revocation is reversible, and un-revoking twice is not an error.

    Revocation is the only way to retract a stateless bearer token before it
    expires, so its inverse is what lets a steward undo one made in error.
    """
    path = tmp_path / "revocations.json"
    assert revoke_subject(path, "steward-1") == {"steward-1"}
    assert load_revocations(path) == {"steward-1"}

    assert unrevoke_subject(path, "steward-1") == set()
    assert load_revocations(path) == set()
    # Un-revoking a subject that was never revoked leaves the set unchanged.
    assert unrevoke_subject(path, "never-revoked") == set()


# --- consent.py: SubjectTokenStore refusals and corruption -------------------


def test_registering_no_hashes_is_a_no_op(tmp_path: Path) -> None:
    """An ingest with no named subjects writes no store at all."""
    path = tmp_path / "subject-tokens.json"
    SubjectTokenStore(path).register("rec-1", [])
    assert not path.exists()


def test_an_empty_subject_token_never_verifies(tmp_path: Path) -> None:
    """The empty string is not a token, and must not match a stored hash."""
    store = SubjectTokenStore(tmp_path / "subject-tokens.json")
    token = issue_subject_token("rec-1", 0, _SECRET)
    store.register("rec-1", [subject_token_hash(token)])
    assert store.verify("rec-1", token) is True  # the control
    assert store.verify("rec-1", "") is False


def test_a_subject_token_round_trips_and_merges_without_duplicating(tmp_path: Path) -> None:
    """Registering the same hash twice stores it once (append/merge, not append)."""
    store = SubjectTokenStore(tmp_path / "subject-tokens.json")
    first = subject_token_hash(issue_subject_token("rec-1", 0, _SECRET))
    second = subject_token_hash(issue_subject_token("rec-1", 1, _SECRET))
    store.register("rec-1", [first])
    store.register("rec-1", [first, second])
    assert store.hashes_for("rec-1") == [first, second]
    assert store.hashes_for("rec-unknown") == []


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ("{ not json", "is not valid JSON"),
        ('["a list, not an object"]', "must contain a JSON object"),
        ('{"rec-1": "a string, not a list"}', "must be a list"),
        ('{"rec-1": [1, 2, 3]}', "must be a list"),
    ],
)
def test_a_corrupt_subject_token_store_raises(tmp_path: Path, payload: str, expected: str) -> None:
    """A damaged subject-token store fails closed, like every sibling store.

    Reading it as empty would silently make every subject objection unverifiable:
    a named subject presenting a real token would be told it does not match.
    """
    path = tmp_path / "subject-tokens.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(LedgerError, match=expected):
        SubjectTokenStore(path).hashes_for("rec-1")


def test_an_unwritable_subject_token_store_raises(tmp_path: Path) -> None:
    """An OSError on write is reported, never swallowed into a silent no-op."""
    path = tmp_path / "subject-tokens.json"
    path.mkdir()  # a directory where the file belongs
    with pytest.raises(LedgerError, match="could not be written"):
        SubjectTokenStore(path).register("rec-1", ["deadbeef"])


# --- consent.py: request-store refusals --------------------------------------


def test_resolving_an_unknown_request_raises(tmp_path: Path) -> None:
    """A resolve against an id the store never held fails loudly."""
    store = ConsentRequestStore(tmp_path / "consent.json")
    store.add(ConsentRequest(record_id="rec-1", kind="withdraw", message="m", request_id="req-1"))
    with pytest.raises(LedgerError, match="no consent request"):
        store.resolve("req-missing", "resolved", now=_NOW)


def test_an_unwritable_consent_store_raises(tmp_path: Path) -> None:
    """The same OSError translation as every other store in this package."""
    path = tmp_path / "consent.json"
    path.mkdir()
    with pytest.raises(LedgerError, match="could not be written"):
        ConsentRequestStore(path).add(
            ConsentRequest(record_id="rec-1", kind="withdraw", message="m", request_id="req-1")
        )


def test_a_nonstring_consent_field_is_refused(tmp_path: Path) -> None:
    """A field the shape says is text must be text, or parsing refuses it.

    `_require_str` and `_optional_str` are the boundary between a JSON document
    someone could have edited by hand and the typed record the rest of the module
    trusts. They differ only in whether absence is allowed, never in whether a
    present-but-wrongly-typed value is.
    """
    assert _require_str({"note": "fine"}, "note") == "fine"
    with pytest.raises(LedgerError, match="missing required field"):
        _require_str({}, "note")
    with pytest.raises(LedgerError, match="must be a string"):
        _require_str({"note": 17}, "note")

    assert _optional_str({"note": "fine"}, "note") == "fine"
    assert _optional_str({}, "note") == ""  # absent is the empty string, not an error
    with pytest.raises(LedgerError, match="must be a string"):
        _optional_str({"note": 17}, "note")


def test_a_corrupt_consent_store_is_refused(tmp_path: Path) -> None:
    """A damaged consent queue raises rather than reading as no open requests."""
    path = tmp_path / "consent.json"
    path.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
    with pytest.raises(LedgerError):
        ConsentRequestStore(path).open_requests()
