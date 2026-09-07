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
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from omniscribe.config import load_settings
from omniscribe.harness.context import Context
from omniscribe.harness.plugin import Plugin

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
            redis_url = (
                str(self.config.get("redis_url") or "").strip() or settings.redis_url
            )
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


def _redact_redis_url(url: str) -> str:
    """Strip the password component from a ``redis://user:pass@host:port/db`` URL.

    The password is the only sensitive bit; the host/port/db
    are useful for log triage. Returns the URL with ``:***@``
    replacing ``:password@``.
    """
    if "@" not in url:
        return url
    scheme, _, rest = url.partition("://")
    if "@" not in rest:
        return url
    userinfo, _, hostpart = rest.partition("@")
    if ":" in userinfo:
        _, _, _ = userinfo.partition(":")
        return f"{scheme}://:***@{hostpart}"
    return url


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
    "SQLiteStateBackend",
    "StateBackend",
    "StateBackendPlugin",
    "StateBackendSchema",
    "get_args",
    "plugin",
]
