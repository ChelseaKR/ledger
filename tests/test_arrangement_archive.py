"""The archive end of arrangement: storing it, filing into it, and the CLI (#202).

`tests/test_arrangement_policy.py` proves the resolver; this proves the archive
actually uses it — that `browse` and `disclose` are judged against the ceiling
on disk, that a placement is refused at the door rather than discovered by a
record's absence, and that the one operable surface over failing closed
(`ledger arrange check`) says what the resolver silently decided.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ledger import cli
from ledger.access.grants import anonymous, community_member, steward
from ledger.arrangement import save_container
from ledger.config import Config
from ledger.errors import LedgerError, ObjectNotFound
from ledger.ingest import Archive
from ledger.models import (
    OBJECT_TYPE_CONTAINER,
    OBJECT_TYPE_RECORD,
    AccessPolicy,
    ArchivalContainer,
    ContainerLevel,
    DublinCore,
    Field,
    PremisEventType,
    Record,
)

pytestmark = pytest.mark.disclosure

_NOW = "2026-06-16T00:00:00Z"


def _archive(tmp_path: Path) -> Archive:
    return Archive.init(Config.default("Casa Abierta Archive", tmp_path / "arch"))


def _record(rid: str = "rec-1", *, placement: str | None = None) -> Record:
    return Record(
        title="Rent strike flyer",
        record_id=rid,
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(title=["Rent strike flyer"], subject=["housing"]),
        fields=[Field(name="story", value="We papered the block.", policy=AccessPolicy.PUBLIC)],
        created_at=_NOW,
        placement=placement,
    )


def _collection(
    cid: str = "casa-abierta",
    *,
    policy: AccessPolicy = AccessPolicy.PUBLIC,
    records_policy: AccessPolicy = AccessPolicy.PUBLIC,
) -> ArchivalContainer:
    return ArchivalContainer(
        container_id=cid,
        title="Casa Abierta deposit",
        level=ContainerLevel.COLLECTION,
        scope_and_content="Four boxes left with us after the March raid.",
        extent="4 boxes",
        dates="1987-1994",
        policy=policy,
        records_policy=records_policy,
        created_at=_NOW,
    )


# --- the archive reads the ceiling that is on disk --------------------------


def test_browse_applies_the_container_ceiling_stored_on_disk(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    archive.describe_container(_collection(records_policy=AccessPolicy.COMMUNITY), now=_NOW)
    payload = tmp_path / "flyer.txt"
    payload.write_text("flyer", encoding="utf-8")
    archive.ingest({"flyer.txt": payload}, _record(placement="casa-abierta"), now=_NOW)

    assert archive.browse(anonymous(), now=_NOW) == []
    assert [r.record_id for r in archive.browse(community_member("m"), now=_NOW)] == ["rec-1"]


def test_re_describing_a_container_re_clamps_records_already_in_it(tmp_path: Path) -> None:
    """A steward narrows one container and 400 records narrow with it.

    This is the whole point of #202's "one policy on a container instead of 400
    per-record decisions", and it is why the ceiling is applied at read time
    rather than stamped onto each record at ingest.
    """
    archive = _archive(tmp_path)
    archive.describe_container(_collection(records_policy=AccessPolicy.PUBLIC), now=_NOW)
    payload = tmp_path / "flyer.txt"
    payload.write_text("flyer", encoding="utf-8")
    archive.ingest({"flyer.txt": payload}, _record(placement="casa-abierta"), now=_NOW)
    assert len(archive.browse(anonymous(), now=_NOW)) == 1

    archive.describe_container(_collection(records_policy=AccessPolicy.STEWARDS), now=_NOW)
    assert archive.browse(anonymous(), now=_NOW) == []
    assert len(archive.browse(steward("s"), now=_NOW)) == 1

    # And the record's own manifest never moved: the narrowing is the
    # container's, not a rewrite of what the contributor agreed to.
    assert archive.get("rec-1").default_policy is AccessPolicy.PUBLIC


def test_widening_the_container_cannot_widen_the_record_past_its_own_policy(
    tmp_path: Path,
) -> None:
    archive = _archive(tmp_path)
    archive.describe_container(_collection(records_policy=AccessPolicy.PUBLIC), now=_NOW)
    payload = tmp_path / "flyer.txt"
    payload.write_text("flyer", encoding="utf-8")
    sealed = Record(
        title="The safehouse list",
        record_id="rec-sealed",
        default_policy=AccessPolicy.STEWARDS,
        created_at=_NOW,
        placement="casa-abierta",
    )
    archive.ingest({"flyer.txt": payload}, sealed, now=_NOW)
    assert archive.browse(anonymous(), now=_NOW) == []
    assert len(archive.browse(steward("s"), now=_NOW)) == 1


# --- the write path refuses what the read path would silently hide ----------


def test_ingesting_into_a_container_that_is_not_there_is_refused(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    payload = tmp_path / "flyer.txt"
    payload.write_text("flyer", encoding="utf-8")
    with pytest.raises(ObjectNotFound) as exc:
        archive.ingest({"flyer.txt": payload}, _record(placement="nope"), now=_NOW)
    assert str(exc.value) == "nope"
    assert not (archive.bags_dir / "rec-1").exists()


def test_ingesting_into_a_container_with_a_broken_chain_is_refused(tmp_path: Path) -> None:
    """A record nobody could ever see is not quietly stored.

    The container file is written past `describe_container` on purpose — this
    is the hand-edited-directory case, and the point is that the *ingest* still
    refuses rather than leaving the steward to notice a missing row.
    """
    archive = _archive(tmp_path)
    save_container(
        archive.containers_dir,
        ArchivalContainer(
            container_id="orphan",
            title="Orphan series",
            level=ContainerLevel.SERIES,
            parent_id="gone",
            created_at=_NOW,
        ),
    )
    payload = tmp_path / "flyer.txt"
    payload.write_text("flyer", encoding="utf-8")
    with pytest.raises(LedgerError, match="visible to no one"):
        archive.ingest({"flyer.txt": payload}, _record(placement="orphan"), now=_NOW)


def test_placing_a_record_into_a_missing_container_is_refused(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    payload = tmp_path / "flyer.txt"
    payload.write_text("flyer", encoding="utf-8")
    archive.ingest({"flyer.txt": payload}, _record(), now=_NOW)
    with pytest.raises(ObjectNotFound):
        archive.place("rec-1", "nope", now=_NOW)
    assert archive.get("rec-1").placement is None


def test_describing_a_malformed_container_is_refused_before_it_is_stored(
    tmp_path: Path,
) -> None:
    archive = _archive(tmp_path)
    with pytest.raises(LedgerError, match="must name the collection"):
        archive.describe_container(
            ArchivalContainer(
                container_id="ser-1",
                title="Flyers",
                level=ContainerLevel.SERIES,
                created_at=_NOW,
            ),
            now=_NOW,
        )
    assert list(archive.containers_dir.glob("*.json")) == []


# --- placement is an accountable change -------------------------------------


def test_filing_and_unfiling_a_record_are_recorded_on_its_own_premis_log(
    tmp_path: Path,
) -> None:
    archive = _archive(tmp_path)
    archive.describe_container(_collection(), now=_NOW)
    payload = tmp_path / "flyer.txt"
    payload.write_text("flyer", encoding="utf-8")
    archive.ingest({"flyer.txt": payload}, _record(), now=_NOW)

    archive.place("rec-1", "casa-abierta", agent="steward-a", now=_NOW)
    assert archive.get("rec-1").placement == "casa-abierta"
    archive.place("rec-1", None, agent="steward-a", now=_NOW)
    assert archive.get("rec-1").placement is None

    events = [
        e for e in archive.record_events("rec-1") if e.event_type is PremisEventType.ARRANGEMENT
    ]
    assert [e.detail for e in events] == [
        "record filed in container casa-abierta",
        "record removed from its container",
    ]
    assert {e.linked_object_type for e in events} == {OBJECT_TYPE_RECORD}


def test_describing_a_container_is_recorded_and_names_no_description(
    tmp_path: Path,
) -> None:
    """The audit log says a container was described; it never says what it says.

    A scope note is itself disclosable material — "deposited by Casa Abierta
    after the March raid" is the sentence the container's policy exists to
    control — so it must not be copied into a log that every steward view
    aggregates (no-outing rule: logs disclose nothing).
    """
    archive = _archive(tmp_path)
    archive.describe_container(_collection(), agent="steward-a", now=_NOW)
    archive.describe_container(_collection(), agent="steward-a", now=_NOW)

    log = (archive.logs_dir / "arrangement.premis.json").read_text(encoding="utf-8")
    assert "March raid" not in log
    assert "Casa Abierta deposit" not in log
    events = [e for e in archive.audit_events() if e.event_type is PremisEventType.ARRANGEMENT]
    assert sorted(e.detail for e in events) == [
        "collection described",
        "collection re-described",
    ]
    assert {e.linked_object_type for e in events} == {OBJECT_TYPE_CONTAINER}


# --- the operable surface over failing closed -------------------------------


def test_arrangement_problems_names_every_record_the_resolver_silently_hid(
    tmp_path: Path,
) -> None:
    archive = _archive(tmp_path)
    archive.describe_container(_collection(), now=_NOW)
    payload = tmp_path / "flyer.txt"
    payload.write_text("flyer", encoding="utf-8")
    archive.ingest({"flyer.txt": payload}, _record(placement="casa-abierta"), now=_NOW)
    assert archive.arrangement_problems() == []

    # Somebody deletes the container manifest by hand. The record is now
    # invisible to everyone, correctly and silently; this is what says so.
    (archive.containers_dir / "casa-abierta.json").unlink()
    assert archive.browse(steward("s"), now=_NOW) == []
    assert archive.arrangement_problems() == [
        "record rec-1: filed in casa-abierta, which does not resolve — "
        "the record is visible to no one"
    ]


def test_an_unreadable_container_file_is_reported_rather_than_only_skipped(
    tmp_path: Path,
) -> None:
    archive = _archive(tmp_path)
    (archive.containers_dir / "broken.json").write_text("{not json", encoding="utf-8")
    assert archive.arrangement_problems() == ["container file broken.json: manifest cannot be read"]


# --- the CLI ----------------------------------------------------------------


def _run(argv: list[str]) -> int:
    return cli.main(argv)


def test_the_cli_describes_files_and_checks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "arch"
    payload = tmp_path / "flyer.txt"
    payload.write_text("flyer", encoding="utf-8")
    assert _run(["init", "--root", str(root), "--name", "Casa Abierta Archive"]) == 0
    assert (
        _run(
            [
                "arrange",
                "describe",
                "--root",
                str(root),
                "--id",
                "casa-abierta",
                "--title",
                "Casa Abierta deposit",
                "--policy",
                "community",
                "--records-policy",
                "community",
                "--now",
                _NOW,
            ]
        )
        == 0
    )
    assert (
        _run(
            [
                "arrange",
                "describe",
                "--root",
                str(root),
                "--id",
                "flyers",
                "--title",
                "Flyers",
                "--level",
                "series",
                "--parent",
                "casa-abierta",
                "--policy",
                "public",
                "--records-policy",
                "public",
                "--now",
                _NOW,
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert _run(["arrange", "list", "--root", str(root)]) == 0
    listed = capsys.readouterr().out
    assert "casa-abierta" in listed
    assert "  flyers" in listed
    assert "(2 container(s), 0 unresolvable)" in listed

    assert (
        _run(
            [
                "ingest",
                "--root",
                str(root),
                "--title",
                "Rent strike flyer",
                "--collection",
                "flyers",
                str(payload),
                "--now",
                _NOW,
            ]
        )
        == 0
    )
    record_id = next(
        line.split(": ", 1)[1].strip()
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("record_id:")
    )

    # The collection's ceiling is community, so the anonymous browse shows
    # nothing even though the record and its series are both public.
    assert _run(["browse", "--root", str(root)]) == 0
    assert "(0 record(s) visible to anonymous)" in capsys.readouterr().out
    assert _run(["browse", "--root", str(root), "--as", "maria"]) == 0
    assert record_id in capsys.readouterr().out

    assert _run(["arrange", "check", "--root", str(root)]) == 0
    assert "every container resolves" in capsys.readouterr().out

    assert (
        _run(
            [
                "arrange",
                "place",
                "--root",
                str(root),
                "--id",
                record_id,
                "--into",
                "casa-abierta",
            ]
        )
        == 0
    )
    assert "filed in casa-abierta" in capsys.readouterr().out


def test_the_cli_check_exits_non_zero_so_a_cron_can_notice(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "arch"
    assert _run(["init", "--root", str(root), "--name", "A"]) == 0
    (root / "store" / "containers" / "broken.json").write_text("{not json", encoding="utf-8")
    capsys.readouterr()
    assert _run(["arrange", "check", "--root", str(root)]) == 1
    assert "manifest cannot be read" in capsys.readouterr().out


def test_the_stored_container_manifest_matches_the_published_schema(tmp_path: Path) -> None:
    """Every property the writer emits is one the published schema declares.

    `src/ledger/metadata/schema/container.schema.json` is `additionalProperties:
    false`, so a key the writer adds and the schema does not declare makes every
    stored manifest invalid against ledger's own published contract. Nothing in
    the dependency graph validates JSON Schema, so this compares the two key
    sets directly rather than claiming a validation it does not perform.
    """
    archive = _archive(tmp_path)
    archive.describe_container(_collection(), now=_NOW)
    stored = json.loads((archive.containers_dir / "casa-abierta.json").read_text(encoding="utf-8"))
    schema = json.loads(
        (
            Path(__file__).resolve().parent.parent
            / "src/ledger/metadata/schema/container.schema.json"
        ).read_text(encoding="utf-8")
    )
    assert set(stored) == set(schema["properties"])
    assert set(schema["required"]) == set(schema["properties"])


def test_the_record_schema_declares_every_key_a_record_manifest_can_carry(
    tmp_path: Path,
) -> None:
    """The same check for `placement` on the record side.

    `record.schema.json` is also `additionalProperties: false`. #202 adds a
    property to a *published* contract, and the one way that goes wrong quietly
    is the writer and the schema disagreeing.
    """
    archive = _archive(tmp_path)
    archive.describe_container(_collection(), now=_NOW)
    payload = tmp_path / "flyer.txt"
    payload.write_text("flyer", encoding="utf-8")
    archive.ingest({"flyer.txt": payload}, _record(placement="casa-abierta"), now=_NOW)
    stored = json.loads((archive.records_dir / "rec-1.json").read_text(encoding="utf-8"))
    schema = json.loads(
        (
            Path(__file__).resolve().parent.parent / "src/ledger/metadata/schema/record.schema.json"
        ).read_text(encoding="utf-8")
    )
    assert "placement" in stored
    assert set(stored) <= set(schema["properties"])
    # `placement` is the one property that is optional rather than required,
    # because a record written before #202 does not carry it.
    assert set(schema["properties"]) - set(schema["required"]) == {"placement"}


def test_an_unarranged_record_manifest_is_byte_identical_to_the_pre_202_bytes(
    tmp_path: Path,
) -> None:
    """No migration, and none needed: the absent key is absent, not null.

    #202's third "decide first" item asked whether every record should be
    migrated into an implicit "unarranged" container. The answer here is no,
    and this is the property that makes that answer free — a record nobody
    arranged serializes to exactly the bytes it always did, so no committed
    manifest, bag manifest or tag-file digest moves.
    """
    archive = _archive(tmp_path)
    payload = tmp_path / "flyer.txt"
    payload.write_text("flyer", encoding="utf-8")
    archive.ingest({"flyer.txt": payload}, _record(), now=_NOW)
    stored = (archive.records_dir / "rec-1.json").read_text(encoding="utf-8")
    assert "placement" not in stored
