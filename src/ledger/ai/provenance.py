"""AI-output provenance: who made this, with what, and when.

Every AI-generated artifact this package produces (a finding aid, an answer, a
structured query) carries an :class:`AIProvenance` naming the provider, the
exact model, the prompt version, the commit that generated it, and a
timestamp. The mission requirement this exists for: "commit cases + harness +
results with provider/model/prompt-version/commit/date provenance (a test
must reject results lacking it)" — see ``tests/test_ai_provenance.py`` for
that rejecting test, and ``tools/ai_eval.py`` for the harness that stamps this
onto every committed eval result the same way.

Nothing here ever fabricates a value: :func:`resolve_commit` returns an honest
fallback rather than an empty string when no commit is resolvable, and
:meth:`AIProvenance.validate` refuses to serialize a provenance record with
any field blank.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ledger.errors import LedgerError
from ledger.models import now_iso

__all__ = ["AIProvenance", "ProvenanceError", "resolve_commit"]

#: The unconditional label every AI-generated surface must carry (mission
#: requirement: "AI output always labeled AI-generated and unreviewed").
UNREVIEWED_LABEL = "AI-generated, unreviewed"


class ProvenanceError(LedgerError):
    """A provenance record is missing a required field, or could not be built."""


def resolve_commit(repo_root: Path | None = None) -> str:
    """The commit this build/run corresponds to, best-effort but never blank.

    Order: ``LEDGER_BUILD_COMMIT`` or ``GITHUB_SHA`` from the environment (set
    by CI or an operator packaging a release); else ``git rev-parse HEAD`` in
    ``repo_root`` (a source checkout); else the installed package version,
    prefixed so it reads honestly as "not a commit" rather than masquerading
    as one. Never raises — a provenance stamp must never be the reason an AI
    call fails.
    """
    env_commit = os.environ.get("LEDGER_BUILD_COMMIT") or os.environ.get("GITHUB_SHA")
    if env_commit:
        return env_commit

    root = repo_root or Path(__file__).resolve().parents[3]
    git_bin = shutil.which("git")
    if git_bin is not None:
        try:
            completed = subprocess.run(  # noqa: S603 -- fixed, resolved binary; no shell, no user input
                [git_bin, "rev-parse", "HEAD"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )
            sha = completed.stdout.strip()
            if sha:
                return sha
        except (OSError, subprocess.SubprocessError):
            # No git binary, not a checkout, or the rev-parse failed for any
            # reason -- fall through to the package-version fallback below
            # rather than raise; a provenance stamp must never be the reason
            # an AI call fails.
            pass

    from ledger import __version__  # local import: avoid a cycle at module load

    return f"version:{__version__}"


@dataclass(frozen=True)
class AIProvenance:
    """Who produced one AI artifact, with what, and when — never optional.

    ``label`` defaults to :data:`UNREVIEWED_LABEL` and every caller in this
    package leaves it at that default; a *reviewed* label is a human,
    out-of-band decision this package never makes for itself.
    """

    provider: str
    model: str
    prompt_version: str
    commit: str
    generated_at: str = field(default_factory=now_iso)
    label: str = UNREVIEWED_LABEL

    def validate(self) -> None:
        """Raise :class:`ProvenanceError` if any required field is blank."""
        missing = [
            name
            for name, value in (
                ("provider", self.provider),
                ("model", self.model),
                ("prompt_version", self.prompt_version),
                ("commit", self.commit),
                ("generated_at", self.generated_at),
                ("label", self.label),
            )
            if not value
        ]
        if missing:
            raise ProvenanceError(
                f"AI provenance is missing required field(s): {', '.join(missing)} "
                "— an AI result may never be recorded or displayed without full provenance"
            )

    def to_dict(self) -> dict[str, str]:
        """Serialize to a plain mapping, validating completeness first."""
        self.validate()
        return {
            "provider": self.provider,
            "model": self.model,
            "promptVersion": self.prompt_version,
            "commit": self.commit,
            "generatedAt": self.generated_at,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> AIProvenance:
        """Rebuild from a mapping (e.g. a committed eval-evidence file), validating."""
        provenance = cls(
            provider=str(data.get("provider", "")),
            model=str(data.get("model", "")),
            prompt_version=str(data.get("promptVersion", "")),
            commit=str(data.get("commit", "")),
            generated_at=str(data.get("generatedAt", "")),
            label=str(data.get("label", "")) or UNREVIEWED_LABEL,
        )
        provenance.validate()
        return provenance
