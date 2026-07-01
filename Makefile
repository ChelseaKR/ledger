# Makefile — one command reproduces every gate (producibility, repeatability).
# `make verify` is the full merge gate; CI runs exactly these targets, so green
# locally means green in CI (reproducibility, process capabilities).

VENV ?= .venv
PY   := $(VENV)/bin/python
PIP  := $(PY) -m pip

.DEFAULT_GOAL := help
.PHONY: help venv install lint format type test cov audit accessibility acr demo serve \
        i18n i18n-compile verify clean

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

audit: ## Dependency vulnerability scan
	$(PY) -m pip_audit || true

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

# The full gate. Determinism + reproducibility: same inputs, same result, every run.
verify: lint type test i18n ## Run the complete merge gate (lint + type + test + i18n)
	@echo "verify: all gates green"

clean: ## Remove caches and build artifacts (never touches an archive's data)
	rm -rf build dist .pytest_cache .mypy_cache .ruff_cache *.egg-info src/*.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
