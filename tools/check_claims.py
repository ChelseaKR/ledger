#!/usr/bin/env python3
"""Truthfulness gate — verify factual repo claims made in README/docs (merge-blocking).

A README that overclaims is a slow-acting bug: readers, adopters, and reviewers
trust prose that has drifted from the code. This tripwire pins a *small* inventory
of load-bearing, checkable claims and fails the build when reality and documentation
diverge, so a correction stays corrected and a future edit cannot silently reintroduce
a dead claim.

Two claim kinds, both pure standard library (no new dependency — ledger's runtime is
stdlib-first and this tool runs in the same gate):

* ``path_exists`` — a repo-relative path the docs promise the repo ships (e.g. the
  ``docs/audits/`` directory, the ADR set, the threat model, the self-host infra).
* ``forbidden_string`` — a substring that must *not* reappear in a file, each anchoring
  a specific corrected drift (a removed overclaim). Reintroducing the phrase fails the
  build and names the claim plus how to fix it.

Keep the inventory deliberately small: a noisy tripwire that flags prose churn trains
reviewers to ignore it. Add a claim only when it is factual, load-bearing, and cheap to
check.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class PathExists:
    """A repo-relative path the documentation claims the repo ships."""

    name: str
    path: str
    hint: str

    def check(self) -> str | None:
        if (ROOT / self.path).exists():
            return None
        return f"{self.name}: {self.path!r} does not exist — {self.hint}"


@dataclass(frozen=True)
class ForbiddenString:
    """A dead claim (corrected overclaim) that must not reappear in ``file``."""

    name: str
    file: str
    substring: str
    hint: str

    def check(self) -> str | None:
        target = ROOT / self.file
        if not target.is_file():
            return f"{self.name}: {self.file!r} is missing — cannot check for {self.substring!r}"
        if self.substring in target.read_text(encoding="utf-8"):
            return f"{self.name}: {self.file} still contains {self.substring!r} — {self.hint}"
        return None


# The inventory. Small on purpose (see module docstring). Each entry pins one
# factual, checkable claim the README/docs make about the repo.
CLAIMS: tuple[PathExists | ForbiddenString, ...] = (
    PathExists(
        "docs-audits",
        "docs/audits",
        "the README claims a committed docs/audits/ (auditability); create the directory "
        "(with a README) or drop the claim.",
    ),
    PathExists(
        "docs-adr",
        "docs/adr",
        "the README claims ADRs under docs/adr/; restore them or drop the claim.",
    ),
    PathExists(
        "threat-model",
        "docs/THREAT-MODEL.md",
        "the README/docs reference a committed threat model.",
    ),
    PathExists(
        "infra-compose",
        "infra/docker-compose.yml",
        "the README claims an optional self-host compose deploy under infra/.",
    ),
    PathExists(
        "infra-terraform",
        "infra/aws/terraform",
        "the README claims a Terraform self-host path under infra/aws/.",
    ),
    ForbiddenString(
        "no-cdk",
        "README.md",
        "compose/CDK",
        "infra/aws ships Terraform, not CDK; the layout line must say compose/Terraform.",
    ),
    ForbiddenString(
        "no-media-streaming",
        "README.md",
        "stream rather than block",
        "the server does not stream large media (see FIX-03); do not reintroduce the "
        "streaming overclaim.",
    ),
    ForbiddenString(
        "no-metrics",
        "README.md",
        "structured logs and metrics",
        "the server emits a scrubbed method+status request log and no metrics; do not "
        "reintroduce the metrics overclaim.",
    ),
)


def main() -> int:
    failures = [msg for claim in CLAIMS if (msg := claim.check()) is not None]
    if failures:
        print("truthfulness check FAILED — documentation drifted from the repo:", file=sys.stderr)
        for msg in failures:
            print(f"  - {msg}", file=sys.stderr)
        return 1
    print(f"truthfulness OK: {len(CLAIMS)} repo claims verified against README/docs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
