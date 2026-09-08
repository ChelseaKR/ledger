"""``/healthz`` over an empty archive: honest to a steward, silent to everyone else.

#208 closed the vacuous fixity fold on ``/status``, the hand-off runbook and
``ledger handoff``'s summary line, and deliberately left ``/healthz``'s
``all_verified`` alone as a machine contract. This module is the finished reading of
that contract, and it lands on **both** sides of it.

**What consumes ``/healthz`` (measured 2026-09-08).** Nothing outside this repository:
``all_verified`` appears in no other repository in the portfolio, and every in-repo
consumer — ``infra/Dockerfile``'s ``HEALTHCHECK``, ``infra/docker-compose.yml`` and
``infra/aws/docker-compose.deploy.yml`` — is ``curl -fsS``, which reads the HTTP status
code and nothing else. ``infra/aws/terraform`` declares no health check and its state
holds zero resources, so there is no deployed monitor whose alert this could move.

**So why is the anonymous payload still unchanged?** Because the constraint that binds
it is not a consumer, it is a disclosure. Every other route to ``all_verified: False``
also sets ``status: "degraded"`` and returns 503: a failing bag, and — since
``AuditReport.ok`` became ``status is VERIFIED`` — a bag that declared no files to
check. Making the empty case honest would therefore make ``200 + "ok" +
all_verified: False`` reachable **only** by an archive holding nothing at all, and an
anonymous caller would learn the absolute fact that the archive is empty. That is
exactly what the gated counts on this endpoint exist to withhold, and
:func:`ledger.attestation.build_attestation` records the identical trade for the
signed attestation at issue #205.

The fix goes where the disclosure has already been made: a steward already reads
``bags_audited``, so it already knows when the archive is empty, and it now gets
``fixity.status`` — the three-state verdict — beside the counts.

The load-bearing test here is
:func:`test_the_anonymous_body_is_identical_over_an_empty_and_a_healthy_archive`. It
does not describe the anti-enumeration property, it asserts it, so a later change to
``all_verified`` fails here loudly instead of quietly turning this endpoint into an
emptiness oracle.
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

from ledger.access.grants import issue_grant_token
from ledger.config import Config
from ledger.fixity import FixityStatus
from ledger.ingest import Archive
from ledger.models import AccessPolicy, DublinCore, Field, Record
from ledger.server import make_server

_VAULT_KEY = "0123456789abcdef0123456789abcdef0123456789a="
_GRANT_SECRET = b"healthz-outsider-test-grant-secret"
_NOW = "2026-06-16T12:00:00Z"


def _archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, records: int, name: str
) -> Archive:
    monkeypatch.setenv("LEDGER_VAULT_KEY", _VAULT_KEY)
    monkeypatch.setenv("LEDGER_GRANT_SECRET", _GRANT_SECRET.decode())
    root = tmp_path / name
    archive = Archive.init(Config.default("Healthz Test Archive", root))
    for index in range(records):
        payload = root.parent / f"{name}-doc{index}.txt"
        payload.write_text(f"synthetic record {index}\n", encoding="utf-8")
        archive.ingest(
            {payload.name: payload},
            Record(
                title=f"Record {index}",
                default_policy=AccessPolicy.PUBLIC,
                dublin_core=DublinCore(title=[f"Record {index}"], type=["flyer"]),
                fields=[Field("text", "public", AccessPolicy.PUBLIC)],
            ),
            now=_NOW,
        )
    return archive


def _serve(archive: Archive, tmp_path: Path, name: str) -> Iterator[str]:
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


@pytest.fixture
def empty_site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    yield from _serve(_archive(tmp_path, monkeypatch, records=0, name="empty"), tmp_path, "empty")


@pytest.fixture
def seeded_site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    yield from _serve(_archive(tmp_path, monkeypatch, records=2, name="seeded"), tmp_path, "seeded")


def _healthz(base: str, *, steward: bool = False) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(f"{base}/healthz")  # noqa: S310 - loopback
    if steward:
        request.add_header("X-Ledger-Grant", issue_grant_token("warden", _GRANT_SECRET))
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - loopback URL we constructed for the in-process test server
            body = response.read().decode("utf-8")
            code = int(response.status)
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8")
        code = int(error.code)
    parsed: dict[str, Any] = json.loads(body)
    return code, parsed


# --- the anti-enumeration property ------------------------------------------


def test_the_anonymous_body_is_identical_over_an_empty_and_a_healthy_archive(
    empty_site: str, seeded_site: str
) -> None:
    """The property, asserted rather than promised.

    An outsider polling this endpoint must not be able to tell an archive holding
    nothing from an archive holding two public records. Any change that makes
    ``all_verified`` honest for the anonymous caller turns ``/healthz`` into an
    emptiness oracle, and fails here.

    If that trade is later made deliberately (#205), this test is the thing to delete,
    and deleting it is the record that the decision was taken.
    """
    empty_code, empty_body = _healthz(empty_site)
    seeded_code, seeded_body = _healthz(seeded_site)
    assert empty_body == {"status": "ok", "all_verified": True, "ready": True}
    assert empty_body == seeded_body
    assert empty_code == seeded_code == 200


def test_an_outsider_is_given_no_fixity_block_at_all(empty_site: str) -> None:
    """The three-state verdict rides inside the block that is already gated."""
    _code, body = _healthz(empty_site)
    assert "fixity" not in body
    assert "chain_head" not in body


# --- what a steward now sees -------------------------------------------------


def test_a_steward_is_told_an_empty_archive_verified_nothing(empty_site: str) -> None:
    """The defect: `bags_audited: 0` sat beside `all_verified: true` and nothing said so."""
    code, body = _healthz(empty_site, steward=True)
    assert code == 200
    fixity_block = body["fixity"]
    assert fixity_block["bags_audited"] == 0
    assert fixity_block["status"] == FixityStatus.UNVERIFIED.value
    # The published boolean is deliberately untouched, on both sides of the grant.
    assert body["all_verified"] is True


def test_a_steward_reading_a_healthy_archive_still_sees_verified(seeded_site: str) -> None:
    """The passing branch is unchanged; this adds a word, it does not move a verdict."""
    code, body = _healthz(seeded_site, steward=True)
    assert code == 200
    assert body["all_verified"] is True
    assert body["fixity"]["status"] == FixityStatus.VERIFIED.value
    assert body["fixity"]["bags_audited"] == 2
    assert body["fixity"]["bags_failed"] == 0


def test_a_damaged_archive_is_failed_not_merely_unverified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Damage dominates absence, and the endpoint says which it is.

    Without this the new field could report ``could-not-verify`` for every non-ideal
    archive, which would be the same collapse in the other direction: a reader must be
    able to tell "nothing was checked" from "something is broken".
    """
    archive = _archive(tmp_path, monkeypatch, records=1, name="damaged")
    payloads = sorted(Path(archive.config.store_root).rglob("*doc0.txt"))
    assert payloads, "no payload to corrupt; this test would prove nothing"
    payloads[0].write_text("tampered\n", encoding="utf-8")
    for base in _serve(archive, tmp_path, "damaged"):
        code, body = _healthz(base, steward=True)
        assert code == 503
        assert body["all_verified"] is False
        assert body["fixity"]["status"] == FixityStatus.FAILED.value
        assert body["fixity"]["bags_failed"] == 1
        break
