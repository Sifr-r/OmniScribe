"""Runtime plugin — settings holder, readiness flag, prune cadence.

Owns the artifact/channel cleanup loop (the single place that decides prune
cadence) and emits ``HarnessReady`` once the server flips readiness.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel

from omniscribe.config import RuntimeSettings, load_settings
from omniscribe.harness.context import Context
from omniscribe.harness.events import SessionEvent
from omniscribe.harness.plugin import Plugin
from omniscribe.plugins.state_backend_types import StateBackend

_LOGGER = logging.getLogger("omniscribe.plugins.runtime")


@dataclass(frozen=True)
class HarnessReady(SessionEvent):
    """Emitted once the server has finished mounting every plugin."""


class RuntimeService(Protocol):
    """Lifespan coordination surface."""

    settings: RuntimeSettings

    @property
    def ready(self) -> bool: ...

    def mark_ready(self) -> None: ...

    async def shutdown(self) -> None: ...


class RuntimeSchema(BaseModel):
    cleanup_interval_seconds: int = 60
    artifact_ttl_seconds: int = 86_400
    channel_ttl_seconds: int = 600


class RuntimeServiceImpl:
    """Concrete RuntimeService backed by the harness context."""

    def __init__(self, ctx: Context, config: RuntimeSchema) -> None:
        self._ctx = ctx
        self.config = config
        self.settings = load_settings()
        self._ready = False
        self._stopping = False

    @property
    def ready(self) -> bool:
        return self._ready

    def mark_ready(self) -> None:
        self._ready = True
        with contextlib.suppress(RuntimeError):
            asyncio.get_running_loop().create_task(self._ctx.emit(HarnessReady()))

    async def shutdown(self) -> None:
        self._stopping = True

    async def prune_once(self) -> None:
        """One prune pass; skips with a debug log when no state backend exists."""
        if not self._ctx.has(StateBackend):
            _LOGGER.debug("cleanup pass skipped: no StateBackend registered")
            return
        state = self._ctx.inject(StateBackend)
        now = time.time()
        artifacts = await state.prune_expired_artifacts(now)
        channels = await state.prune_expired_channels(now)
        if artifacts or channels:
            _LOGGER.info(
                "cleanup pass pruned %d artifact(s), %d channel(s)",
                artifacts,
                channels,
            )

    async def cleanup_loop(self) -> None:
        while not self._stopping:
            await asyncio.sleep(self.config.cleanup_interval_seconds)
            await self.prune_once()


class RuntimePlugin(Plugin):
    Schema = RuntimeSchema

    async def apply(self, ctx: Context) -> None:
        config = RuntimeSchema(**self.config)
        service = RuntimeServiceImpl(ctx, config)
        ctx.service(RuntimeService, service)

        task = asyncio.create_task(
            service.cleanup_loop(), name="omniscribe-harness-cleanup"
        )

        async def _stop_loop() -> None:
            await service.shutdown()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        ctx.effect(_stop_loop)


plugin = RuntimePlugin()
