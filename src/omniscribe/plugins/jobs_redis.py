"""Redis ``JobQueue`` implementation (multi-worker dispatch over Redis).

Provides :class:`RedisJobQueue` adhering to the :class:`JobQueue` Protocol
from :mod:`omniscribe.plugins.jobs`. Completes RFC 003 §12 and RFC 004 §4 R3.

Data Model:
- ``omniscribe:jobs:queue`` (ZSET: score = timestamp) for pending jobs.
- ``omniscribe:jobs:active`` (ZSET: score = claim_time + visibility_timeout) for claimed jobs.
- ``omniscribe:jobs:lease:{id}`` (STRING) identifies the worker that owns a claim.
- ``omniscribe:jobs:payload:{id}`` (STRING JSON) for serialized job payload.
- ``omniscribe:jobs:heartbeat:{worker_id}`` (STRING with TTL) for worker liveness.
- ``omniscribe:jobs:attempts:{id}`` (STRING counter) for retry attempts.
- ``omniscribe:jobs:cancelled`` (SET) for cancelled job IDs.
- ``omniscribe:jobs:control`` (Pub/Sub) for real-time cancellation signals across workers.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from dataclasses import asdict, is_dataclass, replace
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from omniscribe.harness.context import Context
from omniscribe.plugins.artifacts import ArtifactStore
from omniscribe.plugins.jobs import (
    _TERMINAL_STATUSES,
    JobCancelled,
    JobFailed,
    JobHandle,
    JobQueued,
)
from omniscribe.plugins.state_backend import (
    JobRecord,
    StateBackend,
)
from omniscribe.utils.security import redact_redis_url

_LOGGER = logging.getLogger("omniscribe.plugins.jobs_redis")

#: Redis key names and prefixes.
KEY_QUEUE = "omniscribe:jobs:queue"
KEY_ACTIVE = "omniscribe:jobs:active"
KEY_LEASE_PREFIX = "omniscribe:jobs:lease:"
KEY_PAYLOAD_PREFIX = "omniscribe:jobs:payload:"
KEY_HEARTBEAT_PREFIX = "omniscribe:jobs:heartbeat:"
KEY_ATTEMPTS_PREFIX = "omniscribe:jobs:attempts:"
KEY_CANCELLED_SET = "omniscribe:jobs:cancelled"
KEY_CONTROL_CHANNEL = "omniscribe:jobs:control"

#: Lua script for atomic job claiming from ``omniscribe:jobs:queue``.
#:
#: 1. Inspects the oldest job (lowest score) in the queue.
#: 2. Skips and purges any cancelled or terminal jobs from the queue.
#: 3. Atomically removes the valid job from ``omniscribe:jobs:queue``,
#:    adds it to ``omniscribe:jobs:active`` with score = claim_time + visibility_timeout,
#:    and returns {job_id, payload_json}.
#: 4. Returns nil if the queue is empty or no uncancelled jobs remain.
_CLAIM_JOB_LUA = """
local queue_key = KEYS[1]
local active_key = KEYS[2]
local cancelled_key = KEYS[3]
local active_score = tonumber(ARGV[1])
local job_prefix = ARGV[2]
local payload_prefix = ARGV[3]
local lease_prefix = ARGV[4]
local lease_owner = ARGV[5]

while true do
    local items = redis.call('ZRANGE', queue_key, 0, 0)
    if not items or #items == 0 then
        return nil
    end
    local job_id = items[1]
    redis.call('ZREM', queue_key, job_id)

    local is_cancelled = false
    if cancelled_key and redis.call('SISMEMBER', cancelled_key, job_id) == 1 then
        is_cancelled = true
    end

    if not is_cancelled then
        local job_key = job_prefix .. job_id
        local job_raw = redis.call('GET', job_key)
        if job_raw then
            local ok, job_rec = pcall(cjson.decode, job_raw)
            if ok and job_rec and (job_rec['status'] == 'cancelled' or job_rec['status'] == 'complete' or job_rec['status'] == 'error') then
                is_cancelled = true
            end
        end
    end

    if not is_cancelled then
        redis.call('ZADD', active_key, active_score, job_id)
        if lease_owner ~= '' then
            redis.call('SET', lease_prefix .. job_id, lease_owner)
        else
            redis.call('DEL', lease_prefix .. job_id)
        end
        local payload_raw = redis.call('GET', payload_prefix .. job_id)
        return {job_id, payload_raw or ''}
    else
        redis.call('DEL', payload_prefix .. job_id)
    end
end
"""

#: Extends a lease only when the caller still owns the current claim. A worker
#: that wakes after recovery must not revive or finish a newer worker's claim.
_RENEW_LEASE_LUA = """
local active_key = KEYS[1]
local lease_key = KEYS[2]
local job_id = ARGV[1]
local owner = ARGV[2]
local active_score = tonumber(ARGV[3])

if redis.call('GET', lease_key) ~= owner then
    return 0
end
if not redis.call('ZSCORE', active_key, job_id) then
    return 0
end
redis.call('ZADD', active_key, active_score, job_id)
return 1
"""

#: Removes an active lease only for its owner, preventing a late worker from
#: deleting the payload of a recovered claim.
_FINISH_LEASE_LUA = """
local active_key = KEYS[1]
local lease_key = KEYS[2]
local payload_key = KEYS[3]
local attempts_key = KEYS[4]
local owner = ARGV[1]

if redis.call('GET', lease_key) ~= owner then
    return 0
end
redis.call('ZREM', active_key, ARGV[2])
redis.call('DEL', lease_key)
redis.call('DEL', payload_key)
redis.call('DEL', attempts_key)
return 1
"""

#: Recovers a lease only when it is still expired at the point of mutation.
_RECOVER_STALE_JOB_LUA = """
local active_key = KEYS[1]
local queue_key = KEYS[2]
local lease_key = KEYS[3]
local payload_key = KEYS[4]
local attempts_key = KEYS[5]
local job_id = ARGV[1]
local now = tonumber(ARGV[2])
local max_retries = tonumber(ARGV[3])

local score = redis.call('ZSCORE', active_key, job_id)
if not score or tonumber(score) > now then
    return ''
end
if redis.call('ZREM', active_key, job_id) == 0 then
    return ''
end
redis.call('DEL', lease_key)
local attempts = redis.call('INCR', attempts_key)
if attempts > max_retries then
    redis.call('DEL', payload_key)
    redis.call('DEL', attempts_key)
    return 'failed'
end
redis.call('ZADD', queue_key, now, job_id)
return 'requeued'
"""


def _decode_str(val: Any) -> str:
    """Decode bytes or other types to clean UTF-8 string."""
    if isinstance(val, bytes):
        return val.decode("utf-8")
    return str(val)


def serialize_payload(payload: Any, *, job_id: str = "") -> str:
    """Serialize an arbitrary job payload into a JSON envelope.

    Handles OCR payloads, translation payloads, glossary payloads,
    dataclasses, Pydantic models, and raw dicts/primitives.
    """
    # 1. OCR Payload
    if payload.__class__.__name__ == "_OcrPayload":
        req = getattr(payload, "request", None)
        req_dict = req.model_dump() if isinstance(req, BaseModel) else dict(req or {})
        return json.dumps(
            {
                "__type__": "ocr",
                "submission_id": getattr(payload, "submission_id", ""),
                "job_id": job_id or getattr(payload, "job_id", ""),
                "input_path": str(getattr(payload, "input_path", "")),
                "filename": getattr(payload, "filename", ""),
                "request": req_dict,
            }
        )

    # 2. Translation Payload
    if payload.__class__.__name__ == "_TranslatePayload":
        req = getattr(payload, "request", None)
        req_dict = req.model_dump() if isinstance(req, BaseModel) else dict(req or {})
        return json.dumps(
            {
                "__type__": "translate",
                "submission_id": getattr(payload, "submission_id", ""),
                "job_id": job_id or getattr(payload, "job_id", ""),
                "request": req_dict,
            }
        )

    # 3. Glossary Payload
    if payload.__class__.__name__ == "_GlossaryImportPayload":
        return json.dumps(
            {
                "__type__": "glossary",
                "submission_id": getattr(payload, "submission_id", ""),
                "format_name": getattr(payload, "format_name", ""),
                "kwargs": getattr(payload, "kwargs", {}),
                "display_name": getattr(payload, "display_name", ""),
            }
        )

    # 4. General Pydantic Model
    if isinstance(payload, BaseModel):
        return json.dumps(
            {
                "__type__": "pydantic",
                "__module__": payload.__class__.__module__,
                "__qualname__": payload.__class__.__qualname__,
                "data": payload.model_dump(),
            }
        )

    # 5. General Dataclass
    if is_dataclass(payload) and not isinstance(payload, type):
        runner_marker = getattr(type(payload), "runner_protocol", None)
        runner_marker_name = runner_marker.__name__ if runner_marker else None
        return json.dumps(
            {
                "__type__": "dataclass",
                "__module__": payload.__class__.__module__,
                "__qualname__": payload.__class__.__qualname__,
                "runner_protocol": runner_marker_name,
                "data": asdict(payload),
            }
        )

    # 6. Raw dict or JSON-serializable structure
    return json.dumps({"__type__": "raw", "data": payload})


def deserialize_payload(raw_json: str) -> Any:
    """Deserialize a JSON envelope back into its domain payload instance."""
    if not raw_json:
        return None
    try:
        envelope = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return raw_json

    if not isinstance(envelope, dict) or "__type__" not in envelope:
        return envelope

    payload_type = envelope["__type__"]

    if payload_type == "ocr":
        from omniscribe.plugins.ocr.schemas import OCRRequest
        from omniscribe.plugins.ocr.service import _OcrPayload

        return _OcrPayload(
            submission_id=envelope.get("submission_id", ""),
            input_path=Path(envelope.get("input_path", "")),
            filename=envelope.get("filename", ""),
            request=OCRRequest(**envelope.get("request", {})),
            job_id=envelope.get("job_id", ""),
        )

    if payload_type == "translate":
        from omniscribe.plugins.translate.schemas import AsyncTranslationRequest
        from omniscribe.plugins.translate.service import _TranslatePayload

        return _TranslatePayload(
            submission_id=envelope.get("submission_id", ""),
            job_id=envelope.get("job_id", ""),
            request=AsyncTranslationRequest(**envelope.get("request", {})),
        )

    if payload_type == "glossary":
        from omniscribe.plugins.glossary.service import _GlossaryImportPayload

        return _GlossaryImportPayload(
            submission_id=envelope.get("submission_id", ""),
            format_name=envelope.get("format_name", ""),
            kwargs=envelope.get("kwargs", {}),
            display_name=envelope.get("display_name", ""),
        )

    if payload_type in ("pydantic", "dataclass"):
        # Allowlist reconstruction was never populated; keep the closed
        # deserialization path (no envelope-driven class instantiation)
        # and hand back the raw payload dict.
        module_name = envelope.get("__module__", "")
        qualname = envelope.get("__qualname__", "")
        _LOGGER.warning(
            "Refusing to reconstruct %s payload %s.%s; returning dict",
            payload_type,
            module_name,
            qualname,
        )
        return envelope.get("data", {})

    if payload_type == "raw":
        return envelope.get("data")

    return envelope


class RedisJobQueue:
    """Redis-backed multi-worker job queue implementing the :class:`JobQueue` Protocol.

    Provides distributed atomic queueing and claiming via Redis ZSETs and Lua,
    visibility timeouts with automated dead-worker recovery, and heartbeat monitoring.
    """

    def __init__(
        self,
        ctx: Context,
        backend: StateBackend,
        artifacts: ArtifactStore | None = None,
        *,
        redis_url: str = "redis://localhost:6379/0",
        redis_client: Any | None = None,
        visibility_timeout_seconds: float = 300.0,
        max_retries: int = 3,
    ) -> None:
        self._ctx = ctx
        self._backend = backend
        self._artifacts = artifacts
        self._redis_url = redis_url
        self._visibility_timeout_seconds = visibility_timeout_seconds
        self._max_retries = max_retries

        self._redis = redis_client
        self._owns_redis = redis_client is None
        self._cancelled: set[str] = set()
        self._claim_script: Any | None = None
        self._control_task: asyncio.Task[None] | None = None
        self._recovery_task: asyncio.Task[None] | None = None
        self._closed = False

    async def open(self) -> None:
        """Initialize Redis connection, load Lua script, and sync cancelled jobs."""
        if self._redis is None:
            import redis.asyncio as redis_async

            self._redis = redis_async.from_url(
                self._redis_url, encoding="utf-8", decode_responses=False
            )
        try:
            await self._redis.ping()
        except Exception as exc:
            raise RuntimeError(
                f"Redis reachable check failed at {redact_redis_url(self._redis_url)}: {exc}"
            ) from exc

        # Pre-load Lua claim script
        try:
            self._claim_script = self._redis.register_script(_CLAIM_JOB_LUA)
        except Exception:
            _LOGGER.warning("Could not register Lua claim script; will eval directly")
            self._claim_script = None

        # Synchronize local cancelled cache from Redis
        try:
            cancelled_members = await self._redis.smembers(KEY_CANCELLED_SET)
            for m in cancelled_members:
                self._cancelled.add(_decode_str(m))
        except Exception:
            pass

        # Start control channel listener
        if self._control_task is None:
            self._control_task = asyncio.create_task(
                self._listen_control_channel(), name="omniscribe-jobs-redis-control"
            )

    async def aclose(self) -> None:
        """Gracefully terminate background listeners and close Redis connection."""
        self._closed = True
        if self._control_task is not None:
            self._control_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._control_task
            self._control_task = None

        if self._recovery_task is not None:
            self._recovery_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._recovery_task
            self._recovery_task = None

        if self._owns_redis and self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    @property
    def _client(self) -> Any:
        """Return the active Redis client; raises if not opened."""
        if self._redis is None:
            raise RuntimeError("RedisJobQueue must be opened before use")
        return self._redis

    # -- JobQueue Protocol implementation --------------------------------------

    async def submit(
        self,
        request: Any,
        *,
        request_meta: dict[str, Any] | None = None,
        input_path: str | None = None,
    ) -> JobHandle:
        """Submit a job to the distributed Redis queue."""
        if self._redis is None:
            await self.open()

        job_id = uuid.uuid4().hex
        now = time.time()

        # 1. Record job in StateBackend
        await self._backend.upsert_job(
            JobRecord(
                job_id=job_id,
                status="queued",
                request_meta=dict(request_meta or {}),
                input_path=input_path,
                created_at=now,
                updated_at=now,
            )
        )

        # 2. Store payload and push to queue ZSET
        payload_json = serialize_payload(request, job_id=job_id).encode("utf-8")
        payload_key = f"{KEY_PAYLOAD_PREFIX}{job_id}"

        try:
            async with self._client.pipeline(transaction=True) as pipe:
                pipe.set(payload_key, payload_json)
                pipe.zadd(KEY_QUEUE, {job_id: now})
                await pipe.execute()
        except Exception:
            await self._backend.delete_job(job_id)
            raise

        # 3. Emit JobQueued event
        await self._ctx.emit(JobQueued(job_id=job_id))
        return JobHandle(job_id=job_id, status_url=f"/api/process/status/{job_id}")

    async def status(self, job_id: str) -> JobRecord | None:
        """Return the current JobRecord from the persistent StateBackend."""
        return await self._backend.get_job(job_id)

    async def cancel(self, job_id: str) -> bool:
        """Cancel a queued or active job across all workers."""
        if self._redis is None:
            await self.open()

        record = await self._backend.get_job(job_id)
        if record is None or record.status in _TERMINAL_STATUSES:
            return False

        # Mark in local cancelled cache
        self._cancelled.add(job_id)

        # Mark in Redis cancelled set and pop from queue
        async with self._client.pipeline(transaction=True) as pipe:
            pipe.sadd(KEY_CANCELLED_SET, job_id)
            pipe.zrem(KEY_QUEUE, job_id)
            pipe.publish(
                KEY_CONTROL_CHANNEL,
                json.dumps({"action": "cancel", "job_id": job_id}),
            )
            await pipe.execute()

        # If queued, transition to cancelled immediately
        if record.status == "queued":
            await self._backend.upsert_job(
                replace(record, status="cancelled", updated_at=time.time())
            )
            await self._ctx.emit(JobCancelled(job_id=job_id))

        return True

    def is_cancelled(self, job_id: str) -> bool:
        """Synchronous O(1) cancellation check matching the JobQueue Protocol."""
        return job_id in self._cancelled

    async def list_jobs(self, *, limit: int = 100, offset: int = 0) -> list[JobRecord]:
        """List paginated jobs ordered by created_at descending."""
        return await self._backend.list_jobs(limit=limit, offset=offset)

    async def clear(self) -> int:
        """Clear only terminal jobs from StateBackend and their payload keys."""
        if self._redis is None:
            await self.open()

        all_jobs: list[JobRecord] = []
        offset = 0
        while True:
            batch = await self._backend.list_jobs(limit=100, offset=offset)
            if not batch:
                break
            all_jobs.extend(batch)
            offset += len(batch)

        terminal_statuses = _TERMINAL_STATUSES | {"failed"}
        count = 0
        async with self._client.pipeline(transaction=True) as pipe:
            for job in all_jobs:
                if job.status in terminal_statuses:
                    await self._backend.delete_job(job.job_id)
                    pipe.delete(f"{KEY_PAYLOAD_PREFIX}{job.job_id}")
                    pipe.delete(f"{KEY_LEASE_PREFIX}{job.job_id}")
                    pipe.srem(KEY_CANCELLED_SET, job.job_id)
                    self._cancelled.discard(job.job_id)
                    count += 1
            if count > 0:
                await pipe.execute()

        return count

    # -- JobQueueProtocol aliases ----------------------------------------------
    enqueue = submit
    get_job = status
    cancel_job = cancel

    # -- Distributed Claim & Recovery Operations ------------------------------

    async def claim(
        self,
        *,
        worker_id: str = "",
        visibility_timeout: float | None = None,
    ) -> tuple[str, Any] | None:
        """Atomically claim the next queued job via Lua script.

        Transitions the claimed job from ``KEY_QUEUE`` to ``KEY_ACTIVE``
        with score = now + visibility_timeout. Returns (job_id, deserialized_payload)
        or None if no uncancelled jobs are pending.
        """
        if self._redis is None:
            await self.open()

        # Perform opportunistic recovery of stale jobs
        with contextlib.suppress(Exception):
            await self.recover_stale_jobs(max_retries=self._max_retries)

        timeout = (
            visibility_timeout
            if visibility_timeout is not None
            else self._visibility_timeout_seconds
        )
        active_score = time.time() + timeout
        job_prefix = "omniscribe:job:"

        result: Any = None
        claim_script = self._claim_script
        if claim_script is not None and callable(claim_script):
            try:
                result = await claim_script(
                    keys=[KEY_QUEUE, KEY_ACTIVE, KEY_CANCELLED_SET],
                    args=[
                        active_score,
                        job_prefix,
                        KEY_PAYLOAD_PREFIX,
                        KEY_LEASE_PREFIX,
                        worker_id,
                    ],
                )
            except Exception as exc:
                _LOGGER.warning(
                    "Claim Lua script execution failed (%s); using fallback", exc
                )
                self._claim_script = None

        if result is None:
            try:
                result = await self._client.eval(
                    _CLAIM_JOB_LUA,
                    3,
                    KEY_QUEUE,
                    KEY_ACTIVE,
                    KEY_CANCELLED_SET,
                    active_score,
                    job_prefix,
                    KEY_PAYLOAD_PREFIX,
                    KEY_LEASE_PREFIX,
                    worker_id,
                )
            except Exception as exc:
                _LOGGER.debug(
                    "Direct Lua eval failed in claim (%s); falling back to WATCH", exc
                )
                return await self._claim_watch(
                    active_score=active_score, worker_id=worker_id
                )

        if not result or not isinstance(result, (list, tuple)) or len(result) < 2:
            return None

        job_id = _decode_str(result[0])
        payload_raw = _decode_str(result[1])

        # Double check cancelled status
        if self.is_cancelled(job_id):
            await self.fail(
                job_id,
                error="Job was cancelled",
                lease_owner=worker_id or None,
            )
            return None

        payload = deserialize_payload(payload_raw)
        return job_id, payload

    async def _claim_watch(
        self,
        *,
        active_score: float,
        worker_id: str,
    ) -> tuple[str, Any] | None:
        """WATCH/MULTI/EXEC fallback for atomic job claim when Lua is unavailable."""
        while not self._closed:
            try:
                async with self._client.pipeline() as pipe:
                    await pipe.watch(KEY_QUEUE, KEY_CANCELLED_SET, KEY_ACTIVE)
                    items = await self._client.zrange(KEY_QUEUE, 0, 0)
                    if not items:
                        await pipe.unwatch()
                        return None
                    raw_jid = items[0]
                    job_id = _decode_str(raw_jid)

                    # Check cancelled
                    is_cancelled = bool(
                        await self._client.sismember(KEY_CANCELLED_SET, job_id)
                    )
                    if not is_cancelled:
                        job_key = f"omniscribe:job:{job_id}"
                        job_raw = await self._client.get(job_key)
                        if job_raw:
                            try:
                                job_rec = json.loads(_decode_str(job_raw))
                                if job_rec.get("status") in (
                                    "cancelled",
                                    "complete",
                                    "error",
                                ):
                                    is_cancelled = True
                            except Exception:
                                pass

                    if is_cancelled:
                        pipe.multi()
                        pipe.zrem(KEY_QUEUE, raw_jid)
                        pipe.delete(f"{KEY_PAYLOAD_PREFIX}{job_id}")
                        await pipe.execute()
                        continue

                    # Valid uncancelled job
                    payload_raw = await self._client.get(
                        f"{KEY_PAYLOAD_PREFIX}{job_id}"
                    )

                    pipe.multi()
                    pipe.zrem(KEY_QUEUE, raw_jid)
                    pipe.zadd(KEY_ACTIVE, {job_id: active_score})
                    lease_key = f"{KEY_LEASE_PREFIX}{job_id}"
                    if worker_id:
                        pipe.set(lease_key, worker_id)
                    else:
                        pipe.delete(lease_key)
                    await pipe.execute()

                    payload = (
                        deserialize_payload(_decode_str(payload_raw))
                        if payload_raw
                        else None
                    )
                    return job_id, payload
            except Exception as exc:
                if "WatchError" in exc.__class__.__name__:
                    await asyncio.sleep(0.001)
                    continue
                _LOGGER.warning("Error during _claim_watch: %s", exc)
                return None
        return None

    async def renew_lease(
        self, job_id: str, lease_owner: str, visibility_timeout: float
    ) -> bool:
        """Extend one active claim, returning ``False`` after ownership changes."""
        if self._redis is None or not lease_owner or visibility_timeout <= 0:
            return False
        active_score = time.time() + visibility_timeout
        lease_key = f"{KEY_LEASE_PREFIX}{job_id}"
        try:
            result = await self._client.eval(
                _RENEW_LEASE_LUA,
                2,
                KEY_ACTIVE,
                lease_key,
                job_id,
                lease_owner,
                active_score,
            )
            return bool(int(result))
        except Exception as exc:
            _LOGGER.debug("Lease renewal Lua unavailable (%s); using WATCH", exc)

        while not self._closed:
            try:
                async with self._client.pipeline() as pipe:
                    await pipe.watch(KEY_ACTIVE, lease_key)
                    owner = await self._client.get(lease_key)
                    score = await self._client.zscore(KEY_ACTIVE, job_id)
                    if _decode_str(owner or "") != lease_owner or score is None:
                        await pipe.unwatch()
                        return False
                    pipe.multi()
                    pipe.zadd(KEY_ACTIVE, {job_id: active_score})
                    await pipe.execute()
                    return True
            except Exception as exc:
                if "WatchError" in exc.__class__.__name__:
                    continue
                _LOGGER.warning("Lease renewal fallback failed for %s: %s", job_id, exc)
                return False
        return False

    async def owns_lease(self, job_id: str, lease_owner: str) -> bool:
        """Return whether ``lease_owner`` still owns ``job_id``'s active lease."""
        if self._redis is None or not lease_owner:
            return False
        return (
            _decode_str(await self._client.get(f"{KEY_LEASE_PREFIX}{job_id}") or "")
            == lease_owner
        )

    async def _finish(self, job_id: str, lease_owner: str | None) -> bool:
        if not lease_owner:
            async with self._client.pipeline(transaction=True) as pipe:
                pipe.zrem(KEY_ACTIVE, job_id)
                pipe.delete(f"{KEY_LEASE_PREFIX}{job_id}")
                pipe.delete(f"{KEY_PAYLOAD_PREFIX}{job_id}")
                pipe.delete(f"{KEY_ATTEMPTS_PREFIX}{job_id}")
                await pipe.execute()
            return True
        lease_key = f"{KEY_LEASE_PREFIX}{job_id}"
        payload_key = f"{KEY_PAYLOAD_PREFIX}{job_id}"
        attempts_key = f"{KEY_ATTEMPTS_PREFIX}{job_id}"
        try:
            result = await self._client.eval(
                _FINISH_LEASE_LUA,
                4,
                KEY_ACTIVE,
                lease_key,
                payload_key,
                attempts_key,
                lease_owner,
                job_id,
            )
            return bool(int(result))
        except Exception as exc:
            _LOGGER.debug("Lease finish Lua unavailable (%s); using WATCH", exc)

        while not self._closed:
            try:
                async with self._client.pipeline() as pipe:
                    await pipe.watch(KEY_ACTIVE, lease_key)
                    if (
                        _decode_str(await self._client.get(lease_key) or "")
                        != lease_owner
                    ):
                        await pipe.unwatch()
                        return False
                    pipe.multi()
                    pipe.zrem(KEY_ACTIVE, job_id)
                    pipe.delete(lease_key)
                    pipe.delete(payload_key)
                    pipe.delete(attempts_key)
                    await pipe.execute()
                    return True
            except Exception as exc:
                if "WatchError" in exc.__class__.__name__:
                    continue
                _LOGGER.warning("Lease finish fallback failed for %s: %s", job_id, exc)
                return False
        return False

    async def complete(self, job_id: str, *, lease_owner: str | None = None) -> bool:
        """Mark job complete: remove from active set and delete payload."""
        if self._redis is None:
            return False
        return await self._finish(job_id, lease_owner)

    async def fail(
        self, job_id: str, error: str = "", *, lease_owner: str | None = None
    ) -> bool:
        """Mark job failed: remove from active set and delete payload."""
        if self._redis is None:
            return False
        return await self._finish(job_id, lease_owner)

    async def requeue(self, job_id: str) -> None:
        """Requeue an active job back into the pending queue (e.g. on worker drain)."""
        if self._redis is None:
            return
        now = time.time()
        async with self._client.pipeline(transaction=True) as pipe:
            pipe.zrem(KEY_ACTIVE, job_id)
            pipe.zadd(KEY_QUEUE, {job_id: now})
            await pipe.execute()

        record = await self._backend.get_job(job_id)
        if record is not None and record.status not in _TERMINAL_STATUSES:
            await self._backend.upsert_job(
                replace(record, status="queued", updated_at=now)
            )

    async def heartbeat(self, worker_id: str, ttl_seconds: int = 30) -> None:
        """Publish worker liveness heartbeat to Redis with TTL."""
        if self._redis is None:
            return
        key = f"{KEY_HEARTBEAT_PREFIX}{worker_id}"
        await self._client.set(key, str(time.time()), ex=ttl_seconds)

    async def recover_stale_jobs(
        self, *, max_retries: int = 3, now: float | None = None
    ) -> list[str]:
        """Detect and recover abandoned jobs whose visibility timeout expired.

        If a worker crashes or drops offline without completing a job:
        - If attempts < max_retries: job is requeued into ``omniscribe:jobs:queue``.
        - If attempts >= max_retries: job is transitioned to ``error`` and evicted.
        """
        if self._redis is None:
            return []

        current_time = time.time() if now is None else now
        expired_ids = await self._client.zrangebyscore(KEY_ACTIVE, "-inf", current_time)
        if not expired_ids:
            return []

        recovered: list[str] = []
        for raw_id in expired_ids:
            jid = _decode_str(raw_id)
            outcome = await self._recover_expired_job(
                jid, current_time=current_time, max_retries=max_retries
            )
            if not outcome:
                continue
            now = time.time()
            record = await self._backend.get_job(jid)
            if outcome == "failed":
                err_msg = (
                    f"Visibility timeout exceeded ({max_retries} attempts exhausted)"
                )
                if record is not None and record.status not in _TERMINAL_STATUSES:
                    await self._backend.upsert_job(
                        replace(record, status="error", error=err_msg, updated_at=now)
                    )
                await self._ctx.emit(JobFailed(job_id=jid, error=err_msg))
            else:
                if record is not None and record.status not in _TERMINAL_STATUSES:
                    await self._backend.upsert_job(
                        replace(record, status="queued", updated_at=now)
                    )
                await self._ctx.emit(JobQueued(job_id=jid))
            recovered.append(jid)

        return recovered

    async def _recover_expired_job(
        self, job_id: str, *, current_time: float, max_retries: int
    ) -> str:
        """Atomically recover ``job_id`` if its active lease remains expired."""
        lease_key = f"{KEY_LEASE_PREFIX}{job_id}"
        payload_key = f"{KEY_PAYLOAD_PREFIX}{job_id}"
        attempts_key = f"{KEY_ATTEMPTS_PREFIX}{job_id}"
        try:
            result = await self._client.eval(
                _RECOVER_STALE_JOB_LUA,
                5,
                KEY_ACTIVE,
                KEY_QUEUE,
                lease_key,
                payload_key,
                attempts_key,
                job_id,
                current_time,
                max_retries,
            )
            return _decode_str(result) if result else ""
        except Exception as exc:
            _LOGGER.debug("Recovery Lua unavailable (%s); using WATCH", exc)

        while not self._closed:
            try:
                async with self._client.pipeline() as pipe:
                    await pipe.watch(KEY_ACTIVE, lease_key)
                    score = await self._client.zscore(KEY_ACTIVE, job_id)
                    if score is None or float(score) > current_time:
                        await pipe.unwatch()
                        return ""
                    attempts_raw = await self._client.get(attempts_key)
                    attempts = int(_decode_str(attempts_raw or "0")) + 1
                    outcome = "failed" if attempts > max_retries else "requeued"
                    pipe.multi()
                    pipe.zrem(KEY_ACTIVE, job_id)
                    pipe.delete(lease_key)
                    pipe.incr(attempts_key)
                    if outcome == "failed":
                        pipe.delete(payload_key)
                        pipe.delete(attempts_key)
                    else:
                        pipe.zadd(KEY_QUEUE, {job_id: current_time})
                    await pipe.execute()
                    return outcome
            except Exception as exc:
                if "WatchError" in exc.__class__.__name__:
                    continue
                _LOGGER.warning("Recovery fallback failed for %s: %s", job_id, exc)
                return ""
        return ""

    # -- Internal pub/sub listener --------------------------------------------

    async def _listen_control_channel(self) -> None:
        """Subscribe to Redis control channel to synchronize cancellation signals."""
        if self._redis is None:
            return
        try:
            pubsub = self._client.pubsub()
            await pubsub.subscribe(KEY_CONTROL_CHANNEL)
            while not self._closed:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if message and message.get("type") == "message":
                    data_str = _decode_str(message.get("data"))
                    try:
                        data = json.loads(data_str)
                        if data.get("action") == "cancel":
                            self._cancelled.add(str(data.get("job_id", "")))
                    except Exception:
                        pass
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            if not self._closed:
                _LOGGER.warning("Redis control channel listener exited: %s", exc)


__all__ = [
    "KEY_ACTIVE",
    "KEY_CANCELLED_SET",
    "KEY_CONTROL_CHANNEL",
    "KEY_HEARTBEAT_PREFIX",
    "KEY_LEASE_PREFIX",
    "KEY_PAYLOAD_PREFIX",
    "KEY_QUEUE",
    "RedisJobQueue",
    "deserialize_payload",
    "serialize_payload",
]
