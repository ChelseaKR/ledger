"""Seed a throwaway demo archive and serve it for the browser accessibility run.

This is a **CI/dev-only** helper: it does not ship inside ``src/ledger`` and adds
no runtime dependency. It exists so the Playwright + axe job (see
``.github/workflows/ci.yml``, job ``accessibility-browser``) can drive the *real*
served surface in a headless Chromium — engine-backed depth over the static,
stdlib-only gate (``python -m ledger.accessibility_check web``).

It reuses the archive-building parts of :mod:`ledger.demo` (``_build_demo_record``
+ ``Archive.ingest`` + ``make_server``/``serve``) so the pages under test are the
same ones the executable no-outing proof renders — including a record that carries
a content warning, which exercises the CW interstitial state.

Usage (normally started/stopped by Playwright's ``webServer`` config)::

    python -m serve_demo            # seed ./local-archive then serve on :8099

Environment:
    LEDGER_A11Y_ARCHIVE    archive root to (re)create   (default: ./local-archive)
    LEDGER_A11Y_HOST       bind host                    (default: 127.0.0.1)
    LEDGER_A11Y_PORT       bind port                    (default: 8099)
    LEDGER_A11Y_TOKEN_FILE where the signed steward grant token is written for the
                           specs (default: ./.steward-token, gitignored)
    LEDGER_VAULT_KEY       urlsafe-base64 vault key; a fixed dev key is set if unset
                           so the /contribute write path (sealed contact) is enabled.
    LEDGER_GRANT_SECRET    grant-token HMAC secret (FIX-02); a fixed dev secret is
                           set if unset so the steward console is testable through
                           the real authenticated grant path.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from ledger.access.grants import issue_grant_token
from ledger.config import Config
from ledger.demo import _build_demo_record
from ledger.identity import ContributorIdentity
from ledger.ingest import Archive
from ledger.models import AccessPolicy, DublinCore, Field, Record
from ledger.server import serve

# A fixed dev vault key (urlsafe-base64 shape). It protects nothing real: this
# archive is synthetic and thrown away after the CI job. Keeping it constant makes
# the seed deterministic and enables the /contribute sealed-contact path so the
# contribute form is a genuine write surface under test.
_DEV_VAULT_KEY = "0123456789abcdef0123456789abcdef0123456789a="

# A fixed dev grant-signing secret (FIX-02: X-Ledger-Grant headers carry an
# HMAC-signed token, never a bare subject). Like the vault key above it protects
# nothing real; it exists so the browser run exercises the real authenticated
# grant path instead of a disabled or mocked one.
_DEV_GRANT_SECRET = "a11y-demo-grant-secret-not-real"  # noqa: S105 - synthetic dev value

# Fixed timestamps so the seeded bags/manifests are byte-reproducible run to run.
_NOW = "2026-06-16T12:00:00Z"


def _second_record(config: Config) -> Record:
    """A second, warning-free public record so browse/search/facets have breadth.

    It carries a Dublin Core subject so the search facet surface has a facet to
    render, and no content warning so the browse list mixes warned and unwarned
    items — both are pages the axe run checks.
    """
    return Record(
        title="The Saturday repair table",
        default_policy=AccessPolicy.PUBLIC,
        dublin_core=DublinCore(
            title=["The Saturday repair table"],
            description=["How the collective kept its tools and its people going."],
            publisher=[config.archive_name],
            subject=["mutual aid"],
            type=["oral history"],
            language=["en"],
        ),
        fields=[
            Field(
                name="story",
                value="Every Saturday someone brought a broken thing and left with it mended.",
                policy=AccessPolicy.PUBLIC,
            ),
        ],
    )


def seed_archive(root: Path) -> Archive:
    """Build a fresh synthetic archive at ``root`` (destroying any prior one).

    Seeds two public records — one carrying a content warning (via the demo's
    ``_build_demo_record``, which exercises the CW interstitial) and one without —
    plus a ``grants.json`` naming a steward subject so the gated steward console is
    reachable with a *signed* ``X-Ledger-Grant`` token for ``steward-1`` (written
    to the token sidecar file by :func:`main`; bare subjects are rejected since
    FIX-02). The contributor identity is a throwaway sentinel sealed into the
    vault, exactly as the demo does.
    """
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)

    config = Config.default("Demo Community Archive", root)
    archive = Archive.init(config)
    vault_key = _DEV_VAULT_KEY.encode("ascii")

    identity = ContributorIdentity(
        name="DEMO-CONTRIBUTOR-DO-NOT-LEAK",
        contact="demo-contact@sentinel.invalid",
    )
    archive.ingest(
        {},
        _build_demo_record(config),
        identity=identity,
        vault_key=vault_key,
        agent="a11y-seed",
        now=_NOW,
    )
    archive.ingest(
        {},
        _second_record(config),
        agent="a11y-seed",
        now=_NOW,
    )

    # A pre-provisioned steward grant so the /steward console is reachable in the
    # axe run via the X-Ledger-Grant header (deny-by-default otherwise).
    grants_path = root / "grants.json"
    grants_path.write_text(
        '{"steward-1": {"levels": ["public", "community", "stewards"], '
        '"is_steward": true}}\n',
        encoding="utf-8",
    )
    return archive


def main() -> int:
    """Seed the demo archive, then serve it (blocking) for the browser run."""
    root = Path(os.environ.get("LEDGER_A11Y_ARCHIVE", "local-archive")).resolve()
    host = os.environ.get("LEDGER_A11Y_HOST", "127.0.0.1")
    port = int(os.environ.get("LEDGER_A11Y_PORT", "8099"))
    # Enable the contribute write path with a fixed dev key when none is provided.
    os.environ.setdefault("LEDGER_VAULT_KEY", _DEV_VAULT_KEY)
    # FIX-02: grant headers are HMAC-authenticated; a bare subject is rejected.
    # Sign a real steward token with the (dev) secret and hand it to the
    # Playwright specs through a gitignored sidecar file, so the axe run drives
    # the same authenticated grant path production uses.
    os.environ.setdefault("LEDGER_GRANT_SECRET", _DEV_GRANT_SECRET)

    archive = seed_archive(root)
    token = issue_grant_token("steward-1", os.environ["LEDGER_GRANT_SECRET"].encode("utf-8"))
    token_file = Path(
        os.environ.get("LEDGER_A11Y_TOKEN_FILE", str(Path(__file__).with_name(".steward-token")))
    )
    token_file.write_text(token + "\n", encoding="utf-8")
    print(f"seeded demo archive at {root}; serving on http://{host}:{port}", flush=True)
    serve(
        archive,
        host=host,
        port=port,
        grants_path=root / "grants.json",
        allow_contributions=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
