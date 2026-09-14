"""Runtime plugin: settings, readiness, HarnessReady, cleanup loop."""

from __future__ import annotations

import asyncio

from omniscribe.harness.context import Context
from omniscribe.plugins.runtime import (
    HarnessReady,
    RuntimePlugin,
    RuntimeService,
)
from omniscribe.plugins.state_backend import StateBackend


class _FakeState:
    def __init__(self) -> None:
        self.artifact_prunes: list[float] = []
        self.channel_prunes: list[float] = []

    async def prune_expired_artifacts(self, now: float) -> int:
        self.artifact_prunes.append(now)
        return 1

    async def prune_expired_channels(self, now: float) -> int:
        self.channel_prunes.append(now)
        return 2


async def _mount(config: dict | None = None) -> tuple[Context, RuntimeService]:
    ctx = Context()
    await ctx.plugin(
        RuntimePlugin(), config=config or {"cleanup_interval_seconds": 3600}
    )
    return ctx, ctx.inject(RuntimeService)


async def test_registers_runtime_service_with_settings() -> None:
    ctx, service = await _mount()
    assert service.ready is False
    assert service.settings.llm_api_base
    await ctx.dispose()


async def test_mark_ready_flips_flag_and_emits_harness_ready() -> None:
    ctx, service = await _mount()
    seen: list[HarnessReady] = []
    emitted = asyncio.Event()

    def _record(_ev: HarnessReady) -> None:
        seen.append(_ev)
        emitted.set()

    ctx.on(HarnessReady, _record)  # type: ignore[arg-type]
    service.mark_ready()
    # mark_ready schedules the HarnessReady emission as a task; wait for
    # the actual event rather than guessing a wall-clock delay.
    await asyncio.wait_for(emitted.wait(), timeout=5.0)
    assert service.ready is True
    assert seen == [HarnessReady()]
    await ctx.dispose()


async def test_prune_once_delegates_to_state_backend() -> None:
    ctx, service = await _mount()
    fake = _FakeState()
    ctx.service(StateBackend, fake)
    await service.prune_once()  # type: ignore[attr-defined]
    assert len(fake.artifact_prunes) == 1
    assert len(fake.channel_prunes) == 1
    await ctx.dispose()


async def test_prune_once_without_backend_is_a_skip() -> None:
    ctx, service = await _mount()
    # no StateBackend registered — must not raise; ignored below
    await service.prune_once()  # type: ignore[attr-defined]
    await ctx.dispose()


async def test_dispose_cancels_cleanup_loop() -> None:
    ctx, _service = await _mount()
    tasks = [
        t for t in asyncio.all_tasks() if t.get_name() == "omniscribe-harness-cleanup"
    ]
    assert tasks, "cleanup loop task not started"
    await ctx.dispose()
    assert tasks[0].cancelled() or tasks[0].done()


async def test_schema_defaults() -> None:
    from omniscribe.plugins.runtime import RuntimeSchema

    cfg = RuntimeSchema()
    assert cfg.cleanup_interval_seconds == 60
    assert cfg.artifact_ttl_seconds == 86_400
    assert cfg.channel_ttl_seconds == 600
