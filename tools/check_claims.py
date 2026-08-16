#!/usr/bin/env python3
"""Truthfulness gate — verify factual repo claims made in README/docs (merge-blocking).

A README that overclaims is a slow-acting bug: readers, adopters, and reviewers
trust prose that has drifted from the code. Underclaiming costs the same reader the
same time — someone working the standards table has to disprove a gap that closed
weeks ago — and a self-contradicting README is worse for trust than either half
alone. This tripwire pins a *small* inventory of load-bearing, checkable claims and
fails the build when reality and documentation diverge, so a correction stays
corrected and a future edit cannot silently reintroduce a dead claim.

Seven claim kinds, all pure standard library (no new dependency — ledger's runtime is
stdlib-first and this tool runs in the same gate):

* ``path_exists`` — a repo-relative path the docs promise the repo ships (e.g. the
  ``docs/audits/`` directory, the ADR set, the threat model, the self-host infra).
* ``forbidden_string`` — a substring that must *not* reappear in a file, each anchoring
  a specific corrected drift (a removed overclaim or a removed underclaim). Reintroducing
  the phrase fails the build and names the claim plus how to fix it.
* ``required_string`` — the other half of a correction: the *evidence* a corrected
  sentence now rests on (e.g. the hashes in ``uv.lock`` that make "hash-pinned" true).
  ``forbidden_string`` alone is satisfiable by deleting the whole paragraph, which is
  how a gate quietly stops gating; pairing the two means the claim and its evidence
  travel together.
* ``stated_count`` — a number the prose states, re-derived from the repo. It fails
  both ways: if the count is wrong, *and* if the sentence stating it disappears (a
  silently vacuous check is the failure mode this whole file exists to prevent).
* ``reference_exists`` — every "tracked in ``docs/ROADMAP.md``, <ID>" pointer in any
  committed Markdown must find <ID> in ``docs/ROADMAP.md``. DOC-13's rule is that a
  gap declaration links something a reader can actually open; a pointer to a tracker
  item that no longer exists is a dead claim of the same family as a dead path.
* ``config_number`` — a threshold the prose states, re-derived from the config that
  actually enforces it rather than from another sentence. Three documents spent two
  weeks citing a coverage flag (``--cov-fail-under=85``) that appears nowhere in the
  repo and a floor three points below the one ``pyproject.toml`` enforces, because
  nothing tied the number to its source. Like ``stated_count`` it fails both ways.
* ``ruleset_contexts`` — every required status check in the committed branch-protection
  mirror (``.github/rulesets/main.json``) must name a job that exists in
  ``.github/workflows/``. Renaming a job is otherwise a silent way to drop a
  merge-blocking gate: the ruleset keeps requiring a context nothing will ever report.
  An empty required-check list is a failure too, not a vacuous pass.

Keep the inventory deliberately small: a noisy tripwire that flags prose churn trains
reviewers to ignore it. Add a claim only when it is factual, load-bearing, and cheap to
check. What this gate is *not* able to see is not left implicit either: ``UNCOVERED``
below names the load-bearing claims outside its reach, they are printed on every run,
and ``CONTRIBUTING.md`` publishes the same list for readers (kept in step by
``tests/test_claims_gate.py``).
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
    """A dead claim (a corrected over- or underclaim) that must not reappear in ``file``."""

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


@dataclass(frozen=True)
class RequiredString:
    """Evidence a corrected claim rests on, which must still be present in ``file``.

    Paired with a :class:`ForbiddenString` wherever a claim was corrected rather than
    deleted: "the old phrase is gone" is true of an empty file, so on its own it proves
    the drift is absent, not that the replacement is earned.
    """

    name: str
    file: str
    substring: str
    hint: str

    def check(self) -> str | None:
        target = ROOT / self.file
        if not target.is_file():
            return f"{self.name}: {self.file!r} is missing — cannot verify {self.substring!r}"
        if self.substring in target.read_text(encoding="utf-8"):
            return None
        return f"{self.name}: {self.file} no longer contains {self.substring!r} — {self.hint}"


_NUMBER_WORDS: dict[str, int] = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}


@dataclass(frozen=True)
class StatedCount:
    """A count the prose states, re-derived from the repo itself.

    ``pattern`` must capture the stated number (digits, or an English word — the docs
    are prose, and "eight review documents" reads better than "8"). The truth is the
    number of repo-relative paths matching ``glob``, minus ``exclude``.

    Fails in *both* directions on purpose: a stated number that no longer matches the
    repo, and a sentence that stopped stating the number at all. The second case is the
    one that turns a gate into decoration — the regex silently matches nothing, the
    check silently passes, and nobody learns the claim went unverified.
    """

    name: str
    file: str
    pattern: str
    glob: str
    hint: str
    exclude: tuple[str, ...] = ()

    def actual(self) -> int:
        excluded = {ROOT / name for name in self.exclude}
        return len([p for p in ROOT.glob(self.glob) if p not in excluded])

    def check(self) -> str | None:
        target = ROOT / self.file
        if not target.is_file():
            return f"{self.name}: {self.file!r} is missing — cannot check the stated count"
        found = re.findall(self.pattern, target.read_text(encoding="utf-8"))
        if not found:
            return (
                f"{self.name}: {self.file} no longer states the count this gate re-derives "
                f"(pattern {self.pattern!r} matched nothing) — restate it, or delete this claim "
                "from the inventory rather than leaving a check that verifies nothing"
            )
        actual = self.actual()
        for raw in found:
            stated = _NUMBER_WORDS.get(str(raw).lower())
            if stated is None:
                if not str(raw).isdigit():
                    return f"{self.name}: {self.file} states {raw!r}, which is not a number"
                stated = int(raw)
            if stated != actual:
                return (
                    f"{self.name}: {self.file} states {raw} but the repo has {actual} "
                    f"({self.glob}) — {self.hint}"
                )
        return None


# --- reference_exists -------------------------------------------------------
#
# Roadmap *item* ids only (the ``P<phase>-<n>`` scheme). Control ids from the
# portfolio standards (SEC-04, A11Y-11, CQ-08 …) are deliberately out of scope:
# they are defined in `STANDARDS/`, not here, so a doc may legitimately cite one
# that `docs/ROADMAP.md` never mentions. See UNCOVERED.

_ROADMAP = "docs/ROADMAP.md"
_ITEM_ID_RE = re.compile(r"\bP([1-4])-(\d+(?:\s*[/–—-]\s*\d+)*)")
_POINTER_WINDOW = 160
_SKIP_DIRS = frozenset({".git", ".venv", "node_modules", "site-packages", "__pycache__"})


def _item_ids(text: str) -> set[str]:
    """Every roadmap item id in ``text``, expanding compact forms like ``P1-3/6``."""
    found: set[str] = set()
    for match in _ITEM_ID_RE.finditer(text):
        phase, tail = match.group(1), match.group(2)
        found.update(f"P{phase}-{n.strip()}" for n in re.split(r"[/–—-]", tail) if n.strip())
    return found


def _pointer_windows(text: str) -> list[tuple[int, str]]:
    """The prose immediately following each ``docs/ROADMAP.md`` mention.

    A pointer is "this is tracked over there, under that id", so only ids in the same
    breath as the path count. The window stops at the end of the sentence or the
    paragraph so an unrelated id further down cannot be read as part of the pointer.
    """
    windows: list[tuple[int, str]] = []
    for match in re.finditer(re.escape(_ROADMAP), text):
        tail = text[match.end() : match.end() + _POINTER_WINDOW]
        for terminator in ("\n\n", ". ", ".\n"):
            cut = tail.find(terminator)
            if cut != -1:
                tail = tail[:cut]
        windows.append((text.count("\n", 0, match.start()) + 1, tail))
    return windows


@dataclass(frozen=True)
class ReferenceExists:
    """Every "tracked in ``docs/ROADMAP.md``, <ID>" pointer must resolve in that file.

    Scans every committed Markdown file — a blind spot that only covers the README is
    how the last five drifted claims survived — and reports the file and line of each
    dead pointer.

    Honest limit: resolution means the id *appears* in ``docs/ROADMAP.md``, not that
    the roadmap defines a row for it. See UNCOVERED.
    """

    name: str
    hint: str

    def _docs(self) -> list[Path]:
        return sorted(
            path
            for path in ROOT.rglob("*.md")
            if not _SKIP_DIRS.intersection(path.relative_to(ROOT).parts)
        )

    def pointers(self) -> list[tuple[str, int, str]]:
        """Every (file, line, id) pointer this claim inspects."""
        found: list[tuple[str, int, str]] = []
        for path in self._docs():
            text = path.read_text(encoding="utf-8")
            rel = path.relative_to(ROOT).as_posix()
            for line, window in _pointer_windows(text):
                found.extend((rel, line, item) for item in sorted(_item_ids(window)))
        return found

    def check(self) -> str | None:
        roadmap = ROOT / _ROADMAP
        if not roadmap.is_file():
            return f"{self.name}: {_ROADMAP} is missing — every roadmap pointer is dead"
        known = _item_ids(roadmap.read_text(encoding="utf-8"))
        dead = [(f, line, item) for f, line, item in self.pointers() if item not in known]
        if not dead:
            return None
        listed = "; ".join(f"{f}:{line} → {item}" for f, line, item in dead)
        return f"{self.name}: {_ROADMAP} does not mention {listed} — {self.hint}"


@dataclass(frozen=True)
class ConfigNumber:
    """A threshold the prose states, re-derived from the config that enforces it.

    ``stated_count`` re-derives a number from the *tree*; this re-derives one from the
    *configuration*, which is where thresholds actually live. The failure this exists
    to prevent is documented drift in the safe direction: three files claimed an 85%
    coverage floor, and cited a ``--cov-fail-under=85`` flag the repo does not pass,
    while ``pyproject.toml`` had been enforcing 88 since the ratchet in #83's first
    pass. Nobody was lying; the sentence simply had no tie to its source.

    ``key_path`` walks the parsed TOML. Fails when the source is missing, when the key
    is absent, when the prose stops stating the number, and when the two disagree.
    """

    name: str
    file: str
    pattern: str
    source: str
    key_path: tuple[str, ...]
    hint: str

    def actual(self) -> int | str | None:
        source = ROOT / self.source
        if not source.is_file():
            return None
        try:
            data: Any = tomllib.loads(source.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            return None
        for key in self.key_path:
            if not isinstance(data, dict) or key not in data:
                return None
            data = data[key]
        return data if isinstance(data, int) and not isinstance(data, bool) else None

    def check(self) -> str | None:
        target = ROOT / self.file
        if not target.is_file():
            return f"{self.name}: {self.file!r} is missing — cannot check the stated threshold"
        dotted = ".".join(self.key_path)
        actual = self.actual()
        if actual is None:
            return (
                f"{self.name}: cannot read {dotted} from {self.source} — the documented "
                "threshold has no source to be checked against, which is not the same as "
                "the documentation being right"
            )
        found = re.findall(self.pattern, target.read_text(encoding="utf-8"))
        if not found:
            return (
                f"{self.name}: {self.file} no longer states the threshold this gate "
                f"re-derives (pattern {self.pattern!r} matched nothing) — restate it, or "
                "delete this claim rather than leaving a check that verifies nothing"
            )
        for raw in found:
            if str(raw) != str(actual):
                return (
                    f"{self.name}: {self.file} states {raw} but {self.source} sets "
                    f"{dotted} = {actual} — {self.hint}"
                )
        return None


# --- ruleset_contexts -------------------------------------------------------
#
# Job-level names sit at four-space indentation under `jobs:`; a job with no `name:`
# reports under its own id. A name may interpolate a matrix value
# (`CodeQL analyze (${{ matrix.language }})`), so each name becomes a pattern rather
# than a literal.

_JOB_ID_RE = re.compile(r"^  ([A-Za-z_][\w-]*):\s*$")
_JOB_NAME_RE = re.compile(r"^    name:\s*(.+?)\s*$")
_EXPRESSION_RE = re.compile(r"\$\{\{.*?\}\}")


def _job_name_patterns(workflows: Path) -> list[re.Pattern[str]]:
    """One regex per job GitHub could report a check for, across every workflow."""
    patterns: list[re.Pattern[str]] = []
    for path in sorted(workflows.glob("*.yml")) + sorted(workflows.glob("*.yaml")):
        lines = path.read_text(encoding="utf-8").splitlines()
        in_jobs = False
        for index, line in enumerate(lines):
            if line.rstrip() == "jobs:":
                in_jobs = True
                continue
            if not in_jobs or not (job := _JOB_ID_RE.match(line)):
                continue
            name = job.group(1)
            for following in lines[index + 1 :]:
                if _JOB_ID_RE.match(following) or following.rstrip() == "jobs:":
                    break
                if declared := _JOB_NAME_RE.match(following):
                    name = declared.group(1).strip("\"'")
                    break
            # A matrix interpolation stands for "any value", so blank it to a sentinel
            # the name itself cannot contain, escape the literal remainder, and put a
            # wildcard back where the sentinel was.
            escaped = re.escape(_EXPRESSION_RE.sub("\x00", name))
            patterns.append(re.compile(f"^{escaped.replace(chr(0), '.+')}$"))
    return patterns


@dataclass(frozen=True)
class RulesetContexts:
    """Every required status check in the committed ruleset must name a real job.

    ``.github/rulesets/main.json`` mirrors the live ``protect-main`` ruleset so the
    branch-protection posture is reviewable in a diff. A required context is a string
    match against a check name: rename the job and the ruleset goes on requiring a
    context that nothing will ever report, which reads as "still protected" and is not.

    An empty required-check list fails too. A ruleset that requires nothing is the
    purest form of the failure this whole file is about.
    """

    name: str
    ruleset: str
    workflows: str
    hint: str

    def contexts(self) -> list[str] | str:
        """The required contexts, or a message explaining why they cannot be read."""
        target = ROOT / self.ruleset
        if not target.is_file():
            return f"{self.ruleset!r} is missing — the committed ruleset mirror is the evidence"
        try:
            data: Any = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return f"{self.ruleset} cannot be parsed: {exc}"
        if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
            return f"{self.ruleset} has no `rules` array"
        for rule in data["rules"]:
            if not isinstance(rule, dict) or rule.get("type") != "required_status_checks":
                continue
            parameters = rule.get("parameters")
            if not isinstance(parameters, dict):
                return f"{self.ruleset}: required_status_checks has no `parameters` object"
            checks = parameters.get("required_status_checks")
            if not isinstance(checks, list):
                return f"{self.ruleset}: `required_status_checks` must be an array"
            found = [c["context"] for c in checks if isinstance(c, dict) and "context" in c]
            if len(found) != len(checks):
                return f"{self.ruleset}: every required status check needs a `context`"
            return found
        return f"{self.ruleset} declares no `required_status_checks` rule"

    def check(self) -> str | None:
        contexts = self.contexts()
        if isinstance(contexts, str):
            return f"{self.name}: {contexts} — {self.hint}"
        if not contexts:
            return (
                f"{self.name}: {self.ruleset} requires zero status checks — a branch "
                "protection profile that gates nothing is not protection"
            )
        workflows = ROOT / self.workflows
        if not workflows.is_dir():
            return (
                f"{self.name}: {self.workflows!r} is missing — nothing to resolve contexts against"
            )
        patterns = _job_name_patterns(workflows)
        if not patterns:
            return f"{self.name}: no job names found under {self.workflows} — {self.hint}"
        unresolved = [c for c in contexts if not any(p.match(c) for p in patterns)]
        if unresolved:
            listed = ", ".join(repr(c) for c in unresolved)
            return (
                f"{self.name}: {self.ruleset} requires {listed}, which no job in "
                f"{self.workflows} is named — {self.hint}"
            )
        return None


@dataclass(frozen=True)
class Uncovered:
    """A load-bearing claim this gate cannot check, named rather than left implicit.

    A known blind spot that stays known is a gate that will be wrong again next month.
    Every entry is printed on each run and published in ``CONTRIBUTING.md`` so a reader
    can see the boundary without reading this file.
    """

    claim: str
    why: str
    checked_elsewhere: str = ""


Claim = (
    PathExists
    | ForbiddenString
    | RequiredString
    | StatedCount
    | ReferenceExists
    | ConfigNumber
    | RulesetContexts
)

# The inventory. Small on purpose (see module docstring). Each entry pins one
# factual, checkable claim the README/docs make about the repo.
CLAIMS: tuple[Claim, ...] = (
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
    PathExists(
        "residual-risk-register",
        "docs/audits/residual-risk-register.md",
        "the README names the residual-risk register as committed (only the human "
        "sign-off is open, #82); restore the file or correct the sentence.",
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
    ForbiddenString(
        "lockfile-is-committed",
        "README.md",
        "dependency pinning is a range today",
        "uv.lock is committed and hash-pinned and `uv sync --locked` installs from it; "
        "this underclaim contradicted the README's own standards table (#124).",
    ),
    ForbiddenString(
        "residual-risk-register-is-committed",
        "README.md",
        "human review and the residual-risk register",
        "the register is committed under docs/audits/; only the human sign-off (#82) is "
        "outstanding, so do not describe the artifact itself as still owed (#124).",
    ),
    ForbiddenString(
        "audits-set-is-committed",
        "README.md",
        "a fuller `docs/audits/` set is tracked",
        "the review set is committed under docs/audits/; state what ships and track only "
        "the human sign-off (#124).",
    ),
    ForbiddenString(
        "healthz-counts-are-gated",
        "docs/ARCHITECTURE.md",
        "`/healthz` reports counts only",
        "anonymous /healthz returns status, all_verified, ready, and chain_head; the counts "
        "are behind a steward grant (P2-2). Do not describe the endpoint as leaking totals "
        "the code no longer exposes (#124).",
    ),
    ForbiddenString(
        "healthz-counts-are-gated-in-the-runbook",
        "infra/README.md",
        "fixity counts only",
        "the self-host runbook told an operator to expect counts from an anonymous "
        "/healthz; they are behind a steward grant (P2-2), so a monitor pointed at the "
        "endpoint sees all_verified and not the numbers (#124).",
    ),
    RequiredString(
        "healthz-gating-in-the-runbook",
        "infra/README.md",
        "gated to a steward grant",
        "the self-host runbook must say which /healthz fields an anonymous monitor "
        "actually gets, not merely stop promising counts.",
    ),
    RequiredString(
        "lockfile-is-hash-pinned",
        "uv.lock",
        'hash = "sha256:',
        "the README says dependencies install from a hash-pinned uv.lock; if the lockfile "
        "stops carrying hashes, the claim is no longer earned.",
    ),
    RequiredString(
        "install-refuses-to-re-resolve",
        "Makefile",
        "uv sync --locked",
        "the README says the install fails rather than re-resolving; that is `--locked`, "
        "and dropping the flag would make the sentence false.",
    ),
    RequiredString(
        "healthz-gating-documented",
        "docs/ARCHITECTURE.md",
        "gated to a steward grant",
        "the architecture doc must say the /healthz counts are steward-gated, not merely "
        "stop saying the opposite (the live behaviour is asserted in "
        "tests/test_server_remediation.py).",
    ),
    StatedCount(
        "audits-count",
        "README.md",
        r"(\w+) review documents under",
        "docs/audits/*.md",
        "count the review documents in docs/audits/ and restate the number (#124).",
        exclude=("docs/audits/README.md",),
    ),
    ReferenceExists(
        "roadmap-item-pointers",
        "a gap declaration must link something a reader can open (DOC-13): either add the "
        "item to docs/ROADMAP.md or stop citing an id that does not exist (#124).",
    ),
    PathExists(
        "committed-ruleset-mirror",
        ".github/rulesets/main.json",
        "the README and DEFINITION_OF_DONE.md say the live protect-main ruleset is mirrored "
        "in-tree (CI-CD-STANDARD §5); restore the mirror or stop claiming it.",
    ),
    RulesetContexts(
        "ruleset-contexts-name-real-jobs",
        ".github/rulesets/main.json",
        ".github/workflows",
        "a required context that matches no job name is a check that will never report, "
        "which leaves the branch reading as protected by a gate that cannot run.",
    ),
    ConfigNumber(
        "coverage-floor-in-definition-of-done",
        "DEFINITION_OF_DONE.md",
        r"(\d+)% branch-coverage floor",
        "pyproject.toml",
        ("tool", "coverage", "report", "fail_under"),
        "restate the floor pyproject.toml actually enforces, or change the floor.",
    ),
    ConfigNumber(
        "coverage-floor-in-contributing",
        "CONTRIBUTING.md",
        r"(\d+)% branch-coverage floor",
        "pyproject.toml",
        ("tool", "coverage", "report", "fail_under"),
        "restate the floor pyproject.toml actually enforces, or change the floor.",
    ),
    ConfigNumber(
        "coverage-floor-in-dora-review",
        "docs/DORA-DELIVERY-HEALTH-REVIEW.md",
        r"(\d+)%\s*\n?branch-coverage floor",
        "pyproject.toml",
        ("tool", "coverage", "report", "fail_under"),
        "restate the floor pyproject.toml actually enforces, or change the floor.",
    ),
)

# What this gate cannot see. Published in CONTRIBUTING.md ("What the truthfulness gate
# does not cover") and kept in step with it by tests/test_claims_gate.py, so the boundary
# is visible to a reader who never opens this file.
UNCOVERED: tuple[Uncovered, ...] = (
    Uncovered(
        "no release has shipped yet",
        "depends on the tags on the remote, which a CI checkout does not fetch; a local "
        "`git tag` would answer a different question than the one the README asks",
    ),
    Uncovered(
        "the audit artifacts under docs/audits/ have not been signed off by a human",
        "review status is a human fact about people, not a property of the tree (#82)",
    ),
    Uncovered(
        "no dated assistive-technology walkthrough exists",
        "no scan can produce or confirm a screen-reader session (#81)",
    ),
    Uncovered(
        "the test, coverage, and mutation figures in docs/ROADMAP.md",
        "dated measurements, true of the run that produced them",
        "re-measured by `make test`, `make cov`, and `make mutation`",
    ),
    Uncovered(
        "a roadmap pointer resolves to a defined roadmap row",
        "reference_exists proves the id is mentioned in docs/ROADMAP.md, not that a row "
        "defines it; the P-scheme has no defining rows left",
    ),
    Uncovered(
        "standards control ids (SEC-04, A11Y-11, CQ-08 …) cited beside a roadmap pointer",
        "they are defined in the portfolio's STANDARDS/, not in this repo, so this gate "
        "has nothing local to resolve them against",
    ),
    Uncovered(
        "whether .github/rulesets/main.json still matches the live protect-main ruleset",
        "parity needs a GitHub API call, and this stdlib script deliberately makes no "
        "network request; the mirror is only as current as the change that last touched it",
    ),
    Uncovered(
        "whether every merge-blocking gate is in the required-check set",
        "which gates ought to be required is a policy decision, not a repo fact; today "
        "Semgrep and the OSV lockfile scan run on every PR and are not required (#79)",
    ),
    Uncovered(
        "the response shape of the browse server's routes",
        "needs a running server, which this stdlib script deliberately does not start",
        "asserted live against the documented prose in tests/test_server_remediation.py",
    ),
)

_KIND_LABEL: dict[str, tuple[str, str]] = {
    "PathExists": ("path present", "paths present"),
    "ForbiddenString": ("dead claim absent", "dead claims absent"),
    "RequiredString": ("supporting fact present", "supporting facts present"),
    "StatedCount": ("stated count re-derived", "stated counts re-derived"),
    "ConfigNumber": (
        "threshold re-derived from config",
        "thresholds re-derived from config",
    ),
    "RulesetContexts": (
        "required-check set resolved",
        "required-check sets resolved",
    ),
    # ReferenceExists is reported by the number of pointers it swept, not by the
    # number of inventory entries: "1 claim" would hide how much it looked at.
}


@dataclass
class _Tally:
    counts: dict[str, int] = field(default_factory=dict)

    def add(self, claim: Claim) -> None:
        kind = type(claim).__name__
        self.counts[kind] = self.counts.get(kind, 0) + 1

    def summary(self) -> str:
        parts = []
        for kind, (singular, plural) in _KIND_LABEL.items():
            n = self.counts.get(kind, 0)
            if n:
                parts.append(f"{n} {singular if n == 1 else plural}")
        sweeps = [claim for claim in CLAIMS if isinstance(claim, ReferenceExists)]
        if sweeps:
            pointers = sum(len(claim.pointers()) for claim in sweeps)
            noun = "pointer" if pointers == 1 else "pointers"
            parts.append(f"{pointers} roadmap {noun} resolved across every committed Markdown file")
        return ", ".join(parts)


def _print_boundary() -> None:
    print(f"outside this gate ({len(UNCOVERED)}) — see CONTRIBUTING.md:")
    for item in UNCOVERED:
        tail = f" [{item.checked_elsewhere}]" if item.checked_elsewhere else ""
        print(f"  - {item.claim} — {item.why}{tail}")


def main() -> int:
    failures = [msg for claim in CLAIMS if (msg := claim.check()) is not None]
    if failures:
        print("truthfulness check FAILED — documentation drifted from the repo:", file=sys.stderr)
        for msg in failures:
            print(f"  - {msg}", file=sys.stderr)
        return 1
    tally = _Tally()
    for claim in CLAIMS:
        tally.add(claim)
    print(f"truthfulness OK: {len(CLAIMS)} repo claims verified — {tally.summary()}.")
    _print_boundary()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
