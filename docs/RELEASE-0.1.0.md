# Release checklist — ledger 0.1.0

**Status:** prepared; not yet a published release.

The repository side is done. `pyproject.toml` declares `0.1.0` (not a `.dev`
version, which `pip install` would skip), `CHANGELOG.md` carries a dated
`## [0.1.0]` section, and `CITATION.cff` mirrors the version. What is left is
owner-only: the three pypi.org / GitHub settings below, then a signed tag and one
`workflow_dispatch`.

This checklist separates work that is verifiable from the repository from the
owner-controlled and human-review prerequisites that cannot honestly be automated.
`v0.1.0` must not be pushed until every required item is checked.

## Required before tagging

- [ ] Independent threat-model/security review is completed, its approved findings
  are recorded, and public claims are updated. See
  [the review packet](reviews/threat-model-review.md).
- [ ] Manual NVDA/Firefox and VoiceOver/Safari review passes are completed and dated.
  See [the accessibility review packet](reviews/manual-accessibility-review.md).
- [ ] The accountable owner reviews and signs the residual-risk register.
- [ ] A signed annotated tag signer identity is documented and approved.
      *Repo side done:* the approved public key is committed at
      [`.github/allowed_signers`](../.github/allowed_signers) and `release.yml`
      verifies every release tag against it. `tests/test_release_readiness.py`
      fails the build if that list is emptied, gains a second signer, or stops
      naming an Ed25519 key. **Owner action:** confirm the committed key is the
      one you intend to sign with — it is the same key already trusted by
      `tods-validate`.
- [ ] `ledger-archive` is registered on PyPI and a trusted publisher is configured
  for `ChelseaKR/ledger`, `.github/workflows/release.yml`, environment `pypi`.
- [ ] The GitHub `pypi` environment exists with the intended protection rule.

### The owner-only steps, in full

Everything below needs an account only the owner has. They are written out here
so they can be followed without rediscovering them, and every value is asserted
against the workflow by `tests/test_release_readiness.py`, so this list cannot
drift from what `release.yml` actually declares.

**1 — Create the GitHub Environment (do this first).**
`Settings → Environments → New environment`, named exactly:

```
pypi
```

Optionally add a deployment branch/tag rule limited to the tag pattern `v*`, so
a `workflow_dispatch` from a branch is refused before it starts rather than
partway through a publish. Do this *before* the first release run: the
`publish-pypi` job already declares `environment: name: pypi`, and a run whose
environment does not exist does not start.

**2 — Register the project on PyPI.**
A never-before-published name cannot be claimed by OIDC alone. On
[pypi.org](https://pypi.org/manage/account/publishing/), use **"Add a new
pending publisher"** for the project name `ledger-archive`. (The alternative,
one manual `twine upload`, would publish an artifact that never went through
this workflow's gates and is not recommended.)

**3 — Fill in the Trusted Publisher form.** Exactly these five values:

| Field | Value |
| --- | --- |
| PyPI Project Name | `ledger-archive` |
| Owner | `ChelseaKR` |
| Repository name | `ledger` |
| Workflow name | `release.yml` |
| Environment name | `pypi` |

Leaving *Environment name* blank means "any workflow in this repository that
can mint an OIDC token may publish". Fill it in.

No API token is created, stored, or pasted anywhere at any point. `release.yml`
holds no `secrets.*` reference, and the build asserts that it never gains one.

**4 — Everything after that is already wired** and needs no further setup:
version/tag agreement, the full `make verify` at the tagged commit, the
CHANGELOG section check, tag-signature verification against the committed
allowed-signers list, the CycloneDX SBOM, SLSA build provenance, keyless cosign
signatures, the PyPI publish, the re-download-and-verify pass, and the GitHub
Release.
- [x] `pyproject.toml` declares the release version `0.1.0`. It is deliberately not
  a `.dev` version: `pip install ledger-archive` skips PEP 440 developmental
  releases, so a `.dev` string cannot be what ships. Nothing bumps this
  automatically — `release.yml`'s build job *asserts* the dispatched tag matches it.
- [x] `CHANGELOG.md` has a dated `## [0.1.0]` section. **Check the date matches the
  day you actually tag** — it is written as the day the release was prepared, and
  `release.yml` only greps for the `## [0.1.0]` heading, so a stale date will not
  fail the build. It is the one value here a machine will not catch for you.
- [x] `CITATION.cff`'s `version` matches `pyproject.toml` (gated by the
  `citation-version-mirrors-pyproject` claim).
- [ ] `CITATION.cff` gets a `date-released` matching the tag date. Deliberately still
  absent: the `citation-claims-no-release-date` claim in `tools/check_claims.py`
  forbids the key while no tag exists, and a prepared release has no release date.
  Add the key and retire that claim in the follow-up commit after publication —
  see "After it publishes" below.
- [ ] `make verify` passes at the exact commit to be tagged.

## Tag and verify

`release.yml` is **`workflow_dispatch` only** — it has no `push: tags:` trigger.
Pushing the tag does not start it; pushing the tag and then dispatching the
workflow does. That is deliberate (the tag exists and is signed *before* anything
reads it), but it means a pushed tag sitting with no run is the expected state, not
a broken one.

- [ ] Create a signed annotated tag at the merge commit on `main`, with the key in
  `.github/allowed_signers`:

  ```sh
  git -C ~/portfolio/ledger checkout main && git pull
  git tag -s v0.1.0 -m "ledger 0.1.0"
  git verify-tag v0.1.0     # must pass locally before the tag is pushed
  git push origin v0.1.0
  ```

- [ ] Dispatch the workflow against that tag — this is the button press:

  ```sh
  gh workflow run release.yml --repo ChelseaKR/ledger --ref main -f tag=v0.1.0
  gh run watch --repo ChelseaKR/ledger
  ```

  `--ref main` is required: the `verify` job asserts `GITHUB_REF` is
  `refs/heads/main` and that the tagged commit is an ancestor of `origin/main`.

- [ ] Confirm the GitHub Release contains the wheel, source distribution, SBOM, and
  signatures and that its notes match `CHANGELOG.md`.
- [ ] Install the published package into a clean environment and run `ledger --help`.

## After it publishes

These retire claims that are true only while nothing is published, and must not be
changed before the release actually succeeds:

- [ ] `CITATION.cff`: add `date-released: <tag date>` and delete the comment
  explaining its absence; retire the `citation-claims-no-release-date` claim in
  `tools/check_claims.py` and its test in `tests/test_claims_gate.py`.
- [ ] `README.md`: replace the four "no release has shipped yet" statements
  (§ badges, § release-producing, § standards table, § supply chain).
- [ ] `CHANGELOG.md`: add the `[Unreleased]` compare link and the `[0.1.0]` release
  link recorded in the comment at the foot of the file.
- [ ] `docs/RELEASE-0.1.0.md`: this file's "not yet a published release" status, and
  the test in `tests/test_release_readiness.py` that holds it there.
- [ ] `docs/ROADMAP.md`: the REL-03/08/17/20 row and the SEC-04 row — the first real
  run is what unblocks flipping `release.yml`'s eight Harden-Runner jobs from
  `audit` to `block` ([#78](https://github.com/ChelseaKR/ledger/issues/78)); read
  the observed endpoints off that run rather than guessing them.

## Rollback and communication

- [ ] If the tagged release or artifacts fail verification, stop publication and
  document the failed attempt; do not retag a different commit with the same version.
- [ ] If a released artifact needs withdrawal, follow `SECURITY.md`, record the reason
  in the changelog, and publish a new fixed version rather than silently replacing
  `0.1.0`.
- [ ] Announce only the verified scope: pre-1.0 reference implementation, synthetic
  demo available, and no claim of production suitability beyond completed evidence.
