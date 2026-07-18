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
- [ ] `ledger-archive` is registered on PyPI and a trusted publisher is configured
  for `ChelseaKR/ledger`, `.github/workflows/release.yml`, environment `pypi`.
- [ ] The GitHub `pypi` environment exists with the intended protection rule.
- [ ] `CHANGELOG.md` has a dated `## [0.1.0]` section that reflects the exact tag.
- [ ] `CITATION.cff`'s release date matches the tag date.
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
