"""ArtifactStore plugin — opaque token-gated blob storage over StateBackend.

Ids are ``uuid.uuid4().hex``; tokens are ``secrets.token_urlsafe(32)``.
Every ``put`` emits :class:`ArtifactCreated` (a ``SessionEvent``) so audit
consumers see durable storage facts without coupling to the store.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from dataclasses import dataclass
from typing import NamedTuple, Protocol, runtime_checkable

from pydantic import BaseModel

from omniscribe.harness.context import Context
from omniscribe.harness.events import SessionEvent
from omniscribe.harness.plugin import Plugin
from omniscribe.plugins.state_backend_types import ArtifactBlob, StateBackend

_LOGGER = logging.getLogger("omniscribe.plugins.artifacts")


class ArtifactHandle(NamedTuple):
    """Opaque id plus one download token for one stored artifact."""

    id: str
    token: str


@dataclass(frozen=True)
class ArtifactCreated(SessionEvent):
    """Durable fact: a new artifact was stored."""

    artifact_id: str
    owner_job_id: str
    content_type: str


@runtime_checkable
class ArtifactStore(Protocol):
    """High-level artifact seam on top of the StateBackend."""

    async def put(
        self,
        blob: bytes,
        *,
        content_type: str,
        owner_job_id: str,
        ttl_seconds: int | None = None,
    ) -> ArtifactHandle: ...

    async def get(self, artifact_id: str, token: str) -> ArtifactBlob | None: ...

    async def delete(self, artifact_id: str) -> None: ...


class ArtifactStoreImpl:
    """Delegates persistence to the injected StateBackend."""

    def __init__(
        self, ctx: Context, backend: StateBackend, *, default_ttl_seconds: int
    ) -> None:
        self._ctx = ctx
        self._backend = backend
        self._default_ttl_seconds = default_ttl_seconds

    async def put(
        self,
        blob: bytes,
        *,
        content_type: str,
        owner_job_id: str,
        ttl_seconds: int | None = None,
    ) -> ArtifactHandle:
        handle = ArtifactHandle(id=uuid.uuid4().hex, token=secrets.token_urlsafe(32))
        await self._backend.put_artifact(
            id=handle.id,
            token=handle.token,
            owner_job_id=owner_job_id,
            content_type=content_type,
            blob=blob,
            ttl_seconds=(
                ttl_seconds if ttl_seconds is not None else self._default_ttl_seconds
            ),
        )
        await self._ctx.emit(
            ArtifactCreated(
                artifact_id=handle.id,
                owner_job_id=owner_job_id,
                content_type=content_type,
            )
        )
        _LOGGER.debug("artifact stored id=%s job=%s", handle.id, owner_job_id)
        return handle

    async def get(self, artifact_id: str, token: str) -> ArtifactBlob | None:
        return await self._backend.get_artifact(artifact_id, token)

    async def delete(self, artifact_id: str) -> None:
        await self._backend.delete_artifact(artifact_id)


class ArtifactsSchema(BaseModel):
    default_ttl_seconds: int = 86_400


class ArtifactsPlugin(Plugin):
    """Registers the composite ArtifactStore over the injected StateBackend."""

    Schema = ArtifactsSchema

    async def apply(self, ctx: Context) -> None:
        backend = ctx.inject(StateBackend)
        store = ArtifactStoreImpl(
            ctx,
            backend,
            default_ttl_seconds=int(self.config.get("default_ttl_seconds", 86_400)),
        )
        ctx.service(ArtifactStore, store)


plugin = ArtifactsPlugin()
