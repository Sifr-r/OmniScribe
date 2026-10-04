"""Sprint 4 (RFC 003) end-to-end smoke for the redis state backend.

This is a maintainer-run recipe, not part of CI. It exercises the
redis backend end-to-end against a real Redis server. The
``state_backend_redis.py`` pytest tests use fakeredis, which is
fast and in-process, but doesn't catch issues that only show up
against a real Redis (cluster routing, network failures,
persistence semantics, etc.).

Usage::

    # 1. Start a local Redis with no persistence (smoke only)
    redis-server --save "" --appendonly no --port 6379

    # 2. In one terminal, boot with Redis state AND Redis job dispatch
    OMNISCRIBE_STATE_BACKEND=redis \\
    OMNISCRIBE_JOBS_MODE=redis \\
    REDIS_URL=redis://localhost:6379/0 \\
    uv run omniscribe-server --port 8000

    # 3. For job checks, start a worker against that same broker
    REDIS_URL=redis://localhost:6379/0 uv run omniscribe-worker --concurrency 4

    # 4. In another terminal, run this recipe
    uv run python scripts/dev_redis_smoke.py --base http://127.0.0.1:8000

    # 5. After the script completes, verify with redis-cli
    redis-cli -p 6379 KEYS 'omniscribe:*'

The default checks health and Redis keys only. For concurrent worker jobs,
start the API with both OMNISCRIBE_STATE_BACKEND=redis and
OMNISCRIBE_JOBS_MODE=redis, start omniscribe-worker against the same Redis,
and configure the deployment's LLM endpoint/model/key. Then run::

    uv run python scripts/dev_redis_smoke.py --jobs 4 --timeout 600
    uv run python scripts/dev_redis_smoke.py --jobs 4 --verify-recovery --timeout 600

Export OMNISCRIBE_AUTH_TOKEN for an authenticated API. Credentials are never
printed and HTTP redirects are rejected. Use rediss:// for Redis TLS and put
Redis credentials in the URL. On the recovery cue, stop the identified worker
while its job is active and restart with a new worker ID. The smoke observes
the same job's lease moving to a different worker and checks its completed
token-bound HTTP/Redis artifact. It never controls deployment processes or
deletes Redis data. Jobs finishing before handoff fail recovery verification.
Choose a timeout longer than the deployment visibility timeout plus processing
time; a graceful drain may finish the job instead of exercising recovery.
Submitted jobs persist under the deployment's normal retention policy.

Opt-in live jobs (requires a configured translation model AND
OMNISCRIBE_STATE_BACKEND=redis, OMNISCRIBE_JOBS_MODE=redis on the API)::

    uv run python scripts/dev_redis_smoke.py --jobs 4 --timeout 180

Recovery: use a disposable deployment with one worker initially, matching
REDIS_URL, and ``omniscribe-worker --visibility-timeout 10``. Run with
``--jobs 2 --verify-recovery --timeout 180`` and sufficiently slow ``--text``.
On the printed lease cue, abruptly stop only that deployment's worker and
restart with a different worker ID (the default generates a fresh ID).
The script requires a different worker to claim the SAME job and complete its
token-bound artifact. It never controls a process or edits Redis. A handoff
proves recovery, not process provenance; the operator must ensure it came from
the intended restart. Fast completion before handoff fails. Graceful requeue
also qualifies; use an abrupt stop to exercise visibility-timeout recovery.
Jobs remain for inspection; normal artifact TTL applies. Printed timings
describe this run only, not production capacity or overlapping execution.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import redis.asyncio as redis_async

from omniscribe.plugins.jobs_redis import KEY_LEASE_PREFIX, KEY_PAYLOAD_PREFIX
from omniscribe.plugins.state_backend_redis import RedisStateBackend
from omniscribe.plugins.state_backend_types import StateBackend
from omniscribe.utils.security import redact_redis_url


class SmokeError(RuntimeError):
    """A prerequisite or verification failed."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        raise SmokeError("HTTP redirect rejected; credentials were not forwarded")


def _object(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise SmokeError("Expected JSON response") from exc
    if not isinstance(value, dict):
        raise SmokeError("Expected JSON object")
    return value


def _request_json(
    base: str,
    path: str,
    payload: dict[str, Any] | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    bearer = os.environ.get("OMNISCRIBE_AUTH_TOKEN", "").strip()
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    if token:
        headers["X-Artifact-Token"] = token
    request = urllib.request.Request(
        f"{base}{path}",
        headers=headers,
        data=json.dumps(payload).encode() if payload is not None else None,
    )
    try:
        opener = urllib.request.build_opener(_NoRedirect())
        with opener.open(request, timeout=5) as response:
            return _object(response.read())
    except urllib.error.HTTPError as exc:
        raise SmokeError(f"{path}: HTTP {exc.code}") from exc
    except (OSError, urllib.error.URLError) as exc:
        raise SmokeError(f"{path}: HTTP connection failed") from exc


def _hit_health(base: str, timeout_s: int = 30) -> None:
    """Confirm the server's /api/health returns 200."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if _request_json(base, "/api/health").get("status") == "ok":
                print("  /api/health OK")
                return
        except SmokeError:
            pass
        time.sleep(0.5)
    raise SmokeError(f"Health check timed out after {timeout_s}s")


async def _dump_keys(redis_url: str) -> int:
    """Print the omniscribe:* keyspace from Redis. Returns the count."""
    r = redis_async.from_url(redis_url, decode_responses=True, socket_timeout=5)
    try:
        keys = [
            k.decode("utf-8") if isinstance(k, bytes) else k
            async for k in r.scan_iter(match="omniscribe:*", count=100)
        ]
        print(f"  redis keyspace: {len(keys)} omniscribe:* keys")
        for k in sorted(keys)[:20]:
            ttl = await r.ttl(k)
            kind = await r.type(k)
            if isinstance(kind, bytes):
                kind = kind.decode("ascii")
            print(f"    {k}  (type={kind}, ttl={ttl}s)")
        return len(keys)
    finally:
        await r.aclose()


def _worker_identity(owner: str) -> str:
    # Worker leases are worker_id:loop_index:unique_claim_id.
    parts = owner.rsplit(":", 2)
    if len(parts) != 3 or not parts[0] or not parts[1].isdigit() or not parts[2]:
        raise SmokeError("Redis lease does not match the worker lease contract")
    return parts[0]


async def _verify_job(
    base: str,
    job_id: str,
    token: str,
    backend: StateBackend,
    r: redis_async.Redis,
    *,
    recovery: bool = False,
    poll_interval: float = 0.2,
) -> bool:
    first_worker: str | None = None
    handed_off = False
    queue_observed = False
    while True:
        owner = await r.get(f"{KEY_LEASE_PREFIX}{job_id}")
        queue_observed |= bool(owner) or bool(
            await r.exists(f"{KEY_PAYLOAD_PREFIX}{job_id}")
        )
        if recovery and owner:
            worker = _worker_identity(str(owner))
            if first_worker is None:
                first_worker = worker
                print(
                    f"  Recovery cue: job {job_id} claimed by {worker}; stop that "
                    "deployment's worker and restart with a new ID.",
                    flush=True,
                )
            elif worker != first_worker:
                handed_off = True
        status = await asyncio.to_thread(
            _request_json, base, f"/api/translate/status/{job_id}"
        )
        if status.get("job_id") != job_id:
            raise SmokeError(f"Job {job_id}: status returned a different job")
        state = status.get("state")
        if state == "FAILURE":
            raise SmokeError(f"Job {job_id}: terminal failure; check deployment logs")
        if state not in {"PENDING", "PROGRESS", "SUCCESS"}:
            raise SmokeError(f"Job {job_id}: unexpected HTTP job state")
        record = await backend.get_job(job_id)
        if record is None or record.request_meta.get("result_access_token") != token:
            raise SmokeError(f"Job {job_id}: server and Redis state backend disagree")
        if state == "SUCCESS":
            if recovery and not handed_off:
                raise SmokeError(
                    f"Job {job_id}: completed before a different worker's lease was observed; recovery unverified"
                )
            if record.status != "complete" or not (
                record.result_artifact_id and record.result_artifact_token
            ):
                raise SmokeError(
                    f"Job {job_id}: missing complete Redis artifact record"
                )
            result = await asyncio.to_thread(
                _request_json, base, f"/api/translate/result/{job_id}", None, token
            )
            text = result.get("translated_text")
            if not isinstance(text, str) or not text.strip():
                raise SmokeError(f"Job {job_id}: empty translated result")
            artifact = await backend.get_artifact(
                record.result_artifact_id, record.result_artifact_token
            )
            if (
                artifact is None
                or artifact.record.owner_job_id != job_id
                or _object(artifact.blob) != result
            ):
                raise SmokeError(
                    f"Job {job_id}: HTTP result and Redis artifact disagree"
                )
            print(f"  Job {job_id}: complete, token-bound Redis artifact verified")
            return queue_observed
        await asyncio.sleep(poll_interval)


async def _run_jobs(base: str, args: argparse.Namespace) -> None:
    backend = RedisStateBackend(args.redis_url)
    r = redis_async.from_url(args.redis_url, decode_responses=True, socket_timeout=5)
    started = time.monotonic()
    tasks: list[asyncio.Task[bool]] = []
    try:
        async with asyncio.timeout(args.timeout):
            payload = {"text": args.text, "target_language": args.target_language}
            submissions = await asyncio.gather(
                *(
                    asyncio.to_thread(
                        _request_json, base, "/api/translate/async", payload
                    )
                    for _ in range(args.jobs)
                )
            )
            handles: list[tuple[str, str]] = []
            for submission in submissions:
                job_id, token = submission.get("job_id"), submission.get("result_token")
                if (
                    not isinstance(job_id, str)
                    or re.fullmatch(r"[0-9a-f]{32}", job_id) is None
                    or not isinstance(token, str)
                    or not token
                ):
                    raise SmokeError(
                        "Submission omitted a valid job ID or result token"
                    )
                handles.append((job_id, token))
            if len({job_id for job_id, _ in handles}) != args.jobs:
                raise SmokeError("Concurrent submissions returned duplicate job IDs")
            tasks = [
                asyncio.create_task(
                    _verify_job(
                        base,
                        job_id,
                        token,
                        backend,
                        r,
                        recovery=args.verify_recovery and index == 0,
                    )
                )
                for index, (job_id, token) in enumerate(handles)
            ]
            observed = await asyncio.gather(*tasks)
            if not any(observed):
                raise SmokeError(
                    "No Redis queue payload or lease observed; queue mode unverified"
                )
    except TimeoutError as exc:
        raise SmokeError(f"Jobs/recovery timed out after {args.timeout}s") from exc
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await r.aclose()
        await backend.aclose()
    elapsed = time.monotonic() - started
    print(
        f"  {args.jobs} jobs in {elapsed:.2f}s ({args.jobs / elapsed:.2f} completed/s; this run only)"
    )
    if args.verify_recovery:
        print(
            "  Recovery verified: same job handed to a different worker and completed"
        )


def _validate(args: argparse.Namespace) -> None:
    base = urllib.parse.urlsplit(args.base)
    redis_url = urllib.parse.urlsplit(args.redis_url)
    if (
        base.scheme not in {"http", "https"}
        or not base.hostname
        or base.username is not None
        or base.password is not None
        or base.query
        or base.fragment
    ):
        raise SmokeError(
            "--base must be an HTTP(S) URL without credentials, query or fragment"
        )
    if redis_url.scheme not in {"redis", "rediss"} or not redis_url.hostname:
        raise SmokeError("--redis-url must be a redis:// or rediss:// URL")
    if not 0 <= args.jobs <= 100 or not 1 <= args.port <= 65535:
        raise SmokeError("--jobs must be 0..100 and --port must be 1..65535")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        raise SmokeError("--timeout must be finite and positive")
    if not args.text.strip() or not 1 <= len(args.target_language.strip()) <= 80:
        raise SmokeError(
            "--text and --target-language must be nonempty (language <= 80 characters)"
        )
    if args.verify_recovery and args.jobs == 0:
        raise SmokeError("--verify-recovery requires --jobs > 0")


async def main_async(args: argparse.Namespace) -> int:
    _validate(args)
    base = args.base.rstrip("/")
    print(
        f"Smoke-testing {base} with redis backend at {redact_redis_url(args.redis_url)}"
    )
    print()

    print("[1/3] Health check")
    await asyncio.to_thread(_hit_health, base)
    print()

    print("[2/3] Inspecting Redis keyspace after server boot")
    n = await _dump_keys(args.redis_url)
    if n == 0:
        print(
            "  NOTE: 0 keys yet — that's expected. The redis backend "
            "writes to Redis on the first job / artifact / channel "
            "operation. Submit a job via the Workstation to see keys "
            "appear; rerun this script after that to confirm."
        )
    print()

    if args.jobs:
        print("[3/3] Concurrent jobs and artifacts")
        await _run_jobs(base, args)
        return 0
    print("[3/3] Done (probe only; use --jobs 4 for live jobs)")
    print("  Manual verification next:")
    print(f"    redis-cli -p {args.port} KEYS 'omniscribe:*'")
    print(f"    redis-cli -p {args.port} GET omniscribe:job:<id>")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--base", default="http://127.0.0.1:8000", help="server base URL"
    )
    parser.add_argument(
        "--redis-url",
        default="redis://localhost:6379/0",
        help="redis URL the server was started with",
    )
    parser.add_argument("--port", type=int, default=6379, help="redis port (for hints)")
    parser.add_argument(
        "--jobs",
        type=int,
        default=0,
        help="concurrent translation jobs (0 = probe only)",
    )
    parser.add_argument(
        "--text", default="Hello from the OmniScribe Redis smoke check."
    )
    parser.add_argument("--target-language", default="Spanish")
    parser.add_argument(
        "--timeout",
        type=float,
        default=120,
        help="total jobs/recovery deadline in seconds",
    )
    parser.add_argument(
        "--verify-recovery",
        action="store_true",
        help="observe operator-triggered worker handoff for the first job",
    )
    args = parser.parse_args()
    try:
        return asyncio.run(main_async(args))
    except (SmokeError, redis_async.RedisError, ValueError) as exc:
        detail = str(exc) if isinstance(exc, SmokeError) else type(exc).__name__
        print(f"Smoke FAILED: {detail}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
