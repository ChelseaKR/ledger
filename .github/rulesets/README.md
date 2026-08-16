# Committed ruleset evidence

`main.json` is a byte-for-meaning mirror of the **live**, repository-owned GitHub
ruleset named `protect-main`, so the branch-protection posture is reviewable in the
tree instead of only in a settings page nobody can diff. `CI-CD-STANDARD.md` §5 asks
for exactly this artifact, and `CODEOWNERS` routes this directory to the maintainer.

Fetched from `GET /repos/ChelseaKR/ledger/rulesets/18823575` on **2026-08-15**. The
live ruleset carried `"updated_at": "2026-07-11T20:44:59.635-07:00"` at that moment.
The API returned `"bypass_actors": []` — a *visible* empty list, not a redaction.

Two fields are dropped from the mirror on purpose because they are server-assigned
and would make an honest diff impossible: `id` and `updated_at`. Everything the
standard treats as a security-relevant setting is reproduced verbatim.

## This file is evidence, not a model

Do not copy `main.json` into another repository as a starting profile. It records
what ledger's protection *is* today, and today that is **weaker than the portfolio
floor**. `ChelseaKR/outcome-receipts` carries `.github/rulesets/main.json` at the
`CI-CD-STANDARD.md` §5.1 solo-maintainer profile and is the profile to copy.

Measured against `CI-CD-STANDARD.md` §5/§5.1, this profile is missing:

| Floor | §5 target | Here |
|---|---|---|
| `pull_request` rule [CICD-12/14] | required; stale reviews dismissed on push | rule absent entirely |
| `required_signatures` | required | absent |
| `required_linear_history` | required | absent |
| `strict_required_status_checks_policy` [CICD-13] | `true` | `false` |
| `require_code_owner_review` [CICD-18] | `true`, or reasoned `false` under §5.1 | no PR rule to carry it |
| Required-check set [CICD-13] | every merge-blocking gate | `OSV lockfile scan (uv.lock)` and `Semgrep SAST (p/ci)` run on every pull request and are not in the set |

What this profile *does* hold: `deletion` and `non_fast_forward` are blocked, eleven
status checks are required, and `bypass_actors` is empty (no break-glass actor, so
`current_user_can_bypass` reads `never`).

Choosing the review model that closes the first five rows is an owner decision, not
an automatable one — it is tracked in
[#79](https://github.com/ChelseaKR/ledger/issues/79). The required-check set is a
separate, narrower fix.

## Keeping the mirror honest

A committed mirror that silently stops matching the live ruleset is worse than no
mirror at all. Two things guard it:

- `tools/check_claims.py` fails the build if any `context` named here does not match
  the `name:` of a real job in `.github/workflows/`, which is how a renamed job would
  otherwise quietly drop out of the required set.
- Any change to the live ruleset must land here in the same breath. There is no
  automated live-versus-committed parity check in this repo yet; the portfolio
  standards repo's `automation/check_ruleset_profile.py --hosted` is the tool that
  does it, and wiring it in here waits on the same review-model decision, because it
  validates the full §5 profile and would fail against the gaps listed above.
