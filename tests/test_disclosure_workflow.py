"""Disclosure-policy workflow tests — :func:`set_field_policy` / :func:`set_payload_policy`.

These pin the engine-side primitives behind the ``ledger seal`` workflow: applying a
disclosure policy (a visibility level, a temporal embargo, a conditional or absolute
seal) to one already-archived field or payload. Each is a *recorded transform* in the
mould of :func:`ledger.moderate.change_consent` — it returns a lossy copy, a PREMIS
event, and an accountable action, leaves the original untouched, requires a rationale,
and crucially never names the withheld value (auditability, autonomy, the no-outing
rule).
"""

from __future__ import annotations

import pytest

from ledger.errors import ModerationError
from ledger.models import (
    AccessPolicy,
    ContentAddress,
    Field,
    PayloadFile,
    PremisEventType,
    Record,
)
from ledger.moderate import set_field_policy, set_payload_policy

pytestmark = pytest.mark.disclosure

_NOW = "2026-06-16T12:00:00Z"
# A loud sentinel value that must NEVER appear in any event/action the workflow emits.
_SENSITIVE_VALUE = "Deadname McSurname, 555-0100"


def _record() -> Record:
    return Record(
        title="Letter, undated",
        record_id="rec-letter",
        fields=[
            Field(name="story", value="the public account", policy=AccessPolicy.PUBLIC),
            Field(name="signature", value=_SENSITIVE_VALUE, policy=AccessPolicy.PUBLIC),
        ],
        payloads=[
            PayloadFile(
                filename="master.wav",
                address=ContentAddress.parse("sha256:" + "a" * 64),
                policy=AccessPolicy.PUBLIC,
            )
        ],
    )


# --- set_field_policy -------------------------------------------------------


def test_set_field_policy_returns_triple() -> None:
    """The workflow returns a (Record, PremisEvent, ModerationAction) triple."""
    updated, event, action = set_field_policy(
        _record(), "signature", AccessPolicy.STEWARDS, actor="steward", reason="sensitive", now=_NOW
    )
    assert isinstance(updated, Record)
    assert event.event_type is PremisEventType.POLICY_CHANGE
    assert action.action == "consent-change"


def test_set_field_policy_changes_only_named_field() -> None:
    """Only the targeted field's policy moves; the others keep their policy."""
    updated, _e, _a = set_field_policy(
        _record(), "signature", AccessPolicy.STEWARDS, actor="s", reason="r", now=_NOW
    )
    by_name = {f.name: f for f in updated.fields}
    assert by_name["signature"].policy is AccessPolicy.STEWARDS
    assert by_name["story"].policy is AccessPolicy.PUBLIC


def test_set_field_policy_applies_temporal_embargo() -> None:
    """A sealed-until level with --until is stored as a dated embargo on the field."""
    updated, _e, _a = set_field_policy(
        _record(),
        "signature",
        AccessPolicy.SEALED_UNTIL,
        unseal_at="2035-01-01",
        actor="s",
        reason="r",
        now=_NOW,
    )
    field = next(f for f in updated.fields if f.name == "signature")
    assert field.policy is AccessPolicy.SEALED_UNTIL
    assert field.unseal_at == "2035-01-01"


def test_set_field_policy_clears_date_when_moving_to_dateless_level() -> None:
    """Re-sealing to a dateless level clears any prior unseal date/condition."""
    embargoed, _e, _a = set_field_policy(
        _record(),
        "signature",
        AccessPolicy.SEALED_UNTIL,
        unseal_at="2035-01-01",
        actor="s",
        reason="r",
        now=_NOW,
    )
    reopened, _e2, _a2 = set_field_policy(
        embargoed, "signature", AccessPolicy.PUBLIC, actor="s", reason="r", now=_NOW
    )
    field = next(f for f in reopened.fields if f.name == "signature")
    assert field.unseal_at is None
    assert field.unseal_condition is None


def test_set_field_policy_leaves_original_unchanged() -> None:
    """The transform is non-mutating: the input record keeps its original policy."""
    original = _record()
    set_field_policy(original, "signature", AccessPolicy.STEWARDS, actor="s", reason="r", now=_NOW)
    assert next(f for f in original.fields if f.name == "signature").policy is AccessPolicy.PUBLIC


def test_set_field_policy_event_names_field_never_value() -> None:
    """The audit event names the field and policy, never the withheld value."""
    _u, event, _a = set_field_policy(
        _record(),
        "signature",
        AccessPolicy.SEALED_UNTIL,
        unseal_at="2035-01-01",
        actor="s",
        reason="r",
        now=_NOW,
    )
    assert "signature" in event.detail
    assert "sealed-until" in event.detail
    assert _SENSITIVE_VALUE not in event.detail
    assert event.linked_object == "rec-letter"


def test_set_field_policy_requires_reason() -> None:
    """A policy change without a rationale is rejected (accountability)."""
    with pytest.raises(ModerationError):
        set_field_policy(
            _record(), "signature", AccessPolicy.STEWARDS, actor="s", reason="", now=_NOW
        )


def test_set_field_policy_unknown_field_names_field_only() -> None:
    """An unknown field raises naming only the field, never a value (no-outing)."""
    with pytest.raises(ModerationError) as exc:
        set_field_policy(_record(), "nope", AccessPolicy.STEWARDS, actor="s", reason="r", now=_NOW)
    assert "nope" in str(exc.value)
    assert _SENSITIVE_VALUE not in str(exc.value)


def test_set_field_policy_is_deterministic() -> None:
    """Identical inputs yield byte-identical events (no clock, no randomness)."""
    _u1, e1, _a1 = set_field_policy(
        _record(), "signature", AccessPolicy.STEWARDS, actor="s", reason="r", now=_NOW
    )
    _u2, e2, _a2 = set_field_policy(
        _record(), "signature", AccessPolicy.STEWARDS, actor="s", reason="r", now=_NOW
    )
    assert e1.to_dict() == e2.to_dict()


# --- set_payload_policy -----------------------------------------------------


def test_set_payload_policy_changes_named_payload() -> None:
    """The targeted payload's policy moves; the event is a POLICY_CHANGE."""
    updated, event, action = set_payload_policy(
        _record(), "master.wav", AccessPolicy.STEWARDS, actor="s", reason="r", now=_NOW
    )
    assert next(p for p in updated.payloads if p.filename == "master.wav").policy is (
        AccessPolicy.STEWARDS
    )
    assert event.event_type is PremisEventType.POLICY_CHANGE
    assert action.action == "consent-change"


def test_set_payload_policy_leaves_original_unchanged() -> None:
    """Non-mutating: the input record's payload keeps its original policy."""
    original = _record()
    set_payload_policy(
        original, "master.wav", AccessPolicy.STEWARDS, actor="s", reason="r", now=_NOW
    )
    assert original.payloads[0].policy is AccessPolicy.PUBLIC


def test_set_payload_policy_unknown_payload_names_filename_only() -> None:
    """An unknown payload raises naming only the filename."""
    with pytest.raises(ModerationError) as exc:
        set_payload_policy(
            _record(), "ghost.wav", AccessPolicy.STEWARDS, actor="s", reason="r", now=_NOW
        )
    assert "ghost.wav" in str(exc.value)


def test_set_payload_policy_requires_reason() -> None:
    """A payload policy change without a rationale is rejected."""
    with pytest.raises(ModerationError):
        set_payload_policy(
            _record(), "master.wav", AccessPolicy.STEWARDS, actor="s", reason=" ", now=_NOW
        )
