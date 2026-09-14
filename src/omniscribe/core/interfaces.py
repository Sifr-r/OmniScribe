"""Core structural protocols and abstract seams.

Decouples core workflows, translation services, and lexicon components from
plugin infrastructure, concrete state backends, and queue runtimes.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class JobQueueProtocol(Protocol):
    """Abstract async job queue interface for asynchronous execution."""

    async def enqueue(
        self,
        request: Any,
        *,
        request_meta: dict[str, Any] | None = None,
        input_path: str | None = None,
    ) -> Any:
        """Enqueue a job request for async execution."""
        ...

    async def get_job(self, job_id: str) -> Any:
        """Retrieve job record or status by ID."""
        ...

    async def cancel_job(self, job_id: str) -> bool:
        """Cancel a pending or active job."""
        ...

    async def list_jobs(self, *, limit: int = 100, offset: int = 0) -> list[Any]:
        """List paginated job records."""
        ...


@runtime_checkable
class StateBackendProtocol(Protocol):
    """Abstract state persistence seam providing key-value operations."""

    async def get(self, key: str) -> Any:
        """Retrieve stored value by key."""
        ...

    async def set(self, key: str, value: Any, *, ttl: int | None = None) -> None:
        """Store or update key with value and optional TTL in seconds."""
        ...

    async def delete(self, key: str) -> bool | None:
        """Remove a key from the state backend."""
        ...


__all__ = [
    "JobQueueProtocol",
    "StateBackendProtocol",
]
