"""Tests for OCR routes, jobs queue, and state backend capability security and integrity.

Covers:
1. P1 #2: Job capability isolation (SSE event stream and page preview require capability tokens;
   event_entry does not broadcast artifact_token).
2. P1 #4: Password redaction in Redis URLs (redact_redis_url).
3. P2 #1: TTL expiration enforcement for memory and SQLite state backends (artifacts and channels).
4. P2 #2: Disk-backed preview document cache streaming and temp file cleanup on eviction/exit.
5. P2 #3: JobQueue clear() preserves non-terminal (queued/running) jobs in both in-memory and Redis queues.
6. P2 #4: Redis job creation failure rolls back backend job record.
"""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis.aioredis
import pytest
from fastapi import HTTPException

from omniscribe.harness.context import Context
from omniscribe.plugins.jobs import (
    InMemoryJobQueue,
    JobCompleted,
    JobRecord,
)
from omniscribe.plugins.jobs_redis import (
    RedisJobQueue,
)
from omniscribe.plugins.ocr.routes import (
    _cleanup_all_preview_files,
    _preview_doc_cache,
    handle_get_document_page_preview,
    handle_get_page_preview,
    handle_subscribe_events,
)
from omniscribe.plugins.ocr.service import OCRServiceImpl, event_entry
from omniscribe.plugins.state_backend_memory import MemoryStateBackend
from omniscribe.plugins.state_backend_sqlite import SQLiteStateBackend
from omniscribe.utils.security import redact_redis_url

# ==============================================================================
# 1. P1 #4: Redis URL Password Redaction
# ==============================================================================


def test_redact_redis_url() -> None:
    assert (
        redact_redis_url("redis://:secret@localhost:6379/0")
        == "redis://:***@localhost:6379/0"
    )
    assert (
        redact_redis_url("rediss://user:pass123@redis.example.com:6380/1")
        == "rediss://user:***@redis.example.com:6380/1"
    )
    assert redact_redis_url("redis://localhost:6379/0") == "redis://localhost:6379/0"
    assert redact_redis_url("redis://localhost") == "redis://localhost"
    assert redact_redis_url("not-a-redis-url") == "not-a-redis-url"
    assert redact_redis_url("") == ""


# ==============================================================================
# 2. P1 #2: Job Capability Isolation
# ==============================================================================


def test_event_entry_does_not_broadcast_artifact_token() -> None:
    event = JobCompleted(
        job_id="job-abc", artifact_id="art-xyz", artifact_token="secret-tok"
    )
    entry = event_entry(event)
    assert entry["event"] == "job_completed"
    assert entry["data"]["job_id"] == "job-abc"
    assert entry["data"]["artifact_id"] == "art-xyz"
    assert "artifact_token" not in entry["data"]


async def test_handle_subscribe_events_requires_token() -> None:
    mock_service = MagicMock(spec=OCRServiceImpl)
    mock_record = JobRecord(
        job_id="job-123",
        status="running",
        request_meta={"result_access_token": "valid-token-123"},
    )
    mock_service.job_record = AsyncMock(return_value=mock_record)
    mock_service.event_backlog = MagicMock(return_value=[])

    # 1. Missing token -> 404
    with pytest.raises(HTTPException) as exc_info:
        await handle_subscribe_events("job-123", mock_service)
    assert exc_info.value.status_code == 404

    # 2. Invalid token -> 404
    with pytest.raises(HTTPException) as exc_info:
        await handle_subscribe_events("job-123", mock_service, token="wrong-token")
    assert exc_info.value.status_code == 404

    # 3. Valid token in query param -> 200 StreamingResponse
    resp = await handle_subscribe_events(
        "job-123", mock_service, token="valid-token-123"
    )
    assert resp.media_type == "text/event-stream"

    # 4. Valid token in Bearer authorization header -> 200
    resp_auth = await handle_subscribe_events(
        "job-123", mock_service, authorization="Bearer valid-token-123"
    )
    assert resp_auth.media_type == "text/event-stream"

    # 5. Valid token in X-Artifact-Token header -> 200
    resp_art = await handle_subscribe_events(
        "job-123", mock_service, x_artifact_token="valid-token-123"
    )
    assert resp_art.media_type == "text/event-stream"

    # 6. Valid token in X-Job-Token header -> 200
    resp_job = await handle_subscribe_events(
        "job-123", mock_service, x_job_token="valid-token-123"
    )
    assert resp_job.media_type == "text/event-stream"


async def test_handle_get_page_preview_requires_token() -> None:
    mock_service = MagicMock(spec=OCRServiceImpl)
    mock_record = JobRecord(
        job_id="job-123",
        status="complete",
        request_meta={"result_access_token": "preview-token-456"},
    )
    mock_service.job_record = AsyncMock(return_value=mock_record)
    mock_service.get_page_preview = AsyncMock(return_value=b"\x89PNGfakeimage")

    # Negative page_index -> 400
    with pytest.raises(HTTPException) as exc_info:
        await handle_get_page_preview(
            "job-123", -1, mock_service, token="preview-token-456"
        )
    assert exc_info.value.status_code == 400

    # Missing token -> 404
    with pytest.raises(HTTPException) as exc_info:
        await handle_get_page_preview("job-123", 0, mock_service)
    assert exc_info.value.status_code == 404

    # Invalid token -> 404
    with pytest.raises(HTTPException) as exc_info:
        await handle_get_page_preview("job-123", 0, mock_service, token="bad-token")
    assert exc_info.value.status_code == 404

    # Valid token -> 200
    resp = await handle_get_page_preview(
        "job-123", 0, mock_service, token="preview-token-456"
    )
    assert resp.status_code == 200
    assert resp.media_type == "image/png"
    assert resp.body == b"\x89PNGfakeimage"

    # Valid token via X-Job-Token header -> 200
    resp_header = await handle_get_page_preview(
        "job-123", 0, mock_service, x_job_token="preview-token-456"
    )
    assert resp_header.status_code == 200


# ==============================================================================
# 3. P2 #1: Token TTL Expiration in Memory and SQLite State Backends
# ==============================================================================


async def test_memory_state_backend_token_ttl_expiration() -> None:
    backend = MemoryStateBackend()

    # 1. Expired artifact
    await backend.put_artifact(
        id="art-1",
        token="tok-art-1",
        owner_job_id="job-1",
        content_type="text/plain",
        blob=b"secret data",
        ttl_seconds=50,
    )
    # Fast-forward created_at into the past
    backend._artifacts["art-1"] = replace(
        backend._artifacts["art-1"], created_at=time.time() - 100
    )
    assert await backend.get_artifact("art-1", "tok-art-1") is None

    # 2. Valid artifact
    await backend.put_artifact(
        id="art-2",
        token="tok-art-2",
        owner_job_id="job-1",
        content_type="text/plain",
        blob=b"valid data",
        ttl_seconds=300,
    )
    retrieved = await backend.get_artifact("art-2", "tok-art-2")
    assert retrieved is not None
    assert retrieved.blob == b"valid data"

    # 3. Expired channel
    await backend.put_channel(
        channel_id="chan-1",
        session_token="tok-chan-1",
        job_id="job-1",
        ttl_seconds=50,
    )
    backend._channels["chan-1"] = replace(
        backend._channels["chan-1"], created_at=time.time() - 100
    )
    assert await backend.get_channel("chan-1") is None
    assert await backend.consume_channel("chan-1", "tok-chan-1") is None


async def test_sqlite_state_backend_token_ttl_expiration(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    blob_dir = tmp_path / "blobs"
    blob_dir.mkdir(parents=True, exist_ok=True)
    backend = SQLiteStateBackend(db_path=db_path, blob_dir=blob_dir)
    await backend.open()

    try:
        # 1. Expired artifact
        await backend.put_artifact(
            id="art-sql-1",
            token="tok-sql-1",
            owner_job_id="job-1",
            content_type="text/plain",
            blob=b"sqlite expired",
            ttl_seconds=50,
        )
        assert backend._conn is not None
        backend._conn.execute(
            "UPDATE artifacts SET created_at = ? WHERE id = ?",
            (time.time() - 100, "art-sql-1"),
        )
        backend._conn.commit()
        assert await backend.get_artifact("art-sql-1", "tok-sql-1") is None

        # 2. Valid artifact
        await backend.put_artifact(
            id="art-sql-2",
            token="tok-sql-2",
            owner_job_id="job-1",
            content_type="text/plain",
            blob=b"sqlite valid",
            ttl_seconds=300,
        )
        retrieved = await backend.get_artifact("art-sql-2", "tok-sql-2")
        assert retrieved is not None
        assert retrieved.blob == b"sqlite valid"

        # 3. Expired channel
        await backend.put_channel(
            channel_id="chan-sql-1",
            session_token="tok-sql-chan-1",
            job_id="job-1",
            ttl_seconds=50,
        )
        backend._conn.execute(
            "UPDATE progress_channels SET created_at = ? WHERE channel_id = ?",
            (time.time() - 100, "chan-sql-1"),
        )
        backend._conn.commit()
        assert await backend.get_channel("chan-sql-1") is None
        assert await backend.consume_channel("chan-sql-1", "tok-sql-chan-1") is None
    finally:
        await backend.aclose()


# ==============================================================================
# 4. P2 #2: Preview Caching Streams to Disk & Cleans Up Temp Files
# ==============================================================================


async def test_preview_cache_disk_temp_files() -> None:
    import pymupdf as fitz

    # Create dummy pdf
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "Preview Test Disk")
    pdf_bytes = doc.tobytes()

    class DummyUpload:
        def __init__(self, data: bytes) -> None:
            self._data = data
            self._read = False
            self.filename = "test_doc.pdf"

        async def read(self, size: int = -1) -> bytes:
            if self._read:
                return b""
            self._read = True
            return self._data

    class DummyRequest:
        def __init__(self, upload: DummyUpload) -> None:
            self._upload = upload
            self.headers: dict[str, str] = {}

        async def form(self) -> dict[str, Any]:
            return {"file": self._upload, "page": "0", "dpi": "100"}

    req = DummyRequest(DummyUpload(pdf_bytes))
    resp = await handle_get_document_page_preview(req)  # type: ignore[arg-type]
    assert resp.status_code == 200
    doc_id = resp.headers["X-Document-Id"]

    # Verify that cache stored a Path on disk
    assert doc_id in _preview_doc_cache
    file_path, filetype, _ = _preview_doc_cache[doc_id]
    assert isinstance(file_path, Path)
    assert file_path.exists()
    assert filetype == "pdf"

    # Verify cleanup on exit removes temp file
    _cleanup_all_preview_files()
    assert len(_preview_doc_cache) == 0
    assert not file_path.exists()


# ==============================================================================
# 5. P2 #3: Clear Jobs Preserves Queued and Running Work
# ==============================================================================


async def test_inmemory_job_queue_clear_preserves_non_terminal() -> None:
    backend = MemoryStateBackend()
    ctx = Context()
    artifacts = MagicMock()
    queue = InMemoryJobQueue(ctx, backend, artifacts)

    h_pending = await queue.submit({"item": 1})
    h_running = await queue.submit({"item": 2})
    h_complete = await queue.submit({"item": 3})
    h_failed = await queue.submit({"item": 4})
    h_cancelled = await queue.submit({"item": 5})

    assert await queue.status(h_pending.job_id) is not None
    rec_running = await queue.status(h_running.job_id)
    rec_complete = await queue.status(h_complete.job_id)
    rec_failed = await queue.status(h_failed.job_id)
    rec_cancelled = await queue.status(h_cancelled.job_id)

    # Set statuses in persistent backend
    assert rec_running is not None
    assert rec_complete is not None
    assert rec_failed is not None
    assert rec_cancelled is not None
    await backend.upsert_job(replace(rec_running, status="running"))
    await backend.upsert_job(replace(rec_complete, status="complete"))
    await backend.upsert_job(replace(rec_failed, status="error"))
    await backend.upsert_job(replace(rec_cancelled, status="cancelled"))

    cleared = await queue.clear()
    assert cleared == 3  # complete, error, cancelled

    jobs_left = await queue.list_jobs()
    remaining_ids = {r.job_id for r in jobs_left}
    assert remaining_ids == {h_pending.job_id, h_running.job_id}


# ==============================================================================
# 6. P2 #4: Redis Job Creation Rolls Back on Pipeline Failure
# ==============================================================================


async def test_redis_job_queue_submit_rollback_on_failure() -> None:
    backend = MemoryStateBackend()
    ctx = Context()
    fake_redis = fakeredis.aioredis.FakeRedis()

    queue = RedisJobQueue(
        ctx=ctx,
        backend=backend,
        redis_client=fake_redis,
    )
    await queue.open()

    # Mock pipeline to raise an exception during execute
    original_pipeline = queue._client.pipeline

    class FailingPipelineContext:
        async def __aenter__(self) -> Any:
            pipe = original_pipeline(transaction=True)
            pipe.execute = AsyncMock(
                side_effect=RuntimeError("Redis pipeline write failed")
            )
            return pipe

        async def __aexit__(self, *args: Any) -> None:
            pass

    queue._client.pipeline = MagicMock(return_value=FailingPipelineContext())

    with pytest.raises(RuntimeError, match="Redis pipeline write failed"):
        await queue.submit({"order": "test"})

    # Verify backend job was rolled back (deleted)
    all_jobs = await backend.list_jobs()
    assert len(all_jobs) == 0

    await queue.aclose()
