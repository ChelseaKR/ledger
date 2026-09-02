# Release checklist — ledger 0.1.0

**Status:** release candidate; not yet a published release.

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
- [ ] `CHANGELOG.md` has a dated `## [0.1.0]` section that reflects the exact tag.
- [ ] `CITATION.cff` gets a `date-released` matching the tag date, and its `version`
  matches the tagged `pyproject.toml` version. Both are gated: the
  `citation-claims-no-release-date` claim in `tools/check_claims.py` forbids the key
  while no tag exists, so retire that claim in the same commit that cuts the tag.
- [ ] `make verify` passes at the exact commit to be tagged.

## Tag and verify

- [ ] Create and verify a signed annotated `v0.1.0` tag at the checked commit.
- [ ] Push the tag and let `release.yml` run its full gate, build, SBOM,
  provenance, cosign signing, trusted PyPI publishing, and post-publication
  checksum verification.
- [ ] Confirm the GitHub Release contains the wheel, source distribution, SBOM, and
  signatures and that its notes match `CHANGELOG.md`.
- [ ] Install the published package into a clean environment and run `ledger --help`.

## Rollback and communication

- [ ] If the tagged release or artifacts fail verification, stop publication and
  document the failed attempt; do not retag a different commit with the same version.
- [ ] If a released artifact needs withdrawal, follow `SECURITY.md`, record the reason
  in the changelog, and publish a new fixed version rather than silently replacing
  `0.1.0`.
- [ ] Announce only the verified scope: pre-1.0 reference implementation, synthetic
  demo available, and no claim of production suitability beyond completed evidence.
