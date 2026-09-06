"""The version this repository declares, held against the releases it actually has.

`pyproject.toml` owns the version (RELEASE-AND-VERSIONING-STANDARD REL-02) and declares
`0.1.0`. `git tag` returns nothing. That gap is deliberate and documented -- the release
is *prepared*, and cutting the signed tag is the owner's act (`docs/RELEASE-0.1.0.md`) --
but until now nothing held the two together, and `tools/check_claims.py` said so in as
many words: "no release has shipped yet" was published in `UNCOVERED` as a claim the
truthfulness gate could not check, because "a CI checkout does not fetch tags".

That reason was correct and is now fixed rather than accepted. `ci.yml`'s gate job checks
out with `fetch-depth: 0`, which fetches `refs/tags/*` explicitly, so the tags are there
to read -- and this file refuses to read an empty tag list off a shallow clone as "never
released", which would be an absence rendered as a value.

Two states are distinguished, and only one is a defect:

* **No tags at all** -- where this repository stands. It passes, but only if the repository
  still says so where a reader looks, and only if the artefacts that make "prepared, not
  published" a checkable claim are present: the dated `## [0.1.0]` CHANGELOG heading must
  still carry its prepared-not-published note, and `docs/RELEASE-0.1.0.md` must exist for
  the version being declared.
* **Tags exist and none matches the declared version** -- a defect. The failure names the
  declared version and the newest tag.

`ledger` deliberately does *not* use a PEP 440 `.devN` suffix to signal this, unlike
`mrf-honest` and `disclosed`: `pyproject.toml` records the reason (a developmental release
is skipped by a plain `pip install`, which makes it unreleasable by construction) and
`docs/RELEASE-0.1.0.md` is a checklist for an imminent release, not an indefinite one. The
checks below are that decision's replacement, not an omission of it.

The failing branch is unreachable from this repository today, so it is driven below
against synthetic tag lists and was proved end to end against a throwaway clone carrying
real `v9.9.9` and `v0.0.1` tags. No automation here may mint a tag.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tomllib
from collections.abc import Sequence
from pathlib import Path

import pytest

from ledger import __version__ as PACKAGE_VERSION

ROOT = Path(__file__).resolve().parent.parent


def declared_version(root: Path = ROOT) -> str:
    """The one source of truth for this project's version (REL-02)."""

    with (root / "pyproject.toml").open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def repository_tags(root: Path = ROOT) -> list[str]:
    """Every tag this checkout can see. Empty means "none visible", not "none exist"."""

    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "-C", str(root), "tag", "--list"],  # noqa: S607 - git is the tool
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def is_shallow(root: Path = ROOT) -> bool:
    """Whether this checkout was truncated -- an empty tag list then proves nothing."""

    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "-C", str(root), "rev-parse", "--is-shallow-repository"],  # noqa: S607 - git is the tool
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() == "true"


def remote_tags(root: Path = ROOT) -> list[str] | None:
    """The tags the remote actually has, or None when the remote could not be asked.

    The local list is only as complete as the fetch that produced it: `actions/checkout`
    fetches no tags unless asked, so a gate reading `git tag` off a default checkout
    answers "none" whatever the truth is, and passes blind. `git ls-remote` is the
    authoritative answer, and it goes over the git protocol -- it costs no GitHub API
    quota at all.
    """

    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [  # noqa: S607 - git is the tool
            "git",
            "-C",
            str(root),
            "ls-remote",
            "--tags",
            "--refs",
            "origin",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if result.returncode != 0:
        return None
    return sorted(
        line.rsplit("refs/tags/", 1)[-1]
        for line in result.stdout.splitlines()
        if "refs/tags/" in line
    )


def strip_v(tag: str) -> str:
    return tag[1:] if tag.startswith("v") else tag


def newest_tag(tags: Sequence[str]) -> str:
    """The highest tag by numeric components, ties broken by name.

    Deliberately not `--sort=-creatordate`: a tag re-cut later would then read as newer
    than the release it replaced, and a shallow fetch may not carry creator dates at all.
    """

    def key(tag: str) -> tuple[tuple[int, ...], str]:
        return tuple(int(part) for part in re.findall(r"\d+", strip_v(tag))), tag

    return max(tags, key=key)


def version_against_tags(declared: str, tags: Sequence[str]) -> str | None:
    """The finding, or None when the declared version is answerable to the tags."""

    if not tags:
        return None
    if any(strip_v(tag) == declared for tag in tags):
        return None
    return (
        f"pyproject.toml declares version {declared!r}, and none of the {len(tags)} tag(s) in "
        f"this repository matches it. The newest tag is {newest_tag(tags)!r}. Either a release "
        "was cut without bumping the declared version, or the declared version has run ahead "
        "of what was released and should say so where a reader and a tool can both see it."
    )


#: Where a reader is told, in prose, that nothing has been released. Required while there
#: are no tags; retired by the commit that cuts the first one -- both directions gated.
UNRELEASED_STATEMENTS: tuple[tuple[str, str], ...] = (
    ("README.md", r"no release has shipped yet"),
    ("README.md", r"No release tag has been cut yet\."),
    ("CONTRIBUTING.md", r"no tag or release has shipped yet"),
    (
        "CHANGELOG.md",
        r"Until the owner cuts and dispatches that signed tag, nothing is\n> ?published",
    ),
)


def unreleased_statement_findings(root: Path, tags: Sequence[str]) -> list[str]:
    findings = []
    for name, pattern in UNRELEASED_STATEMENTS:
        text = (root / name).read_text(encoding="utf-8")
        present = re.search(pattern, text) is not None
        if not tags and not present:
            findings.append(
                f"{name} no longer tells a reader that nothing has been released (expected to "
                f"match {pattern!r}), and no tag exists to make that true."
            )
        if tags and present:
            findings.append(
                f"{name} still says nothing has been released, but {newest_tag(tags)!r} exists."
            )
    return findings


def prepared_release_findings(root: Path, declared: str, tags: Sequence[str]) -> list[str]:
    """What "prepared, not published" has to look like on disk while it is true.

    This is what stands in for the `.devN` suffix `ledger` declined. A dated CHANGELOG
    heading with no tag behind it is exactly the phantom release this portfolio keeps
    finding; it is allowed here only because the note beside it says what the heading does
    and does not mean, and because a checklist names what is still outstanding.
    """

    findings = []
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    dated_heading = re.search(
        rf"^## \[{re.escape(declared)}\] — \d{{4}}-\d{{2}}-\d{{2}}", changelog, re.MULTILINE
    )
    checklist = root / "docs" / f"RELEASE-{declared}.md"

    if not tags:
        if dated_heading is None:
            findings.append(
                f"CHANGELOG.md has no dated `## [{declared}]` heading. release.yml (REL-10) "
                "refuses to build a tag without one, so the release it prepares cannot be cut."
            )
        elif "nothing is\n> published" not in changelog:
            findings.append(
                f"CHANGELOG.md carries a dated `## [{declared}]` heading and no tag exists, but "
                "the note saying the heading means 'prepared for that tag' rather than "
                "'published' is gone. Without it the date reads as a release that happened."
            )
        if not checklist.is_file():
            findings.append(
                f"no docs/RELEASE-{declared}.md: the declared version has neither a tag behind "
                "it nor a checklist saying what is still outstanding before it can have one."
            )
    return findings


def restated_versions(root: Path) -> dict[str, str]:
    """Every place outside `pyproject.toml` that writes the version down again.

    `CITATION.cff`'s copy is also held by `tools/check_claims.py`
    (`citation-version-mirrors-pyproject`); it is re-checked here because this file is
    where a reader now comes to ask what the version means, and the duplication costs two
    lines. The CHANGELOG heading is not covered there at all, and `release.yml` refuses a
    tag whose section is missing -- so a heading that drifts blocks the release silently
    until someone tries to cut it.
    """

    citation = (root / "CITATION.cff").read_text(encoding="utf-8")
    citation_version = re.search(r'^version:\s*"([^"]+)"', citation, re.MULTILINE)
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    headings = re.findall(r"^## \[(?!Unreleased)([^\]]+)\]", changelog, re.MULTILINE)
    return {
        "CITATION.cff version": citation_version.group(1) if citation_version else "",
        "CHANGELOG.md newest dated section": headings[0] if headings else "",
    }


def restatement_findings(root: Path, declared: str) -> list[str]:
    return [
        f"{where} says {found!r}, but pyproject.toml declares {declared!r}."
        for where, found in restated_versions(root).items()
        if found != declared
    ]


DECLARED = declared_version()
TAGS = repository_tags()


# --- the repository as it actually is --------------------------------------------------


def test_the_declared_version_is_answerable_to_the_tags_that_exist() -> None:
    finding = version_against_tags(DECLARED, TAGS)
    assert finding is None, finding


def test_an_empty_tag_list_is_measured_rather_than_inherited_from_a_shallow_clone() -> None:
    """The reason `UNCOVERED` gave for not checking this claim, closed rather than accepted.

    An unfetched ref namespace looks exactly like a project that never released. Every
    workflow here that runs this suite checks out with `fetch-depth: 0`, which fetches
    `refs/tags/*` explicitly.
    """

    assert TAGS or not is_shallow(), (
        "this checkout is shallow and reports zero tags, which is indistinguishable from a "
        "repository that has never been released. Check out with fetch-depth: 0."
    )


def test_the_checkout_can_see_every_tag_the_remote_has() -> None:
    """The half the local tag list cannot prove about itself.

    A checkout that fetched no tags and a repository that has none are the same empty
    list, and the checks above would take the second reading and pass. The remote settles
    it. A skip here is a visible "not checked", not a pass: it means the remote could not
    be reached, which does not happen in CI.
    """

    published = remote_tags()
    if published is None:
        pytest.skip("the remote could not be reached; only this checkout's tag list is available")

    missing = sorted(set(published) - set(TAGS))
    assert not missing, (
        f"the remote has tag(s) this checkout cannot see: {missing}. A gate reading tags from "
        "this checkout would report 'never released' and pass. Check out with fetch-depth: 0."
    )

    finding = version_against_tags(DECLARED, published)
    assert finding is None, finding


def test_a_prepared_release_looks_like_one_on_disk() -> None:
    """`ledger` declined the `.devN` suffix; these are what it uses instead."""

    findings = prepared_release_findings(ROOT, DECLARED, TAGS)
    assert findings == [], findings


def test_every_document_that_restates_the_version_agrees_with_pyproject() -> None:
    findings = restatement_findings(ROOT, DECLARED)
    assert findings == [], findings


def test_the_documents_say_nothing_has_been_released_while_nothing_has() -> None:
    findings = unreleased_statement_findings(ROOT, TAGS)
    assert findings == [], findings


def test_the_package_reports_the_version_the_source_tree_declares() -> None:
    """REL-02 again, from the other end: `__version__` is derived, so it has to agree."""

    assert PACKAGE_VERSION == DECLARED, (
        f"the installed package reports {PACKAGE_VERSION!r} but pyproject.toml declares "
        f"{DECLARED!r} -- re-run `make install` so the metadata matches the source tree."
    )


# --- the branches this repository cannot reach ------------------------------------------


def test_a_tag_that_matches_the_declared_version_is_the_passing_case() -> None:
    assert version_against_tags("0.1.0", ["v0.1.0"]) is None
    assert version_against_tags("0.1.0", ["0.1.0"]) is None
    assert version_against_tags("0.2.0", ["v0.1.0", "v0.2.0"]) is None


@pytest.mark.parametrize(
    ("declared", "tags", "newest"),
    [
        ("0.1.0", ["v0.2.0"], "v0.2.0"),
        ("0.1.0", ["v0.0.9"], "v0.0.9"),
        ("0.3.0", ["v0.1.0", "v0.10.0", "v0.9.0"], "v0.10.0"),
    ],
)
def test_a_declared_version_no_tag_backs_is_a_failure_that_names_both(
    declared: str, tags: list[str], newest: str
) -> None:
    """A gate that cannot fail is not a gate, and this repository cannot reach the failing
    state on its own, so it is driven here with tag lists it does not have."""

    finding = version_against_tags(declared, tags)
    assert finding is not None
    assert repr(declared) in finding
    assert repr(newest) in finding


def test_the_newest_tag_is_chosen_numerically_not_lexically() -> None:
    assert newest_tag(["v0.9.0", "v0.10.0"]) == "v0.10.0"
    assert newest_tag(["v1.0.0", "v0.10.0"]) == "v1.0.0"


# --- negative controls: each check is shown to bite on a sabotaged copy ------------------


@pytest.fixture
def sabotage(tmp_path: Path) -> Path:
    """A copy of the documents these checks read, so a mutation cannot touch the repo."""

    copy = tmp_path / "repo"
    (copy / "docs").mkdir(parents=True)
    for name in ("README.md", "CONTRIBUTING.md", "CHANGELOG.md", "CITATION.cff", "pyproject.toml"):
        shutil.copy(ROOT / name, copy / name)
    shutil.copy(ROOT / "docs" / f"RELEASE-{DECLARED}.md", copy / "docs" / f"RELEASE-{DECLARED}.md")
    return copy


def test_the_clean_tree_produces_no_findings_at_all(sabotage: Path) -> None:
    """Without this, every negative control below could pass for the wrong reason."""

    assert declared_version(sabotage) == DECLARED
    assert restatement_findings(sabotage, DECLARED) == []
    assert unreleased_statement_findings(sabotage, []) == []
    assert prepared_release_findings(sabotage, DECLARED, []) == []


@pytest.mark.parametrize(("document", "pattern"), UNRELEASED_STATEMENTS)
def test_deleting_a_release_stance_sentence_is_caught(
    sabotage: Path, document: str, pattern: str
) -> None:
    path = sabotage / document
    before = path.read_text(encoding="utf-8")
    after = re.sub(pattern, "REMOVED", before)
    path.write_text(after, encoding="utf-8")

    assert after != before, f"the sabotage did not land: {pattern!r} never matched {document}"
    assert re.search(pattern, after) is None

    findings = unreleased_statement_findings(sabotage, [])
    assert any(document in finding for finding in findings), findings


def test_a_stance_sentence_left_standing_after_a_release_is_caught(sabotage: Path) -> None:
    """The other direction: the first tag has to retire these sentences."""

    findings = unreleased_statement_findings(sabotage, ["v0.1.0"])
    assert len(findings) == len(UNRELEASED_STATEMENTS)
    assert all("v0.1.0" in finding for finding in findings)


def test_a_dated_changelog_heading_losing_its_prepared_note_is_caught(sabotage: Path) -> None:
    """The phantom-release shape: a dated section with no tag and nothing saying so."""

    path = sabotage / "CHANGELOG.md"
    before = path.read_text(encoding="utf-8")
    after = before.replace("nothing is\n> published", "everything is\n> published", 1)
    path.write_text(after, encoding="utf-8")

    assert after != before, "the sabotage did not land: the prepared-not-published note moved"
    assert "nothing is\n> published" not in after

    findings = prepared_release_findings(sabotage, DECLARED, [])
    assert any("prepared for that tag" in finding for finding in findings), findings


def test_a_missing_release_checklist_is_caught(sabotage: Path) -> None:
    checklist = sabotage / "docs" / f"RELEASE-{DECLARED}.md"
    assert checklist.is_file()
    checklist.unlink()
    assert not checklist.exists(), "the sabotage did not land"

    findings = prepared_release_findings(sabotage, DECLARED, [])
    assert any("checklist" in finding for finding in findings), findings


@pytest.mark.parametrize(
    ("document", "old", "new"),
    [
        ("CITATION.cff", f'version: "{DECLARED}"', 'version: "9.9.9"'),
        ("CHANGELOG.md", f"## [{DECLARED}] \u2014", "## [9.9.9] \u2014"),
    ],
)
def test_a_restated_version_drifting_from_pyproject_is_caught(
    sabotage: Path, document: str, old: str, new: str
) -> None:
    path = sabotage / document
    before = path.read_text(encoding="utf-8")
    after = before.replace(old, new, 1)
    path.write_text(after, encoding="utf-8")

    assert after != before, f"the sabotage did not land: {old!r} not found in {document}"
    assert "9.9.9" in after

    findings = restatement_findings(sabotage, DECLARED)
    assert any("9.9.9" in finding for finding in findings), findings
