"""The route tables, and the dispatch that reads them (#83).

``do_GET`` and ``do_POST`` were one 35-branch and one 12-branch ``if/elif``
chain, each carrying a C901 complexity waiver that #83 tracked as a split to be
made carefully rather than same-day: this is the disclosure/no-outing choke
point, and every public read passes through it.

The split is to data. That removes the complexity, but the property that
actually matters is that it changed *nothing else*: the same path must reach the
same handler with the same arguments as before, and the routes that are tried in
order must still be tried in that order, because ``/record/{id}`` is a catch-all
that would otherwise swallow ``/record/{id}/history``.

Two kinds of check here, and both are needed.

* **The tables, as data.** Every path is pinned to the handler the chain called,
  written out in full rather than derived, so a table edit that silently
  re-points a route fails here rather than in production.
* **The dispatch, over a live server.** The tables are replaced with recorders
  and the real routes are requested over loopback, so what is asserted is where
  a request actually lands and with which arguments -- not what a table says it
  should. A pinned table that nothing dispatches through would pass the first
  set of checks and serve nothing.
"""

from __future__ import annotations

import threading
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import redirect_stderr, redirect_stdout
from http.server import HTTPServer
from io import StringIO
from pathlib import Path
from typing import Any

import pytest

from ledger.config import Config
from ledger.ingest import Archive
from ledger.server import ArchiveRequestHandler, make_server

# ----------------------------------------------------------------------------------
# The tables, as data
# ----------------------------------------------------------------------------------

#: Exact GET paths served by a handler that reads nothing but the request, and the
#: handler each one reached in the `if/elif` chain this replaced.
_EXPECTED_GET_PAGES: dict[str, str] = {
    "/healthz": "_handle_healthz",
    "/status": "_handle_status",
    "/about": "_handle_about",
    "/overview": "_handle_overview",
    "/places": "_handle_places",
    "/timeline": "_handle_timeline",
    "/governance": "_handle_governance",
    "/how-it-works": "_handle_how_it_works",
    "/proof": "_handle_proof",
    "/proof/attestation.json": "_handle_proof_attestation",
    "/transparency": "_handle_transparency",
    "/sitemap.xml": "_handle_sitemap",
    "/robots.txt": "_handle_robots",
    "/feed.atom": "_handle_feed",
    "/steward": "_handle_steward_console",
    "/steward/audit": "_handle_steward_audit",
    "/contribute": "_handle_contribute_form",
    "/withdraw": "_handle_withdraw_form",
    "/edit": "_handle_edit_form",
    "/api/records": "_handle_api_records",
    "/collections": "_handle_collections",
}

#: Exact GET paths whose handler also reads the query string.
_EXPECTED_GET_QUERY_PAGES: dict[str, str] = {
    "/": "_handle_browse",
    "/search": "_handle_search",
    "/consent-status": "_handle_consent_status",
    "/oai": "_handle_oai",
    "/api/search": "_handle_api_search",
    "/api/search.csv": "_handle_api_search_csv",
}

#: ``/record/{id}{suffix}`` GET routes taking only the identifier, in order.
_EXPECTED_RECORD_SUBROUTES: tuple[tuple[str, str], ...] = (
    ("/consent", "_handle_consent_form"),
    ("/object", "_handle_object_form"),
)

#: Exact POST paths.
_EXPECTED_POST_ACTIONS: dict[str, str] = {
    "/contribute": "_post_contribute",
    "/withdraw": "_post_withdraw",
    "/edit": "_post_edit",
    "/steward/submissions/withhold": "_post_bulk_withhold",
}

#: ``{prefix}{id}{suffix}`` POST routes, in the order they are tried.
_EXPECTED_POST_ID_ACTIONS: tuple[tuple[str, str, str], ...] = (
    ("/record/", "/consent", "_post_consent"),
    ("/record/", "/object", "_post_object"),
    ("/steward/requests/", "/resolve", "_post_resolve_request"),
    ("/steward/records/", "/warn", "_post_steward_warn"),
    ("/steward/records/", "/takedown", "_post_steward_takedown"),
    ("/steward/submissions/", "/review", "_post_review_submission"),
)


def test_the_exact_get_tables_map_every_path_to_the_handler_it_reached_before() -> None:
    """A re-pointed route is a disclosure change; it fails here, not in production."""
    assert {
        path: fn.__name__ for path, fn in ArchiveRequestHandler._GET_PAGES.items()
    } == _EXPECTED_GET_PAGES
    assert {
        path: fn.__name__ for path, fn in ArchiveRequestHandler._GET_QUERY_PAGES.items()
    } == _EXPECTED_GET_QUERY_PAGES


def test_the_post_tables_map_every_path_to_the_handler_it_reached_before() -> None:
    """POSTs are the only writes the site accepts, so the same pinning applies."""
    assert {
        path: fn.__name__ for path, fn in ArchiveRequestHandler._POST_ACTIONS.items()
    } == _EXPECTED_POST_ACTIONS
    assert (
        tuple(
            (prefix, suffix, fn.__name__)
            for prefix, suffix, fn in ArchiveRequestHandler._POST_ID_ACTIONS
        )
        == _EXPECTED_POST_ID_ACTIONS
    )
    assert (
        tuple((suffix, fn.__name__) for suffix, fn in ArchiveRequestHandler._RECORD_SUBROUTES)
        == _EXPECTED_RECORD_SUBROUTES
    )


def test_no_exact_path_is_claimed_by_two_tables() -> None:
    """A path in both GET tables would be served by whichever is consulted first.

    The chain could not express that -- one `elif` per path -- and two mappings
    can. `do_GET` reads `_GET_PAGES` before `_GET_QUERY_PAGES`, so a duplicate
    would silently drop the query string from a handler that needs it.
    """
    overlap = set(ArchiveRequestHandler._GET_PAGES) & set(ArchiveRequestHandler._GET_QUERY_PAGES)
    assert not overlap, f"served by both GET tables: {sorted(overlap)}"


def test_the_bulk_withhold_path_is_not_shadowed_by_the_review_prefix_rule() -> None:
    """The one ordering the exact/prefixed split has to preserve on POST.

    ``/steward/submissions/withhold`` sat above
    ``/steward/submissions/{id}/review`` in the chain. It survives because the
    exact table is consulted before the prefixed one -- and because it does not
    end in ``/review``, which is the belt to that braces.
    """
    exact = "/steward/submissions/withhold"
    assert exact in ArchiveRequestHandler._POST_ACTIONS
    for prefix, suffix, _fn in ArchiveRequestHandler._POST_ID_ACTIONS:
        assert not (exact.startswith(prefix) and exact.endswith(suffix)), (
            f"{exact} also matches the prefixed rule {prefix}...{suffix}"
        )


# ----------------------------------------------------------------------------------
# The dispatch, over a live server
# ----------------------------------------------------------------------------------


@pytest.fixture
def routed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, list[str]]]:
    """A live server whose every route is a recorder; yields (base_url, recorded).

    Every table entry and every directly-called route handler is replaced with a
    stub that appends what it was called as and answers 204, so a request over
    loopback proves which handler the dispatcher reached and with which
    arguments. The archive underneath is empty on purpose: nothing real is
    invoked, and the test is about routing rather than about any page.
    """
    recorded: list[str] = []

    def _recorder(name: str) -> Callable[..., None]:
        def _stub(handler: Any, *args: object) -> None:
            recorded.append(":".join([name, *(str(a) for a in args if a != {})]))
            handler.send_response(204)
            handler.end_headers()

        return _stub

    monkeypatch.setattr(
        ArchiveRequestHandler,
        "_GET_PAGES",
        {path: _recorder(fn.__name__) for path, fn in ArchiveRequestHandler._GET_PAGES.items()},
    )
    monkeypatch.setattr(
        ArchiveRequestHandler,
        "_GET_QUERY_PAGES",
        {
            path: _recorder(fn.__name__)
            for path, fn in ArchiveRequestHandler._GET_QUERY_PAGES.items()
        },
    )
    monkeypatch.setattr(
        ArchiveRequestHandler,
        "_RECORD_SUBROUTES",
        tuple(
            (suffix, _recorder(fn.__name__))
            for suffix, fn in ArchiveRequestHandler._RECORD_SUBROUTES
        ),
    )
    monkeypatch.setattr(
        ArchiveRequestHandler,
        "_POST_ACTIONS",
        {path: _recorder(fn.__name__) for path, fn in ArchiveRequestHandler._POST_ACTIONS.items()},
    )
    monkeypatch.setattr(
        ArchiveRequestHandler,
        "_POST_ID_ACTIONS",
        tuple(
            (prefix, suffix, _recorder(fn.__name__))
            for prefix, suffix, fn in ArchiveRequestHandler._POST_ID_ACTIONS
        ),
    )
    for name in (
        "_handle_file",
        "_handle_record_history",
        "_handle_record",
        "_handle_api_record",
        "_handle_collection",
        "_handle_collection_ead",
        "_handle_static",
        "_handle_not_found",
    ):
        monkeypatch.setattr(ArchiveRequestHandler, name, _recorder(name))

    archive = Archive.init(Config.default("Route Table Archive", tmp_path / "arc"))
    httpd: HTTPServer = make_server(archive, host="127.0.0.1", port=0)
    host, port = httpd.server_address[0], httpd.server_address[1]
    host_s = host.decode("ascii") if isinstance(host, bytes | bytearray) else str(host)
    sink = StringIO()
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    with redirect_stderr(sink), redirect_stdout(sink):
        thread.start()
        try:
            yield f"http://{host_s}:{int(port)}", recorded
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()


def _request(base: str, path: str, *, method: str = "GET") -> int:
    """Drive one request over loopback and return its status."""
    request = urllib.request.Request(  # noqa: S310 - loopback URL this test constructed
        f"{base}{path}",
        method=method,
        data=b"" if method == "POST" else None,
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - loopback URL this test constructed
            return int(response.status)
    except urllib.error.HTTPError as failed:  # pragma: no cover - stubs answer 204
        return int(failed.code)


def test_every_exact_get_path_reaches_its_own_handler(
    routed: tuple[str, list[str]],
) -> None:
    """Requested over loopback, not asserted from the table that declares it."""
    base, recorded = routed
    expected = {**_EXPECTED_GET_PAGES, **_EXPECTED_GET_QUERY_PAGES}
    for path in expected:
        assert _request(base, path) == 204, path
    assert recorded == [expected[path] for path in expected]


def test_every_exact_post_path_reaches_its_own_handler(
    routed: tuple[str, list[str]],
) -> None:
    base, recorded = routed
    for path in _EXPECTED_POST_ACTIONS:
        assert _request(base, path, method="POST") == 204, path
    assert recorded == list(_EXPECTED_POST_ACTIONS.values())


def test_the_record_family_is_tried_in_the_order_the_chain_tried_it(
    routed: tuple[str, list[str]],
) -> None:
    """The ordering that a mapping cannot express and a catch-all would destroy.

    ``/record/{id}`` matches every path below it. Each of the four more specific
    shapes is reachable only because it is tried first, and the arguments are the
    identifier with its suffix removed -- so this asserts the slicing too, which
    is where an off-by-one in a refactor of this shape would land.
    """
    base, recorded = routed
    for path in (
        "/record/abc123/file/photo.jpg",
        "/record/abc123/consent",
        "/record/abc123/object",
        "/record/abc123/history",
        "/record/abc123",
    ):
        assert _request(base, path) == 204, path
    assert recorded == [
        "_handle_file:abc123:photo.jpg",
        "_handle_consent_form:abc123",
        "_handle_object_form:abc123",
        "_handle_record_history:abc123",
        "_handle_record:abc123",
    ]


def test_the_remaining_prefixed_get_routes_reach_their_handlers(
    routed: tuple[str, list[str]],
) -> None:
    """``/api/record/`` and ``/static/`` pass the remainder, not the whole path."""
    base, recorded = routed
    assert _request(base, "/api/record/abc123") == 204
    assert _request(base, "/static/site.css") == 204
    assert recorded == ["_handle_api_record:abc123", "_handle_static:site.css"]


def test_the_collection_family_tries_the_finding_aid_before_the_catch_all(
    routed: tuple[str, list[str]],
) -> None:
    """The same ordering hazard as ``/record/{id}``, one family later (#202).

    ``/collection/{id}`` is a catch-all: ``casa-abierta/ead.xml`` matches it
    too, so the finding-aid route is reachable only because it is tried first.
    Asserted over loopback, with the slicing, for the same reason the record
    family is.
    """
    base, recorded = routed
    assert _request(base, "/collection/casa-abierta/ead.xml") == 204
    assert _request(base, "/collection/casa-abierta") == 204
    assert recorded == [
        "_handle_collection_ead:casa-abierta",
        "_handle_collection:casa-abierta",
    ]


def test_a_path_no_table_claims_is_a_not_found(routed: tuple[str, list[str]]) -> None:
    """The floor under every check above: dispatch can still miss.

    A dispatcher that answered everything would satisfy each positive assertion
    here and serve a 200 for a path that does not exist.
    """
    base, recorded = routed
    assert _request(base, "/no-such-page") == 204
    assert _request(base, "/steward/nothing/here", method="POST") == 204
    assert recorded == ["_handle_not_found", "_handle_not_found"]
