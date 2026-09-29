"""Tests for P1-1, P1-2, P2-11, and P2-12 queue and worker enhancements.

Validates:
- [P1-1] OCR payload spool path validation in deserialize_payload().
- [P1-2] Persistence order: JobRecord written to backend BEFORE calling queue.complete/fail.
- [P2-11] Atomic requeue with lease_owner check in Redis and backend update.
- [P2-12] Cancellation of queued jobs cleans up spooled input paths.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis.aioredis
import pytest

from omniscribe.config import RuntimeSettings
from omniscribe.harness.context import Context
from omniscribe.plugins import artifacts as art_plugin
from omniscribe.plugins.artifacts import ArtifactStore
from omniscribe.plugins.jobs import (
    JobOutcome,
    JobQueue,
    JobRunner,
)
from omniscribe.plugins.jobs_redis import (
    KEY_ACTIVE,
    KEY_LEASE_PREFIX,
    KEY_PAYLOAD_PREFIX,
    KEY_QUEUE,
    RedisJobQueue,
    deserialize_payload,
)
from omniscribe.plugins.ocr.schemas import OCRRequest
from omniscribe.plugins.ocr.service import OCRServiceImpl, _OcrPayload
from omniscribe.plugins.state_backend import JobRecord
from omniscribe.plugins.state_backend_redis import RedisStateBackend
from omniscribe.plugins.state_backend_types import StateBackend
from omniscribe.worker import _execute_job, _handle_job_failure


@pytest.fixture
async def fake_redis() -> AsyncIterator[fakeredis.aioredis.FakeRedis]:
    fake = fakeredis.aioredis.FakeRedis()
    try:
        yield fake
    finally:
        await fake.aclose()


@pytest.fixture
async def harness(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> AsyncIterator[dict[str, Any]]:
    ctx = Context()
    backend = RedisStateBackend(redis_url="redis://fake:6379/0")
    backend._redis = fake_redis
    await backend.open()
    ctx.service(StateBackend, backend)

    await ctx.plugin(art_plugin.ArtifactsPlugin(), config={})
    artifacts = ctx.inject(ArtifactStore)

    queue = RedisJobQueue(
        ctx,
        backend,
        artifacts,
        redis_client=fake_redis,
        visibility_timeout_seconds=5.0,
    )
    await queue.open()
    ctx.service(JobQueue, queue)

    try:
        yield {
            "ctx": ctx,
            "backend": backend,
            "artifacts": artifacts,
            "queue": queue,
            "redis": fake_redis,
        }
    finally:
        await queue.aclose()
        await backend.aclose()


# ------------------------------------------------------------------------------
# [P1-1] Spool Path Validation in deserialize_payload()
# ------------------------------------------------------------------------------


def test_deserialize_ocr_payload_missing_or_empty_path_raises() -> None:
    """Missing or empty input_path in OCR envelope raises ValueError."""
    with pytest.raises(ValueError, match="Invalid or unsafe input_path in OCR payload"):
        deserialize_payload(json.dumps({"__type__": "ocr", "input_path": ""}))

    with pytest.raises(ValueError, match="Invalid or unsafe input_path in OCR payload"):
        deserialize_payload(json.dumps({"__type__": "ocr", "input_path": "   "}))

    with pytest.raises(ValueError, match="Invalid or unsafe input_path in OCR payload"):
        deserialize_payload(json.dumps({"__type__": "ocr"}))

    with pytest.raises(ValueError, match="Invalid or unsafe input_path in OCR payload"):
        deserialize_payload(json.dumps({"__type__": "ocr", "input_path": None}))


def test_deserialize_ocr_payload_root_or_spool_root_raises() -> None:
    """Root directory or spool root itself in OCR envelope raises ValueError."""
    spool_root = str(Path(tempfile.gettempdir()).resolve())

    # Spool root itself
    with pytest.raises(ValueError, match="Invalid or unsafe input_path in OCR payload"):
        deserialize_payload(json.dumps({"__type__": "ocr", "input_path": spool_root}))

    # Filesystem root
    with pytest.raises(ValueError, match="Invalid or unsafe input_path in OCR payload"):
        deserialize_payload(json.dumps({"__type__": "ocr", "input_path": "/"}))


def test_deserialize_ocr_payload_outside_spool_or_traversal_raises() -> None:
    """Path outside spool or attempting directory traversal raises ValueError."""
    # System path outside spool
    with pytest.raises(ValueError, match="Invalid or unsafe input_path in OCR payload"):
        deserialize_payload(
            json.dumps({"__type__": "ocr", "input_path": "/etc/passwd"})
        )

    # Traversal attempting to escape tempdir
    escaped = str(Path(tempfile.gettempdir()).resolve().parent / "escaped.pdf")
    with pytest.raises(ValueError, match="Invalid or unsafe input_path in OCR payload"):
        deserialize_payload(json.dumps({"__type__": "ocr", "input_path": escaped}))


def test_deserialize_ocr_payload_valid_paths_succeed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Valid paths under trusted spool directories succeed."""
    # Under system tempdir
    sub_dir = Path(tempfile.gettempdir()).resolve() / "omniscribe-ocr-test1"
    sub_file = sub_dir / "input.pdf"
    env1 = json.dumps(
        {"__type__": "ocr", "input_path": str(sub_file), "job_id": "job-1"}
    )
    res1 = deserialize_payload(
        env1, expected_job_id="job-1", expected_input_path=sub_file
    )
    assert isinstance(res1, _OcrPayload)
    assert res1.input_path == sub_file.resolve()

    # Under OMNISCRIBE_SPOOL_DIR
    spool_dir = tmp_path / "custom_spool"
    spool_dir.mkdir()
    monkeypatch.setenv("OMNISCRIBE_SPOOL_DIR", str(spool_dir))
    job_dir = spool_dir / "omniscribe-ocr-test2"
    job_dir.mkdir()
    spool_file = job_dir / "job.pdf"
    env2 = json.dumps(
        {"__type__": "ocr", "input_path": str(spool_file), "job_id": "job-2"}
    )
    res2 = deserialize_payload(
        env2, expected_job_id="job-2", expected_input_path=spool_file
    )
    assert isinstance(res2, _OcrPayload)
    assert res2.input_path == spool_file.resolve()

    # Under OMNISCRIBE_ARTIFACT_DIR
    art_dir = tmp_path / "custom_artifacts"
    art_dir.mkdir()
    monkeypatch.setenv("OMNISCRIBE_ARTIFACT_DIR", str(art_dir))
    art_file = art_dir / "omniscribe-ocr-job" / "input.pdf"
    env3 = json.dumps({"__type__": "ocr", "input_path": str(art_file), "job_id": "job"})
    res3 = deserialize_payload(
        env3, expected_job_id="job", expected_input_path=art_file
    )
    assert isinstance(res3, _OcrPayload)
    assert res3.input_path == art_file.resolve()


def test_deserialize_ocr_payload_requires_authoritative_claim_binding() -> None:
    """Payload-declared identifiers cannot authorize an OCR input path."""
    work_dir = Path(tempfile.gettempdir()).resolve() / "omniscribe-ocr-foreign"
    work_file = work_dir / "input.pdf"
    with pytest.raises(
        ValueError,
        match="OCR payload requires an authoritative claimed job binding",
    ):
        deserialize_payload(
            json.dumps(
                {
                    "__type__": "ocr",
                    "input_path": str(work_file),
                    "job_id": "payload-job",
                    "submission_id": "foreign",
                }
            )
        )

    with pytest.raises(
        ValueError,
        match="OCR payload job_id does not match claimed job",
    ):
        deserialize_payload(
            json.dumps(
                {
                    "__type__": "ocr",
                    "input_path": str(work_file),
                    "job_id": "payload-job",
                }
            ),
            expected_job_id="claimed-job",
            expected_input_path=work_file,
        )


async def test_claim_rejects_forged_cross_job_ocr_input_path(
    harness: dict[str, Any],
) -> None:
    """A payload cannot replace its persisted input path with another job's path."""
    queue: RedisJobQueue = harness["queue"]
    backend: StateBackend = harness["backend"]
    fake_redis = harness["redis"]
    legitimate_dir = Path(tempfile.mkdtemp(prefix="omniscribe-ocr-legitimate-"))
    victim_dir = Path(tempfile.mkdtemp(prefix="omniscribe-ocr-victim-"))
    legitimate_input = legitimate_dir / "input.pdf"
    victim_input = victim_dir / "input.pdf"
    legitimate_input.write_bytes(b"%PDF-legitimate")
    victim_input.write_bytes(b"%PDF-victim")

    try:
        payload = _OcrPayload(
            submission_id="legitimate",
            input_path=legitimate_input,
            filename="input.pdf",
            request=OCRRequest(),
        )
        handle = await queue.submit(payload, input_path=str(legitimate_input))
        forged_payload = {
            "__type__": "ocr",
            "submission_id": "victim",
            "job_id": handle.job_id,
            "input_path": str(victim_input),
            "filename": "input.pdf",
            "request": OCRRequest().model_dump(),
        }
        await fake_redis.set(
            f"{KEY_PAYLOAD_PREFIX}{handle.job_id}", json.dumps(forged_payload)
        )

        claim = await queue.claim(worker_id="worker-security-test")
        assert claim is None

        record = await backend.get_job(handle.job_id)
        assert record is not None
        assert record.status == "error"
        assert victim_input.read_bytes() == b"%PDF-victim"
        assert victim_dir.exists()
        assert not legitimate_dir.exists()
        assert await fake_redis.get(f"{KEY_PAYLOAD_PREFIX}{handle.job_id}") is None
    finally:
        shutil.rmtree(legitimate_dir, ignore_errors=True)
        shutil.rmtree(victim_dir, ignore_errors=True)


# ------------------------------------------------------------------------------
# [P1-2] Persistence Order in worker.py
# ------------------------------------------------------------------------------


async def test_execute_job_completion_persists_backend_first() -> None:
    """On success, backend.upsert_job(status="complete") must be called BEFORE queue.complete."""
    call_order: list[str] = []

    ctx = Context()
    job_id = "job-order-test"
    record = JobRecord(job_id=job_id, status="running")

    backend = MagicMock(spec=StateBackend)
    backend.get_job = AsyncMock(return_value=record)

    async def mock_upsert_job(rec: JobRecord) -> None:
        if rec.status == "complete":
            call_order.append("backend.upsert_job_complete")

    backend.upsert_job = AsyncMock(side_effect=mock_upsert_job)

    queue = MagicMock(spec=RedisJobQueue)
    queue.owns_lease = AsyncMock(return_value=True)
    queue.is_cancelled = MagicMock(return_value=False)

    async def mock_complete(jid: str, *, lease_owner: str | None = None) -> bool:
        call_order.append("queue.complete")
        return True

    queue.complete = AsyncMock(side_effect=mock_complete)

    artifacts = MagicMock(spec=ArtifactStore)
    handle_mock = MagicMock()
    handle_mock.id = "art-1"
    handle_mock.token = "tok-1"
    artifacts.put = AsyncMock(return_value=handle_mock)

    async def dummy_runner(req: Any) -> JobOutcome:
        return JobOutcome(blob=b"data", content_type="application/json")

    ctx.service(JobRunner, dummy_runner)

    await _execute_job(
        job_id,
        {"filename": "test.pdf"},
        ctx,
        queue,
        backend,
        artifacts,
        lease_owner="worker-1",
    )

    assert call_order == ["backend.upsert_job_complete", "queue.complete"]


async def test_execute_job_completion_does_not_overwrite_cancelled_status() -> None:
    """Worker completion does not overwrite a cancelled status with complete."""
    ctx = Context()
    job_id = "job-cancelled-no-overwrite"
    record = JobRecord(job_id=job_id, status="cancelled")

    backend = MagicMock(spec=StateBackend)
    backend.get_job = AsyncMock(return_value=record)
    backend.upsert_job = AsyncMock()

    queue = MagicMock(spec=RedisJobQueue)
    queue.owns_lease = AsyncMock(return_value=True)
    queue.is_cancelled = MagicMock(return_value=False)
    queue.complete = AsyncMock(return_value=True)

    artifacts = MagicMock(spec=ArtifactStore)
    handle_mock = MagicMock()
    handle_mock.id = "art-1"
    handle_mock.token = "tok-1"
    artifacts.put = AsyncMock(return_value=handle_mock)

    async def dummy_runner(req: Any) -> JobOutcome:
        return JobOutcome(blob=b"data", content_type="application/json")

    ctx.service(JobRunner, dummy_runner)

    await _execute_job(
        job_id,
        {"filename": "test.pdf"},
        ctx,
        queue,
        backend,
        artifacts,
        lease_owner="worker-1",
    )

    # Worker completion must NOT overwrite cancelled status with complete
    for call in backend.upsert_job.call_args_list:
        rec = call.args[0]
        assert rec.status != "complete"
    assert backend.upsert_job.call_count == 0


async def test_execute_job_cooperative_cancel_persists_backend_first() -> None:
    """On cooperative cancel, backend.upsert_job(status="cancelled") is called BEFORE queue.fail."""
    call_order: list[str] = []

    ctx = Context()
    job_id = "job-cancel-order"
    record = JobRecord(job_id=job_id, status="running")

    backend = MagicMock(spec=StateBackend)
    backend.get_job = AsyncMock(return_value=record)

    async def mock_upsert_job(rec: JobRecord) -> None:
        if rec.status == "cancelled":
            call_order.append("backend.upsert_job_cancelled")

    backend.upsert_job = AsyncMock(side_effect=mock_upsert_job)

    queue = MagicMock(spec=RedisJobQueue)
    queue.owns_lease = AsyncMock(return_value=True)
    queue.is_cancelled = MagicMock(return_value=True)

    async def mock_fail(
        jid: str, *, error: str = "", lease_owner: str | None = None
    ) -> bool:
        call_order.append("queue.fail")
        return True

    queue.fail = AsyncMock(side_effect=mock_fail)

    artifacts = MagicMock(spec=ArtifactStore)

    async def dummy_runner(req: Any) -> JobOutcome:
        return JobOutcome(blob=b"data", content_type="application/json")

    ctx.service(JobRunner, dummy_runner)

    await _execute_job(
        job_id,
        {"filename": "test.pdf"},
        ctx,
        queue,
        backend,
        artifacts,
        lease_owner="worker-1",
    )

    assert call_order == ["backend.upsert_job_cancelled", "queue.fail"]


async def test_handle_job_failure_persists_backend_first() -> None:
    """On failure/cancel in _handle_job_failure, backend.upsert_job is called BEFORE queue.fail."""
    # 1. Failure path
    call_order_fail: list[str] = []
    ctx = Context()
    job_id = "job-fail-order"
    record = JobRecord(job_id=job_id, status="running")

    backend = MagicMock(spec=StateBackend)
    backend.get_job = AsyncMock(return_value=record)

    async def mock_upsert_fail(rec: JobRecord) -> None:
        if rec.status == "error":
            call_order_fail.append("backend.upsert_job_error")

    backend.upsert_job = AsyncMock(side_effect=mock_upsert_fail)

    queue = MagicMock(spec=RedisJobQueue)
    queue.owns_lease = AsyncMock(return_value=True)
    queue.is_cancelled = MagicMock(return_value=False)

    async def mock_fail_call(
        jid: str, *, error: str = "", lease_owner: str | None = None
    ) -> bool:
        call_order_fail.append("queue.fail")
        return True

    queue.fail = AsyncMock(side_effect=mock_fail_call)

    await _handle_job_failure(
        job_id,
        RuntimeError("crash"),
        ctx,
        queue,
        backend,
        lease_owner="worker-1",
    )
    assert call_order_fail == ["backend.upsert_job_error", "queue.fail"]

    # 2. Cancelled path
    call_order_cancel: list[str] = []
    queue.is_cancelled = MagicMock(return_value=True)

    async def mock_upsert_cancel(rec: JobRecord) -> None:
        if rec.status == "cancelled":
            call_order_cancel.append("backend.upsert_job_cancelled")

    backend.upsert_job = AsyncMock(side_effect=mock_upsert_cancel)

    async def mock_fail_cancel(
        jid: str, *, error: str = "", lease_owner: str | None = None
    ) -> bool:
        call_order_cancel.append("queue.fail")
        return True

    queue.fail = AsyncMock(side_effect=mock_fail_cancel)

    await _handle_job_failure(
        job_id,
        RuntimeError("cancelled by user"),
        ctx,
        queue,
        backend,
        lease_owner="worker-1",
    )
    assert call_order_cancel == ["backend.upsert_job_cancelled", "queue.fail"]


# ------------------------------------------------------------------------------
# [P2-11] Atomic Requeue with Lease Owner
# ------------------------------------------------------------------------------


async def test_requeue_atomic_lease_check(harness: dict[str, Any]) -> None:
    """requeue() verifies lease_owner, moves active->queue, and updates backend."""
    queue: RedisJobQueue = harness["queue"]
    fake_redis = harness["redis"]
    backend: StateBackend = harness["backend"]

    handle = await queue.submit({"data": "requeue-me"})
    job_id = handle.job_id

    # Claim with owner-1
    claim = await queue.claim(worker_id="owner-1")
    assert claim is not None
    assert claim[0] == job_id

    # Check active in Redis
    assert await fake_redis.zscore(KEY_ACTIVE, job_id) is not None
    assert await fake_redis.get(f"{KEY_LEASE_PREFIX}{job_id}") == b"owner-1"

    # 1. Requeue with WRONG lease owner -> returns False
    ok_wrong = await queue.requeue(job_id, lease_owner="wrong-owner")
    assert ok_wrong is False
    # Still active
    assert await fake_redis.zscore(KEY_ACTIVE, job_id) is not None
    assert await fake_redis.zscore(KEY_QUEUE, job_id) is None

    # 2. Requeue with MATCHING lease owner -> returns True
    ok_right = await queue.requeue(job_id, lease_owner="owner-1")
    assert ok_right is True

    # Job is no longer active and lease key deleted
    assert await fake_redis.zscore(KEY_ACTIVE, job_id) is None
    assert await fake_redis.get(f"{KEY_LEASE_PREFIX}{job_id}") is None

    # Job is back in queue
    assert await fake_redis.zscore(KEY_QUEUE, job_id) is not None

    # Backend record is updated to "queued"
    rec = await backend.get_job(job_id)
    assert rec is not None
    assert rec.status == "queued"

    # 3. Requeue without lease_owner (None) -> returns True
    claim2 = await queue.claim(worker_id="owner-2")
    assert claim2 is not None
    ok_none = await queue.requeue(job_id, lease_owner=None)
    assert ok_none is True
    assert await fake_redis.zscore(KEY_ACTIVE, job_id) is None
    assert await fake_redis.zscore(KEY_QUEUE, job_id) is not None


# ------------------------------------------------------------------------------
# [P2-12] Cancellation of Queued Jobs Cleans Up Spooled Input Paths
# ------------------------------------------------------------------------------


async def test_cancel_queued_job_cleans_up_omniscribe_ocr_dir(
    harness: dict[str, Any],
) -> None:
    """Cancelling a queued job whose parent dir starts with omniscribe-ocr- removes the dir."""
    queue: RedisJobQueue = harness["queue"]

    # Create temporary omniscribe-ocr- directory under tempfile.gettempdir()
    work_dir = Path(tempfile.mkdtemp(prefix="omniscribe-ocr-"))
    test_file = work_dir / "input.pdf"
    test_file.write_bytes(b"%PDF-test")

    assert work_dir.exists()
    assert test_file.exists()

    payload = _OcrPayload(
        submission_id="sub-cancel-1",
        input_path=test_file,
        filename="input.pdf",
        request=OCRRequest(),
    )
    handle = await queue.submit(payload, input_path=str(test_file))

    # Cancel while still queued
    ok = await queue.cancel(handle.job_id)
    assert ok is True

    # The entire work_dir should have been removed via rmtree
    assert not work_dir.exists()
    assert not test_file.exists()


async def test_cancel_queued_job_cleans_up_single_file(
    harness: dict[str, Any],
) -> None:
    """Cancelling a queued job whose parent dir is not omniscribe-ocr- unlinks the file."""
    queue: RedisJobQueue = harness["queue"]

    # Create a plain file in tempdir (parent doesn't start with omniscribe-ocr-)
    temp_root = Path(tempfile.gettempdir()).resolve()
    plain_dir = temp_root / "other_dir"
    plain_dir.mkdir(exist_ok=True)
    test_file = plain_dir / "single_file.pdf"
    test_file.write_bytes(b"%PDF-single")

    assert plain_dir.exists()
    assert test_file.exists()

    try:
        handle = await queue.submit({"action": "test"}, input_path=str(test_file))
        ok = await queue.cancel(handle.job_id)
        assert ok is True

        # File is unlinked, but parent dir remains
        assert not test_file.exists()
        assert plain_dir.exists()
    finally:
        shutil.rmtree(plain_dir, ignore_errors=True)


async def test_cancel_queued_job_does_not_trust_redis_payload_input_path(
    harness: dict[str, Any],
) -> None:
    """Cancellation never deletes a path sourced only from the queue payload."""
    queue: RedisJobQueue = harness["queue"]
    backend: StateBackend = harness["backend"]

    work_dir = Path(tempfile.mkdtemp(prefix="omniscribe-ocr-"))
    test_file = work_dir / "input.pdf"
    test_file.write_bytes(b"%PDF-payload")

    payload = _OcrPayload(
        submission_id="sub-payload-clean",
        input_path=test_file,
        filename="input.pdf",
        request=OCRRequest(),
    )
    # Submit without input_path argument on submit, so record.input_path is None
    handle = await queue.submit(payload)
    rec = await backend.get_job(handle.job_id)
    assert rec is not None
    assert rec.input_path is None

    # Cancel while still queued
    ok = await queue.cancel(handle.job_id)
    assert ok is True

    try:
        assert work_dir.exists()
        assert test_file.exists()
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


async def test_cancel_queued_job_does_not_delete_outside_spool(
    harness: dict[str, Any], tmp_path: Path
) -> None:
    """Cancel does NOT delete files located outside trusted spool roots."""
    queue: RedisJobQueue = harness["queue"]

    # Create a file outside trusted spool (in tmp_path when not in env var)
    # tmp_path in pytest is normally under tempfile, but let's test a non-temp path
    outside_file = Path("d:/OmniScribe/outside_safety_check.txt")
    outside_file.write_text("safe content")

    try:
        handle = await queue.submit({"action": "unsafe"}, input_path=str(outside_file))
        ok = await queue.cancel(handle.job_id)
        assert ok is True

        # File must NOT have been deleted
        assert outside_file.exists()
    finally:
        if outside_file.exists():
            outside_file.unlink()


# ------------------------------------------------------------------------------
# [Workstream B] Distributed Worker Lifecycle & Spool Safety
# ------------------------------------------------------------------------------


async def test_run_ocr_cancellation_preserves_input_path_and_work_dir() -> None:
    """Cancelling an OCR task in _run_ocr with asyncio.CancelledError preserves input_path and work_dir."""
    work_dir = Path(tempfile.mkdtemp(prefix="omniscribe-ocr-cancel-preserve-"))
    input_file = work_dir / "input.pdf"
    input_file.write_bytes(b"%PDF-cancel-preserve")

    settings = RuntimeSettings()
    queue = MagicMock(spec=JobQueue)
    artifacts = MagicMock(spec=ArtifactStore)
    service = OCRServiceImpl(
        settings=settings,
        queue=queue,
        artifacts=artifacts,
        progress=None,
        max_upload_mb=50,
    )

    try:
        with (
            patch(
                "omniscribe.plugins.ocr.service.build_pipeline",
                return_value=MagicMock(),
            ),
            patch(
                "omniscribe.plugins.ocr.service.run_pipeline",
                side_effect=asyncio.CancelledError(),
            ),
        ):
            with pytest.raises(asyncio.CancelledError):
                await service._run_ocr(
                    OCRRequest(),
                    input_file,
                    "input.pdf",
                    job_id="job-cancel-preserve",
                )

        # Both work_dir and input_file must be preserved after cancellation
        assert work_dir.exists(), "work_dir should be preserved on asyncio.CancelledError"
        assert input_file.exists(), "input_path should be preserved on asyncio.CancelledError"
        assert input_file.read_bytes() == b"%PDF-cancel-preserve"
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


async def test_run_ocr_normal_completion_cleans_up_work_dir() -> None:
    """Normal OCR task completion in _run_ocr cleans up the temporary work_dir."""
    work_dir = Path(tempfile.mkdtemp(prefix="omniscribe-ocr-normal-cleanup-"))
    input_file = work_dir / "input.pdf"
    input_file.write_bytes(b"%PDF-normal-cleanup")
    output_file = work_dir / "output.pdf"
    output_file.write_bytes(b"%PDF-normal-output")

    settings = RuntimeSettings()
    queue = MagicMock(spec=JobQueue)
    artifacts = MagicMock(spec=ArtifactStore)
    service = OCRServiceImpl(
        settings=settings,
        queue=queue,
        artifacts=artifacts,
        progress=None,
        max_upload_mb=50,
    )

    try:
        with (
            patch(
                "omniscribe.plugins.ocr.service.build_pipeline",
                return_value=MagicMock(),
            ),
            patch(
                "omniscribe.plugins.ocr.service.run_pipeline",
                return_value={0: ["normal text"]},
            ),
        ):
            res_bytes, pages, _ = await service._run_ocr(
                OCRRequest(),
                input_file,
                "input.pdf",
                job_id="job-normal-cleanup",
            )
            assert res_bytes == b"%PDF-normal-output"
            assert pages == {0: ["normal text"]}

        # On normal completion, work_dir should have been removed
        assert not work_dir.exists()
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
