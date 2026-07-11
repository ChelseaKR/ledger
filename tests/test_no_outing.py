"""THE NO-OUTING AUDIT — the single most important test in the project.

ledger exists to let vulnerable people contribute their histories without being
outed. The promise is *selective disclosure*: a record carries only an opaque
``identity_ref``; the real contributor identity lives encrypted in the vault and is
returned only through :meth:`IdentityVault.resolve` under an explicit
``identity_unseal`` grant.

This module ingests a real record through :class:`~ledger.ingest.Archive` carrying a
unique SENTINEL contributor identity, then walks *every public surface* and asserts
the sentinel never appears on any of them:

* the :class:`~ledger.models.DisclosedRecord` ``to_dict`` for anonymous and for a
  steward WITHOUT ``identity_unseal`` (privilege does not equal outing);
* the JSON API output (``/api/records`` and ``/api/record/{id}``);
* the on-disk ``bag-info.txt``, ``record.json``, Dublin Core sidecar, and PREMIS log;
* the rendered HTML for the browse page and the record page (driven through the real
  server handler);
* captured ``logging`` output and the server's own request log.

It then asserts the positive half of the guarantee: the identity IS retrievable via
``vault.resolve`` with a proper ``identity_unseal`` grant. The guarantee is selective
disclosure, not deletion.
"""

from __future__ import annotations

import io
import json
import logging
import threading
import urllib.request
from collections.abc import Iterator
from contextlib import closing, redirect_stderr
from dataclasses import dataclass
from http.server import HTTPServer
from pathlib import Path

import pytest

from ledger.access import disclose
from ledger.access.grants import build_grant, steward
from ledger.config import Config
from ledger.identity import ContributorIdentity, IdentityVault
from ledger.ingest import Archive
from ledger.models import (
    AccessPolicy,
    DublinCore,
    Field,
    Grant,
    Record,
)
from ledger.server import (
    _browse_main_html,
    _record_main_html,
    make_server,
)

pytestmark = pytest.mark.disclosure

# Unique, improbable strings. If ANY appears on a public surface, a contributor has
# been outed. Every assertion in this file is a hunt for them. The marker prefix is
# deliberately not a real word so a fragment can never collide with legitimate public
# collection-level metadata (titles, descriptions, fields) — only an identity leak
# can produce a match.
_MARK = "ZZQ7OUTING"
SENTINEL = f"{_MARK}-name-Wrenflux-Okonkworth-0xDEADBEEF"
SENTINEL_CONTACT = f"{_MARK}-contact@nowhere.invalid"
SENTINEL_PRONOUNS = f"{_MARK}-pronouns-ze-zir"
SENTINEL_NOTES = f"{_MARK}-notes-do-not-contact-at-work"

# Sentinel fragments, so a partial leak (e.g. just the surname) is also caught. Each
# fragment is unique to the contributor identity and appears in no public field.
SENTINEL_FRAGMENTS = (
    SENTINEL,
    SENTINEL_CONTACT,
    SENTINEL_PRONOUNS,
    SENTINEL_NOTES,
    _MARK,
    "Wrenflux",
    "Okonkworth",
    "DEADBEEF",
)

_NOW = "2026-06-16T00:00:00Z"
_RECORD_ID = "rec-audit"


def _sentinel_identity() -> ContributorIdentity:
    return ContributorIdentity(
        name=SENTINEL,
        contact=SENTINEL_CONTACT,
        pronouns=SENTINEL_PRONOUNS,
        notes=SENTINEL_NOTES,
    )


@dataclass
class IngestedArchive:
    """An archive with one ingested record plus the metadata a test needs."""

    archive: Archive
    record_id: str
    identity_ref: str
    bag_dir: Path
    store_root: Path


@pytest.fixture
def ingested(tmp_path: Path) -> IngestedArchive:
    """Stand up an archive and ingest one record carrying the SENTINEL identity.

    The record mixes policies (PUBLIC story, SEALED names) so the surfaces below are
    exercised at every visibility level, but the *contributor identity* travels only
    into the vault — it is never placed in any field, and these tests prove it never
    surfaces.
    """
    root = tmp_path / "archive"
    key = IdentityVault.generate_key()
    config = Config.default("Audit Archive", root)
    archive = Archive.init(config)

    src_dir = tmp_path / "incoming"
    src_dir.mkdir(parents=True, exist_ok=True)
    payload_file = src_dir / "account.txt"
    payload_file.write_text("a public account of mutual aid", encoding="utf-8")

    record = Record(
        title="Oral history: a safehouse network",
        record_id=_RECORD_ID,
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(
            title=["Oral history: a safehouse network"],
            description=["A community-held account; the contributor is protected."],
            publisher=["Audit Archive"],
        ),
        fields=[
            Field(name="story", value="We moved people to safety.", policy=AccessPolicy.PUBLIC),
            Field(
                name="participants",
                value="names withheld by the contributor",
                policy=AccessPolicy.SEALED_UNTIL,
            ),
        ],
        content_warnings=["deportation"],
    )

    aip = archive.ingest(
        {"account.txt": payload_file},
        record,
        identity=_sentinel_identity(),
        vault_key=key,
        now=_NOW,
    )
    assert aip.record.identity_ref is not None
    return IngestedArchive(
        archive=archive,
        record_id=_RECORD_ID,
        identity_ref=aip.record.identity_ref,
        bag_dir=aip.bag.path,
        store_root=archive.store_root,
    )


def _assert_clean(text: str, surface: str) -> None:
    """Assert no sentinel fragment appears in ``text`` for the named ``surface``."""
    for fragment in SENTINEL_FRAGMENTS:
        assert fragment not in text, f"NO-OUTING VIOLATION: {fragment!r} leaked into {surface}"


# --- the identity is sealed in the record, not absent from the system -------


def test_identity_ref_is_opaque_not_the_identity(ingested: IngestedArchive) -> None:
    """The record's identity_ref is an opaque token, never the identity itself."""
    record = ingested.archive.get(ingested.record_id)
    assert record.identity_ref is not None
    _assert_clean(record.identity_ref, "record.identity_ref token")


# --- surface 1: DisclosedRecord.to_dict for anonymous AND steward ------------


def _grants_without_unseal() -> Iterator[tuple[str, Grant]]:
    """The grants a public surface might run under — none may unseal identity."""
    yield "anonymous", build_grant("anon")
    yield (
        "community-member",
        build_grant("member", levels=(AccessPolicy.PUBLIC, AccessPolicy.COMMUNITY)),
    )
    yield "steward-without-unseal", steward("steward")


@pytest.mark.parametrize(
    "label,grant",
    list(_grants_without_unseal()),
    ids=[label for label, _ in _grants_without_unseal()],
)
def test_disclosed_dict_never_contains_sentinel(
    ingested: IngestedArchive, label: str, grant: Grant
) -> None:
    """DisclosedRecord.to_dict carries no contributor identity for any non-unseal grant.

    A steward sees every *field* but is still not handed the contributor identity:
    the disclosed shape has no identity_ref, and disclose never injects identity.
    """
    record = ingested.archive.get(ingested.record_id)
    disclosed = disclose(record, grant, _NOW)
    payload = json.dumps(disclosed.to_dict(), ensure_ascii=False, sort_keys=True)
    _assert_clean(payload, f"DisclosedRecord.to_dict ({label})")
    # The disclosed shape cannot even carry the ref field that points at identity.
    assert "identity_ref" not in payload


def test_archive_disclose_never_contains_sentinel(ingested: IngestedArchive) -> None:
    """Archive.disclose (the facade read path) is identity-free for a steward."""
    disclosed = ingested.archive.disclose(ingested.record_id, steward("steward"), now=_NOW)
    payload = json.dumps(disclosed.to_dict(), ensure_ascii=False, sort_keys=True)
    _assert_clean(payload, "Archive.disclose to_dict (steward)")


def test_browse_listing_never_contains_sentinel(ingested: IngestedArchive) -> None:
    """Archive.browse — the listing read path — is identity-free for a steward."""
    listing = ingested.archive.browse(steward("steward"), now=_NOW)
    payload = json.dumps([d.to_dict() for d in listing], ensure_ascii=False, sort_keys=True)
    _assert_clean(payload, "Archive.browse listing (steward)")


# --- surface 2: the on-disk artifacts ---------------------------------------


def _on_disk_artifacts(ingested: IngestedArchive) -> Iterator[tuple[str, Path]]:
    bag = ingested.bag_dir
    yield "bag-info.txt", bag / "bag-info.txt"
    yield "record.json", bag / "record.json"
    yield "dublincore.json (sidecar)", bag / "dublincore.json"
    yield "premis.json (PREMIS log)", bag / "premis.json"
    yield "records/ fast-lookup manifest", ingested.store_root / "records" / f"{_RECORD_ID}.json"


def test_on_disk_artifacts_never_contain_sentinel(ingested: IngestedArchive) -> None:
    """bag-info.txt, record.json, the DC sidecar, and the PREMIS log hold no identity."""
    for name, path in _on_disk_artifacts(ingested):
        assert path.exists(), f"expected artifact missing: {name}"
        text = path.read_text(encoding="utf-8")
        _assert_clean(text, f"on-disk {name}")


def test_catalog_index_never_contains_sentinel(ingested: IngestedArchive) -> None:
    """FIX-04: the sqlite catalog index caches record manifests -- audit it too.

    The index (:mod:`ledger.catalog_index`) is a cache of the same identity-free
    ``records/*.json`` text every other read path already trusts, but it is a new
    on-disk artifact, so it gets its own explicit sentinel scan rather than relying
    only on the whole-tree sweep below (belt and suspenders on the single riskiest
    new surface FIX-04 adds). ``browse`` is called first so the index is actually
    built before the file is inspected.
    """
    ingested.archive.browse(steward("steward"), now=_NOW)
    index_file = ingested.archive.index_path
    assert index_file.exists(), "browse() must have built the catalog index"
    raw = index_file.read_bytes().decode("latin-1")
    _assert_clean(raw, "catalog index (sqlite)")


def test_whole_store_tree_never_contains_sentinel(ingested: IngestedArchive) -> None:
    """Defense in depth: NO clear-text file anywhere under the store leaks the sentinel.

    The vault itself is excluded because it legitimately holds the (encrypted) identity;
    every other file — manifests, tag files, payload, config, and the catalog index
    (browse is called first so it exists to be scanned) — is scanned.
    """
    ingested.archive.browse(steward("steward"), now=_NOW)
    vault_path = Path(ingested.archive.vault_path).resolve()
    for path in ingested.store_root.rglob("*"):
        if not path.is_file() or path.resolve() == vault_path:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_bytes().decode("latin-1")
        _assert_clean(text, f"store file {path.name}")


# --- surface 3: rendered HTML (browse + record pages) -----------------------


def test_rendered_browse_html_never_contains_sentinel(ingested: IngestedArchive) -> None:
    """The browse page HTML, rendered from disclosed records, holds no identity."""
    for label, grant in _grants_without_unseal():
        records = ingested.archive.browse(grant, now=_NOW)
        html = _browse_main_html(records, heading="Browse the archive")
        _assert_clean(html, f"rendered browse HTML ({label})")


def test_rendered_record_html_never_contains_sentinel(ingested: IngestedArchive) -> None:
    """The record page HTML (interstitial and proceeded) holds no identity."""
    for label, grant in _grants_without_unseal():
        disclosed = ingested.archive.disclose(ingested.record_id, grant, now=_NOW)
        for proceed in (False, True):
            html = _record_main_html(disclosed, proceed=proceed)
            _assert_clean(html, f"rendered record HTML ({label}, proceed={proceed})")


# --- surface 4: the live server (HTML + JSON + request log) ------------------


@pytest.fixture
def running_server(ingested: IngestedArchive) -> Iterator[tuple[int, io.StringIO]]:
    """Start the real browse server on an ephemeral port, capturing its request log.

    The server's request log goes to stderr; we redirect it so the audit can also
    confirm the access log never carries the sentinel (no-outing rule for logs).
    """
    log_capture = io.StringIO()
    with redirect_stderr(log_capture):
        httpd: HTTPServer = make_server(ingested.archive, host="127.0.0.1", port=0)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield port, log_capture
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)


def _get(port: int, path: str, *, grant_subject: str | None = None) -> str:
    """Issue a GET against the running server, optionally as a named grant subject."""
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    if grant_subject is not None:
        request.add_header("X-Ledger-Grant", grant_subject)
    with closing(urllib.request.urlopen(request)) as response:  # noqa: S310 - loopback only
        body: bytes = response.read()
    return body.decode("utf-8")


def test_server_browse_and_record_html_never_contain_sentinel(
    running_server: tuple[int, io.StringIO],
) -> None:
    """The live server's browse and record HTML responses never carry the sentinel."""
    port, _ = running_server
    browse = _get(port, "/")
    _assert_clean(browse, "server GET / (browse HTML)")
    record = _get(port, f"/record/{_RECORD_ID}?proceed=1")
    _assert_clean(record, "server GET /record (record HTML)")
    interstitial = _get(port, f"/record/{_RECORD_ID}")
    _assert_clean(interstitial, "server GET /record (CW interstitial)")
    search = _get(port, "/search?q=safehouse")
    _assert_clean(search, "server GET /search")


def test_server_json_api_never_contains_sentinel(
    running_server: tuple[int, io.StringIO],
) -> None:
    """The JSON API (/api/records and /api/record/{id}) never carries the sentinel."""
    port, _ = running_server
    records = _get(port, "/api/records")
    _assert_clean(records, "server GET /api/records")
    assert _RECORD_ID in records  # sanity: the record really is being served
    one = _get(port, f"/api/record/{_RECORD_ID}")
    _assert_clean(one, "server GET /api/record/{id}")


def test_server_request_log_never_contains_sentinel(
    running_server: tuple[int, io.StringIO],
) -> None:
    """The server's own access log holds no sentinel, even after sentinel-shaped requests."""
    port, log_capture = running_server
    # Hit the server, including a query echoing a sentinel fragment, then inspect log.
    _get(port, "/")
    _get(port, f"/record/{_RECORD_ID}?proceed=1")
    _get(port, f"/search?q={_MARK}-Okonkworth", grant_subject=f"{_MARK}-Wrenflux")
    _assert_clean(log_capture.getvalue(), "server request log")


# --- surface 5: captured application logging --------------------------------


def test_captured_logging_never_contains_sentinel(
    ingested: IngestedArchive, caplog: pytest.LogCaptureFixture
) -> None:
    """Driving the read paths emits no log line carrying the sentinel."""
    with caplog.at_level(logging.DEBUG):
        ingested.archive.get(ingested.record_id)
        ingested.archive.disclose(ingested.record_id, steward("steward"), now=_NOW)
        ingested.archive.browse(steward("steward"), now=_NOW)
        ingested.archive.audit_fixity()
    _assert_clean(caplog.text, "captured logging output")


# --- the positive guarantee: selective disclosure, not deletion -------------


def test_identity_is_retrievable_with_proper_unseal_grant(
    ingested: IngestedArchive,
) -> None:
    """With an identity_unseal grant naming the ref, resolve_identity returns the identity.

    This is the other half of the promise: ledger does not destroy the identity, it
    *gates* it. An authorized custodian can still reach it; everyone else cannot.
    """
    grant = build_grant("custodian", identity_unseal=(ingested.identity_ref,))
    identity = ingested.archive.resolve_identity(ingested.record_id, grant)
    assert identity.name == SENTINEL
    assert identity.contact == SENTINEL_CONTACT
    assert identity.pronouns == SENTINEL_PRONOUNS
    assert identity.notes == SENTINEL_NOTES


def test_identity_not_retrievable_without_unseal_grant(
    ingested: IngestedArchive,
) -> None:
    """A steward without an identity_unseal token cannot resolve the contributor."""
    from ledger.errors import AccessDenied

    with pytest.raises(AccessDenied):
        ingested.archive.resolve_identity(ingested.record_id, steward("steward"))
