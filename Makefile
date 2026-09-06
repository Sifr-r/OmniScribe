.DEFAULT_GOAL := help

.PHONY: help setup run build-client bundle bundle-smoke test test-slow check lint typecheck audit security clean doctor openapi

help: ## Show available developer commands
	@uv run python -c "print('Available targets:\n  help           Show available developer commands\n  setup          Install project, web, and preprocessing dependencies\n  build-client   Build Flutter web client static assets\n  bundle         Build the PyInstaller server bundle (Windows; macOS/Linux untested)\n  bundle-smoke   Build the bundle and run a /api/health smoke test\n  run            Start the web server on port 8000\n  test           Run the fast test suite\n  test-slow      Run the slow test suite (Surya, full fixtures) -- pulls model weights on first run\n  check          Run the full fast gate (lint + typecheck + fast tests with coverage) -- same as CI\n  lint           Run Ruff lint and format checks\n  typecheck      Run mypy against production code\n  audit          Run pip-audit dependency vulnerability scan\n  security       Run Semgrep static analysis (best-effort, no CI gating)\n  clean          Remove generated caches and build artifacts\n  doctor         Report Python, uv, Redis, and model server health\n  openapi        Regenerate tests/openapi.json from the FastAPI app spec')"

setup: ## Install project, web, and preprocessing dependencies
	uv sync --extra web --extra preprocessing

build-client: ## Build Flutter web client static assets
	cd client && flutter build web --release

run: ## Start the web server on port 8000
	uv run omniscribe-server --port 8000

test: ## Run the fast test suite
	uv run pytest -m "not slow"

# F5-27 audit fix: dedicated `test-slow` target so the slow tier
# (Surya model load + full-dataset fixtures) is one `make` invocation
# away. ``nightly.yml`` runs the same command on the CI side; local
# operators who want to debug a Surya regression don't have to
# remember the marker incantation.
test-slow: ## Run the slow test suite (Surya, full fixtures) -- pulls model weights on first run
	uv run pytest -m "slow" -v

# CI-equivalent fast gate. Same flags as ``.github/workflows/test.yml``
# (the ``fast`` job, lines 80-85). The ``--cov-fail-under=80`` matches
# ``[tool.coverage] fail_under`` in ``pyproject.toml``. This is the
# pre-PR command documented in ``CONTRIBUTING.md`` -- a one-shot that
# tells you what CI would tell you, locally.
check: ## Run the full fast gate (lint + typecheck + fast tests with coverage) -- same as CI
	$(MAKE) lint
	$(MAKE) typecheck
	uv run pytest -m "not slow and not slow_dataset" --cov=src/omniscribe --cov-fail-under=80 --cov-report=term-missing

lint: ## Run Ruff lint and format checks
	# D18 (audit 5.4): ``--no-fix`` is intentional here. ``make lint`` is
	# the CI-equivalent check target — auto-applying fixes from a lint
	# invocation would surprise developers whose source tree mutates
	# under their feet. The pre-commit hook
	# (``.pre-commit-config.yaml:22``) runs ``ruff --fix`` on commit
	# and is the auto-fix path; ``make lint`` is the read-only gate.
	uv run ruff check src tests --no-fix
	uv run ruff format src tests --check

typecheck: ## Run mypy against production code
	uv run mypy src

# PYSEC-2026-311 / CVE-2026-45829 (chromadb server RCE) was previously
# risk-accepted here with --ignore-vuln; chromadb left the dependency tree
# in the lexicon migration Phase 5, so the ignore flag is gone (2026-09-06).
# If pip-audit flags chromadb in your local env, it is a stale local install,
# not a declared dependency.
audit: ## Run pip-audit dependency vulnerability scan
	uv run pip-audit

# F5-27 audit fix: `make security` runs the local Semgrep static
# analysis pass on the same ruleset `security.yml` uses in CI. The
# CI path uploads the SARIF; this target prints findings to the
# terminal so a developer can iterate without pushing. Semgrep is
# invoked via ``uvx`` so the local venv doesn't need it as a hard
# dep — matches the pattern ``install.ps1`` uses for ``pip-audit``.
# ``--error`` makes the exit code non-zero on any finding, so this
# target slots into a pre-push hook if anyone wants to wire one.
# The target name is intentionally distinct from `audit` (which is
# dependency scanning); `security` is source-code analysis.
security: ## Run Semgrep static analysis (best-effort, no CI gating)
	@command -v uvx >/dev/null 2>&1 || { echo "uvx not found; install via 'uv tool install uvx' or 'pipx install uvx'." >&2; exit 1; }
	uvx --from semgrep semgrep scan --config=p/owasp-top-ten --config=p/python --config=p/security-audit src/ --error

clean: ## Remove generated caches and build artifacts
	uv run python scripts/dev.py clean

doctor: ## Report Python, uv, Redis, and model server health
	uv run python scripts/dev.py doctor

# Regenerate the checked-in OpenAPI snapshot the frontend contract tests
# diff against. ``tests/routers/test_openapi_schema.py`` will fail if
# the snapshot drifts; running this target re-syncs the file to whatever
# ``app.openapi()`` currently returns. The redirect uses ``>`` (not ``>>``)
# so stale content is fully replaced on each run.
openapi: ## Regenerate tests/openapi.json from the FastAPI app spec
	uv run python -c "from omniscribe.server import app; import json; print(json.dumps(app.openapi(), indent=2))" > tests/openapi.json

# Phase 4.4 (RFC 001 Option A): onefile PyInstaller bundle of the
# FastAPI server. Cold-cache build is ~5-10 minutes on Windows; warm
# cache ~2-3 minutes. Output: ``dist/omniscribe-server/omniscribe-server.exe``
# (Windows) or ``dist/omniscribe-server/omniscribe-server`` (macOS / Linux).
# Codesigning is intentionally out of scope (no cert budget per RFC 001
# §Decision needed). macOS / Linux support is not exercised yet — see
# docs/deployment/windows-bundle.md.
bundle: ## Build the PyInstaller server bundle (Windows; macOS/Linux untested)
	uv run python scripts/build_windows.py

# Phase 4.4 smoke test: build + boot the binary + hit /api/health.
# This is the cheapest end-to-end verification possible without a
# real VLM endpoint. Add this to ``make check`` once a CI runner is
# available; for now it requires a developer machine.
bundle-smoke: ## Build the bundle and run a /api/health smoke test
	uv run python scripts/build_windows.py --smoke
