"""Physical holdings: a catalogue entry for something the archive does not hold (#188).

The README's shoebox under someone's bed is full of zines, flyers, buttons and
cassettes, and ledger can only *preserve* those once they are digitized. What a
community needs first is a catalogue: what exists, where it is, who has it. This
module pins the whole of that feature, and it is one module rather than five
because #188 is a schema change across four surfaces that have to agree —
**ingest, access policy, fixity, and the no-outing path** — and a partial
agreement is worse than none.

Three properties carry most of the weight here.

1. **A physical holding is never reported as passing fixity.** Its bag is full of
   verifiable bytes — ``record.json``, ``premis.json``, the manifests — so before
   #188 an undigitized shoebox printed ``PASS`` in the same column as a re-hashed
   video. :func:`ledger.fixity.holding_status` is a function of the report **and**
   the declared kind, and ``NOT_APPLICABLE`` is a fourth state that only it and
   :func:`~ledger.fixity.overall_holding_status` can return.
2. **A failure always dominates the kind.** ``not_applicable`` must never become a
   place to hide damage: a physical record whose manifest was altered is a
   failure, and a digital record relabelled ``physical`` by somebody with disk
   access still fails on the payload it continues to declare.
3. **Custody is on the no-outing path.** Where the object is and who keeps it are
   ordinary sealed ``custody.*`` fields, so they go through the one disclosure
   decision point rather than a second one beside it. What every viewer sees is a
   three-state *word* — recorded-and-shown, recorded-and-withheld, not-recorded —
   which says whether a fact exists without ever saying what it is.

The migration is deliberately invisible: a manifest with no ``holding_kind`` reads
as ``digital``, and a digital record serializes byte-for-byte as it did before this
feature existed, so no stored digest moves and no bag needs a reseal.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from ledger import i18n, render
from ledger.access.grants import anonymous, issue_grant_token, steward
from ledger.access.policy import disclose
from ledger.bag import validate_bag
from ledger.config import Config
from ledger.errors import BagValidationError, LedgerError
from ledger.export_drive import build_export_drive
from ledger.fixity import (
    AuditReport,
    FixityStatus,
    holding_status,
    overall_holding_status,
    overall_status,
)
from ledger.ingest import (
    HOLDINGS_LOG_FILENAME,
    Archive,
    deserialize_record,
    serialize_record,
    validate_holding,
)
from ledger.metadata.premis import PremisLog
from ledger.models import (
    CUSTODY_CUSTODIAN_FIELD,
    CUSTODY_LOCATION_FIELD,
    AccessPolicy,
    ContentAddress,
    CustodyState,
    DublinCore,
    Field,
    FixityResult,
    HashAlgo,
    HoldingKind,
    PayloadFile,
    PhysicalFormat,
    PhysicalHolding,
    PremisEventType,
    Record,
    custody_fields,
)
from ledger.print_edition import build_print_edition
from ledger.succession import build_handoff

pytestmark = pytest.mark.preservation

_VAULT_KEY = "0123456789abcdef0123456789abcdef0123456789a="
_GRANT_SECRET = b"physical-holdings-test-grant-secret"
_NOW = "2026-09-10T00:00:00Z"

# The custody values every no-outing assertion in this module hunts for. Distinctive
# on purpose: a substring search for "Rosa" would match prose, and a sentinel that
# can appear by accident is a sentinel that cannot fail.
_SENTINEL_CUSTODIAN = "CUSTODIAN-SENTINEL-8f3a91"
_SENTINEL_LOCATION = "LOCATION-SENTINEL-4c7b02"


# --- builders ---------------------------------------------------------------


def _physical_record(
    title: str = "Four boxes from the 1994 clinic defence",
    *,
    record_id: str | None = None,
    custody: bool = True,
    fmt: PhysicalFormat = PhysicalFormat.FLYER,
) -> Record:
    fields = [Field("summary", "Leaflets and a run of the newsletter.", AccessPolicy.PUBLIC)]
    if custody:
        fields.extend(custody_fields(location=_SENTINEL_LOCATION, custodian=_SENTINEL_CUSTODIAN))
    kwargs: dict[str, Any] = {}
    if record_id is not None:
        kwargs["record_id"] = record_id
    return Record(
        title=title,
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(title=[title], type=["ephemera"], language=["en"]),
        fields=fields,
        holding_kind=HoldingKind.PHYSICAL,
        physical=PhysicalHolding(
            format=fmt, extent="1 box, ~380 flyers", condition="Water damage along one edge."
        ),
        **kwargs,
    )


def _digital_record(title: str = "A scanned newsletter") -> Record:
    return Record(
        title=title,
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(title=[title], type=["newsletter"], language=["en"]),
        fields=[Field("summary", "One issue, scanned.", AccessPolicy.PUBLIC)],
    )


def _archive(root: Path, monkeypatch: pytest.MonkeyPatch, name: str = "Shoebox Archive") -> Archive:
    monkeypatch.setenv("LEDGER_VAULT_KEY", _VAULT_KEY)
    monkeypatch.setenv("LEDGER_GRANT_SECRET", _GRANT_SECRET.decode())
    return Archive.init(Config.default(name, root))


def _ingest_digital(archive: Archive, tmp_path: Path, title: str, body: str = "scan\n") -> Record:
    source = tmp_path / f"{title.replace(' ', '-')}.txt"
    source.write_text(body, encoding="utf-8")
    record = _digital_record(title)
    archive.ingest({source.name: source}, record, now=_NOW)
    return record


def _report(*, ok: bool = True, count: int = 1) -> AuditReport:
    """An :class:`AuditReport` with ``count`` results, all matching or all not."""
    return AuditReport(
        results=[
            FixityResult(
                path=f"f{i}", algo=HashAlgo.SHA256, expected="a", actual="a" if ok else "b"
            )
            for i in range(count)
        ]
    )


# --- the migration: absence means digital, and nothing else moves -----------


def test_a_manifest_written_before_this_feature_reads_as_digital() -> None:
    """The whole migration, and it is one branch: no key means ``digital``.

    Not "the payload list is empty, so it must be physical" — an empty payload list
    is equally what a failed ingest looks like, and inferring the kind from it is
    the defect the declared field exists to prevent.
    """
    pre_188 = json.dumps(
        {
            "record_id": "old",
            "title": "Written in 2026-08",
            "default_policy": "public",
            "created_at": _NOW,
            "identity_ref": None,
            "dublin_core": {},
            "content_warnings": [],
            "fields": [],
            "payloads": [],
        }
    )
    record = deserialize_record(pre_188)
    assert record.holding_kind is HoldingKind.DIGITAL
    assert record.physical is None


def test_a_digital_record_serializes_exactly_as_it_did_before_physical_holdings() -> None:
    """No new key at the default, so no stored manifest's digest moves.

    This is the reason the migration needs no bag reseal and no data rewrite. It is
    the rule ``PremisEvent.to_dict`` already follows for its optional links, applied
    one level up.
    """
    serialized = json.loads(serialize_record(_digital_record()))
    assert "holding_kind" not in serialized
    assert "physical" not in serialized


def test_a_physical_record_round_trips_through_the_manifest() -> None:
    original = _physical_record()
    restored = deserialize_record(serialize_record(original))
    assert restored.holding_kind is HoldingKind.PHYSICAL
    assert restored.physical == original.physical
    assert restored.field_named(CUSTODY_CUSTODIAN_FIELD) is not None


def test_an_unreadable_holding_kind_is_refused_rather_than_defaulted() -> None:
    """ADR 0018's rule one layer down: never state what could not be read.

    Defaulting an unknown kind to ``digital`` would make every downstream surface
    report content fixity over a record whose kind this build could not parse.
    """
    manifest = json.loads(serialize_record(_digital_record()))
    manifest["holding_kind"] = "hologram"
    with pytest.raises(LedgerError, match="unreadable holding_kind"):
        deserialize_record(json.dumps(manifest))


def test_an_unknown_physical_format_degrades_to_other_rather_than_raising() -> None:
    """A newer ledger's vocabulary must still open here — as ``other``, not as a guess."""
    holding = PhysicalHolding.from_dict({"format": "wax-cylinder", "extent": "3"})
    assert holding.format is PhysicalFormat.OTHER
    assert holding.extent == "3"


# --- the declaration has to agree with what the record carries --------------


def test_a_digital_record_with_no_payloads_is_still_legal() -> None:
    """#188 does not get to redefine a description-only digital record."""
    validate_holding(_digital_record())


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        pytest.param(
            lambda r: setattr(r, "physical", PhysicalHolding(format=PhysicalFormat.ZINE)),
            "carries a physical description",
            id="digital-with-a-physical-description",
        ),
        pytest.param(
            lambda r: r.fields.append(Field(CUSTODY_CUSTODIAN_FIELD, "x", AccessPolicy.STEWARDS)),
            "custody field",
            id="digital-with-custody",
        ),
    ],
)
def test_a_digital_record_is_refused_if_it_carries_physical_parts(
    mutate: Any, message: str
) -> None:
    record = _digital_record()
    mutate(record)
    with pytest.raises(LedgerError, match=message):
        serialize_record(record)


def test_a_physical_record_with_no_description_is_refused() -> None:
    record = _physical_record()
    record.physical = None
    with pytest.raises(LedgerError, match="no physical description"):
        serialize_record(record)


def test_a_physical_record_carrying_payloads_must_declare_the_surrogate() -> None:
    """Bytes attached to a ``physical`` record have real fixity and must be reported."""
    record = _physical_record()
    record.payloads.append(
        PayloadFile(filename="scan.png", address=ContentAddress(HashAlgo.SHA256, "0" * 64))
    )
    with pytest.raises(LedgerError, match="physical_with_surrogate"):
        serialize_record(record)


def test_a_surrogate_that_does_not_exist_may_not_be_advertised() -> None:
    record = _physical_record()
    record.holding_kind = HoldingKind.PHYSICAL_WITH_SURROGATE
    with pytest.raises(LedgerError, match="carries no payload"):
        serialize_record(record)


# --- the fourth state, and the two functions that may return it -------------


@pytest.mark.parametrize(
    ("kind", "report", "expected"),
    [
        (HoldingKind.DIGITAL, _report(ok=True), FixityStatus.VERIFIED),
        (HoldingKind.DIGITAL, _report(ok=False), FixityStatus.FAILED),
        (HoldingKind.DIGITAL, AuditReport(results=[]), FixityStatus.UNVERIFIED),
        (HoldingKind.PHYSICAL, _report(ok=True), FixityStatus.NOT_APPLICABLE),
        # A verified surrogate is not a verified holding. This row is the single
        # inference the fourth state exists to refuse.
        (HoldingKind.PHYSICAL_WITH_SURROGATE, _report(ok=True), FixityStatus.NOT_APPLICABLE),
        # Failure dominates, so `not_applicable` can never be a place to hide damage.
        (HoldingKind.PHYSICAL, _report(ok=False), FixityStatus.FAILED),
        (HoldingKind.PHYSICAL_WITH_SURROGATE, _report(ok=False), FixityStatus.FAILED),
    ],
)
def test_holding_status_is_a_function_of_the_report_and_the_kind(
    kind: HoldingKind, report: AuditReport, expected: FixityStatus
) -> None:
    assert holding_status(kind, report) is expected


@pytest.mark.parametrize(
    ("pairs", "expected"),
    [
        pytest.param([], FixityStatus.UNVERIFIED, id="empty-archive"),
        pytest.param(
            [(HoldingKind.PHYSICAL, _report()), (HoldingKind.PHYSICAL, _report())],
            FixityStatus.NOT_APPLICABLE,
            id="a-shoebox-catalogue",
        ),
        pytest.param(
            [(HoldingKind.PHYSICAL, _report()), (HoldingKind.DIGITAL, _report())],
            FixityStatus.VERIFIED,
            id="mixed-and-everything-checkable-checked",
        ),
        pytest.param(
            [(HoldingKind.PHYSICAL, _report()), (HoldingKind.DIGITAL, _report(ok=False))],
            FixityStatus.FAILED,
            id="failure-dominates",
        ),
        pytest.param(
            [(HoldingKind.PHYSICAL, _report()), (HoldingKind.DIGITAL, AuditReport(results=[]))],
            FixityStatus.UNVERIFIED,
            id="unverified-beats-verified",
        ),
    ],
)
def test_overall_holding_status_folds_the_four_states(
    pairs: list[tuple[HoldingKind, AuditReport]], expected: FixityStatus
) -> None:
    assert overall_holding_status(pairs) is expected


def test_nothing_but_the_two_holding_functions_can_return_not_applicable() -> None:
    """The self-limiting assertion, and it is most of the value of a fourth state.

    ``AuditReport.status`` and ``overall_status`` answer "did the bytes match their
    manifest", which has no not-applicable answer. If either learns to return one,
    every one of their thirteen callers inherits a state it was never written to
    handle — so the separation is asserted rather than described.
    """
    reports = [_report(ok=True), _report(ok=False), AuditReport(results=[]), _report(count=9)]
    for report in reports:
        assert report.status is not FixityStatus.NOT_APPLICABLE
    assert overall_status(reports) is not FixityStatus.NOT_APPLICABLE
    assert overall_status([]) is not FixityStatus.NOT_APPLICABLE


# --- ingest, audit, and the archive-level log -------------------------------


def test_a_physical_record_ingests_with_no_payload_and_audits_as_not_applicable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Done-when 1 and 2, at the storage layer.

    Note the second assertion: the bag is structurally valid and its metadata
    verifies, which is exactly why the report alone would have said PASS.
    """
    archive = _archive(tmp_path / "arc", monkeypatch)
    record = _physical_record()
    archive.ingest({}, record, now=_NOW)

    holdings = archive.audit_holdings()
    assert [name for name, _k, _r in holdings] == [record.record_id]
    _name, kind, report = holdings[0]
    assert kind is HoldingKind.PHYSICAL
    assert report.status is FixityStatus.VERIFIED, "the bag's own bytes really do verify"
    assert report.checked > 0
    assert holding_status(kind, report) is FixityStatus.NOT_APPLICABLE


def test_the_archive_level_premis_log_says_the_fixity_is_not_applicable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Done-when 2: one hash-chained log answers "what here cannot be verified?"."""
    archive = _archive(tmp_path / "arc", monkeypatch)
    record = _physical_record()
    archive.ingest({}, record, now=_NOW)

    log_path = archive.logs_dir / HOLDINGS_LOG_FILENAME
    assert log_path.is_file()
    events = PremisLog.read(log_path).events
    matching = [
        e
        for e in events
        if e.event_type is PremisEventType.FIXITY_CHECK
        and e.outcome == "not-applicable"
        and e.linked_object == record.record_id
    ]
    assert len(matching) == 1, "exactly one standing statement per physical record"
    # A consumer filtering the log by fixity check must SEE this record and read the
    # answer. An absent event and a not-applicable one look identical to anything
    # counting successes, and only one of them is a statement.
    assert matching[0].outcome != "success"
    # The log is covered by the ordinary archive-level chain audit, like the takedown
    # and key-rotation logs beside it.
    chains = dict(archive.audit_log_chains())
    assert chains[HOLDINGS_LOG_FILENAME].ok


def test_the_holdings_log_never_carries_custody(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The log is a plain file on disk and an archive-level artifact. It is not a
    place for the custodian's whereabouts, however convenient that would be."""
    archive = _archive(tmp_path / "arc", monkeypatch)
    archive.ingest({}, _physical_record(), now=_NOW)
    text = (archive.logs_dir / HOLDINGS_LOG_FILENAME).read_text(encoding="utf-8")
    assert _SENTINEL_CUSTODIAN not in text
    assert _SENTINEL_LOCATION not in text


def test_a_tampered_physical_record_fails_rather_than_reading_as_not_applicable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``not_applicable`` must never become a place to hide damage.

    Editing a physical record's manifest breaks the bag's tag manifest, and failure
    dominates the kind, so the row is FAIL — the same as it would be for a digital
    record.
    """
    archive = _archive(tmp_path / "arc", monkeypatch)
    record = _physical_record()
    archive.ingest({}, record, now=_NOW)
    manifest = archive.bags_dir / record.record_id / "record.json"
    manifest.write_text(manifest.read_text(encoding="utf-8").replace("380", "999"), "utf-8")

    _name, kind, report = archive.audit_holdings()[0]
    assert kind is HoldingKind.PHYSICAL
    assert holding_status(kind, report) is FixityStatus.FAILED


def test_relabelling_a_rotted_digital_record_as_physical_does_not_silence_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The attack the fourth state invites, and why it does not work.

    Somebody with raw disk access flips a digital record's ``holding_kind`` to
    ``physical`` to make a rotted payload read as "nothing to check". The bag still
    *declares* that payload in its payload manifest, so ``validate_bag`` still fails
    on it, and failure is evaluated before the kind.
    """
    archive = _archive(tmp_path / "arc", monkeypatch)
    record = _ingest_digital(archive, tmp_path, "Rotting scan")
    bag = archive.bags_dir / record.record_id
    (bag / "data" / "Rotting-scan.txt").write_text("tampered\n", encoding="utf-8")

    manifest = bag / "record.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["holding_kind"] = "physical"
    payload["physical"] = {"format": "zine"}
    manifest.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), "utf-8")

    _name, kind, report = archive.audit_holdings()[0]
    assert kind is HoldingKind.PHYSICAL, "the relabelling did take effect"
    assert holding_status(kind, report) is FixityStatus.FAILED


# --- custody is on the no-outing path ---------------------------------------


def test_custody_is_absent_from_every_ungranted_surface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Done-when 1, and the safety property the whole feature rests on.

    Every read path an outsider can reach is walked and asserted free of both the
    custodian and the location. They travel as ordinary sealed fields, so this is
    the existing guarantee rather than a new one — which is precisely the argument
    for carrying them that way instead of in a structured block with its own
    disclosure branch.
    """
    archive = _archive(tmp_path / "arc", monkeypatch)
    record = _physical_record()
    archive.ingest({}, record, now=_NOW)

    grant = anonymous()
    disclosed = disclose(archive.get(record.record_id), grant, _NOW)
    surfaces = {
        "disclosed.fields": json.dumps(disclosed.fields),
        "to_dict(insider form)": json.dumps(disclosed.to_dict()),
        "to_dict(outsider form)": json.dumps(disclosed.to_dict(withheld_reasons=False)),
        "record page": render._record_main_html(disclosed, proceed=True),
        "browse list": render._records_list_html([disclosed]),
        "browse table": render._records_table_html([disclosed]),
    }
    booklet = tmp_path / "booklet.html"
    build_print_edition(archive, booklet, base_url="https://example.test", now=_NOW)
    surfaces["print edition"] = booklet.read_text(encoding="utf-8")

    drive = tmp_path / "drive"
    build_export_drive(archive, drive, grant=grant, now=_NOW)
    surfaces["courier package"] = "\n".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in sorted(drive.rglob("*"))
        if p.is_file()
    )

    for name, text in surfaces.items():
        assert _SENTINEL_CUSTODIAN not in text, f"custodian leaked on {name}"
        assert _SENTINEL_LOCATION not in text, f"custody location leaked on {name}"


def test_a_steward_with_a_grant_does_see_the_custody_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The positive half. A guarantee proven only in the refusing direction is
    satisfied by a policy that refuses everybody, and custody sealed from the
    stewards who have to look after the collection would be useless."""
    archive = _archive(tmp_path / "arc", monkeypatch)
    record = _physical_record()
    archive.ingest({}, record, now=_NOW)

    disclosed = disclose(archive.get(record.record_id), steward("warden"), _NOW)
    assert disclosed.fields[CUSTODY_CUSTODIAN_FIELD] == _SENTINEL_CUSTODIAN
    assert disclosed.fields[CUSTODY_LOCATION_FIELD] == _SENTINEL_LOCATION
    assert disclosed.custody_state is CustodyState.DISCLOSED


def test_the_custody_state_word_has_three_distinct_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three states, and the third is the one a collapse would lose.

    "Recorded but not shown to you" and "nobody wrote it down" are different facts,
    and rendering the second as the first publishes *somebody is looking after
    this* over a record where nobody is.
    """
    archive = _archive(tmp_path / "arc", monkeypatch)
    with_custody = _physical_record()
    without = _physical_record("An orphaned box of negatives", custody=False)
    archive.ingest({}, with_custody, now=_NOW)
    archive.ingest({}, without, now=_NOW)

    stored_with = archive.get(with_custody.record_id)
    stored_without = archive.get(without.record_id)
    assert stored_with.has_custody()
    assert not stored_without.has_custody()

    assert disclose(stored_with, anonymous(), _NOW).custody_state is CustodyState.WITHHELD
    assert disclose(stored_with, steward("w"), _NOW).custody_state is CustodyState.DISCLOSED
    assert disclose(stored_without, anonymous(), _NOW).custody_state is CustodyState.NOT_RECORDED
    assert disclose(stored_without, steward("w"), _NOW).custody_state is CustodyState.NOT_RECORDED


def test_the_custody_state_word_is_derived_from_the_projection_not_supplied() -> None:
    """It cannot disagree with the values beside it, because both come from one place."""
    record = _physical_record()
    withheld = disclose(record, anonymous(), _NOW)
    assert withheld.custody_state is CustodyState.WITHHELD
    assert CUSTODY_CUSTODIAN_FIELD not in withheld.fields
    assert CUSTODY_CUSTODIAN_FIELD in {r.name for r in withheld.withheld}


# --- what browse and the record page render ---------------------------------


def test_browse_shows_physical_not_digitized_in_both_equivalent_views() -> None:
    """Done-when 1. Both views, because they are documented equivalents: a badge in
    one and not the other is a reader on a small screen learning less."""
    disclosed = disclose(_physical_record(), anonymous(), _NOW)
    badge = i18n.t("en", "holding_physical_badge")
    assert badge == "Physical · not digitized"
    assert badge in render._records_list_html([disclosed])
    assert badge in render._records_table_html([disclosed])


def test_a_digital_record_carries_no_holding_badge() -> None:
    """The badge means something only if it is absent from the ordinary case."""
    disclosed = disclose(_digital_record(), anonymous(), _NOW)
    assert "badge holding" not in render._records_list_html([disclosed])
    assert "badge holding" not in render._records_table_html([disclosed])


@pytest.mark.parametrize("lang", ["es", "fr", "ar"])
def test_the_badge_is_translated_in_the_rendered_page_not_only_at_the_seam(lang: str) -> None:
    """A seam gate cannot see a locale bug: a call that passes ``"en"`` through the
    seam is still *through the seam* and serves English on a translated page. So this
    asserts on rendered output, and asserts the English is gone rather than only that
    the translation is present."""
    disclosed = disclose(_physical_record(), anonymous(), _NOW)
    page = render._records_list_html([disclosed], lang=lang)
    translated = i18n.t(lang, "holding_physical_badge")
    assert translated != i18n.t("en", "holding_physical_badge")
    assert translated in page
    assert "Physical · not digitized" not in page


def test_the_record_page_renders_format_extent_and_the_custody_state_word() -> None:
    """Done-when 1: browse and the record view render the format, the extent, and
    the custody state word."""
    disclosed = disclose(_physical_record(), anonymous(), _NOW)
    page = render._record_main_html(disclosed, proceed=True)
    assert i18n.t("en", "holding_heading") in page
    assert i18n.physical_format_label("en", "flyer") in page
    assert "1 box, ~380 flyers" in page
    assert i18n.t("en", "custody_withheld") in page
    # The line that stops the page reading like a record whose files verified.
    assert i18n.t("en", "holding_no_fixity") in page


def test_the_record_page_of_a_digital_record_has_no_holding_section() -> None:
    page = render._record_main_html(disclose(_digital_record(), anonymous(), _NOW), proceed=True)
    assert i18n.t("en", "holding_heading") not in page


def test_an_unknown_format_falls_back_to_itself_not_to_another_format() -> None:
    assert i18n.physical_format_label("en", "wax-cylinder") == "Wax cylinder"
    assert i18n.physical_format_label("es", "zine") != i18n.physical_format_label("es", "book")


# --- attaching a surrogate --------------------------------------------------


def test_attaching_a_surrogate_links_the_record_to_its_derivative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Done-when 3, all four halves: the event, the link, the surrogate's real
    fixity, and the holding's verdict staying not-applicable."""
    archive = _archive(tmp_path / "arc", monkeypatch)
    record = _physical_record()
    archive.ingest({}, record, now=_NOW)

    scan = tmp_path / "page-01.txt"
    scan.write_text("a photographed page\n", encoding="utf-8")
    event = archive.attach_surrogate(record.record_id, "page-01.txt", scan, now=_NOW)

    assert event.event_type is PremisEventType.DIGITIZATION
    assert event.linked_object == record.record_id
    assert event.linked_content_address is not None

    stored = archive.get(record.record_id)
    assert stored.holding_kind is HoldingKind.PHYSICAL_WITH_SURROGATE
    assert [p.filename for p in stored.payloads] == ["page-01.txt"]

    # The surrogate is ordinary digital content with an ordinary fixity result: the
    # bag re-validates, and the new file is covered by the payload manifests.
    report = validate_bag(archive.bags_dir / record.record_id)
    assert report.ok
    assert any("page-01.txt" in result.path for result in report.results)

    # And the holding's own verdict has not moved. This is the assertion the whole
    # `physical_with_surrogate` state exists for.
    _name, kind, bag_report = archive.audit_holdings()[0]
    assert holding_status(kind, bag_report) is FixityStatus.NOT_APPLICABLE


def test_the_digitization_event_reaches_both_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _archive(tmp_path / "arc", monkeypatch)
    record = _physical_record()
    archive.ingest({}, record, now=_NOW)
    scan = tmp_path / "page-01.txt"
    scan.write_text("a photographed page\n", encoding="utf-8")
    archive.attach_surrogate(record.record_id, "page-01.txt", scan, now=_NOW)

    in_bag = PremisLog.read(archive.bags_dir / record.record_id / "premis.json").events
    archive_level = PremisLog.read(archive.logs_dir / HOLDINGS_LOG_FILENAME).events
    for events in (in_bag, archive_level):
        assert any(e.event_type is PremisEventType.DIGITIZATION for e in events)


def test_a_surrogate_is_refused_on_a_digital_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _archive(tmp_path / "arc", monkeypatch)
    record = _ingest_digital(archive, tmp_path, "Already digital")
    scan = tmp_path / "another.txt"
    scan.write_text("x\n", encoding="utf-8")
    with pytest.raises(LedgerError, match="digitizes a physical object"):
        archive.attach_surrogate(record.record_id, "another.txt", scan, now=_NOW)


def test_a_second_surrogate_never_overwrites_the_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An *add* that silently replaced a file would lose a payload the archive was
    handed, which is the one outcome a preservation tool must never reach quietly."""
    archive = _archive(tmp_path / "arc", monkeypatch)
    record = _physical_record()
    archive.ingest({}, record, now=_NOW)
    scan = tmp_path / "page-01.txt"
    scan.write_text("first\n", encoding="utf-8")
    archive.attach_surrogate(record.record_id, "page-01.txt", scan, now=_NOW)

    replacement = tmp_path / "other.txt"
    replacement.write_text("second\n", encoding="utf-8")
    with pytest.raises((LedgerError, BagValidationError)):
        archive.attach_surrogate(record.record_id, "page-01.txt", replacement, now=_NOW)
    assert (archive.bags_dir / record.record_id / "data" / "page-01.txt").read_text(
        encoding="utf-8"
    ) == "first\n"


# --- the offline artifacts --------------------------------------------------


def test_the_print_edition_says_the_digest_covers_the_description_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Paper cannot be corrected. A printed SHA-256 under a description of a
    cassette would otherwise read as proof the cassette is intact."""
    archive = _archive(tmp_path / "arc", monkeypatch)
    archive.ingest({}, _physical_record(), now=_NOW)
    out = tmp_path / "booklet.html"
    build_print_edition(archive, out, base_url="https://example.test", now=_NOW)
    html = out.read_text(encoding="utf-8")
    assert "Physical item — not digitized" in html
    assert "The digest below covers this printed description only" in html
    assert "1 box, ~380 flyers" in html


def test_the_courier_package_says_there_are_no_files_rather_than_none_disclosed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "No files disclosed." tells the reader that files exist and are being kept
    from them. For a physical holding there are none and never were."""
    archive = _archive(tmp_path / "arc", monkeypatch)
    record = _physical_record()
    archive.ingest({}, record, now=_NOW)
    drive = tmp_path / "drive"
    build_export_drive(archive, drive, grant=anonymous(), now=_NOW)
    page = (drive / "records" / f"{record.record_id}.html").read_text(encoding="utf-8")
    assert "Not digitized — there are no files, and none are being withheld." in page
    assert "No files disclosed." not in page
    assert "(not digitized)" in (drive / "index.html").read_text(encoding="utf-8")


# --- the hand-off document --------------------------------------------------


def test_a_handoff_of_an_undigitized_collection_does_not_say_all_bags_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reader here is a non-ops volunteer taking custody of something they did
    not build, and "All bags verified intact at hand-off time" over forty
    undigitized zines is the worst sentence in the project to get wrong."""
    archive = _archive(tmp_path / "arc", monkeypatch)
    archive.ingest({}, _physical_record(), now=_NOW)
    manifest = build_handoff(archive, successor="new-steward", now=_NOW)

    assert manifest.fixity_status is FixityStatus.NOT_APPLICABLE
    runbook = manifest.runbook()
    assert "All bags verified intact at hand-off time." not in runbook
    assert "has not been digitized" in runbook
    assert manifest.records[0].to_dict()["holding_kind"] == "physical"


def test_a_mixed_handoff_still_names_the_records_nothing_can_verify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _archive(tmp_path / "arc", monkeypatch)
    _ingest_digital(archive, tmp_path, "A scan")
    archive.ingest({}, _physical_record(), now=_NOW)
    manifest = build_handoff(archive, successor="new-steward", now=_NOW)

    assert manifest.fixity_status is FixityStatus.VERIFIED
    assert "1 of these record(s) describe physical objects" in manifest.runbook()


def test_a_digital_only_handoff_row_is_unchanged_by_this_feature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The schema does not move for an archive that has no physical holdings."""
    archive = _archive(tmp_path / "arc", monkeypatch)
    _ingest_digital(archive, tmp_path, "A scan")
    manifest = build_handoff(archive, successor=None, now=_NOW)
    assert set(manifest.records[0].to_dict()) == {"record_id", "fixity_ok", "files_checked"}


# --- /healthz and /status ---------------------------------------------------


def _serve(archive: Archive, tmp_path: Path, name: str) -> Iterator[str]:
    from ledger.server import make_server

    grants = tmp_path / f"{name}-grants.json"
    grants.write_text(
        json.dumps({"warden": {"levels": ["public", "community", "stewards"], "is_steward": True}}),
        encoding="utf-8",
    )
    httpd = make_server(archive, host="127.0.0.1", port=0, grants_path=grants)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        thread.join(timeout=5)
        httpd.server_close()


def _get(base: str, path: str, *, steward_token: bool = False) -> tuple[int, str]:
    request = urllib.request.Request(f"{base}{path}")  # noqa: S310 - loopback
    if steward_token:
        request.add_header("X-Ledger-Grant", issue_grant_token("warden", _GRANT_SECRET))
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - loopback URL we constructed for the in-process test server
            return int(response.status), response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        return int(error.code), error.read().decode("utf-8")


@pytest.fixture
def physical_site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    archive = _archive(tmp_path / "physical", monkeypatch, name="Physical Only")
    archive.ingest({}, _physical_record(), now=_NOW)
    yield from _serve(archive, tmp_path, "physical")


@pytest.fixture
def digital_site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    archive = _archive(tmp_path / "digital", monkeypatch, name="Digital Only")
    _ingest_digital(archive, tmp_path, "A scan")
    yield from _serve(archive, tmp_path, "digital")


def test_the_anonymous_healthz_body_is_identical_for_a_physical_and_a_digital_archive(
    physical_site: str, digital_site: str
) -> None:
    """The anti-enumeration line, held.

    Making the anonymous payload "honest" about a physical archive would hand an
    outsider a second oracle: ``degraded`` with no failing bag would say the archive
    holds only undigitized material. Every other route to ``all_verified: false``
    also returns 503, so the honest verdict goes in the steward-gated block instead
    — exactly where #219 put the three-state one.
    """
    physical = _get(physical_site, "/healthz")
    digital = _get(digital_site, "/healthz")
    assert physical == digital
    assert physical[0] == 200


def test_a_steward_reading_healthz_is_told_the_archive_verified_nothing(
    physical_site: str,
) -> None:
    code, body = _get(physical_site, "/healthz", steward_token=True)
    assert code == 200
    fixity_block = json.loads(body)["fixity"]
    assert fixity_block["status"] == FixityStatus.NOT_APPLICABLE.value
    assert fixity_block["bags_audited"] == 1
    assert fixity_block["bags_verified"] == 0
    assert fixity_block["bags_not_applicable"] == 1
    assert fixity_block["bags_failed"] == 0


def test_a_steward_reading_healthz_over_a_digital_archive_still_sees_a_verified_count(
    digital_site: str,
) -> None:
    """The other direction, so the block above is not satisfied by a reader that
    reports not-applicable for everything."""
    _code, body = _get(digital_site, "/healthz", steward_token=True)
    fixity_block = json.loads(body)["fixity"]
    assert fixity_block["status"] == FixityStatus.VERIFIED.value
    assert fixity_block["bags_verified"] == 1
    assert fixity_block["bags_not_applicable"] == 0


def test_the_status_page_calls_a_shoebox_catalogue_what_it_is(physical_site: str) -> None:
    _code, body = _get(physical_site, "/status")
    assert i18n.t("en", "status_headline_not_applicable") in body
    assert i18n.t("en", "status_headline_verified") not in body


def test_the_status_page_still_says_healthy_for_a_digital_archive(digital_site: str) -> None:
    _code, body = _get(digital_site, "/status")
    assert i18n.t("en", "status_headline_verified") in body


# --- the steward's own surface ----------------------------------------------


def test_browse_carries_the_holding_kind_through_the_catalog_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The browse path reads through a sqlite cache, not the record files.

    A cache that stored a subset of the manifest would drop ``holding_kind`` and
    every page built from browse would render a physical holding as an ordinary
    record — a build-time model that the runtime never sees. It stores the manifest
    text verbatim, and this is the assertion that says so.
    """
    archive = _archive(tmp_path / "arc", monkeypatch)
    record = _physical_record()
    archive.ingest({}, record, now=_NOW)
    browsed = {r.record_id: r for r in archive.browse(anonymous(), now=_NOW)}
    assert browsed[record.record_id].holding_kind is HoldingKind.PHYSICAL
    assert browsed[record.record_id].physical is not None
    assert browsed[record.record_id].physical.extent == "1 box, ~380 flyers"


def test_the_cli_catalogues_a_shoebox_audits_it_and_attaches_a_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Done-when 1, 2 and 3 through the surface a community archivist actually uses.

    The audit line is the whole point of the feature in one row: ``n/a``, never
    ``PASS``, with the denominators beside it so "0 failed" cannot be read as
    "everything is fine".
    """
    from ledger import cli

    monkeypatch.setenv("LEDGER_VAULT_KEY", _VAULT_KEY)
    root = tmp_path / "arc"
    assert cli.main(["init", "--root", str(root), "--name", "Shoebox"]) == 0
    capsys.readouterr()

    assert (
        cli.main(
            [
                "ingest",
                "--root",
                str(root),
                "--title",
                "Four boxes, 1994 clinic defence",
                "--description",
                "Leaflets and a newsletter run.",
                "--physical",
                "flyer",
                "--extent",
                "1 box, ~380 flyers",
                "--custodian",
                _SENTINEL_CUSTODIAN,
                "--now",
                _NOW,
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    record_id = next(
        line.split(": ", 1)[1] for line in captured.out.splitlines() if line.startswith("record_id")
    )
    assert "not-applicable" in captured.err
    assert _SENTINEL_CUSTODIAN not in captured.out, "the custodian is never echoed back"

    assert cli.main(["audit", "--root", str(root)]) == 0
    audit = capsys.readouterr().out
    assert f"n/a\t{record_id}" in audit
    assert "PASS\t" + record_id not in audit
    assert "NOTHING TO VERIFY: 1 bag(s) audited, 0 verified, 1 not applicable" in audit

    scan = tmp_path / "page-01.txt"
    scan.write_text("a photographed page\n", encoding="utf-8")
    assert (
        cli.main(
            [
                "surrogate",
                "--root",
                str(root),
                "--id",
                record_id,
                "--file",
                str(scan),
                "--now",
                _NOW,
            ]
        )
        == 0
    )
    capsys.readouterr()

    # And after a scan is attached the row still refuses to say PASS.
    assert cli.main(["audit", "--root", str(root)]) == 0
    after = capsys.readouterr().out
    assert f"n/a\t{record_id}" in after
    assert "physical_with_surrogate" in after


def test_the_cli_refuses_a_custodian_with_no_physical_declaration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Silently dropping a custodian the steward typed loses exactly the datum this
    feature exists to protect, so it is a refusal rather than a shrug."""
    from ledger import cli

    monkeypatch.setenv("LEDGER_VAULT_KEY", _VAULT_KEY)
    root = tmp_path / "arc"
    assert cli.main(["init", "--root", str(root), "--name", "Shoebox"]) == 0
    capsys.readouterr()
    code = cli.main(
        [
            "ingest",
            "--root",
            str(root),
            "--title",
            "A record",
            "--custodian",
            _SENTINEL_CUSTODIAN,
            "--now",
            _NOW,
        ]
    )
    assert code != 0
    captured = capsys.readouterr()
    assert "pass --physical FORMAT" in captured.err
    # The refusal names the flags without echoing the value it refused to store.
    assert _SENTINEL_CUSTODIAN not in captured.err
    assert _SENTINEL_CUSTODIAN not in captured.out
