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
    LEDGER_A11Y_ARCHIVE   archive root to (re)create   (default: ./local-archive)
    LEDGER_A11Y_HOST      bind host                    (default: 127.0.0.1)
    LEDGER_A11Y_PORT      bind port                    (default: 8099)
    LEDGER_VAULT_KEY      urlsafe-base64 vault key; a fixed dev key is set if unset
                          so the /contribute write path (sealed contact) is enabled.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

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
    reachable with an ``X-Ledger-Grant: steward-1`` header. The contributor
    identity is a throwaway sentinel sealed into the vault, exactly as the demo does.
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

    archive = seed_archive(root)
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
