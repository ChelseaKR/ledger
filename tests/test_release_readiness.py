"""The repo half of the release path, gated so it cannot rot before first use.

`release.yml` has never run: there are no tags and no releases on this
repository. Everything it depends on is therefore *unexercised*, and the first
time any of it is tested for real is the moment a version tag becomes public --
the single worst moment in this repo to discover a wrong value, because a PyPI
version number cannot be reused and a burnt release is not a retry (REL-03/08/
13, #80).

So the parts that CAN be checked from the tree are checked here, every build:

* the committed SSH allowed-signers list the tag-signature check trusts,
* that the workflow actually points its `git verify-tag` at that file,
* that the publish job is genuinely tokenless and environment-scoped, and
* that the three Trusted Publisher values a human has to type into pypi.org --
  repository, workflow filename, environment name -- plus the project name,
  are the ones the workflow really declares, and are stated identically in the
  runbook a human will read.

None of this can create, sign, or push a tag, and none of it publishes
anything. It exists so that when the owner does, the only thing left is the
part only she can do.
"""

from __future__ import annotations

import base64
import re
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.preservation

_ROOT = Path(__file__).resolve().parent.parent
_ALLOWED_SIGNERS = _ROOT / ".github" / "allowed_signers"
_RELEASE_WORKFLOW = _ROOT / ".github" / "workflows" / "release.yml"
_RUNBOOK = _ROOT / "docs" / "RELEASE-0.1.0.md"

#: The repository owner, as the only principal trusted to sign a release tag.
_OWNER_PRINCIPAL = "3114598+ChelseaKR@users.noreply.github.com"
#: A syntactically real ed25519 SSH public key body, used only to build the
#: deliberately-broken fixtures below. It is a throwaway, not a trusted key.
_REAL_KEY_PLACEHOLDER = base64.b64encode(
    b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00 " + bytes(32)
).decode()

#: The three values that must match between the workflow, the runbook, and the
#: form on pypi.org. A mismatch is not caught by any build; it surfaces as an
#: OIDC audience error partway through the first real publish.
TRUSTED_PUBLISHER = {
    "repository": "ChelseaKR/ledger",
    "workflow": "release.yml",
    "environment": "pypi",
}


def _signer_entries(path: Path) -> list[tuple[str, str, str]]:
    """Parse the allowed-signers file into (principal, key type, key body).

    Deliberately strict: OpenSSH's own format allows options, wildcards and
    `cert-authority` entries, none of which belong in a release-signing list,
    and a permissive parser that skipped what it did not understand would be a
    gate that stops gating exactly when someone adds something unusual.
    """
    entries: list[tuple[str, str, str]] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 3:
            raise AssertionError(
                f"{path.name}:{lineno}: expected exactly "
                f"'<principal> <keytype> <key>', got {len(parts)} fields. Options, "
                "wildcards and cert-authority entries are not accepted here."
            )
        entries.append((parts[0], parts[1], parts[2]))
    return entries


def _validate_signers(path: Path) -> None:
    """Every rule the release-signing list must satisfy. Raises on any breach.

    One function, used both against the real committed file and against
    deliberately broken fixtures, so the rules and their proof cannot drift.
    """
    assert path.is_file(), f"{path} is missing"
    assert path.read_text(encoding="utf-8").strip(), "the file is empty"
    entries = _signer_entries(path)
    assert entries, "no signer entries; every tag would fail verification"
    assert len(entries) == 1, f"expected exactly one trusted signer, found {len(entries)}"
    principal, key_type, key_body = entries[0]
    assert principal == _OWNER_PRINCIPAL, f"unexpected release-signing principal {principal!r}"
    assert key_type == "ssh-ed25519", (
        f"release tags are signed with an Ed25519 key; the list names {key_type!r}"
    )
    decoded = base64.b64decode(key_body, validate=True)
    assert decoded.startswith(b"\x00\x00\x00\x0bssh-ed25519"), (
        "the key body does not decode to an ssh-ed25519 public key"
    )


def test_the_committed_allowed_signers_list_is_exactly_the_owners_key() -> None:
    """One key, one principal, no second actor, and the file is really there.

    `git verify-tag` against an empty or missing list rejects every tag -- not a
    permissive failure, but one that lands after the tag is public, in a
    workflow nobody has ever run. And a list that quietly grows a second entry
    is a change nobody reviewed, invisible to every other gate. Same reasoning
    as the ruleset's `bypass_actors` check.
    """
    _validate_signers(_ALLOWED_SIGNERS)


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param("", id="empty"),
        pytest.param("   \n\n", id="whitespace-only"),
        pytest.param("# only a comment\n", id="comments-only"),
        pytest.param(f"* ssh-ed25519 {_REAL_KEY_PLACEHOLDER}\n", id="wildcard-principal"),
        pytest.param(f"{_OWNER_PRINCIPAL} ssh-rsa AAAAB3NzaC1yc2E=\n", id="wrong-key-type"),
        pytest.param(
            f"cert-authority {_OWNER_PRINCIPAL} ssh-ed25519 {_REAL_KEY_PLACEHOLDER}\n",
            id="cert-authority-option",
        ),
        pytest.param(
            f"{_OWNER_PRINCIPAL} ssh-ed25519 {_REAL_KEY_PLACEHOLDER}\n"
            f"someone-else@example.com ssh-ed25519 {_REAL_KEY_PLACEHOLDER}\n",
            id="two-signers",
        ),
        pytest.param(f"{_OWNER_PRINCIPAL} ssh-ed25519 bm90LWEta2V5\n", id="not-an-ed25519-key"),
    ],
)
def test_the_signer_rules_reject_a_bad_list(bad: str, tmp_path: Path) -> None:
    """Anti-vacuity: prove every rule above can actually fail.

    Without this, the check on the real file is satisfiable by a validator that
    happens to accept the one list in the tree and nothing else is ever tried.
    """
    path = tmp_path / "allowed_signers"
    path.write_text(bad, encoding="utf-8")
    with pytest.raises((AssertionError, ValueError)):
        _validate_signers(path)


def test_the_workflow_verifies_the_tag_against_that_exact_file() -> None:
    """The list is only worth gating if the workflow actually consults it."""
    text = _RELEASE_WORKFLOW.read_text(encoding="utf-8")
    assert "git verify-tag" in text, "release.yml does not verify the tag signature at all"
    assert "gpg.ssh.allowedSignersFile" in text
    assert ".github/allowed_signers" in text, (
        "release.yml points its allowed-signers file somewhere other than the "
        "committed .github/allowed_signers"
    )
    assert "git config gpg.format ssh" in text, (
        "without `gpg.format ssh`, `git verify-tag` looks for a GPG signature and "
        "rejects the SSH-signed tag this project actually creates"
    )
    # An annotated tag object, not a lightweight ref: a lightweight tag carries no
    # signature at all, so a signature check that never asserts the object type
    # would pass a tag it cannot have verified.
    assert 'git cat-file -t "refs/tags/${TAG}")" = tag' in text


def test_the_publish_job_is_tokenless_and_environment_scoped() -> None:
    """PyPI Trusted Publishing, with the second half of the scoping in place.

    `id-token: write` alone lets ANY workflow in this repository that can mint an
    OIDC token publish. PyPI treats a blank environment as "any", so the
    `environment:` block is what narrows it to a job the repository can protect
    independently. Its absence is silent and widening -- exactly the shape worth
    a test.
    """
    text = _RELEASE_WORKFLOW.read_text(encoding="utf-8")
    assert "id-token: write" in text
    assert f"name: {TRUSTED_PUBLISHER['environment']}" in text, (
        "the publish job declares no GitHub Environment; PyPI would accept a token "
        "minted by any workflow in this repository"
    )
    assert "pypa/gh-action-pypi-publish@" in text
    # No long-lived credential anywhere in the release path (REL-13).
    for forbidden in ("secrets.PYPI", "password:", "PYPI_API_TOKEN", "TWINE_PASSWORD"):
        assert forbidden not in text, (
            f"release.yml references {forbidden!r}; Trusted Publishing means no stored token"
        )


def test_the_pypi_project_name_is_the_distribution_name() -> None:
    """The Trusted Publisher is registered against a PyPI *project name*.

    If the workflow's project URL and the distribution `pyproject.toml` builds
    disagree, the publisher is configured for a project this repo never uploads
    to, and the failure appears at publish time.
    """
    project = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    dist_name = project["name"]
    text = _RELEASE_WORKFLOW.read_text(encoding="utf-8")
    assert f"https://pypi.org/project/{dist_name}/" in text, (
        f"release.yml's environment URL does not name the built distribution {dist_name!r}"
    )
    assert dist_name in _RUNBOOK.read_text(encoding="utf-8")


def test_the_workflow_filename_is_the_one_the_docs_tell_pypi_to_expect() -> None:
    """A Trusted Publisher names a workflow FILE. Renaming the file silently
    invalidates the publisher, and nothing else in the build would notice."""
    assert _RELEASE_WORKFLOW.name == TRUSTED_PUBLISHER["workflow"]
    runbook = _RUNBOOK.read_text(encoding="utf-8")
    assert TRUSTED_PUBLISHER["workflow"] in runbook
    assert TRUSTED_PUBLISHER["repository"] in runbook
    assert f"environment `{TRUSTED_PUBLISHER['environment']}`" in runbook


def test_the_runbook_still_says_no_release_has_been_published() -> None:
    """The one claim in this area that must be retired deliberately, not drift.

    `docs/RELEASE-0.1.0.md` describes a release that has been prepared and not
    published. Publication is a fact about the remote's tags and about PyPI, not
    about this tree, which is why it sits in the claims gate's published
    ``UNCOVERED`` list rather than being asserted here. What this test holds is
    the direction that *is* local: the sentence stays until someone deletes it on
    purpose, in the commit that follows a real publish.
    """
    assert "not yet a published release" in _RUNBOOK.read_text(encoding="utf-8")


def test_the_changelog_has_a_section_for_the_declared_version() -> None:
    """`release.yml`'s REL-10 check, run on every build instead of after the tag.

    The workflow refuses to build a ``vX.Y.Z`` tag whose version has no matching
    ``## [X.Y.Z]`` heading in CHANGELOG.md. That check is correct and it is also
    far too late: it runs against a tag that is already public, and a PyPI version
    number cannot be reused, so failing it burns the version rather than retrying
    it. Running the identical check here makes the same mistake a red pull request.

    The reverse direction is the other half of the same coupling. A ``.dev``
    version with a dated section for it would announce a release that cannot be
    installed at all -- ``pip install`` skips PEP 440 developmental releases unless
    asked for one by name -- so a dated section and a ``.dev`` version must never
    appear together.
    """
    version = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    changelog = (_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    developmental = re.match(r"^(\d+\.\d+\.\d+)\.dev\d+$", version)
    if developmental:
        # The heading a `.dev` version would wrongly claim is the one for the release
        # it is developing *toward* -- `0.1.0`, not `0.1.0.dev0`. Checking for the
        # literal version string here would look for `## [0.1.0.dev0]`, which is
        # absent for the boring reason that nobody writes a changelog heading that
        # way, and the check would pass on exactly the state it exists to catch.
        heading = f"## [{developmental.group(1)}]"
        assert heading not in changelog, (
            f"CHANGELOG.md has a {heading!r} section, but pyproject declares the "
            f"developmental version {version!r}, which `pip install` will not install; "
            "finish the release bump or move the section back under [Unreleased]"
        )
        return
    heading = f"## [{version}]"
    assert re.match(r"^\d+\.\d+\.\d+$", version), (
        f"pyproject version {version!r} is neither a stable SemVer version nor a .dev "
        "version; release.yml only accepts a `vX.Y.Z` tag and matches it against this"
    )
    assert heading in changelog, (
        f"pyproject declares {version!r} but CHANGELOG.md has no {heading!r} section. "
        "release.yml (REL-10) would fail this check *after* the tag is public, which "
        "burns the version number instead of retrying it"
    )
