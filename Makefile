# Makefile — one command reproduces every gate (producibility, repeatability).
# `make verify` is the full merge gate; CI runs exactly these targets, so green
# locally means green in CI (reproducibility, process capabilities).

VENV ?= .venv
PY   := $(VENV)/bin/python
PIP  := $(PY) -m pip

.DEFAULT_GOAL := help
.PHONY: help venv install lint format type test cov audit accessibility acr demo serve verify clean

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

# The full gate. Determinism + reproducibility: same inputs, same result, every run.
verify: lint type test ## Run the complete merge gate (lint + type + test)
	@echo "verify: all gates green"

clean: ## Remove caches and build artifacts (never touches an archive's data)
	rm -rf build dist .pytest_cache .mypy_cache .ruff_cache *.egg-info src/*.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
