"""In-memory ``StateBackend`` implementation.

Audit catalog (Sprint 6 long-file split): separated from
``state_backend.py`` so the file at the ``state_backend`` import
path is just the Protocol + dataclasses + plugin + re-exports.
The two backends and the SQLite row helpers live in their own
modules so each can be reasoned about in isolation.

Public surface preserved: ``MemoryStateBackend`` is re-exported
from ``omniscribe.plugins.state_backend``.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import replace

from .state_backend_types import (
    ArtifactBlob,
    ArtifactRecord,
    ChannelRecord,
    JobRecord,
)

_MEMORY_BLOB_CAP_BYTES = 256 * 1024 * 1024


class MemoryStateBackend:
    """Default in-process backend; blobs capped per artifact for safety."""

    def __init__(self) -> None:
        """Initialize empty in-process dicts; safe to construct any time.

        The backend is intentionally lock-free on construction: the
        asyncio.Lock is created here so it binds to the running loop
        that first awaits a method, but no other state is touched.
        """
        self._lock = asyncio.Lock()
        self._artifacts: dict[str, ArtifactRecord] = {}
        self._blobs: dict[str, bytes] = {}
        self._jobs: dict[str, JobRecord] = {}
        self._channels: dict[str, ChannelRecord] = {}

    # -- artifacts ----------------------------------------------------------

    async def put_artifact(
        self,
        *,
        id: str,
        token: str,
        owner_job_id: str,
        content_type: str,
        blob: bytes,
        ttl_seconds: int,
    ) -> None:
        """Store an artifact in process memory.

        Raises ``ValueError`` if the blob exceeds the per-artifact
        memory cap (256 MiB by default). The cap protects the process
        from a runaway client uploading a multi-GB blob to RAM.
        """
        if len(blob) > _MEMORY_BLOB_CAP_BYTES:
            raise ValueError(
                f"artifact {id!r} exceeds the {_MEMORY_BLOB_CAP_BYTES}-byte "
                "in-memory blob cap"
            )
        async with self._lock:
            self._artifacts[id] = ArtifactRecord(
                id=id,
                token=token,
                owner_job_id=owner_job_id,
                content_type=content_type,
                created_at=time.time(),
                ttl_seconds=ttl_seconds,
            )
            self._blobs[id] = blob

    async def get_artifact(self, id: str, token: str) -> ArtifactBlob | None:
        """Return an artifact if the id+token pair matches, else ``None``."""
        async with self._lock:
            record = self._artifacts.get(id)
            if record is None or not secrets.compare_digest(record.token, token):
                return None
            if time.time() >= record.created_at + record.ttl_seconds:
                return None
            return ArtifactBlob(record=record, blob=self._blobs[id])

    async def delete_artifact(self, id: str) -> None:
        """Remove the artifact metadata and blob from memory (no-op if absent)."""
        async with self._lock:
            self._artifacts.pop(id, None)
            self._blobs.pop(id, None)

    async def prune_expired_artifacts(self, now: float) -> int:
        """Drop artifacts whose TTL has elapsed. Returns the count pruned."""
        async with self._lock:
            expired = [
                artifact_id
                for artifact_id, record in self._artifacts.items()
                if now >= record.created_at + record.ttl_seconds
            ]
            for artifact_id in expired:
                self._artifacts.pop(artifact_id, None)
                self._blobs.pop(artifact_id, None)
            return len(expired)

    # -- jobs -----------------------------------------------------------------

    async def upsert_job(self, record: JobRecord) -> None:
        """Insert or replace the in-memory job record keyed by ``record.job_id``."""
        async with self._lock:
            self._jobs[record.job_id] = record

    async def get_job(self, job_id: str) -> JobRecord | None:
        """Return the job record for ``job_id`` or ``None`` if absent."""
        async with self._lock:
            return self._jobs.get(job_id)

    async def list_jobs(self, *, limit: int = 100, offset: int = 0) -> list[JobRecord]:
        """Return jobs newest-first, paginated by ``limit``/``offset``."""
        async with self._lock:
            ordered = sorted(
                self._jobs.values(), key=lambda r: r.created_at, reverse=True
            )
            return ordered[offset : offset + limit]

    async def clear_jobs(self) -> int:
        """Remove every in-memory job record. Returns the count removed."""
        async with self._lock:
            count = len(self._jobs)
            self._jobs.clear()
            return count

    async def delete_job(self, job_id: str) -> None:
        """Remove a single job record (no-op if absent)."""
        async with self._lock:
            self._jobs.pop(job_id, None)

    # -- channels ---------------------------------------------------------------

    async def put_channel(
        self, channel_id: str, session_token: str, job_id: str, ttl_seconds: int
    ) -> None:
        """Create or replace a progress channel record (consumed=False)."""
        async with self._lock:
            self._channels[channel_id] = ChannelRecord(
                channel_id=channel_id,
                session_token=session_token,
                job_id=job_id,
                created_at=time.time(),
                ttl_seconds=ttl_seconds,
            )

    async def get_channel(self, channel_id: str) -> ChannelRecord | None:
        """Return the channel record or ``None``. Does NOT consume."""
        async with self._lock:
            record = self._channels.get(channel_id)
            if record is None:
                return None
            if time.time() >= record.created_at + record.ttl_seconds:
                return None
            return record

    async def consume_channel(
        self, channel_id: str, session_token: str
    ) -> ChannelRecord | None:
        """Return + atomically mark the channel as consumed if the token matches."""
        async with self._lock:
            record = self._channels.get(channel_id)
            if (
                record is None
                or record.consumed
                or not secrets.compare_digest(record.session_token, session_token)
            ):
                return None
            if time.time() >= record.created_at + record.ttl_seconds:
                return None
            self._channels[channel_id] = replace(record, consumed=True)
            return record

    async def delete_channel(self, channel_id: str) -> None:
        """Remove the channel record (no-op if absent)."""
        async with self._lock:
            self._channels.pop(channel_id, None)

    async def prune_expired_channels(self, now: float) -> int:
        """Drop channels whose TTL has elapsed. Returns the count pruned."""
        async with self._lock:
            expired = [
                channel_id
                for channel_id, record in self._channels.items()
                if now >= record.created_at + record.ttl_seconds
            ]
            for channel_id in expired:
                self._channels.pop(channel_id, None)
            return len(expired)

    async def aclose(self) -> None:
        """No-op: the in-memory backend has no external resources to release."""


__all__ = ["MemoryStateBackend"]
