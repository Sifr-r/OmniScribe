"""Tests for scripts/migrate_sqlite_to_redis.py."""

from __future__ import annotations

import io
import sqlite3
import time
from contextlib import redirect_stdout
from pathlib import Path

import fakeredis.aioredis
import pytest

from omniscribe.plugins.state_backend_redis import RedisStateBackend
from omniscribe.plugins.state_backend_sqlite import SQLiteStateBackend
from omniscribe.plugins.state_backend_types import JobRecord
from scripts.migrate_sqlite_to_redis import main, migrate


@pytest.fixture
def sqlite_test_db(tmp_path: Path) -> tuple[Path, Path]:
    """Create a temporary SQLite database and blob directory."""
    db_path = tmp_path / "omniscribe-state.db"
    blob_dir = tmp_path / "blobs"
    blob_dir.mkdir(parents=True, exist_ok=True)
    return db_path, blob_dir


async def test_migrate_all_entities(
    sqlite_test_db: tuple[Path, Path],
) -> None:
    db_path, blob_dir = sqlite_test_db
    sqlite_backend = SQLiteStateBackend(db_path=db_path, blob_dir=blob_dir)
    await sqlite_backend.open()

    now = time.time()

    # 1. Artifacts: 1 active, 1 expired
    await sqlite_backend.put_artifact(
        id="art-active",
        token="tok-active",
        owner_job_id="job-1",
        content_type="application/pdf",
        blob=b"%PDF-active-content",
        ttl_seconds=3600,
    )

    # 2. Jobs: 3 jobs
    await sqlite_backend.upsert_job(
        JobRecord(
            job_id="job-1",
            status="queued",
            created_at=now - 50,
            updated_at=now - 50,
        )
    )
    await sqlite_backend.upsert_job(
        JobRecord(
            job_id="job-2",
            status="running",
            request_meta={"priority": 1},
            created_at=now - 20,
            updated_at=now - 10,
            started_at=now - 15,
        )
    )
    await sqlite_backend.upsert_job(
        JobRecord(
            job_id="job-3",
            status="complete",
            result_artifact_id="art-active",
            result_artifact_token="tok-active",
            created_at=now - 10,
            updated_at=now - 5,
            started_at=now - 8,
        )
    )

    # 3. Channels: 1 unconsumed, 1 consumed, 1 expired
    await sqlite_backend.put_channel(
        channel_id="chan-active",
        session_token="sess-active",
        job_id="job-1",
        ttl_seconds=3600,
    )
    await sqlite_backend.put_channel(
        channel_id="chan-consumed",
        session_token="sess-consumed",
        job_id="job-2",
        ttl_seconds=3600,
    )

    # Close backend before direct SQLite inspection / inserts to avoid WAL locking
    await sqlite_backend.aclose()

    # Direct inserts for expired artifact and expired channel
    expired_blob_file = blob_dir / "art-expired.bin"
    expired_blob_file.write_bytes(b"%PDF-expired-content")
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO artifacts (id, token, owner_job_id, content_type, blob_path, created_at, ttl_seconds) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "art-expired",
                "tok-expired",
                "job-old",
                "text/plain",
                str(expired_blob_file),
                now - 7200.0,
                3600,
            ),
        )
        conn.execute(
            "UPDATE progress_channels SET consumed = 1 WHERE channel_id = 'chan-consumed'"
        )
        conn.execute(
            "INSERT INTO progress_channels (channel_id, session_token, job_id, created_at, ttl_seconds, consumed) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "chan-expired",
                "sess-expired",
                "job-old",
                now - 7200.0,
                3600,
                0,
            ),
        )


    # Create fakeredis instance
    fake = fakeredis.aioredis.FakeRedis()

    summary = await migrate(
        sqlite_path=db_path,
        redis_client=fake,
        blob_dir=blob_dir,
        now=now,
    )

    # Assert summary counts
    assert summary.artifacts.migrated == 1
    assert summary.artifacts.skipped == 1  # 1 expired

    assert summary.jobs.migrated == 3
    assert summary.jobs.skipped == 0

    assert summary.channels.migrated == 2
    assert summary.channels.skipped == 1  # 1 expired

    # Verify that migrated data is readable by RedisStateBackend
    import redis.asyncio as redis_async
    orig_from_url = redis_async.from_url
    redis_async.from_url = lambda *a, **kw: fake
    try:
        redis_backend = RedisStateBackend(redis_url="redis://fake:6379/0")
        await redis_backend.open()

        # 1. Read artifact
        art_blob = await redis_backend.get_artifact("art-active", "tok-active")
        assert art_blob is not None
        assert art_blob.blob == b"%PDF-active-content"
        assert art_blob.record.owner_job_id == "job-1"
        assert art_blob.record.content_type == "application/pdf"

        # Wrong token auth check
        assert await redis_backend.get_artifact("art-active", "wrong-tok") is None
        # Expired artifact wasn't migrated
        assert await redis_backend.get_artifact("art-expired", "tok-expired") is None

        # 2. Read jobs
        j1 = await redis_backend.get_job("job-1")
        assert j1 is not None
        assert j1.status == "queued"

        j2 = await redis_backend.get_job("job-2")
        assert j2 is not None
        assert j2.status == "running"
        assert j2.request_meta == {"priority": 1}
        assert j2.started_at == now - 15

        j3 = await redis_backend.get_job("job-3")
        assert j3 is not None
        assert j3.status == "complete"
        assert j3.result_artifact_id == "art-active"

        # List jobs: should be sorted by created_at desc (job-3, job-2, job-1)
        job_list = await redis_backend.list_jobs(limit=10)
        assert [j.job_id for j in job_list] == ["job-3", "job-2", "job-1"]

        # 3. Read channels
        c1 = await redis_backend.get_channel("chan-active")
        assert c1 is not None
        assert c1.session_token == "sess-active"
        assert c1.consumed is False

        c2 = await redis_backend.get_channel("chan-consumed")
        assert c2 is not None
        assert c2.session_token == "sess-consumed"
        assert c2.consumed is True

        # Consume channel
        consumed = await redis_backend.consume_channel("chan-active", "sess-active")
        assert consumed is not None
        assert consumed.consumed is False
        # Second consume returns None
        assert await redis_backend.consume_channel("chan-active", "sess-active") is None

        # Prune expired channels at future time
        deleted_count = await redis_backend.prune_expired_channels(now + 100_000)
        assert deleted_count == 2
        assert await redis_backend.get_channel("chan-active") is None
        assert await redis_backend.get_channel("chan-consumed") is None

        await redis_backend.aclose()
    finally:
        redis_async.from_url = orig_from_url
        await sqlite_backend.aclose()


async def test_migrate_dry_run(sqlite_test_db: tuple[Path, Path]) -> None:
    db_path, blob_dir = sqlite_test_db
    sqlite_backend = SQLiteStateBackend(db_path=db_path, blob_dir=blob_dir)
    await sqlite_backend.open()
    await sqlite_backend.put_artifact(
        id="art-dry",
        token="tok-dry",
        owner_job_id="job-dry",
        content_type="text/plain",
        blob=b"dry-run-data",
        ttl_seconds=3600,
    )
    await sqlite_backend.upsert_job(
        JobRecord(job_id="job-dry", status="queued")
    )
    await sqlite_backend.put_channel(
        channel_id="chan-dry",
        session_token="sess-dry",
        job_id="job-dry",
        ttl_seconds=3600,
    )

    fake = fakeredis.aioredis.FakeRedis()
    summary = await migrate(
        sqlite_path=db_path,
        redis_client=fake,
        dry_run=True,
    )
    assert summary.dry_run is True
    assert summary.artifacts.migrated == 1
    assert summary.jobs.migrated == 1
    assert summary.channels.migrated == 1

    # Verify NOTHING was written to Redis
    keys = await fake.keys("omniscribe:*")
    assert keys == []
    await sqlite_backend.aclose()


async def test_migrate_missing_blob_file(sqlite_test_db: tuple[Path, Path]) -> None:
    db_path, blob_dir = sqlite_test_db
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE artifacts ("
        "id TEXT PRIMARY KEY, token TEXT, owner_job_id TEXT, content_type TEXT, "
        "blob_path TEXT, created_at REAL, ttl_seconds INTEGER)"
    )
    conn.execute(
        "INSERT INTO artifacts VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "art-missing",
            "tok",
            "job-1",
            "text/plain",
            str(blob_dir / "nonexistent.bin"),
            time.time(),
            3600,
        ),
    )
    conn.commit()
    conn.close()

    fake = fakeredis.aioredis.FakeRedis()
    summary = await migrate(sqlite_path=db_path, redis_client=fake)
    assert summary.artifacts.migrated == 0
    assert summary.artifacts.skipped == 1
    assert await fake.keys("omniscribe:*") == []


async def test_migrate_missing_database() -> None:
    with pytest.raises(FileNotFoundError):
        await migrate(sqlite_path="nonexistent_sqlite_file.db")


def test_cli_execution(sqlite_test_db: tuple[Path, Path]) -> None:
    db_path, _ = sqlite_test_db
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE jobs (job_id TEXT PRIMARY KEY, status TEXT, request_meta TEXT, created_at REAL, updated_at REAL)")
    conn.execute("INSERT INTO jobs VALUES ('job-cli', 'queued', '{}', 1000.0, 1000.0)")
    conn.commit()
    conn.close()

    buf = io.StringIO()
    with redirect_stdout(buf):
        exit_code = main(["--sqlite-path", str(db_path), "--dry-run"])

    assert exit_code == 0
    output = buf.getvalue()
    assert "DRY RUN" in output
    assert "Jobs:      Migrated=1" in output
