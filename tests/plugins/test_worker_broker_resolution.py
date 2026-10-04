"""Broker resolution consistency for the worker plugin tree (audit Finding 5).

Covers the two halves of the finding:

1. **One broker per process.** ``boot_worker_context`` resolves a
   ``--redis-url`` override that ``load_settings()`` can never see, so
   every plugin that re-derived its own URL from the environment put the
   state backend, the job queue, and the progress broker on three
   different Redis instances. These tests assert the *resolved* URL on
   the constructed services, not on log output.
2. **Interrupted work at startup.** The default SQLite backend persists
   the job record, not the executable work, so a row left ``queued`` /
   ``running`` by a dead process can never be resumed. Startup marks
   such rows terminal; these tests pin that verdict and its idempotency.

No Redis server is required or contacted: ``redis.asyncio.from_url``
only builds a client (no socket), and the ``open()`` pings — the only
step that needs a live server — are stubbed by ``offline_redis``. Real
Redis concurrency and restart-recovery behaviour remain UNVERIFIED here.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, cast

import pytest
import redis.asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omniscribe.harness.context import Context
from omniscribe.plugins.artifacts import ArtifactsPlugin, ArtifactStore
from omniscribe.plugins.jobs import (
    InMemoryJobQueue,
    JobQueue,
    JobsPlugin,
)
from omniscribe.plugins.jobs_redis import RedisJobQueue
from omniscribe.plugins.progress import (
    ProgressPlugin,
    ProgressService,
    ProgressServiceImpl,
)
from omniscribe.plugins.runtime import RuntimePlugin, RuntimeService
from omniscribe.plugins.state_backend import StateBackendPlugin
from omniscribe.plugins.state_backend_memory import MemoryStateBackend
from omniscribe.plugins.state_backend_redis import RedisStateBackend
from omniscribe.plugins.state_backend_sqlite import SQLiteStateBackend
from omniscribe.plugins.state_backend_types import JobRecord, StateBackend
from omniscribe.server import create_app
from omniscribe.worker import boot_worker_context

#: Broker the worker was explicitly told to use (``--redis-url``).
CLI_URL = "redis://cli-override.internal:6380/3"
#: Broker the environment says to use; deliberately different from CLI_URL.
ENV_URL = "redis://env-default.internal:6379/9"

_ENV_BROKER_VARS = (
    "REDIS_URL",
    "OMNISCRIBE_JOBS_MODE",
    "OMNISCRIBE_STATE_BACKEND",
    "OMNISCRIBE_STATE_DB_PATH",
    "OMNISCRIBE_ARTIFACT_DIR",
    "OMNISCRIBE_SPOOL_DIR",
)


@pytest.fixture
def offline_redis(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stub the Redis ``open()`` pings so URL resolution is testable offline.

    ``open()`` is the only step that needs a live server; every service
    stores the URL it was constructed with, which is what these tests
    assert on. Returns the list of service classes that were opened.
    """
    opened: list[str] = []

    async def _fake_open(self: Any) -> None:
        opened.append(type(self).__name__)

    monkeypatch.setattr(RedisStateBackend, "open", _fake_open)
    monkeypatch.setattr(RedisJobQueue, "open", _fake_open)
    monkeypatch.setattr(ProgressServiceImpl, "open", _fake_open)
    return opened


@pytest.fixture
def isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Clear every broker/backend override so each test states its own."""
    for name in _ENV_BROKER_VARS:
        monkeypatch.delenv(name, raising=False)
    artifacts = tmp_path / "artifacts"
    monkeypatch.setenv("OMNISCRIBE_ARTIFACT_DIR", str(artifacts))
    return artifacts


async def _base_context() -> Context:
    """Runtime + memory state backend + artifacts, no queue or progress yet.

    Mount order matches ``boot_worker_context`` so the runtime service is
    registered before anything that resolves a broker.
    """
    ctx = Context()
    await ctx.plugin(RuntimePlugin(), config={})
    await ctx.plugin(StateBackendPlugin(), config={"backend": "memory"})
    await ctx.plugin(ArtifactsPlugin(), config={})
    return ctx


async def _seed(backend: StateBackend, **rows: tuple[str, float]) -> None:
    """Insert job records with deterministic ``created_at`` ordering."""
    for job_id, (status, created_at) in rows.items():
        await backend.upsert_job(
            JobRecord(
                job_id=job_id,
                status=status,  # type: ignore[arg-type]
                created_at=created_at,
                updated_at=created_at,
            )
        )


# -- one broker per process -------------------------------------------------


async def test_cli_override_reaches_state_queue_and_progress(
    monkeypatch: pytest.MonkeyPatch,
    isolated_env: Path,
    offline_redis: list[str],
) -> None:
    """A ``--redis-url`` override must land on all three services at once."""
    monkeypatch.setenv("REDIS_URL", ENV_URL)

    ctx = await boot_worker_context(redis_url=CLI_URL)
    try:
        backend = ctx.inject(StateBackend)
        queue = ctx.inject(JobQueue)
        service = ctx.inject(ProgressService)

        assert backend.redis_url == CLI_URL
        assert queue._redis_url == CLI_URL
        assert service._redis_mode is True
        assert service._redis_url == CLI_URL

        # Exactly one source of truth: the resolved settings object the
        # worker published is the one every plugin read its URL from.
        assert ctx.inject(RuntimeService).settings.redis_url == CLI_URL
        assert len({backend.redis_url, queue._redis_url, service._redis_url}) == 1
    finally:
        await ctx.dispose()


async def test_without_cli_override_environment_wins(
    monkeypatch: pytest.MonkeyPatch,
    isolated_env: Path,
    offline_redis: list[str],
) -> None:
    """No override: all three services follow the environment."""
    monkeypatch.setenv("REDIS_URL", ENV_URL)

    ctx = await boot_worker_context()
    try:
        backend = ctx.inject(StateBackend)
        queue = ctx.inject(JobQueue)
        service = ctx.inject(ProgressService)

        assert backend.redis_url == ENV_URL
        assert queue._redis_url == ENV_URL
        assert service._redis_mode is True
        assert service._redis_url == ENV_URL
    finally:
        await ctx.dispose()


async def test_explicit_plugin_redis_url_beats_environment(
    monkeypatch: pytest.MonkeyPatch,
    isolated_env: Path,
    offline_redis: list[str],
) -> None:
    """A plugin config row carrying a URL wins over the environment."""
    monkeypatch.setenv("REDIS_URL", ENV_URL)

    ctx = await _base_context()
    try:
        await ctx.plugin(JobsPlugin(), config={"mode": "redis", "redis_url": CLI_URL})
        await ctx.plugin(
            ProgressPlugin(), config={"mode": "redis", "redis_url": CLI_URL}
        )

        assert ctx.inject(JobQueue)._redis_url == CLI_URL
        assert ctx.inject(ProgressService)._redis_url == CLI_URL
    finally:
        await ctx.dispose()


async def test_explicit_mode_beats_environment_for_both_plugins(
    monkeypatch: pytest.MonkeyPatch,
    isolated_env: Path,
    offline_redis: list[str],
) -> None:
    """An explicit ``inprocess`` overrides ``OMNISCRIBE_JOBS_MODE=redis``.

    Previously ``ProgressPlugin`` re-derived the mode from the
    environment and went to Redis while ``JobsPlugin`` honoured the
    config row — a second, mode-shaped split-brain. Both must now agree
    on the explicit value.
    """
    monkeypatch.setenv("REDIS_URL", ENV_URL)
    monkeypatch.setenv("OMNISCRIBE_JOBS_MODE", "redis")

    ctx = await _base_context()
    try:
        await ctx.plugin(JobsPlugin(), config={"mode": "inprocess"})
        await ctx.plugin(ProgressPlugin(), config={"mode": "inprocess"})

        queue = ctx.inject(JobQueue)
        service = ctx.inject(ProgressService)
        assert isinstance(queue, InMemoryJobQueue)
        assert service._redis_mode is False
    finally:
        await ctx.dispose()


async def test_explicit_redis_mode_without_url_falls_back_to_environment(
    monkeypatch: pytest.MonkeyPatch,
    isolated_env: Path,
    offline_redis: list[str],
) -> None:
    """Explicit mode wins; the absent URL still defers to the environment."""
    monkeypatch.setenv("REDIS_URL", ENV_URL)
    monkeypatch.setenv("OMNISCRIBE_JOBS_MODE", "inprocess")

    ctx = await _base_context()
    try:
        await ctx.plugin(JobsPlugin(), config={"mode": "redis"})
        await ctx.plugin(ProgressPlugin(), config={"mode": "redis"})

        queue = ctx.inject(JobQueue)
        service = ctx.inject(ProgressService)
        assert not isinstance(queue, InMemoryJobQueue)
        assert queue._redis_url == ENV_URL
        assert service._redis_mode is True
        assert service._redis_url == ENV_URL
    finally:
        await ctx.dispose()


# -- default (no Redis) path -------------------------------------------------


async def test_default_path_is_inprocess_and_never_builds_a_redis_client(
    monkeypatch: pytest.MonkeyPatch,
    isolated_env: Path,
) -> None:
    """Default install: in-process queue, no progress Redis, zero Redis I/O."""
    attempted: list[str] = []

    def _tripwire(*args: Any, **kwargs: Any) -> Any:
        attempted.append("from_url")
        raise AssertionError("default path must not construct a Redis client")

    monkeypatch.setattr(redis.asyncio, "from_url", _tripwire)

    ctx = await _base_context()
    try:
        await ctx.plugin(JobsPlugin(), config={})
        await ctx.plugin(ProgressPlugin(), config={})

        queue = ctx.inject(JobQueue)
        service = ctx.inject(ProgressService)
        assert isinstance(queue, InMemoryJobQueue)
        assert isinstance(ctx.inject(StateBackend), MemoryStateBackend)
        assert service._redis_mode is False
        assert attempted == []
    finally:
        await ctx.dispose()


def test_default_server_boot_is_sqlite_inprocess_and_redis_free(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The shipped default tree (sqlite + inprocess) still boots cleanly."""
    for name in _ENV_BROKER_VARS + (
        "OMNISCRIBE_CORDIS_CONFIG",
        "OMNISCRIBE_CORDIS_PATCH",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OMNISCRIBE_ARTIFACT_DIR", str(tmp_path / "artifacts"))

    attempted: list[str] = []

    def _tripwire(*args: Any, **kwargs: Any) -> Any:
        attempted.append("from_url")
        raise AssertionError("default server boot must not construct a Redis client")

    monkeypatch.setattr(redis.asyncio, "from_url", _tripwire)

    with TestClient(create_app()) as client:
        assert client.get("/api/health").json() == {"status": "ok"}
        assert client.get("/ready").json() == {"status": "ready"}

    assert attempted == []
    # The default backend really is the durable sqlite one.
    assert (tmp_path / "artifacts" / "omniscribe-state.db").exists()


# -- interrupted work at startup --------------------------------------------


async def test_mark_interrupted_terminal_closes_out_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
    isolated_env: Path,
) -> None:
    """Option (b): a restart marks stranded work terminal, exactly once.

    ``queued`` → ``cancelled`` (with its staged input removed, since no
    payload references it any more) and ``running`` → ``error`` with the
    restart reason. Terminal rows are left alone, so a second call is a
    no-op reporting ``0``.
    """
    monkeypatch.setenv("OMNISCRIBE_SPOOL_DIR", str(isolated_env))
    work_dir = Path(tempfile.mkdtemp(prefix="omniscribe-ocr-"))
    staged_input = work_dir / "input.pdf"
    staged_input.write_bytes(b"%PDF-interrupted")

    ctx = await _base_context()
    try:
        backend = ctx.inject(StateBackend)
        await _seed(
            backend,
            **{"job-complete": ("complete", 400.0)},
            **{"job-running": ("running", 300.0)},
            **{"job-queued": ("queued", 200.0)},
        )
        queued = await backend.get_job("job-queued")
        assert queued is not None
        await backend.upsert_job(
            JobRecord(
                job_id="job-queued",
                status="queued",
                input_path=str(staged_input),
                created_at=200.0,
                updated_at=200.0,
            )
        )

        queue = InMemoryJobQueue(ctx, backend, ctx.inject(ArtifactStore))

        assert await queue.mark_interrupted_terminal() == 2

        running = await backend.get_job("job-running")
        queued = await backend.get_job("job-queued")
        complete = await backend.get_job("job-complete")
        assert running is not None and running.status == "error"
        assert running.error is not None and "restart" in running.error
        assert queued is not None and queued.status == "cancelled"
        assert complete is not None and complete.status == "complete"
        assert complete.error is None
        # The staged input of a job nothing can resume any more is removed.
        assert not staged_input.exists()

        # Idempotent: a second startup changes nothing and reports zero.
        assert await queue.mark_interrupted_terminal() == 0
        rerun = await backend.get_job("job-running")
        assert rerun is not None
        assert (rerun.status, rerun.error) == (running.status, running.error)
        assert (await backend.get_job("job-queued")) == queued
    finally:
        await ctx.dispose()


async def test_startup_marks_interrupted_jobs_terminal_and_survives_repeat(
    monkeypatch: pytest.MonkeyPatch,
    isolated_env: Path,
) -> None:
    """``JobsPlugin.apply`` runs the sweep on every startup, idempotently."""
    ctx = await _base_context()
    try:
        backend = ctx.inject(StateBackend)
        await _seed(backend, **{"job-stale": ("running", 100.0)})

        await ctx.plugin(JobsPlugin(), config={})
        stale = await backend.get_job("job-stale")
        assert stale is not None and stale.status == "error"

        # A second process booting against the same records is a no-op.
        second = await _base_context()
        try:
            await second.plugin(JobsPlugin(), config={})
            again = await backend.get_job("job-stale")
            assert again is not None
            assert (again.status, again.error) == (stale.status, stale.error)
        finally:
            await second.dispose()
    finally:
        await ctx.dispose()


def test_shipped_loader_uses_redis_for_jobs_and_progress(
    monkeypatch: pytest.MonkeyPatch,
    isolated_env: Path,
    offline_redis: list[str],
) -> None:
    """Exercise schema defaults through the shipped Loader, not Context.plugin."""
    monkeypatch.setenv("OMNISCRIBE_JOBS_MODE", "redis")
    monkeypatch.setenv("OMNISCRIBE_STATE_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", ENV_URL)
    with TestClient(create_app()) as client:
        ctx = cast(FastAPI, client.app).state.context
        assert isinstance(ctx.inject(JobQueue), RedisJobQueue)
        progress = ctx.inject(ProgressService)
        assert progress._redis_mode is True
        assert progress._redis_url == ENV_URL


async def test_live_peer_cannot_reconcile_or_delete_queued_input(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OMNISCRIBE_SPOOL_DIR", str(tmp_path))
    ctx = await _base_context()
    backend = ctx.inject(StateBackend)
    artifacts = ctx.inject(ArtifactStore)
    first = InMemoryJobQueue(ctx, backend, artifacts)
    second = InMemoryJobQueue(Context(), backend, artifacts)
    work = tmp_path / "omniscribe-ocr-live"
    work.mkdir()
    source = work / "input.pdf"
    source.write_bytes(b"%PDF-live")
    try:
        handle = await first.submit({}, input_path=str(source))
        with pytest.raises(RuntimeError, match="already owns"):
            await second.mark_interrupted_terminal()
        record = await backend.get_job(handle.job_id)
        assert record is not None and record.status == "queued"
        assert source.exists()
    finally:
        await first.shutdown()
        await ctx.dispose()


async def test_sqlite_owner_excludes_other_process_and_recovers_after_death(
    tmp_path: Path,
) -> None:
    db = tmp_path / "state.db"
    backend = SQLiteStateBackend(db, tmp_path)
    await backend.open()
    queue = InMemoryJobQueue(Context(), backend, None)  # type: ignore[arg-type]
    child = (
        "import os; from pathlib import Path; "
        "from omniscribe.harness.context import Context; "
        "from omniscribe.plugins.jobs import InMemoryJobQueue; "
        "from omniscribe.plugins.state_backend_sqlite import SQLiteStateBackend; "
        f"b=SQLiteStateBackend(Path({str(db)!r}),Path({str(tmp_path)!r})); "
        "q=InMemoryJobQueue(Context(),b,None); q._claim_owner(); os._exit(0)"
    )
    try:
        queue._claim_owner()
        excluded = subprocess.run(
            [sys.executable, "-c", child], capture_output=True, text=True, timeout=45
        )
        assert excluded.returncode != 0
        assert "Another process owns" in excluded.stderr
        queue._release_owner()
        dead = subprocess.run(
            [sys.executable, "-c", child], capture_output=True, text=True, timeout=45
        )
        assert dead.returncode == 0, dead.stderr
        await backend.upsert_job(JobRecord(job_id="abandoned", status="running"))
        assert await queue.mark_interrupted_terminal() == 1
        record = await backend.get_job("abandoned")
        assert record is not None and record.status == "error"
        assert await queue.mark_interrupted_terminal() == 0
    finally:
        queue._release_owner()
        await backend.aclose()


async def test_shutdown_is_idempotent_after_new_owner_accepts_work() -> None:
    ctx = await _base_context()
    backend = ctx.inject(StateBackend)
    artifacts = ctx.inject(ArtifactStore)
    first = InMemoryJobQueue(ctx, backend, artifacts)
    second = InMemoryJobQueue(Context(), backend, artifacts)
    try:
        await first.submit({})
        await first.shutdown()
        handle = await second.submit({})
        await first.shutdown()
        record = await backend.get_job(handle.job_id)
        assert record is not None and record.status == "queued"
    finally:
        await second.shutdown()
        await ctx.dispose()
