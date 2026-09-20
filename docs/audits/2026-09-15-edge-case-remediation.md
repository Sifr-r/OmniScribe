# Edge-Case Remediation Blueprint — 2026-09-15

| Area | Existing files to change | Responsibility |
| --- | --- | --- |
| Container reachability | `Dockerfile`, `compose.yaml` | Bind the service on the container interface while retaining host-loopback exposure. |
| Distributed jobs | `plugins/jobs_redis.py`, `worker.py`, OCR job payload/service | Preserve one active lease per job and make cancellation available to the worker process. |
| OCR lifecycle | `plugins/ocr/service.py`, `plugins/ocr/routes.py` | Retain previewable inputs for the job lifetime and use collision-resistant document cache identifiers. |
| Payment errors | hybrid OCR/refine/repair stages | Propagate permanent provider-balance failures rather than silently generating partial output. |
| Async translation | translation service/routes/plugin as needed | Deliver a usable, token-bound completed result without exposing tokens to unauthenticated polling. |
| Flutter lifecycle | WebSocket client and job notifier | Complete/cancel exactly one active run and discard stale asynchronous results. |

Each implementation must add or extend a focused regression test. No new production directory or abstraction is planned.
