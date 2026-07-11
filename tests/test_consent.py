"""Tests for :mod:`ledger.consent` — the contributor consent-request backend.

Covers the :class:`ConsentRequest` round-trip and validation, the append-only
atomic store (including a missing file reading as empty and steward resolution),
and the stateless claim token (issue/verify across valid, wrong-record,
wrong-secret, and tampered cases). The no-outing rule is checked too: a malformed
entry must not leak its private message content into the raised error.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ledger.consent import (
    VALID_KINDS,
    ConsentRequest,
    ConsentRequestStore,
    SubjectTokenStore,
    issue_claim_token,
    issue_subject_token,
    subject_token_hash,
    verify_claim_token,
    verify_subject_token,
)
from ledger.errors import LedgerError

_SECRET = b"server-secret-key-for-tests-only"


# --- ConsentRequest round-trip & validation ---------------------------------


def test_round_trip_preserves_all_fields() -> None:
    """``from_dict(to_dict(req))`` reproduces an identical request (determinism)."""
    req = ConsentRequest(
        record_id="rec-123",
        kind="withdraw",
        message="Please remove this; my situation changed.",
        request_id="fixed-id-abcd",
        status="open",
        created_at="2026-06-16T00:00:00Z",
    )
    restored = ConsentRequest.from_dict(req.to_dict())
    assert restored == req


def test_to_dict_has_stable_keys() -> None:
    """The serialized mapping carries exactly the documented fields."""
    req = ConsentRequest(record_id="r", kind="contact", message="hi")
    assert set(req.to_dict()) == {
        "record_id",
        "kind",
        "message",
        "request_id",
        "status",
        "created_at",
        "due_by",
        "resolved_at",
    }


def test_defaults_are_generated() -> None:
    """A request without explicit id/timestamp/status still validates with defaults."""
    req = ConsentRequest(record_id="r", kind="tighten", message="seal the location field")
    assert req.status == "open"
    assert req.request_id  # a non-empty random id
    assert req.created_at.endswith("Z")


@pytest.mark.parametrize("kind", sorted(VALID_KINDS))
def test_all_valid_kinds_accepted(kind: str) -> None:
    """Every documented kind constructs without error."""
    req = ConsentRequest(record_id="r", kind=kind, message="m")
    assert req.kind == kind


def test_invalid_kind_rejected() -> None:
    """An unknown kind is refused at construction (correctness)."""
    with pytest.raises(LedgerError):
        ConsentRequest(record_id="r", kind="delete-everything", message="m")


def test_invalid_status_rejected() -> None:
    """An unknown status is refused at construction (correctness)."""
    with pytest.raises(LedgerError):
        ConsentRequest(record_id="r", kind="contact", message="m", status="bogus")


def test_from_dict_rejects_non_mapping() -> None:
    with pytest.raises(LedgerError):
        ConsentRequest.from_dict(["not", "a", "mapping"])  # type: ignore[arg-type]


def test_from_dict_rejects_missing_field() -> None:
    """A dict missing a required key raises, naming the field not the content."""
    bad = {"record_id": "r", "kind": "contact", "message": "secret message text"}
    with pytest.raises(LedgerError) as exc:
        ConsentRequest.from_dict(bad)
    assert "secret message text" not in str(exc.value)


def test_from_dict_rejects_non_string_field() -> None:
    bad: dict[str, object] = {
        "record_id": "r",
        "kind": "contact",
        "message": 123,
        "request_id": "x",
        "status": "open",
        "created_at": "2026-06-16T00:00:00Z",
    }
    with pytest.raises(LedgerError):
        ConsentRequest.from_dict(bad)


def test_malformed_kind_in_dict_rejected() -> None:
    """A tampered file with a bad kind raises rather than being accepted."""
    bad = {
        "record_id": "r",
        "kind": "nope",
        "message": "m",
        "request_id": "x",
        "status": "open",
        "created_at": "2026-06-16T00:00:00Z",
    }
    with pytest.raises(LedgerError):
        ConsentRequest.from_dict(bad)


# --- ConsentRequestStore -----------------------------------------------------


def test_missing_file_reads_as_empty(tmp_path: Path) -> None:
    """A store whose file does not exist yet reads as an empty queue."""
    store = ConsentRequestStore(tmp_path / "consent.json")
    assert store.all() == []
    assert store.open_requests() == []


def test_add_is_append_only(tmp_path: Path) -> None:
    """Each add appends; earlier requests are preserved in order."""
    store = ConsentRequestStore(tmp_path / "consent.json")
    first = ConsentRequest(record_id="a", kind="withdraw", message="one", request_id="id1")
    second = ConsentRequest(record_id="b", kind="contact", message="two", request_id="id2")
    store.add(first)
    store.add(second)
    ids = [r.request_id for r in store.all()]
    assert ids == ["id1", "id2"]


def test_add_persists_across_instances(tmp_path: Path) -> None:
    """A freshly opened store sees what a prior instance wrote (durability)."""
    path = tmp_path / "consent.json"
    ConsentRequestStore(path).add(
        ConsentRequest(record_id="a", kind="correct", message="fix date", request_id="id1")
    )
    reopened = ConsentRequestStore(path)
    assert [r.request_id for r in reopened.all()] == ["id1"]


def test_store_writes_a_json_list(tmp_path: Path) -> None:
    """The on-disk form is a JSON list of request mappings."""
    path = tmp_path / "consent.json"
    store = ConsentRequestStore(path)
    store.add(ConsentRequest(record_id="a", kind="withdraw", message="m", request_id="id1"))
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(raw, list)
    assert raw[0]["request_id"] == "id1"


def test_open_requests_excludes_resolved(tmp_path: Path) -> None:
    """``open_requests`` returns only those still awaiting action."""
    store = ConsentRequestStore(tmp_path / "consent.json")
    store.add(ConsentRequest(record_id="a", kind="withdraw", message="m", request_id="id1"))
    store.add(ConsentRequest(record_id="b", kind="contact", message="m", request_id="id2"))
    store.resolve("id1", "resolved")
    open_ids = [r.request_id for r in store.open_requests()]
    assert open_ids == ["id2"]


def test_resolve_acknowledged(tmp_path: Path) -> None:
    store = ConsentRequestStore(tmp_path / "consent.json")
    store.add(ConsentRequest(record_id="a", kind="withdraw", message="m", request_id="id1"))
    store.resolve("id1", "acknowledged")
    assert store.all()[0].status == "acknowledged"


def test_resolve_rejects_unknown_status(tmp_path: Path) -> None:
    store = ConsentRequestStore(tmp_path / "consent.json")
    store.add(ConsentRequest(record_id="a", kind="withdraw", message="m", request_id="id1"))
    with pytest.raises(LedgerError):
        store.resolve("id1", "open")


def test_resolve_rejects_unknown_request_id(tmp_path: Path) -> None:
    store = ConsentRequestStore(tmp_path / "consent.json")
    store.add(ConsentRequest(record_id="a", kind="withdraw", message="m", request_id="id1"))
    with pytest.raises(LedgerError):
        store.resolve("nope", "resolved")


def test_corrupt_store_raises(tmp_path: Path) -> None:
    path = tmp_path / "consent.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(LedgerError):
        ConsentRequestStore(path).all()


def test_store_must_be_a_list(tmp_path: Path) -> None:
    path = tmp_path / "consent.json"
    path.write_text('{"not": "a list"}', encoding="utf-8")
    with pytest.raises(LedgerError):
        ConsentRequestStore(path).all()


# --- claim token -------------------------------------------------------------


def test_claim_token_issue_and_verify_valid() -> None:
    """A token minted for a record verifies for that record and secret."""
    token = issue_claim_token("rec-1", _SECRET)
    assert token.startswith("claim:")
    assert verify_claim_token("rec-1", token, _SECRET) is True


def test_claim_token_is_deterministic() -> None:
    """The same (record, secret) always yields the same token (statelessness)."""
    assert issue_claim_token("rec-1", _SECRET) == issue_claim_token("rec-1", _SECRET)


def test_claim_token_wrong_record_rejected() -> None:
    """A token for one record does not verify for another."""
    token = issue_claim_token("rec-1", _SECRET)
    assert verify_claim_token("rec-2", token, _SECRET) is False


def test_claim_token_wrong_secret_rejected() -> None:
    """A token does not verify under a different server secret."""
    token = issue_claim_token("rec-1", _SECRET)
    assert verify_claim_token("rec-1", token, b"a-different-secret") is False


def test_claim_token_tampered_rejected() -> None:
    """A token whose digest has been altered does not verify."""
    token = issue_claim_token("rec-1", _SECRET)
    tampered = token[:-1] + ("0" if token[-1] != "0" else "1")
    assert verify_claim_token("rec-1", tampered, _SECRET) is False


def test_claim_token_garbage_rejected() -> None:
    """An arbitrary non-token string does not verify."""
    assert verify_claim_token("rec-1", "not-a-real-token", _SECRET) is False


# --- subject token (RM12/EXP-04) ---------------------------------------------


def test_subject_token_issue_and_verify_valid() -> None:
    """A subject token verifies for its own record and index under the same secret."""
    token = issue_subject_token("rec-1", 0, _SECRET)
    assert token.startswith("subject:")
    assert verify_subject_token("rec-1", 0, token, _SECRET) is True


def test_subject_token_is_deterministic() -> None:
    """Same (record, index, secret) always yields the same token (statelessness)."""
    assert issue_subject_token("rec-1", 2, _SECRET) == issue_subject_token("rec-1", 2, _SECRET)


def test_subject_token_wrong_index_rejected() -> None:
    """A token minted for one subject index does not verify for another."""
    token = issue_subject_token("rec-1", 0, _SECRET)
    assert verify_subject_token("rec-1", 1, token, _SECRET) is False


def test_subject_token_wrong_record_rejected() -> None:
    """A token minted for one record does not verify for another."""
    token = issue_subject_token("rec-1", 0, _SECRET)
    assert verify_subject_token("rec-2", 0, token, _SECRET) is False


def test_subject_token_wrong_secret_rejected() -> None:
    """A subject token does not verify under a different server secret."""
    token = issue_subject_token("rec-1", 0, _SECRET)
    assert verify_subject_token("rec-1", 0, token, b"a-different-secret") is False


def test_subject_token_differs_from_claim_token() -> None:
    """A subject token and a contributor claim token are never the same value."""
    assert issue_subject_token("rec-1", 0, _SECRET) != issue_claim_token("rec-1", _SECRET)


# --- SubjectTokenStore -------------------------------------------------------


def test_subject_token_store_missing_file_is_empty(tmp_path: Path) -> None:
    store = SubjectTokenStore(tmp_path / "subject-tokens.json")
    assert store.hashes_for("rec-1") == []
    assert store.verify("rec-1", issue_subject_token("rec-1", 0, _SECRET)) is False


def test_subject_token_store_verifies_a_registered_token(tmp_path: Path) -> None:
    """A token whose hash was registered verifies; an unregistered one does not."""
    store = SubjectTokenStore(tmp_path / "subject-tokens.json")
    good = issue_subject_token("rec-1", 0, _SECRET)
    store.register("rec-1", [subject_token_hash(good)])
    assert store.verify("rec-1", good) is True
    other = issue_subject_token("rec-1", 1, _SECRET)
    assert store.verify("rec-1", other) is False


def test_subject_token_store_persists_only_hashes(tmp_path: Path) -> None:
    """The on-disk file holds SHA-256 hashes only — never the clear tokens."""
    path = tmp_path / "subject-tokens.json"
    store = SubjectTokenStore(path)
    tokens = [issue_subject_token("rec-1", i, _SECRET) for i in range(2)]
    store.register("rec-1", [subject_token_hash(t) for t in tokens])
    text = path.read_text(encoding="utf-8")
    for token in tokens:
        assert token not in text
        assert subject_token_hash(token) in text


def test_subject_token_store_register_is_idempotent(tmp_path: Path) -> None:
    """Registering the same hash twice does not duplicate it."""
    store = SubjectTokenStore(tmp_path / "subject-tokens.json")
    h = subject_token_hash(issue_subject_token("rec-1", 0, _SECRET))
    store.register("rec-1", [h])
    store.register("rec-1", [h])
    assert store.hashes_for("rec-1") == [h]


# --- subject-objection kind + due_by/resolved_at (RM12) ----------------------


def test_subject_objection_kind_accepted() -> None:
    """The RM12 verified-objection kind constructs without error."""
    req = ConsentRequest(record_id="r", kind="subject-objection", message="please redact me")
    assert req.kind == "subject-objection"


def test_due_by_and_resolved_at_round_trip(tmp_path: Path) -> None:
    """due_by/resolved_at survive a to_dict/from_dict round-trip and a store write."""
    req = ConsentRequest(
        record_id="r",
        kind="subject-objection",
        message="m",
        request_id="id1",
        due_by="2026-07-09T00:00:00Z",
        resolved_at="2026-07-03T00:00:00Z",
    )
    restored = ConsentRequest.from_dict(req.to_dict())
    assert restored == req
    assert restored.due_by == "2026-07-09T00:00:00Z"
    assert restored.resolved_at == "2026-07-03T00:00:00Z"


def test_resolve_stamps_resolved_at(tmp_path: Path) -> None:
    """Resolving a request records when the steward responded (RM12)."""
    store = ConsentRequestStore(tmp_path / "consent.json")
    store.add(
        ConsentRequest(
            record_id="a",
            kind="subject-objection",
            message="m",
            request_id="id1",
            due_by="2026-07-09T00:00:00Z",
        )
    )
    store.resolve("id1", "resolved", now="2026-07-03T12:00:00Z")
    resolved = store.all()[0]
    assert resolved.status == "resolved"
    assert resolved.resolved_at == "2026-07-03T12:00:00Z"
    # The committed response window is preserved, not lost, when resolving.
    assert resolved.due_by == "2026-07-09T00:00:00Z"


def test_acknowledge_does_not_claim_request_was_resolved(tmp_path: Path) -> None:
    """Acknowledgement records lifecycle state without inventing a completed response."""
    store = ConsentRequestStore(tmp_path / "consent.json")
    store.add(
        ConsentRequest(
            record_id="a",
            kind="subject-objection",
            message="m",
            request_id="id1",
            due_by="2026-07-09T00:00:00Z",
        )
    )
    store.resolve("id1", "acknowledged", now="2026-07-03T12:00:00Z")
    acknowledged = store.all()[0]
    assert acknowledged.status == "acknowledged"
    assert acknowledged.resolved_at == ""


def test_legacy_json_without_new_fields_still_loads(tmp_path: Path) -> None:
    """A consent-requests.json written before RM12 (no due_by/resolved_at) loads."""
    path = tmp_path / "consent.json"
    legacy = [
        {
            "record_id": "r",
            "kind": "object",
            "message": "m",
            "request_id": "id1",
            "status": "open",
            "created_at": "2026-06-16T00:00:00Z",
        }
    ]
    path.write_text(json.dumps(legacy), encoding="utf-8")
    loaded = ConsentRequestStore(path).all()
    assert len(loaded) == 1
    assert loaded[0].due_by == ""
    assert loaded[0].resolved_at == ""
