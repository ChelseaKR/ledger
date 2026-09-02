# Makefile — one command reproduces every gate (producibility, repeatability).
# `make verify` is the full merge gate; CI runs exactly these targets, so green
# locally means green in CI (reproducibility, process capabilities).

VENV ?= .venv
# Prefer the project venv (created by `make install`'s `uv sync`, in CI too —
# uv always materializes $(VENV) rather than installing into system Python), but
# fall back to python3 so a target still resolves before `install` has run.
# `?=` also lets a caller override, e.g. `make i18n PY=python`. This closes the
# i18n gate's `.venv/bin/python: No such file or directory` (exit 127) failure.
PY   ?= $(if $(wildcard $(VENV)/bin/python),$(VENV)/bin/python,python3)

.DEFAULT_GOAL := help
.PHONY: help venv install lock lint format type test cov audit osv semgrep accessibility acr demo serve \
        i18n i18n-extract i18n-compile claims secret-scan workflow-lint perf real-corpus real-corpus-evidence \
        acr-check container mutation verify clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

venv: ## Create the virtual environment
	uv venv $(VENV)

# SEC-13/CQ-09/CQ-27: install exactly the pinned, hash-locked graph in uv.lock
# (the runtime dependency plus the `dev` PEP 735 dependency group), never a fresh
# resolve against version ranges. `--locked` fails the build instead of silently
# re-resolving if pyproject.toml and uv.lock ever drift apart, so "it installed"
# means "it installed the audited, committed lockfile" (reproducibility).
install: ## Install ledger plus dev tooling from the locked dependency graph
	uv sync --locked --group dev

lock: ## Regenerate uv.lock after a pyproject.toml dependency change
	uv lock

lint: ## Static analysis (ruff): correctness, security, import hygiene
	$(PY) -m ruff check src tests
	$(PY) -m ruff format --check src tests

format: ## Auto-format
	$(PY) -m ruff format src tests
	$(PY) -m ruff check --fix src tests

type: ## Strict type checking (mypy)
	$(PY) -m mypy

test: ## Run the test suite (preservation + disclosure + no-outing audit)
	$(PY) -m pytest

cov: ## Run tests with coverage (95% floor on the access/consent/dual-control core; 90% on moderate.py)
	$(PY) -m pytest --cov --cov-report=term-missing
	# Per-module floor (CODE-QUALITY-STANDARD, security/crypto-critical paths): the
	# access-policy, consent, and dual-control modules must hold >=95% branch
	# coverage, above the 85% baseline. Scoped re-report over the .coverage data.
	#
	# NOTE ON THE POOL: `coverage report --fail-under` gates the TOTAL row, not each
	# module, so this line passes at 95% overall while `grants.py` (92%) and
	# `consent.py` (91%) sit below it. That is a known weakness of the pooled figure,
	# not a claim that every module in the list clears 95.
	$(PY) -m coverage report --include="src/ledger/access/*,src/ledger/consent.py,src/ledger/dualcontrol.py" --fail-under=95
	# `moderate.py` gets its OWN scoped report and its own floor rather than joining
	# the list above. Adding it there would have let its coverage average against
	# `policy.py`/`dualcontrol.py` at 100% — a new module reading as covered because
	# its neighbours are, which is the pooling weakness the note above describes,
	# repeated deliberately. 90% is where the module measures with the accountable
	# moderation log's tests in place (the remainder is pre-existing validation and
	# refusal branches); it is a ratchet, so raise it when the number rises.
	$(PY) -m coverage report --include="src/ledger/moderate.py" --fail-under=90

backup-test: ## Exercise the full back-up -> wipe -> restore disaster-recovery cycle
	$(PY) -m pytest -m recovery

audit: ## Dependency vulnerability scan (blocking)
	# SECURITY-AND-SUPPLY-CHAIN-STANDARD §4 forbids muting this gate by name; a
	# finding must be fixed or explicitly triaged/waived, never `|| true`d away.
	$(PY) -m pip_audit

osv: ## OSV-Scanner over uv.lock — mirrors ci.yml's osv job locally
	# CI-authoritative (same shape as secret-scan): CI runs the pinned
	# OSV-Scanner container over the committed uv.lock (.github/workflows/ci.yml,
	# `osv` job) regardless of what is on this machine, so CI is the gate of
	# record even when the Go binary is absent locally. This target just lets a
	# contributor scan the lockfile before pushing when osv-scanner happens to be
	# installed (https://google.github.io/osv-scanner/installation/).
	@command -v osv-scanner >/dev/null 2>&1 || { \
		echo "osv-scanner not found locally; skipping (CI is authoritative — see ci.yml osv job)"; \
		exit 0; \
	}
	osv-scanner --lockfile=./uv.lock

accessibility: ## Run the accessibility checks over the built web surface
	$(PY) -m ledger.accessibility_check web

acr: ## Regenerate the Accessibility Conformance Report (VPAT 2.5)
	$(PY) -m ledger.acr_gen > docs/accessibility/ACR.md
	@echo "ACR regenerated at docs/accessibility/ACR.md"

acr-check: ## The committed ACR is byte-identical to what ledger.acr_gen renders today
	# The ACR is a committed artifact standing in for a computation, and until this
	# target existed nothing re-ran that computation: `acr` writes the file and is
	# not part of `verify`, and no test read it. A conformance level edited in
	# `src/ledger/acr_gen.py` could ship while `docs/accessibility/ACR.md` still said
	# the opposite, and the document is what a procurement reviewer reads.
	#
	# `--check` renders into memory and diffs. It deliberately does NOT regenerate
	# into the working tree: a gate that rewrites the artifact it is checking heals
	# drift locally on every run while the committed bytes stay stale.
	$(PY) -m ledger.acr_gen --check docs/accessibility/ACR.md

demo: ## Scripted end-to-end: ingest -> seal -> grant -> verified-replica -> no-outing proof
	$(PY) -m ledger.demo

serve: ## Run the accessible archive browse server locally
	$(PY) -m ledger.cli serve --root ./local-archive

i18n: ## i18n gettext catalog gate: POT current + en/es/fr/ar parity + PO compiles + UTF-8 + BCP-47 + CLDR pin
	# G2-lite — re-extract the template into a TEMPORARY directory and fail if it
	# drifts from the committed one (a new/changed user-facing string without a
	# re-extract is a merge-blocker). The normalizer freezes volatile header/flag
	# noise so this is a meaningful diff, not a flaky timestamp check. Local == CI.
	#
	# The extraction used to write straight over `src/ledger/locales/messages.pot`
	# and then `git diff --exit-code` the working tree. That compares, so it caught
	# drift — but it also means the merge gate REPAIRS the artifact it is judging.
	# Every local `make verify` silently rewrote the committed template, so drift
	# healed on the contributor's disk whether or not they noticed the red line, and
	# a gate that edits its own subject is one `git add -A` away from committing a
	# regeneration nobody reviewed. `git diff` is blind in a second way that matters
	# for any future artifact added here: it cannot see an UNTRACKED file, so the
	# same shape applied to a new catalog would exit 0 on a file that was never
	# committed at all. Extract to a temp dir, diff, write nothing. `make
	# i18n-extract` is the authoring path that writes the template on purpose.
	@tmp="$$(mktemp -d)"; \
	trap 'rm -rf "$$tmp"' EXIT INT TERM; \
	$(PY) -m babel.messages.frontend extract -F babel.cfg --no-location \
		--sort-output --project=ledger-archive --version=0.1.0 \
		-o "$$tmp/messages.pot" src/ || exit 1; \
	$(PY) tools/i18n_normalize_pot.py "$$tmp/messages.pot" || exit 1; \
	if ! diff -u src/ledger/locales/messages.pot "$$tmp/messages.pot"; then \
		echo "i18n G2-lite: FAIL — src/ledger/locales/messages.pot is stale." >&2; \
		echo "  Regenerate it with: make i18n-extract" >&2; \
		exit 1; \
	fi
	# G7 — every PO compiles cleanly (format + domain checks), no msgfmt errors.
	for lang in en es fr ar; do \
		msgfmt --check --check-format --check-domain -o /dev/null \
			src/ledger/locales/$$lang/LC_MESSAGES/messages.po || exit 1; \
	done
	# G1 — every catalog and every rendered page is valid UTF-8 (charset declared).
	$(PY) tools/check_i18n_utf8.py
	# G6 key-parity + G5 completeness/placeholder parity across en/es/fr/ar.
	$(PY) tools/check_catalog_parity.py
	# G3 — BCP 47 / RFC 5646 validity of every authored locale tag.
	$(PY) tools/check_bcp47.py
	# G12 — CLDR/locale-data freshness pin (Babel within the reviewed range, data loads).
	$(PY) tools/check_i18n_deps.py
	# G13 — every committed `messages.mo` carries exactly the messages its
	# `messages.po` declares, compared through the same gettext reader the running
	# program uses.
	#
	# The `.mo` files are committed (docs/I18N.md explains why) and `make
	# i18n-compile` is the only thing that writes them. It is not part of `verify`,
	# so until this check existed the compiled catalog that actually ships was a
	# committed artifact standing in for a computation nothing re-ran: edit a
	# `msgstr`, skip `make i18n-compile`, and every gate above stays green while the
	# running program serves the OLD translation. G5/G6 read the `.po` and never
	# open the `.mo`. docs/I18N.md claimed the render/server tests guarded this;
	# they assert a handful of specific strings ("Browse" -> "Explorar"), so they
	# guard those strings and nothing else.
	#
	# This compares MEANING, not bytes, and tools/check_mo_current.py records why:
	# msgfmt's MO hash-table layout changed between gettext releases, so the same
	# `.po` compiles to different bytes under 0.21 than under 0.23.1/1.0 while the
	# message maps stay identical. Byte equality would have pinned every contributor
	# and every runner to one gettext build. The excluded subset is named in that
	# file: the MO byte layout, and header fields other than Plural-Forms.
	$(PY) tools/check_mo_current.py
	@echo "i18n: POT current; MO compiled from the committed PO; en/es/fr/ar key-parity + completeness; PO compiles; UTF-8; BCP-47 valid; CLDR pinned."

i18n-extract: ## Regenerate src/ledger/locales/messages.pot (the authoring path that writes)
	# `make i18n`'s G2-lite step extracts into a temp dir and only compares, so this
	# is the one target allowed to write the committed template.
	$(PY) -m babel.messages.frontend extract -F babel.cfg --no-location \
		--sort-output --project=ledger-archive --version=0.1.0 \
		-o src/ledger/locales/messages.pot src/
	$(PY) tools/i18n_normalize_pot.py src/ledger/locales/messages.pot
	@echo "i18n-extract: rewrote src/ledger/locales/messages.pot; review and commit it."

i18n-compile: ## Compile the committed PO catalogs to MO (run after editing a .po)
	for lang in en es fr ar; do \
		msgfmt -o src/ledger/locales/$$lang/LC_MESSAGES/messages.mo \
			src/ledger/locales/$$lang/LC_MESSAGES/messages.po || exit 1; \
	done
	@echo "i18n-compile: refreshed messages.mo for en, es, fr, ar."

claims: ## Truthfulness gate: verify README/doc factual claims against the repo
	$(PY) tools/check_claims.py

hygiene: ## Suppression hygiene (CQ-34/35): every noqa/type-ignore is coded, explained, and — for complexity debt — issue-linked
	$(PY) tools/check_hygiene.py

semgrep: ## Semgrep SAST (p/ci) — mirrors semgrep.yml locally (SEC-11/13, CICD-13/27)
	# CI-authoritative, in the same shape as `osv` and `secret-scan`: the
	# `semgrep` workflow installs the pinned semgrep and scans on every push and PR
	# regardless of what is on this machine, and `Semgrep SAST (p/ci)` is a required
	# check, so CI is the gate of record. This target closes the gap
	# `docs/ROADMAP.md` recorded under SEC-11/13 + CICD-13/27: a contributor had no
	# pre-push signal for the one required check `make verify` could not run.
	#
	# Semgrep is deliberately NOT in the locked dependency graph, which is the one
	# place this differs from what `docs/ROADMAP.md` originally proposed. Locking
	# `semgrep==1.145.0` was tried and reverted: it pins `click 8.1.8` and
	# `mcp 1.16.0`, and OSV-Scanner reports 4 High-severity advisories across those
	# two (PYSEC-2026-2132; PYSEC-2026-1617 / -3482 / -3483), none of which can be
	# bumped independently because semgrep pins them. Importing four known-vulnerable
	# packages to gain a local mirror of a check CI already runs is a bad trade, and
	# SECURITY-AND-SUPPLY-CHAIN-STANDARD §4 forbids muting the audit gate instead.
	# So semgrep is treated exactly as `gitleaks` and `osv-scanner` are: an external
	# tool this target uses when present, never a dependency of this package.
	# Install it however you like, e.g. `pipx install semgrep==1.145.0`.
	#
	# `--config p/ci` fetches the ruleset from semgrep.dev, so this target needs
	# network — a property of the dev tool, not of ledger, whose runtime stays
	# offline (README hard rules).
	@command -v semgrep >/dev/null 2>&1 || { \
		echo "semgrep not found locally; skipping (CI is authoritative — see semgrep.yml). Install it as an external tool, e.g. pipx install semgrep==1.145.0"; \
		exit 0; \
	}
	semgrep scan --config p/ci --error src tests

secret-scan: ## Secret scan (gitleaks) — mirrors ci.yml's supply-chain job locally
	# CI-authoritative: CI pins and downloads gitleaks 8.30.1 itself
	# (.github/workflows/ci.yml, supply-chain job) regardless of what is on this
	# machine, so CI is the gate of record even if a local binary is missing or a
	# different version. This target just lets a contributor catch a leak before
	# pushing when gitleaks happens to be installed locally.
	@command -v gitleaks >/dev/null 2>&1 || { \
		echo "gitleaks not found locally; skipping (CI is authoritative — see ci.yml supply-chain job)"; \
		exit 0; \
	}
	gitleaks detect --source . --config .gitleaks.toml --no-banner --redact --exit-code 1

workflow-lint: ## Static analysis of the workflow YAML itself (zizmor) — mirrors ci.yml's workflow-lint job
	# zizmor ships a compiled binary via its pip wheel, not a `python -m`-runnable
	# module, so it's invoked directly off $(VENV)/bin, falling back to PATH.
	@if [ -x "$(VENV)/bin/zizmor" ]; then "$(VENV)/bin/zizmor" .github/workflows; \
	elif command -v zizmor >/dev/null 2>&1; then zizmor .github/workflows; \
	else echo "zizmor not found; run 'make install' (or 'pip install zizmor')"; exit 1; fi

perf: ## Performance budgets (QM-02): CAS, fixity, ingest, browse must clear their time budgets
	# Not part of `verify`: timing is meaningful relative to the machine it runs
	# on, and a contributor's laptop under load is not that machine. CI's `perf`
	# job (ci.yml) — a fixed, roughly consistent runner — is the gate of record;
	# this target just lets a contributor run the same budgets locally out of
	# curiosity or to reproduce a CI failure.
	$(PY) tools/perf_budget.py

real-corpus: ## Ingest a real, openly-licensed archival corpus (OPF format-corpus) and report what broke
	# Not part of `verify`: it downloads ~302 MB from the network, which a merge
	# gate must never depend on. Every other proof in this repo runs on fixtures
	# ledger wrote itself — a closed loop that can only confirm its own
	# assumptions. This target opens it against the digital-preservation
	# community's own corpus of awkward real files (CC0), pinned to one commit and
	# verified file-by-file against its git blob SHA-1 so a run cannot silently
	# measure something other than the corpus it names. The corpus lands in the
	# gitignored ./real-corpus and is never committed. What the run MEASURED is
	# committed, as metadata and hashes under docs/data/real-corpus/, and this
	# target fails if a fresh run drifts from it; tests/test_real_corpus_evidence.py
	# (in `make verify`, no network) re-derives every number the write-up states
	# from that file. Findings are written up in docs/REAL-CORPUS-REPORT.md.
	$(PY) tools/real_corpus.py

real-corpus-evidence: ## Re-run the real corpus and REWRITE the committed evidence (then update every doc that cites it)
	$(PY) tools/real_corpus.py --write-evidence

container: ## Build the self-host image and scan it for CRITICAL/HIGH CVEs (Trivy)
	# Not part of `verify`: it needs a working Docker daemon, which not every
	# contributor's environment has, and a Dockerfile-only change is rare enough
	# that gating every `make verify` run on a container build/scan is the wrong
	# trade-off. CI's `container` job (ci.yml) is the gate of record and runs
	# unconditionally on every push/PR — this target just mirrors it locally.
	docker build -f infra/Dockerfile -t ledger:local-scan .
	trivy image --severity CRITICAL,HIGH --ignore-unfixed --exit-code 1 ledger:local-scan

mutation: ## ADVISORY (never a merge gate): mutation-test the safety-critical core (CQ-47)
	@echo "mutation: ADVISORY ONLY — this is NOT part of 'make verify' and never gates a PR."
	@echo "          Scoped to access/, identity.py, fixity.py — see docs/MUTATION-TESTING.md."
	@# Install the isolated mutation extra on demand while honoring the lockfile.
	@# Scope (which files get mutated) and kill oracle (which tests run) live in
	@# [tool.mutmut] in pyproject.toml.
	@$(PY) -c "import mutmut" 2>/dev/null || uv sync --locked --group dev --extra mutation
	@# `-` prefixes keep this target advisory: a surviving mutant never fails the build.
	-$(PY) -m mutmut run
	-$(PY) -m mutmut results
	@echo "mutation: advisory run complete. Review any survivors above against the"
	@echo "          documented baseline in docs/MUTATION-TESTING.md (equivalent mutants noted)."

# The full local gate. Determinism + reproducibility: same inputs, same result, every
# run. It is the PORTABLE SUBSET of CI's required-check set, not the whole of it
# (CICD-27), and the difference is written out here rather than implied, because a
# contributor who reads "parity" reads green locally as green in CI and stops looking.
#
# Eight of the thirteen contexts `.github/rulesets/main.json` requires are reproduced:
#
#   lint · type · test (py3.12)        <- lint, type, test, claims, hygiene
#   dependency & secret scan           <- audit, secret-scan
#   no-outing audit (safety gate)      <- test's own `disclosure`-marked subset, which
#                                         CI also runs standalone for visibility
#   accessibility gate (WCAG 2.2 AA)   <- accessibility, acr-check
#   i18n (gettext catalog gate)        <- i18n
#   OSV lockfile scan (uv.lock)        <- osv
#   Semgrep SAST (p/ci)                <- semgrep
#   workflow linter (zizmor)           <- workflow-lint
#
# Five have no local target at all, and a green `verify` says nothing about them:
#
#   CodeQL analyze (python)            no local CodeQL database is built
#   CodeQL analyze (actions)           the same
#   container image CVE scan (Trivy)   `container` is excluded on purpose — see its own
#                                      target comment
#   performance budgets (QM-02)        `perf` is excluded on purpose — a contributor's
#                                      laptop is not a stable timing surface
#   accessibility (browser axe over the served site)
#                                      Playwright + Chromium are CI-only dev deps;
#                                      `accessibility` is the static half of that pair
#
# `osv`, `secret-scan` and `semgrep` no-op with a message when their binary is absent,
# so the three they mirror are only as strong locally as the tooling actually installed.
# Semgrep is deliberately not in the locked graph — pinning it pulls four
# known-vulnerable transitive packages, see the `semgrep` target — so CI's required
# `Semgrep SAST (p/ci)` context remains the gate of record for it.
# `tools/check_claims.py` fails the build if a context in the mirror is not named above.
verify: lint type test i18n accessibility acr-check audit osv semgrep secret-scan claims hygiene workflow-lint ## Run the portable subset of CI's required checks (8 of 13 contexts)
	@echo "verify: all gates green (the portable subset — see the comment above this target)"

clean: ## Remove caches and build artifacts (never touches an archive's data)
	rm -rf build dist .pytest_cache .mypy_cache .ruff_cache *.egg-info src/*.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
