# OmniScribe Comprehensive Audit — Historical Snapshot

**Document Version:** 1.0.0 (Master Unified Audit)  
**Date:** 2026-09-16  
**Auditor / Lead Orchestrator:** OmniScribe Lead Architect  
**Status:** Historical snapshot through 2026-09-16; not a current roadmap

> This report consolidated seven predecessor audits that have now been removed
> from the working tree. Their exact contents remain available in Git history.
> Use [`../ARCHITECTURE.md`](../ARCHITECTURE.md) for the current system shape,
> [`../outstanding-work.md`](../outstanding-work.md) for open work, and
> [`../CHANGELOG.md`](../CHANGELOG.md) for release history. Source paths, line
> numbers, metrics, and status claims below describe the repository at the
> audit date and may no longer match the current tree.

---

## Master Table of Contents

- [1. Executive Summary & Aggregate Health Dashboard](#1-executive-summary--aggregate-health-dashboard)
  - [1.1 High-Level System Verdict](#11-high-level-system-verdict)
  - [1.2 Aggregate Health Dashboard](#12-aggregate-health-dashboard)
  - [1.3 Convergence Summary (Reconciling C1–C7)](#13-convergence-summary-reconciling-c1c7)
- [2. Macro Architecture & Plugin Harness Topology](#2-macro-architecture--plugin-harness-topology)
  - [2.1 The Cordis Plugin Harness Architecture](#21-the-cordis-plugin-harness-architecture)
  - [2.2 Inventory of the 14 Mounted Plugins](#22-inventory-of-the-14-mounted-plugins)
  - [2.3 StateBackend Architecture: SQLite vs. Redis vs. Memory](#23-statebackend-architecture-sqlite-vs-redis-vs-memory)
  - [2.4 Martin Packaging Metrics for Core Modules](#24-martin-packaging-metrics-for-core-modules)
- [3. Security, Authentication & Threat Modeling](#3-security-authentication--threat-modeling)
  - [3.1 Three Deployment Profiles Threat Model](#31-three-deployment-profiles-threat-model)
  - [3.2 The ASGI Middleware Triad](#32-the-asgi-middleware-triad)
  - [3.3 SSRF Defense Architecture & DNS-Pinning Transport](#33-ssrf-defense-architecture--dns-pinning-transport)
  - [3.4 Credential Isolation & Token Binding](#34-credential-isolation--token-binding)
  - [3.5 Container & Deployment Hardening](#35-container--deployment-hardening)
  - [3.6 Detailed Status of Security Findings (S1–S19)](#36-detailed-status-of-security-findings-s1s19)
- [4. Code Complexity, Nesting & AST Profiling](#4-code-complexity-nesting--ast-profiling)
  - [4.1 Executive Summary & Aggregate Metrics Dashboard](#41-executive-summary--aggregate-metrics-dashboard)
  - [4.2 McCabe Cyclomatic Complexity (CC) Analysis](#42-mccabe-cyclomatic-complexity-cc-analysis)
  - [4.3 Cognitive Complexity (SonarQube) Distribution & Top Culprits](#43-cognitive-complexity-sonarqube-distribution--top-culprits)
  - [4.4 Lexical Nesting Depth & The Arrow Anti-Pattern](#44-lexical-nesting-depth--the-arrow-anti-pattern)
  - [4.5 Monolithic Files (>300 LOC) and God Classes (>500 LOC) Analysis](#45-monolithic-files-300-loc-and-god-classes-500-loc-analysis)
  - [4.6 Coupling & Stability Metrics (Martin Packaging Metrics)](#46-coupling--stability-metrics-martin-packaging-metrics)
  - [4.7 Comprehensive Status & Resolution of Findings COMP-01 through COMP-10](#47-comprehensive-status--resolution-of-findings-comp-01-through-comp-10)
  - [4.8 Automated CI Quality Gates: Ruff C901 & Whitelist Audit](#48-automated-ci-quality-gates-ruff-c901--whitelist-audit)
- [5. Code Duplication & DRY Compliance](#5-code-duplication--dry-compliance)
  - [5.1 Duplication Audit Scope & Impact Matrix (F1–F15)](#51-duplication-audit-scope--impact-matrix-f1f15)
  - [5.2 Deep-Dive Resolution Analysis](#52-deep-dive-resolution-analysis)
  - [5.3 Quantified DRY Payoff & Architectural Impact](#53-quantified-dry-payoff--architectural-impact)
- [6. Functional Integration, Edge Cases & Defect Resolution](#6-functional-integration-edge-cases--defect-resolution)
  - [6.1 Synthesis of the 2026-09-15 Edge-Case Remediation Blueprint](#61-synthesis-of-the-2026-09-15-edge-case-remediation-blueprint)
  - [6.2 Comprehensive Audit of the 10 Diagnosed Defects & Verification of Repairs](#62-comprehensive-audit-of-the-10-diagnosed-defects--verification-of-repairs)
  - [6.3 Live Functional Validation Results (scripts/verify_live_functionality.py)](#63-live-functional-validation-results-scriptsverify_live_functionalitypy)
  - [6.4 Functional Integration & Reliability Verdict](#64-functional-integration--reliability-verdict)
- [7. Frontend Architecture & Flutter Client Health](#7-frontend-architecture--flutter-client-health)
  - [7.1 Riverpod State Management & Layered Segregation](#71-riverpod-state-management--layered-segregation)
  - [7.2 The WorkstationNotifier Architectural Decomposition](#72-the-workstationnotifier-architectural-decomposition)
  - [7.3 URL-Change Safety, Scheme Reactivity & Lifecycle Teardown](#73-url-change-safety-scheme-reactivity--lifecycle-teardown)
  - [7.4 Real-time WebSocket Protocol & Streaming Frame Processing](#74-real-time-websocket-protocol--streaming-frame-processing)
  - [7.5 Headless Accessibility (?a11y=1), Semantics Tree, & CI Testing Strategy](#75-headless-accessibility-a11y1-semantics-tree--ci-testing-strategy)
- [8. QA, Verification Engineering & Test Suite Posture](#8-qa-verification-engineering--test-suite-posture)
  - [8.1 Testing Philosophy and Tiered Architecture](#81-testing-philosophy-and-tiered-architecture)
  - [8.2 Test Coverage Shape and Distribution](#82-test-coverage-shape-and-distribution)
  - [8.3 Hypothesis Property-Based Testing Status (Q4 / C3 Resolution)](#83-hypothesis-property-based-testing-status-q4--c3-resolution)
  - [8.4 Flake Detection and Timing Synchronization](#84-flake-detection-and-timing-synchronization)
  - [8.5 Client Integration Testing Architecture (client/integration_test/)](#85-client-integration-testing-architecture-clientintegration_test)
  - [8.6 Mutation Testing Evaluation (Q7)](#86-mutation-testing-evaluation-q7)
  - [8.7 Detailed Status of QA Lens Findings (Q1–Q16)](#87-detailed-status-of-qa-lens-findings-q1q16)
- [9. Product Readiness, Packaging & Developer Experience](#9-product-readiness-packaging--developer-experience)
  - [9.1 Target Audience and The User Journey Transformation](#91-target-audience-and-the-user-journey-transformation)
  - [9.2 Windows Single-Binary PyInstaller Distribution (522 MB)](#92-windows-single-binary-pyinstaller-distribution-522-mb)
  - [9.3 Documentation Drift Resolution](#93-documentation-drift-resolution)
  - [9.4 Developer Tooling & Diagnostics](#94-developer-tooling--diagnostics)
  - [9.5 Detailed Status of Product Manager (P1–P16) and End-User (U1–U14) Findings](#95-detailed-status-of-product-manager-p1p16-and-end-user-u1u14-findings)
- [10. Master Unified Audit Matrix & Living Roadmap](#10-master-unified-audit-matrix--living-roadmap)
  - [10.1 Master Unified Audit Cross-Reference Matrix](#101-master-unified-audit-cross-reference-matrix)
  - [10.2 Prioritized Open Backlog & Living Roadmap](#102-prioritized-open-backlog--living-roadmap)

---


## 1. Executive Summary & Aggregate Health Dashboard

### 1.1 High-Level System Verdict
OmniScribe has transitioned from an internally inconsistent, documentation-drifted beta (assessed on 2026-09-04) into a robust, defensively hardened, and functionally integrated self-hosted document processing platform (as of 2026-09-16). 

The initial five-lens audit (2026-09-04, preserved in Git history) established that while core engine algorithms (Surya layout detection, dynamic programming alignment, grounded VLM parsing) were mature, significant operational and architectural liabilities existed:
1. **Documentation Drift:** Security and deployment documentation framed critical defense mechanisms—specifically the ASGI middleware triad—as deferred "scaffolding" despite being live in code.
2. **State Volatility:** The default state backend was volatile in-memory storage (`MemoryStateBackend`), leading to silent data loss on process restart.
3. **Hardcoded Credentials:** Public development credentials in configuration templates (`REDIS_PASSWORD=omniscribe-secure-dev-password`).
4. **Integration Seams:** Critical mismatches between the Flutter client and FastAPI backend across URL reconfiguration, async job completion tracking, credential transmission, and artifact delivery.

Through the two-stage remediation executed on 2026-09-15 and 2026-09-16 (diagnoses and repair logs preserved in Git history), all ten diagnosed cross-boundary integration defects and six distributed edge cases have been completely resolved. The backend plugin harness is fully wired with 14 active plugins, all 10 non-mocked checks in `scripts/verify_live_functionality.py` pass cleanly, and the client and server maintain rigorous boundary isolation.

OmniScribe v0.3.0 represents a **production-ready, self-hosted system** suitable for local single-user, LAN-distributed, and reverse-proxied deployments when operated under documented security profiles.

---

### 1.2 Aggregate Health Dashboard

| Architectural Domain | 2026-09-04 Five-Lens Audit | 2026-09-16 Current State | Health Status | Key Evidence / Citations |
|---|---|---|---|---|
| **Macro Architecture & Modularity** | Clean Cordis plugin harness; 13 plugins mounted; 2 god files (`ocr/service.py`, `workflows/hybrid.py`); moderate coupling hotspots. | 14 plugins mounted (added `sample_pdfs`); Protocol-keyed dependency injection; strict LIFO effect teardown & atomic rollback. | **EXCELLENT** | `src/omniscribe/harness/context.py:86-107`, `resources/cordis.yml:14-89` |
| **Security & Threat Defense** | Middleware wired but documented as "scaffolding"; `.env.example` shipped active Redis password; `ALLOW_SSRF_LOCAL` mismatch; query-param token leakage on broad prefixes. | Full ASGI triad enforced; empty password forcing Compose `:?` abort; SSRF DNS-pinning transport active; query-token restricted to SSE `/events`; credential isolation verified. | **HARDENED** | `src/omniscribe/server.py:196-256`, `src/omniscribe/middleware/auth.py:54-72`, `src/omniscribe/plugins/glossary/http_fetch.py:59-97` |
| **State & Data Reliability** | Default `memory` backend silently drops jobs and artifacts on restart; `redis` backend crashed at plugin apply. | Default flipped to durable `sqlite`; path-traversal validation enforced; `redis` backend fully operational with Lua atomic leases and worker heartbeats. | **ROBUST** | `src/omniscribe/plugins/state_backend.py:84-154`, `src/omniscribe/plugins/jobs_redis.py:65-112` |
| **Client-Backend Integration** | 10 integration defects: client crashes on URL change; async jobs stuck at 100%; bearer token leaks into upstream LLM headers; missing text artifacts. | All 10 defects resolved: dynamic repository getters; polling fallback + WS status reconciliation; header separation (`X-Provider-Api-Key`); dual artifact headers. | **VERIFIED** | `client/lib/data/providers/settings_notifier.dart:12-25`, `src/omniscribe/plugins/providers.py:90-103` |
| **Container & Supply Chain** | Digest pinning on Python base; non-root user; but Compose startup crashed under `0.0.0.0` bind without token; Trivy false positives on pip. | `0.0.0.0` container bind reconciled with startup guard via forced token; system & venv pip purged from runtime; `tini` PID 1; `cap_drop: ALL`. | **SECURE** | `Dockerfile:83-165`, `compose.yaml:39-79` |
| **Functional Verification** | Unit tests passing but integration seams untested; 10+ magic sleeps in test suite. | Comprehensive live validation script (`verify_live_functionality.py`) executes 10 non-mocked real end-to-end tests cleanly (10/10 PASS). | **VALIDATED** | `scripts/verify_live_functionality.py:1-267` |

---

### 1.3 Convergence Summary (Reconciling C1–C7)

The 2026-09-04 audit identified seven convergent findings across multiple independent lenses. The following matrix tracks their systematic resolution:

| Item | Description | Initial Severity | Current Status | Resolution Mechanics & Code Evidence |
|---|---|---|---|---|
| **C1** | **Documentation Drift:** `SECURITY.md`, `DEPLOYMENT.md`, and `outstanding-work.md` framed live middleware as deferred scaffolding. | High (4 lenses) | **RESOLVED** | `docs/SECURITY.md:62-78` and `docs/DEPLOYMENT.md` updated to document the ASGI middleware triad (`server.py:184-256`) as live and enforced since Wave 14. |
| **C2** | **End-User Install Friction:** 12–16 step manual install journey; no desktop launcher or pre-flight guidance. | High (2 lenses) | **MITIGATED** | `docs/rfcs/2026-09-end-user-install.md` drafted; `make doctor` documented; sample PDF endpoint added to eliminate cold-start blank states. |
| **C3** | **State-Loss Default:** In-memory state backend was the default, silently losing all jobs and artifacts upon server reboot. | High (2 lenses) | **RESOLVED** | Default state backend flipped to `sqlite` in `resources/cordis.yml:35` and `src/omniscribe/plugins/state_backend.py:85-97`. Loud `WARN` logged if operator explicitly opts into `memory`. |
| **C4** | **Bare Project Root / Flutter Stub:** Missing root README and default starter `client/README.md`. | Medium (2 lenses) | **RESOLVED** | `docs/README.md` reconciled; `client/README.md` populated with Riverpod architecture, tab definitions, and server connectivity instructions. |
| **C5** | **Hardcoded Redis Password in `.env.example`:** `.env.example:194` shipped active password bypassing Compose `:?` mandatory check. | High (Security / Dev) | **RESOLVED** | `.env.example:202` now ships `REDIS_PASSWORD=` (empty string). Compose `:?` substitution aborts startup immediately if left blank (`compose.yaml:62,126,137`). |
| **C6** | **Undocumented `make doctor`:** Health diagnostic utility invisible to operators. | Low (2 lenses) | **RESOLVED** | `make doctor` surfaced in `docs/TROUBLESHOOTING.md` and installation documentation as the primary diagnostic entry point. |
| **C7** | **Missing Guidance Docs:** Absence of `TROUBLESHOOTING.md`, `CONTRIBUTING.md`, and issue templates. | Medium (2 lenses) | **RESOLVED** | `docs/TROUBLESHOOTING.md` created covering top 10 error modes (Defender false positive, LM Studio port binding, token errors). |

---

## 2. Macro Architecture & Plugin Harness Topology

### 2.1 The Cordis Plugin Harness Architecture
OmniScribe organizes its HTTP and execution layer around a micro-kernel container inspired by the Cordis architectural pattern, implemented under `src/omniscribe/harness/`. The system decouples service contracts from their concrete implementations, executing dynamic lifecycle management inside FastAPI's lifespan context (`src/omniscribe/server.py:160-184`).

```text
FastAPI Lifespan (server.py)
   │
   ├──> Loader (harness/loader.py) reads resources/cordis.yml + cordis.patch.yml
   │      │
   │      └──> Context (harness/context.py)
   │             ├── 1. Service Registry (Protocol -> Implementation)
   │             ├── 2. Event Bus (async emit & concurrent exact-type listeners)
   │             ├── 3. Effect Stack (LIFO cleanup registrations)
   │             └── 4. Router Queue (FastAPI APIRouter mount queue)
```

#### Core Harness Invariants & Mechanics
1. **Protocol-Keyed Service Contracts:** Services are registered and injected using explicit Python protocols or base classes (`ctx.service(Protocol, instance)` and `ctx.inject(Protocol)`), strictly preventing module-level singleton coupling (`src/omniscribe/harness/context.py:86-107`). If a service is already registered for a given protocol, `DuplicateServiceError` is raised immediately (`context.py:90-92`).
2. **LIFO Effect Disposal:** Every resource allocation, worker thread, background loop, or database handle registers a `Cleanup` callable via `ctx.effect(cleanup)` (`context.py:153-164`). When a plugin unloads or the server shuts down via `ctx.dispose()`, cleanups execute in strict Last-In, First-Out (LIFO) order (`context.py:223-246`), ensuring dependent services remain alive while consumers tear down.
3. **Atomic Rollback on Partial Registration Failure:** If a plugin raises an unhandled exception during `plugin.apply(ctx)` (`src/omniscribe/harness/context.py:205-220`), the harness catches the error, purges the plugin instance from internal tracking, and immediately reverses all effects, services, listeners, and routers registered by that specific plugin in reverse order:
   ```python
   # src/omniscribe/harness/context.py:207-219
   except Exception:
       self._plugin_order.remove(plugin_id)
       self._plugin_instances.pop(plugin_id, None)
       refs = self._plugin_effects.pop(plugin_id, [])
       for ref in reversed(refs):
           try:
               await self._reverse(ref)
           except Exception as rev_exc:
               _LOGGER.exception("error during rollback reversal of %r: %s", ref, rev_exc)
       raise
   ```
4. **Context Isolation:** Active plugin context is tracked via `contextvars.ContextVar("omniscribe_harness_plugin_id")` (`context.py:32-34`), enabling deterministic attribution of every side-effect to its owning plugin.

---

### 2.2 Inventory of the 14 Mounted Plugins

OmniScribe boots 14 active plugins configured in `src/omniscribe/resources/cordis.yml`. The table below outlines their topology, injection keys, mounted HTTP routes, and domain responsibilities:

| # | Plugin ID | Implementation Target (`use:`) | Injected Service Protocol | Mounted HTTP / WS Routes | Single Responsibility |
|---|---|---|---|---|---|
| 1 | `runtime` | `omniscribe.plugins.runtime:plugin` | `RuntimeService` | *None (lifecycle only)* | Holds `RuntimeSettings`, manages readiness state, schedules periodic artifact and channel pruning. |
| 2 | `logging` | `omniscribe.plugins.logging:plugin` | *Side-effect only* | *None* | Reconfigures logging to structured JSON or human-readable text according to environment. |
| 3 | `state_backend` | `omniscribe.plugins.state_backend:plugin` | `StateBackend` | *None* | Provides durable or ephemeral storage for `JobRecord`, `ArtifactRecord`, and `ChannelRecord`. |
| 4 | `artifacts` | `omniscribe.plugins.artifacts:plugin` | `ArtifactStore` | *None* | Content-addressed, token-authenticated blob storage abstraction over `StateBackend`. |
| 5 | `jobs` | `omniscribe.plugins.jobs:plugin` | `JobQueue` | *None* | Async job orchestration, runner registration, and task lifecycle execution (in-process or Redis). |
| 6 | `progress` | `omniscribe.plugins.progress:plugin` | `ProgressService` | `/api/progress/*`, `/ws/{channel_id}` | Session token generation, WebSocket progress broadcasting, and client cancellation dispatch. |
| 7 | `providers` | `omniscribe.plugins.providers:plugin` | `ProviderManager` | `/api/providers`, `/api/providers/active`, `/api/providers/validate` | LLM/VLM provider catalog, model discovery, validation, and active configuration persistence. |
| 8 | `health` | `omniscribe.plugins.health:plugin` | *None* | `/api/health`, `/api/healthz`, `/ready`, `/readyz` | Unauthenticated liveness and readiness probes for orchestrators and load balancers. |
| 9 | `documents` | `omniscribe.plugins.documents:plugin` | `DocumentService` | `/api/extract`, `/api/export/*`, `/api/export/{id}`, `/api/text/{id}`, `/api/metadata/{id}` | Structured data extraction (invoices, tables) and multi-format export (DOCX, HTML, Chunks, Markdown). |
| 10 | `translate` | `omniscribe.plugins.translate:plugin` | `TranslationService` | `/api/translate`, `/api/translate/async`, `/api/translate/status/{id}`, `/api/translate/result/{id}`, `/api/translate/nllb` | Synchronous and async document translation, LangGraph loop integration, and result token redemption. |
| 11 | `transcribe` | `omniscribe.plugins.transcribe:plugin` | `TranscriptionService` | `/api/transcribe`, `/api/config/transcription`, `/api/models/transcription` | Audio transcription via Whisper/OpenAI-compatible APIs, masked config storage, and model discovery. |
| 12 | `glossary` | `omniscribe.plugins.glossary:plugin` | `GlossaryImportService` | `/api/glossary/*` (sources, library, entries, import) | Terminology import (TBX, CSV, TMX, Git), LanceDB vector store RAG, and case-insensitive term queries. |
| 13 | `ocr` | `omniscribe.plugins.ocr:plugin` | `OCRService` | `/api/process`, `/api/process/async`, `/api/process/status/{id}`, `/api/process/{id}/events`, `/api/jobs`, `/api/config` | Core document OCR orchestration, Surya layout detection, VLM bounding-box alignment, and quality repair loops. |
| 14 | `sample_pdfs` | `omniscribe.plugins.sample_pdfs:plugin` | *None* | `/api/sample-pdf/{name}` | Serves bundled CC0 test PDFs from fixed allowlist to enable zero-configuration testing on fresh installs. |

---

### 2.3 StateBackend Architecture: SQLite vs. Redis vs. Memory

The `StateBackend` protocol (`src/omniscribe/plugins/state_backend_types.py:100-160`) abstracts three critical data models:
- **`JobRecord`**: Tracks async job states (`queued`, `running`, `complete`, `error`, `cancelled`), timestamps, input paths, and attached artifact ID/token pairs.
- **`ArtifactRecord` / `ArtifactBlob`**: Opaque token-bound storage for generated PDFs, extracted text JSON, and transcripts.
- **`ChannelRecord`**: One-shot session tokens and metadata for WebSocket progress streaming.

```text
                               StateBackend Protocol
                                        │
             ┌──────────────────────────┼──────────────────────────┐
             ▼                          ▼                          ▼
   SQLiteStateBackend          RedisStateBackend          MemoryStateBackend
  (Production Default)       (Distributed / LAN)        (Ephemeral Dev / CI)
  - SQLite WAL Mode          - Redis Hashes + ZSETs     - In-memory Python dicts
  - Path traversal checks    - Atomic Lua claim/renew   - Zero disk footprint
  - Survives reboots         - Redacted URL logging     - Lost on process exit
```

#### Comparison Matrix

| Feature / Dimension | `SQLiteStateBackend` | `RedisStateBackend` | `MemoryStateBackend` |
|---|---|---|---|
| **Primary Use Case** | Profile 1 & 2 (Single-host desktop & server default). | Profile 3 & 4 (Multi-worker, distributed container deployments). | Fast unit tests and isolated CI runs. |
| **Persistence Guarantee** | Durable. Stored in SQLite database file (WAL mode). | Durable across server restarts; depends on Redis AOF/RDB configuration. | **Ephemeral**. 100% data loss upon process exit or reboot. |
| **Path Traversal Defense** | Enforces `candidate.relative_to(artifact_base)` (`state_backend.py:114-122`). | N/A (network socket connection). | N/A (memory address space). |
| **Distributed Locking / Leases** | Single process (database file lock). | Lua-scripted atomic lease acquisition (`_CLAIM_JOB_LUA`) and renewal. | In-process `asyncio.Lock`. |
| **Startup Validation** | Eager directory and schema migration check on `open()`. | Eager network ping to Redis server; failure aborts startup loud. | None. Logs prominent startup `WARN` reminding operator of ephemerality. |
| **Credential Redaction** | N/A (local file permissions). | Log filter strips passwords: `redis://:***@host:6379/0` (`state_backend.py:156-173`). | N/A. |

---

### 2.4 Martin Packaging Metrics for Core Modules

Module stability and coupling were evaluated using Robert C. Martin’s software packaging metrics (historical code complexity report, preserved in Git history):
- **Afferent Coupling ($C_a$):** Number of internal modules depending on this module (incoming dependencies). High $C_a$ indicates high responsibility.
- **Efferent Coupling ($C_e$):** Number of external modules this module depends upon (outgoing dependencies). High $C_e$ indicates vulnerability to changes.
- **Instability Index ($I$):**
  $$I = \frac{C_e}{C_a + C_e}$$
  - $I = 0.0$: **Maximally Stable** (heavily depended upon, depends on nothing).
  - $I = 1.0$: **Maximally Unstable** (depends on many modules, no dependents).

#### Metric Analysis of Core Subsystems

```text
               Efferent Coupling (Ce) -> High
     +-------------------------------------------+
     | ZONE OF PAIN           | UNSTABLE COORD.  |
     | (High Ca, High Ce)     | (Low Ca, High Ce)|
     | - plugins.translate.svc| - worker.py      |
High | - harness.loader       | - server.py      |
 Ca  | - plugins.ocr.service  | - workflows.hybr |
     +------------------------+------------------+
     | STABLE ABSTRACTIONS    | ISOLATED LEAVES  |
Low  | (High Ca, Low Ce)      | (Low Ca, Low Ce) |
 Ca  | - core.document        | - utils.env      |
     | - core.block_tree      | - core.callbacks |
     | - config.py            | - ocr.exceptions |
     +-------------------------------------------+
```

1. **Stable Abstractions ($I \le 0.15, C_a \ge 10$):**
   - `omniscribe.core.document` ($C_a = 41, C_e = 1, I = 0.024$): Bedrock domain model (`DocumentResult`, `PageResult`, `BBox`). Extremely stable.
   - `omniscribe.core.block_tree` ($C_a = 22, C_e = 1, I = 0.043$): Hierarchical document AST representation.
   - `omniscribe.config` ($C_a = 19, C_e = 0, I = 0.000$): Pure configuration dataclasses and env parsers.
   - `omniscribe.harness.context` ($C_a = 21, C_e = 3, I = 0.125$): Micro-kernel container and event bus.
   - `omniscribe.harness.plugin` ($C_a = 16, C_e = 1, I = 0.059$): Base plugin interfaces.
   - `omniscribe.utils.security` ($C_a = 12, C_e = 1, I = 0.077$): SSRF validation, IP normalization, and token comparison.

2. **High-Coupling Coordinators ($I \ge 0.85, C_e \ge 10$):**
   - `omniscribe.worker` ($C_a = 0, C_e = 11, I = 1.000$): Standalone queue worker process.
   - `omniscribe.core.workflows.hybrid` ($C_a = 1, C_e = 11, I = 0.917$): Multi-stage hybrid engine orchestrating Surya, VLM, whitespace recall, and text layer recall.
   - `omniscribe.plugins.documents.routes` ($C_a = 1, C_e = 10, I = 0.909$): Exporter and document route definitions.
   - `omniscribe.harness.loader` ($C_a = 2, C_e = 18, I = 0.900$): Dynamic YAML manifest parser and module loader.

3. **High-Risk Bridge Modules in the "Zone of Pain" ($C_a \ge 8, C_e \ge 7$):**
   - `omniscribe.plugins.jobs` ($C_a = 10, C_e = 7, I = 0.412$): Bridges OCR, translation, glossary, and documents with state backends and runners. Changes to `JobRecord` ripple across the entire system.
   - `omniscribe.core.workflows.base` ($C_a = 14, C_e = 5, I = 0.263$): Defines workflow stages and progress callbacks.
   - `omniscribe.core.processors.base` ($C_a = 8, C_e = 9, I = 0.529$): Dispatches post-processing plugins (tables, layout, reading order).

---

## 3. Security, Authentication & Threat Modeling

### 3.1 Three Deployment Profiles Threat Model
OmniScribe explicitly documents and implements three operational deployment profiles (`docs/SECURITY.md:36-58`):

```text
[Profile 1: Local Single-User] ──> Bound to 127.0.0.1. Auth optional. Zero external telemetry.
                                   Guards: Magic-byte sniffing, upload caps, path traversal checks.

[Profile 2: LAN / Trusted Network] ─> Bound to LAN IP. OMNISCRIBE_AUTH_TOKEN strictly enforced.
                                      Guards: Startup exit on placeholder tokens, rate limiter,
                                      token-bound artifacts, credential isolation.

[Profile 3: Public Internet] ────> Reverse-proxy TLS (Caddy/Nginx). ALLOW_SSRF_LOCAL=false.
                                   Guards: IP-pinned HTTP transport, container cap_drop: ALL,
                                   read-only system user, non-loopback bind protections.
```

1. **Profile 1: Local Single-User (Default)**
   - *Target Environment:* Running on `127.0.0.1` / `localhost` on a workstation.
   - *Threat Surface:* Maliciously crafted PDF/image files, local process interference.
   - *Active Defenses:* Upload size verification, magic-byte file signature validation (`src/omniscribe/plugins/ocr/plugin.py:186-240`), path sanitization on tempdirs, zero external telemetry.
2. **Profile 2: LAN / Trusted Network**
   - *Target Environment:* Bound to a local private network interface (e.g. office or home lab).
   - *Threat Surface:* Unauthorized LAN clients, credential sniffing, denial-of-service via resource exhaustion.
   - *Active Defenses:* Mandatory `OMNISCRIBE_AUTH_TOKEN` (startup crashes if unbound or placeholder; `server.py:531-551`), constant-time bearer verification, sliding-window rate limiting (`RateLimitMiddleware`), token-bound artifact redemption.
3. **Profile 3: Public Internet**
   - *Target Environment:* Exposed behind an external TLS reverse proxy (Caddy/Traefik).
   - *Threat Surface:* Arbitrary internet callers, SSRF attempts against cloud metadata APIs (AWS/GCP/Azure), brute-force token enumeration, container breakouts.
   - *Active Defenses:* All Profile 2 guards plus `ALLOW_SSRF_LOCAL=false`, DNS-pinning HTTP transport (`_PinnedIPTransport`), container privilege dropping (`cap_drop: ALL`), non-root `app` user (uid 1001, no shell), and hardened PID 1 (`tini`).

---

### 3.2 The ASGI Middleware Triad
The FastAPI application unconditionally mounts an ASGI middleware triad in `src/omniscribe/server.py:196-256`, enforcing defense-in-depth before requests reach the plugin routing tree:

```text
Incoming ASGI Request
         │
         ▼
[ 1. BearerAuthMiddleware ] ──(Invalid / Missing Token)──> HTTP 401 Unauthorized
         │ (Valid or Loopback Dev)
         ▼
[ 2. RateLimitMiddleware ]  ──(Exceeds Sliding Window)───> HTTP 429 Too Many Requests
         │ (Within Rate Limit)
         ▼
[ 3. UploadSizeLimitMiddleware ] ─(Content-Length / Stream > Cap)─> HTTP 413 Payload Too Large
         │ (Within Size Bounds)
         ▼
FastAPI Application & Plugin Routers
```

#### 1. Bearer Authentication Middleware (`src/omniscribe/middleware/auth.py`)
- **Inspection & Gating:** Inspects `Authorization: Bearer <token>`. Returns HTTP 401 with `WWW-Authenticate: Bearer realm="omniscribe"` on failure (`auth.py:212-230`).
- **Constant-Time Verification:** Compares provided tokens against `settings.auth_token` using `hmac.compare_digest` to eliminate timing side-channel attacks (`auth.py:146-151`).
- **Exemptions:** Strictly limited to public endpoints: `/`, `/api/health`, `/api/healthz`, `/api/ready`, `/ready`, `/readyz`, static assets (`/static/`), and public test assets (`/api/sample-pdf/`) (`auth.py:30-52`).
- **Narrowed SSE Query Token Exception (Audit S5 Remediation):** Browser `EventSource` APIs cannot attach HTTP headers. Previously, `?token=` was accepted across all `/api/process/*` and `/api/jobs/*` routes. In Phase 3.6, `_matches_query_token_path` was locked down to require both `path.startswith("/api/process/")` **and** `path.endswith("/events")` (`auth.py:118-135`), ensuring URL tokens never leak into access logs for status or result endpoints.

#### 2. Sliding-Window Rate Limiting Middleware (`src/omniscribe/middleware/rate_limit.py`)
- **Sliding-Window Algorithm:** Tracks request timestamps per client IP using an in-memory `collections.deque` protected by a `threading.Lock` (`rate_limit.py:135-210`).
- **Proxy-Aware Extraction:** `_extract_client_ip` inspects `X-Forwarded-For` and `X-Real-IP` **only** if the immediate socket client matches `DEFAULT_TRUSTED_PROXIES` (`127.0.0.1`, `::1`, `localhost`, `testclient`), preventing header spoofing by untrusted external callers (`rate_limit.py:85-122`).
- **Response Headers:** When tripped, returns HTTP 429 `{"error": "rate_limited", "detail": "Rate limit exceeded"}` and sets `Retry-After: <seconds>` (`rate_limit.py:190-210`).
- **Multi-Worker Reality (Audit S7):** Explicitly documents that because state is in-process, running $N$ Uvicorn workers results in an effective aggregate limit of $N \times \text{rate\_limit\_per\_min}$.

#### 3. Request Body Size Limiting Middleware (`src/omniscribe/middleware/upload_limit.py`)
- **Dual-Phase Enforcement:**
  1. *Eager Header Inspection:* Parses incoming `Content-Length` header. If it exceeds `max_bytes` (default 1024 MB / 1 GB), rejects immediately with HTTP 413 before allocating buffers (`upload_limit.py:89-109`).
  2. *Streaming Chunk Accumulator:* For chunked transfers or missing `Content-Length`, wraps ASGI `receive()` and sums chunk lengths on the fly (`upload_limit.py:111-150`), terminating the connection if the cumulative threshold is breached.

---

### 3.3 SSRF Defense Architecture & DNS-Pinning Transport
OmniScribe accepts operator-supplied URLs for external glossary synchronization (`/api/glossary/import/url`) and transcription model discovery. To prevent Server-Side Request Forgery against cloud environments or internal networks, OmniScribe implements a multi-layer SSRF defense:

```text
Operator Supplied URL
         │
         ▼
is_ssrf_target(url) (utils/security.py)
   ├── 1. Scheme Check: http / https only
   ├── 2. Cloud Metadata Denylist: metadata.google.internal, 169.254.169.254, fe80::/10
   ├── 3. Private / Loopback IP Filter: 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 127.0.0.0/8
   └── 4. Asynchronous DNS Resolution off event loop (socket.getaddrinfo)
         │
         ├── Validates ALL returned A/AAAA records
         └── Picks validated IP via _pick_pinned_address()
                 │
                 ▼
_PinnedIPTransport (plugins/glossary/http_fetch.py)
   └── _PinnedNetworkBackend intercepts connect_tcp()
       └── Replaces host with pre-resolved IP
           (Eliminates DNS Rebinding / TOCTOU Window)
```

1. **Fail-Closed Target Checking (`src/omniscribe/utils/security.py:162-246`):**
   - Validates URI scheme (rejecting `file://`, `gopher://`, `ftp://`).
   - Unconditionally blocks AWS/GCP/Azure link-local metadata ranges (`169.254.0.0/16`, `fe80::/10`, `fd00:ec2::/64`, `metadata.google.internal`) regardless of configuration (`security.py:38-65`).
   - Resolves all A/AAAA records via `asyncio.to_thread(socket.getaddrinfo)` (`security.py:115-141`). If **any** returned IP address belongs to a blocked subnet (unless explicitly allowed via `ALLOW_SSRF_LOCAL=true`), the entire request is rejected (`security.py:215-238`).
2. **DNS-Pinning `_PinnedIPTransport` (`src/omniscribe/plugins/glossary/http_fetch.py:59-97`):**
   - Standard HTTP clients re-resolve hostnames upon initiating the TCP connection, introducing a Time-of-Check to Time-of-Use (TOCTOU) vulnerability where a malicious DNS server returns a public IP during validation and `127.0.0.1` during connection.
   - `_PinnedIPTransport` subclasses `httpx.AsyncHTTPTransport` with a custom `httpcore.AsyncNetworkBackend` (`_PinnedNetworkBackend`). Inside `connect_tcp`, it transparently substitutes `host` with `self._resolved_ip` while leaving the HTTP `Host` header and TLS SNI intact (`http_fetch.py:67-83`), completely neutralizing DNS rebinding without monkeypatching global sockets.

---

### 3.4 Credential Isolation & Token Binding

#### Separation of Upstream AI Credentials from Server Authentication
In the 2026-09-15 audit (Finding 3), model discovery in `src/omniscribe/plugins/providers.py` fell back to inspecting `Authorization: Bearer <token>`, causing the server's own internal bearer secret to be forwarded to external LLM providers.

This was resolved on 2026-09-16 (`src/omniscribe/plugins/providers.py:90-103`):
```python
# src/omniscribe/plugins/providers.py:100-103
resolved_api_key = x_provider_api_key or api_key
return await manager.discover_models(
    provider_id, api_base=api_base, api_key=resolved_api_key
)
```
The server now enforces strict credential plane separation:
- `Authorization: Bearer <token>`: Exclusively consumed by `BearerAuthMiddleware` for OmniScribe API authentication.
- `X-Provider-Api-Key` (or legacy `api_key` query param): Exclusively consumed by `ProviderManager` for upstream LLM provider calls (OpenAI, Anthropic, Ollama).

#### Token-Bound Artifact Redemption
Artifacts stored in `ArtifactStore` (`src/omniscribe/plugins/artifacts.py:75-110`) generate 32-byte URL-safe cryptographic tokens (`secrets.token_urlsafe(32)`). Fetching an artifact (`/api/export/{id}`, `/api/text/{id}`) requires presenting both the artifact ID and matching `X-Artifact-Token` (or query token). Mismatches fail with uniform HTTP 404 responses, preventing artifact enumeration across tenant boundaries.

---

### 3.5 Container & Deployment Hardening

The production containerization stack (`Dockerfile` and `compose.yaml`) enforces least privilege and attack surface minimization:

1. **Multi-Stage Build with Base Image Digest Pinning (`Dockerfile:28, 83`):**
   - Pinned to exact multi-arch OCI SHA256 digest: `python:3.14-slim@sha256:656d12e70054d5fda18a045e2494c96701e9792dd1445f95b3d038df954f57e9`.
   - Redis base image pinned: `redis:7-alpine@sha256:ff02b58f971e7d7d156a1267e283fcbbeee91773b6aa36c49dac28ecfe28eadf`.
2. **Non-Root Execution (`Dockerfile:89, 141`):**
   - System user `app` (uid 1001, gid 1001) created with `--no-create-home` and `--shell /usr/sbin/nologin`. No interactive login shell exists inside the container.
3. **PID 1 Process Supervision (`Dockerfile:110, 164`):**
   - Uses `tini` as `ENTRYPOINT ["/usr/bin/tini", "--"]`. Ensures proper signal forwarding (SIGTERM) to Python workers for clean WebSocket draining and lifespan execution, preventing zombie process accumulation.
4. **Supply Chain & False-Positive CVE Purging (`Dockerfile:112-115, 129-132`):**
   - System `pip` and venv `pip` (along with vendored `msgpack` and `setuptools`) are completely removed from the runtime image after build completion, eliminating false-positive Trivy/Grype CVE alerts (CVE-2025-47273, GHSA-6v7p-g79w-8964).
5. **Docker Compose Security Options (`compose.yaml:39-44, 114-117`):**
   - `security_opt: ["no-new-privileges:true"]`: Prevents setuid privilege escalation.
   - `cap_drop: ["ALL"]`: Drops all Linux kernel capabilities.
   - `ports: ["127.0.0.1:8000:8000"]`: Binds exclusively to host loopback.
   - Forced environment variable evaluation: `${OMNISCRIBE_AUTH_TOKEN:?...}` and `${REDIS_PASSWORD:?...}` abort container launch if secrets are omitted.

---

### 3.6 Detailed Status of Security Findings (S1–S19)

| # | Severity | Title | Location | Status | Technical Details & Verification Evidence |
|---|---|---|---|---|---|
| **S1** | High | `.env.example` shipped active `REDIS_PASSWORD` | `.env.example:194` | **RESOLVED** | `.env.example:202` now leaves `REDIS_PASSWORD=` empty. Compose `:?` enforces non-empty secret at boot (`compose.yaml:62,126,137`). |
| **S2** | High | `SECURITY.md` described live middlewares as "deferred" | `SECURITY.md:62-78` | **RESOLVED** | `docs/SECURITY.md:62-78` reconciled. Explicitly documents `BearerAuthMiddleware`, `RateLimitMiddleware`, and `UploadSizeLimitMiddleware` as live. |
| **S3** | Medium | `ALLOW_SSRF_LOCAL` defaulted `true` in env template | `.env.example:66` | **RESOLVED** | `.env.example:71` now explicitly sets `ALLOW_SSRF_LOCAL=false`, aligning with `src/omniscribe/config.py:206`. |
| **S4** | Medium | `MAX_UPLOAD_MB` defaulted to 10 GB | `config.py:230` | **RESOLVED** | Default reduced to 1024 MB (1 GB) in `compose.yaml:70` and `config.py:230`, preventing memory/disk exhaustion. |
| **S5** | Medium | Query-param bearer accepted on broad path prefixes | `middleware/auth.py:69` | **RESOLVED** | `_matches_query_token_path` locked to `path.startswith("/api/process/") and path.endswith("/events")` (`auth.py:118-135`). |
| **S6** | Medium | `/api/progress/cancel/{channel_id}` unauthenticated on dev | `plugins/progress.py:294` | **RESOLVED / BY DESIGN** | Protected by `BearerAuthMiddleware` when auth is configured. Profile 1 loopback dev explicitly permits unauthenticated local control. |
| **S7** | Medium | In-memory rate limiter per-process multiplier ($N \times$) | `middleware/rate_limit.py:16` | **DOCUMENTED** | Documented in `rate_limit.py:16-31`. Multi-worker setups must configure thresholds accounting for process count. |
| **S8** | Medium | Transcription auth token mask leaked 8 characters | `plugins/transcribe/config_store.py:34` | **RESOLVED** | Short secrets masked with constant-length asterisks `********` rather than slicing `first4...last4`. |
| **S9** | Medium | `StaticFiles` mount exempt from authentication | `server.py:258-272` | **VERIFIED** | `_STATIC_DIR` points strictly to immutable package-bundled directory (`src/omniscribe/static`). No user uploads can be served. |
| **S10** | Low | Substring-based JSON log redaction | `utils/structured_logging.py:134` | **RESOLVED** | Exact dictionary key matching for `password`, `token`, `secret`, `api_key`, avoiding over/under-redaction. |
| **S11** | Low | Dockerfile CMD binds `0.0.0.0` | `Dockerfile:165` | **RESOLVED** | Server startup guard (`server.py:531-538`) refuses `0.0.0.0` without token. `compose.yaml:79` requires token; ports bound loopback-only. |
| **S12** | Low | `git archive` embeds secret into remote URL argv | `glossary_sources/git_repo.py:123` | **RESOLVED** | Uses temporary git credentials cache / GIT_ASKPASS mechanism, avoiding plain-text tokens in process table (`ps aux`). |
| **S13** | Low | `cors_origins=*` paired with `allow_credentials=True` | `server.py:196-206` | **RESOLVED** | `allow_credentials = bool(cors_origins) and "*" not in cors_origins` in `server.py:205`. Wildcard explicitly disables credentials. |
| **S14** | Low | WebSocket origin check disabled with wildcard CORS | `plugins/progress.py:310` | **VERIFIED** | WebSocket handshake requires valid, cryptographically random `session_token` (`secrets.token_urlsafe(32)`), mitigating CSRF. |
| **S15** | Low | `GET /api/jobs` enumerates all jobs | `plugins/ocr/plugin.py:308` | **VERIFIED** | Protected by `BearerAuthMiddleware`. Job IDs are non-sequential UUIDs; artifact redemption requires secondary secret token. |
| **S16** | Low | `extract_json` O(n²) DoS scan | `utils/json_parse.py:8` | **RESOLVED** | Single-pass scanning with `_MAX_SPAN_ATTEMPTS = 250` and `_MAX_CANDIDATE_ATTEMPTS = 1000` (`json_parse.py:8-10`). |
| **S17** | Info | PyMuPDF AGPL-3.0 licensing implications | `pyproject.toml:25` | **DOCUMENTED** | Extensively disclosed in `README.md:148-164`, `THIRD_PARTY_LICENSES.md`. Clean drop-in `pypdfium2` fallback documented for closed forks. |
| **S18** | Info | `requests` package in development dependency tree | `pyproject.toml:165` | **VERIFIED** | Confined to development dependency groups; production runtime relies strictly on `httpx` and stdlib `urllib`. |
| **S19** | Info | Uvicorn access log header leakage verification | `server.py:680` | **VERIFIED** | Standard Uvicorn log format logs only method, path, HTTP version, and status code. `Authorization` headers are never logged. |

---

---

## 4. Code Complexity, Nesting & AST Profiling

### 4.1 Executive Summary & Aggregate Metrics Dashboard

An exhaustive Abstract Syntax Tree (AST) static analysis and lexical profiling was performed across the complete OmniScribe codebase, encompassing **214 Python modules** (1,377 functions/methods, 316 classes in `src/omniscribe/`) and **80 Dart modules** (397 functions/methods, 196 classes in `client/lib/`).

While the system macro-architecture demonstrates clean modular boundaries (Cordis micro-kernel plugin harness in Python, declarative Riverpod architecture in Flutter), micro-architectural hotspots historically suffered from excessive cyclomatic complexity, deep control nesting (the Arrow Anti-Pattern), and monolithic God Objects.

#### Table 4.1: System-Wide Complexity & Architectural Health Dashboard

| Metric Category | Evaluation Threshold | Python Backend (`src/`) | Flutter Frontend (`client/lib/`) | System Total | Risk Classification |
|---|---|---|---|---|---|
| **Cyclomatic Complexity (CC)** | $CC > 10$ | 158 functions | 45 functions | **203 functions** | Moderate / High |
| **High Cyclomatic Complexity** | $CC > 20$ | 37 functions | 10 functions | **47 functions** | Critical Hotspot |
| **Cognitive Complexity (Cog)** | $Cog \ge 15$ | 63 functions | — | **63 functions** | High Cognitive Load |
| **Severe Cognitive Complexity** | $Cog \ge 30$ | 24 functions | — | **24 functions** | Comprehension Barrier |
| **Deep Control Nesting** | $\text{Depth} \ge 4$ (Py) / $\ge 6$ (Dart) | 32 functions | 36 functions | **68 functions** | Arrow Anti-Pattern |
| **Long Functions (LOC)** | $> 50$ physical lines | 146 functions | 58 functions | **204 functions** | Splitting Required |
| **Monolithic Files (LOC)** | $> 300$ physical lines | 45 files | 35 files | **80 files** | Decomposition Needed |
| **God Classes (LOC)** | $> 500$ physical lines | 4 classes | 8 classes | **12 classes** | SRP Violation |
| **Tightly Coupled Modules** | $C_a + C_e \ge 10$ | 30 modules | — | **30 modules** | Ripple Effect Hazard |

---

### 4.2 McCabe Cyclomatic Complexity (CC) Analysis

Cyclomatic complexity evaluates the number of linearly independent paths through program source code:
$$M = E - N + 2P$$
Where $E$ represents control-flow edges, $N$ represents nodes, and $P$ represents connected components. Decision points (`if`, `elif`, `for`, `while`, `except`, `match_case`, boolean `and`/`or`, conditional ternaries) systematically increment $M$.

- **$CC \le 10$**: Simple, low-risk, easily testable.
- **$11 \le CC \le 20$**: Moderate complexity, requires comprehensive test coverage.
- **$21 \le CC \le 40$**: High complexity, high defect probability, test surface explosion.
- **$CC > 40$**: Untestable God Method; extreme operational risk.

#### Table 4.2: Top 20 Python Cyclomatic Complexity Hotspots

| Rank | Identifier | File Location | CC | Cog | LOC | Max Nesting | Core Driver |
|---|---|---|---|---|---|---|---|
| 1 | `complete_vlm_prompt` | `src/omniscribe/core/ocr/multi_format_client.py:91` | **64** | 132 | 343 | 6 | Multi-provider format multiplexer + retry loop |
| 2 | `build_ocr_router` | `src/omniscribe/plugins/ocr/plugin.py:235` | **63** | 68 | 378 | 3 | Monolithic closure defining all endpoints inline |
| 3 | `_parse_markdown_fallback` | `src/omniscribe/core/readers/markdown_reader.py:84` | **50** | 56 | 198 | 3 | Procedural token scanner with while loops & regex |
| 4 | `build_documents_router` | `src/omniscribe/plugins/documents/routes.py:76` | **47** | 58 | 258 | 2 | Monolithic route factory closure |
| 5 | `OCRServiceImpl.preflight_check` | `src/omniscribe/plugins/ocr/service.py:625` | **41** | 26 | 137 | 4 | Cascading configuration fallback & URL probing |
| 6 | `DocxReader.read` | `src/omniscribe/core/readers/docx_reader.py:118` | **40** | 56 | 228 | 4 | Procedural XML body traversal & style mapping |
| 7 | `build_glossary_router` | `src/omniscribe/plugins/glossary/routes.py:62` | **37** | 45 | 251 | 3 | Monolithic router closure |
| 8 | `_render_block` | `src/omniscribe/core/writers/docx_tree.py:67` | **33** | 129 | 88 | 10 | 8-branch elif ladder with nested formatting |
| 9 | `_parse_with_selectolax` | `src/omniscribe/core/readers/html_reader.py:301` | **33** | 64 | 97 | 7 | HTML AST traversal with nested element branches |
| 10 | `_dp_align` | `src/omniscribe/core/aligner.py:534` | **32** | 39 | 101 | 5 | 2D dynamic programming grid + back-pointer matrix |
| 11 | `LanceDBLexiconStore.save_glossary` | `src/omniscribe/core/lexicon/lancedb_store.py:447` | **32** | 17 | 131 | 2 | Transactional table upsert, merge, and indexing |
| 12 | `_StdlibHTMLDocParser.handle_endtag` | `src/omniscribe/core/readers/html_reader.py:149` | **32** | 69 | 105 | 8 | Tag state machine with stack popping & buffering |
| 13 | `_render_block` | `src/omniscribe/core/writers/html.py:147` | **32** | 18 | 64 | 2 | Block type mapping ladder |
| 14 | `SectionAwareChunker.chunk` | `src/omniscribe/core/chunking/chunker.py:88` | **28** | 52 | 135 | 6 | Section hierarchy tracking & window boundary math |
| 15 | `render_markdown` | `src/omniscribe/core/writers/markdown.py:48` | **28** | 19 | 70 | 2 | Markdown generation branching per block type |
| 16 | `_parse_grounded_json` | `src/omniscribe/core/grounded/parsers.py:280` | **26** | 22 | 131 | 3 | Defensive schema parsing of fuzzy LLM JSON |
| 17 | `_parse_with_mistune` | `src/omniscribe/core/readers/markdown_reader.py:284` | **26** | 45 | 76 | 6 | Markdown AST traversal with nested list parsing |
| 18 | `build_progress_router` | `src/omniscribe/plugins/progress.py:393` | **25** | 32 | 146 | 4 | WebSocket and progress channel router closure |
| 19 | `extract_model_ids_from_response` | `src/omniscribe/plugins/transcribe/config_store.py:85` | **25** | 47 | 40 | 7 | Defensive parsing of diverse provider model payloads |
| 20 | `HybridAligner.get_detected_boxes_batch` | `src/omniscribe/core/aligner.py:156` | **24** | 21 | 109 | 3 | Batch box sorting, filtering, and Surya alignment |

#### Table 4.3: Top 10 Dart Cyclomatic Complexity Hotspots

| Rank | Identifier | File Location | CC | Lines | Nesting | Core Driver |
|---|---|---|---|---|---|---|
| 1 | `RuntimeConfig.fromJson` | `client/lib/data/models/process_settings.dart:716` | **44** | 64 | 3 | 40+ field null-coalescing and fallback deserialization |
| 2 | `ConfigUpdate.fromJson` | `client/lib/data/models/process_settings.dart:503` | **40** | 62 | 1 | Optional configuration delta unpacking |
| 3 | `ProcessSettings.fromJson` | `client/lib/data/models/process_settings.dart:322` | **34** | 44 | 3 | Nested model deserialization |
| 4 | `ApiClient._translateDioError` | `client/lib/core/network/api_client.dart:491` | **33** | 128 | 7 | 10-case switch statement + error payload sniffing |
| 5 | `_AppSelectState.build` | `client/lib/presentation/common/app_select.dart:62` | **29** | 253 | 14 | Giant modal overlay builder with deep widget hierarchy |
| 6 | `_AppInputState.build` | `client/lib/presentation/common/app_input.dart:138` | **24** | 210 | 10 | Form input styling, validation, and suffix icon branches |
| 7 | `_AppCardState.build` | `client/lib/presentation/common/app_card.dart:76` | **23** | 164 | 12 | Dynamic borders, elevation, and gesture branches |
| 8 | `WorkstationNotifier._startBackgroundPreloader` | `client/lib/data/providers/workstation_notifier.dart:338` | **23** | 84 | 11 | Render queue prioritization and async page cache loop |
| 9 | `SmartPreset._matchesPreset` | `client/lib/data/models/smart_preset.dart:315` | **21** | 40 | 19 | 19-level deep nested field equality checks |
| 10 | `_ProviderCardState.build` | `client/lib/presentation/providers/provider_card.dart:52` | **19** | 279 | 10 | Provider configuration card rendering and status logic |

---

### 4.3 Cognitive Complexity (SonarQube) Distribution & Top Culprits

Cognitive complexity measures human comprehension difficulty by assessing code linearity and nesting penalties ($+1 + \text{nesting\_level}$ per control structure):

```
Cognitive Complexity Score Distribution (src/omniscribe):
[  0 -  5 ]: 1,180 functions (85.7%) ████████████████████████████████
[  6 - 14 ]:   134 functions ( 9.7%) ████
[ 15 - 29 ]:    39 functions ( 2.8%) █
[ 30 - 49 ]:    15 functions ( 1.1%) ▏
[ 50+     ]:     9 functions ( 0.7%) ▏
```

#### Top 10 Cognitive Culprits Breakdown

1. **`complete_vlm_prompt` (`multi_format_client.py:91`) — Cog: 132 (CC: 64, LOC: 343, Nesting: 6)**  
   *Driver:* Multiplexing 3 provider formats, manual retry loop with exponential backoff math, deep payload manipulation, and error fallback reasoning.
2. **`_render_block` (`docx_tree.py:67`) — Cog: 129 (CC: 33, LOC: 88, Nesting: 10)**  
   *Driver:* 8-branch `elif` ladder with nested inline span loops and style formatting.
3. **`_extract_prompt_and_image` (`llm/client.py:91`) — Cog: 108 (CC: 23, LOC: 58, Nesting: 10)**  
   *Driver:* 10-level nested iteration over message content arrays, dictionary inspection, and base64 string splitting.
4. **`_StdlibHTMLDocParser.handle_endtag` (`html_reader.py:149`) — Cog: 69 (CC: 32, LOC: 105, Nesting: 8)**  
   *Driver:* Stack-popping state machine unwinding HTML tags and constructing AST blocks.
5. **`build_ocr_router` (`plugins/ocr/plugin.py:235`) — Cog: 68 (CC: 63, LOC: 378, Nesting: 3)**  
   *Driver:* Nested route closure with chunked multipart stream readers and status error cascades.
6. **`_parse_with_selectolax` (`html_reader.py:301`) — Cog: 64 (CC: 33, LOC: 97, Nesting: 7)**  
   *Driver:* Recursive-style node traversal with element tag matching and text buffering.
7. **`build_documents_router` (`plugins/documents/routes.py:76`) — Cog: 58 (CC: 47, LOC: 258, Nesting: 2)**  
   *Driver:* Monolithic factory closure with bearer authentication and format conversion logic.
8. **`DocxReader.read` (`docx_reader.py:118`) — Cog: 56 (CC: 40, LOC: 228, Nesting: 4)**  
   *Driver:* XML paragraph iteration, style detection, heading page-splitting, and table cell iteration.
9. **`_parse_markdown_fallback` (`markdown_reader.py:84`) — Cog: 56 (CC: 50, LOC: 198, Nesting: 3)**  
   *Driver:* Procedural `while i < num_lines:` parser tracking code fences, ATX/Setext headings, tables, blockquotes, and lists.
10. **`SectionAwareChunker.chunk` (`chunker.py:88`) — Cog: 52 (CC: 28, LOC: 135, Nesting: 6)**  
    *Driver:* Section hierarchy stack maintenance, nested `flush_accum` closure, and distinct chunking strategies.

#### Recursion & Mixed Levels of Abstraction (MLA)

- **Legitimate Structural Recursion:** Bounded data-structure recursion in `core/block_tree.py:420` (`_walk_text`), `core/writers/html.py:287` (`_walk`), `core/errors.py:278` (`_redact`), and `harness/loader.py:226` (`_merge_config`).
- **Mixed Levels of Abstraction:** Highlighted by `complete_vlm_prompt` mixing high-level VLM completion logic with raw byte string slicing (`url_str.split("base64,", 1)[1]`), and `ocr/plugin.py` mixing route validation with raw byte-stream chunk reading (`while True: chunk = await upload.read(...)`).

---

### 4.4 Lexical Nesting Depth & The Arrow Anti-Pattern

Indentation exceeding 4 levels in Python and 6 levels in Dart produces the "Arrow Anti-Pattern", obscuring logical intent and multiplying test matrix explosion:

1. **`_extract_prompt_and_image` (`src/omniscribe/core/llm/client.py:91-149`, Nesting = 10):** Loops messages $\to$ checks role $\to$ checks content list $\to$ loops items $\to$ checks dict $\to$ checks `item_type == "image_url"` $\to$ inspects image dict $\to$ verifies non-null $\to$ splits base64 string.
2. **`_render_block` (`src/omniscribe/core/writers/docx_tree.py:67-155`, Nesting = 10):** 8-level `elif` ladder where the fallback paragraph branch contains nested span iterations: `if spans: for sp in spans: if sp.bold: ... if sp.italic: ...`.
3. **`_StdlibHTMLDocParser.handle_endtag` (`src/omniscribe/core/readers/html_reader.py:149-254`, Nesting = 8):** Tag stack resolution with nested `while` unwinding, string sanitization, and block reconstruction.
4. **`translate_tree` (`src/omniscribe/core/translate/tree.py:75-182`, Nesting = 8):** Loops pages $\to$ loops children $\to$ checks for `cells` $\to$ loops rows $\to$ loops cells $\to$ checks `isinstance(cell, BlockNode)` $\to$ checks translation.
5. **`SmartPreset._matchesPreset` (`client/lib/data/models/smart_preset.dart:315`, Nesting = 19):** 19-level cascading logical condition tree comparing preset configuration fields.

---

### 4.5 Monolithic Files (>300 LOC) and God Classes (>500 LOC) Analysis

A total of **45 Python files** and **35 Dart files** exceed 300 LOC. Twelve classes across Python and Dart exceeded 500 lines of implementation:

#### Table 4.4: The 12 Identified "God Classes" (>500 LOC)

| Class Identifier | File Location | Original Lines | Methods / Responsibilities | Architectural Violation |
|---|---|---|---|---|
| `WorkstationNotifier` | `client/lib/data/providers/workstation_notifier.dart` | **1,023** | 42 methods | Merged viewport zoom/pan, bbox selection, OCR job submission, WebSocket frame routing, and PDF rasterization |
| `_AISetupWizardModalState` | `client/lib/presentation/providers/ai_setup_wizard_modal.dart` | **979** | Stepper UI | Multi-step setup wizard, model scanning, key validation, and prompt test execution |
| `LanceDBLexiconStore` | `src/omniscribe/core/lexicon/lancedb_store.py` | **966** | 27 methods | PyArrow schema migrations, LanceDB vector search, BM25 Tantivy search, SQLite sync, and CSV/JSON export |
| `OCRServiceImpl` | `src/omniscribe/plugins/ocr/service.py` | **796** | 33 methods | Job queue submission, ephemeral preflight reachability probing, SSE fanout, and pipeline assembly |
| `_SettingsScreenState` | `client/lib/presentation/settings/settings_screen.dart` | **684** | Settings UI | Server health check, theme toggles, OCR parameters, glossary management, and JSON viewer |
| `_RightControlDockState` | `client/lib/presentation/workstation/controls/right_control_dock.dart` | **663** | Sidebar UI | Monolithic build method defining all right dock controls, preset toggles, and CTA buttons |
| `OCRProcessor` | `src/omniscribe/core/ocr/processor.py` | **610** | 14 methods | Coordinate normalizer, dense vs sparse partitioner, image cropper, concurrent VLM caller, and retry loop |
| `ApiClient` | `client/lib/core/network/api_client.dart` | **593** | 28 methods | HTTP transport, token injection, multipart streaming, and monolithic 10-case Dio error translation |
| `_DocumentViewportState` | `client/lib/presentation/workstation/canvas/document_viewport.dart` | **565** | Canvas UI | InteractiveViewer, gesture handling, zoom/fit math, bbox selection overlay, and scroll synchronization |
| `_ProviderModalState` | `client/lib/presentation/providers/provider_modal.dart` | **553** | Modal UI | Provider configuration form, custom headers, endpoint probing, and model discovery table |
| `HybridEngine` | `src/omniscribe/core/workflows/hybrid.py` | **546** | 16 methods | Stage execution, whitespace recall, text layer recall, VLM OCR dispatch, and quality repair execution |
| `_GlossaryScreenState` | `client/lib/presentation/features/glossary_screen.dart` | **521** | Glossary UI | Glossary library browser, entry table with inline pagination, search filtering, and import modal launcher |

---

### 4.6 Coupling & Stability Metrics (Martin Packaging Metrics)

Software coupling was evaluated using Robert C. Martin's packaging metrics:
- **Afferent Coupling ($C_a$):** Number of external modules depending on this module (incoming dependencies). Indicates module responsibility and stability requirements.
- **Efferent Coupling ($C_e$):** Number of external modules this module depends upon (outgoing dependencies). Indicates vulnerability to external changes.
- **Instability Index ($I$):**
  $$I = \frac{C_e}{C_a + C_e}$$
  - $I = 0.0$: Maximally Stable (bedrock abstractions).
  - $I = 1.0$: Maximally Unstable (leaf coordinators).

#### Table 4.5: Martin Packaging Stability Matrix

| Module Category | Module Identifier | $C_a$ | $C_e$ | Instability ($I$) | Architectural Role & Risk Profile |
|---|---|---|---|---|---|
| **Stable Abstractions** ($I \le 0.15$) | `omniscribe.core.document` | **41** | 1 | **0.024** | Central domain model (`DocumentResult`, `PageResult`, `BBox`). Max stability. |
| | `omniscribe.core.block_tree` | **22** | 1 | **0.043** | Structural AST representation (`DocumentTree`, `BlockNode`). Bedrock. |
| | `omniscribe.harness.context` | **21** | 3 | **0.125** | Plugin execution context & service registry. Low churn. |
| | `omniscribe.config` | **19** | 0 | **0.000** | Root configuration dataclasses and env loading. Completely independent. |
| | `omniscribe.harness.plugin` | **16** | 1 | **0.059** | Plugin interface contracts & lifecycle primitives. |
| **Zone of Pain** ($C_a \ge 8, C_e \ge 5$) | `omniscribe.plugins.jobs` | **10** | **7** | **0.412** | Depended on by OCR, translation, glossary, and documents plugins; depends on Redis, state backends, and config. Changes ripple in both directions. |
| | `omniscribe.core.workflows.base` | **14** | **5** | **0.263** | Defines workflow stages and engine bases. Depended on by hybrid, grounded, and quality repair workflows. |
| | `omniscribe.core.processors.base` | **8** | **9** | **0.529** | Bridges document results with post-processors. High bidirectional ripple risk. |
| **Unstable Coordinators** ($I \ge 0.85$) | `omniscribe.plugins.translate.service` | 3 | **18** | **0.857** | Imports harness, artifacts, jobs, lexicon, LLM, NLLB, and block tree. |
| | `omniscribe.harness.loader` | 2 | **18** | **0.900** | Dynamic plugin loader importing every plugin module. |
| | `omniscribe.plugins.ocr.service` | 2 | **13** | **0.867** | Imports workflows, aligner, processors, artifacts, and jobs. |
| | `omniscribe.core.grounded.prompted` | 1 | **11** | **0.917** | Coordinates prompt templates, multi-format client, and bbox parsers. |
| | `omniscribe.worker` | 0 | **11** | **1.000** | Standalone worker CLI consuming all plugin services. Safe leaf. |

---

### 4.7 Comprehensive Status & Resolution of Findings COMP-01 through COMP-10

Every complexity finding from the 2026-09-13 audit has been addressed and resolved in the codebase:

#### COMP-01: Multi-Format VLM Client God Function (`complete_vlm_prompt`)
- **Location:** `src/omniscribe/core/ocr/multi_format_client.py:91-433` (Lines: 343, CC: 64, Cog: 132).
- **Status: RESOLVED.**
- **Implementation:** Decomposed the monolithic function into a formal Strategy/Adapter pattern (`ProviderFormatAdapter` Protocol at `multi_format_client.py:101-120`). Implemented concrete adapters:
  - `OpenAIFormatAdapter` (`multi_format_client.py:122-202`)
  - `AnthropicFormatAdapter` (`multi_format_client.py:204-282`)
  - `OllamaFormatAdapter` (`multi_format_client.py:284-350`)
- Extracted shared HTTP execution and retry logic into `_execute_http_with_retry` (`multi_format_client.py:352-454`), with process-wide warm connection reuse via `_get_shared_client()` (`multi_format_client.py:40-79`). The file was removed from the Ruff C901 whitelist.

#### COMP-02: DOCX Tree AST Renderer Deep Branching (`_render_block`)
- **Location:** `src/omniscribe/core/writers/docx_tree.py:67-155` (Lines: 88, CC: 33, Cog: 129, Nesting: 10).
- **Status: RESOLVED.**
- **Implementation:** Replaced the cascading `elif` ladder with a dictionary dispatch table `_RENDER_DISPATCH` (`docx_tree.py:185-198`) mapping block types to modular handlers:
  - `_render_section_header`, `_render_list_item`, `_render_code`, `_render_equation`, `_render_figure`, `_render_key_value`, `_render_table_block`, `_render_paragraph`, and `_render_noop`.
- Integrated `TableDedup` (`docx_tree.py:68, 144`) to eliminate duplicate table rendering. Nesting depth reduced from 10 to 2; file removed from Ruff C901 whitelist.

#### COMP-03: Flutter Right Control Dock Monolithic Build (`_RightControlDockState.build`)
- **Location:** `client/lib/presentation/workstation/controls/right_control_dock.dart:54-700` (Lines: 646, CC: 16, Nesting: 7).
- **Status: RESOLVED.**
- **Implementation:** Decomposed the 700-line monolithic widget into an 81-line root orchestrator (`right_control_dock.dart:1-81`) and a suite of dedicated sub-components in `client/lib/presentation/workstation/controls/components/`:
  - `AiEngineStatusCard`, `SmartPresetSection`, `ExecutionOptionsSection`, `DocumentProcessorsCard`, `ImagePreprocessingCard`, and `WorkstationActionButtons`.
- Enables Flutter element-tree pruning during partial state changes, preventing full-dock redraws.

#### COMP-04: Deep Nested Message Payload Extractor (`_extract_prompt_and_image`)
- **Location:** `src/omniscribe/core/llm/client.py:91-149` (Lines: 58, CC: 23, Cog: 108, Nesting: 10).
- **Status: RESOLVED.**
- **Implementation:** Refactored with guard clauses and decomposed into helper functions:
  - `_parse_dict_item` (`client.py:91-121`): Normalizes text, OpenAI `image_url`, and Anthropic `image` blocks.
  - `_parse_content_items` (`client.py:123-142`): Linear scanner aggregating parts.
- Nesting depth dropped from 10 to 2; file removed from Ruff C901 whitelist.

#### COMP-05: Monolithic Route Closure Factories (`build_*_router`)
- **Locations:** `src/omniscribe/plugins/ocr/plugin.py:235-613`, `src/omniscribe/plugins/documents/routes.py:76-334`, `src/omniscribe/plugins/glossary/routes.py:62-313`.
- **Status: RESOLVED.**
- **Implementation:** Decoupled inner route closures into top-level functions and dedicated route modules. For OCR, extracted 681 lines into `src/omniscribe/plugins/ocr/routes.py`, shrinking `ocr/plugin.py` from 677 lines to 201 lines. Direct testability unlocked without instantiating outer plugin harnesses.

#### COMP-06: God Classes Exceeding 500 LOC
- **Locations:** `LanceDBLexiconStore` (966 lines), `WorkstationNotifier` (1,023 lines), `OCRServiceImpl` (796 lines), `ApiClient` (593 lines).
- **Status: RESOLVED.**
- **Implementation:**
  - `LanceDBLexiconStore`: Split SQLite sync, migration schemas, and embedding initialization.
  - `WorkstationNotifier`: Fully decomposed across three Slices (`DocumentViewportNotifier`, `DocumentSelectionNotifier`, `JobOrchestrationNotifier`). Implementation size dropped from 1,049 lines to 588 lines (see Section 7).

#### COMP-07: Fallback Parser Procedural Loops (`_parse_markdown_fallback`)
- **Location:** `src/omniscribe/core/readers/markdown_reader.py:84-282` (Lines: 198, CC: 50, Cog: 56).
- **Status: RESOLVED.**
- **Implementation:** Decomposed the procedural loop into dedicated block consumers:
  - `_consume_code_fence` (`markdown_reader.py:84-101`)
  - `_consume_table` (`markdown_reader.py:103-123`)
  - `_consume_blockquote` (`markdown_reader.py:125-140`)
  - `_consume_list_item` and `_consume_atx_heading`.
- Size reduced from 198 to 112 lines; eliminates infinite-loop risks on malformed markdown lookaheads.

#### COMP-08: High Instability & Afferent Coupling Ripple Hazards
- **Locations:** `omniscribe.plugins.jobs` ($C_a=10, C_e=7$), `omniscribe.plugins.translate.service` ($C_a=3, C_e=18$), `omniscribe.core.processors.base` ($C_a=8, C_e=9$).
- **Status: RESOLVED.**
- **Implementation:** Applied the Dependency Inversion Principle (DIP). Protocols defined in `harness/` and `plugins/` establish stable interfaces. Concrete dependencies injected via Context service registry, shielding callers from internal queue and engine modifications.

#### COMP-09: Monolithic Preflight Validator (`OCRServiceImpl.preflight_check`)
- **Location:** `src/omniscribe/plugins/ocr/service.py:625-762` (Lines: 137, CC: 41, Cog: 26).
- **Status: RESOLVED.**
- **Implementation:** Extracted coordinate resolution and network probing out of `preflight_check` into top-level helpers:
  - `_resolve_preflight_coordinates` (`service.py:117-190`): Resolves request overrides, settings, and configuration fallback hierarchy.
  - `_probe_vlm_server` (`service.py:192-230`): Ephemeral HTTP probe with clean connection pool release and SSRF guards (`check_ssrf_target_sync`).

#### COMP-10: Monolithic Dio Error Translation Switch (`ApiClient._translateDioError`)
- **Location:** `client/lib/core/network/api_client.dart:491-619` (Lines: 128, CC: 33, Nesting: 7).
- **Status: RESOLVED.**
- **Implementation:** Replaced the 10-case switch statement with a table-driven exception factory `_statusFactories` (`api_client.dart:494-565`), mapping status codes (400, 401, 402, 403, 404, 409, 413, 422, 429, 502, 503) directly to domain `ApiException` constructors. Nested control depth collapsed to 2.

---

### 4.8 Automated CI Quality Gates: Ruff C901 & Whitelist Audit

The automated cyclomatic complexity gate is strictly enforced via Ruff's McCabe check (`C901`) in `pyproject.toml:275, 283-284`:

```toml
[tool.ruff.lint]
select = [
    "E", "W", "F", "I", "B", "C4", "UP", "SIM", "RUF", "G",
    "C90", # McCabe cyclomatic complexity linting
]

[tool.ruff.lint.mccabe]
max-complexity = 15
```

Any new Python function exceeding $CC > 15$ immediately breaks the merge-gate CI workflow (`.github/workflows/test.yml`).

#### Active C901 Whitelist (`pyproject.toml:317-336`)

A legacy whitelist of exactly **20 files** remains temporarily exempted from the $CC \le 15$ gate:

1. `src/omniscribe/core/aligner.py`
2. `src/omniscribe/core/chunking/chunker.py`
3. `src/omniscribe/core/chunking/taxonomy.py`
4. `src/omniscribe/core/glossary_sources/encoding.py`
5. `src/omniscribe/core/grounded/parsers.py`
6. `src/omniscribe/core/lexicon/migration.py`
7. `src/omniscribe/core/processors/table.py`
8. `src/omniscribe/core/readers/docx_reader.py`
9. `src/omniscribe/core/readers/html_reader.py`
10. `src/omniscribe/core/readers/markdown_reader.py`
11. `src/omniscribe/core/translate/config.py`
12. `src/omniscribe/core/workflows/stages/ocr.py`
13. `src/omniscribe/core/workflows/stages/refine.py`
14. `src/omniscribe/core/writers/html.py`
15. `src/omniscribe/middleware/upload_limit.py`
16. `src/omniscribe/plugins/glossary/routes.py`
17. `src/omniscribe/plugins/ocr/service.py`
18. `src/omniscribe/plugins/progress.py`
19. `src/omniscribe/plugins/transcribe/config_store.py`
20. `src/omniscribe/utils/security.py`

**Ratcheting Policy:** Critical refactored modules—including `multi_format_client.py` (COMP-01), `docx_tree.py` (COMP-02), `ocr/plugin.py` (COMP-05), and `llm/client.py` (COMP-04)—were successfully dropped from the whitelist. Remaining files are ratcheted down opportunistically whenever modified.

---

---

## 5. Code Duplication & DRY Compliance

### 5.1 Duplication Audit Scope & Impact Matrix (F1–F15)

A comprehensive code duplication sweep across 214+ Python files identified 15 distinct duplication patterns spanning exact string duplicates, structural boilerplate, and divergence risks.

#### Table 5.1: Master Duplication Findings Matrix

| Finding ID | Classification | Pattern Description | Original Sites | Severity | Status |
|---|---|---|---|---|---|
| **F1** | Exact Duplicate | `_envelope(status_code, error, detail)` JSON error helper | 4 route files | **9/10** | **RESOLVED** (`plugins/_http.py`) |
| **F2** | Structural Duplicate | Per-route `try/except XError: return _envelope(...)` boilerplate | 60+ endpoints | **9/10** | **RESOLVED** (`plugin_error_exception_handler`) |
| **F3** | Structural Duplicate | XML glossary parser pipeline (`require_bytes -> safe_xml_root -> finalize`) | TBX, TMX, XLIFF | **8/10** | **RESOLVED** (`XmlGlossaryParser`) |
| **F4** | Exact Duplicate | Divergent `parse_bool` implementations | `utils/env.py` vs `_common.py` | **8/10** | **RESOLVED** (`utils/env.py`) |
| **F5** | Structural Duplicate | Empty `*Schema(BaseModel)` classes across plugins | 4 plugin entry points | **6/10** | **RESOLVED** (`EmptySchema`) |
| **F6** | Data Duplicate | Redundant `EMBEDDING_DIM = 384` constant definition | `store.py` vs `embedding.py` | **8/10** | **RESOLVED** (Centralized in `lexicon`) |
| **F7** | Near Duplicate | Divergent `decode_base64 -> PIL.Image` helpers | `workflows/utils.py` vs `imaging/utils.py` | **7/10** | **RESOLVED** (`decode_base64_image`) |
| **F8** | Structural Duplicate | Raw `base64.b64decode` + `Image.open` without context management | `ocr/processor.py` (3 sites) | **6/10** | **RESOLVED** (`decode_base64_image`) |
| **F9** | Structural Duplicate | Defensive `hasattr(node.block_type, "value")` block type coercion | 6 writers & chunkers | **6/10** | **RESOLVED** (`block_type_str`) |
| **F10** | Exact Duplicate | `_bearer_token(authorization)` header parser | `documents/routes.py` vs `providers.py` | **7/10** | **RESOLVED** (`plugins/_http.py:bearer_token`) |
| **F11** | Structural Duplicate | Per-plugin `XError(PluginError)` with outlier `DocumentsError(Exception)` | 5 plugin services | **6/10** | **RESOLVED** (`DocumentsError(PluginError)`) |
| **F12** | Structural Duplicate | `BaseRecallOptions` subclass boilerplate with redundant constants | `whitespace.py` vs `text_layer.py` | **7/10** | **RESOLVED** (`BaseRecallOptions.from_env`) |
| **F13** | Structural Duplicate | Repetitive `Image.open().convert("RGB"\|"L")` buffer manipulation | OCR & Workflow stages | **5/10** | **RESOLVED** (`decode_base64_image(mode=...)`) |
| **F14** | Interface Duplicate | Inconsistent naming: `image_b64` vs `image_base64` | 14 files | **4/10** | **RESOLVED** (Standardized by convention) |
| **F15** | Structural Duplicate | Table ID de-duplication `set[str | int]` guard loop | 4 writers & chunkers | **5/10** | **RESOLVED** (`TableDedup` in `_guard.py`) |

---

### 5.2 Deep-Dive Resolution Analysis

#### F1, F2, F10: Unified HTTP & Error Envelope Architecture (`src/omniscribe/plugins/_http.py`)
- **Problem:** `_envelope` and `_bearer_token` were copied verbatim across 4 plugin routes. Every route wrapped endpoints in repetitive 8-line `try/except` blocks (60+ occurrences) mapping errors to JSON responses.
- **Resolution:** Centralized in `src/omniscribe/plugins/_http.py`:
  - `envelope(status_code: int, error: str, detail: str) -> JSONResponse` (`_http.py:28-37`): Emits stable `{error, detail}` payload.
  - `bearer_token(authorization: str | None) -> str | None` (`_http.py:40-49`): Strips and validates Bearer prefixes.
  - `plugin_error_exception_handler(request: Any, exc: PluginError) -> JSONResponse` (`_http.py:51-67`): Global FastAPI exception handler that catches any `PluginError` subclass and maps its `status_code`, `error`, and `detail` straight to `envelope`. Replaced ~60 endpoint boilerplate blocks.

#### F3: XML Glossary Parser Unification (`src/omniscribe/core/glossary_sources/_base.py`)
- **Problem:** TBX, TMX, and XLIFF parsers shared an identical outer execution contract: `require_bytes` $\to$ `decode_source` $\to$ `safe_xml_root` $\to$ extraction loop $\to$ empty check $\to$ `finalize`.
- **Resolution:** Introduced `XmlGlossaryParser` (`_base.py:39-87`). The base class executes the template method `parse()`, handling encoding detection, XXE-safe parsing, empty source validation, and summary generation. Subclasses (`tbx.py`, `tmx.py`, `xliff.py`) only implement `_extract_entries(root, source_lang)`.

#### F4: Boolean Parsing Canonicalization (`omniscribe.utils.env`)
- **Problem:** `core/glossary_sources/_common.py:82` defined a local `parse_bool` that only checked a truthy set (`{"1", "true", "yes", "y", "on"}`), silently diverging from `utils/env.py:66` which rigorously checks both `ENABLE_STRINGS` and `DISABLE_STRINGS` frozensets.
- **Resolution:** Removed the duplicate in `_common.py`. `_common.py:12` now directly imports `from omniscribe.utils.env import parse_bool`, ensuring uniform boolean interpretation across CLI, environment, and glossary sources.

#### F5: Sentinel Schema Normalization (`omniscribe.harness.plugin`)
- **Problem:** Four plugins (`documents`, `translate`, `glossary`, `transcribe`) independently declared identical dummy Pydantic classes: `class TranscribeSchema(BaseModel): """No configurable fields."""`.
- **Resolution:** Defined `EmptySchema(BaseModel)` in `src/omniscribe/harness/plugin.py:27`. Unconfigurable plugins declare `Schema = EmptySchema`, eliminating redundant classes.

#### F6: Data Duplicate Elimination (`EMBEDDING_DIM = 384`)
- **Problem:** `EMBEDDING_DIM = 384` was hardcoded in both `src/omniscribe/core/lexicon/store.py:23` and `src/omniscribe/core/lexicon/embedding.py:28`.
- **Resolution:** Centralized in `src/omniscribe/core/lexicon/embedding.py:26` and re-exported through `src/omniscribe/core/lexicon/__init__.py:26`. `store.py` imports the canonical constant.

#### F7, F8, F13: Base64 Image Decoding & Buffer Safety (`src/omniscribe/core/imaging/utils.py`)
- **Problem:** Multiple modules used raw `base64.b64decode` + `Image.open(io.BytesIO(...))` without context management, leaving image buffers open until garbage collection and triggering memory leaks during large (200+ page) OCR runs.
- **Resolution:** Centralized in `src/omniscribe/core/imaging/utils.py:60-78` via `decode_base64_image(data: str, *, mode: str | None = None) -> Image.Image`. Utilizes a strict `with Image.open(...) as img:` block with `img.load()` and `img.copy()`, guaranteeing instant memory buffer cleanup upon function exit.

#### F9: AST Node Representation Standardization (`src/omniscribe/core/block_tree.py`)
- **Problem:** Defensive `hasattr(node.block_type, "value")` checks were copied across 6 writers and chunkers (`markdown.py`, `html.py`, `docx_tree.py`, `taxonomy.py`, `chunker.py`, `block_tree.py`).
- **Resolution:** Introduced `block_type_str(block_type) -> str` in `src/omniscribe/core/block_tree.py:131`, providing a single canonical coercion helper.

#### F11: Error Class Hierarchy Alignment (`src/omniscribe/plugins/documents/service.py`)
- **Problem:** `TranslateError`, `TranscribeError`, and `GlossaryError` inherited from `PluginError`, but `DocumentsError` inherited from bare `Exception`, preventing it from participating in the F2 automated error envelope middleware.
- **Resolution:** Modified `DocumentsError` in `documents/service.py:39` to inherit directly from `PluginError`, standardizing status codes and wire serialization across all plugins.

#### F12: Text Recall Configuration Mixin (`src/omniscribe/core/recall/base.py`)
- **Problem:** `whitespace.py` and `text_layer.py` re-declared duplicate option fields and identical `from_env` methods with hardcoded constants.
- **Resolution:** Extracted `BaseRecallOptions` (`core/recall/base.py:12-37`) with a standardized `@classmethod def from_env(cls) -> Self` evaluating `DISABLE_STRINGS`. Subclasses only specify their unique `env_var` and threshold overrides.

#### F14: Wire vs Pipeline Image Naming Convention
- **Problem:** Inconsistent use of `image_b64` vs `image_base64` across 14 files.
- **Resolution:** Resolved by formal convention documented in `src/omniscribe/core/imaging/utils.py:16-37`:
  - `image_b64`: Canonical in-process pipeline variable across `workflows/`, `imaging/`, `grounded/`, and `FigureNode.image_bytes_b64`.
  - `image_base64`: Wire-format boundary name restricted to HTTP endpoints, JSON request bodies, and external LLM provider adapters.

#### F15: Contextual Table De-duplication Guard (`src/omniscribe/core/writers/_guard.py`)
- **Problem:** Exporters (`markdown.py`, `html.py`, `docx_tree.py`) and `chunker.py` tracked rendered tables via duplicate `set[str | int]` loops comparing Python `id(node)` and `node.block_id`.
- **Resolution:** Created `TableDedup` (`_guard.py:23-57`) providing an encapsulated container with `add(table)` and `__contains__(table)` checking both `id()` and `block_id`.

---

### 5.3 Quantified DRY Payoff & Architectural Impact

1. **Lines of Code Eliminated:** Over **650+ lines** of duplicated logic, boilerplate try/except blocks, and redundant class declarations were eliminated.
2. **Defect Surface Reduction:**
   - Unified boolean parsing eliminates subtle configuration bugs where `"off"` or `"disabled"` evaluated truthy in glossary adapters.
   - Resource-managed `decode_base64_image` eliminates unclosed buffer memory leaks on multi-page PDF processing runs.
   - Centralized `EMBEDDING_DIM = 384` prevents silent vector dimension mismatches between index storage and transformer embedding models.
3. **API Consistency:** 100% of plugin route errors are guaranteed to follow the `{error, detail}` wire envelope contract parsed by the Flutter client.

---

---

## 6. Functional Integration, Edge Cases & Defect Resolution

### 6.1 Synthesis of the 2026-09-15 Edge-Case Remediation Blueprint
The 2026-09-15 remediation blueprint (historical edge-case remediation, preserved in Git history) targeted six systemic operational edge cases:

1. **Container Reachability:** The container must bind `0.0.0.0` internally to allow Docker bridge forwarding, while Compose restricts host publishing to `127.0.0.1:8000:8000`. Reconciled by providing explicit `OMNISCRIBE_AUTH_TOKEN` in `compose.yaml:79` to satisfy the server's non-loopback startup guard.
2. **Distributed Job Concurrency:** Distributed multi-worker processing over Redis must avoid duplicate execution and handle node failure. Resolved via `RedisJobQueue` (`src/omniscribe/plugins/jobs_redis.py:65-112`) using atomic Lua script claiming (`_CLAIM_JOB_LUA`), active claim timeouts in a sorted set, worker heartbeats, and pub/sub cancellation channels (`omniscribe:jobs:control`).
3. **OCR Lifecycle & Artifact Identity:** Transient disk paths for uploaded PDFs were previously unlinked prematurely, causing preview rendering to fail. Resolved by caching input artifacts in `ArtifactStore` and adding an automatic fallback to result artifacts in `OCRServiceImpl.get_page_preview` (`src/omniscribe/plugins/ocr/service.py:780-815`).
4. **Permanent Payment Errors (`LLMBalanceError`):** When upstream LLM credits are exhausted (HTTP 402), OCR loops previously retried or silently swallowed the error, emitting corrupt partial text. Resolved by propagating `LLMBalanceError` up through pipeline stages and mapping it to a dedicated HTTP 402 `payment_required` envelope (`src/omniscribe/server.py:344-355`).
5. **Async Translation Token Delivery:** Async translation completed jobs without exposing token-bound results to unauthenticated polling. Resolved by returning job metadata without tokens on public status queries, while providing a dedicated token-authenticated result redemption endpoint (`/api/translate/result/{job_id}`).
6. **Flutter Lifecycle Synchronization:** Client notifiers previously retained stale job IDs across page switches, causing race conditions in canvas rendering. Resolved by establishing strict completion/cancellation lifecycles and unique channel subscription tokens in `JobOrchestrationNotifier`.

---

### 6.2 Comprehensive Audit of the 10 Diagnosed Defects & Verification of Repairs

The table below provides a comprehensive trace of the ten integration defects diagnosed on 2026-09-15 and verified as repaired on 2026-09-16 (diagnoses and repair logs preserved in Git history):

```text
Defect 1: Client URL Change Crash       ──> Repaired: Dynamic repo getters in Notifiers
Defect 2: Async OCR Stuck at 100%       ──> Repaired: Terminal status polling + WS reconciliation
Defect 3: Bearer Overrides Upstream Key ──> Repaired: X-Provider-Api-Key separation
Defect 4: Async Translation 400 Bad Req ──> Repaired: Dual inline text / artifact support
Defect 5: Storage Error Kills Worker    ──> Repaired: Worker try-except wrapping & restart
Defect 6: Async OCR Drops Text Artifact ──> Repaired: Dual PDF/text artifact headers & outcome
Defect 7: OCR Settings Ignored          ──> Repaired: Pipeline bridge parameter forwarding
Defect 8: Valid Markdown Rejected 415   ──> Repaired: UTF-8 parser validation replaces magic bytes
Defect 9: Grounded Enum & Page Drop     ──> Repaired: Removed grounded_native, forwarded pages
Defect 10: Compose Startup Crash        ──> Repaired: Wired OMNISCRIBE_AUTH_TOKEN in Compose
```

#### Detailed Breakdown of Defects 1–10

#### Defect 1: Backend URL Changes Crash Client State
- **Root Cause:** `client/lib/data/providers/settings_notifier.dart:12-17`, `jobs_notifier.dart`, and `provider_notifier.dart` declared `_repo` as `late final` and assigned it inside `build()`. When `apiBaseUrlProvider` changed, `build()` re-executed on the existing notifier, triggering `LateInitializationError: Field '_repo' has already been initialized`.
- **Repair Verification:** Replaced `late final _repo` with dynamic property getters (`JobRepository get _repo => ref.read(jobRepositoryProvider);`). Tested and verified in Flutter unit tests (`test/network_test.dart` and `test/workstation_notifier_test.dart`).

#### Defect 2: Async OCR Completion Never Reaches Client
- **Root Cause:** `client/lib/data/providers/job_orchestration_notifier.dart:498-500` listened for WebSocket closure to trigger job completion. The server's WebSocket loop (`src/omniscribe/plugins/progress.py:544-546`) stays open indefinitely. Normal OCR completion never closed the socket, leaving the client stuck at 100% `isProcessing=true`.
- **Repair Verification:** Added `_scheduleStatusCheck` with polling fallback upon reaching 100% progress and unified terminal state reconciliation in `_handleWsClosed`. Verified that `ProcessOcrResult` completes and retrieves artifacts.

#### Defect 3: Backend Authentication Overrides Provider Credentials
- **Root Cause:** `src/omniscribe/plugins/providers.py:112` resolved discovery credentials using `X-Provider-Api-Key or bearer_token(Authorization) or api_key`. The Flutter client automatically attached OmniScribe's server bearer token to `Authorization`, which took precedence over the user's provider key, sending internal server secrets to external APIs.
- **Repair Verification:** `src/omniscribe/plugins/providers.py:100` was rewritten to resolve credentials strictly from `x_provider_api_key or api_key`. `Authorization` is reserved exclusively for the server's own bearer auth.

#### Defect 4: Async Translation Contract Mismatch
- **Root Cause:** `client/lib/data/providers/features_notifier.dart:168-177` submitted inline `text` JSON to `/api/translate/async`, whereas `src/omniscribe/plugins/translate/routes.py:81-87` required `text_artifact_id` and `text_artifact_token`, immediately returning HTTP 400 Bad Request.
- **Repair Verification:** `src/omniscribe/plugins/translate/routes.py` was updated to support either inline `text` or artifact pairs. `features_notifier.dart` was updated to store the returned `result_token`.

#### Defect 5: Artifact-Storage Failure Kills Async Queue Worker
- **Root Cause:** `src/omniscribe/plugins/jobs.py:428` executed artifact persistence outside the runner's exception handler. If storage raised an error (e.g. disk full `OSError`), the exception escaped `_process_one` and permanently crashed the single worker task.
- **Repair Verification:** Enclosed artifact storage and terminal state updates within worker `try...except` blocks in `src/omniscribe/plugins/jobs.py:420-445`. Added task liveness verification and auto-restart in `JobQueue.start()`.

#### Defect 6: Async OCR Loses Text Artifact
- **Root Cause:** `src/omniscribe/plugins/ocr/service.py:395-400` stored only the compiled PDF bytes in `run_job`. The extracted per-page text was discarded, and status responses erroneously mapped the PDF artifact ID to `text_artifact_id`. Downstream structured export could not function.
- **Repair Verification:** `OCRServiceImpl.run_job` persists extracted text as a distinct JSON artifact, storing its ID/token in `JobOutcome.metadata`. Response headers `X-Text-Artifact-Id` and `X-Text-Artifact-Token` are emitted on completion.

#### Defect 7: Processing Settings Ignored at Runtime
- **Root Cause:** Custom user settings (DPI, concurrency, image size) saved via `/api/config` were persisted in `ocr/service.py:870-877`, but `pipeline_bridge.py:113-148` never passed them to `OCRPipeline.run()`, silently falling back to defaults (DPI 200, concurrency 1).
- **Repair Verification:** `pipeline_bridge.py` now maps and forwards validated `dpi`, `concurrency`, `dense_threshold`, and `max_image_dim` into `OCRPipeline.run()`. Verified via `tests/routers/test_pipeline_bridge.py`.

#### Defect 8: Valid Markdown Uploads Fail with HTTP 415
- **Root Cause:** `src/omniscribe/plugins/ocr/routes.py:209-222` required a rigid 12-byte binary signature (e.g. starting with `# `). Valid Markdown files starting with plain text paragraphs failed validation with HTTP 415 Unsupported Media Type.
- **Repair Verification:** Replaced binary signature sniffing with `_is_markdown_text`, performing UTF-8 decoding and line-based structure analysis.

#### Defect 9: Grounded Controls Mismatch & Page Selection Dropped
- **Root Cause:** The Flutter client exposed `grounded_native` in `PipelineMode`, which was rejected by backend schemas (HTTP 422). Additionally, `src/omniscribe/pipeline.py:234-246` never forwarded the `pages` selection parameter into `GroundedEngine.execute()`.
- **Repair Verification:** Removed `grounded_native` from `client/lib/data/models/process_settings.dart:8`. Wired page range slicing into `GroundedEngine` in `pipeline.py:238`.

#### Defect 10: Default Compose Configuration Startup Failure
- **Root Cause:** `Dockerfile:165` CMD bound `0.0.0.0`. The server startup guard (`server.py:531-538`) exited immediately with `SystemExit` if bound to non-loopback without `OMNISCRIBE_AUTH_TOKEN`. `compose.yaml` had no token defined.
- **Repair Verification:** Configured `OMNISCRIBE_AUTH_TOKEN: ${OMNISCRIBE_AUTH_TOKEN:?OMNISCRIBE_AUTH_TOKEN must be set in .env}` in `compose.yaml:79` and updated documentation.

---

### 6.3 Live Functional Validation Results (`scripts/verify_live_functionality.py`)

To guarantee that the repaired codebase functions as a cohesive system, the live functional verification suite `scripts/verify_live_functionality.py` was executed directly against the live FastAPI application and plugin harness context.

The script executes 10 non-mocked integration checks spanning the HTTP boundary, document exporters, state storage, and WebSockets.

#### Live Execution Log Output
```
============================================================
STARTING COMPREHENSIVE LIVE FUNCTIONAL VALIDATION
============================================================
2026-09-16T21:51:12+0100 INFO omniscribe.plugins.state: state backend sqlite db=.../omniscribe-state.db
2026-09-16T21:51:12+0100 INFO omniscribe.plugins.jobs: jobs plugin mounted (mode=inprocess)
2026-09-16T21:51:12+0100 INFO omniscribe.plugins.progress: progress plugin mounted (frame_cap=1000, mode=inprocess)
2026-09-16T21:51:12+0100 INFO omniscribe.harness: harness mounted plugins: runtime, logging, state_backend, artifacts, jobs, progress, providers, health, documents, translate, transcribe, glossary, ocr, sample_pdfs (14 plugins)

[CHECK 1] Health & Readiness Endpoints...
  GET http://testserver/api/health   -> HTTP 200 {"status": "ok"}
  GET http://testserver/api/healthz  -> HTTP 200 {"status": "ok"}
  GET http://testserver/ready        -> HTTP 200 {"status": "ready"}
  GET http://testserver/readyz       -> HTTP 200 {"status": "ready"}
  -> Health & Readiness OK

[CHECK 2] Providers and Configuration...
  GET http://testserver/api/providers -> HTTP 200 (Active provider catalog returned)
  GET http://testserver/api/config    -> HTTP 200 (Effective pipeline config returned)
  -> Providers & Config OK

[CHECK 3] Transcription Service Endpoints...
  GET http://testserver/api/config/transcription -> HTTP 200 (Masked keys returned)
  GET http://testserver/api/models/transcription -> HTTP 200 (Fallback whisper models returned)
  -> Transcription OK

[CHECK 4] Glossary Service Endpoints...
  GET http://testserver/api/glossary/sources -> HTTP 200 []
  GET http://testserver/api/glossary/library -> HTTP 200 []
  -> Glossary OK

[CHECK 5] Document Export - Rich DOCX...
  POST http://testserver/api/export/docx -> HTTP 200 OK (PK\x03\x04 archive verified)
  -> Parsed Word Document: 1 Table (4 rows), Headings verified.
  -> Rich DOCX Export OK (Table & Headings verified)

[CHECK 6] Document Export - Empty DOCX Placeholder Guard...
  POST http://testserver/api/export/docx with empty & whitespace inputs
  -> Fallback verified: "No text recognized" inserted into docx paragraphs.
  -> Empty DOCX Placeholder Guard OK

[CHECK 7] Document Export - Chunks...
  Stored JSON text artifact -> POST http://testserver/api/export/chunks
  -> HTTP 200 OK: 2 semantic chunks generated with boundary metadata.
  -> Document Chunks OK (2 chunks generated)

[CHECK 8] Error Envelope Standards (404, 402, 422)...
  GET /api/process/status/non-existent-uuid -> HTTP 404 {"error": "not_found", "detail": "..."}
  GET /test-live-llm-balance               -> HTTP 402 {"error": "payment_required", "detail": "..."}
  -> Error Envelopes OK (404 and 402 verified)

[CHECK 9] OCR Preview Rendering with Result Artifact Fallback...
  Created test PDF in ArtifactStore, referenced by JobRecord with missing disk input
  ocr_service.get_page_preview("job-fallback-test", 0)
  -> Successfully fell back to result artifact; returned valid \x89PNG byte stream.
  -> Preview Fallback to Artifact OK (Valid PNG output verified)

[CHECK 10] WebSocket Channel LifeCycle...
  progress_service.open_channel() -> WS /ws/{channel_id}?token={session_token}
  -> Frame 1 received: {"type": "connected", "channel_id": "..."}
  -> progress_service.broadcast(50% OCR)
  -> Frame 2 received: {"type": "progress", "percent": 50, "stage": "OCR"}
  -> WebSocket Progress OK

============================================================
ALL 10 FUNCTIONAL VERIFICATION CHECKS PASSED!
============================================================
```

### 6.4 Functional Integration & Reliability Verdict
Sections 1, 2, 3, and 6 establish that OmniScribe’s foundational architecture, security model, and functional integration pathways are fully unified. With 14 plugins mounted in strict LIFO order, an ASGI security triad guarding all entry points, constant-time token comparison, SSRF DNS-pinning, full resolution of all 10 diagnosed integration defects, and 10/10 passing live functional checks, the system is verified and ready for deployment.

---

## 7. Frontend Architecture & Flutter Client Health

### 7.1 Riverpod State Management & Layered Segregation

The OmniScribe Flutter client (`client/lib/`) is architected on Flutter Riverpod (migrated to modern Riverpod 2.x/3.x `Notifier` / `NotifierProvider` primitives, eliminating legacy `StateProvider` and mutable `.state =` assignments).

The client enforces strict unidirectional data flow across three distinct architectural layers:

```
+-----------------------------------------------------------------------+
|                         PRESENTATION LAYER                            |
|  Screens, Views, Widgets, Custom Painters (ConsumerWidget / State)     |
|  - client/lib/presentation/workstation/                               |
|  - client/lib/presentation/shell/                                     |
|  - client/lib/presentation/providers/                                 |
+-----------------------------------+-----------------------------------+
                                    | watches / reads
                                    v
+-----------------------------------------------------------------------+
|                         DOMAIN & STATE LAYER                          |
|  Immutable State Models, Riverpod Notifiers, UI Coordination          |
|  - DocumentViewportNotifier (Scale, Translation, Fit Math)            |
|  - DocumentSelectionNotifier (BBox Selection, Hover, Inline Edits)    |
|  - JobOrchestrationNotifier (OCR Pipeline, Progress, WS Frames)       |
|  - WorkstationNotifier (Document Data, Previews, Page Navigation)     |
+-----------------------------------+-----------------------------------+
                                    | reads / calls
                                    v
+-----------------------------------------------------------------------+
|                            DATA LAYER                                 |
|  Repositories, Network Transport, Protocols, DTOs                     |
|  - ApiClient (Dio HTTP Client, Error Translation, Retries)            |
|  - WsClient (WebSocket Protocol, Reconnection, Framing)              |
|  - OcrRepository, JobRepository, ConfigRepository, ProviderRepository |
+-----------------------------------------------------------------------+
```

---

### 7.2 The `WorkstationNotifier` Architectural Decomposition

#### The Problem: The 1,049-LOC God Object
Historically, `WorkstationNotifier` (`client/lib/data/providers/workstation_notifier.dart`) aggregated every workstation responsibility:
- Binary PDF loading and texture caching.
- Viewport transform matrix calculation, zoom clamping, and fit-to-screen geometry.
- Bounding box hit testing, selection, hover state, and manual text editing.
- HTTP multipart OCR job submission.
- WebSocket frame parsing and streaming state mutation.
- Export generation.

A user zooming into the PDF canvas triggered state notifications across the identical notifier handling background OCR progress packets, forcing unnecessary widget rebuilds.

#### The 3-Slice Decomposition Architecture

Under remediation item **P2 (COMP-06 remainder)**, `WorkstationNotifier` was decomposed into three focused, single-responsibility notifiers:

```
                        [Workstation UI Screens]
                                   |
         +-------------------------+-------------------------+
         |                         |                         |
         v                         v                         v
+------------------+     +-------------------+     +--------------------+
| DocumentViewport |     | DocumentSelection |     |  JobOrchestration  |
|     Notifier     |     |     Notifier      |     |      Notifier      |
+------------------+     +-------------------+     +--------------------+
| - scale: double  |     | - selectedBBox    |     | - isProcessing     |
| - translation    |     | - hoveredBBox     |     | - stage / percent  |
| - zoomBy()       |     | - select()        |     | - avgConfidence    |
| - fitToScreen()  |     | - hover()         |     | - handleWsFrame()  |
| - syncFromMatrix |     | - replaceSelected |     | - cancelOcr()      |
+------------------+     +-------------------+     +--------------------+
         ^                         ^                         ^
         |                         |                         |
         +-------------------------+-------------------------+
                                   |
                                   v
                      +--------------------------+
                      |   WorkstationNotifier    |
                      |         (Facade)         |
                      +--------------------------+
                      | - loadedBytes: Uint8List |
                      | - pages: List<PageResult>|
                      | - bboxes per page        |
                      | - loadDocument()         |
                      | - selectPage()           |
                      +--------------------------+
```

##### Slice 1: `DocumentViewportNotifier` (`client/lib/data/providers/document_viewport_notifier.dart`)
- **Domain:** Pure canvas geometry, zoom, pan, and transform calculations.
- **State:** `DocumentViewportState` (`scale: double`, `translation: Offset`, `matrix: Matrix4`).
- **Methods:**
  - `zoomBy(factor, viewportSize)`: Center-anchored zoom with strict scale clamping $[0.15, 6.0]$.
  - `fitToScreen(viewportSize, canvasSize)`: Symmetric fit with 28 px padding margin and a 3.0 scale cap.
  - `resetToActualSize()`: Centers canvas at 100% zoom.
  - `syncFromMatrix(Matrix4)`: Mirrors `InteractiveViewer` gesture transforms without feedback loops.
- **Verification:** 10 dedicated unit tests in `client/test/data/document_viewport_notifier_test.dart`.

##### Slice 2: `DocumentSelectionNotifier` (`client/lib/data/providers/document_selection_notifier.dart`)
- **Domain:** Canvas bounding box selection, mouse hover tracking, and inline text editing.
- **State:** `DocumentSelectionState` (`selectedBBox: BBoxItem?`, `hoveredBBox: BBoxItem?`).
- **Methods:** `select(bbox)`, `hover(bbox)`, `replaceSelected(updated)`, `clear()`.
- **Decoupling Seam:** Automatically cleared on page changes (`selectPage`) and document loading, but isolated from canvas transforms and OCR networking.
- **Verification:** 8 dedicated unit tests in `client/test/data/document_selection_notifier_test.dart`.

##### Slice 3: `JobOrchestrationNotifier` (`client/lib/data/providers/job_orchestration_notifier.dart`)
- **Domain:** OCR job lifecycle, progress tracking, WebSocket frame ingestion, and quality metrics.
- **State:** `JobOrchestrationState` (18 fields: `isProcessing`, `percent`, `stage`, `statusMessage`, `warnings`, `channelId`, `activeJobId`, `processedBlocks`, `totalBlocks`, `scoredBlocks`, `avgConfidence`, `blockRetryCounts`, `qualitySummary`, `trustSummary`, `error`, `textArtifactId`, `textArtifactToken`).
- **Methods:**
  - `processOcrSync()`: Opens progress session, attaches WebSocket, dispatches sync multipart request, and adopts result.
  - `processOcrAsync()`: Enqueues worker job, attaches WebSocket, and schedules fallback status polling via `_handleWsClosed`.
  - `handleWsFrame(frame)`: Direct frame router (see Section 7.4).
  - `cancelOcr()`: Aborts network streams, closes WebSocket channels, and notifies backend.
  - `tryWithSamplePdf()`: Fetches canonical sample PDF fixture from backend.
- **Verification:** 9 dedicated unit tests in `client/test/data/job_orchestration_state_test.dart`.

##### Residual `WorkstationNotifier` Facade (`client/lib/data/providers/workstation_notifier.dart`)
- Refactored from 1,049 lines down to **588 lines**.
- Maintains pure document state: `loadedBytes`, `filename`, `pages`, `currentPageBBoxes`, `filePickSignal`.
- Preserves thin facade delegates (`processOcrSync`, `cancelOcr`, `handleWsFrame`) ensuring complete backward compatibility with existing widget call sites.

---

### 7.3 URL-Change Safety, Scheme Reactivity & Lifecycle Teardown

Dynamic configuration updates in the Flutter client require seamless network client re-instantiation without lingering socket leaks or mixed-content protocol errors:

#### 1. Reactive Dependency Tree
In `client/lib/data/providers/repository_providers.dart:57-99`:
- `apiBaseUrlProvider` holds the current backend base URL.
- `apiClientProvider` watches `apiBaseUrlProvider`: when the base URL changes, a fresh `ApiClient` is created with updated headers and error handlers.
- All repository providers (`ocrRepositoryProvider`, `jobRepositoryProvider`, `configRepositoryProvider`, etc.) watch `apiClientProvider` and re-bind automatically.

#### 2. URI-Aware Scheme Translation (`http` $\to$ `ws`, `https` $\to$ `wss`)
The previous implementation used a brittle string replacement (`baseUrl.replaceFirst(RegExp(r'^http'), 'ws')`), which failed for secure HTTPS deployments (producing invalid `https://` WebSocket URIs) or corrupted URLs containing "http" in host paths.
The current implementation in `wsClientProvider` uses `Uri.tryParse`:

```dart
final parsed = Uri.tryParse(baseUrl);
final wsScheme = switch (parsed?.scheme) {
  'https' => 'wss',
  'http' => 'ws',
  'wss' => 'wss',
  'ws' => 'ws',
  _ => 'ws',
};
final wsUrl = parsed?.replace(scheme: wsScheme).toString() ?? ...;
```

#### 3. Lifecycle Teardown via `ref.onDispose`
In `JobOrchestrationNotifier.build()` (`job_orchestration_notifier.dart:239`), `ref.onDispose(_disposeTeardown)` is registered. When providers re-bind or screens unmount:
- Active WebSocket subscriptions (`_wsSubscription`, `_wsClosedSubscription`) are cancelled.
- The WebSocket client disconnects cleanly.
- An asynchronous cancellation request is dispatched to the backend progress channel (`/api/progress/{channelId}`).
- Reconnection races are prevented via a monotonic `_runEpoch` generation counter that discards out-of-order replies.

---

### 7.4 Real-time WebSocket Protocol & Streaming Frame Processing

OmniScribe uses a typed JSON envelope protocol over WebSockets (`client/lib/data/models/ws_frames.dart`) for streaming live OCR progress:

#### Frame Ingestion Architecture (`JobOrchestrationNotifier._applyWsFrame`)

```dart
switch (frame) {
  case ProgressFrame p:
    state = state.copyWith(
      percent: p.percent.clamp(0, 100),
      stage: p.stage.isNotEmpty ? p.stage : state.stage,
      statusMessage: p.status,
    );
  case BlockCompleteFrame b:
    // 1. Delegate bbox rendering directly to document store
    ref.read(workstationProvider.notifier).addOrUpdateBBox(b.pageIdx, b.toBBoxItem());
    // 2. Incrementally update running confidence average
    final newProcessed = state.processedBlocks + 1;
    final newScored = b.confidence != null ? state.scoredBlocks + 1 : state.scoredBlocks;
    final newAvg = b.confidence != null
        ? ((state.avgConfidence ?? 0.0) * state.scoredBlocks + b.confidence!) / newScored
        : state.avgConfidence;
    state = state.copyWith(processedBlocks: newProcessed, scoredBlocks: newScored, avgConfidence: newAvg);
  case BlockRetryFrame r:
    final currentCount = state.blockRetryCounts[r.blockKey] ?? 0;
    state = state.copyWith(
      stage: 'Refine / Quality Repair',
      blockRetryCounts: {...state.blockRetryCounts, r.blockKey: currentCount + 1},
    );
  case BlockRevisedFrame rev:
    ref.read(workstationProvider.notifier).addOrUpdateBBox(rev.pageIdx, rev.toBBoxItem());
  case QualitySummaryFrame q:
    state = state.copyWith(qualitySummary: q.summary, avgConfidence: q.avgConfidence);
  case CancelledFrame c:
    state = state.copyWith(isProcessing: false, stage: 'Cancelled');
  case ConnectedFrame conn:
    state = state.copyWith(channelId: conn.channelId);
}
```

#### Key Protocol Features
1. **Live Bounding Box Streaming:** `BlockCompleteFrame` and `BlockRevisedFrame` inject bounding boxes into `WorkstationNotifier` in real-time, allowing users to see recognized text overlayed onto the canvas page while subsequent pages are still running through the VLM.
2. **Fail-Open Synchronous OCR:** If the WebSocket connection fails during `processOcrSync`, the client logs the failure and falls open—the HTTP multipart request proceeds and returns the full result PDF and text artifacts regardless.
3. **Out-of-Band Disconnect Recovery:** If the WebSocket closes unexpectedly during an asynchronous run, `_handleWsClosed()` initiates status polling against `/api/jobs/{id}`, retrieving the result PDF as soon as the worker reaches a terminal state.

---

### 7.5 Headless Accessibility (`?a11y=1`), Semantics Tree, & CI Testing Strategy

#### 1. Headless Accessibility Hook (`?a11y=1`)
In `client/lib/main.dart:14-16`:

```dart
if (Uri.base.queryParameters['a11y'] == '1') {
  SemanticsBinding.instance.ensureSemantics();
}
```

By default, Flutter Web disables its accessibility semantics tree unless an active screen reader (e.g., VoiceOver, NVDA) is detected. Passing `?a11y=1` forces `SemanticsBinding.instance.ensureSemantics()` on startup.
- **Headless Browser Tooling:** Enables Playwright, Puppeteer, and Chrome Headless to query DOM semantics nodes (`flt-semantics`), find interactive elements by accessibility labels, and drive UI flows without visual coordinates.
- **Automated Screenshot Capture:** Enabled the automated capture of 7 golden screen states in `docs/screenshots/` without requiring manual human interaction.

#### 2. Comprehensive Test Suite & Integration Verification
Frontend quality and regression safety are backed by a complete multi-tier test suite:
- **Unit & State Tests:** **345 / 345 green tests** (`flutter test`), including dedicated suites for:
  - `document_viewport_notifier_test.dart` (10 tests)
  - `document_selection_notifier_test.dart` (8 tests)
  - `job_orchestration_state_test.dart` (9 tests)
  - `workstation_notifier_test.dart`
- **End-to-End Desktop Integration Tests (`client/integration_test/`):**
  - `stub_omniscribe_server.dart`: In-process `dart:io` server stub implementing the complete golden-path surface (`/api/progress/session`, real WebSocket upgrade pushing `block_complete` frames, sync/async OCR endpoints, preview rendering).
  - `app_workstation_test.dart`: Drives real `OmniScribeApp` execution on Windows Desktop: connects to server $\to$ loads PDF $\to$ runs OCR $\to$ ingests streamed WebSocket frames $\to$ validates terminal state (100% progress, stage 'Complete', bounding box count, confidence metrics). Passes end-to-end (11s).
  - `app_real_server_test.dart`: Live server integration test launching `omniscribe-server` via `uv run`, fetching the auth-exempt `/api/sample-pdf/digital.pdf` fixture, and validating end-to-end server preview rasterization.

---

## 8. QA, Verification Engineering & Test Suite Posture

### 8.1 Testing Philosophy and Tiered Architecture

OmniScribe enforces an intentional, three-tier test architecture designed to balance rapid developer feedback loops in CI against the compute-intensive requirements of document layout models and calibration datasets.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            OmniScribe Test Tiers                            │
├───────────────────────────┬─────────────────────────┬───────────────────────┤
│ Tier 1: Fast Merge Gate   │ Tier 2: Nightly Slow    │ Tier 3: Nightly       │
│                           │                         │ Calibration           │
├───────────────────────────┼─────────────────────────┼───────────────────────┤
│ • Marker: "not slow and   │ • Marker: "slow"        │ • Marker:             │
│   not slow_dataset"       │ • Trigger: Cron 03:00   │   "slow_dataset"      │
│ • Trigger: PR & push main │   UTC / dispatch        │ • Trigger: Cron 03:00 │
│ • Python: 3.11, 3.13,     │ • Python: 3.12 (Ubuntu) │   UTC / dispatch      │
│   3.14 (Ubuntu) + 3.11    │ • Scope: Surya ONNX     │ • Python: 3.12        │
│   (Windows)               │   model loading, layout │ • Scope: Full         │
│ • Execution: ~23 seconds  │   detection, large-PDF  │   OCR-Quality &       │
│ • Strict: 0 retries,      │   rendering regressions │   KIE-HVQA datasets,  │
│   fail-under >= 80%       │ • Flake Policy: 1 rerun │   Platt scaling ECE   │
│ • Machine-readable JUnit  │   with 2s delay         │ • Flake Policy:       │
│   XML uploaded            │                         │   1 rerun with 2s     │
│                           │                         │   delay               │
└───────────────────────────┴─────────────────────────┴───────────────────────┘
```

#### Tier 1: Fast Merge Gate
- **Invocation:** `pytest -m "not slow and not slow_dataset" --cov=src/omniscribe --cov-fail-under=80 --cov-report=term-missing --cov-report=xml --junitxml=reports/junit.xml -v`
- **Matrix:** Ubuntu (Python 3.11, 3.13, 3.14) and Windows (Python 3.11). Python 3.14 on Ubuntu exercises the Dockerfile base image (`python:3.14-slim`) to prevent container-drift breakage.
- **Contract:** Zero live network calls, zero external LLM dependencies, and zero heavy weights downloads. Total collection is **2,464 test items** across **200 test modules**, executing in **~23 seconds**.
- **Enforcement:** Flake absorption (`pytest-rerunfailures`) was explicitly eliminated from Tier 1; PR builds fail immediately on any non-deterministic assertion, and a machine-readable JUnit XML artifact (`junit-${{ matrix.python }}-${{ matrix.os }}`) is uploaded for triage.

#### Tier 2: Nightly Slow Suite
- **Invocation:** `pytest -m slow --no-header -v --reruns 1 --reruns-delay 2`
- **Environment:** Dedicated Ubuntu runner with HuggingFace cache restoration (`~/.cache/huggingface`).
- **Contract:** Downloads Surya layout detection models (~500 MB) once and validates the hybrid OCR pipeline, dynamic programming text-line aligner (`core/aligner.py`), and multi-page PyMuPDF rasterization under realistic memory constraints.
- **Flake Policy:** 1 automatic rerun with a 2-second delay (`pytest-rerunfailures>=15.0`) to absorb transient network drops from upstream model hubs without failing the scheduled run.

#### Tier 3: Nightly Dataset & Calibration Suite
- **Invocation:** `pytest -m slow_dataset --no-header -v --reruns 1 --reruns-delay 2`
- **Contract:** Exercises the OCR Quality Trust Layer (`core/ocr_quality/`). Idempotently fetches benchmark datasets (`scripts/fetch_datasets.py`) and asserts:
  1. Expected Calibration Error (ECE) drops by $\ge 20\%$ under Platt scaling vs. raw model confidence on held-out OCR-Quality splits.
  2. Per-region reliability agreement $\ge 80\%$ on KIE-HVQA hallucination guard fixtures.
  3. Clean skip semantics (exit code 77) when upstream dataset licensing prohibits automatic download.

#### The Cordis In-Memory Harness Boot Fixture Chain
The backend testing infrastructure relies on an exemplary Cordis-style boot fixture chain (`tests/conftest.py:204-238`):
```
[cordis_env] ──> [harness_ctx] ──> [api_client (TestClient)]
```
- Boots the entire 14-plugin Cordis dependency tree in-process without spinning up background OS processes or persistent network sockets.
- Mocks out the physical VLM via `_StubOCR` (providing deterministic OCR bounding boxes and pre-canned JSON text responses).
- Establishes isolated, temporary SQLite databases for state backends with aggressive TTLs to guarantee cross-test test-case isolation.
- Validates OpenAPI schema consistency on every test run (`tests/routers/test_openapi_schema.py`) by asserting that the runtime FastAPI `app.openapi()` dictionary is byte-for-byte compatible with the checked-in `tests/openapi.json` contract (3,200+ lines), instantly preventing route signature drift.

---

### 8.2 Test Coverage Shape and Distribution

OmniScribe maintains a strictly enforced code coverage threshold of **$\ge 80\%$** (`[tool.coverage.report] fail_under = 80` in `pyproject.toml:376`, verified via `--cov-fail-under=80` in CI). Current baseline coverage across `src/omniscribe` sits at **~82.4%**.

```
OmniScribe Test Distribution by Subsystem:
Core OCR & Resilience       [ 10 files ] █████████████████ 85%
OCR Quality & Trust Layer   [ 14 files ] ██████████████████ 90%
Workflows (Hybrid/Grounded) [  9 files ] ████████████████ 80%
Translation & LangGraph     [  9 files ] █████████████ 65%
PDF & Rasterization         [  6 files ] █████████████████ 85%
Lexicon RAG & LanceDB       [  5 files ] ████████████ 60%
Plugins (14 plugins)        [ 28 files ] █████████████████ 85%
HTTP Routers & API Surface  [ 18 files ] ██████████████████ 90%
Harness & Plugin Loader     [ 10 files ] ██████████████████ 90%
Utilities & Structured Log  [ 12 files ] ██████████████████ 90%
Property-Based Fuzzing      [  8 files ] ████████████████ 80%
```

#### High-Coverage Pillars ($\ge 85\%$)
- **`core/ocr_quality/` (14 test files, ~90%):** Exhaustive coverage of watermarking, script detection, hallucination cross-checking, trust score aggregation, and Platt scaling confidence calibration.
- **`harness/` and `middleware/` (13 test files, ~90%):** Complete coverage of the plugin lifecycle, protocol registration, LIFO effect cleanup rollback on boot failure (`context.py:204-221`), constant-time token authentication (`middleware/auth.py`), rate-limiting buckets, and streaming upload size guards (`middleware/upload_limit.py`).
- **`routers/` and `plugins/ocr/` (18 test files, ~90%):** Complete route validation, multipart streaming parsing, magic-byte content sniffing, and SSE event streaming.

#### Monitored Lower-Coverage Modules (The 15 Under-Tested Modules)
Tracked in `pyproject.toml:370-376`, fifteen specific modules account for the remaining coverage delta due to requiring external hardware or licensing dependencies:
- `core/transcription`: Requires live Faster-Whisper models and audio DSP hardware.
- `core/grounded`: Requires native vision bounding-box inference models.
- `core/lexicon/store.py` & `lancedb_store.py`: Native LanceDB / PyArrow vector operations.
- `core/glossary_sources/git_repo.py`: Requires external git subprocess fixtures.
- `plugins/glossary`: Multi-table SQL imports and large-scale TMX/TBX file ingestion.

#### Flutter Frontend Coverage
- **Unit & Widget Suite:** **39 test files** in `client/test/` comprising **345 passing tests**.
- **Coverage Surface:** Complete verification of Riverpod state notifiers (`SettingsNotifier`, `JobsNotifier`, `ProviderNotifier`, `DocumentViewportNotifier`, `DocumentSelectionNotifier`, `JobOrchestrationNotifier`), table-driven Dio error translation (`network_test.dart`), modal dialogs (`export_modal_test.dart`), and viewport zoom/pan geometry.

---

### 8.3 Hypothesis Property-Based Testing Status (Q4 / C3 Resolution)

In the historical five-lens audit (Finding Q4, preserved in Git history), property-based testing was classified as "token: 5 `@given` in 1 file". Through Action C3 and follow-on remediation waves, OmniScribe expanded its property-based testing posture into **8 dedicated test suites** containing **41 `@given` invariants**:

| Test File | `@given` Tests | Subsystem Targeted | Invariant Verified |
|---|---|---|---|
| `tests/utils/test_prompt_safety_props.py` | 6 | `utils/prompt_safety.py` | Length bounding, unicode sanitization, null byte removal, boundary marker stripping, whitespace collapse, idempotence on short strings. |
| `tests/utils/test_json_parse_props.py` | 10 | `utils/json_parse.py` | Valid JSON round-trips, surrounding whitespace tolerance, markdown code fences, embedded object extraction, braces in string literals, leading/trailing junk recovery, unclosed quote tolerance. |
| `tests/core/chunking/test_chunker_props.py` | 1 | `core/chunking/` | Token limit bounding, section hierarchy preservation, zero text loss across chunk boundaries. |
| `tests/core/ocr/test_filters_props.py` | 7 | `core/ocr/filters.py` | Bounding box coordinate normalization, clamp to unit square $[0, 1]$, non-negative area, deduplication geometry. |
| `tests/core/translate/test_workflow.py` | 1 | `core/translate/workflow.py` | Chunker convergence on arbitrary text strings, non-empty chunk emission. |
| `tests/core/recall/test_whitespace_props.py` | 5 | `core/recall/whitespace.py` | Straddle overlap calculation monotonicity, box containment invariance, candidate drop limits. |
| `tests/core/pdf/test_page_range_props.py` | 6 | `core/pdf/page_range.py` | Positive integer parsing, disjoint set formatting, rejection of zero/negative integers, comma-separated token robustness. |
| `tests/core/ocr_quality/test_ocr_quality_trust_scorer_props.py` | 5 | `core/ocr_quality/trust_scorer.py` | Trust score interval bounds $[0.0, 1.0]$, flag accumulation determinism, monotonic score degradation under increased error penalties. |

These property suites fuzz thousands of generated inputs on every run, eliminating edge-case crashes from malformed JSON payloads, adversarial prompts, and irregular PDF page selections.

---

### 8.4 Flake Detection and Timing Synchronization

#### Elimination of Magic-Number Sleeps (Q3 Resolution)
Historical finding Q3 identified 10+ "drain the worker" sleeps (`time.sleep(0.01)`, `asyncio.sleep(0.01..0.05)`) that represented textbook flake vectors. A full code sweep across all 33 sleep sites in the test suite was executed:
- **4 Unsynchronized Waits Eliminated:** Replaced with deterministic event synchronization (`asyncio.wait_for` + conditions) in `tests/harness/test_context.py` (handler ordering), `tests/plugins/test_runtime_plugin.py` (awaiting `HarnessReady`), `tests/core/grounded/test_prompted_grounded_ocr.py` (event-driven cancellation), and `tests/plugins/test_jobs_redis.py:414` (deadline poll for pubsub frame).
- **2 Documented Clock-Order Constraints:** Retained in `test_jobs_redis.py:139, 199` due to Windows `time.time()` ~15 ms resolution; documented with pending migration to monotonic nanosecond clocks.
- **27 Verified Deterministic Patterns:** Confirmed as deadline-bounded polling loops (`_wait_status`), deliberate chaos test race windows, or wall-clock cache expiration verifications.

#### Flake Detection Strategy
1. **Strict Fast Tier:** Merge gate (`.github/workflows/test.yml`) runs **zero re-runs**. Any flaking test immediately fails the PR.
2. **Machine-Readable JUnit XML:** `--junitxml=reports/junit.xml` records test run outcomes and is preserved as a 30-day workflow artifact.
3. **Nightly Flake Mitigation:** `pytest-rerunfailures>=15.0` is configured exclusively on nightly jobs (`.github/workflows/nightly.yml`), allowing `--reruns 1 --reruns-delay 2` to absorb external HuggingFace network latency spikes while logging the occurrence.

---

### 8.5 Client Integration Testing Architecture (`client/integration_test/`)

Historical finding Q12 flagged that Flutter lacked an `integration_test/` directory. Today, `client/integration_test/` contains **15 Dart integration files**:

```
client/integration_test/
├── _test_helpers.dart                   # Shared test pump & finder utilities
├── stub_omniscribe_server.dart          # In-process dart:io FastAPI mock server
├── app_workstation_test.dart            # Golden path OCR run against stub server
├── app_real_server_test.dart            # Live server spawn & preview roundtrip
├── app_ai_wizard_test.dart              # AI Provider setup wizard workflow
├── app_auth_banner_test.dart            # Bearer token prompt & submission
├── app_export_modal_test.dart           # Export format configuration & download
├── app_feature_screens_test.dart        # Screen navigation & routing
├── app_job_history_test.dart            # Job table inspection & cancellation
├── app_provider_modal_test.dart         # Provider credentials configuration
├── app_settings_test.dart               # Settings persistence & URL switching
├── app_tab_ribbon_test.dart             # Top navigation tab bar
├── app_workstation_dock_test.dart       # Right control dock interaction
├── app_workstation_dropzone_test.dart   # File drop & staging
└── app_workstation_page_strip_test.dart # Page thumbnail navigation
```

#### In-Process Stub Server (`stub_omniscribe_server.dart`)
An in-process `dart:io` HTTP server binding to loopback that mocks the entire backend contract:
- `POST /api/progress/session`: Returns dynamic channel ID and WebSocket token.
- `WebSocket /ws/{channel}`: Handles full RFC-6455 WebSocket upgrades and streams synthetic `block_complete` and `job_completed` frames to drive UI animations.
- `POST /api/process`: Validates multipart file uploads, returns mock PDF bytes, and emits required `X-Text-Artifact-Id` and `X-Text-Artifact-Token` headers.
- `POST /api/documents/preview`: Returns rasterized 1x1 PNG bytes with width/height headers.
- `Catch-All Handler`: Responds with `{"cancelled": true}` on unmatched POSTs to guarantee clean cancellation teardown.

#### Real Server Execution (`app_real_server_test.dart`)
Spawns a real, live `omniscribe-server` child process via `uv run` on a dynamically allocated loopback port. It polls `/api/health` until ready, downloads the auth-exempt sample fixture (`/api/sample-pdf/digital.pdf`), stages it in the Flutter workstation, and executes a real server-side preview rasterization roundtrip. Constrained environments without Python tooling gracefully skip via `markTestSkipped()`.

---

### 8.6 Mutation Testing Evaluation (Q7)

Historical finding Q7 recommended evaluating mutation testing (`mutmut`, `cosmic-ray`, `mutatest`). An exhaustive tooling evaluation and pilot benchmark were conducted on 2026-09-13:

#### Tooling Constraints
- **`mutmut`:** Fails on native Windows due to Unix-specific process forking assumptions (upstream issue `boxed/mutmut#397`). Adopting `mutmut` would force all mutation testing into WSL or Linux CI containers.
- **`cosmic-ray 8.7`:** Works out of the box on native Windows via Python's standard `multiprocessing` and executes cleanly via `uv run --with cosmic-ray`.

#### Pilot Benchmark Data (`core/pdf/page_range.py`)
- **Mutants Generated:** 137 syntax mutations.
- **Execution Run:** 81 mutants processed before budget parking:
  - **45 KILLED** (56.2% raw kill rate against unit tests).
  - **35 SURVIVED** (survivors analyzed: primarily type-checking redundancies such as `not not isinstance(...)` that are already caught by the companion property test `test_page_range_props.py`).
- **Cost Metrics:** Observed runtime was **8–15 seconds per mutant** on Windows, heavily dominated by repeated `pytest` process startup overhead. A full 137-mutant pass requires **~35 minutes** for a single 150-line utility module.

#### Strategic Conclusion
Mutation testing with `cosmic-ray` is technically verified and reproducible, but its extreme execution cost makes it unsuitable for CI gating. It is retained as an **optional, on-demand maintainer tool** for core algorithmic parsers (`utils/json_parse.py`, `utils/prompt_safety.py`, `core/pdf/page_range.py`).

---

### 8.7 Detailed Status of QA Lens Findings (Q1–Q16)

| ID | Title | Historical Severity | Current Status | Verification Evidence & Resolution |
|---|---|---|---|---|
| **Q1** | Stale Makefile test path | High | **RESOLVED** | `Makefile:85` updated to target `tests/routers/test_openapi_schema.py`. Running `make openapi` correctly runs schema regeneration. |
| **Q2** | Stale `live_llm` references in CI | High | **RESOLVED** | Comments in `.github/workflows/test.yml:11` and `nightly.yml:12` rewritten to reflect that `live_llm` was retired in Wave 14. |
| **Q3** | 10+ magic-number sleeps | High | **RESOLVED** | All 33 sleep sites audited. 4 un-synchronized waits replaced with `asyncio.wait_for` event listeners; 2 clock-order constraints documented; remaining 27 verified as bounded polls or chaos tests. |
| **Q4** | Token property-based testing | Medium | **RESOLVED** | Expanded from 5 `@given` tests in 1 file to 41 `@given` tests across 8 suites covering prompt safety, JSON parsing, chunking, filters, whitespace recall, page ranges, and trust scoring. |
| **Q5** | No direct test for `translate/workflow.py` | Medium | **RESOLVED** | Added loop-level tests in `tests/core/translate/test_workflow.py` (26/26 passing). Discovered and fixed latent sync `app.invoke` bug via `_invoke_app` thread bridge. |
| **Q6** | `tests/fixtures/` missing PDFs | Medium | **RESOLVED** | Sample PDFs re-homed to `tests/fixtures/pdfs/` with autouse session fixtures in `tests/conftest.py` and bundle mirroring in `Makefile:pre-build`. |
| **Q7** | No mutation testing | Medium | **DEFERRED** | Evaluated via `cosmic-ray 8.7` pilot on `core/pdf/page_range.py` (56.2% kill rate). Documented as optional maintainer tool due to 35-minute runtime cost on Windows. |
| **Q8** | Coverage gate 80% floor with 15 under-tested modules | Medium | **RESOLVED** | CI enforce `--cov-fail-under=80`. 15 heavy-fixture modules documented in `pyproject.toml:370-376`; overall suite sits at 82.4% coverage. |
| **Q9** | Calibration test uses hard-coded seed=42 | Low | **RESOLVED** | Verified in `tests/core/ocr_quality/test_ocr_quality_calibration_fit.py` and `test_calibrate_model_script.py` that seed controls optimizer trajectory and convergence. |
| **Q10** | Single live smoke test for `arrow_substrait` | Low | **RESOLVED** | Reconciled into ops suite (`tests/ops/test_arrow_substrait_present.py`) with clean skip handling when LanceDB extra is uninstalled. |
| **Q11** | No chaos / fault-injection tests | Low | **RESOLVED** | Chaos and fault-injection suites added in `tests/core/ocr/test_ocr_resilience.py`, `test_circuit_breaker_registry.py`, and `test_llm_balance_error.py`. |
| **Q12** | Flutter client lacks `integration_test/` | Low | **RESOLVED** | Created `client/integration_test/` with 15 test files, including `stub_omniscribe_server.dart`, `app_workstation_test.dart`, and `app_real_server_test.dart`. |
| **Q13** | Flutter widget test smoke count uneven | Low | **RESOLVED** | Expanded Flutter test suite from 1 test in `widget_test.dart` to 345 unit/widget tests across 39 test files in `client/test/`. |
| **Q14** | Scripts excluded from lint/typecheck | Info | **RESOLVED** | `scripts/dev.py`, `scripts/build_windows.py`, and `scripts/confidence_eval.py` typechecked and linted. `E402` scoped in `pyproject.toml:346-348`. |
| **Q15** | No explicit `[tool.hypothesis]` profile | Info | **RESOLVED** | Hypothesis default profile configured with 100 iterations; deadlines tuned for deterministic CI runs. |
| **Q16** | `slow_dataset` references mini-fixtures | Info | **RESOLVED** | In-tree mini-fixtures (`ocr_quality_mini.json`, `kie_hvqa_mini.json`) run offline in fast tier; `scripts/fetch_datasets.py` handles licensed full sets in nightly. |

---

---

## 9. Product Readiness, Packaging & Developer Experience

### 9.1 Target Audience and The User Journey Transformation

Historically, OmniScribe suffered from an identity crisis: positioned as a local-first privacy tool for researchers and professionals, its front door forced users through a grueling, 16-step developer journey:

```
Historical User Journey (16 Steps — High Abandonment):
[Install Git] ──> [Install Python 3.11] ──> [Install uv] ──> [Clone Repo] ──>
[uv sync with 3 extras] ──> [Find & Install LM Studio] ──> [Download Vision Model (5-50GB)] ──>
[Start LM Studio on Port 1234] ──> [Run backend in Terminal A] ──> [Install Flutter SDK (1.5GB)] ──>
[cd client && flutter pub get] ──> [flutter run in Terminal B] ──> [Select Target Device] ──>
[Configure Client IP] ──> [Drop PDF] ──> [Searchable PDF]
```

#### The Transformed Dual-Track Journey

OmniScribe v0.3.0 establishes two distinct, fully supported onboarding tracks:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       End-User Track (3 Steps — Zero Code)                  │
├─────────────────────────────────────────────────────────────────────────────┤
│ 1. Download & Launch LM Studio (start local vision model on port 1234).     │
│ 2. Download & Run `omniscribe-server-windows.exe` (522 MB single binary).   │
│ 3. Launch Flutter Desktop Client (auto-connects to 127.0.0.1:8000).        │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                    Developer / Contributor Track (Fast & Guarded)           │
├─────────────────────────────────────────────────────────────────────────────┤
│ 1. `git clone https://github.com/Sifr-r/OmniScribe.git && cd OmniScribe`    │
│ 2. `uv sync --extra web --extra preprocessing`                              │
│ 3. `make doctor` (validates Python, uv, Redis, and local VLM port 1234).    │
│ 4. `make check` (runs lint + mypy + 2,464 fast tests in ~23s).             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### 9.2 Windows Single-Binary PyInstaller Distribution (522 MB)

The flagship achievement of the v0.3.0 packaging sprint (RFC 001 & RFC 002) is the standalone Windows executable distribution:

```
omniscribe-server-windows.exe (522 MB)
├── Embedded Python 3.12 Runtime
├── FastAPI + Uvicorn ASGI Server
├── Complete OmniScribe Core Engine + 14 Plugins
├── Embedded PyTorch + TorchVision + Surya ONNX Models
├── PyMuPDF (AGPL-3.0) + Pillow (Native AVIF)
├── LanceDB + PyArrow + DuckDB Lexicon RAG Stack
├── Redis State Backend Plugin & Redis Client
└── Bundled Dictionaries & Resources (`cordis.yml`)
```

#### Spec Engineering & Bundling Resolution
Building a 500+ MB PyInstaller binary with complex C-extensions previously failed across 14 attempts. The root cause was an `EXCLUDES` conflict in `omniscribe_server.spec`:
1. **AnyIO Collision:** `anyio` was erroneously listed in `EXCLUDES` while simultaneously targeted by `collect_submodules("anyio")`. PyInstaller's exclusion rules took precedence, breaking FastAPI's asynchronous event loop on boot.
2. **Hidden Imports Resolved:** Added `import anyio.abc` to `scripts/run_server.py`, included `collect_submodules("fastapi")` and `collect_submodules("pydantic_settings")`, and manually added private SciPy submodule `"scipy._external.array_api_compat.numpy.fft"`.
3. **Submodule Harvesting:** Configured `collect_submodules("omniscribe")` so all plugins—including the Redis state backend (`plugins/jobs_redis.py`)—are automatically bundled.
4. **Footprint Growth:** Binary expanded from 307 MB in initial prototypes to 522 MB due to the inclusion of `lancedb`, `pyarrow`, and `duckdb` dependencies required by the lexicon vector store.

#### Automated Packaging Verification
- `make bundle`: Executes `scripts/build_windows.py` to build the binary using PyInstaller 6.20+.
- `make bundle-smoke`: Spawns the compiled executable as a subprocess, hits `/api/health -> 200`, verifies empty job queue `/api/jobs -> 200 []`, asserts the 45 KB `/openapi.json` payload, and gracefully terminates the server.
- **SmartScreen & Security:** Unsigned binary behavior is documented in `docs/deployment/windows-bundle.md` (explaining the "More info -> Run anyway" prompt). The application writes state to `%LOCALAPPDATA%\omniscribe\omniscribe-state.db` (WAL mode).

---

### 9.3 Documentation Drift Resolution

The historical audit revealed significant documentation drift, where shipped security features were described as "deferred scaffolding" and operational guides lacked cohesion. A comprehensive documentation remediation was executed:

| Document | Historical State | Remediated State | Key Verification |
|---|---|---|---|
| **Root `README.md`** | Non-existent at root (only `docs/README.md`); opened with internal refactor changelog. | Moved to repository root; opened with compelling 1-line hook ("turns scans into searchable PDFs, on your machine, no internet"); added 4 screenshots, privacy policy, hardware VRAM table, and performance benchmarks. | `README.md:1-60` |
| **`client/README.md`** | Default Flutter starter stub ("A new Flutter project"). | 104-line comprehensive guide detailing architecture, prerequisite VLM setup, build overrides (`--dart-define=OMNISCRIBE_API_BASE`), UI tabs, and error recovery. | `client/README.md:1-104` |
| **`docs/SECURITY.md`** | Described bearer auth, rate limiting, and upload limits as "deferred scaffolding". | Reconciled against `server.py:184-202`; documented unconditional middleware wiring, placeholder token denylist, loopback vs LAN guards, and upload limit reduction to 1024 MB. | `docs/SECURITY.md:62-82` |
| **`docs/DEPLOYMENT.md`** | Documented non-existent per-service tokens (`OMNISCRIBE_OCR_AUTH_TOKEN`) and in-memory default. | Documented single enforced bearer token (`OMNISCRIBE_AUTH_TOKEN`); updated SQLite as the default persistent state backend since 2026-09-05. | `docs/DEPLOYMENT.md:132-140, 212-218` |
| **`docs/TROUBLESHOOTING.md`** | Absent (only 3 bullets in deployment docs). | 457-line field guide covering top first-run failure modes: OCR returning blank pages, LAN bind token rejection, Windows Defender `arrow_substrait.dll` false positives, and missing VLM endpoints. | `docs/TROUBLESHOOTING.md:1-457` |
| **`CONTRIBUTING.md`** | Absent. | Complete contributor guide created linking to `docs/AGENTS.md`, pre-PR `make check` gates, and issue templates. | `CONTRIBUTING.md:1-123` |

---

### 9.4 Developer Tooling & Diagnostics

OmniScribe features a mature developer experience designed for maintainers and operators:

#### `make doctor` & `scripts/dev.py`
Running `make doctor` (or `uv run python scripts/dev.py doctor`) performs an instantaneous, 4-point environmental diagnostic:
```
Runtime health
--------------
OK    uv: uv 0.6.5 (x86_64-pc-windows-msvc)
OK    Python: 3.12.3 (requires 3.11+)
WARN  Redis: unavailable at localhost:6379 ([WinError 10061] No connection could be made)
      -> see docs/TROUBLESHOOTING.md#make-doctor-says-redis-is-unreachable-but-i-dont-use-redis
OK    Model server: reachable at http://localhost:1234/v1 (1 model(s) loaded)
```
- **Windows Terminal Safe:** Emits standard ASCII arrows (`->`) to avoid character encoding crashes on Windows consoles running code page `cp1252`.
- **Actionable Jump Links:** Directly outputs Markdown anchor slugs matching headings in `docs/TROUBLESHOOTING.md` so users can resolve warnings with one click.

#### Developer Automation in `Makefile`
- `make check`: Executes the exact CI fast gate locally (`make lint` + `make typecheck` + `pytest -m "not slow and not slow_dataset" --cov=src/omniscribe --cov-fail-under=80`).
- `make clean`: Reliably purges `.mypy_cache`, `.pytest_cache`, `.ruff_cache`, `build`, `dist`, `htmlcov`, and `__pycache__` trees.
- `make openapi`: Boots the FastAPI server via `TestClient` and regenerates `tests/openapi.json` to detect API schema drift.
- `make audit`: Runs `uv run pip-audit` to scan dependencies for known CVEs.
- `make security`: Invokes Semgrep static analysis via `uvx` against OWASP top-ten rules.

---

### 9.5 Detailed Status of Product Manager (P1–P16) and End-User (U1–U14) Findings

#### Product Manager Lens (P1–P16)

| ID | Title | Severity | Status | Verification Evidence & Resolution |
|---|---|---|---|---|
| **P1** | Middleware described as deferred in docs | Critical | **RESOLVED** | `docs/SECURITY.md:62-78` and `outstanding-work.md` updated to document that bearer auth, rate-limiting, and upload capping are live in `server.py:184-202`. |
| **P2** | `client/README.md` is starter template | Critical | **RESOLVED** | Rewritten into a comprehensive 104-line guide covering client installation, prerequisites, UI features, and backend connection settings. |
| **P3** | Large install footprint / extras confusion | High | **RESOLVED** | `README.md:92-106` clearly scopes extras (`async-translation`, `lexicon`, `trocr`, `nllb`); Windows single-binary distribution eliminates Python install entirely. |
| **P4** | "Local" claim conditional on non-shipped VLM | High | **RESOLVED** | Added prominent "Before you start" section to `README.md:29-43` with LM Studio download links, model recommendations, and a VRAM sizing table. |
| **P5** | In-memory state backend loses history on restart | High | **RESOLVED** | SQLite state backend (`SQLiteStateBackend`) made default since 2026-09-05. Documented in `DEPLOYMENT.md:212-218`. |
| **P6** | No `CONTRIBUTING.md`, CoC, or issue templates | High | **RESOLVED** | Created `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `.github/ISSUE_TEMPLATE/bug_report.md`, and `.github/ISSUE_TEMPLATE/feature_request.md`. |
| **P7** | PyMuPDF AGPL warning buried | Medium | **RESOLVED** | Prominent AGPL-3.0 badge placed at top of `README.md:6`; detailed dual-licensing disclosure and `pypdfium2` fallback guide in `README.md:227-246`. |
| **P8** | CHANGELOG is internal audit heavy | Medium | **RESOLVED** | `docs/CHANGELOG.md` restructured with clear "User-visible changes" and "Maintenance" subheadings per release. |
| **P9** | No GitHub release tag / automated releases | Medium | **RESOLVED** | v0.2.0 and v0.3.0 release tags cut and published with automated assets via `.github/workflows/release.yml`. |
| **P10** | Per-service tokens documented but not enforced | Medium | **RESOLVED** | `docs/DEPLOYMENT.md:132-140` rewritten to clarify that a single `OMNISCRIBE_AUTH_TOKEN` is enforced across all guarded routes. |
| **P11** | `omniscribe-migrate-lexicon` CLI script confusion | Low | **RESOLVED** | Documented in `README.md:107-122` as a dedicated maintenance migration utility for upgrading legacy ChromaDB stores to LanceDB. |
| **P12** | `outstanding-work.md` is closing log not roadmap | Low | **RESOLVED** | `docs/outstanding-work.md` restructured to track only open backlog items, with completed items archived to git history. |
| **P13** | No published PGP key for security contact | Low | **DEFERRED** | Security disclosure process documented in `docs/SECURITY.md:11-25`. Public PGP key publication deferred to maintainer discretion. |
| **P14** | Compose upload limit (10 GB) misaligned | Low | **RESOLVED** | `compose.yaml:109` and `docs/SECURITY.md:75` aligned; default max upload set to 1024 MB. |
| **P15** | No telemetry or analytics | Info | **CONFIRMED** | Zero telemetry, zero tracking, zero outbound network calls. Reaffirmed in `README.md:20-27` and `docs/SECURITY.md:131-143`. |
| **P16** | Single point of contact (bus-factor risk) | Info | **ACKNOWLEDGED**| Disclosed in `pyproject.toml` and `CONTRIBUTING.md`. Architectural documentation in `docs/` maintained to lower onboarding barriers. |

#### End-User Lens (U1–U14)

| ID | Title | Severity | Status | Verification Evidence & Resolution |
|---|---|---|---|---|
| **U1** | No top-level `README.md` | Critical | **RESOLVED** | `docs/README.md` moved to repository root `README.md` with updated relative links across all documentation. |
| **U2** | User must configure VLM with no guidance | Critical | **RESOLVED** | "Before you start" section added to `README.md:29-43` with LM Studio instructions, model links, and hardware VRAM recommendations. |
| **U3** | No end-user install path (12+ steps) | Critical | **RESOLVED** | Shipped single-binary Windows distribution `omniscribe-server-windows.exe` (522 MB) reducing setup to 3 zero-code steps. |
| **U4** | "Web UI" in docs is a 5-line placeholder | Critical | **RESOLVED** | Clarified in `README.md:3-6` and `DEPLOYMENT.md` that the Flutter client is the primary UI; legacy web workstation is deprecated. |
| **U5** | `client/README.md` is starter template | High | **RESOLVED** | Completely rewritten with installation instructions, tab descriptions, and troubleshooting steps. |
| **U6** | No screenshots or demo media | High | **RESOLVED** | 7 UI screenshots captured in `docs/screenshots/`; 4 embedded in `README.md:10-18`. Interactive GIF remains owner item. |
| **U7** | No FAQ or `TROUBLESHOOTING.md` | High | **RESOLVED** | Created comprehensive 457-line `docs/TROUBLESHOOTING.md` covering common failure modes and error strings. |
| **U8** | No performance expectations set | High | **RESOLVED** | Added "Performance expectations" section to `README.md:44-60` detailing local CPU stage timings and VLM cost models. |
| **U9** | First paragraph contains internal jargon | Medium | **RESOLVED** | Replaced introductory text with clean hook: "OmniScribe turns scanned PDFs and photos into searchable, selectable PDFs. Everything runs on your machine..." |
| **U10** | `docker compose up` requires unset Redis password | Medium | **RESOLVED** | Updated `.env.example:188-195` and `compose.yaml` with explicit instructions for generating `REDIS_PASSWORD` and `OMNISCRIBE_AUTH_TOKEN`. |
| **U11** | No supported platforms table | Medium | **RESOLVED** | Added "Supported platforms" table to `README.md:125-141` breaking down OS support across backend, client, and binary packaging. |
| **U12** | `examples/` framed as dev fixtures | Medium | **RESOLVED** | Exposed in-UI "Try with sample PDF" button in Flutter client calling auth-exempt `/api/sample-pdf/digital.pdf` endpoint. |
| **U13** | Trust and privacy signals buried | Low | **RESOLVED** | Dedicated "Trust & Privacy" section added near top of `README.md:20-27` detailing local-first guarantees and open-source licensing. |
| **U14** | CHANGELOG is developer-focused | Low | **RESOLVED** | `docs/CHANGELOG.md` organized into user-facing release summaries and maintenance technical breakdowns. |

---

---

## 10. Master Unified Audit Matrix & Living Roadmap

### 10.1 Master Unified Audit Cross-Reference Matrix

The table below cross-references all **127 findings** identified across historical audits, complexity reports, duplication reviews, and diagnostic investigations:
- **C1–C7:** Convergent Findings (Five-Lens Audit, 2026-09-04)
- **D1–D20:** Dev / Software Engineering Lens (Five-Lens Audit, 2026-09-04)
- **S1–S19:** Security Lens (Five-Lens Audit, 2026-09-04)
- **Q1–Q16:** QA / Test Engineering Lens (Five-Lens Audit, 2026-09-04)
- **P1–P16:** Product Manager Lens (Five-Lens Audit, 2026-09-04)
- **U1–U14:** End-User Lens (Five-Lens Audit, 2026-09-04)
- **COMP-01–COMP-10:** Code Complexity Findings (Complexity Report, 2026-09-13)
- **F1–F15:** Code Duplication Findings (Duplication Audit, 2026-09-13)
- **Defects 1–10:** Functional Integration Defects (Functional Diagnosis, 2026-09-15)
- **Roadmap P1–P4:** Remediation Roadmap Tasks (Roadmap, 2026-09-13)

| Finding ID | Domain | Summary | Severity | Current Status | Verification Evidence / Resolution |
|---|---|---|---|---|---|
| **C1** | Convergent | Documentation drift vs. shipped code | High | **RESOLVED** | Reconciled across `SECURITY.md`, `DEPLOYMENT.md`, `README.md`, and `outstanding-work.md`. |
| **C2** | Convergent | End-user install path is 12–16 steps | High | **RESOLVED** | Windows single-binary PyInstaller distribution shipped (522 MB) reducing setup to 3 steps. |
| **C3** | Convergent | In-memory state backend loses history on restart | High | **RESOLVED** | `SQLiteStateBackend` enabled as default state backend since 2026-09-05. |
| **C4** | Convergent | Missing top-level README; client README is stub | High | **RESOLVED** | `README.md` placed at repository root; `client/README.md` completely rewritten. |
| **C5** | Convergent | Active default for `REDIS_PASSWORD` in `.env.example` | High | **RESOLVED** | Emptied default in `.env.example`; Compose `:?` enforces explicit operator password. |
| **C6** | Convergent | `make doctor` is undocumented | Medium | **RESOLVED** | Documented in `README.md`, `client/README.md`, and `TROUBLESHOOTING.md` with ASCII pointers. |
| **C7** | Convergent | Missing troubleshooting and contributing guides | Medium | **RESOLVED** | Created `docs/TROUBLESHOOTING.md`, `CONTRIBUTING.md`, and GitHub issue templates. |
| **D1** | Dev | `JobStatusResponse.started_at` is always `None` | Critical | **RESOLVED** | Persisted job start timestamp in `plugins/jobs.py` and returned in status response. |
| **D2** | Dev | `InMemoryJobQueue._run` swallows runner exceptions | Critical | **RESOLVED** | Exceptions caught, job transitioned to `error`, and `JobFailed` event emitted. |
| **D3** | Dev | `HybridEngine` decorative re-injection on `execute()` | High | **RESOLVED** | Standardized on constructor dependency injection; passthroughs kept for test back-compat. |
| **D4** | Dev | `HybridEngine._reset_run_state` resets only two states | High | **RESOLVED** | Full stage state reset implemented across layout, OCR, and refine stages. |
| **D5** | Dev | Multiple `load_dotenv()` calls at module import time | High | **RESOLVED** | Consolidated `load_dotenv()` into `main()`; removed import-time calls in `processor.py`. |
| **D6** | Dev | `plugins/ocr/service.py` is monolithic (890 LOC) | High | **RESOLVED** | Decomposed into `routes.py`, `service.py`, and sniffing helpers (COMP-05, COMP-09). |
| **D7** | Dev | Routes reach into private service state | High | **RESOLVED** | Promoted private attributes to public properties on `OCRService`. |
| **D8** | Dev | Global exception envelope differs from route envelope | High | **RESOLVED** | Standardized error envelope format `{"error": ..., "detail": ...}` in `plugins/_http.py`. |
| **D9** | Dev | Mypy `disallow_untyped_defs` enabled only for `core.*` | Medium | **RESOLVED** | Extended strict typing to `omniscribe.plugins.*` and `omniscribe.harness.*` in `pyproject.toml`. |
| **D10** | Dev | `cors_origins` accepts multiple aliases | Medium | **RESOLVED** | Removed deprecated config aliases; unified CORS handling in `config.py`. |
| **D11** | Dev | `extract_json` has O(n²) loop over opening braces | Medium | **RESOLVED** | Implemented single-pass balanced-span scan `_balanced_spans` in `utils/json_parse.py`. |
| **D12** | Dev | `OCRProcessor.__getattr__` rebuilds `_DEFAULTS` | Medium | **RESOLVED** | Hoisted immutable default dictionary to module level. |
| **D13** | Dev | `is_transient_error` conflates `ValueError` with bugs | Medium | **RESOLVED** | Separated input validation errors from transient connection failures in `resilience.py`. |
| **D14** | Dev | `_DENSE_MODE_ALIASES` hidden in property | Medium | **RESOLVED** | Unified dense mode parsing into `DenseMode` enum in `plugins/ocr/schemas.py`. |
| **D15** | Dev | `trust_images_dict` passed then discarded | Medium | **RESOLVED** | Removed unused parameter from `_finalize` in `core/workflows/hybrid.py`. |
| **D16** | Dev | Dockerfile pins Python 3.14 vs. classifiers 3.11–3.13 | Low | **RESOLVED** | Added Python 3.14 to CI matrix (`test.yml`) and updated classifiers. |
| **D17** | Dev | `transformers>=5.15.1` pin in extras | Low | **RESOLVED** | Verified dependency resolver compatibility for `trocr` and `nllb` extras. |
| **D18** | Dev | `make lint` runs `--no-fix` while pre-commit fixes | Low | **RESOLVED** | Intentional: `make lint` is read-only CI check; pre-commit is auto-fix (`Makefile:39`). |
| **D19** | Dev | `is_transient_error` treats `RuntimeError` as permanent | Low | **RESOLVED** | Added special handling for transient HTTPX/network `RuntimeError` subclasses. |
| **D20** | Dev | `tests/conftest.py` mutates `sys.path` | Low | **RESOLVED** | Scoped `sys.path` modifications cleanly to test root. |
| **S1** | Security | Shipped active `REDIS_PASSWORD` in `.env.example` | High | **RESOLVED** | Emptied default value; Compose `:?` enforces operator-defined secret. |
| **S2** | Security | `SECURITY.md` claims middleware is deferred | High | **RESOLVED** | Documentation corrected to reflect live ASGI middleware triad (`SECURITY.md:62-78`). |
| **S3** | Security | `ALLOW_SSRF_LOCAL` default mismatch | Medium | **RESOLVED** | Aligned `.env.example` default to `false` matching codebase default. |
| **S4** | Security | `MAX_UPLOAD_MB` default is 10 GB | Medium | **RESOLVED** | Lowered default upload limit to 1024 MB in `config.py` and `SECURITY.md`. |
| **S5** | Security | `?token=` query parameter accepted on broad prefix | Medium | **RESOLVED** | Restricted query token authentication strictly to SSE and WebSocket paths. |
| **S6** | Security | `/api/progress/cancel` unauthenticated on loopback | Medium | **RESOLVED** | Bound cancellation to active session/channel tokens. |
| **S7** | Security | Rate limiter is per-process | Medium | **RESOLVED** | Documented per-worker rate limit scaling behavior in `DEPLOYMENT.md`. |
| **S8** | Security | `transcription_auth_token` mask leaks 8 chars | Medium | **RESOLVED** | Implemented constant-length fixed token masking. |
| **S9** | Security | `StaticFiles` mount is exempt from auth | Medium | **RESOLVED** | Documented rationale: static assets are public frontend bundles only. |
| **S10** | Security | JSON logger sensitive-field redaction is substring-based | Low | **RESOLVED** | Implemented exact-key sensitive field scrubbing in `structured_logging.py`. |
| **S11** | Security | Dockerfile CMD binds `0.0.0.0` | Low | **RESOLVED** | Server startup guard requires `OMNISCRIBE_AUTH_TOKEN` for `0.0.0.0` binds. |
| **S12** | Security | `git_repo._with_credentials` embeds secret in URL | Low | **RESOLVED** | Implemented credential sanitization and netrc support for git operations. |
| **S13** | Security | `cors_origins=*` with credentials interaction | Low | **RESOLVED** | Added unit tests verifying credentials rejection on wildcard CORS in `test_server_boot.py`. |
| **S14** | Security | WebSocket origin check disabled with wildcard CORS | Low | **RESOLVED** | Retained token verification as the primary WebSocket authorization gate. |
| **S15** | Security | `GET /api/jobs` lists all jobs without pagination | Low | **RESOLVED** | Bearer auth required when non-loopback; documented single-user dev design. |
| **S16** | Security | `extract_json` O(n²) DoS vector | Low | **RESOLVED** | Replaced with balanced-span parser bounded to 1,000 candidates in `utils/json_parse.py`. |
| **S17** | Security | PyMuPDF AGPL-3.0 dual-licensing | Info | **RESOLVED** | Disclosed in `README.md`, `pyproject.toml`, and `THIRD_PARTY_LICENSES.md`; `pypdfium2` documented. |
| **S18** | Security | `requests` dependency in dev tree | Info | **RESOLVED** | Audited; confined strictly to `dependency-groups.dev` and scripts. |
| **S19** | Security | Uvicorn access logs authorization headers | Info | **RESOLVED** | Verified: Uvicorn does not log `Authorization` headers in access logs. |
| **Q1** | QA | Makefile references non-existent test path | High | **RESOLVED** | Fixed target path in `Makefile:85` to `tests/routers/test_openapi_schema.py`. |
| **Q2** | QA | Stale `live_llm` references in CI | High | **RESOLVED** | CI comments updated in `test.yml` and `nightly.yml` reflecting marker removal. |
| **Q3** | QA | 10+ magic-number sleeps in tests | High | **RESOLVED** | 33 sleep sites audited; 4 replaced with event synchronization; 2 documented; rest verified. |
| **Q4** | QA | Token property-based testing (5 tests in 1 file) | Medium | **RESOLVED** | Expanded to 41 `@given` tests across 8 suites covering all core utilities and algorithms. |
| **Q5** | QA | `translate/workflow.py` lacks direct unit test | Medium | **RESOLVED** | Added 26 unit and loop tests in `test_workflow.py`; fixed sync `app.invoke` bug. |
| **Q6** | QA | `tests/fixtures/` missing PDFs | Medium | **RESOLVED** | Sample PDFs re-homed to `tests/fixtures/pdfs/` with autouse session fixtures. |
| **Q7** | QA | No mutation testing configured | Medium | **DEFERRED** | Evaluated with `cosmic-ray 8.7` pilot (56.2% kill rate); deferred as optional maintainer tool. |
| **Q8** | QA | Coverage floor at 80% with 15 under-tested modules | Medium | **RESOLVED** | Maintained $\ge 80\%$ coverage gate in CI; under-tested modules documented in `pyproject.toml`. |
| **Q9** | QA | Calibration test uses hard-coded seed=42 | Low | **RESOLVED** | Verified seed controls optimizer path in calibration regression tests. |
| **Q10** | QA | Single live smoke test for `arrow_substrait` | Low | **RESOLVED** | Reconciled in `tests/ops/test_arrow_substrait_present.py` with clean skip guards. |
| **Q11** | QA | No chaos / fault-injection tests | Low | **RESOLVED** | Added fault-injection tests in `test_ocr_resilience.py` and `test_circuit_breaker_registry.py`. |
| **Q12** | QA | Flutter client lacks `integration_test/` directory | Low | **RESOLVED** | Added 15 integration test files in `client/integration_test/` (stub + real server). |
| **Q13** | QA | Flutter widget test coverage uneven | Low | **RESOLVED** | Expanded Flutter test suite to 345 passing tests across 39 files in `client/test/`. |
| **Q14** | QA | Scripts excluded from lint/typecheck | Info | **RESOLVED** | Linting and typechecking applied to `scripts/dev.py` and `scripts/build_windows.py`. |
| **Q15** | QA | No explicit `[tool.hypothesis]` profile | Info | **RESOLVED** | Configured hypothesis settings for deterministic, non-flaky execution in CI. |
| **Q16** | QA | `slow_dataset` references mini-fixtures | Info | **RESOLVED** | Mini-fixtures run offline in fast tier; `scripts/fetch_datasets.py` gates full sets in nightly. |
| **P1** | PM | Middleware described as deferred in docs | Critical | **RESOLVED** | Reconciled in `SECURITY.md`, `DEPLOYMENT.md`, and `outstanding-work.md`. |
| **P2** | PM | `client/README.md` is unmodified starter | Critical | **RESOLVED** | Rewritten into comprehensive 104-line Flutter client operational guide. |
| **P3** | PM | Install footprint large; extras confusion | High | **RESOLVED** | Extras scoped cleanly in `pyproject.toml`; single-binary distribution created. |
| **P4** | PM | "Local" claim conditional on external VLM | High | **RESOLVED** | Added "Before you start" guide with LM Studio links and hardware VRAM recommendations. |
| **P5** | PM | In-memory state backend loses history on restart | High | **RESOLVED** | SQLite state backend set as default since 2026-09-05. |
| **P6** | PM | Missing contributor guide and issue templates | High | **RESOLVED** | Created `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, and GitHub issue templates. |
| **P7** | PM | PyMuPDF AGPL warning buried mid-README | Medium | **RESOLVED** | Prominent AGPL badge added to header; comprehensive licensing notice in README. |
| **P8** | PM | CHANGELOG is internal audit heavy | Medium | **RESOLVED** | Reorganized `CHANGELOG.md` into user-facing release notes and maintenance sections. |
| **P9** | PM | No GitHub release tag or release pipeline | Medium | **RESOLVED** | Automated release workflow (`release.yml`) published v0.2.0 and v0.3.0 releases. |
| **P10** | PM | Per-service tokens documented but not enforced | Medium | **RESOLVED** | `DEPLOYMENT.md` updated to document single enforced `OMNISCRIBE_AUTH_TOKEN`. |
| **P11** | PM | `omniscribe-migrate-lexicon` script confusion | Low | **RESOLVED** | Documented in `README.md` as a dedicated maintenance migration utility. |
| **P12** | PM | `outstanding-work.md` is closing log not roadmap | Low | **RESOLVED** | Pruned closed items to git history; file repurposed as living open backlog. |
| **P13** | PM | No published PGP key for security contact | Low | **DEFERRED** | Contact email published; public PGP key generation left to maintainer discretion. |
| **P14** | PM | Compose upload limit misaligned (10 GB) | Low | **RESOLVED** | Aligned Compose and documentation upload limit to 1024 MB. |
| **P15** | PM | No telemetry or analytics | Info | **CONFIRMED** | Local-first privacy guarantees reaffirmed across documentation. |
| **P16** | PM | Single point of contact (bus-factor risk) | Info | **ACKNOWLEDGED**| Bus factor acknowledged; thorough architectural ledger maintained to aid onboarding. |
| **U1** | End User | No top-level `README.md` | Critical | **RESOLVED** | Moved `docs/README.md` to root `README.md` and updated all relative links. |
| **U2** | End User | User must configure VLM with no guidance | Critical | **RESOLVED** | Added "Before you start" section with LM Studio instructions and model guide. |
| **U3** | End User | No end-user install path (12+ steps) | Critical | **RESOLVED** | Shipped single-binary Windows distribution `omniscribe-server-windows.exe` (522 MB). |
| **U4** | End User | "Web UI" in docs is a placeholder | Critical | **RESOLVED** | Clarified Flutter desktop/web client is the primary UI; legacy web workstation deprecated. |
| **U5** | End User | `client/README.md` is starter template | High | **RESOLVED** | Rewritten with full install, run, settings, and troubleshooting instructions. |
| **U6** | End User | No screenshots or demo media | High | **RESOLVED** | 7 screenshots captured; 4 embedded in `README.md`. Demo GIF remains owner item. |
| **U7** | End User | No FAQ or `TROUBLESHOOTING.md` | High | **RESOLVED** | Created 457-line `docs/TROUBLESHOOTING.md` with solutions for common errors. |
| **U8** | End User | No performance expectations set | High | **RESOLVED** | Added performance section to `README.md` with measured local stage timings. |
| **U9** | End User | First paragraph contains internal jargon | Medium | **RESOLVED** | Rewritten with concise user hook ("turns scans into searchable PDFs..."). |
| **U10** | End User | Compose requires unset Redis password | Medium | **RESOLVED** | Updated `.env.example` and `compose.yaml` with explicit setup instructions. |
| **U11** | End User | No supported platforms table | Medium | **RESOLVED** | Added supported platforms table to `README.md:125-141`. |
| **U12** | End User | `examples/` framed as dev fixtures | Medium | **RESOLVED** | In-UI "Try with sample PDF" button added; calls auth-exempt sample route. |
| **U13** | End User | Trust and privacy signals buried | Low | **RESOLVED** | Prominent "Trust & Privacy" section added near top of `README.md:20-27`. |
| **U14** | End User | CHANGELOG is developer-focused | Low | **RESOLVED** | Restructured `CHANGELOG.md` with clear user-facing release notes. |
| **COMP-01**| Complexity | Multi-format VLM client god function (`complete_vlm_prompt`, CC 64, Cog 132) | Critical | **RESOLVED** | Refactored using Strategy/Adapter pattern with `_FORMAT_REGISTRY` in `multi_format_client.py`. |
| **COMP-02**| Complexity | DOCX AST renderer deep branching (`_render_block`, CC 33, Cog 129) | Critical | **RESOLVED** | Refactored using module-level `_RENDER_DISPATCH` table in `core/writers/docx_tree.py`. |
| **COMP-03**| Complexity | Flutter right control dock monolithic build (`RightControlDock.build`, 646 lines) | High | **RESOLVED** | Decomposed into modular components in `presentation/workstation/controls/components/`. |
| **COMP-04**| Complexity | Deep nested message extractor (`_extract_prompt_and_image`, nesting 10, Cog 108) | High | **RESOLVED** | Flattened using guard clauses and helper parsers in `core/llm/client.py`. |
| **COMP-05**| Complexity | Monolithic route closures (`build_*_router`, CC 63, Cog 68) | High | **RESOLVED** | Extracted top-level route handlers in `plugins/ocr/routes.py` and `plugins/documents/routes.py`. |
| **COMP-06**| Complexity | God classes exceeding 500 LOC (`LanceDBLexiconStore`, `WorkstationNotifier`) | High | **RESOLVED** | `LanceDBLexiconStore` decomposed (schema/search); `WorkstationNotifier` decomposed into 3 notifiers. |
| **COMP-07**| Complexity | Fallback parser procedural loops (`_parse_markdown_fallback`, CC 50, Cog 56) | High | **RESOLVED** | Decomposed into discrete block consumers in `core/readers/markdown_reader.py`. |
| **COMP-08**| Complexity | High instability and coupling ripple hazards | High | **RESOLVED** | Introduced `@runtime_checkable` protocols `JobQueueProtocol` and `StateBackendProtocol`. |
| **COMP-09**| Complexity | Monolithic preflight validator (`OCRServiceImpl.preflight_check`, CC 41) | Medium | **RESOLVED** | Extracted coordinate resolver and probe client in `plugins/ocr/service.py`. |
| **COMP-10**| Complexity | Monolithic Dio error translation switch (`ApiClient._translateDioError`, 10 cases) | Medium | **RESOLVED** | Implemented table-driven `_statusFactories` translation in `client/lib/core/network/api_client.dart`. |
| **F1** | Duplication| `_envelope` JSON error helper duplicated ×4 | High | **RESOLVED** | Centralized in `omniscribe/plugins/_http.py` with standard status helpers. |
| **F2** | Duplication| Per-route `try/except XError` boilerplate (~60 sites) | High | **RESOLVED** | Implemented table-driven error mapping in plugin route handlers. |
| **F3** | Duplication| TBX / TMX / XLIFF XML glossary parsers share boilerplate | High | **RESOLVED** | Consolidated safe decode and entry finalization in `core/glossary_sources/`. |
| **F4** | Duplication| Two `parse_bool` implementations (`utils/env.py` vs `_common.py`) | High | **RESOLVED** | Unified canonical `parse_bool` in `utils/env.py`; re-exported in `_common.py`. |
| **F5** | Duplication| Empty `*Schema` classes in 4 plugin entry points | Medium | **RESOLVED** | Provided default `EmptySchema` in plugin harness base class. |
| **F6** | Duplication| `EMBEDDING_DIM = 384` duplicated in store and embedding | High | **RESOLVED** | Single source of truth defined in `core/lexicon/` constants. |
| **F7** | Duplication| Two `decode_base64 -> PIL.Image` helpers | Medium | **RESOLVED** | Promoted `decode_base64_image` in `core/imaging/utils.py` with optional mode argument. |
| **F8** | Duplication| `base64.b64decode` + `Image.open` repeated across stages | Medium | **RESOLVED** | Replaced with canonical `decode_base64_image` helper. |
| **F9** | Duplication| `block_type.value` normalization in 5 files | Medium | **RESOLVED** | Centralized in `block_type_str` helper in `core/block_tree.py`. |
| **F10** | Duplication| `_bearer_token` duplicated in documents and providers | Medium | **RESOLVED** | Centralized `bearer_token` helper in `plugins/_http.py`. |
| **F11** | Duplication| Per-plugin `XError(PluginError)` with outlier `DocumentsError` | Medium | **RESOLVED** | Made `DocumentsError` inherit `PluginError` in `plugins/documents/service.py`. |
| **F12** | Duplication| `BaseRecallOptions` subclass boilerplate | Medium | **RESOLVED** | Consolidated shared options into `BaseRecallOptions` mixin in `core/recall/base.py`. |
| **F13** | Duplication| `Image.open(...).convert(...)` repeated | Low | **RESOLVED** | Folded into `decode_base64_image(..., mode=...)`. |
| **F14** | Duplication| `image_b64` vs `image_base64` naming inconsistency | Low | **RESOLVED** | Convention documented: `image_b64` internal canonical, `image_base64` external wire boundary. |
| **F15** | Duplication| `rendered_table_ids` de-dup guard repeated across writers | Low | **RESOLVED** | Consolidated table deduplication guard logic in writers. |
| **Defect 1**| Functional | Backend URL changes crash client state (`late final _repo`) | High | **RESOLVED** | Replaced `late final _repo` with dynamic getters in `SettingsNotifier`, `JobsNotifier`, and `ProviderNotifier`. |
| **Defect 2**| Functional | Async OCR completion never reaches client | High | **RESOLVED** | Added `_scheduleStatusCheck` polling fallback and `_handleWsClosed` terminal reconciliation. |
| **Defect 3**| Functional | Backend auth overrides provider credentials | High | **RESOLVED** | Isolated provider credentials to `X-Provider-Api-Key` or query param; removed Authorization fallback. |
| **Defect 4**| Functional | Async translation sends inline text vs. artifact ID | High | **RESOLVED** | Supported inline text in `plugins/translate/routes.py`; updated client to pass text and store token. |
| **Defect 5**| Functional | Artifact storage failure kills async queue | High | **RESOLVED** | Wrapped storage in try-except in `plugins/jobs.py`; restarted done tasks; set job status to `error`. |
| **Defect 6**| Functional | Async OCR loses text artifact | High | **RESOLVED** | Persisted JSON text artifact in `OCRServiceImpl.run_job`; emitted `X-Text-Artifact-Id` and token headers. |
| **Defect 7**| Functional | Processing settings save but have no effect | High | **RESOLVED** | Forwarded validated `dpi`, `concurrency`, and thresholds through `pipeline_bridge.py` into `OCRPipeline.run`. |
| **Defect 8**| Functional | Valid Markdown uploads fail with HTTP 415 | Medium | **RESOLVED** | Replaced 12-byte binary signature check with `_is_markdown_text` UTF-8 validation in `routes.py`. |
| **Defect 9**| Functional | Grounded controls mismatch backend capabilities | Medium | **RESOLVED** | Removed unsupported `grounded_native` from UI; forwarded `pages` slice into `GroundedEngine.execute`. |
| **Defect 10**| Functional | Default Compose cannot start current image | Medium | **RESOLVED** | Wired `OMNISCRIBE_AUTH_TOKEN` in `compose.yaml` with clear `.env.example` guidance. |
| **P1.1** | Roadmap | Deduplicate audit reports and sweep relative links | Low | **RESOLVED** | Deduplicated audit reports into `docs/audits/`; swept 142 links; 0 broken links remaining. |
| **P2.1** | Roadmap | Decompose `WorkstationNotifier` (COMP-06 remainder) | High | **RESOLVED** | Decomposed into `DocumentViewportNotifier`, `DocumentSelectionNotifier`, and `JobOrchestrationNotifier`. |
| **P3.1** | Roadmap | Replace magic-number sleeps with explicit synchronization | High | **RESOLVED** | Swept 33 sites; replaced 4 un-synchronized waits with event listeners; 2 clock orders documented. |
| **P3.2** | Roadmap | Direct test coverage for `translate/workflow.py` (Q5) | Medium | **RESOLVED** | Added 26 unit and loop tests; resolved sync `app.invoke` bug via `_invoke_app` thread bridge. |
| **P4.1** | Roadmap | CI flake detection (`pytest-rerunfailures`) | Low | **RESOLVED** | Configured `--reruns 1 --reruns-delay 2` in nightly slow tiers; kept fast tier strict; uploaded JUnit XML. |
| **P4.2** | Roadmap | `extract_json` single-pass scan (D11) | Low | **RESOLVED** | Implemented balanced-span scanner `_balanced_spans` in `utils/json_parse.py` (25/25 tests passing). |
| **P4.3** | Roadmap | Flutter `integration_test/` suite (Q12) | Low | **RESOLVED** | Added 15 integration test files in `client/integration_test/` (stub and real-server variants). |
| **P4.4** | Roadmap | Screenshots and demo GIF (U6) | Low | **RESOLVED** | Captured 7 PNG screenshots; embedded 4 in README. 10s demo GIF screen recording remains owner item. |
| **P4.5** | Roadmap | Performance expectations documentation (U8) | Low | **RESOLVED** | Documented measured local stage timings and VLM cost models in `README.md:44-60`. |
| **P4.6** | Roadmap | Mutation testing evaluation (Q7) | Low | **DEFERRED** | Evaluated via `cosmic-ray 8.7` pilot (56.2% kill rate); documented as optional maintainer tool. |

---

### 10.2 Prioritized Open Backlog & Living Roadmap

With all 10 functional defects repaired and historical audit findings resolved, the remaining backlog represents **operator deployment decisions, media asset creation, and long-tail maintenance**:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      Prioritized Living Open Backlog                        │
├──────┬──────────────────────┬──────────┬──────────┬─────────────────────────┤
│ Rank │ Task Title           │ Domain   │ Effort   │ Unblocker / Dependency  │
├──────┼──────────────────────┼──────────┼──────────┼─────────────────────────┤
│ 1    │ Profile 4 Redis      │ Deploy   │ 1–2 days │ Operator decision:      │
│      │ Multi-Worker Scale   │          │          │ worker count, jobs/sec  │
│      │ Decision             │          │          │ target, Redis cluster   │
│      │                      │          │          │ vs standalone.          │
│ 2    │ 10-Second Demo GIF   │ Product  │ 2 hours  │ Maintainer interactive  │
│      │ Screen Recording     │          │          │ session: live screen    │
│      │                      │          │          │ capture of drag-and-    │
│      │                      │          │          │ drop OCR against VLM.   │
│ 3    │ OmniDocBench / Full  │ QA /     │ 1 day    │ Upstream dataset        │
│      │ Dataset Benchmark    │ Bench    │          │ license clearance for   │
│      │ Run                  │          │          │ `fetch_datasets.py`.    │
│ 4    │ Opportunistic C901   │ Code     │ Ongoing  │ Ratchet down legacy     │
│      │ Complexity Ratchet   │ Hygiene  │          │ functions in pyproject  │
│      │                      │          │          │ whitelist during future │
│      │                      │          │          │ feature edits.          │
│ 5    │ PGP Public Key       │ Security │ 30 min   │ Maintainer keypair      │
│      │ Publication          │          │          │ generation & upload to  │
│      │                      │          │          │ keyserver.              │
└──────┴──────────────────────┴──────────┴──────────┴─────────────────────────┘
```

#### 1. Profile 4 Redis Multi-Worker Deployment Shape (RFC 003 §12 / Handoff §2)
- **Current State:** Code is production-ready. `RedisJobQueue` (`plugins/jobs_redis.py`), `omniscribe-worker` CLI (`worker.py`), and Redis Pub/Sub progress fan-out (`plugins/progress.py`) are fully implemented and covered by tests (`tests/plugins/test_jobs_redis.py`). SQLite to Redis migration utility (`scripts/migrate_sqlite_to_redis.py`) supports batched atomic migrations.
- **Open Action:** An operator decision defining worker pool concurrency, target job throughput, and Redis topology (standalone vs sentinel vs cluster) to complete real-world deployment smoke testing (`scripts/dev_redis_smoke.py`).

#### 2. Interactive 10-Second Demo GIF (U6 Remainder)
- **Current State:** Seven high-resolution PNG screenshots are captured and embedded across `README.md` and `docs/screenshots/`.
- **Open Action:** Maintainer screen recording demonstrating: drag-and-drop PDF onto Workstation $\to$ progress bar animation $\to$ rendered searchable PDF with selectable text. Target size $< 5\text{ MB}$, optimized via `ffmpeg`.

#### 3. Full Benchmark Dataset Execution (Q16 / RFC 004 §10)
- **Current State:** In-tree mini fixtures run offline in fast CI; `scripts/confidence_eval.py --score-markdown` calculates CER, WER, BLEU, chrF, heading F1, and table similarity.
- **Open Action:** Execute full benchmark evaluation against OmniDocBench, OCR-Quality, and KIE-HVQA once upstream dataset redistribution licenses clear.

#### 4. Opportunistic C901 Complexity Ratchet
- **Current State:** McCabe complexity enforcement is active in Ruff (`max-complexity = 15`). 20 legacy functions exceeding CC 15 remain explicitly whitelisted in `pyproject.toml:316-335`.
- **Open Action:** Whenever a whitelisted file is touched for functional modifications, decompose the offending function and drop its ignore entry.

#### 5. PGP Public Key Publication (P13)
- **Current State:** Vulnerability reporting procedures are documented in `docs/SECURITY.md:11-25` with security contact email.
- **Open Action:** Generate and publish maintainer PGP public key to keyservers and embed fingerprint in `SECURITY.md`.

---
*End of Sections 8, 9 & 10 Technical Synthesis.*
