# Committed ruleset evidence

`main.json` is a byte-for-meaning mirror of the **live**, repository-owned GitHub
ruleset named `protect-main`, so the branch-protection posture is reviewable in the
tree instead of only in a settings page nobody can diff. `CI-CD-STANDARD.md` §5 asks
for exactly this artifact, and `CODEOWNERS` routes this directory to the maintainer.

Fetched from `GET /repos/ChelseaKR/ledger/rulesets/18823575` on **2026-08-21**, and
re-read on **2026-08-28**. The live ruleset carried
`"updated_at": "2026-08-21T04:00:06.895-07:00"` at the first of those moments — the
same change that closed the gaps below (#79).

Two fields are dropped from the mirror on purpose because they are server-assigned
and would make an honest diff impossible: `id` and `updated_at`. Everything the
standard treats as a security-relevant setting is reproduced verbatim.

## Why the owner can bypass

`bypass_actors` holds exactly the repository owner's standing bypass
(`RepositoryRole` 5, `bypass_mode: always`), deliberately and permanently: an agent
once applied a ruleset with no bypass and locked the owner out of their own
repository, and restoring access took a sweep across eighteen repositories. An empty
list here is not a stricter gate, it is the lockout.

This file used to read the other way. It said the API returned `"bypass_actors": []`
— "a *visible* empty list, not a redaction" — and offered that as evidence the
posture was tight. The mirror was wrong, not the live ruleset: the 2026-08-28 read of
`GET /repos/ChelseaKR/ledger/rulesets/18823575` returns the owner's bypass and
`"current_user_can_bypass": "always"`, and it has been the correct configuration
throughout. The committed file is what changed to match; **no live ruleset or
repository setting was touched.**

The distinction that mattered in the old sentence still holds and is worth keeping:
GitHub redacts `bypass_actors` from callers without ruleset write access, so an empty
list read by an unprivileged token proves nothing. What the old sentence got wrong was
treating *empty* as the good answer. The good answer is *exactly one actor, and it is
the owner's own*: a second bypass handed to a team, a GitHub App or another repository
role is the thing actually worth catching, and so is the owner's going missing.

A standing admin bypass is a recovery path, not a merge policy. Every change to `main`
still goes through a pull request with the thirteen required checks green; using the
bypass routinely would be a defect in practice, which no ruleset field can prevent and
an empty list does not either — it only removes the way back in.

If you are reading this because the empty list looked more secure and you are about to
restore it: re-applying a ruleset file that omits the owner's bypass is how the lockout
happens. Do not.

## The solo-maintainer-safe review model this profile chose

`CI-CD-STANDARD.md` §5.1 lets a solo-maintainer repo hold the §5 floor without a
second human reviewer, provided the choices are explicit and dated rather than
silently absent. This profile now holds:

- **`pull_request` rule**, `required_approving_review_count: 0`. Every change to
  `main` must go through a pull request — direct pushes are blocked — but zero
  approvals are required, because a sole maintainer cannot approve their own PR on
  GitHub and requiring one would make the repository unmergeable by its only
  contributor. `dismiss_stale_reviews_on_push: true` and
  `required_review_thread_resolution: true` are set anyway: they cost nothing today
  and hold if a second maintainer ever joins.
- **`require_code_owner_review: false`, reasoned.** `CODEOWNERS` routes every path to
  `@ChelseaKR`, the same person the `pull_request` rule already can't get a
  self-approval from — setting this `true` would require an approval no one can give,
  not add a real second reader. §5.1 permits `false` here exactly when the sole code
  owner is also the sole approver it would otherwise demand.
- **`required_signatures: true`.** Every merge into `main` happens through
  `gh pr merge --squash`, which GitHub signs server-side with its own key
  (`git log --show-signature` shows recent merges signed by GitHub's RSA key
  `B5690EEEBB952194`) — so this holds without the maintainer managing a personal
  signing key for ordinary commits. (Release *tags* are a separate signer identity,
  tracked under #80.)
- **`required_linear_history: true`.** Every merge is a squash merge already; this
  makes that a server-enforced property instead of a convention.
- **`strict_required_status_checks_policy: true`.** A PR branch must be up to date
  with `main` before its checks count, closing the stale-base-branch gap CICD-13
  names.
- **Required-check set: thirteen contexts**, up from eleven — `OSV lockfile scan
  (uv.lock)` and `Semgrep SAST (p/ci)` (SEC-11/13, CICD-13/27) now block a merge
  instead of only running fail-closed and being ignorable.

This is the profile `ChelseaKR/outcome-receipts` already carries at
`.github/rulesets/main.json`; ledger's now matches it in shape, applied to this
repo's own required-check set.

## What is still open

Local `make verify` has no Semgrep target — CI's `semgrep` workflow is the gate of
record (matching the existing `osv-scanner`/`gitleaks` local-target pattern, which
call out CI as authoritative the same way), but a contributor cannot get the same
signal before pushing. Tracked in `docs/ROADMAP.md`'s open conformance gaps as a
narrower, separate item from #79.

## Keeping the mirror honest

A committed mirror that silently stops matching the live ruleset is worse than no
mirror at all. Two things guard it:

- `tools/check_claims.py` fails the build if any `context` named here does not match
  the `name:` of a real job in `.github/workflows/`, which is how a renamed job would
  otherwise quietly drop out of the required set.
- `tools/check_claims.py` also fails the build if this file stops recording the
  owner's standing bypass, or starts recording a second bypass actor. That is the
  committed half of the check; `check_claims.bypass_findings()` is the whole of it,
  and it holds the live ruleset and this file against the owner's bypass
  **independently** rather than comparing the two to each other. Comparing them is
  what a mirror-parity check naturally does, and it is exactly wrong here: if a future
  edit put the empty list back into this file on a day the owner had also been locked
  out, the two sides would agree and the parity check would report conformance on the
  incident it exists to catch. Both sides emptied together is two findings, not zero,
  and `tests/test_claims_gate.py` pins that case.
- Any change to the live ruleset must land here in the same breath. There is no
  automated live-versus-committed parity check in this repo yet; the portfolio
  standards repo's `automation/check_ruleset_profile.py --hosted` is the tool that
  does it, and wiring it in here is now unblocked (the review-model decision it was
  waiting on is made) but not yet done. When it is wired in, it must feed
  `bypass_findings()` rather than diff the two bypass lists.
