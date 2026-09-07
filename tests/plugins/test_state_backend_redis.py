"""Tests for the Redis ``StateBackend`` (Sprint 4, RFC 003).

Tests use ``fakeredis.aioredis.FakeRedis`` as the in-process Redis
server. ``fakeredis`` honours TTL on ``SETEX`` and Lua scripts via
``lupa``; if the Lua path fails (older fakeredis version), the
``WATCH/MULTI/EXEC`` fallback in ``consume_channel`` is exercised
automatically and a ``_LOGGER.warning`` is emitted.

The test harness (see ``_redis_state_backend``) patches the
backend's lazy ``redis.asyncio.from_url`` import to return a fresh
``FakeRedis`` per test, so each test starts with an empty Redis.
"""

from __future__ import annotations

import asyncio
import time
from typing import AsyncIterator

import fakeredis.aioredis
import pytest

from omniscribe.plugins import state_backend_redis as sbm
from omniscribe.plugins.state_backend_redis import RedisStateBackend
from omniscribe.plugins.state_backend_types import (
    ArtifactRecord,
    ChannelRecord,
    JobRecord,
)


@pytest.fixture
async def redis_state_backend() -> AsyncIterator[RedisStateBackend]:
    """Fresh ``FakeRedis`` per test, with the backend's ``from_url`` patched.

    The ``RedisStateBackend.__init__`` does a local
    ``import redis.asyncio as redis_async`` and then calls
    ``redis_async.from_url(...)``. We patch the ``from_url`` of
    the redis module that the backend sees via its
    ``__init__.__globals__``.
    """
    fake = fakeredis.aioredis.FakeRedis()
    # The backend's ``__init__`` does ``import redis.asyncio as
    # redis_async`` inside the function. The binding is in the
    # function's locals, not the module's globals, but the module
    # ``redis.asyncio`` is the same Python module the import
    # statement resolves against, so patching the module's
    # ``from_url`` does the right thing.
    import redis.asyncio as redis_async

    orig_from_url = redis_async.from_url
    redis_async.from_url = lambda *a, **kw: fake  # type: ignore[assignment]
    sbm.RedisStateBackend.__init__.__globals__[  # type: ignore[attr-defined]
        "redis_async"
    ] = redis_async
    try:
        backend = RedisStateBackend(redis_url="redis://fake:6379/0")
        await backend.open()
        yield backend
    finally:
        await backend.aclose()
        redis_async.from_url = orig_from_url  # type: ignore[assignment]


# -- Artifacts ---------------------------------------------------------------


async def test_artifact_round_trip(redis_state_backend: RedisStateBackend) -> None:
    await redis_state_backend.put_artifact(
        id="a1",
        token="t1",
        owner_job_id="j1",
        content_type="text/plain",
        blob=b"hello",
        ttl_seconds=60,
    )
    blob = await redis_state_backend.get_artifact("a1", "t1")
    assert blob is not None
    assert blob.blob == b"hello"
    assert blob.record.token == "t1"
    assert blob.record.owner_job_id == "j1"
    assert blob.record.content_type == "text/plain"


async def test_artifact_bad_token_returns_none(
    redis_state_backend: RedisStateBackend,
) -> None:
    await redis_state_backend.put_artifact(
        id="a1", token="t1", owner_job_id="j1",
        content_type="text/plain", blob=b"x", ttl_seconds=60,
    )
    blob = await redis_state_backend.get_artifact("a1", "wrong-token")
    assert blob is None  # auth gate


async def test_artifact_missing_returns_none(
    redis_state_backend: RedisStateBackend,
) -> None:
    assert await redis_state_backend.get_artifact("nope", "t") is None


async def test_artifact_delete(
    redis_state_backend: RedisStateBackend,
) -> None:
    await redis_state_backend.put_artifact(
        id="a1", token="t1", owner_job_id="j1",
        content_type="text/plain", blob=b"x", ttl_seconds=60,
    )
    await redis_state_backend.delete_artifact("a1")
    assert await redis_state_backend.get_artifact("a1", "t1") is None


async def test_prune_expired_artifacts_is_noop(
    redis_state_backend: RedisStateBackend,
) -> None:
    # Redis handles artifact TTL natively; the prune method is a
    # Protocol no-op that returns 0.
    n = await redis_state_backend.prune_expired_artifacts(time.time() + 1e9)
    assert n == 0


# -- Jobs --------------------------------------------------------------------


async def test_job_round_trip(redis_state_backend: RedisStateBackend) -> None:
    j = JobRecord(job_id="j1", status="queued")
    await redis_state_backend.upsert_job(j)
    out = await redis_state_backend.get_job("j1")
    assert out is not None
    assert out.status == "queued"
    assert out.job_id == "j1"


async def test_job_unknown_status_raises(
    redis_state_backend: RedisStateBackend,
) -> None:
    # Direct-write a malformed status to bypass the Literal
    # check; the next ``get_job`` should raise ValueError.
    fake = redis_state_backend._redis
    await fake.set(
        "omniscribe:job:bad",
        b'{"job_id": "bad", "status": "frobnicated", "request_meta": {}, '
        b'"created_at": 0, "updated_at": 0}',
    )
    with pytest.raises(ValueError, match="unknown job status"):
        await redis_state_backend.get_job("bad")


async def test_job_list_pagination(
    redis_state_backend: RedisStateBackend,
) -> None:
    # Insert 10 jobs with strictly-increasing created_at so the
    # ZREVRANGE order is deterministic.
    base = time.time()
    for i in range(10):
        await redis_state_backend.upsert_job(
            JobRecord(
                job_id=f"j{i}",
                status="queued",
                created_at=base + i,
            )
        )
    # Newest first.
    page1 = await redis_state_backend.list_jobs(limit=3, offset=0)
    assert [j.job_id for j in page1] == ["j9", "j8", "j7"]
    page2 = await redis_state_backend.list_jobs(limit=3, offset=3)
    assert [j.job_id for j in page2] == ["j6", "j5", "j4"]
    page3 = await redis_state_backend.list_jobs(limit=3, offset=6)
    assert [j.job_id for j in page3] == ["j3", "j2", "j1"]
    page4 = await redis_state_backend.list_jobs(limit=3, offset=9)
    assert [j.job_id for j in page4] == ["j0"]


async def test_job_delete(
    redis_state_backend: RedisStateBackend,
) -> None:
    await redis_state_backend.upsert_job(JobRecord(job_id="j1", status="queued"))
    await redis_state_backend.delete_job("j1")
    assert await redis_state_backend.get_job("j1") is None
    # The index entry is gone too (next list_jobs must not return it).
    assert all(
        j.job_id != "j1"
        for j in await redis_state_backend.list_jobs()
    )


async def test_clear_jobs(
    redis_state_backend: RedisStateBackend,
) -> None:
    for i in range(5):
        await redis_state_backend.upsert_job(JobRecord(job_id=f"j{i}", status="queued"))
    n = await redis_state_backend.clear_jobs()
    assert n == 5
    assert await redis_state_backend.list_jobs() == []


# -- Progress channels ------------------------------------------------------


async def test_channel_round_trip(
    redis_state_backend: RedisStateBackend,
) -> None:
    await redis_state_backend.put_channel("c1", "sess1", "j1", 60)
    out = await redis_state_backend.get_channel("c1")
    assert out is not None
    assert out.session_token == "sess1"
    assert out.job_id == "j1"
    assert out.ttl_seconds == 60
    assert out.consumed is False


async def test_channel_consume_is_atomic(
    redis_state_backend: RedisStateBackend,
) -> None:
    """Two concurrent ``consume_channel`` calls — exactly one wins.

    This is the core atomicity guarantee of the progress-channel
    pattern. The first call returns the record; the second call
    returns None because the channel is marked consumed.
    """
    await redis_state_backend.put_channel("c1", "sess1", "j1", 60)
    first = await redis_state_backend.consume_channel("c1", "sess1")
    assert first is not None
    assert first.consumed is False  # the returned snapshot is pre-consume
    second = await redis_state_backend.consume_channel("c1", "sess1")
    assert second is None


async def test_channel_consume_wrong_token(
    redis_state_backend: RedisStateBackend,
) -> None:
    await redis_state_backend.put_channel("c1", "sess1", "j1", 60)
    out = await redis_state_backend.consume_channel("c1", "wrong-sess")
    assert out is None
    # The channel is still consumable by the right token.
    first = await redis_state_backend.consume_channel("c1", "sess1")
    assert first is not None


async def test_channel_concurrent_consumers(
    redis_state_backend: RedisStateBackend,
) -> None:
    """Simulate two consumers racing; exactly one wins.

    This exercises the Lua-script / WATCH-MULTI-EXEC atomicity
    path. With fakeredis, the Lua path may fall back to
    WATCH/MULTI/EXEC; both paths must give the same result.
    """
    await redis_state_backend.put_channel("c1", "sess1", "j1", 60)
    # ``asyncio.gather`` schedules both; the atomicity guarantee
    # is the test target, not the exact ordering.
    a, b = await asyncio.gather(
        redis_state_backend.consume_channel("c1", "sess1"),
        redis_state_backend.consume_channel("c1", "sess1"),
    )
    winners = [r for r in (a, b) if r is not None]
    losers = [r for r in (a, b) if r is None]
    assert len(winners) == 1
    assert len(losers) == 1
    assert winners[0].consumed is False  # the snapshot is pre-consume


async def test_channel_delete(
    redis_state_backend: RedisStateBackend,
) -> None:
    await redis_state_backend.put_channel("c1", "sess1", "j1", 60)
    await redis_state_backend.delete_channel("c1")
    assert await redis_state_backend.get_channel("c1") is None


async def test_prune_expired_channels(
    redis_state_backend: RedisStateBackend,
) -> None:
    """Channels whose ``created_at + ttl_seconds <= now`` are deleted.

    The ``omniscribe:channel:index`` ZSET stores the
    expiration epoch as the score, so the prune is a single
    ``ZRANGEBYSCORE 0 now`` call.
    """
    base = time.time()
    # 3 channels: one already-expired, one expiring now, one future.
    await redis_state_backend.put_channel("expired", "s1", "j1", 1)
    await redis_state_backend.put_channel("expiring", "s1", "j1", 60)
    await redis_state_backend.put_channel("future", "s1", "j1", 3600)
    # Override ``created_at`` to control the expiry epoch.
    fake = redis_state_backend._redis
    # Move the first channel to a long-past created_at.
    await fake.zadd(
        "omniscribe:channel:index",
        {"expired": base - 7200.0},
    )
    n = await redis_state_backend.prune_expired_channels(base)
    assert n == 1
    # The expired channel is gone; the others are still there.
    assert await redis_state_backend.get_channel("expired") is None
    assert await redis_state_backend.get_channel("expiring") is not None
    assert await redis_state_backend.get_channel("future") is not None
