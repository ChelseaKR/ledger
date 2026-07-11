# Mutation testing (advisory)

Branch coverage (`make cov`, floor 85% in `pyproject.toml`) proves a line *ran*;
it does not prove a test would *notice* if that line were wrong. For ledger's
safety-critical core — who gets to see what (`access/`), who a contributor
really is (`identity.py`), and whether a preserved file has silently changed
(`fixity.py`) — that gap is exactly where a no-outing or corruption bug would
hide behind green coverage. Mutation testing closes it:
[mutmut](https://mutmut.readthedocs.io/) systematically introduces small faults
("mutants") into the code and reports which ones the existing test suite fails
to catch ("survivors"). A surviving mutant is a concrete, reproducible bug the
suite would ship.

This closes CODE-QUALITY-STANDARD **CQ-47** (`docs/ROADMAP.md`). It is
**advisory only**: it is **never** a merge gate, never runs on `pull_request` or
`push`, and never blocks. The blocking correctness/disclosure gates stay `make
test` (via `gate` and `no-outing-audit` in `ci.yml`) and the 85% branch-coverage
floor. Mutation score is a slower, deeper health signal watched weekly.

## Scope

Deliberately the three modules CQ-47 names, plus the rest of `access/`
(`grants.py`, `redaction.py`) it groups under the same package:

| Module | What it decides | Why it is in scope |
| --- | --- | --- |
| `src/ledger/access/policy.py` | The single visibility decision point (`is_visible`, `disclose`, `withheld_reason`) | The place the no-outing / disclosure rule is actually decided for every read. |
| `src/ledger/access/grants.py` | Grant construction, including loading a subject → grant mapping from an on-disk JSON file (`load_grants`) | A quietly wrong grant (or a silently ignored one from a malformed file) is a privilege-escalation or under-provisioning bug with no crash to notice it by. |
| `src/ledger/access/redaction.py` | Destructive field/payload redaction | A no-op or partial redaction would leave the exact value it was told to remove. |
| `src/ledger/identity.py` | The encrypted contributor-identity vault (encrypt, decrypt, rekey, resolve) | The single place a contributor's real identity is ever stored; a bug here is a direct outing risk. |
| `src/ledger/fixity.py` | Content-hash computation and verification | A weakened comparison here would silently accept a corrupted or tampered preserved file as fixity-valid. |

Configuration lives in `[tool.mutmut]` in `pyproject.toml`. The kill oracle is
the existing pytest markers defined for exactly this purpose
(`[tool.pytest.ini_options].markers`): `disclosure` (access-policy, grant,
redaction, identity, no-outing guarantees) and `preservation` (fixity, bagging,
content-addressing guarantees). Reusing the markers keeps the kill-oracle
selection self-maintaining as tests are added or moved, instead of a
hand-enumerated file list that silently drifts.

mutmut is kept in its **own** optional-dependency group (`.[mutation]`), *not*
in `dev`, so `make verify` — and in particular `pip-audit` — keeps auditing
exactly the dependency surface it did before. mutmut's larger transitive tree
never enters the gated install path.

## Running it

```bash
make mutation           # installs .[mutation] on demand, runs mutmut, prints results
```

Or directly:

```bash
pip install -e ".[mutation]"
python -m mutmut run                       # generates + tests mutants (writes ./mutants/, gitignored)
python -m mutmut results                    # list survivors, timeouts, and uncovered mutants
python -m mutmut show <id>                  # show the exact diff of one surviving mutant
```

The `mutants/` working copy and `.mutmut-cache`/`mutmut-*.json` stats are
regenerated every run and are gitignored — never commit them.

CI runs the same thing weekly and on demand via
`.github/workflows/mutation.yml` (`workflow_dispatch` + a Monday cron), with
`continue-on-error: true` on top of `make mutation`'s own advisory (never
non-zero) exit, so a score dip surfaces in the run summary without ever being
able to turn a required check red.

## Baseline

Recorded 2026-07-07 (Python 3.12, mutmut 3.6.0), after the one test file this
pass added (`tests/test_grants_load.py`, see below) to close the two gaps
mutmut's *first* run found with genuinely zero coverage.

| Module | Total mutants | Killed | Survived | Not covered | Timeout | Score (killed / total) |
| --- | --- | --- | --- | --- | --- | --- |
| `access/policy.py` | 154 | 123 | 31 | 0 | 0 | 79.9% |
| `access/grants.py` | 80 | 73 | 7 | 0 | 0 | 91.3% |
| `access/redaction.py` | 42 | 38 | 4 | 0 | 0 | 90.5% |
| `identity.py` | 193 | 118 | 54 | 21 | 0 | 61.1% |
| `fixity.py` | 62 | 54 | 4 | 0 | 4 | 87.1% |
| **Total** | **531** | **406** | **100** | **21** | **4** | **76.5%** |

"Not covered" (`identity.py`, 21) means no test exercises that line at all —
mutmut can't even ask whether a fault there would be caught. All 21 are in
`IdentityVault.rekey`'s and `.persist`'s less-common branches (partial-failure
and re-encryption edge paths); closing them is real follow-up work, tracked
below, not attempted in this pass to keep it scoped to CQ-47's literal ask
(add the mutation-testing capability + an honest baseline) rather than
open-endedly hardening five files in one PR.

### A real gap this run found and closed

`load_grants` — the function that reads a subject → grant mapping from an
on-disk JSON file — had **zero** existing tests anywhere in the suite before
this pass, despite being the exact kind of surface CQ-47 exists to catch: a
quietly wrong or quietly ignored grant is a privilege-escalation or
under-provisioning bug with nothing to crash and nothing to notice it. The
first mutmut run surfaced this as `access/grants.py` sitting at 55% (44/80
killed, 9 outright "not covered", 27 survived). One representative survivor,
verified with `mutmut show`:

```diff
--- a/src/ledger/access/grants.py
+++ b/src/ledger/access/grants.py
@@
-        levels = tuple(AccessPolicy(level) for level in spec.get("levels", ("public",)))
+        levels = tuple(AccessPolicy(level) for level in spec.get("XXlevelsXX", ("public",)))
```

Mutating the dict key `"levels"` to a key that can never be present in real
data is semantically identical, at runtime, to a typo in a hand-edited grants
file: `spec.get(...)` silently falls through to the `("public",)` default with
no error, no log line, and — until this pass — no test anywhere that would
notice a subject's configured access levels were silently discarded.

`tests/test_grants_load.py` (new, six tests, marked `disclosure`) closes this:
missing-file → `{}` (deny-by-default), a full spec round-trips every field
(`levels`, `is_steward`, `identity_unseal`, `expires_at`), omitted fields
default to the narrowest possible grant, one subject's elevated grant never
leaks onto a neighboring entry in the same file, and `designated_successor`
(also previously untested) gets steward-level access with no implicit
identity-unseal token. Re-running mutmut scoped to `grants.py` alone after
this change: 73/80 killed (was 44/80), 0 "not covered" (was 9), 7 survived
(was 27) — the numbers already folded into the baseline table above.

### Remaining survivors (not chased in this pass)

The residual 100 survivors are, by module, mostly either message-text-only
mutants (e.g. an exception's string argument changed or deleted, with no test
asserting the exact wording — legitimate to leave, since ledger's tests assert
exception *type*, not message text, by design) or boundary/arithmetic
variants in `identity.py`'s encryption/rekey plumbing and `policy.py`'s
embargo-countdown formatting that were not run down individually here.
Tightening these — and closing `identity.py`'s 21 uncovered `rekey`/`persist`
branches — is real, valuable follow-up work; recorded here rather than
attempted in the same pass so the CQ-47 scope (capability + honest baseline)
stays legible instead of open-ended.
