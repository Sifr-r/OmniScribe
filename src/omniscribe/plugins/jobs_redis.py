"""Redis ``JobQueue`` implementation (multi-worker dispatch over Redis).

Provides :class:`RedisJobQueue` adhering to the :class:`JobQueue` Protocol
from :mod:`omniscribe.plugins.jobs`. Completes RFC 003 §12 and RFC 004 §4 R3.

Data Model:
- ``omniscribe:jobs:queue`` (ZSET: score = timestamp) for pending jobs.
- ``omniscribe:jobs:active`` (ZSET: score = claim_time + visibility_timeout) for claimed jobs.
- ``omniscribe:jobs:payload:{id}`` (STRING JSON) for serialized job payload.
- ``omniscribe:jobs:heartbeat:{worker_id}`` (STRING with TTL) for worker liveness.
- ``omniscribe:jobs:attempts:{id}`` (STRING counter) for retry attempts.
- ``omniscribe:jobs:cancelled`` (SET) for cancelled job IDs.
- ``omniscribe:jobs:control`` (Pub/Sub) for real-time cancellation signals across workers.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
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

_LOGGER = logging.getLogger("omniscribe.plugins.jobs_redis")

#: Redis key names and prefixes.
KEY_QUEUE = "omniscribe:jobs:queue"
KEY_ACTIVE = "omniscribe:jobs:active"
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
        local payload_raw = redis.call('GET', payload_prefix .. job_id)
        return {job_id, payload_raw or ''}
    else
        redis.call('DEL', payload_prefix .. job_id)
    end
end
"""


def _decode_str(val: Any) -> str:
    """Decode bytes or other types to clean UTF-8 string."""
    if isinstance(val, bytes):
        return val.decode("utf-8")
    return str(val)


def serialize_payload(payload: Any) -> str:
    """Serialize an arbitrary job payload into a JSON envelope.

    Handles OCR payloads, translation payloads, glossary payloads,
    dataclasses, Pydantic models, and raw dicts/primitives.
    """
    # 1. OCR Payload
    if payload.__class__.__name__ == "_OcrPayload":
        req = getattr(payload, "request", None)
        req_dict = req.model_dump() if isinstance(req, BaseModel) else dict(req or {})
        return json.dumps({
            "__type__": "ocr",
            "submission_id": getattr(payload, "submission_id", ""),
            "input_path": str(getattr(payload, "input_path", "")),
            "filename": getattr(payload, "filename", ""),
            "request": req_dict,
        })

    # 2. Translation Payload
    if payload.__class__.__name__ == "_TranslatePayload":
        req = getattr(payload, "request", None)
        req_dict = req.model_dump() if isinstance(req, BaseModel) else dict(req or {})
        return json.dumps({
            "__type__": "translate",
            "submission_id": getattr(payload, "submission_id", ""),
            "request": req_dict,
        })

    # 3. Glossary Payload
    if payload.__class__.__name__ == "_GlossaryImportPayload":
        return json.dumps({
            "__type__": "glossary",
            "submission_id": getattr(payload, "submission_id", ""),
            "format_name": getattr(payload, "format_name", ""),
            "kwargs": getattr(payload, "kwargs", {}),
            "display_name": getattr(payload, "display_name", ""),
        })

    # 4. General Pydantic Model
    if isinstance(payload, BaseModel):
        return json.dumps({
            "__type__": "pydantic",
            "__module__": payload.__class__.__module__,
            "__qualname__": payload.__class__.__qualname__,
            "data": payload.model_dump(),
        })

    # 5. General Dataclass
    if is_dataclass(payload) and not isinstance(payload, type):
        runner_marker = getattr(type(payload), "runner_protocol", None)
        runner_marker_name = runner_marker.__name__ if runner_marker else None
        return json.dumps({
            "__type__": "dataclass",
            "__module__": payload.__class__.__module__,
            "__qualname__": payload.__class__.__qualname__,
            "runner_protocol": runner_marker_name,
            "data": asdict(payload),
        })

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
        )

    if payload_type == "translate":
        from omniscribe.plugins.translate.schemas import AsyncTranslationRequest
        from omniscribe.plugins.translate.service import _TranslatePayload

        return _TranslatePayload(
            submission_id=envelope.get("submission_id", ""),
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

    if payload_type == "pydantic":
        module_name = envelope.get("__module__", "")
        qualname = envelope.get("__qualname__", "")
        data = envelope.get("data", {})
        try:
            mod = importlib.import_module(module_name)
            cls = getattr(mod, qualname)
            return cls(**data)
        except Exception:
            _LOGGER.warning("Could not reconstruct Pydantic class %s.%s; returning dict", module_name, qualname)
            return data

    if payload_type == "dataclass":
        module_name = envelope.get("__module__", "")
        qualname = envelope.get("__qualname__", "")
        data = envelope.get("data", {})
        try:
            mod = importlib.import_module(module_name)
            cls = getattr(mod, qualname)
            return cls(**data)
        except Exception:
            _LOGGER.warning("Could not reconstruct dataclass %s.%s; returning dict", module_name, qualname)
            return data

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
            raise RuntimeError(f"Redis reachable check failed at {self._redis_url}: {exc}") from exc

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
        payload_json = serialize_payload(request).encode("utf-8")
        payload_key = f"{KEY_PAYLOAD_PREFIX}{job_id}"

        async with self._client.pipeline(transaction=True) as pipe:
            pipe.set(payload_key, payload_json)
            pipe.zadd(KEY_QUEUE, {job_id: now})
            await pipe.execute()

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
        """Clear all jobs from queue, active set, payloads, and StateBackend."""
        if self._redis is None:
            await self.open()

        count = await self._backend.clear_jobs()

        # Delete all queue data structures
        async with self._client.pipeline(transaction=True) as pipe:
            pipe.delete(KEY_QUEUE)
            pipe.delete(KEY_ACTIVE)
            pipe.delete(KEY_CANCELLED_SET)
            await pipe.execute()

        # Clean up payloads via scan
        cursor = 0
        while True:
            cursor, keys = await self._client.scan(
                cursor, match=f"{KEY_PAYLOAD_PREFIX}*", count=100
            )
            if keys:
                await self._client.delete(*keys)
            if cursor == 0:
                break

        self._cancelled.clear()
        return count

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
                    args=[active_score, job_prefix, KEY_PAYLOAD_PREFIX],
                )
            except Exception as exc:
                _LOGGER.warning("Claim Lua script execution failed (%s); using fallback", exc)
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
                )
            except Exception as exc:
                _LOGGER.debug("Direct Lua eval failed in claim (%s); falling back to WATCH", exc)
                return await self._claim_watch(active_score=active_score)

        if not result or not isinstance(result, (list, tuple)) or len(result) < 2:
            return None

        job_id = _decode_str(result[0])
        payload_raw = _decode_str(result[1])

        # Double check cancelled status
        if self.is_cancelled(job_id):
            await self.fail(job_id, error="Job was cancelled")
            return None

        payload = deserialize_payload(payload_raw)
        return job_id, payload

    async def _claim_watch(
        self,
        *,
        active_score: float,
    ) -> tuple[str, Any] | None:
        """WATCH/MULTI/EXEC fallback for atomic job claim when Lua is unavailable."""
        while not self._closed:
            try:
                async with self._client.pipeline() as pipe:
                    await pipe.watch(KEY_QUEUE, KEY_CANCELLED_SET)
                    items = await self._client.zrange(KEY_QUEUE, 0, 0)
                    if not items:
                        await pipe.unwatch()
                        return None
                    raw_jid = items[0]
                    job_id = _decode_str(raw_jid)

                    # Check cancelled
                    is_cancelled = bool(await self._client.sismember(KEY_CANCELLED_SET, job_id))
                    if not is_cancelled:
                        job_key = f"omniscribe:job:{job_id}"
                        job_raw = await self._client.get(job_key)
                        if job_raw:
                            try:
                                job_rec = json.loads(_decode_str(job_raw))
                                if job_rec.get("status") in ("cancelled", "complete", "error"):
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
                    payload_raw = await self._client.get(f"{KEY_PAYLOAD_PREFIX}{job_id}")

                    pipe.multi()
                    pipe.zrem(KEY_QUEUE, raw_jid)
                    pipe.zadd(KEY_ACTIVE, {job_id: active_score})
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

    async def complete(self, job_id: str) -> None:
        """Mark job complete: remove from active set and delete payload."""
        if self._redis is None:
            return
        async with self._client.pipeline(transaction=True) as pipe:
            pipe.zrem(KEY_ACTIVE, job_id)
            pipe.delete(f"{KEY_PAYLOAD_PREFIX}{job_id}")
            pipe.delete(f"{KEY_ATTEMPTS_PREFIX}{job_id}")
            await pipe.execute()

    async def fail(self, job_id: str, error: str = "") -> None:
        """Mark job failed: remove from active set and delete payload."""
        if self._redis is None:
            return
        async with self._client.pipeline(transaction=True) as pipe:
            pipe.zrem(KEY_ACTIVE, job_id)
            pipe.delete(f"{KEY_PAYLOAD_PREFIX}{job_id}")
            pipe.delete(f"{KEY_ATTEMPTS_PREFIX}{job_id}")
            await pipe.execute()

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
            await self._backend.upsert_job(replace(record, status="queued", updated_at=now))

    async def extend_visibility(self, job_id: str, extra_seconds: float) -> None:
        """Extend visibility timeout for a long-running active job."""
        if self._redis is None:
            return
        new_score = time.time() + extra_seconds
        await self._client.zadd(KEY_ACTIVE, {job_id: new_score}, xx=True)

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
            # Increment attempts
            attempts = await self._client.incr(f"{KEY_ATTEMPTS_PREFIX}{jid}")
            if attempts > max_retries:
                # Exceeded maximum retry threshold: fail the job
                async with self._client.pipeline(transaction=True) as pipe:
                    pipe.zrem(KEY_ACTIVE, jid)
                    pipe.delete(f"{KEY_PAYLOAD_PREFIX}{jid}")
                    pipe.delete(f"{KEY_ATTEMPTS_PREFIX}{jid}")
                    await pipe.execute()

                record = await self._backend.get_job(jid)
                err_msg = f"Visibility timeout exceeded ({max_retries} attempts exhausted)"
                if record is not None and record.status not in _TERMINAL_STATUSES:
                    await self._backend.upsert_job(
                        replace(record, status="error", error=err_msg, updated_at=time.time())
                    )
                await self._ctx.emit(JobFailed(job_id=jid, error=err_msg))
                recovered.append(jid)
            else:
                # Requeue job
                async with self._client.pipeline(transaction=True) as pipe:
                    pipe.zrem(KEY_ACTIVE, jid)
                    pipe.zadd(KEY_QUEUE, {jid: time.time()})
                    await pipe.execute()

                record = await self._backend.get_job(jid)
                if record is not None and record.status not in _TERMINAL_STATUSES:
                    await self._backend.upsert_job(
                        replace(record, status="queued", updated_at=time.time())
                    )
                await self._ctx.emit(JobQueued(job_id=jid))
                recovered.append(jid)

        return recovered

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
    "KEY_PAYLOAD_PREFIX",
    "KEY_QUEUE",
    "RedisJobQueue",
    "deserialize_payload",
    "serialize_payload",
]
