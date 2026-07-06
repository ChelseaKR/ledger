# Makefile — one command reproduces every gate (producibility, repeatability).
# `make verify` is the full merge gate; CI runs exactly these targets, so green
# locally means green in CI (reproducibility, process capabilities).

VENV ?= .venv
PY   := $(VENV)/bin/python
PIP  := $(PY) -m pip

.DEFAULT_GOAL := help
.PHONY: help venv install lint format type test cov audit accessibility acr demo serve \
        i18n i18n-compile secret-scan container verify clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

venv: ## Create the virtual environment
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip

install: venv ## Install ledger plus dev tooling
	$(PIP) install -e ".[dev]"

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

cov: ## Run tests with coverage
	$(PY) -m pytest --cov --cov-report=term-missing

backup-test: ## Exercise the full back-up -> wipe -> restore disaster-recovery cycle
	$(PY) -m pytest -m recovery

audit: ## Dependency vulnerability scan (blocking)
	# SECURITY-AND-SUPPLY-CHAIN-STANDARD §4 forbids muting this gate by name; a
	# finding must be fixed or explicitly triaged/waived, never `|| true`d away.
	$(PY) -m pip_audit

accessibility: ## Run the accessibility checks over the built web surface
	$(PY) -m ledger.accessibility_check web

acr: ## Regenerate the Accessibility Conformance Report (VPAT 2.5)
	$(PY) -m ledger.acr_gen > docs/accessibility/ACR.md
	@echo "ACR regenerated at docs/accessibility/ACR.md"

demo: ## Scripted end-to-end: ingest -> seal -> grant -> verified-replica -> no-outing proof
	$(PY) -m ledger.demo

serve: ## Run the accessible archive browse server locally
	$(PY) -m ledger.cli serve --root ./local-archive

i18n: ## i18n gettext catalog gate: POT current + EN/ES parity + PO compiles + BCP-47
	# G2-lite — regenerate the extraction template and fail if it drifts from the
	# committed one (a new/changed user-facing string without a re-extract is a
	# merge-blocker). The normalizer freezes volatile header/flag noise so this is a
	# meaningful diff, not a flaky timestamp check. Local == CI.
	$(PY) -m babel.messages.frontend extract -F babel.cfg --no-location \
		--sort-output --project=ledger-archive --version=0.1.0 \
		-o src/ledger/locales/messages.pot src/
	$(PY) tools/i18n_normalize_pot.py src/ledger/locales/messages.pot
	git diff --exit-code -- src/ledger/locales/messages.pot
	# G7 — every PO compiles cleanly (format + domain checks), no msgfmt errors.
	msgfmt --check --check-format --check-domain -o /dev/null \
		src/ledger/locales/en/LC_MESSAGES/messages.po
	msgfmt --check --check-format --check-domain -o /dev/null \
		src/ledger/locales/es/LC_MESSAGES/messages.po
	# G6 EN/ES key-parity + G5 completeness/placeholder parity.
	$(PY) tools/check_catalog_parity.py
	# G3 — BCP 47 / RFC 5646 validity of every authored locale tag.
	$(PY) tools/check_bcp47.py
	@echo "i18n: POT current; EN/ES key-parity + completeness; PO compiles; BCP-47 valid."

i18n-compile: ## Compile the committed PO catalogs to MO (run after editing a .po)
	msgfmt -o src/ledger/locales/en/LC_MESSAGES/messages.mo \
		src/ledger/locales/en/LC_MESSAGES/messages.po
	msgfmt -o src/ledger/locales/es/LC_MESSAGES/messages.mo \
		src/ledger/locales/es/LC_MESSAGES/messages.po
	@echo "i18n-compile: refreshed messages.mo for en, es."

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

container: ## Build the self-host image and scan it for CRITICAL/HIGH CVEs (Trivy)
	# Not part of `verify`: it needs a working Docker daemon, which not every
	# contributor's environment has, and a Dockerfile-only change is rare enough
	# that gating every `make verify` run on a container build/scan is the wrong
	# trade-off. CI's `container` job (ci.yml) is the gate of record and runs
	# unconditionally on every push/PR — this target just mirrors it locally.
	docker build -f infra/Dockerfile -t ledger:local-scan .
	trivy image --severity CRITICAL,HIGH --ignore-unfixed --exit-code 1 ledger:local-scan

# The full gate. Determinism + reproducibility: same inputs, same result, every run.
# Matches CI's required-check set byte-for-byte (CICD-27): the `gate`, `i18n`,
# `accessibility`, and `supply-chain` jobs in ci.yml run exactly these targets, so
# green here means green in CI. (The `no-outing-audit` job is `test`'s own
# `disclosure`-marked subset, run standalone in CI for visibility, not a distinct
# local gate; `container` is intentionally excluded — see its own target comment.)
verify: lint type test i18n accessibility audit secret-scan ## Run the complete merge gate (== CI's required checks)
	@echo "verify: all gates green"

clean: ## Remove caches and build artifacts (never touches an archive's data)
	rm -rf build dist .pytest_cache .mypy_cache .ruff_cache *.egg-info src/*.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
