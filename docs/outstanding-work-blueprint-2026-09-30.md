# Outstanding work implementation blueprint

Preserve the existing feature-owned architecture and all in-progress changes.
This pass implements executable verification gaps; release version selection,
production measurements, external dataset licensing, and live capture require
their actual environments and remain open until evidenced.

| Files / owner | Single responsibility and acceptance condition |
| --- | --- |
| `scripts/dev_redis_smoke.py` / Redis agent | Extend the existing probe with opt-in concurrent asynchronous job submission, bounded terminal/result verification, and explicit recovery verification using existing queue/worker contracts. Retain the health-only default; never kill unrelated processes or mutate unrelated Redis keys. |
| `tests/scripts/test_dev_redis_smoke.py` / Redis agent | Offline regression checks for smoke success, failure, timeout and recovery claims. |
| `client/integration_test/app_real_server_test.dart` / OCR agent | Add opt-in model-backed OCR using existing client orchestration; require recognized content and processed output, avoid the digital text fast path, and fail explicitly when opted-in prerequisites are missing. |
| `client/README.md` / OCR agent | Document the opt-in invocation, endpoint/model configuration and limits. |
| Existing Flutter verification commands / verification agent | Attempt the normal targeted gate without bypassing directory permissions; report precise results and blockers. Add focused widget coverage only where a concrete uncovered behavior is found. |
| `docs/outstanding-work.md`, `docs/ARCHITECTURE.md`, root ledger / lead | Record completed implementation separately from unexecuted deployment verification, and document each new file's responsibility. |
| This artifact / lead | Record scope, parallel ownership and final verifiable evidence. |

All agents must validate trust boundaries, use contextual deterministic errors,
strict available types and repeat-safe state transitions. Reuse installed tools
and helpers. Git operations are limited to status, log and diff. Do not write
outside the project or bypass denied user/SDK directories.

Validation: focused script tests, Ruff checks and relevant typing; normal Flutter
format/analyze/targeted tests when permitted; final diff whitespace and ledger
checks. A mocked test does not establish a real deployment or live VLM result.

## Verification evidence

- Source ownership check passed: `rtk python client/tool/check_feature_layout.py`
  reported 166 Dart files and 31 unique provider declarations.
- Normal Dart formatting failed before formatting with `PathAccessException`
  creating `C:\Users\rahin\AppData\Roaming\.dart-tool` (access denied,
  errno 5). SDK checks were halted without redirects or helper runners.
- The existing export modal smoke suite now checks format-specific readiness
  and document replacement clearing recognized text/artifact readiness. These
  new assertions remain unexecuted until the normal Flutter launcher can run.
- Redis smoke submits concurrent translation jobs, verifies terminal results
  against token-bound Redis artifacts, and observes a different worker claiming
  the same job during operator-triggered recovery. Authentication uses the
  backend bearer token environment variable; redirects reject forwarding tokens.
- Focused pytest: **22 passed** (21 smoke regressions plus the existing script
  import check). Coverage includes concurrency, artifacts, terminal failures,
  timeout cleanup, invalid inputs, recovery evidence and real HTTP credential/
  redirect behavior. Pytest reported a denied write to its existing node-ID cache;
  no cache permission or redirection workaround was attempted.
- Ruff lint and format checks passed for both Python files. Focused mypy passed
  for the script with `--follow-imports=skip --ignore-missing-imports`; this does
  not establish whole-project typing. A transient mapped-file formatter error
  resolved when ordinary patching and formatting succeeded; no bypass was used.
- Live OCR additionally verifies the processed PDF through real preview rendering.
  Its child runtime stays beneath the workspace with memory state/in-process
  dispatch; Windows teardown terminates only the owned process tree before cleanup.
- No live Redis/worker recovery, live VLM, device/browser, external dataset or
  Windows release-bundle run was performed in this pass. Release decisions and
  licensing remain open in the canonical work list.
- Final diff whitespace and local Markdown target checks passed across six
  affected documents. Existing unrelated changes were preserved.
