"""Can an outsider learn that a sealed record EXISTS? Not "read it" — exist.

``tests/test_no_outing.py`` proves a contributor identity reaches no public
surface. This file asks the other question the README's promise implies, and that
``docs/VERIFYING-ATTESTATIONS.md`` names as the reason absolute counts are kept
steward-only: whether an observer can *date* a sealed deposit. Knowing that
something was sealed into this archive at 14:32 is enough, given who was seen at
the community space at 14:30, to point at a person — no record content required.

Two channels are tested, both by differencing rather than by reading the code:

* **Live surfaces.** Two archives are built with identical public content; one of
  them additionally holds a sealed record. Every anonymous surface is fetched from
  each and compared byte for byte. Anything that differs is a channel — this is
  what caught the live ``chain_head`` commitment, which carries no count (so it
  passed the count-gating rule) but moves the instant *any* record is written, and
  was served to anonymous callers on ``/healthz`` and ``/proof``.

* **Off-box backups.** The encrypted backup's sidecar manifest is plaintext by
  necessity (a restore needs the salt out of it). It must therefore describe the
  file and not the collection: a nightly cron leaves a dated series of these on a
  host the community does not control, and a ``bag-count`` in each one reads off
  the archive's true size and the date every sealed deposit was made.
"""

from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import closing
from http.server import HTTPServer
from pathlib import Path

import pytest

from ledger.backup import _manifest_for, create_backup
from ledger.config import Config
from ledger.identity import IdentityVault
from ledger.ingest import Archive
from ledger.models import AccessPolicy, DublinCore, Field, Record
from ledger.server import make_server

pytestmark = pytest.mark.disclosure

_NOW = "2026-06-16T00:00:00Z"
_VAULT_KEY = b"0123456789abcdef0123456789abcdef0123456789a="
_PASSPHRASE = "correct horse battery staple"  # noqa: S105 - test passphrase, not a secret

# Every anonymous GET the server answers. A route added without being listed here
# is a channel this test cannot see, so the route table is asserted against the
# handler's own dispatcher below rather than trusted to stay complete by hand.
_ANONYMOUS_ROUTES = (
    "/",
    "/search?q=aid",
    "/search?q=",
    "/healthz",
    "/status",
    "/about",
    "/overview",
    "/places",
    "/timeline",
    "/governance",
    "/how-it-works",
    "/proof",
    "/proof/attestation.json",
    "/transparency",
    "/oai?verb=Identify",
    "/oai?verb=ListIdentifiers&metadataPrefix=oai_dc",
    "/oai?verb=ListRecords&metadataPrefix=oai_dc",
    "/oai?verb=ListSets",
    "/sitemap.xml",
    "/robots.txt",
    "/feed.atom",
    "/consent-status",
    "/contribute",
    "/withdraw",
    "/edit",
    "/api/records",
    "/api/search?q=",
    "/api/search.csv?q=",
    "/record/rec-public",
    "/record/rec-public?proceed=1",
    "/record/rec-sealed",
    "/record/rec-absent",
    "/api/record/rec-sealed",
    "/api/record/rec-absent",
    "/steward",
    "/steward/audit",
)


def _public_record() -> Record:
    return Record(
        title="Public: a mutual aid run",
        record_id="rec-public",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(
            title=["Public: a mutual aid run"],
            description=["A public account."],
            coverage=["Oakland"],
            date=["2024-01-01"],
        ),
        fields=[Field(name="story", value="We drove food.", policy=AccessPolicy.PUBLIC)],
        created_at=_NOW,
    )


def _sealed_record() -> Record:
    return Record(
        title="The safehouse list",
        record_id="rec-sealed",
        default_policy=AccessPolicy.SEALED,
        dublin_core=DublinCore(
            title=["The safehouse list"],
            description=["Sealed."],
            coverage=["Sacramento"],
            date=["1999-01-01"],
        ),
        fields=[Field(name="names", value="withheld", policy=AccessPolicy.SEALED)],
        created_at=_NOW,
    )


def _build(root: Path, incoming: Path, *, with_sealed: bool) -> Archive:
    """An archive with one public record, optionally plus one sealed record."""
    archive = Archive.init(Config.default("Differential Archive", root))
    incoming.mkdir(parents=True, exist_ok=True)
    public_payload = incoming / "public.txt"
    public_payload.write_text("a public account of mutual aid", encoding="utf-8")
    archive.ingest({"public.txt": public_payload}, _public_record(), now=_NOW)
    if with_sealed:
        sealed_payload = incoming / "sealed.txt"
        sealed_payload.write_text("names and addresses", encoding="utf-8")
        archive.ingest(
            {"sealed.txt": sealed_payload},
            _sealed_record(),
            vault_key=_VAULT_KEY,
            now=_NOW,
        )
    return archive


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
    """Erase the two incidental differences: the ephemeral port and any timestamp."""
    for port in ports:
        text = text.replace(str(port), "PORT")
    return re.sub(r"\d{4}-\d{2}-\d{2}T[\d:.]+(?:Z|[+-]\d{2}:\d{2})?", "TIMESTAMP", text)


def test_no_anonymous_surface_differs_when_a_sealed_record_is_present(
    tmp_path: Path,
) -> None:
    """The differential attack: sealed-present vs sealed-absent, byte for byte.

    Both archives hold the same public record; only one also holds a sealed one.
    An anonymous observer who can tell the two apart on any surface can, on a
    single live archive, tell the moment a sealed record arrives.
    """
    with_sealed = _build(tmp_path / "b" / "store", tmp_path / "b" / "in", with_sealed=True)
    without = _build(tmp_path / "a" / "store", tmp_path / "a" / "in", with_sealed=False)

    a_server = _serve(without)
    b_server = _serve(with_sealed)
    port_a, port_b = next(a_server), next(b_server)
    try:
        differing: list[str] = []
        for route in _ANONYMOUS_ROUTES:
            status_a, body_a = _fetch(port_a, route)
            status_b, body_b = _fetch(port_b, route)
            if status_a != status_b or _normalize(body_a, (port_a, port_b)) != _normalize(
                body_b, (port_a, port_b)
            ):
                differing.append(route)
    finally:
        for server in (a_server, b_server):
            with pytest.raises(StopIteration):
                next(server)

    assert not differing, (
        f"these anonymous surfaces reveal that a sealed record exists: {differing}"
    )


def test_the_route_list_covers_every_anonymous_get(tmp_path: Path) -> None:
    """A differential test is only as complete as its route list.

    Read the routes straight out of the dispatcher's source so a new ``elif path
    == "/x"`` branch fails here instead of quietly escaping the sweep above.
    """
    from ledger import server as server_module

    source = Path(server_module.__file__).read_text(encoding="utf-8")
    dispatcher = source.split("def do_GET")[1].split("def do_POST")[0]
    literals = set(re.findall(r'path == "(/[^"]*)"', dispatcher))
    covered = {route.split("?")[0] for route in _ANONYMOUS_ROUTES}
    assert literals - covered == set(), (
        f"an anonymous GET route is not in the differential sweep: {sorted(literals - covered)}"
    )


def test_anonymous_healthz_carries_no_live_chain_head(tmp_path: Path) -> None:
    """``/healthz`` is the monitor endpoint an outsider can poll cheaply forever.

    The live commitment moves on every write. A steward — or a monitor with a
    provisioned steward grant — still gets it.
    """
    archive = _build(tmp_path / "store", tmp_path / "in", with_sealed=True)
    server = _serve(archive)
    port = next(server)
    try:
        _status, body = _fetch(port, "/healthz")
        payload = json.loads(body)
        assert "chain_head" not in payload, (
            "a per-request commitment over all history dates every sealed deposit"
        )
        assert payload["status"] == "ok"
        assert payload["all_verified"] is True
    finally:
        with pytest.raises(StopIteration):
            next(server)


def test_the_public_proof_page_shows_no_live_chain_head(tmp_path: Path) -> None:
    """``/proof`` renders the *published* attestation's head, or none at all.

    With nothing published there is no head to show; computing one live would hand
    an anonymous visitor the same polling oracle by another route.
    """
    archive = _build(tmp_path / "store", tmp_path / "in", with_sealed=True)
    live_head = archive.chain_head_summary()
    server = _serve(archive)
    port = next(server)
    try:
        _status, body = _fetch(port, "/proof")
    finally:
        with pytest.raises(StopIteration):
            next(server)

    assert live_head not in body
    assert "hash-chained" in body, "the page must still explain the tamper-evidence"


def test_a_published_attestation_is_still_shown_on_proof(tmp_path: Path) -> None:
    """The positive half: gating the LIVE value must not remove the public's check.

    A visitor is asked to save two dated copies and compare. That still works —
    from the signed attestation, at the steward's cadence.
    """
    from ledger.attestation import build_attestation, publish_attestation

    archive = _build(tmp_path / "store", tmp_path / "in", with_sealed=True)
    attestation = build_attestation(archive, now=_NOW)
    publish_attestation(archive, attestation)

    server = _serve(archive)
    port = next(server)
    try:
        _status, body = _fetch(port, "/proof")
        _json_status, raw = _fetch(port, "/proof/attestation.json")
    finally:
        with pytest.raises(StopIteration):
            next(server)

    assert attestation.chain_head_summary in body
    assert json.loads(raw)["chain_head_summary"] == attestation.chain_head_summary


def test_the_backup_sidecar_manifest_states_no_bag_count(tmp_path: Path) -> None:
    """An off-box backup's plaintext sidecar must describe the file, not the archive.

    ``bag-count`` counted sealed and community bags alongside public ones, in the
    clear, beside ciphertext that is explicitly meant to be safe on a host the
    community does not control. A nightly series of them is a public counter.
    """
    archive = _build(tmp_path / "store", tmp_path / "in", with_sealed=True)
    report = create_backup(archive.config, tmp_path / "offbox", _PASSPHRASE)

    sidecar = json.loads(_manifest_for(report.archive_path).read_text(encoding="utf-8"))

    assert "bag-count" not in sidecar, "the plaintext sidecar leaks the archive's true size"
    assert not any(isinstance(value, int) and value == 2 for value in sidecar.values()), (
        "no sidecar value may equal the bag count by another name"
    )
    # ...and a restore still has everything it needs.
    assert set(sidecar) >= {"salt", "ciphertext-sha256", "archive-file"}
    assert report.bag_count == 2, "the count still reaches the steward, in memory"


def test_the_backup_still_restores_and_verifies(tmp_path: Path) -> None:
    """The guard is capable of passing: dropping the field breaks no recovery."""
    from ledger.backup import restore_backup

    archive = _build(tmp_path / "store", tmp_path / "in", with_sealed=True)
    report = create_backup(archive.config, tmp_path / "offbox", _PASSPHRASE)

    restored = restore_backup(report.archive_path, _PASSPHRASE, tmp_path / "restored")

    assert restored.ok
    assert len(restored.bag_results) == 2


def test_identity_vault_key_is_not_the_thing_under_test(tmp_path: Path) -> None:
    """Guard against the fixtures silently drifting into a no-op.

    If the sealed record ever stopped being sealed, every assertion above would
    pass vacuously. Assert the premise directly.
    """
    archive = _build(tmp_path / "store", tmp_path / "in", with_sealed=True)
    from ledger.access.grants import build_grant

    listed = {record.record_id for record in archive.browse(build_grant("anon"), now=_NOW)}
    assert listed == {"rec-public"}
    assert IdentityVault.generate_key() != _VAULT_KEY  # keys are random, not the fixture
