"""Every number the real-corpus write-up states is re-derived from committed evidence.

``make real-corpus`` needs the network and 302 MB of someone else's files, so it cannot
be a merge gate. What it *measured* can be: ``tools/real_corpus.py`` writes one row per
corpus file plus the counts derived from those rows to ``docs/data/real-corpus/``, and
this module — which needs no network — checks three things on every ``make verify``:

1. **The evidence is internally honest.** The stored aggregates equal what
   :func:`tools.real_corpus.aggregate` derives from the rows *now*, so a hand-edited
   count cannot survive; provenance and invariants hold (679/679 proven, 0 contradictions,
   0 success-while-unidentified, 0 divergence).
2. **Every number the docs state is the evidence's number.** The report's Evidence
   table binds machine keys to values; the prose of the report, ADR 0012, the README,
   the changelog, and two source comments bind specific sentences by regex. A stated
   number that stops matching fails; so does a sentence that stops stating it
   (the same both-directions rule as ``tools/check_claims.py``).
3. **The "before" numbers are measured too.** ``…before-adr-0012.json`` holds what
   today's detectors found in the archive ledger ``dc70b05`` produced; §10 of the report
   and ADR 0012's before-column bind to it.

The *pattern* this guards against is the portfolio's dominant defect: a plausible number
in a document, detached from anything that produced it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from tools.real_corpus import CORPUS_COMMIT, DEFAULT_EVIDENCE, EVIDENCE_SCHEMA, aggregate

REPO_ROOT = Path(__file__).resolve().parent.parent
EVIDENCE_PATH = REPO_ROOT / DEFAULT_EVIDENCE
BEFORE_PATH = EVIDENCE_PATH.with_name(EVIDENCE_PATH.stem + ".before-adr-0012.json")
REPORT = REPO_ROOT / "docs" / "REAL-CORPUS-REPORT.md"
ADR = REPO_ROOT / "docs" / "adr" / "0012-the-premis-object-is-the-payload-within-a-record.md"
README = REPO_ROOT / "README.md"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

pytestmark = pytest.mark.preservation


@pytest.fixture(scope="module")
def evidence() -> dict[str, Any]:
    return json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def before() -> dict[str, Any]:
    return json.loads(BEFORE_PATH.read_text(encoding="utf-8"))


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _stated(path: Path, pattern: str) -> list[int]:
    """Every integer a document states in the sentence ``pattern`` captures.

    Fails the test when the sentence is gone: a regex that matches nothing is a check
    that verifies nothing, and nobody would learn the claim went unverified.
    """
    found = re.findall(pattern, _read(path))
    assert found, f"{path.relative_to(REPO_ROOT)} no longer states the sentence {pattern!r}"
    flat: list[str] = []
    for item in found:
        flat.extend(item if isinstance(item, tuple) else (item,))
    return [int(value.replace(",", "")) for value in flat]


# --- 1. the evidence is internally honest -------------------------------------------


def test_evidence_is_for_the_pinned_corpus(evidence: dict[str, Any]) -> None:
    assert evidence["schema"] == EVIDENCE_SCHEMA
    assert evidence["corpus"]["commit"] == CORPUS_COMMIT
    assert EVIDENCE_PATH.name == f"opf-format-corpus-{CORPUS_COMMIT[:8]}.json"
    assert "CC0" in evidence["corpus"]["licence"]


def test_stored_aggregates_are_what_the_rows_derive_to(evidence: dict[str, Any]) -> None:
    """A count edited by hand cannot match what the rows still say."""
    assert evidence["aggregates"] == aggregate(evidence["files"])


def test_rows_carry_provenance_and_no_content(evidence: dict[str, Any]) -> None:
    rows = evidence["files"]
    assert len(rows) == evidence["aggregates"]["files"]
    assert len({row["path"] for row in rows}) == len(rows), "one row per file"
    for row in rows:
        assert re.fullmatch(r"[0-9a-f]{40}", row["blob_sha1"]), row["path"]
        assert re.fullmatch(r"[0-9a-f]{64}", row["sha256"]), row["path"]
        assert set(row) == {
            "path",
            "blob_sha1",
            "sha256",
            "size",
            "format",
            "puid",
            "media_type",
            "basis",
            "at_risk",
            "unassessable",
            "header_offset",
            "obsolete",
        }, "a row carries metadata and hashes only"
    assert rows == sorted(rows, key=lambda row: row["path"]), "deterministic order"


def test_the_invariants_the_harness_fails_on_hold_in_the_evidence(evidence: dict[str, Any]) -> None:
    bags, premis, agg = evidence["bags"], evidence["premis"], evidence["aggregates"]
    assert bags["ingest_failures"] == [] and bags["invalid_bags"] == []
    assert bags["provenance_failures"] == []
    assert bags["payloads_proven"] == agg["files"], "every byte reached the pipeline"
    assert premis["format_events"] == agg["files"], "one identification event per file"
    assert premis["payloads_checked"] == agg["files"]
    assert premis["event_record_disagreements"] == 0
    assert premis["contradictions"] == 0
    assert premis["success_while_unidentified"] == 0
    assert premis["record_log_divergence"] == 0
    # The log and the rows agree on what was at risk and what was not assessable.
    assert premis["outcomes"]["at-risk"] == agg["at_risk"]
    assert premis["outcomes"]["unidentified"] == agg["unassessable"]
    assert premis["outcomes"]["empty"] == agg["by_basis"]["empty"]
    assert sum(premis["outcomes"].values()) == agg["files"]
    # Shared addresses are derived from the rows AND read off the records; they agree.
    assert premis["shared_addresses"] == agg["shared_addresses"]
    assert premis["payloads_on_shared_addresses"] == agg["payloads_on_shared_addresses"]
    assert len(premis["shared_address_groups"]) == premis["shared_addresses"]


def test_unassessable_means_unknown_and_nothing_else(evidence: dict[str, Any]) -> None:
    for row in evidence["files"]:
        assert row["unassessable"] == (row["basis"] == "unknown"), row["path"]
        if row["unassessable"]:
            assert not row["at_risk"], f"{row['path']}: unassessable is never at-risk"
        if row["basis"] == "empty":
            assert row["size"] == 0, row["path"]


# --- 2. every number the docs state is the evidence's number -------------------------


def _resolve(evidence: dict[str, Any], key: str) -> int:
    """Map an Evidence-table key to its value: ``basis:x``, ``bags:x``, ``premis:outcomes:x``…"""
    parts = key.split(":")
    if parts[0] == "basis":
        return int(evidence["aggregates"]["by_basis"][parts[1]])
    if parts[0] == "bags":
        return int(evidence["bags"][parts[1]])
    if parts[0] == "premis":
        node: Any = evidence["premis"]
        for part in parts[1:]:
            node = node[part]
        return int(node)
    return int(evidence["aggregates"][key])


_REQUIRED_TABLE_KEYS = frozenset(
    {
        "files",
        "bytes",
        "distinct_addresses",
        "basis:signature",
        "basis:extension",
        "basis:unknown",
        "basis:empty",
        "basis:signature-offset",
        "basis:text",
        "basis:xml-declaration",
        "at_risk",
        "unassessable",
        "displaced_headers",
        "obsolete",
        "obsolete_flagged",
        "shared_addresses",
        "payloads_on_shared_addresses",
        "bags:collections",
        "bags:bags_valid",
        "bags:payloads_proven",
        "premis:format_events",
        "premis:outcomes:success",
        "premis:outcomes:at-risk",
        "premis:outcomes:unidentified",
        "premis:outcomes:empty",
        "premis:success_while_unidentified",
        "premis:record_log_divergence",
        "premis:payloads_checked",
        "premis:event_record_disagreements",
        "premis:contradictions",
        "premis:name_dependent_verdict_groups",
    }
)


def test_the_reports_evidence_table_matches_the_evidence(evidence: dict[str, Any]) -> None:
    table = dict(re.findall(r"^\| `([a-z_:\-]+)` \| (\d+) \|$", _read(REPORT), flags=re.M))
    missing = _REQUIRED_TABLE_KEYS - set(table)
    assert not missing, f"rows deleted from the Evidence table: {sorted(missing)}"
    for key, stated in table.items():
        assert int(stated) == _resolve(evidence, key), f"`{key}` states {stated}"


def test_the_reports_prose_matches_the_evidence(evidence: dict[str, Any]) -> None:
    agg, premis = evidence["aggregates"], evidence["premis"]
    files = agg["files"]
    unknown = agg["by_basis"]["unknown"]
    report = _read(REPORT)

    assert _stated(REPORT, r"\*\*Sample\*\* \| (\d+) files, (\d+) MB") == [
        files,
        round(agg["bytes"] / 1e6),
    ]
    assert _stated(REPORT, r"\*\*(\d+)/(\d+) payload files byte-identical") == [
        evidence["bags"]["payloads_proven"],
        files,
    ]
    # The "now" column of the basis table, with the percentages it states.
    for basis, cell in re.findall(
        r"^\| \**`([a-z\-]+)`\** \| [^|]+ \| [^|]+ \| \**([\d.]+ \([\d.]+%\))\** \|$",
        report,
        flags=re.M,
    ):
        parsed = re.fullmatch(r"([\d.]+) \(([\d.]+)%\)", cell)
        assert parsed is not None, cell
        count, pct = parsed.groups()
        assert int(count) == agg["by_basis"][basis], basis
        assert float(pct) == round(agg["by_basis"][basis] / files * 100, 1), basis
    at_risk = re.search(r"\| flagged `at_risk` \| [^|]+ \| \*\*(\d+) \(([\d.]+)%\)\*\* \|", report)
    assert at_risk is not None, "the report no longer states the at-risk count"
    assert int(at_risk.group(1)) == agg["at_risk"]
    assert float(at_risk.group(2)) == round(agg["at_risk"] / files * 100, 1)
    assert _stated(REPORT, r"\| reported `unassessable` \| — \| (\d+) \(4\.9%\) \|") == [
        agg["unassessable"]
    ]
    assert round(unknown / files * 100, 1) == 4.9
    assert _stated(
        REPORT,
        r"recall over the 66 known-obsolete files\*\* \| \*\*0 / 66\*\* \| \*\*(\d+) / (\d+) \((\d+)%\)\*\*",
    ) == [
        agg["obsolete_flagged"],
        agg["obsolete"],
        round(agg["obsolete_flagged"] / agg["obsolete"] * 100),
    ]
    assert _stated(
        REPORT,
        r"payloads whose identification event is about \*that payload\* \(ADR 0012\) \| 0 / 679 \| \*\*(\d+) / (\d+)\*\*",
    ) == [
        premis["payloads_checked"] - premis["event_record_disagreements"],
        premis["payloads_checked"],
    ]
    assert _stated(
        REPORT, r"After, on the same (\d+) files: \*\*(\d+) / (\d+)\*\* payloads have exactly one"
    ) == [
        files,
        premis["payloads_checked"] - premis["event_record_disagreements"],
        premis["payloads_checked"],
    ]
    assert _stated(REPORT, r"The (\d+) shared addresses \((\d+) payloads\) are still there") == [
        premis["shared_addresses"],
        premis["payloads_on_shared_addresses"],
    ]
    assert _stated(REPORT, r"with\n\*\*(\d+)\*\* of them differing in verdict by name") == [
        premis["name_dependent_verdict_groups"]
    ]


def test_adr_0012_states_the_measured_after_column(evidence: dict[str, Any]) -> None:
    premis = evidence["premis"]
    assert _stated(ADR, r"about \*that payload\* \| 0 / 679 \| \*\*(\d+) / (\d+)\*\*") == [
        premis["payloads_checked"] - premis["event_record_disagreements"],
        premis["payloads_checked"],
    ]
    assert _stated(
        ADR, r"shared by more than one payload \| 16 \(70 payloads\) \| (\d+) \((\d+) payloads\)"
    ) == [
        premis["shared_addresses"],
        premis["payloads_on_shared_addresses"],
    ]
    assert _stated(ADR, r"verdict differs by payload name \| 1 \| (\d+) \|") == [
        premis["name_dependent_verdict_groups"]
    ]
    ext, files = evidence["aggregates"]["by_basis"]["extension"], evidence["aggregates"]["files"]
    assert f"names {ext} of {files} real files, {ext / files * 100:.1f}%" in _read(ADR)


def test_readme_and_changelog_state_the_evidence_numbers(evidence: dict[str, Any]) -> None:
    files = evidence["aggregates"]["files"]
    premis = evidence["premis"]
    assert _stated(README, r"ingest path over (\d+) real files") == [files]
    assert _stated(
        README,
        r"confident success over material the pipeline had entirely\nfailed to understand — 156 of (\d+) files",
    ) == [files]
    assert _stated(CHANGELOG, r"After: \*\*(\d+) of (\d+)\*\*, 0,\n  and 0\.") == [
        premis["payloads_checked"] - premis["event_record_disagreements"],
        premis["payloads_checked"],
    ]
    assert _stated(
        CHANGELOG, r"The (\d+) content addresses the\n  corpus shares across (\d+) payloads"
    ) == [
        premis["shared_addresses"],
        premis["payloads_on_shared_addresses"],
    ]
    assert _stated(CHANGELOG, r"verdict differs by name \((\d+)\)") == [
        premis["name_dependent_verdict_groups"]
    ]


def test_source_comments_state_the_measured_unidentified_share(evidence: dict[str, Any]) -> None:
    """Two comments quote the share; they are bound like any other stated number."""
    agg = evidence["aggregates"]
    share = f"{round(agg['unassessable'] / agg['files'] * 100, 1)}%"
    for rel in ("src/ledger/ingest.py", "src/ledger/cli.py"):
        text = _read(REPO_ROOT / rel)
        quoted = re.findall(r"(\d+\.\d)% of files|unidentified share to (\d+\.\d)%", text)
        stated = {a or b for a, b in quoted}
        assert stated == {share.rstrip("%")}, f"{rel} states {stated}, measured {share}"


# --- 3. the before-numbers are measured too --------------------------------------------


def test_before_evidence_is_measured_on_the_commit_before_148(before: dict[str, Any]) -> None:
    assert before["measured_on"]["ledger_commit"] == "dc70b05"
    assert before["corpus"]["commit"] == CORPUS_COMMIT
    premis = before["premis"]
    assert premis["typed_payload_events"] == 0, "nothing was keyed by payload before ADR 0012"
    assert premis["payloads_with_exactly_one_event_about_them"] == 0
    assert premis["contradictions"] == len(premis["contradiction_detail"]) == 1
    (contradiction,) = premis["contradiction_detail"]
    assert contradiction["object_type"] == "content-address"
    assert contradiction["events"] == 4
    assert len(contradiction["verdicts"]) == 2
    assert premis["events_on_shared_identifiers"] > premis["identifiers_with_more_than_one_event"]
    assert (
        premis["distinct_linking_object_identifiers"]
        + (premis["events_on_shared_identifiers"] - premis["identifiers_with_more_than_one_event"])
        == premis["format_events"]
    ), "every event was linked to exactly one identifier"


def test_the_before_column_in_the_docs_is_the_before_evidence(
    before: dict[str, Any], evidence: dict[str, Any]
) -> None:
    premis = before["premis"]
    shared_ids = premis["identifiers_with_more_than_one_event"]
    events_on = premis["events_on_shared_identifiers"]
    distinct = premis["distinct_linking_object_identifiers"]
    assert _stated(REPORT, r"linked to \*\*(\d+)\*\* distinct identifiers") == [distinct]
    assert _stated(
        REPORT, r"\*\*(\d+)\*\* identifiers carried more than one event \((\d+) events"
    ) == [
        shared_ids,
        events_on,
    ]
    assert _stated(
        REPORT,
        r"\| object identifiers carrying more than one event \| (\d+) \((\d+) events\) \| \*\*0\*\* \|",
    ) == [
        shared_ids,
        events_on,
    ]
    assert _stated(ADR, r"format-identification events \| (\d+) \|") == [premis["format_events"]]
    assert _stated(
        ADR, r"distinct `linkingObjectIdentifier` values they were linked to \| (\d+) \|"
    ) == [distinct]
    assert _stated(
        ADR, r"identifiers carrying more than one event \| \*\*(\d+)\*\*, carrying (\d+) events"
    ) == [
        shared_ids,
        events_on,
    ]
    assert _stated(
        ADR, r"identifiers carrying more than one event \| (\d+) \((\d+) events\) \| \*\*0\*\*"
    ) == [
        shared_ids,
        events_on,
    ]
    assert _stated(
        CHANGELOG,
        r"(\d+)\n  events on (\d+) identifiers, (\d+) identifiers carrying (\d+) events, (\d+) carrying two verdicts",
    ) == [
        premis["format_events"],
        distinct,
        shared_ids,
        events_on,
        premis["contradictions"],
    ]
    # The shared-address population is a property of the bytes: identical before and after.
    assert premis["shared_addresses"] == evidence["premis"]["shared_addresses"]
    assert (
        premis["payloads_on_shared_addresses"] == evidence["premis"]["payloads_on_shared_addresses"]
    )
