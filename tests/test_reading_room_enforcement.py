"""The reading-room cannot leak a policy applied through the disclosure workflow.

This is the end-to-end safety proof tying the two surfaces together: a steward applies
a disclosure policy *after* ingest through the workflow primitives
(:func:`ledger.moderate.set_field_policy`, :func:`ledger.access.redaction.redact_field`),
then the accessible reading-room is driven over a real loopback socket exactly as a
browser or integrator would drive it. The embargoed value, the redacted value, and the
sealed contributor identity must appear in **no** anonymous response — HTML, the JSON
record/list APIs, or the CSV export — while the reading-room still *honestly* surfaces
that something is withheld and when it opens (the no-outing rule, proven through the
public face, not just the engine).
"""

from __future__ import annotations

import json
import threading
import urllib.request
from collections.abc import Iterator
from contextlib import redirect_stderr, redirect_stdout
from http.server import HTTPServer
from io import StringIO
from pathlib import Path

import pytest

from ledger.access.redaction import redact_field
from ledger.config import Config
from ledger.identity import ContributorIdentity
from ledger.ingest import Archive
from ledger.models import AccessPolicy, DublinCore, Field, Record
from ledger.moderate import set_field_policy
from ledger.server import make_server

pytestmark = pytest.mark.disclosure

# Loud sentinels: any appearance on an anonymous surface is an unmistakable leak.
_IDENTITY = "SENTINEL-IDENTITY-DO-NOT-LEAK-7Q4X"
_EMBARGOED = "SENTINEL-EMBARGOED-FIELD-9Z2K"
_REDACTED = "SENTINEL-REDACTED-FIELD-3T8M"

_VAULT_KEY = b"0123456789abcdef0123456789abcdef0123456789a="
_NOW = "2026-06-16T12:00:00Z"
_FUTURE = "2099-01-01T00:00:00Z"


def _build_archive(tmp_path: Path) -> tuple[Archive, str]:
    """Ingest one public record, then apply an embargo and a redaction via the workflow.

    The record starts fully public (so nothing is hidden merely by its ingest defaults),
    then a steward (1) embargoes ``location`` until the far future through
    :func:`set_field_policy` and (2) redacts ``alias`` through the recorded redaction
    transform. A contributor identity is sealed into the vault at ingest. The returned
    record id therefore exercises an embargoed field, a redacted field, and a sealed
    identity at once.
    """
    config = Config.default("Reading Room Test", tmp_path / "arc")
    archive = Archive.init(config)
    record = Record(
        title="Thursday gatherings",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(title=["Thursday gatherings"], publisher=[config.archive_name]),
        fields=[
            Field(name="story", value="A public account.", policy=AccessPolicy.PUBLIC),
            Field(name="location", value=f"venue: {_EMBARGOED}", policy=AccessPolicy.PUBLIC),
            Field(name="alias", value=f"alias: {_REDACTED}", policy=AccessPolicy.PUBLIC),
        ],
    )
    archive.ingest(
        {},
        record,
        identity=ContributorIdentity(name=_IDENTITY),
        vault_key=_VAULT_KEY,
        agent="rr-test",
        now=_NOW,
    )

    stored = archive.get(record.record_id)
    embargoed, ev1, _a = set_field_policy(
        stored,
        "location",
        AccessPolicy.SEALED_UNTIL,
        unseal_at=_FUTURE,
        actor="steward",
        reason="contributor asked to embargo the venue",
        now=_NOW,
    )
    archive.apply_update(embargoed, ev1)
    redacted, ev2 = redact_field(embargoed, "alias", agent="steward", now=_NOW)
    archive.apply_update(redacted, ev2)
    return archive, record.record_id


@pytest.fixture
def server(tmp_path: Path) -> Iterator[tuple[str, str]]:
    """A running reading-room on an ephemeral port; yields (base_url, record_id)."""
    archive, record_id = _build_archive(tmp_path)
    httpd: HTTPServer = make_server(archive, host="127.0.0.1", port=0)
    host, port = httpd.server_address[0], httpd.server_address[1]
    host_s = host.decode("ascii") if isinstance(host, (bytes, bytearray)) else str(host)
    base = f"http://{host_s}:{int(port)}"
    sink = StringIO()
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    with redirect_stderr(sink), redirect_stdout(sink):
        thread.start()
        try:
            yield base, record_id
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()


def _get(base: str, path: str) -> str:
    url = f"{base}{path}"
    request = urllib.request.Request(url)  # noqa: S310 - loopback URL we constructed
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
        return str(response.read().decode("utf-8"))


def test_reading_room_never_exposes_embargoed_redacted_or_identity(
    server: tuple[str, str],
) -> None:
    """No anonymous reading-room surface leaks the embargoed, redacted, or identity value.

    Sweeps every anonymous read path that carries record data — the record page, the
    browse/list page, the JSON record and list APIs, and the CSV export — and asserts
    none of the three sentinels appears in any body. This is the structural promise of
    the disclosure engine, proven through the public face after the policy was applied
    by the workflow (the no-outing rule, end to end).
    """
    base, rid = server
    bodies = [
        _get(base, f"/record/{rid}"),
        _get(base, "/"),
        _get(base, f"/api/record/{rid}"),
        _get(base, "/api/records"),
        _get(base, "/api/search.csv"),
    ]
    for body in bodies:
        assert _IDENTITY not in body
        assert _EMBARGOED not in body
        assert _REDACTED not in body


def test_reading_room_acknowledges_withholding_but_hides_date_from_outsiders(
    server: tuple[str, str],
) -> None:
    """An anonymous reader learns *that* something is withheld — never the embargo date.

    Honesty without targeting metadata (P2-2): the outsider JSON shape carries a
    withheld *count*, not the field names or the "sealed until <date>" reasons, and the
    embargo date appears nowhere in the anonymous HTML either — so the set of seals
    cannot be scraped as a map of where the sensitive material is. The public field is
    still served, so sealing one field never hides the whole record.
    """
    base, rid = server
    data = json.loads(_get(base, f"/api/record/{rid}"))
    assert data.get("withheld_count", 0) >= 1  # the count is honest...
    assert "withheld" not in data  # ...but names/reasons are not exposed to an outsider
    assert data["fields"]["story"] == "A public account."
    html = _get(base, f"/record/{rid}")
    assert "2099-01-01" not in html  # the embargo date is never shown to anonymous
    assert "sealed until" not in html.lower()


def test_engine_surfaces_the_safe_reason_to_an_authorized_steward(tmp_path: Path) -> None:
    """Where permitted, the same seal is surfaced honestly: a steward sees the reason.

    The generalization above is grant-dependent, not blanket suppression. An authorized
    steward, disclosing through the one disclosure chokepoint, sees the value-free reason
    ("sealed until 2099-…") for the embargoed field — even though the temporal embargo
    still withholds the value from them until the date (honesty + fail-closed).
    """
    from ledger.access.grants import steward

    archive, rid = _build_archive(tmp_path)
    disclosed = archive.disclose(rid, steward("a-steward"), now=_NOW)
    reasons = {r.name: r.reason for r in disclosed.withheld}
    assert "location" in reasons
    assert "sealed until 2099" in reasons["location"]
    assert _EMBARGOED not in reasons["location"]  # the reason never leaks the value


def test_disclosed_record_has_no_identity_ref_field(server: tuple[str, str]) -> None:
    """The JSON record shape structurally omits identity_ref (defense in depth)."""
    base, rid = server
    data = json.loads(_get(base, f"/api/record/{rid}"))
    assert "identity_ref" not in data
