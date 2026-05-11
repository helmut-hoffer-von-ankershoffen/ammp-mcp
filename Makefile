# Makefile — ammp-mcp local-dev shortcuts (mirrors CI verbatim).
#
# Every target wraps a `uv run …` invocation, never plain pip / python.
# CI runs the same commands — if `make lint && make test` passes locally,
# CI will too. `make help` lists every target with its one-line purpose.
#
# Conventions copied from `aignostics/python-sdk`'s Makefile (the
# packaging-and-lint reference for this project): help auto-generated
# from `## ` doc comments, every target `.PHONY`, a catch-all `%` rule
# so positional arguments don't trip up GNU make.

PYTHON_VERSION ?= 3.13

.DEFAULT_GOAL := help

.PHONY: help all install clean lint lint_fix pre_commit_run_all \
        test test_unit test_integration test_e2e test_coverage_reset \
        dist dist_smoke_test audit audit_vulnerabilities audit_licenses audit_sbom \
        docs_walk \
        serve status capability cli_reference attributions

# ─── Help (default) ──────────────────────────────────────────────────────

help: ## Show this help.
	@echo "🍝 ammp-mcp — local-dev targets (Python $(PYTHON_VERSION))"
	@echo ""
	@awk 'BEGIN {FS = ":.*?## "} \
		/^[a-zA-Z0-9_-]+:.*?## / { printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2 } \
		/^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) }' $(MAKEFILE_LIST)
	@echo ""
	@echo "Run \`make all\` after cloning."

##@ Setup

install: ## Install dev deps from uv.lock + pre-push hooks (one-shot for a fresh clone).
	uv sync --extra dev --frozen
	# `docstring-parser` and `docstring_parser_fork` both ship files under
	# the same `docstring_parser/` namespace. The fork adds DocstringYields
	# (used by pydoclint); the official 0.18 does not. Whichever installs
	# last wins on disk. uv's install order varies by Python version
	# (3.13/3.14 land the official last and break pydoclint). Recopy the
	# fork as the final step so its `common.py` always wins.
	uv sync --extra dev --frozen --reinstall-package docstring_parser_fork
	uv run pre-commit install --hook-type pre-push

clean: ## Reset the dev tree — remove caches, .venv, reports, dist.
	rm -rf .mypy_cache .ruff_cache .pytest_cache .venv reports dist
	rm -rf .coverage coverage.xml

##@ Lint / type-check / docstrings

lint: ## Run the full CI lint gate (ruff + format + mypy + docstring coverage + pydoclint).
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy src/ammp_mcp
	uv run python tools/check_docstrings.py
	uv run pydoclint src/ammp_mcp

lint_fix: ## Auto-fix ruff lint + format issues (no other checks).
	uv run ruff check --fix .
	uv run ruff format .

pre_commit_run_all: ## Run every pre-commit hook on every file (not just staged).
	uv run pre-commit run --all-files

##@ Tests

test: ## CI's default — unit + integration with coverage report.
	uv run pytest -m "unit or integration" \
		--cov=ammp_mcp \
		--cov-report=term-missing \
		--cov-report=xml:reports/coverage.xml \
		--junitxml=reports/junit.xml

test_unit: ## Unit tests only (offline, deps mocked).
	uv run pytest -m unit

test_integration: ## Integration tests only (in-memory FastMCP + Typer CliRunner).
	uv run pytest -m integration

test_e2e: ## End-to-end tests (skip without AMMP_ANTHROPIC_API_KEY).
	uv run pytest -m e2e

test_coverage_reset: ## Wipe coverage data (use before re-running for a clean baseline).
	rm -rf .coverage reports/coverage*

##@ Build / dist

dist: ## Build sdist + wheel via uv.
	uv build

dist_smoke_test: dist ## Build, then install the wheel into a throwaway venv and smoke-test it.
	uv venv /tmp/ammp-mcp-wheel-smoke
	uv pip install --python /tmp/ammp-mcp-wheel-smoke/bin/python dist/*.whl
	/tmp/ammp-mcp-wheel-smoke/bin/python -c "import ammp_mcp; print('OK', ammp_mcp.__version__)"
	/tmp/ammp-mcp-wheel-smoke/bin/ammp --help > /dev/null

audit: audit_vulnerabilities audit_licenses audit_sbom  ## Run the full audit pipeline (matches audit.yml).

audit_vulnerabilities: ## pip-audit + fail-on-any-vulnerability (writes reports/vulnerabilities.json).
	mkdir -p reports
	uv run --with pip-audit -- pip-audit \
		--skip-editable \
		--format json \
		--output reports/vulnerabilities.json
	uv run --with pip-audit -- pip-audit --skip-editable --format columns || true
	uv run python tools/audit_vulnerabilities.py

audit_licenses: ## pip-licenses + license allow-list enforcement (writes reports/licenses.{json,csv}).
	mkdir -p reports
	uv run --with pip-licenses -- pip-licenses \
		--format=json \
		--with-urls \
		--with-license-file \
		--output-file=reports/licenses.json
	uv run --with pip-licenses -- pip-licenses \
		--format=csv \
		--output-file=reports/licenses.csv
	uv run python tools/audit_licenses.py

audit_sbom: ## Generate a CycloneDX SBOM at reports/sbom.json.
	mkdir -p reports
	uv run --with cyclonedx-bom -- cyclonedx-py environment \
		--output-format json \
		--output-file reports/sbom.json

##@ Documentation

docs_walk: ## Run the Haiku-driven docs walker (needs AMMP_ANTHROPIC_API_KEY).
	uv run pytest -m e2e tests/e2e/test_docs_walk.py -v --no-cov

cli_reference: ## Regenerate docs/CLI_REFERENCE.md from the live Typer surface.
	uv run python tools/generate_cli_reference.py

attributions: ## Regenerate ATTRIBUTIONS.md from the resolved dep tree.
	uv run python tools/generate_attributions.py

##@ Server convenience

serve: ## Boot the HTTP MCP server (auto-bootstraps ~/.ammp/ on first run).
	uv run ammp serve

status: ## Show the installation status as the CLI sees it.
	uv run ammp status

capability: ## Render the capability advertisement (offline).
	uv run ammp capability

##@ Composite

all: install lint test  ## Install + lint + test — what CI does, end to end.

# Catch-all so passing positional args to `make <target> arg1 arg2` no
# longer trips "no rule to make target" errors. Mirrors the python-sdk
# convention; the actual targets handle their own argument lookup via
# $(MAKECMDGOALS) when they need to.
.PHONY: %
%:
	@:
