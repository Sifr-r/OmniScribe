"""StateBackend Protocol + plugin (frontend).

Three persistence domains live behind one Protocol: artifacts (token-gated
blobs), jobs (async OCR job records), and progress channels (one-shot WS
handshake records). Selection is via the plugin row config
(``OMNISCRIBE_STATE_BACKEND=memory|sqlite|redis``).

Audit catalog:
- Domain types, dataclasses, and the Protocol live in ``state_backend_types.py``.
- Concrete implementations live in ``state_backend_memory.py``,
  ``state_backend_sqlite.py``, and ``state_backend_redis.py``.
- This module configures the plugin and re-exports the public API for backwards compatibility.

Sprint 4 (RFC 003, 2026-09-07): the redis backend ships for
Profile 4 multi-worker LAN deployments. The ``OMNISCRIBE_STATE_BACKEND=redis``
config (paired with ``REDIS_URL=redis://...``) is now a first-class
option. The implementation lives in ``state_backend_redis.py``;
this module only handles plugin-side wiring.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import BaseModel

from omniscribe.config import RuntimeSettings, load_settings
from omniscribe.harness.plugin import Plugin
from omniscribe.utils.security import redact_redis_url as _redact_redis_url

if TYPE_CHECKING:
    # ``Context`` is only used as a type annotation; ``from __future__
    # import annotations`` keeps the annotation lazy so we don't pull
    # the harness in at module-load time (which would loop through
    # ``harness.loader._autoregister_builtin_plugins`` and back into
    # this module while it is still mid-import).
    from omniscribe.harness.context import Context

from .state_backend_memory import MemoryStateBackend

# Sprint 4 (RFC 003): RedisStateBackend is a runtime dep
# (``redis>=8.1.0`` is a base dep, see ``pyproject.toml``), so
# we import it at module level alongside the other backends.
from .state_backend_redis import RedisStateBackend
from .state_backend_sqlite import SQLiteStateBackend
from .state_backend_types import (
    TERMINAL_JOB_STATUSES,
    ArtifactBlob,
    ArtifactRecord,
    ChannelRecord,
    JobRecord,
    JobStatus,
    StateBackend,
    get_args,
)

_LOGGER = logging.getLogger("omniscribe.plugins.state")

_ALLOWED_BACKENDS = {"memory", "sqlite", "redis"}


@dataclass(frozen=True)
class ResolvedBroker:
    """The broker a plugin must connect with, plus the settings it resolved from.

    ``settings`` is the process's single resolved
    :class:`~omniscribe.config.RuntimeSettings` object (see
    :func:`process_settings`). Plugins that need more than the mode and
    the URL — the progress router's CORS origins, for instance — read
    them from this object instead of calling ``load_settings()`` again,
    which is what let a worker's ``--redis-url`` override split the
    state backend, the job queue, and the progress broker across three
    different Redis instances.
    """

    mode: str
    redis_url: str
    settings: RuntimeSettings

    @property
    def redis_mode(self) -> bool:
        """``True`` when this process dispatches over Redis."""
        return self.mode == "redis"


def process_settings(ctx: Context) -> RuntimeSettings:
    """Return the settings object this process is running on.

    ``RuntimePlugin`` (:mod:`omniscribe.plugins.runtime`) owns the
    resolved settings of a booted harness, and it is the only place a
    caller-supplied override (``omniscribe-worker --redis-url``) can be
    published: ``load_settings()`` re-reads the environment, which never
    sees it. Broker resolution therefore goes through here so every
    plugin in the process agrees on one broker.

    Falls back to ``load_settings()`` when the runtime plugin is not
    mounted (bare ``Context`` in tests, embedded use).
    """
    from omniscribe.plugins.runtime import RuntimeService

    if ctx.has(RuntimeService):
        return cast(RuntimeSettings, ctx.inject(RuntimeService).settings)
    return load_settings()


def resolve_broker_config(ctx: Context, config: Mapping[str, Any]) -> ResolvedBroker:
    """Resolve the queue/progress broker under one shared precedence rule.

    The same two steps apply to ``mode`` and ``redis_url``:

    1. the plugin's own config row, whenever it carries a non-empty value;
    2. otherwise the process's resolved settings (:func:`process_settings`),
       which already carry the environment and any published override.

    This is the precedence the redis branch of this module already used
    for ``redis_url`` ("config wins, else ``settings``"), lifted out so
    the jobs and progress plugins resolve the broker the same way instead
    of re-deriving it from ``load_settings()``. Explicit plugin config
    therefore always beats the environment, and an absent value defers
    to it — including for ``mode``, where the two plugins previously
    disagreed about whether an explicit ``inprocess`` was a decision or
    a placeholder.
    """
    settings = process_settings(ctx)
    mode = str(config.get("mode") or "").strip().lower()
    if not mode:
        mode = str(settings.jobs_mode or "").strip().lower()
    if mode not in {"inprocess", "redis"}:
        raise ValueError("jobs mode must be 'inprocess' or 'redis'")
    redis_url = str(config.get("redis_url") or "").strip()
    if not redis_url:
        redis_url = str(settings.redis_url or "").strip()
    return ResolvedBroker(mode=mode, redis_url=redis_url, settings=settings)


class StateBackendSchema(BaseModel):
    backend: Literal["memory", "sqlite", "redis"] = "memory"
    sqlite_path: str = ""
    # Redis overrides (Profile 4). Empty/unset falls back to the
    # env-driven ``REDIS_URL`` / ``OMNISCRIBE_REDIS_TLS`` settings.
    redis_url: str = ""
    redis_tls: bool = False


class StateBackendPlugin(Plugin):
    """Builds the configured backend and registers it under ``StateBackend``."""

    Schema = StateBackendSchema

    async def apply(self, ctx: Context) -> None:
        backend_name = str(self.config.get("backend", "memory")).strip().lower()
        if backend_name not in _ALLOWED_BACKENDS:
            raise ValueError(
                "state backend must be one of "
                f"{sorted(_ALLOWED_BACKENDS)} in this build, got {backend_name!r}"
            )
        settings = load_settings()
        if backend_name == "memory":
            # Phase 2.3 (2026-09-05): the default flipped to ``sqlite``;
            # anyone still on ``memory`` is opting into ephemeral
            # state. Make that loud at boot so a server restart
            # doesn't silently drop job history.
            _LOGGER.warning(
                "STATE BACKEND IS IN-MEMORY: a server restart will lose "
                "all job records, artifact metadata, and progress "
                "channel state. Set OMNISCRIBE_STATE_BACKEND=sqlite (or "
                "omit it; sqlite is the default) for durable persistence. "
                "See docs/TROUBLESHOOTING.md#async-translation-result-is-"
                "gone-after-restart."
            )
            backend: StateBackend = MemoryStateBackend()
        elif backend_name == "sqlite":
            sqlite_path = str(self.config.get("sqlite_path") or "").strip()
            # C-3 audit fix: validate sqlite_path so a misconfigured
            # operator (or a malicious patch file) cannot point the
            # database at an arbitrary filesystem location. The
            # default path under ``settings.artifact_base_dir`` is
            # always allowed; an operator-supplied override must be
            # an absolute path whose parent directory is the same as
            # ``artifact_base_dir`` (no path-traversal escape). A bare
            # file at the artifact base is also accepted; a file
            # *outside* is rejected.
            db_path: Path
            if sqlite_path:
                candidate = Path(sqlite_path).expanduser().resolve(strict=False)
                base = settings.artifact_base_dir.expanduser().resolve(strict=False)
                try:
                    candidate.relative_to(base)
                except ValueError as exc:
                    raise RuntimeError(
                        f"OMNISCRIBE_STATE_BACKEND sqlite_path={sqlite_path!r} "
                        f"resolves outside the artifact base {base}. "
                        "Pin the file under the artifact directory or "
                        "set sqlite_path to a path inside it."
                    ) from exc
                db_path = candidate
            else:
                db_path = settings.artifact_base_dir / "omniscribe-state.db"
            sqlite_backend = SQLiteStateBackend(
                db_path=db_path, blob_dir=settings.artifact_base_dir
            )
            await sqlite_backend.open()
            backend = sqlite_backend
            _LOGGER.info("state backend sqlite db=%s", db_path)
        else:  # redis
            # Sprint 4 (RFC 003): the redis backend is for Profile 4
            # multi-worker LAN deployments. The ``REDIS_URL`` is in
            # ``settings.redis_url`` (default
            # ``redis://localhost:6379/0``); the operator overrides
            # it for non-loopback deployments. ``open()`` pings the
            # server so a misconfigured URL fails loud at boot, not
            # on the first request.
            redis_url = resolve_broker_config(ctx, self.config).redis_url
            redis_tls = bool(self.config.get("redis_tls")) or settings.redis_tls
            redis_backend = RedisStateBackend(redis_url=redis_url, redis_tls=redis_tls)
            await redis_backend.open()
            backend = redis_backend
            _LOGGER.info(
                "state backend redis url=%s tls=%s",
                _redact_redis_url(redis_url),
                redis_tls,
            )
        ctx.service(StateBackend, backend)
        ctx.effect(backend.aclose)


plugin = StateBackendPlugin()

__all__ = [
    "TERMINAL_JOB_STATUSES",
    "ArtifactBlob",
    "ArtifactRecord",
    "ChannelRecord",
    "JobRecord",
    "JobStatus",
    "MemoryStateBackend",
    "RedisStateBackend",
    "ResolvedBroker",
    "SQLiteStateBackend",
    "StateBackend",
    "StateBackendPlugin",
    "StateBackendSchema",
    "get_args",
    "plugin",
    "process_settings",
    "resolve_broker_config",
]
