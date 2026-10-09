# OmniScribe — Outstanding Work

**Updated:** 2026-10-06

**Purpose:** canonical list of open work only. Completed plans, audit details,
and earlier handoffs remain available in Git history or dated review artifacts.
Actions below were checked against source and documentation on this date;
fresh checks from the latest pass are recorded in the
[actionable closeout](actionable-closeout-2026-10-04.md) and the Architecture
Ledgers. Earlier counts below remain dated evidence rather than current totals.

## Closed on 2026-10-06 (previously listed here)

- **Optional extras CI test coverage gap.** Added dedicated `extras` job to
  `.github/workflows/nightly.yml`
  (`pytest (py${{ matrix.python }}, extras tier: lexicon + memory + glossary)`).
  Installs `--extra web --extra async-translation --extra lexicon --extra memory --extra glossary`
  and executes `pytest -m "not slow and not slow_dataset"`, ensuring that
  LanceDB, PyArrow, sentence-transformers, SQLAlchemy, openpyxl, and gitpython
  paths are exercised in CI without inflating PR-cycle latency in the lean fast tier.
- **CI pytest collection break.** Fixed bare `import pyarrow as pa` at module
  scope in `tests/core/test_lexicon_schema.py` by prepending
  `pytest.importorskip("pyarrow")` ahead of pyarrow and lexicon schema imports.
  Unblocked pytest collection across all non-extras CI runs.
- **CI vulnerability audit gate.** `uv run pip-audit` exits 0. `urllib3`
  upgraded 2.7.0 → 2.8.0 in `uv.lock`. Remaining three advisories (`anyio`
  PYSEC-2026-4024 / -4025 and `langgraph-sdk` CVE-2026-104873) risk-accepted
  with written reachability proofs in `.github/workflows/test.yml` and `Makefile`.
- **OCR pipeline HTTPS SSRF DNS-rebinding TOCTOU.** Threaded `resolved_ip` into
  `PromptedGroundedOCR` and `OCRProcessor`, building IP-pinned `httpx.AsyncClient`
  transports for OpenAI SDK and VLM prompt invocations, closed cleanly via
  `OCRPipeline.aclose`. Verified red/green with 4 new regression tests.

## Closed on 2026-10-04 (previously listed here)

- **Job History widget coverage and error feedback.** Four widget regressions
  cover list/refresh, clear confirmation, clear failure retaining rows and fetch
  failure recovery. Fetch/clear errors now appear in the history screen.
  Latest Flutter gates pass: 447 tests, fatal-info analysis, 171-file formatting,
  ownership and JSON contracts.
- **Flutter Windows and web release builds.** Both normal builds completed:
  `client/build/windows/x64/runner/Release/omniscribe_client.exe` and
  `client/build/web`. Web reports an absent optional CupertinoIcons font.
  Building alone does not establish the scanned-document device journey.
- **No-VLM Windows real-server integration.** The ordinary desktop integration
  run passed without skipping: real server connection, sample staging and PDF
  preview rendering, followed by clean teardown. This does not establish OCR.
- **Pinned dataset acquisition and upstream review.** The dataset script now
  preserves upstream README, revision, digests and original annotations; it
  supports OCR-Quality source acquisition, KIE-HVQA annotation acquisition and
  bounded OmniDocBench images/page conversion with explicit research-only
  acknowledgement. Real KIE-HVQA and one-page OmniDocBench acquisitions passed.
  Existing calibration/regional conversions still need missing measurements;
  see [dataset evidence and declared terms](benchmarks.md#5-dataset-ingestion--license-review-status).
- **Bundle exclusion audit and bounded smoke tooling.** The installed runtime
  import chain passes with excluded modules unavailable. Both smoke entry points
  share deadline-aware polling, workspace storage and occupied-port rejection.
  The existing binary served health and sample PDF successfully. Extracted
  evidence was retained after access-denied cleanup; no permission bypass was
  attempted. A fresh source release build remains distinct from this probe.

- **Flutter analysis and widget/unit test execution.** The SDK directory denial
  that blocked these is gone. `flutter analyze --fatal-infos` and
  `flutter test` both run and now pass (439 tests, 0 failures) after fixing 24
  analyzer findings and 5 test failures. Executing them found two real layout
  defects (glossary header pushed its primary action off-screen; export modal
  bottom unreachable) and a test-contract contradiction. Evidence:
  [the gate-execution record](goal-alignment-assessment-2026-10-03.md#gate-execution-2026-10-04-later-pass).
- **Normal Dart formatting.** `dart format` now runs; the tree is canonical
  (169 files, 0 changed). It must be run *before* the lint gate, because
  formatting can surface findings that unformatted sources hide.
- **Windows server bundle build.** The `PermissionError [WinError 5]` on
  `%APPDATA%\Python\Python313\site-packages` did not recur; `PyInstaller
  omniscribe_server.spec` completes. The first build was still broken at boot —
  see the packaging fix below.
- **Redis broker/state path against a real daemon.** State persistence, queue
  dispatch, lease acquisition, terminal outcomes and worker heartbeat are
  confirmed live with one resolved broker URL. Job *completion* is not — see the
  open item below.

## Release and deployment decisions

- Decide whether the Redis/distributed-worker changes belong in a rebuilt
  v0.3.0 artifact or a new patch release. Re-run the Windows bundle smoke gate
  before publishing either option.
- Establish and measure a production Redis deployment profile: worker count,
  throughput target, visibility timeout, Redis topology, and TLS/auth settings.
  This is an operator decision and was **not** attempted; the local throwaway
  instance used for the functional check is not a production profile.
  Run `scripts/dev_redis_smoke.py --jobs 4 --verify-recovery --timeout 600`
  against that real deployment, restarting the identified worker at the printed
  cue. The opt-in smoke now submits concurrent translation jobs, checks
  token-bound HTTP/Redis results and requires the same job to move to a different
  worker before completion. The default remains a health/keyspace probe; offline
  regressions do not establish production throughput or restart recovery.
  Export the deployment's `OMNISCRIBE_AUTH_TOKEN` for an authenticated API;
  the script rejects redirects and accepts `rediss://` Redis URLs for TLS.

## Verification gaps

- ~~The `fast` CI job is red on its vulnerability step.~~ **Closed 2026-10-06.**
  `uv run pip-audit` exits 0. The three `urllib3` advisories
  (PYSEC-2026-4177 / -4176 / -4175) were fixed properly — `urllib3` 2.7.0 →
  2.8.0 in `uv.lock`, no design decision needed since neither `requests` nor
  `lance-namespace-urllib3-client` caps it. The other three are risk-accepted
  with an explicit reachability proof recorded at the step in
  `.github/workflows/test.yml` and mirrored in the `Makefile` `audit` target:
  `anyio` PYSEC-2026-4024 / PYSEC-2026-4025 (no anyio usage in `src/` at all;
  and the IDNA vector is unreachable because the SSRF pin passes a literal IP to
  `httpcore`'s `connect_tcp`) and `langgraph-sdk` CVE-2026-104873 (affects
  `@auth.on.*` handlers on a self-hosted LangGraph server; OmniScribe imports
  only `langgraph.graph.StateGraph` and never `langgraph_sdk`). Re-verify each
  proof before keeping its flag. Note this corrected two errors in the previous
  entry: it was six advisories, not five — `langgraph-sdk` CVE-2026-104873 was
  missing entirely — and the `anyio` advisories are **not** blocked on the
  PyInstaller `_lazyimport` story, which is a separate packaging concern.

- ~~**The `fast` and `nightly` CI jobs both failed at pytest collection.**~~
  **Closed 2026-10-06.** `tests/core/test_lexicon_schema.py` did a bare
  `import pyarrow as pa` at module scope. `pyarrow` is only in the
  `memory`/`lexicon` extras, and neither workflow installs them, so the import
  aborted the whole suite before a single assertion ran — `pyarrow` is also a
  hard module-level import of `core/lexicon/schema.py`, which the test imports
  right after. Fixed with `pytest.importorskip("pyarrow")` placed ahead of both
  imports, matching the three sibling lexicon tests that already skip this way.
  This was invisible locally because the developer venv had the `lexicon` extra
  installed; it is the reason the earlier "clean full backend gate" numbers were
  only ever reproducible on a machine, not in CI.

- ~~**The `memory` and `lexicon` extras run in no CI workflow at all.**~~
  **Closed 2026-10-06.** Closed by adding a dedicated `extras` job to
  `.github/workflows/nightly.yml`
  (`pytest (py${{ matrix.python }}, extras tier: lexicon + memory + glossary)`).
  The job syncs `--extra web --extra async-translation --extra lexicon --extra memory --extra glossary`
  and executes `pytest -m "not slow and not slow_dataset"`, ensuring that
  LanceDB, PyArrow, sentence-transformers, SQLAlchemy, openpyxl, and gitpython
  integration paths are exercised on a scheduled and manual-dispatch cadence.
  This eliminates dark coverage while strictly preserving the lean fast-tier PR
  gate policy documented at `test.yml:67-77`.

- ~~**SSRF rebinding unpinned for HTTPS in the main OCR pipeline.**~~ **Closed
  2026-10-06.** `pipeline_bridge.py` could only rewrite the URL to the resolved IP
  for plain `http` (an https URL→IP rewrite breaks SNI and certificate
  validation), so for an HTTPS `api_base` the validated address was discarded and
  the hostname re-resolved on connect. Both engines were affected. `resolved_ip` is
  now threaded into `PromptedGroundedOCR` and `OCRProcessor`, each builds an
  IP-pinned `httpx.AsyncClient` and passes it to `call_llm` and to the ephemeral
  `AsyncOpenAI`; `ChatClient` gained an `http_client` parameter, and
  `OCRPipeline.aclose` now releases the grounded backend's client as well as the
  hybrid processor's. Both regression tests are parametrised over both engines and
  verified red against the pre-fix source. Every outbound LLM path now pins:
  `documents`, `translate`, `transcribe`, the OCR pre-flight probe, and both OCR
  engines.

- ~~Obtain a clean full backend fast/coverage gate.~~ **Closed 2026-10-04.**
  `pytest -m "not slow and not slow_dataset" --cov=src/omniscribe
  --cov-fail-under=80` → **2,866 passed, 18 skipped, 13 deselected, 0 failed,
  exit 0 in 420.21 s**, 84.04% coverage. The previous 2,848-passed run's single
  child-process timeout is resolved: it was a too-tight 45 s per-subprocess
  ceiling against a measured ~8.7 s cold interpreter start, not a product
  defect, and the ceiling is now `_CHILD_OWNER_TIMEOUT_S = 180`. Note the
  skipped 18 are missing optional extras (`sqlalchemy` for the glossary extra,
  `pytesseract`), not deferred work. Coverage for the SSRF-pinned probe is
  partial by design — the `pinned_client is None` branch is unreachable from
  `preflight_check` because `SSRFCheckResult.allowed` is True only when a
  concrete `resolved_ip` exists.

- **Reachable inference endpoint.** `.env` points `OCR_API_BASE` at a LAN host
  and `TRANSLATION_API_BASE` at an unresolvable placeholder, so every live
  translation job ends `LLMCallError: ... All connection attempts failed`.
  (Re-verified live 2026-10-06 via socket reachability check: `OCR_API_BASE`
  192.168.1.75:1234 timed out, `TRANSLATION_API_BASE` translation-host:80 failed
  DNS resolution, and `OMNISCRIBE_VLM_URL` is unset). With a reachable endpoint,
  close the two remaining Redis gates: a job that *completes*, and the
  abrupt-worker-restart lease handoff (`--verify-recovery`).
- Execute the opt-in live VLM OCR integration run documented in
  [client/README.md](../client/README.md#real-server-integration-verification).
  `OMNISCRIBE_LIVE_OCR=true` now exercises model-backed OCR on a rasterized
  sample through the Flutter workstation and checks recognized content and
  processed PDF output. Its live result remains unverified.
- Produce real confidence measurements matching OCR-Quality outputs and
  verified regional boxes/reliability masks for KIE-HVQA; their upstream schemas
  do not contain the fields required by the existing full regressions. Source
  acquisition is implemented; the large OCR-Quality Parquet download remains
  unexecuted. OmniDocBench research acquisition/page conversion is verified for
  one page. Review underlying-document rights before broader use or publication,
  then run a shared model-backed protocol and publish comparable results.
  Competitor numbers remain illustrative; live `--score-markdown` runs remain
  manual because CI has no configured VLM endpoint.
- Capture `docs/screenshots/drop-to-result.gif` during a live VLM run.
- Device and browser journeys remain open. Windows/web builds, analysis and
  the widget/unit suite now pass; scanned upload → PDF search/copy → complete
  text/structured export on a real device remains unexecuted. The
  [2026-09-29 review](modularity-review-2026-09-29.md#verification) and
  [2026-09-30 document-state review](document-state-review-2026-09-30.md#verification-and-limits)
  record the compiler/native assertions and environment limits that still apply.

## Product follow-ups

- Decide whether to add a multilingual sample document and an in-app first-run
  walkthrough around the existing **Try sample PDF** action.
- Add widget coverage when a concrete uncovered user journey is found.
  Export readiness/replacement, short export-modal scrolling, responsive
  glossary headers and Job History list/refresh/clear/error paths have passing
  assertions; there is no additional identified widget task in this review.

## Low-priority maintenance

- Remove the test-pinned `RuntimeSettings.cors_origins_raw` compatibility
  property only in a breaking cleanup.
- Standardize the overlapping result/artifact identifier names across state,
  job, OCR, and translation response models.
- Revisit the O(n·m) recall-candidate deduplication scan only if profiling shows
  pathological box counts in real documents.
- Simplify `HybridEngine.__init__` and `OCRRequest` only alongside their broad
  caller/test migrations; avoid compatibility wrappers solely for aesthetics.
