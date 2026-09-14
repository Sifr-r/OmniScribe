"""Context: services, events, effects, routers, and plugin lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import pytest

from omniscribe.harness.context import Context
from omniscribe.harness.errors import (
    ContextDisposedError,
    DuplicatePluginError,
    DuplicateServiceError,
    ServiceNotFoundError,
)
from omniscribe.harness.events import Event, SessionEvent
from omniscribe.harness.plugin import Plugin


class SvcA(Protocol):
    def value(self) -> str: ...


class SvcB(Protocol):
    def value(self) -> str: ...


class _ImplA:
    def value(self) -> str:
        return "a"


class _ImplB:
    def value(self) -> str:
        return "b"


# -- services -----------------------------------------------------------------


async def test_service_inject_roundtrip() -> None:
    ctx = Context()
    impl = _ImplA()
    ctx.service(SvcA, impl)
    assert ctx.inject(SvcA) is impl
    assert ctx.has(SvcA)
    assert not ctx.has(SvcB)
    await ctx.dispose()


async def test_inject_unregistered_raises_with_protocol_name() -> None:
    ctx = Context()
    with pytest.raises(ServiceNotFoundError) as excinfo:
        ctx.inject(SvcA)
    assert excinfo.value.protocol_name == "SvcA"
    await ctx.dispose()


async def test_duplicate_service_registration_raises() -> None:
    ctx = Context()
    ctx.service(SvcA, _ImplA())
    with pytest.raises(
        DuplicateServiceError, match="service already registered for protocol 'SvcA'"
    ):
        ctx.service(SvcA, _ImplA())
    await ctx.dispose()


async def test_service_after_dispose_raises() -> None:
    ctx = Context()
    await ctx.dispose()
    with pytest.raises(ContextDisposedError):
        ctx.service(SvcA, _ImplA())


# -- events -------------------------------------------------------------------


@dataclass(frozen=True)
class Ping(Event):
    n: int = 0


@dataclass(frozen=True)
class PingChild(Ping):
    pass


@dataclass(frozen=True)
class Logged(SessionEvent):
    tag: str = ""


async def test_on_emit_invokes_handler() -> None:
    ctx = Context()
    seen: list[Ping] = []
    ctx.on(Ping, lambda ev: seen.append(ev))  # type: ignore[arg-type]
    await ctx.emit(Ping(3))
    assert seen == [Ping(3)]
    await ctx.dispose()


async def test_emit_matches_exact_type_only() -> None:
    ctx = Context()
    base_seen: list[Event] = []
    ctx.on(Ping, lambda ev: base_seen.append(ev))
    await ctx.emit(PingChild(1))
    assert base_seen == []
    await ctx.dispose()


async def test_two_handlers_run_concurrently() -> None:
    import asyncio

    ctx = Context()
    order: list[str] = []

    slow_started = asyncio.Event()
    release_slow = asyncio.Event()

    async def slow(_ev: Event) -> None:
        order.append("slow-start")
        slow_started.set()
        # Deterministic interleaving via events instead of wall-clock
        # sleeps: ``fast`` may only run once ``slow`` has started, and
        # ``slow`` may only finish once ``fast`` has recorded its run.
        await asyncio.wait_for(release_slow.wait(), timeout=5.0)
        order.append("slow-end")

    async def fast(_ev: Event) -> None:
        await asyncio.wait_for(slow_started.wait(), timeout=5.0)
        order.append("fast")
        release_slow.set()

    ctx.on(Ping, slow)
    ctx.on(Ping, fast)
    await ctx.emit(Ping())
    # Concurrent dispatch lets `fast` finish before `slow` completes.
    assert order == ["slow-start", "fast", "slow-end"]
    await ctx.dispose()


async def test_raising_handler_does_not_break_others() -> None:
    ctx = Context()
    seen: list[bool] = []

    def bad(_ev: Event) -> None:
        raise RuntimeError("boom")

    ctx.on(Ping, bad)
    ctx.on(Ping, lambda _ev: seen.append(True))
    await ctx.emit(Ping())
    assert seen == [True]
    await ctx.dispose()


# -- effects + routers ----------------------------------------------------------


async def test_effect_cleanups_run_lifo_on_dispose() -> None:
    ctx = Context()
    order: list[str] = []
    ctx.effect(lambda: order.append("one"))
    ctx.effect(lambda: order.append("two"))
    await ctx.dispose()
    assert order == ["two", "one"]


async def test_mount_router_and_routes_order() -> None:
    ctx = Context()
    r1, r2 = object(), object()
    ctx.mount_router(r1)
    ctx.mount_router(r2)
    assert ctx.routes() == [r1, r2]
    await ctx.dispose()


async def test_registration_after_dispose_raises() -> None:
    ctx = Context()
    await ctx.dispose()
    with pytest.raises(ContextDisposedError):
        ctx.on(Ping, lambda _ev: None)
    with pytest.raises(ContextDisposedError):
        ctx.effect(lambda: None)
    with pytest.raises(ContextDisposedError):
        ctx.mount_router(object())


# -- plugin lifecycle -----------------------------------------------------------


class _PluginA(Plugin):
    id = "a"

    def __init__(self) -> None:
        self.applied_with: Context | None = None
        self.disposed = False
        self.owner_at_registration: str | None = None

    async def apply(self, ctx: Context) -> None:
        self.applied_with = ctx
        ref = ctx.service(SvcA, _ImplA())
        self.owner_at_registration = ref.plugin_id
        ctx.effect(lambda: None)

    async def dispose(self) -> None:
        self.disposed = True


class _PluginB(Plugin):
    id = "b"

    async def apply(self, ctx: Context) -> None:
        ctx.service(SvcB, _ImplB())


async def test_plugin_apply_sets_config_and_attributes() -> None:
    ctx = Context()
    plug = _PluginA()
    await ctx.plugin(plug, config={"k": 1})
    assert plug.config == {"k": 1}
    assert plug.applied_with is ctx
    assert plug.owner_at_registration == "a"
    assert ctx.inject(SvcA).value() == "a"
    await ctx.dispose()
    assert plug.disposed


async def test_unload_removes_only_that_plugins_registrations() -> None:
    ctx = Context()
    a, b = _PluginA(), _PluginB()
    await ctx.plugin(a)
    await ctx.plugin(b)
    await ctx.unload("a")
    assert not ctx.has(SvcA)
    assert ctx.has(SvcB)
    assert a.disposed
    await ctx.dispose()


async def test_unload_runs_effects_lifo() -> None:
    ctx = Context()
    order: list[str] = []

    class _OrderPlug(Plugin):
        id = "ordered"

        async def apply(self, inner_ctx: Context) -> None:
            inner_ctx.effect(lambda: order.append("first"))
            inner_ctx.effect(lambda: order.append("second"))

    await ctx.plugin(_OrderPlug())
    await ctx.unload("ordered")
    assert order == ["second", "first"]
    await ctx.dispose()


async def test_dispose_unloads_every_plugin_reverse_order() -> None:
    ctx = Context()
    events: list[str] = []

    class _TrackPlug(Plugin):
        def __init__(self, plug_id: str) -> None:
            self.id = plug_id

        async def dispose(self) -> None:
            events.append(self.id)

    await ctx.plugin(_TrackPlug("first"))
    await ctx.plugin(_TrackPlug("second"))
    await ctx.dispose()
    assert events == ["second", "first"]
    assert ctx.disposed


async def test_second_dispose_is_noop() -> None:
    ctx = Context()
    calls = 0

    class _CountPlug(Plugin):
        id = "counted"

        async def dispose(self) -> None:
            nonlocal calls
            calls += 1

    await ctx.plugin(_CountPlug())
    await ctx.dispose()
    await ctx.dispose()
    assert calls == 1


async def test_failed_apply_reverses_partial_registrations() -> None:
    ctx = Context()

    class _BadPlug(Plugin):
        id = "bad"

        async def apply(self, inner_ctx: Context) -> None:
            inner_ctx.service(SvcA, _ImplA())
            raise RuntimeError("explode")

    with pytest.raises(RuntimeError, match="explode"):
        await ctx.plugin(_BadPlug())
    assert not ctx.has(SvcA)
    await ctx.dispose()


async def test_duplicate_plugin_mounting_raises() -> None:
    ctx = Context()
    a = _PluginA()
    await ctx.plugin(a)
    with pytest.raises(DuplicatePluginError, match="plugin 'a' is already mounted"):
        await ctx.plugin(_PluginA())
    await ctx.dispose()


async def test_failed_apply_reversal_failure_does_not_shadow_original_exception() -> (
    None
):
    ctx = Context()
    executed: list[str] = []

    class _ExplodingCleanupPlug(Plugin):
        id = "exploding_cleanup"

        async def apply(self, inner_ctx: Context) -> None:
            inner_ctx.service(SvcA, _ImplA())
            inner_ctx.effect(lambda: executed.append("first_cleanup"))

            def _bad_cleanup() -> None:
                executed.append("bad_cleanup")
                raise RuntimeError("cleanup exploded")

            inner_ctx.effect(_bad_cleanup)
            inner_ctx.effect(lambda: executed.append("third_cleanup"))
            raise RuntimeError("original apply failure")

    with pytest.raises(RuntimeError, match="original apply failure"):
        await ctx.plugin(_ExplodingCleanupPlug())

    # LIFO reversal order: third_cleanup, bad_cleanup (logs error), first_cleanup
    assert executed == ["third_cleanup", "bad_cleanup", "first_cleanup"]
    assert not ctx.has(SvcA)
    assert "exploding_cleanup" not in ctx._plugin_instances
    assert "exploding_cleanup" not in ctx._plugin_order
    await ctx.dispose()


async def test_plugin_load_unload_reload_cycle() -> None:
    ctx = Context()

    class _LifecyclePlug(Plugin):
        id = "cycle_plug"

        def __init__(self) -> None:
            self.ping_count = 0
            self.cleaned_up = False
            self.disposed = False

        async def apply(self, inner_ctx: Context) -> None:
            inner_ctx.service(SvcA, _ImplA())

            def _handle_ping(_ev: Event) -> None:
                self.ping_count += 1

            inner_ctx.on(Ping, _handle_ping)

            def _cleanup() -> None:
                self.cleaned_up = True

            inner_ctx.effect(_cleanup)

        async def dispose(self) -> None:
            self.disposed = True

    # 1. Mount first instance
    plug1 = _LifecyclePlug()
    await ctx.plugin(plug1)
    assert ctx.has(SvcA)
    assert ctx.inject(SvcA).value() == "a"
    await ctx.emit(Ping())
    assert plug1.ping_count == 1
    assert not plug1.cleaned_up
    assert not plug1.disposed

    # 2. Unload first instance
    await ctx.unload("cycle_plug")
    assert not ctx.has(SvcA)
    assert plug1.cleaned_up
    assert plug1.disposed

    # Verify no leftover effects or listeners respond after unload
    await ctx.emit(Ping())
    assert plug1.ping_count == 1

    # 3. Re-mount a fresh instance of the plugin on the same context
    plug2 = _LifecyclePlug()
    await ctx.plugin(plug2)
    assert ctx.has(SvcA)
    assert ctx.inject(SvcA).value() == "a"
    await ctx.emit(Ping())
    assert plug2.ping_count == 1
    assert plug1.ping_count == 1  # stale instance unaffected
    assert not plug2.cleaned_up
    assert not plug2.disposed

    # 4. Final context disposal cleans up the reloaded plugin
    await ctx.dispose()
    assert plug2.cleaned_up
    assert plug2.disposed
    assert not ctx.has(SvcA)
