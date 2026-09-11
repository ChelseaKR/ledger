"""What the arrangement changes about what people are shown — and what it must not (#202).

`tests/test_arrangement_policy.py` proves the resolver and
`tests/test_arrangement_archive.py` proves the archive uses it. This file is
about the surfaces: browse, the container pages, EAD, OAI-PMH, the exports.

Two kinds of test, and the second is the reason the file exists.

* **It works.** A collection facet that composes with search, a container page
  that lists what is filed in it, nested EAD, OAI sets.
* **It is invisible to an outsider who is not entitled to it.** #188's lane
  found, by rereading its own diff, that it had put a whole-archive verdict on
  an anonymous page that told a stranger hidden records existed. The rule it
  wrote down is mechanical: *for every surface a non-steward is shown, ask
  whether it is computed over a larger set of records than that viewer can
  list.* Here the answer has to be no on every route, so the anonymous bodies
  are asserted **byte-identical** across two archives that differ only in the
  hidden property — the same shape of test #188 shipped, for this axis.
"""

from __future__ import annotations

import re
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import closing
from http.server import HTTPServer
from pathlib import Path

import pytest

from ledger.access.grants import anonymous, community_member, steward
from ledger.config import Config
from ledger.export import records_csv
from ledger.ingest import Archive
from ledger.metadata.dublincore import is_part_of
from ledger.metadata.ead import to_ead_xml
from ledger.metadata.mets import to_mets_xml
from ledger.models import (
    AccessPolicy,
    ArchivalContainer,
    ContainerLevel,
    DublinCore,
    Field,
    Record,
)
from ledger.oai import oai_response
from ledger.server import make_server

pytestmark = pytest.mark.disclosure

_NOW = "2026-06-16T00:00:00Z"

#: Every anonymous surface the arrangement could reach. Deliberately a superset
#: of what this feature touches: a leak that shows up on `/overview` because a
#: count moved is still a leak, and a route this list forgets is a route the
#: differential below cannot see.
_ANONYMOUS_ROUTES: tuple[str, ...] = (
    "/",
    "/search?q=flyer",
    "/collections",
    "/collection/casa-abierta",
    "/collection/casa-abierta/ead.xml",
    "/collection/flyers",
    "/collection/nope",
    "/overview",
    "/places",
    "/timeline",
    "/status",
    "/healthz",
    "/proof",
    "/feed.atom",
    "/sitemap.xml",
    "/api/records",
    "/api/search?q=",
    "/api/search.csv?q=",
    "/oai?verb=ListSets",
    "/oai?verb=ListIdentifiers&metadataPrefix=oai_dc",
    "/oai?verb=ListRecords&metadataPrefix=oai_dc",
    "/record/rec-flyer",
)


def _collection(
    cid: str,
    title: str,
    *,
    level: ContainerLevel = ContainerLevel.COLLECTION,
    parent: str | None = None,
    policy: AccessPolicy = AccessPolicy.PUBLIC,
    records_policy: AccessPolicy = AccessPolicy.PUBLIC,
    scope: str = "",
    extent: str = "",
    dates: str = "",
) -> ArchivalContainer:
    return ArchivalContainer(
        container_id=cid,
        title=title,
        level=level,
        parent_id=parent,
        scope_and_content=scope,
        extent=extent,
        dates=dates,
        policy=policy,
        records_policy=records_policy,
        created_at=_NOW,
    )


def _record(rid: str, title: str, *, placement: str | None = None, policy: AccessPolicy) -> Record:
    return Record(
        title=title,
        record_id=rid,
        default_policy=policy,
        dublin_core=DublinCore(title=[title], subject=["housing"], date=["1991"]),
        fields=[Field(name="story", value="We papered the block.", policy=policy)],
        created_at=_NOW,
        placement=placement,
    )


def _archive(root: Path) -> Archive:
    return Archive.init(Config.default("Casa Abierta Archive", root))


def _arranged(tmp_path: Path) -> Archive:
    """A public collection, a public series in it, and one public flyer filed there."""
    archive = _archive(tmp_path / "store")
    archive.describe_container(
        _collection(
            "casa-abierta",
            "Casa Abierta deposit",
            scope="Four boxes left with us after the March raid.",
            extent="4 boxes",
            dates="1987-1994",
        ),
        now=_NOW,
    )
    archive.describe_container(
        _collection("flyers", "Flyers", level=ContainerLevel.SERIES, parent="casa-abierta"),
        now=_NOW,
    )
    payload = tmp_path / "flyer.txt"
    payload.write_text("flyer", encoding="utf-8")
    archive.ingest(
        {"flyer.txt": payload},
        _record("rec-flyer", "Rent strike flyer", placement="flyers", policy=AccessPolicy.PUBLIC),
        now=_NOW,
    )
    return archive


# --- browse, facets, container pages ----------------------------------------


def test_the_collection_facet_composes_with_search_and_narrows_to_the_series(
    tmp_path: Path,
) -> None:
    archive = _arranged(tmp_path)
    from ledger import search

    records = archive.browse(anonymous(), now=_NOW)
    facets = search.facet_by_collection(records)
    assert [(f.value, f.label, f.count) for f in facets] == [
        ("casa-abierta", "Casa Abierta deposit", 1),
        ("flyers", "Flyers", 1),
    ]
    # Selecting the collection picks up what is filed in its series; selecting
    # the series picks up only the series.
    assert [r.record_id for r in search.filter_by_facet(records, "collection", "casa-abierta")] == [
        "rec-flyer"
    ]
    assert [r.record_id for r in search.filter_by_facet(records, "collection", "flyers")] == [
        "rec-flyer"
    ]
    assert search.filter_by_facet(records, "collection", "nope") == []


def test_a_series_inherits_the_collections_note_on_its_own_page(tmp_path: Path) -> None:
    from ledger.render import collection_main_html

    archive = _arranged(tmp_path)
    series = archive.disclose_container("flyers", anonymous(), now=_NOW)
    html = collection_main_html(series, [], [], lang="en")
    assert "Four boxes left with us after the March raid." in html
    assert "Inherited from the collection above." in html
    assert "Casa Abierta deposit</a>" in html  # the breadcrumb


def test_a_container_page_says_the_same_thing_when_empty_and_when_withheld(
    tmp_path: Path,
) -> None:
    """The sentence that carries the safety property, asserted as one sentence.

    A public collection that genuinely holds nothing and a public collection
    whose every record is sealed must render the *same* page for an outsider.
    Two sentences here — "nothing here" versus "3 records withheld" — would be
    exactly the aggregation oracle a container's own policy exists to prevent.
    """
    from ledger.render import collection_main_html

    empty = _archive(tmp_path / "empty")
    empty.describe_container(_collection("casa-abierta", "Casa Abierta deposit"), now=_NOW)

    withheld = _archive(tmp_path / "withheld")
    withheld.describe_container(_collection("casa-abierta", "Casa Abierta deposit"), now=_NOW)
    payload = tmp_path / "sealed.txt"
    payload.write_text("names", encoding="utf-8")
    withheld.ingest(
        {"sealed.txt": payload},
        _record(
            "rec-sealed",
            "The safehouse list",
            placement="casa-abierta",
            policy=AccessPolicy.STEWARDS,
        ),
        now=_NOW,
    )

    pages = []
    inputs = []
    for archive in (empty, withheld):
        container = archive.disclose_container("casa-abierta", anonymous(), now=_NOW)
        records = [
            r
            for r in archive.browse(anonymous(), now=_NOW)
            if any(s.container_id == "casa-abierta" for s in r.placement)
        ]
        inputs.append((container, records))
        pages.append(collection_main_html(container, records, [], lang="en"))
    assert pages[0] == pages[1]
    assert "No records here are available to you." in pages[0]
    # The renderer's *inputs* are identical too, which is what makes the identity
    # above structural rather than a coincidence of wording: the page is never
    # handed the withheld records or a count of them, so there is nothing on the
    # page for a future edit to start printing. (The end-to-end version of this,
    # over a live server and every anonymous route, is
    # `test_a_community_only_ceiling_changes_no_anonymous_surface`.)
    assert inputs[0] == inputs[1]
    assert inputs[1][1] == []

    # And a steward does see the record, so the page is not simply always empty.
    container = withheld.disclose_container("casa-abierta", steward("s"), now=_NOW)
    seen = withheld.browse(steward("s"), now=_NOW)
    steward_page = collection_main_html(container, seen, [], lang="en")
    assert "The safehouse list" in steward_page


# --- EAD ---------------------------------------------------------------------


def test_ead_nests_the_collection_the_series_and_the_item(tmp_path: Path) -> None:
    archive = _arranged(tmp_path)
    records = archive.browse(anonymous(), now=_NOW)
    containers = archive.browse_containers(anonymous(), now=_NOW)
    xml = to_ead_xml(
        "Casa Abierta Archive",
        records,
        created="2026-06-16",
        collection_id="casa-abierta-archive",
        arrangement=containers,
    )
    assert '<c01 level="collection" id="c-casa-abierta">' in xml
    assert '<c02 level="series" id="c-flyers">' in xml
    assert '<c03 level="item" id="c-rec-flyer">' in xml
    assert xml.index("<c01") < xml.index("<c02") < xml.index("<c03")
    assert "<scopecontent><p>Four boxes left with us after the March raid.</p>" in xml
    # Parseable, and the item really is inside the series inside the collection.
    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml)  # noqa: S314 - output this test just produced
    ns = "{urn:isbn:1-931666-22-9}"
    c01 = root.find(f".//{ns}dsc/{ns}c01")
    assert c01 is not None
    c02 = c01.find(f"{ns}c02")
    assert c02 is not None
    assert c02.find(f"{ns}c03") is not None


def test_ead_with_no_arrangement_is_byte_identical_to_the_flat_form(tmp_path: Path) -> None:
    """The compatibility property every existing caller depends on."""
    archive = _arranged(tmp_path)
    records = archive.browse(anonymous(), now=_NOW)
    flat = to_ead_xml("A", records, created="2026-06-16", collection_id="a")
    assert '<c01 level="item"' in flat
    assert 'level="series"' not in flat
    assert flat == to_ead_xml("A", records, created="2026-06-16", collection_id="a", arrangement=())


def test_ead_files_a_record_under_the_deepest_container_this_document_renders(
    tmp_path: Path,
) -> None:
    """A container left out of the document does not take its records with it.

    Two cases, and both matter because losing a record from a finding aid is the
    worse failure by far:

    * the series is omitted but the collection is not — the item moves *up* to
      the collection rather than disappearing;
    * nothing in its chain is rendered — the item is a top-level component,
      exactly as an unarranged one is.
    """
    archive = _arranged(tmp_path)
    records = archive.browse(anonymous(), now=_NOW)
    containers = archive.browse_containers(anonymous(), now=_NOW)

    without_series = to_ead_xml(
        "A",
        records,
        created="2026-06-16",
        collection_id="a",
        arrangement=[c for c in containers if c.container_id != "flyers"],
    )
    assert '<c01 level="collection" id="c-casa-abierta">' in without_series
    assert '<c02 level="item" id="c-rec-flyer">' in without_series
    assert "c-flyers" not in without_series

    without_any = to_ead_xml("A", records, created="2026-06-16", collection_id="a")
    assert '<c01 level="item" id="c-rec-flyer">' in without_any


# --- OAI-PMH -----------------------------------------------------------------


def _oai(archive: Archive, verb: str, **params: str) -> str:
    grant = anonymous()
    status, xml = oai_response(
        verb,
        {"verb": verb, **params},
        records=archive.browse(grant, now=_NOW),
        containers=archive.browse_containers(grant, now=_NOW),
        archive_name="Casa Abierta Archive",
        base_url="http://h/oai",
        admin_email="a@example.org",
        now=_NOW,
    )
    assert status == 200
    return xml


def test_list_sets_names_every_visible_container_hierarchically(tmp_path: Path) -> None:
    xml = _oai(_arranged(tmp_path), "ListSets")
    assert "<setSpec>casa-abierta</setSpec>" in xml
    assert "<setSpec>casa-abierta:flyers</setSpec>" in xml
    assert "<setName>Flyers</setName>" in xml
    assert "<dc:description>Four boxes left with us after the March raid.</dc:description>" in xml


def test_a_record_header_carries_one_setspec_per_visible_level(tmp_path: Path) -> None:
    xml = _oai(_arranged(tmp_path), "ListIdentifiers", metadataPrefix="oai_dc")
    assert xml.count("<setSpec>casa-abierta</setSpec>") == 1
    assert xml.count("<setSpec>casa-abierta:flyers</setSpec>") == 1


def test_set_filtering_narrows_and_an_unknown_set_is_the_same_as_an_empty_one(
    tmp_path: Path,
) -> None:
    archive = _arranged(tmp_path)
    inside = _oai(archive, "ListRecords", metadataPrefix="oai_dc", set="casa-abierta")
    assert "rec-flyer" in inside

    unknown = _oai(archive, "ListRecords", metadataPrefix="oai_dc", set="no-such-set")
    assert 'code="noRecordsMatch"' in unknown
    # An existing-but-empty set answers identically, so probing set names tells
    # a harvester nothing about which collections are real.
    archive.describe_container(_collection("empty-one", "An empty collection"), now=_NOW)
    empty = _oai(archive, "ListRecords", metadataPrefix="oai_dc", set="empty-one")
    assert empty.replace("empty-one", "no-such-set") == unknown


def test_an_archive_with_no_visible_sets_says_no_set_hierarchy(tmp_path: Path) -> None:
    """And so does one whose every collection is sealed from the harvester.

    Two different reasons, one answer. A distinct reply for "there are sets but
    not for you" would tell a harvester that hidden collections exist.
    """
    bare = _archive(tmp_path / "bare")
    hidden = _archive(tmp_path / "hidden")
    hidden.describe_container(
        _collection("secret", "2019 raid testimony", policy=AccessPolicy.STEWARDS), now=_NOW
    )
    assert _oai(bare, "ListSets") == _oai(hidden, "ListSets")
    assert 'code="noSetHierarchy"' in _oai(bare, "ListSets")


# --- exports -----------------------------------------------------------------


def test_the_csv_names_the_deepest_visible_container(tmp_path: Path) -> None:
    archive = _arranged(tmp_path)
    csv = records_csv(archive.browse(anonymous(), now=_NOW), base_url="https://h")
    assert csv.splitlines()[0].endswith(",collection")
    assert csv.splitlines()[1].endswith(",Flyers")


def test_mets_carries_is_part_of_and_a_logical_struct_map(tmp_path: Path) -> None:
    archive = _arranged(tmp_path)
    record = archive.disclose("rec-flyer", anonymous(), now=_NOW)
    assert is_part_of(record, base_url="https://h") == [
        "https://h/collection/casa-abierta",
        "https://h/collection/flyers",
    ]
    xml = to_mets_xml(record, created=_NOW, base_url="https://h")
    assert "<dc:relation>https://h/collection/casa-abierta</dc:relation>" in xml
    assert '<mets:structMap TYPE="logical">' in xml
    assert '<mets:div TYPE="series" LABEL="Flyers" ID="c-flyers">' in xml
    assert '<mets:structMap TYPE="physical">' in xml


def test_an_unarranged_record_exports_exactly_as_it_did_before(tmp_path: Path) -> None:
    archive = _archive(tmp_path / "store")
    payload = tmp_path / "flyer.txt"
    payload.write_text("flyer", encoding="utf-8")
    archive.ingest(
        {"flyer.txt": payload},
        _record("rec-flyer", "Rent strike flyer", policy=AccessPolicy.PUBLIC),
        now=_NOW,
    )
    record = archive.disclose("rec-flyer", anonymous(), now=_NOW)
    xml = to_mets_xml(record, created=_NOW, base_url="https://h")
    assert "<dc:relation>" not in xml
    assert 'TYPE="logical"' not in xml
    assert is_part_of(record, base_url="https://h") == []


# --- the differential: is any of this visible to an outsider? ----------------


def _serve(archive: Archive) -> Iterator[int]:
    httpd: HTTPServer = make_server(archive, host="127.0.0.1", port=0)
    port = int(httpd.server_address[1])
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _fetch(port: int, route: str) -> tuple[int, str]:
    request = urllib.request.Request(f"http://127.0.0.1:{port}{route}")
    try:
        with closing(urllib.request.urlopen(request)) as response:  # noqa: S310 - loopback
            return int(response.status), response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8", "replace")


def _normalize(text: str, ports: tuple[int, int]) -> str:
    for port in ports:
        text = text.replace(str(port), "PORT")
    return re.sub(r"\d{4}-\d{2}-\d{2}T[\d:.]+(?:Z|[+-]\d{2}:\d{2})?", "TIMESTAMP", text)


def _differing(a: Archive, b: Archive) -> list[str]:
    """Every anonymous route on which the two archives answer differently."""
    server_a, server_b = _serve(a), _serve(b)
    port_a, port_b = next(server_a), next(server_b)
    try:
        out: list[str] = []
        for route in _ANONYMOUS_ROUTES:
            status_a, body_a = _fetch(port_a, route)
            status_b, body_b = _fetch(port_b, route)
            same = status_a == status_b and _normalize(body_a, (port_a, port_b)) == _normalize(
                body_b, (port_a, port_b)
            )
            if not same:
                out.append(route)
        return out
    finally:
        for server in (server_a, server_b):
            with pytest.raises(StopIteration):
                next(server)


def _hidden_arrangement(tmp_path: Path) -> Archive:
    """The same public flyer, filed in a collection an outsider may not know of.

    The container's own description is steward-only; its ceiling over records is
    public. So the flyer is public and the collection is not — which is exactly
    #202's second "decide first" item: "2019 raid testimony, deposited by Casa
    Abierta" outs by aggregation even when what it holds does not.
    """
    archive = _archive(tmp_path / "store")
    archive.describe_container(
        _collection(
            "casa-abierta",
            "2019 raid testimony, deposited by Casa Abierta",
            policy=AccessPolicy.STEWARDS,
            records_policy=AccessPolicy.PUBLIC,
            scope="Deposited by Casa Abierta after the March raid.",
        ),
        now=_NOW,
    )
    payload = tmp_path / "flyer.txt"
    payload.write_text("flyer", encoding="utf-8")
    archive.ingest(
        {"flyer.txt": payload},
        _record(
            "rec-flyer", "Rent strike flyer", placement="casa-abierta", policy=AccessPolicy.PUBLIC
        ),
        now=_NOW,
    )
    return archive


def _unarranged(tmp_path: Path) -> Archive:
    """The same public flyer, filed nowhere at all."""
    archive = _archive(tmp_path / "store")
    payload = tmp_path / "flyer.txt"
    payload.write_text("flyer", encoding="utf-8")
    archive.ingest(
        {"flyer.txt": payload},
        _record("rec-flyer", "Rent strike flyer", policy=AccessPolicy.PUBLIC),
        now=_NOW,
    )
    return archive


def test_a_hidden_container_changes_no_anonymous_surface_at_all(tmp_path: Path) -> None:
    """The #188 test, for this axis: byte-identical anonymous bodies.

    Two archives holding the *same public record*. In one it is filed in a
    collection whose title would out its depositor; in the other it is filed
    nowhere. An outsider who can tell the two apart on any surface has learned
    that the collection exists.

    Normalized only for the ephemeral port and for timestamps, exactly as
    `tests/test_sealed_existence_leaks.py` normalizes.
    """
    hidden = _hidden_arrangement(tmp_path / "hidden")
    plain = _unarranged(tmp_path / "plain")
    assert _differing(hidden, plain) == []


def test_a_steward_does_see_the_difference_so_the_test_above_is_not_vacuous(
    tmp_path: Path,
) -> None:
    """The floor under the differential: the hidden thing is really there.

    Without this, an archive where `describe_container` silently did nothing
    would pass the test above perfectly.
    """
    hidden = _hidden_arrangement(tmp_path / "hidden")
    as_steward = hidden.disclose("rec-flyer", steward("s"), now=_NOW)
    as_anon = hidden.disclose("rec-flyer", anonymous(), now=_NOW)
    assert [s.container_id for s in as_steward.placement] == ["casa-abierta"]
    assert as_anon.placement == ()
    assert "Casa Abierta" in as_steward.placement[0].title
    assert [c.container_id for c in hidden.browse_containers(steward("s"), now=_NOW)] == [
        "casa-abierta"
    ]
    assert hidden.browse_containers(anonymous(), now=_NOW) == []


def test_a_community_only_ceiling_changes_no_anonymous_surface(tmp_path: Path) -> None:
    """The other direction: the container is public, the records under it are not.

    An archive whose collection holds three community-only records must look,
    to an outsider, exactly like one whose collection holds nothing. This is
    "empty versus withheld" asserted over every route rather than over one
    sentence.
    """
    with_records = _archive(tmp_path / "with" / "store")
    without = _archive(tmp_path / "without" / "store")
    payload = tmp_path / "flyer.txt"
    payload.write_text("flyer", encoding="utf-8")
    for archive in (with_records, without):
        archive.describe_container(
            _collection(
                "casa-abierta",
                "Casa Abierta deposit",
                records_policy=AccessPolicy.COMMUNITY,
                scope="Four boxes.",
            ),
            now=_NOW,
        )
        # Both archives hold one ordinary public record. Without it the
        # comparison would be "three hidden records" against "no records at
        # all", and `/status` tells those two apart on `main` already — see
        # `test_status_already_distinguishes_an_empty_archive_from_a_hidden_one`
        # below. That is a pre-existing oracle and the owner's to decide; this
        # test is about whether *arrangement* adds one.
        archive.ingest(
            {"flyer.txt": payload},
            _record("rec-open", "An open flyer", policy=AccessPolicy.PUBLIC),
            now=_NOW,
        )
    for index in range(3):
        with_records.ingest(
            {"flyer.txt": payload},
            _record(
                f"rec-{index}",
                f"Flyer {index}",
                placement="casa-abierta",
                policy=AccessPolicy.PUBLIC,
            ),
            now=_NOW,
        )
    assert _differing(with_records, without) == []
    # And they are really there, for someone entitled to them.
    assert len(with_records.browse(community_member("m"), now=_NOW)) == 4
    assert len(without.browse(community_member("m"), now=_NOW)) == 1


def test_a_hidden_container_page_answers_exactly_as_an_absent_one(tmp_path: Path) -> None:
    """Probing container ids over HTTP establishes nothing."""
    archive = _hidden_arrangement(tmp_path)
    server = _serve(archive)
    port = next(server)
    try:
        hidden_status, hidden_body = _fetch(port, "/collection/casa-abierta")
        absent_status, absent_body = _fetch(port, "/collection/definitely-not-here")
        hidden_ead, _ = _fetch(port, "/collection/casa-abierta/ead.xml")
    finally:
        with pytest.raises(StopIteration):
            next(server)
    assert hidden_status == absent_status == 404
    assert hidden_ead == 404
    # The 404 page echoes the requested path in its own language-switch links,
    # so the two bodies differ by the id the caller typed and by nothing else.
    # Substituting it back is what makes the comparison about the archive's
    # answer rather than about the request.
    assert hidden_body.replace("casa-abierta", "ID") == absent_body.replace(
        "definitely-not-here", "ID"
    )
    assert "Casa Abierta" not in hidden_body
    assert "March raid" not in hidden_body


def test_status_already_distinguishes_an_empty_archive_from_a_hidden_one(
    tmp_path: Path,
) -> None:
    """A pre-existing oracle on `main`, measured here and deliberately NOT fixed.

    `/status`'s anonymous headline is derived from a fixity sweep over **every**
    bag, not over the records the caller may list. So an archive holding
    nothing says *"This archive holds nothing yet."* and an archive whose
    records are all invisible to this reader says *"Everything is healthy."*
    That is the emptiness oracle `/healthz`'s docstring spends a paragraph
    refusing, on the neighbouring route, and it arrived with #218. It is
    recorded in `_drain-2026-09-06/OWNER-DECISIONS.md` §H item 4 as the owner's
    call, because closing it means either gating `/status`'s headlines to
    stewards or relaxing `/healthz`'s reasoning to match — a published-contract
    decision either way.

    #202 does not create it and does not widen what it distinguishes: the same
    two sentences, for the same reason (is there anything in the store?). What
    it adds is one more way for a record to be invisible while still counting
    toward "not empty" — a container ceiling, alongside a sealed record.

    This test exists so that fact is written down and pinned, rather than
    surfacing as an unexplained exclusion in the differentials above. If the
    owner closes the oracle, this test is what will go red and say so.
    """
    empty = _archive(tmp_path / "empty")
    hidden = _archive(tmp_path / "hidden")
    hidden.describe_container(
        _collection("casa-abierta", "Casa Abierta deposit", records_policy=AccessPolicy.STEWARDS),
        now=_NOW,
    )
    payload = tmp_path / "flyer.txt"
    payload.write_text("flyer", encoding="utf-8")
    hidden.ingest(
        {"flyer.txt": payload},
        _record(
            "rec-flyer", "Rent strike flyer", placement="casa-abierta", policy=AccessPolicy.PUBLIC
        ),
        now=_NOW,
    )
    assert hidden.browse(anonymous(), now=_NOW) == []
    assert empty.browse(anonymous(), now=_NOW) == []

    server_empty, server_hidden = _serve(empty), _serve(hidden)
    port_empty, port_hidden = next(server_empty), next(server_hidden)
    try:
        _, empty_body = _fetch(port_empty, "/status")
        _, hidden_body = _fetch(port_hidden, "/status")
    finally:
        for server in (server_empty, server_hidden):
            with pytest.raises(StopIteration):
                next(server)

    assert "This archive holds nothing yet." in empty_body
    assert "Everything is healthy." in hidden_body
    # Whatever else it says, it still publishes no count to an outsider.
    assert "1 of 1" not in hidden_body


def test_a_record_with_no_visible_placement_renders_no_breadcrumb_at_all(
    tmp_path: Path,
) -> None:
    """Not an empty "Part of:" line — no line.

    An empty label would tell a reader that the record is filed *somewhere*
    they may not see, which is the sentence the whole gating exists to avoid.
    Asserted at the render layer as well as at `disclose`, because the two are
    separately editable and only one of them is the safety boundary.
    """
    from ledger.render import _record_main_html

    hidden = _hidden_arrangement(tmp_path / "hidden")
    plain = _unarranged(tmp_path / "plain")
    for archive in (hidden, plain):
        record = archive.disclose("rec-flyer", anonymous(), now=_NOW)
        assert record.placement == ()
        html = _record_main_html(record, proceed=True, lang="en")
        assert "Part of" not in html
        assert "breadcrumb" not in html
        assert "Casa Abierta" not in html

    # And a steward, who may describe the container, does get one — so the
    # assertions above are about gating and not about a breadcrumb nobody
    # renders.
    as_steward = hidden.disclose("rec-flyer", steward("s"), now=_NOW)
    steward_html = _record_main_html(as_steward, proceed=True, lang="en")
    assert "Part of" in steward_html
    assert "Casa Abierta" in steward_html
