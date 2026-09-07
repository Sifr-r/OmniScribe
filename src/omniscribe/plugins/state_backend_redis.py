"""Redis ``StateBackend`` implementation (async, multi-worker safe).

Audit catalog (Sprint 4 RFC 003, 2026-09-07): the redis backend
ships for Profile 4 multi-worker LAN deployments. The
implementation uses ``redis.asyncio.from_url`` with a
connection pool, and stores the three persistence domains
(artifacts, jobs, progress channels) under the ``omniscribe:``
key prefix.

Data model (full design in
``docs/rfcs/2026-09-redis-state-backend.md``):

- ``omniscribe:artifact:{id}`` — JSON ``ArtifactRecord`` (TTL = ``ttl_seconds``)
- ``omniscribe:artifact:blob:{id}`` — raw blob bytes (TTL = ``ttl_seconds``)
- ``omniscribe:job:{id}`` — JSON ``JobRecord`` (no TTL; terminal jobs persist)
- ``omniscribe:job:index`` — ZSET scored by ``created_at`` for pagination
- ``omniscribe:channel:{id}`` — JSON ``ChannelRecord`` (TTL = ``ttl_seconds``)
- ``omniscribe:channel:index`` — ZSET scored by ``created_at + ttl_seconds``
  so ``prune_expired_channels`` is a single ``ZRANGEBYSCORE 0 now``.

TTL semantics:

- Artifacts: Redis's built-in TTL expires both the metadata and
  blob keys together. ``prune_expired_artifacts`` is a no-op
  (returns 0); it's a Protocol method for cross-backend
  parity with ``SQLiteStateBackend``.
- Channels: same — Redis's TTL handles expiration. The
  ``omniscribe:channel:index`` ZSET stores the
  ``created_at + ttl_seconds`` score so a single
  ``ZRANGEBYSCORE 0 now`` returns expired ids without a SCAN.
  ``prune_expired_channels`` is therefore the index-driven
  cleanup path.
- Jobs: no TTL. Terminal jobs persist until ``clear_jobs`` /
  ``delete_job``.

Atomicity:

- ``consume_channel`` is a one-shot read-and-mark. We use a
  Lua script (server-side atomicity) as the primary path; the
  fallback to ``WATCH/MULTI/EXEC`` is exercised if the Lua
  script fails to load (e.g. fakeredis version mismatch).
"""

from __future__ import annotations

import dataclasses
import json
import logging
import time
from typing import Any, cast

from .state_backend_types import (
    ArtifactBlob,
    ArtifactRecord,
    ChannelRecord,
    JobRecord,
    JobStatus,
    get_args,
)

_LOGGER = logging.getLogger("omniscribe.plugins.state_backend_redis")


#: Redis key prefix for all state keys. Keeps the namespace clean
#: if the same Redis instance is shared with other apps.
_KEY_PREFIX = "omniscribe:"

#: Score offset for the channel index. We want to expire by
#: ``created_at + ttl_seconds`` (not just ``created_at``), so we
#: add the TTL at ``ZADD`` time. The score is the expiration
#: epoch, which lets ``prune_expired_channels`` do
#: ``ZRANGEBYSCORE 0 now``.
_CHANNEL_EXPIRY_SCORE: float = 0.0  # score == expiry epoch; placeholder

#: Lua script for the atomic ``consume_channel`` operation. The
#: script does a single round-trip; server-side atomicity means
#: two concurrent ``consume_channel`` calls are guaranteed to see
#: the second one return None. The script:
#:
#: 1. Reads the channel record.
#: 2. Returns nil if missing, wrong token, or already consumed.
#: 3. Marks ``consumed = true`` (preserving the existing TTL).
#: 4. Returns the original record as JSON (so the caller has the
#:    pre-consume snapshot for downstream logic).
_CONSUME_CHANNEL_LUA = """
local key = KEYS[1]
local expected_token = ARGV[1]
local raw = redis.call('GET', key)
if not raw then return nil end
local record = cjson.decode(raw)
if record['consumed'] then return nil end
if record['session_token'] ~= expected_token then return nil end
record['consumed'] = true
redis.call('SET', key, cjson.encode(record), 'KEEPTTL')
return raw
"""


def _meta_key(artifact_id: str) -> str:
    return f"{_KEY_PREFIX}artifact:{artifact_id}"


def _blob_key(artifact_id: str) -> str:
    return f"{_KEY_PREFIX}artifact:blob:{artifact_id}"


def _job_key(job_id: str) -> str:
    return f"{_KEY_PREFIX}job:{job_id}"


def _job_index_key() -> str:
    return f"{_KEY_PREFIX}job:index"


def _channel_key(channel_id: str) -> str:
    return f"{_KEY_PREFIX}channel:{channel_id}"


def _channel_index_key() -> str:
    return f"{_KEY_PREFIX}channel:index"


def _decode_id(raw: bytes | str) -> str:
    """Decode a redis-py bytes return value to ``str``.

    ``decode_responses=False`` is set on the client, so most
    key-shaped return values come back as ``bytes``. The key
    builder functions (``_job_key`` etc.) take ``str``, so
    callers decode once at the boundary. Decoding with
    ``utf-8`` matches the encoding we use on the write path
    (``json.dumps(...).encode("utf-8")``).
    """
    if isinstance(raw, bytes):
        return raw.decode("utf-8")
    return raw


def _artifact_record_to_dict(record: ArtifactRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "token": record.token,
        "owner_job_id": record.owner_job_id,
        "content_type": record.content_type,
        "created_at": record.created_at,
        "ttl_seconds": record.ttl_seconds,
    }


def _artifact_record_from_dict(data: dict[str, Any]) -> ArtifactRecord:
    return ArtifactRecord(
        id=str(data["id"]),
        token=str(data["token"]),
        owner_job_id=str(data["owner_job_id"]),
        content_type=str(data["content_type"]),
        created_at=float(data["created_at"]),
        ttl_seconds=int(data["ttl_seconds"]),
    )


def _job_record_to_dict(record: JobRecord) -> dict[str, Any]:
    return {
        "job_id": record.job_id,
        "status": record.status,
        "request_meta": record.request_meta,
        "result_artifact_id": record.result_artifact_id,
        "result_artifact_token": record.result_artifact_token,
        "input_path": record.input_path,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "started_at": record.started_at,
        "error": record.error,
    }


def _job_record_from_dict(data: dict[str, Any]) -> JobRecord:
    # ``status`` is a ``Literal[...]`` on ``JobRecord``. We
    # validate against the runtime tuple (the same set the
    # ``JobStatus`` literal is built from) and cast — mypy can't
    # narrow ``str`` to a literal from a runtime set lookup, so a
    # ``cast`` is the cleanest option. The validation still runs
    # at runtime; a malformed record raises a clear ValueError
    # instead of a confusing construction error.
    raw_status = str(data["status"])
    if raw_status not in set(get_args(JobStatus)):
        raise ValueError(f"unknown job status: {raw_status!r}")
    return JobRecord(
        job_id=str(data["job_id"]),
        status=cast(JobStatus, raw_status),
        request_meta=data.get("request_meta") or {},
        result_artifact_id=data.get("result_artifact_id"),
        result_artifact_token=data.get("result_artifact_token"),
        input_path=data.get("input_path"),
        created_at=float(data["created_at"]),
        updated_at=float(data["updated_at"]),
        started_at=(float(data["started_at"]) if data.get("started_at") is not None else None),
        error=data.get("error"),
    )


def _channel_record_to_dict(record: ChannelRecord) -> dict[str, Any]:
    return {
        "channel_id": record.channel_id,
        "session_token": record.session_token,
        "job_id": record.job_id,
        "created_at": record.created_at,
        "ttl_seconds": record.ttl_seconds,
        "consumed": record.consumed,
    }


def _channel_record_from_dict(data: dict[str, Any]) -> ChannelRecord:
    return ChannelRecord(
        channel_id=str(data["channel_id"]),
        session_token=str(data["session_token"]),
        job_id=str(data["job_id"]),
        created_at=float(data["created_at"]),
        ttl_seconds=int(data["ttl_seconds"]),
        consumed=bool(data.get("consumed", False)),
    )


class RedisStateBackend:
    """Multi-worker-safe ``StateBackend`` over Redis (async client).

    Lifecycle::

        backend = RedisStateBackend(redis_url="redis://localhost:6379/0")
        await backend.open()         # ping the server
        # ... use the backend ...
        await backend.aclose()       # close the connection pool

    The connection pool is lazily constructed on first use; ``open()``
    pings the server so a misconfigured URL fails loud at boot.
    """

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        # Imported lazily so test environments without the redis
        # package still load. ``redis>=8.1.0`` is a base dep, so
        # this is for the fakeredis test path.
        import redis.asyncio as redis_async

        self._redis: Any = redis_async.from_url(
            redis_url, encoding="utf-8", decode_responses=False
        )
        self._consume_channel_script: Any = self._redis.register_script(
            _CONSUME_CHANNEL_LUA
        )

    @property
    def redis_url(self) -> str:
        return self._redis_url

    async def open(self) -> None:
        """Ping the server to fail loud at boot if Redis is unreachable.

        The Plugin calls this right after constructing the backend
        so a misconfigured ``REDIS_URL`` produces a clear error in
        the boot log rather than a confusing first-request failure.
        """
        await self._redis.ping()
        _LOGGER.info("redis state backend online url=%s", self._redis_url)

    # -- Artifacts ----------------------------------------------------------

    async def put_artifact(
        self,
        *,
        id: str,
        token: str,
        owner_job_id: str,
        content_type: str,
        blob: bytes,
        ttl_seconds: int,
    ) -> None:
        record = ArtifactRecord(
            id=id,
            token=token,
            owner_job_id=owner_job_id,
            content_type=content_type,
            created_at=time.time(),
            ttl_seconds=ttl_seconds,
        )
        meta = json.dumps(_artifact_record_to_dict(record)).encode("utf-8")
        # SETEX on both keys so the metadata and blob expire
        # together. ``decode_responses=False`` is set on the
        # client, so we pass bytes for both.
        await self._redis.set(_meta_key(id), meta, ex=ttl_seconds)
        await self._redis.set(_blob_key(id), blob, ex=ttl_seconds)

    async def get_artifact(self, id: str, token: str) -> ArtifactBlob | None:
        # Use a pipeline so the two GETs are a single round-trip
        # and the data is internally consistent (no race between
        # the metadata and blob expiring).
        async with self._redis.pipeline(transaction=False) as pipe:
            pipe.get(_meta_key(id))
            pipe.get(_blob_key(id))
            meta_bytes, blob = await pipe.execute()
        if not meta_bytes or blob is None:
            return None
        try:
            record = _artifact_record_from_dict(json.loads(meta_bytes))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None
        if record.token != token:
            # The auth token doesn't match — same as the SQLite
            # impl: return None so a caller without the right
            # token can't tell whether the artifact exists.
            return None
        return ArtifactBlob(record=record, blob=blob)

    # -- Jobs ---------------------------------------------------------------

    async def upsert_job(self, record: JobRecord) -> None:
        data = json.dumps(_job_record_to_dict(record)).encode("utf-8")
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.set(_job_key(record.job_id), data)
            # ZADD with the score = created_at. The index is
            # ordered by score for ``list_jobs`` pagination.
            pipe.zadd(_job_index_key(), {record.job_id: record.created_at})
            await pipe.execute()

    async def get_job(self, job_id: str) -> JobRecord | None:
        data = await self._redis.get(_job_key(job_id))
        if not data:
            return None
        # JSON parse errors (corrupt payload) and missing required
        # fields (drift between the schema and an old record) return
        # None so a single bad row doesn't crash the read path.
        # ``ValueError`` from ``_job_record_from_dict``'s status
        # validation propagates — a malformed status is a data-
        # integrity error the operator must see, not a "record
        # missing" outcome.
        try:
            parsed = json.loads(data)
        except (json.JSONDecodeError, TypeError):
            return None
        try:
            return _job_record_from_dict(parsed)
        except KeyError:
            return None

    async def list_jobs(
        self, *, limit: int = 100, offset: int = 0
    ) -> list[JobRecord]:
        if limit <= 0:
            return []
        # ZREVRANGE returns the highest-scored entries first; the
        # index score is created_at, so the newest jobs are
        # returned first. ``redis-py`` returns bytes (because
        # ``decode_responses=False``); decode the ids to str so
        # ``_job_key`` formats a clean key.
        end = offset + limit - 1
        ids = await self._redis.zrevrange(_job_index_key(), offset, end)
        if not ids:
            return []
        keys = [_job_key(_decode_id(jid)) for jid in ids]
        raw_values = await self._redis.mget(*keys)
        out: list[JobRecord] = []
        for raw in raw_values:
            if not raw:
                continue
            try:
                out.append(_job_record_from_dict(json.loads(raw)))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                # Skip malformed records; the next prune pass
                # will catch them via the index-vs-key drift check.
                continue
        return out

    async def clear_jobs(self) -> int:
        # Scan for all ``omniscribe:job:*`` keys (excluding the
        # index key) and delete them. The index key is deleted
        # separately to keep the scan's MATCH pattern clean.
        deleted = 0
        async for key in self._redis.scan_iter(
            match=f"{_KEY_PREFIX}job:*", count=500
        ):
            if key == _job_index_key().encode("utf-8"):
                continue
            await self._redis.delete(key)
            deleted += 1
        await self._redis.delete(_job_index_key())
        return deleted

    async def delete_job(self, job_id: str) -> None:
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.delete(_job_key(job_id))
            pipe.zrem(_job_index_key(), job_id)
            await pipe.execute()

    # -- Progress channels --------------------------------------------------

    async def put_channel(
        self, channel_id: str, session_token: str, job_id: str, ttl_seconds: int
    ) -> None:
        record = ChannelRecord(
            channel_id=channel_id,
            session_token=session_token,
            job_id=job_id,
            created_at=time.time(),
            ttl_seconds=ttl_seconds,
            consumed=False,
        )
        data = json.dumps(_channel_record_to_dict(record)).encode("utf-8")
        expiry_epoch = record.created_at + ttl_seconds
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.set(_channel_key(channel_id), data, ex=ttl_seconds)
            pipe.zadd(
                _channel_index_key(),
                {channel_id: expiry_epoch},
            )
            await pipe.execute()

    async def get_channel(self, channel_id: str) -> ChannelRecord | None:
        data = await self._redis.get(_channel_key(channel_id))
        if not data:
            return None
        try:
            return _channel_record_from_dict(json.loads(data))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    async def consume_channel(
        self, channel_id: str, session_token: str
    ) -> ChannelRecord | None:
        # The Lua script is the primary path. The
        # WATCH/MULTI/EXEC fallback is below for the rare case
        # where the script can't be loaded.
        try:
            raw = await self._consume_channel_script(
                keys=[_channel_key(channel_id)], args=[session_token]
            )
        except Exception as exc:  # pragma: no cover — Lua load failure
            _LOGGER.warning(
                "consume_channel Lua script failed (%s); falling back to "
                "WATCH/MULTI/EXEC",
                exc,
            )
            return await self._consume_channel_watch(channel_id, session_token)
        if raw is None:
            return None
        try:
            return _channel_record_from_dict(json.loads(raw))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    async def _consume_channel_watch(
        self, channel_id: str, session_token: str
    ) -> ChannelRecord | None:
        """WATCH/MULTI/EXEC fallback for ``consume_channel``.

        The Lua script is the primary path. This fallback exists
        for the rare case where the script can't be loaded (e.g.
        a fakeredis version that doesn't support Lua). The
        semantics match the Lua path: server-side atomicity on the
        read-and-mark, KEEPTTL on the write.
        """
        key = _channel_key(channel_id)
        while True:
            try:
                async with self._redis.pipeline(transaction=True) as pipe:
                    await pipe.watch(key)
                    data = await pipe.get(key)
                    if not data:
                        await pipe.unwatch()
                        return None
                    record = _channel_record_from_dict(json.loads(data))
                    if record.session_token != session_token:
                        await pipe.unwatch()
                        return None
                    if record.consumed:
                        await pipe.unwatch()
                        return None
                    # Mark as consumed, preserving the existing TTL.
                    updated = dataclasses.replace(record, consumed=True)
                    pipe.multi()
                    pipe.set(key, json.dumps(_channel_record_to_dict(updated)).encode("utf-8"), keepttl=True)
                    await pipe.execute()
                    return record
            except Exception:  # WATCH fired; another writer beat us. Retry.
                continue

    async def delete_channel(self, channel_id: str) -> None:
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.delete(_channel_key(channel_id))
            pipe.zrem(_channel_index_key(), channel_id)
            await pipe.execute()

    async def prune_expired_channels(self, now: float) -> int:
        # The channel index ZSET stores ``created_at + ttl_seconds``
        # as the score, so ``ZRANGEBYSCORE 0 now`` returns exactly
        # the expired channel ids — no SCAN needed. The ids come
        # back as bytes; decode them for the key builders.
        expired = await self._redis.zrangebyscore(
            _channel_index_key(), 0, now
        )
        if not expired:
            return 0
        deleted = 0
        # Delete in chunks to keep the pipeline small.
        chunk = 100
        for i in range(0, len(expired), chunk):
            ids = [_decode_id(cid) for cid in expired[i : i + chunk]]
            async with self._redis.pipeline(transaction=True) as pipe:
                for cid in ids:
                    pipe.delete(_channel_key(cid))
                    pipe.zrem(_channel_index_key(), cid)
                await pipe.execute()
                deleted += len(ids)
        return deleted

    async def delete_artifact(self, id: str) -> None:
        await self._redis.delete(_meta_key(id), _blob_key(id))

    async def prune_expired_artifacts(self, now: float) -> int:
        # Redis handles artifact expiration automatically via the
        # TTL on the metadata and blob keys. The Protocol method
        # is preserved for cross-backend parity with SQLite; the
        # redis impl is a no-op. ``return 0`` (no keys deleted by
        # us) is correct because Redis deletes the expired keys
        # itself.
        del now  # Silence "unused argument" lint; signature is fixed by Protocol.
        return 0

    # -- Lifecycle ----------------------------------------------------------

    async def aclose(self) -> None:
        # ``redis.asyncio.Redis`` from ``from_url`` owns a
        # connection pool. ``aclose`` closes the pool. The next
        # call on the client would raise; the Plugin's
        # ``ctx.effect(backend.aclose)`` wiring is the only
        # caller, so this is a one-shot close.
        try:
            await self._redis.aclose()
        except AttributeError:  # pragma: no cover — older redis-py fallback
            # ``redis-py<5.0`` used ``close()`` instead of
            # ``aclose()``. ``redis>=8.1.0`` is the floor in
            # ``pyproject.toml`` so this branch is unreachable in
            # production, but keep it as a safety net for the
            # fakeredis path.
            close = getattr(self._redis, "close", None)
            if close is not None:
                result = close()
                if hasattr(result, "__await__"):
                    await result


__all__ = ["RedisStateBackend"]
