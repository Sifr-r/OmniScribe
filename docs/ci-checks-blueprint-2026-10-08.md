# Failed GitHub checks: repair blueprint

Scope: failures on main commit `f8c78050a7a185ad4af16459277da020dd8517c6`.
Preserve existing uncommitted changes and keep runtime architecture unchanged.

| Owner | File boundary | Responsibility and acceptance |
| --- | --- | --- |
| Dependency automation agent | `.github/dependabot.yml` | Replace obsolete npm updates targeting deleted `/frontend` with pub updates for `/client`; verify every remaining ecosystem has a manifest. |
| Container agent | `Dockerfile`, `.trivyignore.yaml`, container scan configuration only if necessary | Identify actual Trivy findings from run 37290374288; fix affected dependencies or document a narrowly scoped accepted finding; preserve scan enforcement. |
| Test verification agent | `tests/plugins/test_pipeline_bridge.py`, existing pytest/CI files | Verify existing pyarrow collection guard addresses fast and nightly failures; narrow concrete OCR backend types in the regression test; run appropriate local test gates. |
| Orchestrator | `ARCHITECTURE.md`, this document | Record ownership, evidence, validation limits, and final changes. |

Additional observed cause: Trivy reports AnyIO 3.7.1 as CRITICAL, fixed by
4.14.2. The dependency agent also owns `pyproject.toml`, `uv.lock`, and existing
Windows packaging hooks if needed to support lazy imports. The orchestrator
owns removal of obsolete AnyIO exceptions from `test.yml` and `Makefile`.
The migration skill guides compatibility proof: preserve runtime API and
bundle imports; resolve the lock without modifying the test environment while
the baseline suite runs. Rollback restores the old constraint, lock and hook
changes together from the reviewed diff; it does not change application data.
No Git rollback commands are authorized.

This document owns repair scope and evidence. `.trivyignore.yaml` is the other
planned new file and owns the package-scoped container scanner exception for
the already accepted LangGraph SDK advisory, including its reachability proof.
No new production modules or cascading files are required.
Subagents must validate boundaries, use contextual deterministic errors, maintain
strict typing and idempotent mutations wherever their changes introduce behavior.
Git operations are limited to log, status and diff; do not push changes.

## Observed causes and repairs

- Tests run 37290374288 and nightly run 37757996319 abort collection because
  `tests/core/test_lexicon_schema.py` imports absent optional PyArrow. Preserve
  and verify the existing `pytest.importorskip` guard before schema imports.
- Dependabot run 37584026155 cannot find `/frontend/package.json`. Configure
  `pub` updates at `/client`, whose pubspec and lockfile exist.
- Trivy finds patched Debian security packages missing from the pinned August
  base. Both stages upgrade OpenSSL, libssl3t64, openssl-provider-legacy, PCRE2,
  perl-base, SQLite and gzip without adding/removing other packages.
- AnyIO 3.7.1 is vulnerable. Resolve `>=4.14.2,<5` to 4.15.1. Keep the existing
  full submodule collection plus explicit `anyio.abc` bundle import; remove
  obsolete AnyIO audit exceptions. Preserve the unrelated SDK exception.
- CI's exact `mypy src tests` command also finds an un-narrowed backend in the
  existing OCR regression test. Assert the two concrete engine types.

## Validation evidence

- Ruff check/format: 446 files clean; exact CI typing: 446 files clean after
  narrowing. Focused OCR bridge tests: 22 passed.
- PyArrow is absent in the local test environment: the schema module skips
  cleanly. Nightly slow collection selects 6 tests with no collection errors.
- Dependabot and both workflows parse as YAML; every configured ecosystem has
  its matching project manifest. `uv lock --check` passes.
- Patched AnyIO 4.14.2 and locked 4.15.1 work in isolated frozen PyInstaller
  probes. Locked 4.15.1 also serves a synchronous FastAPI endpoint through
  TestClient (HTTP 200), and the frozen probe exercises asyncio, memory streams
  and thread offload. No new production hook is required.
- Docker static checks pass; targeted upgrades on the exact pinned base resolve
  all seven OS package fixes without package additions/removals.
- Baseline full fast suite on Windows Python 3.13.9: 2,779 passed, 30 skipped,
  13 deselected, coverage 80.28%, exit 0 in 413.22 seconds.
- After installing exactly locked AnyIO 4.15.1, all static gates pass (446
  typed source/test files), and pip-audit reports no known vulnerabilities with
  only the existing SDK exception. Nightly slow collection still succeeds.
- Full production image builds and passes non-root server import smoke checks.
  The fresh scan confirms zero OS, AnyIO and urllib3 findings; the remaining
  SDK finding is the same advisory accepted by pip-audit, requiring matching
  narrow scanner configuration or a compatible upstream dependency fix.
- Final upgraded full fast suite: 2,779 passed, 30 skipped, 13 deselected,
  coverage 80.26%, exit 0 in 403.47 seconds. Counts match the baseline.
- Full production image health: HTTP 200 after normal lifecycle startup with
  fourteen plugins. Fresh Trivy 0.74 scan: zero unsuppressed fixable
  HIGH/CRITICAL findings, exactly one accepted SDK finding. The same exception
  leaves the original base failing on its vulnerable OS packages.
- SDK fixed 0.4.4 and latest 0.4.6 still require `websockets<17`, incompatible
  with the current web dependency. The package/version/path-scoped Trivy
  exception expires 2026-11-07 and records the same reachability proof as audit.
- The pinned action passes the YAML exception file unchanged. Its SARIF mode
  requires `limit-severities-for-sarif: true` to retain HIGH/CRITICAL filtering;
  explicitly enable that option to match the stated container gate.
  The final scan in SARIF format also exits 0; evidence is
  `reports/container-scan-accepted-20261008.sarif`.
- Evidence stays in ignored workspace reports: `ci-fast-anyio4.log`,
  `ci-nightly-collection.log`, `junit.xml`, `container-build-20261008.log`, and
  `container-scan-accepted-20261008.json`.
- Full production Windows bundle, nightly model execution and GitHub-hosted
  matrix results require separate validation; a minimal
  frozen probe does not establish those results.

Remote runs remain unchanged until these workspace changes are published.
The supplied guardrails prohibit Git push, so this repair does not publish them.
