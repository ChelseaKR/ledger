"""Every surface that publishes a fixity verdict, judged over an archive with nothing in it.

#207, #218 and #219 each fixed one place where an archive holding nothing read as
an archive that had been checked and found intact. Each fix was correct and each
was found by reading code, one surface at a time, which is why there were three of
them and why #208 stayed open after two. This module replaces that with a
denominator.

**The rule.** A surface that renders a fixity verdict must render a *different*
one over an archive with no bags than over an archive whose every bag was just
re-hashed. If it renders the same thing for both, its affirmative claim is not
backed by a verification — it is `all(...)` folded over an empty sequence, which
is :data:`True` — and it must be named in :data:`_UNBACKED_BY_DESIGN` with the
reason it is allowed to stay that way.

**Why it is a census and not a list of assertions.** A list of assertions grows
when someone remembers to grow it. The load-bearing test here is
:func:`test_every_verdict_producer_is_accounted_for`, which walks the AST of
``src/ledger`` for every call to the five functions that can produce a fixity
verdict and requires each call site to be mapped — to a surface probed below, or
to a named reason it is not a claim. A new surface cannot appear without failing
this file first. That is the half #226 called "giving the rule its denominator",
in the one place in this repository where a green check over nothing is the whole
defect class.

**Both lists are self-limiting.** A surface named in :data:`_UNBACKED_BY_DESIGN`
that starts distinguishing the two archives fails, so an exemption has to earn its
place on every run and cannot outlive the decision that granted it. A call site
mapped to ``_NOT_A_CLAIM`` that moves into a summary fails the same way.

The census prints two numbers — **claims backed by a verification / claims
published** — from ``pytest_terminal_summary``, not a ``print``: on a passing test
pytest captures stdout, so a printed number would have existed and never been
read (the lesson #226 recorded).
"""

from __future__ import annotations

import ast
import json
import threading
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from ledger import cli
from ledger.access.grants import issue_grant_token
from ledger.attestation import build_attestation
from ledger.backup import verify_backup
from ledger.config import Config
from ledger.ingest import Archive
from ledger.lockdown import verify_backup_location
from ledger.models import AccessPolicy, DublinCore, Field, Record
from ledger.server import make_server
from ledger.succession import build_handoff

from .conftest import record_fixity_claim_census

_VAULT_KEY = "0123456789abcdef0123456789abcdef0123456789a="
_GRANT_SECRET = b"fixity-census-grant-secret"
_NOW = "2026-06-16T12:00:00Z"

_SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "ledger"

#: The functions that can produce a fixity verdict. Nothing in this package can
#: state that bytes were checked without going through one of them, so a call to
#: one is the definition of a verdict-producing site.
_VERDICT_PRODUCERS = frozenset(
    {"audit_fixity", "validate_bag", "audit_log_chains", "verify_backup", "overall_status"}
)


# --- the surfaces, and what each renders ------------------------------------


class _Probes:
    """One archive, served, with a probe per surface that publishes a verdict."""

    def __init__(self, archive: Archive, root: Path, tmp_path: Path, label: str) -> None:
        self.archive = archive
        self.root = root
        self.tmp_path = tmp_path
        self.label = label
        grants = tmp_path / f"{label}-grants.json"
        grants.write_text(
            json.dumps(
                {"warden": {"levels": ["public", "community", "stewards"], "is_steward": True}}
            ),
            encoding="utf-8",
        )
        self._httpd = make_server(archive, host="127.0.0.1", port=0, grants_path=grants)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        self.base = f"http://127.0.0.1:{self._httpd.server_address[1]}"

    def close(self) -> None:
        self._httpd.shutdown()
        self._thread.join(timeout=5)
        self._httpd.server_close()

    def _get(self, path: str, *, steward: bool = False) -> str:
        request = urllib.request.Request(f"{self.base}{path}")  # noqa: S310 - loopback
        if steward:
            request.add_header("X-Ledger-Grant", issue_grant_token("warden", _GRANT_SECRET))
        try:
            with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - loopback URL built above
                return f"{response.status} {response.read().decode('utf-8')}"
        except urllib.error.HTTPError as error:
            return f"{error.code} {error.read().decode('utf-8')}"

    # Each probe returns the verdict this surface renders, as a string. Volatile
    # parts (paths, live chain heads) are dropped: a surface must be judged on
    # what it *says about fixity*, or a moving timestamp would make every surface
    # look like it distinguishes the two archives.

    def healthz_anonymous(self) -> str:
        return self._get("/healthz")

    def healthz_steward(self) -> str:
        code, _, body = self._get("/healthz", steward=True).partition(" ")
        payload = json.loads(body)
        payload.pop("chain_head", None)
        return f"{code} {json.dumps(payload, sort_keys=True)}"

    def status_anonymous(self) -> str:
        return _status_prose(self._get("/status"))

    def status_steward(self) -> str:
        return _status_prose(self._get("/status", steward=True))

    def attestation_fixity_ok(self) -> str:
        return json.dumps(build_attestation(self.archive, now=_NOW).fixity_ok)

    def handoff_manifest_fields(self) -> str:
        manifest = build_handoff(self.archive, successor=None, now=_NOW).to_dict()
        return json.dumps(
            {key: manifest[key] for key in ("all_fixity_ok", "fixity_status")}, sort_keys=True
        )

    def handoff_runbook(self) -> str:
        runbook = build_handoff(self.archive, successor=None, now=_NOW).runbook()
        return next(line for line in runbook.splitlines() if line.startswith("Records:"))

    def cli_audit(self, capsys: pytest.CaptureFixture[str]) -> str:
        cli.main(["audit", "--root", str(self.root)])
        return capsys.readouterr().out.splitlines()[-1]

    def cli_export_drive(self, capsys: pytest.CaptureFixture[str]) -> str:
        out = self.tmp_path / f"{self.label}-drive"
        cli.main(
            [
                "export-drive",
                "--root",
                str(self.root),
                "--out",
                str(out),
                "--now",
                _NOW,
            ]
        )
        # The output directory is volatile; the claim is everything after it.
        return capsys.readouterr().out.split("; ", 1)[1].strip()

    def verify_backup_report(self) -> str:
        # `verify_backup` takes a directory holding `store/` and `identity.vault`
        # and re-points the config at it, which is exactly the archive root here:
        # a restored backup and a live root are the same shape by construction.
        report = verify_backup(self.root)
        return f"ok={report.ok} status={report.status} reason={report.reason!r}"

    def lockdown_replica_check(self) -> str:
        verification = verify_backup_location(self.root)
        return f"ok={verification.ok} status={verification.status} reason={verification.reason!r}"


def _status_prose(body: str) -> str:
    """The headline and detail sentence ``/status`` renders, with markup stripped."""
    import re

    matches = re.findall(r"<p><strong>(.*?)</strong></p>\s*<p>(.*?)</p>", body, re.S)
    return " | ".join(f"{head.strip()} :: {detail.strip()}" for head, detail in matches)


#: Probe name -> how to read it. ``capsys`` probes take the capture fixture.
_SURFACES: dict[str, str] = {
    "GET /healthz (anonymous)": "healthz_anonymous",
    "GET /healthz (steward)": "healthz_steward",
    "GET /status (anonymous)": "status_anonymous",
    "GET /status (steward)": "status_steward",
    "attestation fixity_ok (published at /proof)": "attestation_fixity_ok",
    "hand-off manifest fixity fields": "handoff_manifest_fields",
    "hand-off runbook sentence": "handoff_runbook",
    "ledger audit summary": "cli_audit",
    "ledger export-drive summary": "cli_export_drive",
    "verify_backup report": "verify_backup_report",
    "lockdown replica verification": "lockdown_replica_check",
}

_CAPSYS_PROBES = frozenset({"cli_audit", "cli_export_drive"})


#: A surface here renders the same verdict over an archive with nothing in it as
#: over one whose every bag was just re-hashed. Each entry is a decision with a
#: reason, and each is self-limiting: if the surface starts distinguishing the
#: two, this file fails until the entry is removed.
_UNBACKED_BY_DESIGN: dict[str, str] = {
    "GET /healthz (anonymous)": (
        "Anti-enumeration, and the one case where honesty and the no-outing rule "
        "genuinely conflict. Every other route to `all_verified: false` also "
        "answers `degraded` with a 503, so `200 + ok + all_verified: false` would "
        "be reachable ONLY by an archive holding nothing at all — an anonymous "
        "caller would learn the archive is empty, which is the absolute count the "
        "gated block below it exists to withhold. The honest verdict is served to "
        "the caller permitted to hear it: a steward (or a monitor holding a "
        "provisioned grant) reads `fixity.status` beside `bags_audited`. "
        "tests/test_healthz_says_nothing_new_to_an_outsider.py asserts the "
        "anonymous body is byte-identical over the two archives, which is this "
        "entry's other half: the leak and the lie cannot both be closed here, and "
        "this repository closes the leak."
    ),
    "attestation fixity_ok (published at /proof)": (
        "Issue #205, and the same trade as the row above: saying 'there was "
        "nothing to check' in a signed, public document states to anyone that the "
        "archive holds zero records. It also needs ATTESTATION_SCHEMA_VERSION 2. "
        "That is an owner's policy call about what ledger publishes about itself, "
        "recorded at #205 and pinned by tests/test_audit_missing_bags.py."
    ),
}


# --- fixtures ---------------------------------------------------------------


def _archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, records: int, name: str
) -> tuple[Archive, Path]:
    monkeypatch.setenv("LEDGER_VAULT_KEY", _VAULT_KEY)
    monkeypatch.setenv("LEDGER_GRANT_SECRET", _GRANT_SECRET.decode())
    root = tmp_path / name
    archive = Archive.init(Config.default("Fixity Census Archive", root))
    for index in range(records):
        payload = tmp_path / f"{name}-doc{index}.txt"
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
    return archive, root


@pytest.fixture
def census(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> Iterator[dict[str, tuple[str, str]]]:
    """Every surface's verdict over an empty archive and over a healthy one."""
    empty = _Probes(*_archive(tmp_path, monkeypatch, records=0, name="empty"), tmp_path, "empty")
    healthy = _Probes(*_archive(tmp_path, monkeypatch, records=2, name="healthy"), tmp_path, "heal")
    try:
        readings: dict[str, tuple[str, str]] = {}
        for label, attr in _SURFACES.items():
            rendered: list[str] = []
            for probes in (empty, healthy):
                probe: Callable[..., str] = getattr(probes, attr)
                rendered.append(probe(capsys) if attr in _CAPSYS_PROBES else probe())
            readings[label] = (rendered[0], rendered[1])
        yield readings
    finally:
        empty.close()
        healthy.close()


# --- the census -------------------------------------------------------------


def test_the_census_of_published_fixity_claims(census: dict[str, tuple[str, str]]) -> None:
    """Two numbers: claims backed by a verification, of claims published.

    A claim is *backed* when the surface cannot say the same affirmative thing
    over an archive with no bags as over one whose bags were just re-hashed. A
    claim that reads identically for both was produced by a fold over an empty
    sequence and states nothing about any byte.
    """
    unbacked = {label for label, (empty, healthy) in census.items() if empty == healthy}
    backed = len(census) - len(unbacked)
    record_fixity_claim_census(backed=backed, published=len(census), unbacked=sorted(unbacked))

    undeclared = unbacked - set(_UNBACKED_BY_DESIGN)
    assert not undeclared, (
        "these surfaces render the same fixity verdict over an archive with nothing "
        "in it as over a verified one, and no decision records why: "
        f"{sorted(undeclared)}"
    )


def test_an_exemption_that_stopped_being_needed_fails(census: dict[str, tuple[str, str]]) -> None:
    """Self-limiting: a named exemption must still be a real one on every run.

    Without this, a surface fixed in some later change keeps its exemption
    forever, and the list stops describing the code it claims to describe.
    """
    for label, reason in _UNBACKED_BY_DESIGN.items():
        assert label in census, f"{label!r} is exempted but no longer probed"
        empty, healthy = census[label]
        assert empty == healthy, (
            f"{label!r} now distinguishes an empty archive from a verified one, so "
            f"its exemption is stale and must be deleted. It reads {empty!r} over "
            f"the empty archive and {healthy!r} over the healthy one. The reason "
            f"recorded for the exemption was: {reason}"
        )


def test_the_surfaces_fixed_for_208_are_held(census: dict[str, tuple[str, str]]) -> None:
    """The specific sentences #208 was filed about, pinned to their fixed wording.

    Each of these rendered an affirmative claim over an archive with nothing in
    it. They are asserted by content, not merely by "differs", so a later change
    that makes them differ *wrongly* — by breaking the healthy case instead of
    fixing the empty one — cannot pass as a fix.
    """
    empty_runbook, healthy_runbook = census["hand-off runbook sentence"]
    assert "nothing was verified" in empty_runbook
    assert "All bags verified intact" in healthy_runbook

    empty_manifest, healthy_manifest = census["hand-off manifest fixity fields"]
    assert json.loads(empty_manifest)["fixity_status"] == "could-not-verify"
    assert json.loads(healthy_manifest)["fixity_status"] == "verified"

    empty_drive, healthy_drive = census["ledger export-drive summary"]
    assert "0 of 0 bag(s) verified" in empty_drive
    assert "no bags to verify" in empty_drive
    assert "2 of 2 bag(s) verified" in healthy_drive
    assert "all bags verified" in healthy_drive

    empty_audit, healthy_audit = census["ledger audit summary"]
    assert empty_audit.startswith("NOTHING AUDITED")
    assert healthy_audit.startswith("PASS")

    empty_status, healthy_status = census["GET /status (anonymous)"]
    assert "holds nothing yet" in empty_status
    assert "healthy" in healthy_status


# --- the denominator --------------------------------------------------------

#: Every call to a verdict producer in ``src/ledger``, keyed by (module, enclosing
#: function, called name), mapped to the surface it feeds. A site whose verdict
#: never reaches a reader is mapped to ``_NOT_A_CLAIM`` with the reason.
_NOT_A_CLAIM = "_NOT_A_CLAIM"

_VERDICT_CALL_SITES: dict[tuple[str, str, str], str] = {
    ("attestation.py", "build_attestation", "audit_fixity"): (
        "attestation fixity_ok (published at /proof)"
    ),
    ("backup.py", "verify_backup", "audit_fixity"): "verify_backup report",
    ("backup.py", "restore_backup", "verify_backup"): "verify_backup report",
    ("cli.py", "_cmd_audit", "audit_fixity"): "ledger audit summary",
    ("cli.py", "_cmd_audit", "audit_log_chains"): "ledger audit summary",
    ("cli.py", "_cmd_verify_backup", "verify_backup"): "verify_backup report",
    ("export_drive.py", "build_export_drive", "validate_bag"): "ledger export-drive summary",
    ("export_drive.py", "build_export_drive", "overall_status"): "ledger export-drive summary",
    ("lockdown.py", "verify_backup_location", "audit_fixity"): "lockdown replica verification",
    ("server.py", "_handle_healthz", "audit_fixity"): "GET /healthz (anonymous)",
    ("server.py", "_handle_healthz", "overall_status"): "GET /healthz (steward)",
    ("server.py", "_handle_status", "audit_fixity"): "GET /status (anonymous)",
    ("server.py", "_handle_status", "overall_status"): "GET /status (steward)",
    ("succession.py", "build_handoff", "audit_fixity"): "hand-off manifest fixity fields",
    # Single-bag checks. Each judges one named bag that the caller already holds,
    # so there is no sequence to fold and no empty case to be vacuously true
    # about: the answer is about that bag or it raises.
    ("drill.py", "_source_bag_still_validates", "validate_bag"): _NOT_A_CLAIM,
    ("ingest.py", "audit_fixity", "validate_bag"): _NOT_A_CLAIM,
    ("replicate.py", "replicate_bag", "validate_bag"): _NOT_A_CLAIM,
    ("replicate.py", "verify_replicas", "validate_bag"): _NOT_A_CLAIM,
    ("replicate.py", "heal", "validate_bag"): _NOT_A_CLAIM,
    ("replicate.py", "recover_sealed_bag", "validate_bag"): _NOT_A_CLAIM,
}


def _verdict_call_sites() -> set[tuple[str, str, str]]:
    """Walk ``src/ledger`` for every call to a verdict producer."""
    found: set[tuple[str, str, str]] = set()
    for path in sorted(_SOURCE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for call in ast.walk(node):
                if not isinstance(call, ast.Call):
                    continue
                func = call.func
                if isinstance(func, ast.Attribute):
                    name = func.attr
                elif isinstance(func, ast.Name):
                    name = func.id
                else:
                    continue
                if name in _VERDICT_PRODUCERS:
                    found.add((str(path.relative_to(_SOURCE_ROOT)), node.name, name))
    return found


def test_every_verdict_producer_is_accounted_for() -> None:
    """A new fixity claim cannot reach a reader without being judged here.

    This is what makes the numbers above a coverage figure rather than a tally of
    whatever someone happened to think of. Every call to
    :data:`_VERDICT_PRODUCERS` anywhere in the package is mapped to a probed
    surface or to an explicit reason it is not a claim, and both directions are
    checked — an unmapped call site fails, and so does a mapping for a call site
    that no longer exists.
    """
    found = _verdict_call_sites()
    mapped = set(_VERDICT_CALL_SITES)
    assert found - mapped == set(), (
        "these calls can produce a fixity verdict and nothing says where it is "
        f"rendered or why it is not a claim: {sorted(found - mapped)}"
    )
    assert mapped - found == set(), (
        "these call sites are mapped but no longer exist; the map is describing "
        f"code that is gone: {sorted(mapped - found)}"
    )


def test_every_mapped_surface_is_actually_probed() -> None:
    """A call site cannot be mapped to a surface the census does not read."""
    targets = {target for target in _VERDICT_CALL_SITES.values() if target != _NOT_A_CLAIM}
    assert targets <= set(_SURFACES), (
        f"mapped to a surface with no probe: {sorted(targets - set(_SURFACES))}"
    )


def test_no_exemption_names_a_surface_that_is_not_probed() -> None:
    assert set(_UNBACKED_BY_DESIGN) <= set(_SURFACES)
