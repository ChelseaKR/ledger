# Committed ruleset evidence

`main.json` is a byte-for-meaning mirror of the **live**, repository-owned GitHub
ruleset named `protect-main`, so the branch-protection posture is reviewable in the
tree instead of only in a settings page nobody can diff. `CI-CD-STANDARD.md` §5 asks
for exactly this artifact, and `CODEOWNERS` routes this directory to the maintainer.

Fetched from `GET /repos/ChelseaKR/ledger/rulesets/18823575` on **2026-08-21**, and
re-read on **2026-08-29**. At the first reading the live ruleset carried
`"updated_at": "2026-08-21T04:00:06.895-07:00"` — the same change that closed the gaps
below (#79) — and returned `"bypass_actors": []`, a *visible* empty list, not a
redaction.

That field has since changed on the server and the mirror had not followed it. The
re-read on 2026-08-29 returned `"updated_at": "2026-08-26T21:27:37.882-07:00"` and

```json
"bypass_actors": [
  { "actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always" }
]
```

with `"current_user_can_bypass": "always"`. The mirror now carries that, and the section
below says why it is permanent rather than incidental.

Two fields are dropped from the mirror on purpose because they are server-assigned
and would make an honest diff impossible: `id` and `updated_at`. Everything the
standard treats as a security-relevant setting is reproduced verbatim.

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

## The owner's standing bypass

`bypass_actors` carries exactly one entry, deliberately and permanently:

```json
{ "actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always" }
```

`RepositoryRole` 5 is admin. An empty list here is not a stricter gate, it is a lockout:
this profile requires a pull request, thirteen contexts, a strict up-to-date policy,
signatures and linear history, and with no bypass actor a single wedged check leaves the
sole maintainer unable to merge, unable to push, and unable to delete the ruleset that is
blocking them. That is not hypothetical — an agent applied a no-bypass ruleset elsewhere
in this portfolio and restoring access took a sweep across eighteen repositories.

`bypass_mode: "always"` rather than `"pull_request"`, which is what the portfolio's
CI-CD-STANDARD suggests, because a bypass that only works inside a pull request is no use
when the thing that is wedged is the pull request. One actor, and a repository role rather
than a team or a GitHub App: a second entry in this list would be a real finding.

This matters here more than in a repository that only mirrors. `main.json` is a file
somebody re-applies — `gh api -X POST repos/ChelseaKR/ledger/rulesets --input
.github/rulesets/main.json` posts it exactly as it stands, and GitHub answers 201 whether
or not the bypass survived. Mirroring the live ruleset faithfully and being safe to
re-apply are two different properties, and until #79's mirror was corrected this file had
the first and not the second.

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
- `tests/test_ruleset_lockout.py` fails the build if this file would lock the owner out
  when re-applied: an empty `bypass_actors`, an absent key, a non-list, a different
  actor, or the owner with `bypass_mode: "pull_request"`. It parses rather than greps,
  because a truncated JSON file still contains the string `bypass_actors`, and it fails
  on a missing or unparseable file rather than reading one as "nothing wrong".
- Any change to the live ruleset must land here in the same breath. There is no
  automated live-versus-committed parity check in this repo yet; the portfolio
  standards repo's `automation/check_ruleset_profile.py --hosted` is the tool that
  does it, and wiring it in here is now unblocked (the review-model decision it was
  waiting on is made) but not yet done.
