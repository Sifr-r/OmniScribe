"""Cordis-style plugin harness: Context, Event, EffectScope, Loader."""

from __future__ import annotations

from omniscribe.harness.context import Context
from omniscribe.harness.effects import EffectRef, EffectScope, effect_scope
from omniscribe.harness.errors import (
    ContextDisposedError,
    DuplicatePluginError,
    DuplicateServiceError,
    HarnessError,
    PluginLoadError,
    ServiceNotFoundError,
)
from omniscribe.harness.events import AgentEvent, CapabilityEvent, Event, SessionEvent
from omniscribe.harness.loader import Loader, PluginRow
from omniscribe.harness.plugin import Plugin

__all__ = [
    "AgentEvent",
    "CapabilityEvent",
    "Context",
    "ContextDisposedError",
    "DuplicatePluginError",
    "DuplicateServiceError",
    "EffectRef",
    "EffectScope",
    "Event",
    "HarnessError",
    "Loader",
    "Plugin",
    "PluginLoadError",
    "PluginRow",
    "ServiceNotFoundError",
    "SessionEvent",
    "effect_scope",
]
