"""StateBackendPlugin: backend selection and registration."""

from __future__ import annotations

from pathlib import Path

import fakeredis.aioredis
import pytest

from omniscribe.config import RuntimeSettings
from omniscribe.harness.context import Context
from omniscribe.plugins import state_backend as sb
from omniscribe.plugins.state_backend import (
    MemoryStateBackend,
    RedisStateBackend,
    SQLiteStateBackend,
    StateBackend,
)


async def test_memory_backend_registered() -> None:
    ctx = Context()
    await ctx.plugin(sb.StateBackendPlugin(), config={"backend": "memory"})
    assert isinstance(ctx.inject(StateBackend), MemoryStateBackend)
    await ctx.dispose()


async def test_default_backend_is_memory() -> None:
    ctx = Context()
    await ctx.plugin(sb.StateBackendPlugin(), config={})
    assert isinstance(ctx.inject(StateBackend), MemoryStateBackend)
    await ctx.dispose()


async def test_sqlite_backend_registered(tmp_path: Path) -> None:
    ctx = Context()
    await ctx.plugin(
        sb.StateBackendPlugin(),
        config={"backend": "sqlite", "sqlite_path": str(tmp_path / "state.db")},
    )
    backend = ctx.inject(StateBackend)
    assert isinstance(backend, SQLiteStateBackend)
    assert (tmp_path / "state.db").exists()
    await ctx.dispose()
    # dispose closed the connection via the registered effect
    with pytest.raises(RuntimeError):
        await backend.get_job("j")


async def test_redis_backend_registered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sprint 4 (RFC 003): ``OMNISCRIBE_STATE_BACKEND=redis`` is
    accepted, the ``RedisStateBackend`` is registered, and the
    ``aclose`` effect is wired. Uses ``fakeredis`` to avoid a
    real Redis dependency in CI; the production path (real
    ``redis.asyncio.from_url``) is exercised by the manual
    ``dev_redis_smoke.sh`` recipe in the docs.
    """
    fake = fakeredis.aioredis.FakeRedis()
    # Patch the ``redis.asyncio.from_url`` that the backend's
    # ``__init__`` imports at module load. The next
    # ``RedisStateBackend(redis_url=...)`` returns a client backed
    # by ``fake``.
    import redis.asyncio as redis_async

    orig_from_url = redis_async.from_url
    redis_async.from_url = lambda *a, **kw: fake
    try:
        ctx = Context()
        await ctx.plugin(sb.StateBackendPlugin(), config={"backend": "redis"})
        backend = ctx.inject(StateBackend)
        assert isinstance(backend, RedisStateBackend)
        # ``aclose`` was registered as a harness effect; running
        # the dispose chain closes the connection pool. We don't
        # assert a post-dispose failure because fakeredis' server
        # object is process-global and remains usable after
        # ``aclose``; the production behavior (real redis-py)
        # would raise on the next op. The end-to-end real-Redis
        # path is the manual ``dev_redis_smoke.sh`` recipe.
        await ctx.dispose()
    finally:
        redis_async.from_url = orig_from_url


async def test_empty_sqlite_path_defaults_to_artifact_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        sb,
        "load_settings",
        lambda: RuntimeSettings(artifact_base_dir=tmp_path),
    )
    ctx = Context()
    await ctx.plugin(sb.StateBackendPlugin(), config={"backend": "sqlite"})
    assert (tmp_path / "omniscribe-state.db").exists()
    await ctx.dispose()
