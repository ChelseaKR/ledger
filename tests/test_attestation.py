"""Tests for public transparency attestations on ``/proof`` (EXP-01).

Pins three things: the attestation is deterministic and reproducible for a fixed
archive state; ``chain_head_summary`` changes when (and only when) the archive's
recorded history actually changes, so a third party can detect a rewrite; and the
published document never leaks a contributor identity or an absolute count
(no-outing / P2-2, the same convention checked throughout ``test_server_remediation.py``).
"""

from __future__ import annotations

import hashlib
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
    FixityDisclosure,
    HealthAttestation,
    build_attestation,
    chain_head_summary,
    latest_attestation_path,
    publish_attestation,
    sign_attestation,
)
from ledger.config import Config
from ledger.errors import LedgerError
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


def test_empty_archive_attestation_says_nothing_to_verify_and_is_deterministic(
    tmp_path: Path,
) -> None:
    """#205: an archive with nothing in it does not attest that it passed.

    This asserted ``fixity_ok is True`` with the comment "vacuously true: nothing
    to fail", which was an accurate description of the defect.
    """
    config = Config.default("Empty Archive", tmp_path / "arc")
    archive = Archive.init(config)
    a1 = build_attestation(archive, now=_NOW)
    a2 = build_attestation(archive, now=_NOW)
    assert a1.fixity is FixityDisclosure.NOTHING_TO_VERIFY
    assert a1.fixity_ok is False
    assert a1.chain_head_summary == a2.chain_head_summary  # reproducible
    assert a1.schema_version == ATTESTATION_SCHEMA_VERSION == 2


def test_the_empty_archive_chain_head_was_already_a_public_constant(tmp_path: Path) -> None:
    """The measurement #205's decision rests on, kept as a test so it cannot rot.

    #205 weighed saying "nothing to verify" against the no-outing rule's refusal
    to publish absolute counts. This is why the weighing came out the way it did:
    the attestation *already* told anyone that the archive was empty, because
    ``chain_head_summary`` over no logs is ``sha256("[]")`` — the same digest for
    every empty ledger archive, whatever it is called. ``fixity_ok: true`` was
    therefore buying no privacy. If this ever stops holding, the reasoning in
    ``build_attestation``'s docstring must be revisited, not merely this test.
    """
    heads = {
        chain_head_summary(Archive.init(Config.default(name, tmp_path / f"arc{index}")))
        for index, name in enumerate(("Empty Archive", "A Different Name", "z"))
    }
    assert heads == {hashlib.sha256(b"[]").hexdigest()}


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
    #
    # Schema 2 adds exactly one key, `fixity` (#205), and it is a word from a
    # closed vocabulary of four, not a count. The one absolute fact it can state —
    # `nothing-to-verify`, i.e. the archive is empty — was already published by
    # `chain_head_summary`, which is `sha256("[]")` for every empty archive; see
    # test_the_empty_archive_chain_head_was_already_a_public_constant.
    assert set(data.keys()) == {
        "schema_version",
        "archive_name",
        "generated_at",
        "software_version",
        "fixity",
        "fixity_ok",
        "chain_head_summary",
    }
    assert data["fixity"] in {"verified", "failed", "could-not-verify", "nothing-to-verify"}


def test_fixity_ok_false_when_a_bag_is_corrupted(tmp_path: Path) -> None:
    archive = _seed_archive(tmp_path)
    bag_dirs = [p for p in archive.bags_dir.iterdir() if p.is_dir()]
    payload_files = list(bag_dirs[0].glob("data/*"))
    assert payload_files
    payload_files[0].write_bytes(b"corrupted bytes")
    attestation = build_attestation(archive, now=_NOW)
    assert attestation.fixity_ok is False
    assert attestation.fixity is FixityDisclosure.FAILED


def test_a_seeded_healthy_archive_still_attests_verified(tmp_path: Path) -> None:
    """The fix is to the empty case. A checked archive must not lose its pass."""
    attestation = build_attestation(_seed_archive(tmp_path), now=_NOW)
    assert attestation.fixity is FixityDisclosure.VERIFIED
    assert attestation.fixity_ok is True


def test_a_bag_that_declares_no_files_attests_could_not_verify(tmp_path: Path) -> None:
    """Bags present, one uncheckable: not a pass, not a failure, not "empty"."""
    archive = _seed_archive(tmp_path)
    bag = next(p for p in archive.bags_dir.iterdir() if p.is_dir())
    # The same hollowing `tests/test_nothing_verified.py` uses (#206). Truncating
    # the payload manifests alone is not enough: the tag manifests still carry
    # their old checksums, so the bag *fails* rather than proving nothing — which
    # is what this test first did, and why it now removes all three.
    for payload in sorted((bag / "data").rglob("*")):
        if payload.is_file():
            payload.unlink()
    for manifest in sorted(bag.glob("manifest-*.txt")):
        manifest.write_text("", encoding="utf-8")
    for tagmanifest in sorted(bag.glob("tagmanifest-*.txt")):
        tagmanifest.unlink()
    attestation = build_attestation(archive, now=_NOW)
    assert attestation.fixity is FixityDisclosure.COULD_NOT_VERIFY
    assert attestation.fixity_ok is False


# --- schema 1 documents already on disk --------------------------------------

_V1_DOCUMENT = {
    "schema_version": 1,
    "archive_name": "Older Archive",
    "generated_at": "2026-06-01T00:00:00Z",
    "software_version": "0.1.0",
    "fixity_ok": True,
    "chain_head_summary": "a" * 64,
}


def test_a_schema_1_attestation_is_still_read_and_says_nothing_it_did_not(
    tmp_path: Path,
) -> None:
    """A steward upgrades between two cron runs; `/proof` must not go blank.

    And it must not be *upgraded by guesswork* either: v1's ``fixity_ok: true``
    cannot tell a verified archive from an empty one, so it is read as
    ``unstated`` rather than ``verified``.
    """
    restored = HealthAttestation.from_json(json.dumps(_V1_DOCUMENT))
    assert restored.schema_version == 1
    assert restored.fixity is FixityDisclosure.UNSTATED


def test_a_schema_1_signature_still_covers_the_same_bytes() -> None:
    """Adding a field must not invalidate every signature published before it.

    A v1 document was signed over bytes with no ``fixity`` key. If reading it back
    and re-deriving the payload emitted one, every previously published signature
    would fail ``ssh-keygen -Y verify`` — tamper-evidence broken by an upgrade.
    """
    restored = HealthAttestation.from_json(json.dumps(_V1_DOCUMENT))
    assert restored.signing_payload() == json.dumps(
        _V1_DOCUMENT, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    assert b'fixity"' not in restored.signing_payload()
    assert b'"fixity":' not in restored.signing_payload()


def test_a_schema_2_signature_covers_the_disclosure(tmp_path: Path) -> None:
    """The new field is signed. An unsigned verdict beside a signed one is forgeable."""
    attestation = build_attestation(_seed_archive(tmp_path), now=_NOW)
    assert b'"fixity":"verified"' in attestation.signing_payload()


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
        ("schema_version", 3),
        ("fixity_ok", "false"),
        ("chain_head_summary", "not-a-digest"),
        # #205: a schema-2 document must state one of the four disclosures.
        ("fixity", "all-good"),
        ("fixity", True),
        ("fixity", "unstated"),
        ("fixity", None),
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
    # A freshly initialised archive holds nothing. #205: it publishes that, and
    # the command still exits 0 above — an empty archive is not a fault, and the
    # exit code is the alarm, not the statement.
    assert data["fixity"] == "nothing-to-verify"
    assert data["fixity_ok"] is False
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
        with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 - loopback URL we constructed for the in-process test server
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
            assert data["fixity"] == "verified"
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


def _serve_proof(archive: Archive) -> str:
    """The English body `/proof` renders for `archive`'s latest published attestation."""
    httpd = make_server(archive, host="127.0.0.1", port=0)
    base = f"http://127.0.0.1:{int(httpd.server_address[1])}"
    sink = StringIO()
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    with redirect_stderr(sink), redirect_stdout(sink):
        thread.start()
        try:
            status, body = _get(base, "/proof")
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()
    assert status == 200
    assert "/proof/attestation.json" in body, "no attested branch rendered; this proves nothing"
    return body


def test_proof_over_an_empty_archive_does_not_say_it_passed(tmp_path: Path) -> None:
    """#205, end to end: the sentence an anonymous visitor reads, not the field.

    Every other test here reads `fixity` or `fixity_ok` off the document. A `/proof`
    that went back to choosing its sentence from `fixity_ok` alone — the two-branch
    reading this issue replaced — kept every one of them green: the negative control
    that reverted it ran 161 tests and failed none. This is the test that control
    was missing.
    """
    os.environ["LEDGER_VAULT_KEY"] = _VAULT_KEY
    try:
        archive = Archive.init(Config.default("Empty Proof Archive", tmp_path / "arc"))
        publish_attestation(archive, build_attestation(archive, now=_NOW))
        body = _serve_proof(archive)
    finally:
        os.environ.pop("LEDGER_VAULT_KEY", None)
    assert "passed its most recent fixity check" not in body
    assert "did NOT pass" not in body, "an empty archive is not a damaged one either"
    assert "held no records, so there was nothing to check" in body


def test_proof_does_not_upgrade_an_older_ledgers_pass_by_guessing(tmp_path: Path) -> None:
    """A schema-1 `fixity_ok: true` is what an older ledger wrote over an empty archive.

    It cannot tell a checked archive from an empty one, so `/proof` must say that
    rather than render it as the pass it looks like.
    """
    os.environ["LEDGER_VAULT_KEY"] = _VAULT_KEY
    try:
        archive = Archive.init(Config.default("Older Proof Archive", tmp_path / "arc"))
        publish_attestation(archive, HealthAttestation.from_json(json.dumps(_V1_DOCUMENT)))
        body = _serve_proof(archive)
    finally:
        os.environ.pop("LEDGER_VAULT_KEY", None)
    assert "passed its most recent fixity check" not in body
    assert "in an older format that could not say whether anything was actually checked" in body


# --- an unreadable log must never be attested as an empty one ----------------


def test_unreadable_bag_log_refuses_to_attest_instead_of_claiming_genesis(
    tmp_path: Path,
) -> None:
    """A damaged ``premis.json`` stops the attestation; it never publishes genesis.

    ``_log_head`` documents the genesis sentinel as the value that distinguishes "no
    history yet" from any real history. Routing a *present but unreadable* log through
    the lenient reader yielded exactly that sentinel, so corrupting one bag's log made
    the archive sign a public statement that the bag had no history -- inside the one
    field (``chain_head_summary``) whose stated purpose is that two dated attestations
    catch a rollback. Unknown history is not empty history, and it must not be signed.
    """
    archive = _seed_archive(tmp_path)
    healthy = chain_head_summary(archive)

    bag = next(p for p in archive.bags_dir.iterdir() if p.is_dir())
    premis_path = bag / "premis.json"
    genesis_only = "0" * 64
    premis_path.write_text("{ truncated mid-write", encoding="utf-8")

    with pytest.raises(LedgerError, match="present but unreadable"):
        chain_head_summary(archive)
    with pytest.raises(LedgerError, match="present but unreadable"):
        build_attestation(archive, now=_NOW)

    # And the failure is not merely "it changed": the value it used to publish for a
    # damaged log was the genesis sentinel, i.e. the claim "this log is empty".
    assert healthy != genesis_only


def test_a_bag_with_no_premis_log_still_attests_as_empty(tmp_path: Path) -> None:
    """An *absent* log genuinely is no history, and must keep attesting cleanly.

    The counterpart to the test above: failing closed on damage must not turn a
    legitimately empty bag into a refusal, or the fix would just be a different lie.
    """
    archive = _seed_archive(tmp_path)
    bag = next(p for p in archive.bags_dir.iterdir() if p.is_dir())
    (bag / "premis.json").unlink()

    summary = chain_head_summary(archive)
    assert len(summary) == 64
    assert build_attestation(archive, now=_NOW).chain_head_summary == summary
