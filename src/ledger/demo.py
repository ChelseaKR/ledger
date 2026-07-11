"""The executable proof of ledger's headline guarantee.

This is a self-contained, deterministic, end-to-end run in a throwaway temporary
directory. It exists to *demonstrate*, not merely assert, the one promise the
whole project is built around:

    Holding a record — browsing it, reading its JSON or HTML, checking the
    archive's health, or inspecting the files on disk — can never out the person
    who contributed it.

The script ingests a short synthetic oral history together with a deliberately
loud **sentinel** contributor identity (a string that would be unmistakable if it
ever leaked), seals that identity and a ``real_names`` field while publishing the
``story`` field, replicates the bag to a verified second location, and then
renders *every public surface* and asserts the sentinel appears in **none** of
them. Finally it tightens consent and shows the change recorded as a PREMIS event
and reflected in disclosure.

Determinism: the sentinel token and every timestamp are fixed constants, and the
in-process server is driven over loopback, so the run is reproducible and its
"PASS" is meaningful rather than incidental (reproducibility, provability).
"""

from __future__ import annotations

import io
import json
import os
import threading
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import mkdtemp

from ledger.access.grants import anonymous, build_grant, issue_grant_token, steward
from ledger.config import Config, StorageLocation
from ledger.identity import ContributorIdentity
from ledger.ingest import Archive, serialize_record
from ledger.metadata.premis import PremisLog
from ledger.models import (
    AccessPolicy,
    DublinCore,
    Field,
    PremisEventType,
    Record,
)
from ledger.moderate import change_consent
from ledger.replicate import replicate_bag, verify_replicas
from ledger.server import make_server

# The contributor IDENTITY sentinel. This is the headline guarantee: this value
# is sealed into the encrypted vault and must therefore appear on NO surface at
# all — not a public read path, not a steward's authorized view, not the on-disk
# record manifest, not a log line. If any of these substrings ever surfaces, the
# no-outing guarantee is broken. Fixed tokens keep the run deterministic.
_SENTINEL_NAME = "SENTINEL-IDENTITY-DO-NOT-LEAK-7Q4X"
_SENTINEL_CONTACT = "identity-leak-probe@sentinel.invalid"
# Every surface the identity sentinel must be absent from is checked against these.
_IDENTITY_SENTINELS: tuple[str, ...] = (_SENTINEL_NAME, _SENTINEL_CONTACT)

# A separate SEALED-FIELD sentinel. Unlike the identity, a sealed field legitimately
# lives at rest in the record manifest and is visible to an authorized steward —
# sealing is enforced at *disclosure*, not at storage. So this value proves
# SELECTIVE DISCLOSURE: it must be absent from the ANONYMOUS public view, yet
# present for a steward. It is deliberately distinct from the identity tokens so
# the two guarantees are tested independently.
_SENTINEL_SEALED_FIELD = "SEALED-FIELD-PRIVATE-NAMES-9Z2K"

# Fixed timestamps so the bag, manifests, and events are byte-reproducible.
_NOW_INGEST = "2026-06-16T12:00:00Z"
_NOW_CONSENT = "2026-06-16T12:05:00Z"
# A fixed secret so the scripted proof can mint the steward capability token it
# uses to exercise the authenticated read path deterministically. Not a real
# secret — production supplies one via LEDGER_GRANT_SECRET.
_GRANT_SECRET = "demo-grant-secret-not-for-production"  # noqa: S105 - demo fixture

_RULE = "=" * 70


def _header(title: str) -> None:
    """Print a clear section header so the narrative is easy to follow (usability)."""
    print()
    print(_RULE)
    print(f"  {title}")
    print(_RULE)


def _synthetic_story_path(workdir: Path) -> Path:
    """Write a short synthetic oral-history text file and return its path.

    The text is benign and identity-free; the contributor's name lives only in the
    sentinel identity that gets sealed into the vault, never in the payload.
    """
    story = (
        "We met at the community center on Thursdays. Someone always brought tea, "
        "and we kept a shoebox of phone numbers for the nights when the clinic was "
        "closed. This is how we looked after each other when no one else would.\n"
    )
    path = workdir / "oral-history.txt"
    path.write_text(story, encoding="utf-8", newline="\n")
    return path


def _http_get(host: str, port: int, path: str, *, subject: str | None = None) -> str:
    """Fetch ``path`` from the in-process server and return the decoded body.

    Optionally names a pre-provisioned grant subject via the ``X-Ledger-Grant``
    header, exactly as a real client would, so the proof exercises the genuine
    request path rather than a shortcut (fidelity).
    """
    url = f"http://{host}:{port}{path}"
    request = urllib.request.Request(url)  # noqa: S310 - loopback URL we constructed
    if subject is not None:
        request.add_header("X-Ledger-Grant", subject)
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
        raw: bytes = response.read()
    return raw.decode("utf-8")


def _assert_free_of(label: str, text: str, sentinels: tuple[str, ...]) -> None:
    """Raise if any of ``sentinels`` appears in ``text``; else report it clean.

    The exception names only the *surface* that leaked, never the leaked value in
    context, so even a failure message stays no-outing-clean (safety).
    """
    for sentinel in sentinels:
        if sentinel in text:
            raise RuntimeError(f"NO-OUTING VIOLATION: a sealed value appeared on surface {label!r}")
    print(f"  clean: {label}")


def _require(condition: bool, message: str) -> None:
    """Raise :class:`RuntimeError` if ``condition`` is false (a non-stripping check).

    The demo's invariants must hold even when Python runs with ``-O`` (which would
    silence a bare ``assert``), so the proof uses an explicit raise — the whole
    point of this script is that its checks cannot be quietly disabled.
    """
    if not condition:
        raise RuntimeError(message)


def _build_demo_record(config: Config) -> Record:
    """Build the synthetic oral-history record the proof ingests.

    The ``story`` field is public; ``real_names`` carries the sealed-field sentinel
    and is sealed-until, so the record exercises both selective disclosure and the
    no-outing guarantee in one object. Kept as a helper so :func:`main` reads as a
    sequence of named steps rather than one long block.
    """
    return Record(
        title="Thursday nights at the center",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(
            title=["Thursday nights at the center"],
            description=["A short oral history of weekly mutual-aid gatherings."],
            publisher=[config.archive_name],
            type=["oral history"],
            language=["en"],
        ),
        fields=[
            Field(
                name="story",
                value="We met at the community center on Thursdays.",
                policy=AccessPolicy.PUBLIC,
            ),
            Field(
                name="real_names",
                value=f"private participant roster: {_SENTINEL_SEALED_FIELD}",
                policy=AccessPolicy.SEALED_UNTIL,
            ),
        ],
        content_warnings=["outing"],
    )


@dataclass(frozen=True)
class _PublicSurfaces:
    """Every public surface the no-outing proof renders, plus the captured logs.

    Grouping them in one value keeps :func:`main` legible: it gathers the surfaces
    in one call, then asserts the sentinel is absent from each.
    """

    steward_record_html: str
    steward_record_json: str
    anon_browse_html: str
    anon_record_html: str
    anon_search_html: str
    anon_records_json: str
    anon_record_json: str
    healthz: str
    server_logs: str


def _collect_public_surfaces(archive: Archive, grants_path: Path, rid: str) -> _PublicSurfaces:
    """Drive the in-process server over loopback and return every public surface.

    A steward grant (the *most* privileged read-path viewer) and the anonymous
    public both fetch the record, and all server log output is captured so the proof
    can assert no sentinel reaches a log either. The server is always shut down and
    closed, even on error (resource hygiene).
    """
    # The steward header is now an authenticated capability token, so the demo mints
    # a real signed token under a fixed demo secret — exercising the genuine
    # authenticated request path rather than a bypass (fidelity). In production the
    # secret arrives via LEDGER_GRANT_SECRET, never on disk.
    os.environ["LEDGER_GRANT_SECRET"] = _GRANT_SECRET
    steward_token = issue_grant_token("steward-1", _GRANT_SECRET.encode("utf-8"))

    httpd = make_server(archive, host="127.0.0.1", port=0, grants_path=grants_path)
    raw_host, port = httpd.server_address[0], httpd.server_address[1]
    host = raw_host.decode("ascii") if isinstance(raw_host, (bytes, bytearray)) else str(raw_host)
    port = int(port)

    log_buffer = io.StringIO()
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    try:
        with redirect_stderr(log_buffer), redirect_stdout(log_buffer):
            server_thread.start()
            surfaces = _PublicSurfaces(
                steward_record_html=_http_get(
                    host, port, f"/record/{rid}?proceed=1", subject=steward_token
                ),
                steward_record_json=_http_get(
                    host, port, f"/api/record/{rid}", subject=steward_token
                ),
                anon_browse_html=_http_get(host, port, "/"),
                anon_record_html=_http_get(host, port, f"/record/{rid}?proceed=1"),
                anon_search_html=_http_get(host, port, "/search?q=Thursday"),
                anon_records_json=_http_get(host, port, "/api/records"),
                anon_record_json=_http_get(host, port, f"/api/record/{rid}"),
                healthz=_http_get(host, port, "/healthz"),
                server_logs="",  # filled in below, after the buffer is final
            )
            httpd.shutdown()
        server_thread.join(timeout=5)
    finally:
        httpd.server_close()
    # Replace with the now-complete captured log text (frozen dataclass -> rebuild).
    return replace(surfaces, server_logs=log_buffer.getvalue())


def main() -> int:
    """Run the scripted end-to-end no-outing proof; return ``0`` on success.

    Returns non-zero only if a sentinel identity is found on any public surface,
    which would mean the headline guarantee is broken. Every artifact is created
    under a fresh :func:`tempfile.mkdtemp` directory so the run leaves the host
    untouched (idempotence, affordability).
    """
    workdir = Path(mkdtemp(prefix="ledger-demo-"))
    root = workdir / "archive"
    mirror = workdir / "mirror"
    mirror.mkdir(parents=True, exist_ok=True)

    print("ledger demo — the executable proof of the no-outing guarantee")
    print(f"working directory: {workdir}")

    # --- 1. Init -----------------------------------------------------------
    _header("1. Initialize an archive")
    config = Config.default("Demo Community Archive", root)
    # A fixed key so the run is self-contained and deterministic; in production the
    # key arrives via the LEDGER_VAULT_KEY environment variable, never on disk.
    vault_key = b"0123456789abcdef0123456789abcdef0123456789a="
    archive = Archive.init(config)
    print(f"  archive: {config.archive_name!r}")
    print(f"  store:   {config.store_root}")
    print(f"  default policy (narrowest by default): {config.default_policy.value}")

    # --- 2. Ingest with a sealed sentinel identity -------------------------
    _header("2. Ingest a synthetic oral history WITH a sealed sentinel identity")
    story_path = _synthetic_story_path(workdir)
    record = _build_demo_record(config)
    identity = ContributorIdentity(name=_SENTINEL_NAME, contact=_SENTINEL_CONTACT)
    aip = archive.ingest(
        {story_path.name: story_path},
        record,
        identity=identity,
        vault_key=vault_key,
        agent="demo-steward",
        now=_NOW_INGEST,
    )
    print(f"  record_id: {record.record_id}")
    print(f"  bag:       {aip.bag.path}")
    # Print ONLY the opaque ref — proof the name was replaced by a token.
    print(f"  identity_ref (opaque token, NOT a name): {record.identity_ref}")
    ref = record.identity_ref
    if ref is None:
        raise RuntimeError("ingest did not seal an identity ref")
    _require(_SENTINEL_NAME not in ref, "the identity ref leaked the contributor name")

    # --- 3. Replicate to a verified second location ------------------------
    _header("3. Replicate the bag to a second location and verify it")
    mirror_loc = StorageLocation(name="mirror-1", path=str(mirror), kind="mirror")
    event = replicate_bag(aip.bag.path, mirror_loc, agent="demo-steward", now=_NOW_INGEST)
    print(f"  replication event: {event.event_type.value} / {event.outcome}")
    statuses = verify_replicas(record.record_id, [mirror_loc])
    for status in statuses:
        print(
            f"  replica {status.location!r}: ok={status.ok} "
            f"({status.report.checked} file(s) checked)"
        )
    _require(all(status.ok for status in statuses), "a replica failed verification")

    # --- 4. NO-OUTING PROOF: render every public surface -------------------
    _header("4. NO-OUTING PROOF: render every public surface, assert no leak")

    # Provision a grants file so a server request can authenticate as a steward —
    # the *most* privileged read-path viewer. Even a steward must not see identity.
    grants_path = root / "grants.json"
    grants_path.write_text(
        json.dumps(
            {
                "steward-1": {
                    "levels": ["public", "community", "stewards"],
                    "is_steward": True,
                }
            }
        ),
        encoding="utf-8",
    )

    rid = record.record_id
    # Render every public HTTP surface — as the most-privileged steward and as the
    # anonymous public — and capture all server logs, in one helper.
    surfaces = _collect_public_surfaces(archive, grants_path, rid)

    # In-process disclose/browse outputs, as steward and as the anonymous public.
    steward_grant = steward("demo-steward")
    steward_disclosed = archive.disclose(rid, steward_grant, now=_NOW_INGEST)
    steward_disclosed_json = json.dumps(
        steward_disclosed.to_dict(), sort_keys=True, ensure_ascii=False
    )
    anon_disclosed = archive.disclose(rid, anonymous(), now=_NOW_INGEST)
    anon_disclosed_json = json.dumps(anon_disclosed.to_dict(), sort_keys=True, ensure_ascii=False)

    bag_dir = aip.bag.path
    on_disk = {
        "bag-info.txt": (bag_dir / "bag-info.txt").read_text(encoding="utf-8"),
        "record.json": aip.record_path.read_text(encoding="utf-8"),
        "dublincore.json": aip.dc_path.read_text(encoding="utf-8"),
        "premis.json": aip.premis_path.read_text(encoding="utf-8"),
        "manifest-sha256.txt": (bag_dir / "manifest-sha256.txt").read_text(encoding="utf-8"),
        "vault": archive.vault_path.read_text(encoding="utf-8"),
    }

    # (a) The contributor IDENTITY must be absent from EVERY surface — including a
    #     steward's authorized view, the on-disk record manifest, and all logs. Its
    #     only home is the encrypted vault (where it exists solely as ciphertext).
    print("  (a) contributor identity must be absent from EVERY surface:")
    _assert_free_of(
        "record HTML (as steward, proceeded)", surfaces.steward_record_html, _IDENTITY_SENTINELS
    )
    _assert_free_of(
        "/api/record/{id} JSON (as steward)", surfaces.steward_record_json, _IDENTITY_SENTINELS
    )
    _assert_free_of("disclose() JSON (as steward)", steward_disclosed_json, _IDENTITY_SENTINELS)
    _assert_free_of("browse HTML (anonymous)", surfaces.anon_browse_html, _IDENTITY_SENTINELS)
    _assert_free_of(
        "record HTML (anonymous, proceeded)", surfaces.anon_record_html, _IDENTITY_SENTINELS
    )
    _assert_free_of("search HTML (anonymous)", surfaces.anon_search_html, _IDENTITY_SENTINELS)
    _assert_free_of(
        "/api/records JSON (anonymous)", surfaces.anon_records_json, _IDENTITY_SENTINELS
    )
    _assert_free_of(
        "/api/record/{id} JSON (anonymous)", surfaces.anon_record_json, _IDENTITY_SENTINELS
    )
    _assert_free_of("/healthz JSON", surfaces.healthz, _IDENTITY_SENTINELS)
    _assert_free_of("captured server log output", surfaces.server_logs, _IDENTITY_SENTINELS)
    for name, text in on_disk.items():
        _assert_free_of(f"on-disk artifact {name}", text, _IDENTITY_SENTINELS)

    # (b) The SEALED field proves selective disclosure: hidden from the anonymous
    #     public on every read path, yet visible to an authorized steward.
    print("  (b) sealed field must be absent from the ANONYMOUS public view:")
    _assert_free_of("record HTML (anonymous)", surfaces.anon_record_html, (_SENTINEL_SEALED_FIELD,))
    _assert_free_of(
        "/api/record/{id} JSON (anonymous)", surfaces.anon_record_json, (_SENTINEL_SEALED_FIELD,)
    )
    _assert_free_of("disclose() JSON (anonymous)", anon_disclosed_json, (_SENTINEL_SEALED_FIELD,))
    _require(
        _SENTINEL_SEALED_FIELD in steward_disclosed_json,
        "the sealed field should be visible to an authorized steward (selective disclosure)",
    )
    _require(
        "real_names" in anon_disclosed.redactions,
        "the anonymous view should honestly name the withheld sealed field",
    )
    print("  visible to steward, honestly listed as 'Withheld' to the public")

    # Positive control: the identity IS retrievable, but only with an explicit
    # identity-unseal grant naming the ref — proving the seal is real, not absence.
    unseal_grant = build_grant("authorized-investigator", identity_unseal=[ref])
    resolved = archive.resolve_identity(rid, unseal_grant)
    _require(
        resolved.name == _SENTINEL_NAME,
        "identity did not resolve under an explicit unseal grant",
    )
    print("  positive control: identity resolves ONLY under an explicit unseal grant")

    print()
    print("PASS: contributor identity absent from every public surface")

    # --- 5. Consent change recorded and reflected in disclosure ------------
    _header("5. Process a consent change; show it recorded and reflected")
    anon_before = archive.browse(anonymous(), now=_NOW_CONSENT)
    print(
        f"  before: default policy = {archive.get(rid).default_policy.value}; "
        f"anonymous can list it = {any(r.record_id == rid for r in anon_before)}"
    )

    updated, consent_event, action = change_consent(
        archive.get(rid),
        AccessPolicy.STEWARDS,
        actor="demo-steward",
        reason="contributor asked to tighten visibility to stewards only",
        now=_NOW_CONSENT,
    )
    # Persist the tightened manifest and append the PREMIS event to the bag.
    manifest = serialize_record(updated)
    (archive.records_dir / f"{rid}.json").write_text(manifest, encoding="utf-8", newline="\n")
    (bag_dir / "record.json").write_text(manifest, encoding="utf-8", newline="\n")
    premis_log = PremisLog.read(aip.premis_path)
    premis_log.record(consent_event)
    premis_log.write(aip.premis_path)

    anon_after = archive.browse(anonymous(), now=_NOW_CONSENT)
    reloaded_log = PremisLog.read(aip.premis_path)
    has_consent_event = any(
        e.event_type is PremisEventType.CONSENT_CHANGE for e in reloaded_log.events
    )
    print(f"  action recorded: {action.action} by {action.actor!r} (reason logged)")
    print(f"  PREMIS now contains a CONSENT_CHANGE event: {has_consent_event}")
    print(
        f"  after:  default policy = {archive.get(rid).default_policy.value}; "
        f"anonymous can list it = {any(r.record_id == rid for r in anon_after)}"
    )
    _require(has_consent_event, "consent change was not recorded as a PREMIS event")
    _require(
        any(r.record_id == rid for r in anon_before),
        "record was expected to be public-listable before the consent change",
    )
    _require(
        not any(r.record_id == rid for r in anon_after),
        "record should be hidden from anonymous viewers after tightening consent",
    )

    # --- summary -----------------------------------------------------------
    _header("Summary")
    print("  - ingested a record + sealed a SENTINEL identity into the encrypted vault")
    print("  - replicated the bag and verified the replica on arrival")
    print("  - proved the sentinel appears on NO public surface (HTML, JSON, health,")
    print("    on-disk metadata, and all captured logs)")
    print("  - showed identity resolves only under an explicit unseal grant")
    print("  - recorded a consent change as a PREMIS event and saw it tighten disclosure")
    print()
    print("PASS: contributor identity absent from every public surface")
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
