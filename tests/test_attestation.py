"""Tests for public transparency attestations on ``/proof`` (EXP-01).

Pins three things: the attestation is deterministic and reproducible for a fixed
archive state; ``chain_head_summary`` changes when (and only when) the archive's
recorded history actually changes, so a third party can detect a rewrite; and the
published document never leaks a contributor identity or an absolute count
(no-outing / P2-2, the same convention checked throughout ``test_server_remediation.py``).
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import pytest

from ledger import cli
from ledger.attestation import (
    ATTESTATION_SCHEMA_VERSION,
    HealthAttestation,
    build_attestation,
    chain_head_summary,
    latest_attestation_path,
    publish_attestation,
    sign_attestation,
)
from ledger.config import Config
from ledger.identity import ContributorIdentity
from ledger.ingest import Archive
from ledger.models import AccessPolicy, DublinCore, Field, Record
from ledger.server import make_server

_VAULT_KEY = "0123456789abcdef0123456789abcdef0123456789a="
_NOW = "2026-07-07T09:00:00Z"
_LATER = "2026-07-08T09:00:00Z"
_SENTINEL = "SENTINEL-ATTESTATION-DO-NOT-LEAK-77Q"


def _seed_archive(tmp_path: Path, *, name: str = "Attestation Test Archive") -> Archive:
    config = Config.default(name, tmp_path / "arc")
    archive = Archive.init(config)
    payload = tmp_path / "flyer.txt"
    payload.write_text("Pride march 1991, library steps, noon.")
    record = Record(
        title="Flyer",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(title=["Flyer"], subject=["pride"], type=["flyer"]),
        fields=[Field("text", "public", AccessPolicy.PUBLIC)],
    )
    archive.ingest(
        {payload.name: payload},
        record,
        identity=ContributorIdentity(name=_SENTINEL),
        vault_key=_VAULT_KEY.encode(),
        now=_NOW,
    )
    return archive


# --- build_attestation / chain_head_summary ---------------------------------


def test_empty_archive_attestation_is_healthy_and_deterministic(tmp_path: Path) -> None:
    config = Config.default("Empty Archive", tmp_path / "arc")
    archive = Archive.init(config)
    a1 = build_attestation(archive, now=_NOW)
    a2 = build_attestation(archive, now=_NOW)
    assert a1.fixity_ok is True  # vacuously true: nothing to fail
    assert a1.chain_head_summary == a2.chain_head_summary  # reproducible
    assert a1.schema_version == ATTESTATION_SCHEMA_VERSION


def test_chain_head_summary_changes_when_history_grows(tmp_path: Path) -> None:
    archive = _seed_archive(tmp_path)
    before = chain_head_summary(archive)

    payload = tmp_path / "second.txt"
    payload.write_text("a second record")
    record = Record(
        title="Second",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(title=["Second"], subject=["x"], type=["zine"]),
        fields=[],
    )
    archive.ingest({payload.name: payload}, record, now=_LATER)

    after = chain_head_summary(archive)
    assert before != after  # new history -> new commitment


def test_chain_head_summary_changes_on_tamper(tmp_path: Path) -> None:
    """Directly editing a bag's PREMIS log on disk changes the published summary.

    This is the raw-disk-attacker scenario the whole feature exists for: a
    steward with filesystem access rewrites history without going through any
    application code path.
    """
    archive = _seed_archive(tmp_path)
    before = chain_head_summary(archive)

    premis_paths = list(archive.bags_dir.glob("*/premis.json"))
    assert premis_paths, "expected at least one bag premis log"
    target = premis_paths[0]
    tampered = target.read_text(encoding="utf-8").replace("success", "tampered")
    assert tampered != target.read_text(encoding="utf-8")
    target.write_text(tampered, encoding="utf-8")

    after = chain_head_summary(archive)
    assert before != after


def test_attestation_never_contains_identity_or_absolute_counts(tmp_path: Path) -> None:
    archive = _seed_archive(tmp_path)
    attestation = build_attestation(archive, now=_NOW)
    body = attestation.to_json()
    assert _SENTINEL not in body
    data = json.loads(body)
    # Deliberately narrow shape (see ledger.attestation module docstring): no bag
    # count, no per-bag/per-log breakdown, nothing that could be watched over time
    # to infer when a (possibly sealed) record was added.
    assert set(data.keys()) == {
        "schema_version",
        "archive_name",
        "generated_at",
        "software_version",
        "fixity_ok",
        "chain_head_summary",
    }


def test_fixity_ok_false_when_a_bag_is_corrupted(tmp_path: Path) -> None:
    archive = _seed_archive(tmp_path)
    bag_dirs = [p for p in archive.bags_dir.iterdir() if p.is_dir()]
    payload_files = list(bag_dirs[0].glob("data/*"))
    assert payload_files
    payload_files[0].write_bytes(b"corrupted bytes")
    attestation = build_attestation(archive, now=_NOW)
    assert attestation.fixity_ok is False


# --- HealthAttestation JSON round trip ---------------------------------------


def test_attestation_json_round_trip(tmp_path: Path) -> None:
    archive = _seed_archive(tmp_path)
    original = build_attestation(archive, now=_NOW)
    restored = HealthAttestation.from_json(original.to_json())
    assert restored == original


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", "1"),
        ("fixity_ok", "false"),
        ("chain_head_summary", "not-a-digest"),
    ],
)
def test_attestation_json_rejects_mistyped_security_fields(
    tmp_path: Path, field: str, value: object
) -> None:
    """Malformed public state fails closed instead of being coerced into health."""
    archive = _seed_archive(tmp_path)
    body = build_attestation(archive, now=_NOW).to_dict()
    body[field] = value
    with pytest.raises(ValueError):
        HealthAttestation.from_json(json.dumps(body))


# --- signing (ssh-keygen -Y) --------------------------------------------------

_SSH_KEYGEN = shutil.which("ssh-keygen")
_HAVE_SSH_KEYGEN = _SSH_KEYGEN is not None


@pytest.mark.skipif(not _HAVE_SSH_KEYGEN, reason="ssh-keygen not available")
def test_sign_and_verify_round_trip(tmp_path: Path) -> None:
    import subprocess

    key_path = tmp_path / "signing_key"
    subprocess.run(  # noqa: S603 - resolved executable, fixed argv, test fixture
        [_SSH_KEYGEN, "-t", "ed25519", "-N", "", "-C", "test", "-f", str(key_path)],
        check=True,
        capture_output=True,
    )
    archive = _seed_archive(tmp_path)
    attestation = build_attestation(archive, now=_NOW)
    signed = sign_attestation(attestation, key_path)
    assert signed.signature is not None
    assert signed.signature_format == "ssh"
    # The signature still covers exactly the unsigned fields (nothing silently
    # changed underneath it).
    assert signed.signing_payload() == attestation.signing_payload()

    # A genuine third party verifies with only the public key and the payload —
    # never the private key, never this process's in-memory state.
    allowed_signers = tmp_path / "allowed_signers"
    pub_key = (key_path.with_suffix(".pub")).read_text(encoding="utf-8").strip()
    allowed_signers.write_text(f"steward {pub_key}\n", encoding="utf-8")
    sig_path = tmp_path / "attestation.sig"
    sig_path.write_text(signed.signature, encoding="utf-8")
    payload_path = tmp_path / "attestation.signed-payload"
    payload_path.write_bytes(signed.signing_payload())

    result = subprocess.run(  # noqa: S603 - resolved executable, fixed argv, test fixture
        [
            _SSH_KEYGEN,
            "-Y",
            "verify",
            "-f",
            str(allowed_signers),
            "-I",
            "steward",
            "-n",
            "ledger-health-attestation",
            "-s",
            str(sig_path),
        ],
        stdin=payload_path.open("rb"),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "Good" in result.stderr or "Good" in result.stdout


def test_sign_attestation_raises_on_bad_key(tmp_path: Path) -> None:
    from ledger.errors import LedgerError

    archive = _seed_archive(tmp_path)
    attestation = build_attestation(archive, now=_NOW)
    with pytest.raises(LedgerError):
        sign_attestation(attestation, tmp_path / "no-such-key")


# --- publish + CLI ------------------------------------------------------------


def test_publish_attestation_writes_latest_and_dated_copy(tmp_path: Path) -> None:
    archive = _seed_archive(tmp_path)
    attestation = build_attestation(archive, now=_NOW)
    out = publish_attestation(archive, attestation)
    assert out == latest_attestation_path(archive)
    assert out.exists()
    dated = archive.store_root / "attestations" / f"{_NOW.replace(':', '-')}.json"
    assert dated.exists()
    assert dated.read_text(encoding="utf-8") == out.read_text(encoding="utf-8")


def test_cli_attest_health_publishes_unsigned_when_no_key(tmp_path: Path) -> None:
    root = tmp_path / "arc_root"
    os.environ["LEDGER_VAULT_KEY"] = _VAULT_KEY
    try:
        assert cli.main(["init", "--root", str(root), "--name", "CLI Archive"]) == 0
        assert cli.main(["attest-health", "--root", str(root), "--now", _NOW]) == 0
    finally:
        os.environ.pop("LEDGER_VAULT_KEY", None)
    published = root / "store" / "attestations" / "latest.json"
    assert published.exists()
    data = json.loads(published.read_text(encoding="utf-8"))
    assert data["fixity_ok"] is True
    assert "signature" not in data


@pytest.mark.skipif(not _HAVE_SSH_KEYGEN, reason="ssh-keygen not available")
def test_cli_attest_health_signs_with_signing_key_flag(tmp_path: Path) -> None:
    import subprocess

    key_path = tmp_path / "steward_key"
    subprocess.run(  # noqa: S603 - resolved executable, fixed argv, test fixture
        [_SSH_KEYGEN, "-t", "ed25519", "-N", "", "-C", "steward", "-f", str(key_path)],
        check=True,
        capture_output=True,
    )
    root = tmp_path / "arc_root"
    os.environ["LEDGER_VAULT_KEY"] = _VAULT_KEY
    try:
        assert cli.main(["init", "--root", str(root), "--name", "CLI Archive"]) == 0
        assert (
            cli.main(
                [
                    "attest-health",
                    "--root",
                    str(root),
                    "--now",
                    _NOW,
                    "--signing-key",
                    str(key_path),
                ]
            )
            == 0
        )
    finally:
        os.environ.pop("LEDGER_VAULT_KEY", None)
    published = root / "store" / "attestations" / "latest.json"
    data = json.loads(published.read_text(encoding="utf-8"))
    assert data["signature"]["format"] == "ssh"
    assert "BEGIN SSH SIGNATURE" in data["signature"]["value"]


# --- server integration: /proof and /proof/attestation.json -----------------


def _get(base: str, path: str) -> tuple[int, str]:
    req = urllib.request.Request(f"{base}{path}")  # noqa: S310 - loopback
    try:
        with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310
            return int(r.status), r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return int(e.code), e.read().decode("utf-8")


def test_proof_attestation_route_not_yet_published(tmp_path: Path) -> None:
    archive = _seed_archive(tmp_path)
    httpd = make_server(archive, host="127.0.0.1", port=0)
    port = int(httpd.server_address[1])
    base = f"http://127.0.0.1:{port}"
    sink = StringIO()
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    with redirect_stderr(sink), redirect_stdout(sink):
        thread.start()
        try:
            status, body = _get(base, "/proof/attestation.json")
            assert status == 404
            assert json.loads(body)["status"] == "not_published"

            status, body = _get(base, "/proof")
            assert status == 200
            assert "No transparency attestation has been published yet" in body
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()


def test_proof_attestation_route_serves_published_attestation(tmp_path: Path) -> None:
    archive = _seed_archive(tmp_path)
    attestation = build_attestation(archive, now=_NOW)
    publish_attestation(archive, attestation)

    httpd = make_server(archive, host="127.0.0.1", port=0)
    port = int(httpd.server_address[1])
    base = f"http://127.0.0.1:{port}"
    sink = StringIO()
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    with redirect_stderr(sink), redirect_stdout(sink):
        thread.start()
        try:
            status, body = _get(base, "/proof/attestation.json")
            assert status == 200
            data = json.loads(body)
            assert data["fixity_ok"] is True
            assert data["chain_head_summary"] == attestation.chain_head_summary
            assert _SENTINEL not in body

            status, body = _get(base, "/proof")
            assert status == 200
            assert _SENTINEL not in body
            assert "/proof/attestation.json" in body
            assert "passed its most recent fixity check" in body
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()
