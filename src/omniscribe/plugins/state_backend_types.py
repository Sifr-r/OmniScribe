"""StateBackend types, dataclasses, and Protocol definition.

Audit catalog: Extracted from ``state_backend.py`` to decouple domain types
from plugin harness wiring and concrete backend implementations. This breaks
import cycles cleanly without requiring bottom-of-file import workarounds.

Three persistence domains live behind one Protocol: artifacts (token-gated
blobs), jobs (async OCR job records), and progress channels (one-shot WS
handshake records).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, get_args, runtime_checkable

JobStatus = Literal["queued", "running", "complete", "error", "cancelled"]

# Derived canonical terminal-state set from ``JobStatus``.
_NON_TERMINAL_JOB_STATUSES: frozenset[str] = frozenset({"queued", "running"})
TERMINAL_JOB_STATUSES: frozenset[str] = frozenset(
    set(get_args(JobStatus)) - _NON_TERMINAL_JOB_STATUSES
)
del _NON_TERMINAL_JOB_STATUSES


@dataclass(frozen=True)
class ArtifactRecord:
    """Metadata for one stored artifact; blob bytes live elsewhere."""

    id: str
    token: str
    owner_job_id: str
    content_type: str
    created_at: float
    ttl_seconds: int


@dataclass(frozen=True)
class ArtifactBlob:
    """Artifact metadata plus its raw bytes."""

    record: ArtifactRecord
    blob: bytes


@dataclass(frozen=True)
class JobRecord:
    """One async OCR job's lifecycle state.

    Note:
        Unhashable despite ``frozen=True`` because ``request_meta`` is a mutable
        dictionary (``dict[str, Any]``). We explicitly set ``__hash__ = None``
        to mark instances as unhashable for mapping/set keys.
    """

    job_id: str
    status: JobStatus
    request_meta: dict[str, Any] = field(default_factory=dict)
    result_artifact_id: str | None = None
    result_artifact_token: str | None = None
    # Path to the original upload (set by the OCR service at submit time).
    # Used by the per-page preview route to render the source PDF/image
    # without re-uploading. ``None`` for jobs whose original has been
    # cleaned up or that were submitted before this field existed.
    input_path: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    # Set when the worker flips the job from ``queued`` → ``running``;
    # surfaced in ``JobStatusResponse.started_at``. ``None`` until the
    # first ``JobStarted`` event has fired for the job.
    started_at: float | None = None
    error: str | None = None

    __hash__ = None  # type: ignore[assignment]


@dataclass(frozen=True)
class ChannelRecord:
    """One progress channel handshake; ``consume_channel`` is one-shot."""

    channel_id: str
    session_token: str
    job_id: str
    created_at: float
    ttl_seconds: int
    consumed: bool = False


@runtime_checkable
class StateBackend(Protocol):
    """Persistence seam for artifacts, jobs, and progress channels.

    Memory and storage caps across backend implementations (Smell 6.26):
    - **In-memory backend** (:class:`~omniscribe.plugins.state_backend_memory.MemoryStateBackend`):
      Enforces a 256 MB cap (``_MEMORY_BLOB_CAP_BYTES``) to prevent process OOM on
      the Python heap in embedded/Profile 1 mode.
    - **SQLite backend** (:class:`~omniscribe.plugins.state_backend_sqlite.SQLiteStateBackend`):
      Stores blobs on disk (``<blob_dir>/<id>.bin``), bound only by filesystem storage.
    - **Redis backend** (:class:`~omniscribe.plugins.state_backend_redis.RedisStateBackend`):
      Stores blobs as binary string keys, governed by Redis server ``maxmemory`` and
      eviction policy.
    """

    # Artifacts
    async def put_artifact(
        self,
        *,
        id: str,
        token: str,
        owner_job_id: str,
        content_type: str,
        blob: bytes,
        ttl_seconds: int,
    ) -> None: ...

    async def get_artifact(self, id: str, token: str) -> ArtifactBlob | None: ...

    async def delete_artifact(self, id: str) -> None: ...

    async def prune_expired_artifacts(self, now: float) -> int: ...

    # Jobs
    async def upsert_job(self, record: JobRecord) -> None: ...

    async def get_job(self, job_id: str) -> JobRecord | None: ...

    async def list_jobs(
        self, *, limit: int = 100, offset: int = 0
    ) -> list[JobRecord]: ...

    async def clear_jobs(self) -> int: ...

    async def delete_job(self, job_id: str) -> None: ...

    # Progress channels
    async def put_channel(
        self, channel_id: str, session_token: str, job_id: str, ttl_seconds: int
    ) -> None: ...

    async def get_channel(self, channel_id: str) -> ChannelRecord | None: ...

    async def consume_channel(
        self, channel_id: str, session_token: str
    ) -> ChannelRecord | None: ...

    async def delete_channel(self, channel_id: str) -> None: ...

    async def prune_expired_channels(self, now: float) -> int: ...

    async def aclose(self) -> None: ...


__all__ = [
    "TERMINAL_JOB_STATUSES",
    "ArtifactBlob",
    "ArtifactRecord",
    "ChannelRecord",
    "JobRecord",
    "JobStatus",
    "StateBackend",
    "get_args",
]
