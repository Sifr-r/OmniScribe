"""SQLiteStateBackend: same surface on disk, WAL mode, persistence."""

from __future__ import annotations

import logging
import secrets
import sqlite3
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from omniscribe.plugins.state_backend import JobRecord, SQLiteStateBackend
from omniscribe.plugins.state_backend_sqlite import (
    _artifact_from_row,
    _channel_from_row,
    _job_from_row,
    _rowcount,
)


@pytest.fixture
async def backend(tmp_path: Path) -> SQLiteStateBackend:  # type: ignore[misc]
    impl = SQLiteStateBackend(
        db_path=tmp_path / "state.db", blob_dir=tmp_path / "blobs"
    )
    await impl.open()
    yield impl
    await impl.aclose()


async def test_wal_mode_enabled(tmp_path: Path) -> None:
    impl = SQLiteStateBackend(db_path=tmp_path / "state.db", blob_dir=tmp_path)
    await impl.open()
    conn = sqlite3.connect(str(tmp_path / "state.db"))
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    await impl.aclose()
    assert mode == "wal"


async def test_artifact_roundtrip_blob_on_disk(
    backend: SQLiteStateBackend, tmp_path: Path
) -> None:
    await backend.put_artifact(
        id="a1",
        token="tok",
        owner_job_id="job-1",
        content_type="application/pdf",
        blob=b"sqlite-bytes",
        ttl_seconds=3600,
    )
    result = await backend.get_artifact("a1", "tok")
    assert result is not None
    assert result.blob == b"sqlite-bytes"
    assert (tmp_path / "blobs" / "a1.bin").is_file()


async def test_artifact_wrong_token(backend: SQLiteStateBackend) -> None:
    await backend.put_artifact(
        id="a1",
        token="tok",
        owner_job_id="j",
        content_type="t",
        blob=b"x",
        ttl_seconds=1,
    )
    assert await backend.get_artifact("a1", "nope") is None


async def test_artifact_delete_removes_blob_file(
    backend: SQLiteStateBackend, tmp_path: Path
) -> None:
    await backend.put_artifact(
        id="a1",
        token="tok",
        owner_job_id="j",
        content_type="t",
        blob=b"x",
        ttl_seconds=1,
    )
    blob_file = tmp_path / "blobs" / "a1.bin"
    assert blob_file.is_file()
    await backend.delete_artifact("a1")
    assert await backend.get_artifact("a1", "tok") is None
    assert not blob_file.exists()


async def test_put_artifact_replaces_unlinks_previous_blob_file(
    backend: SQLiteStateBackend, tmp_path: Path
) -> None:
    """Pedantic 1.5 / test gap 5.2: ``INSERT OR REPLACE`` must unlink the
    prior ``.bin`` if the existing row points to a different path.

    Simulates the operator-cleanup / backup-restore / ad-hoc-SQL scenario
    where the row's ``blob_path`` is updated out-of-band to a file that
    is not the canonical ``<blob_dir>/<id>.bin``. A subsequent
    ``put_artifact`` for the same id must not leave the stale file
    behind.
    """
    # Seed an artifact and a sibling file the row will be repointed at.
    await backend.put_artifact(
        id="a1",
        token="tok",
        owner_job_id="j",
        content_type="t",
        blob=b"v1",
        ttl_seconds=3600,
    )
    canonical = tmp_path / "blobs" / "a1.bin"
    sibling_dir = tmp_path / "stale_blobs"
    sibling_dir.mkdir()
    sibling = sibling_dir / "a1.bin"
    sibling.write_bytes(b"v0-from-backup")
    # Ad-hoc SQL: repoint the row to the sibling file (no API path does
    # this; this models operator cleanup / backup restore / ad-hoc SQL).
    conn = sqlite3.connect(str(tmp_path / "state.db"))
    conn.execute(
        "UPDATE artifacts SET blob_path = ? WHERE id = ?",
        (str(sibling), "a1"),
    )
    conn.commit()
    conn.close()

    # The previous fix would have written ``v2`` to ``canonical``,
    # updated the row, and left ``sibling`` orphaned on disk.
    await backend.put_artifact(
        id="a1",
        token="tok2",
        owner_job_id="j",
        content_type="t",
        blob=b"v2",
        ttl_seconds=3600,
    )

    assert canonical.read_bytes() == b"v2"
    assert not sibling.exists(), "previous blob file leaked on INSERT OR REPLACE"
    record = await backend.get_artifact("a1", "tok2")
    assert record is not None and record.blob == b"v2"


async def test_artifact_prune(backend: SQLiteStateBackend) -> None:
    await backend.put_artifact(
        id="short",
        token="t",
        owner_job_id="j",
        content_type="c",
        blob=b"x",
        ttl_seconds=1,
    )
    await backend.put_artifact(
        id="long",
        token="t",
        owner_job_id="j",
        content_type="c",
        blob=b"y",
        ttl_seconds=1000,
    )
    removed = await backend.prune_expired_artifacts(now=time.time() + 5)
    assert removed == 1
    assert await backend.get_artifact("short", "t") is None
    assert await backend.get_artifact("long", "t") is not None


async def test_job_roundtrip_and_ordering(backend: SQLiteStateBackend) -> None:
    for index in range(3):
        await backend.upsert_job(
            JobRecord(
                job_id=f"job-{index}",
                status="queued",
                request_meta={"page": index},
                created_at=float(index),
                updated_at=float(index),
            )
        )
    record = await backend.get_job("job-1")
    assert record is not None
    assert record.request_meta == {"page": 1}
    listed = await backend.list_jobs(limit=2)
    assert [r.job_id for r in listed] == ["job-2", "job-1"]
    await backend.upsert_job(JobRecord(job_id="job-1", status="complete"))
    assert (await backend.get_job("job-1")).status == "complete"  # type: ignore[union-attr]
    await backend.delete_job("job-0")
    assert await backend.get_job("job-0") is None
    assert await backend.clear_jobs() == 2


async def test_job_started_at_roundtrip(backend: SQLiteStateBackend) -> None:
    await backend.upsert_job(JobRecord(job_id="job-sa", status="queued"))
    queued = await backend.get_job("job-sa")
    assert queued is not None
    assert queued.started_at is None

    await backend.upsert_job(
        replace(queued, status="running", started_at=123.45, updated_at=200.0)
    )
    running = await backend.get_job("job-sa")
    assert running is not None
    assert running.started_at == 123.45
    assert running.status == "running"


async def test_started_at_column_migrates_legacy_db(tmp_path: Path) -> None:
    legacy = sqlite3.connect(str(tmp_path / "state.db"))
    legacy.executescript(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            request_meta TEXT NOT NULL,
            result_artifact_id TEXT,
            result_artifact_token TEXT,
            input_path TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            error TEXT
        );
        """
    )
    legacy.execute(
        "INSERT INTO jobs (job_id, status, request_meta, created_at, updated_at) "
        "VALUES ('legacy-1', 'complete', '{}', 1.0, 2.0)"
    )
    legacy.commit()
    legacy.close()

    impl = SQLiteStateBackend(
        db_path=tmp_path / "state.db", blob_dir=tmp_path / "blobs"
    )
    await impl.open()
    try:
        record = await impl.get_job("legacy-1")
        assert record is not None
        assert record.started_at is None

        await impl.upsert_job(replace(record, status="running", started_at=99.5))
        updated = await impl.get_job("legacy-1")
        assert updated is not None
        assert updated.started_at == 99.5
    finally:
        await impl.aclose()


async def test_channel_one_shot_consume(backend: SQLiteStateBackend) -> None:
    await backend.put_channel("ch1", "tok", "job-1", ttl_seconds=600)
    assert await backend.get_channel("ch1") is not None
    assert await backend.consume_channel("ch1", "tok") is not None
    assert await backend.consume_channel("ch1", "tok") is None
    assert await backend.consume_channel("ch1", "wrong") is None
    await backend.delete_channel("ch1")
    assert await backend.get_channel("ch1") is None
    await backend.put_channel("stale", "t", "j", ttl_seconds=1)
    assert await backend.prune_expired_channels(now=time.time() + 5) == 1


async def test_persistence_across_reopen(tmp_path: Path) -> None:
    first = SQLiteStateBackend(
        db_path=tmp_path / "state.db", blob_dir=tmp_path / "blobs"
    )
    await first.open()
    await first.put_artifact(
        id="kept",
        token="tok",
        owner_job_id="j",
        content_type="c",
        blob=b"keep",
        ttl_seconds=99,
    )
    await first.upsert_job(JobRecord(job_id="j1", status="complete"))
    await first.aclose()

    second = SQLiteStateBackend(
        db_path=tmp_path / "state.db", blob_dir=tmp_path / "blobs"
    )
    await second.open()
    result = await second.get_artifact("kept", "tok")
    assert result is not None and result.blob == b"keep"
    assert (await second.get_job("j1")) is not None
    await second.aclose()


async def test_operations_before_open_raise(tmp_path: Path) -> None:
    impl = SQLiteStateBackend(db_path=tmp_path / "x.db", blob_dir=tmp_path)
    with pytest.raises(RuntimeError):
        await impl.get_job("j")


async def test_wal_mode_logs_warning_when_not_wal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from unittest.mock import MagicMock

    mock_conn = MagicMock(spec=sqlite3.Connection)
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = ("delete",)
    mock_conn.execute.return_value = mock_cursor

    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: mock_conn)
    impl = SQLiteStateBackend(db_path=tmp_path / "warn.db", blob_dir=tmp_path / "blobs")
    with caplog.at_level(
        logging.WARNING, logger="omniscribe.plugins.state_backend_sqlite"
    ):
        await impl.open()
    assert "SQLite journal_mode is 'delete', expected 'wal'" in caplog.text
    await impl.aclose()


def test_named_row_mapping_helpers_with_sqlite_row_and_dict() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    # 1. _job_from_row with sqlite3.Row
    job_row = conn.execute(
        "SELECT 'job-123' AS job_id, 'running' AS status, '{\"model\": \"test\"}' AS request_meta, "
        "'art-1' AS result_artifact_id, 'tok-1' AS result_artifact_token, "
        "100.0 AS created_at, 105.0 AS updated_at, NULL AS error"
    ).fetchone()
    job_rec = _job_from_row(job_row)
    assert job_rec.job_id == "job-123"
    assert job_rec.status == "running"
    assert job_rec.request_meta == {"model": "test"}
    assert job_rec.result_artifact_id == "art-1"
    assert job_rec.result_artifact_token == "tok-1"
    assert job_rec.created_at == 100.0
    assert job_rec.updated_at == 105.0
    assert job_rec.error is None

    # _job_from_row with dict
    job_dict = {
        "job_id": "job-dict",
        "status": "complete",
        "request_meta": {"custom": 42},
        "result_artifact_id": None,
        "result_artifact_token": None,
        "created_at": 200.0,
        "updated_at": 210.0,
        "error": "some-error",
    }
    job_rec_dict = _job_from_row(job_dict)
    assert job_rec_dict.job_id == "job-dict"
    assert job_rec_dict.status == "complete"
    assert job_rec_dict.request_meta == {"custom": 42}
    assert job_rec_dict.error == "some-error"

    # 2. _channel_from_row with sqlite3.Row
    channel_row = conn.execute(
        "SELECT 'ch-1' AS channel_id, 'sess-tok-1' AS session_token, 'job-123' AS job_id, "
        "150.0 AS created_at, 300 AS ttl_seconds, 1 AS consumed"
    ).fetchone()
    ch_rec = _channel_from_row(channel_row)
    assert ch_rec.channel_id == "ch-1"
    assert ch_rec.session_token == "sess-tok-1"
    assert ch_rec.job_id == "job-123"
    assert ch_rec.created_at == 150.0
    assert ch_rec.ttl_seconds == 300
    assert ch_rec.consumed is True

    # _channel_from_row with dict
    channel_dict = {
        "channel_id": "ch-2",
        "session_token": "sess-tok-2",
        "job_id": "job-456",
        "created_at": 250.0,
        "ttl_seconds": 600,
        "consumed": 0,
    }
    ch_rec_dict = _channel_from_row(channel_dict)
    assert ch_rec_dict.channel_id == "ch-2"
    assert ch_rec_dict.session_token == "sess-tok-2"
    assert ch_rec_dict.job_id == "job-456"
    assert ch_rec_dict.created_at == 250.0
    assert ch_rec_dict.ttl_seconds == 600
    assert ch_rec_dict.consumed is False

    # 3. _artifact_from_row with sqlite3.Row
    art_row = conn.execute(
        "SELECT 'art-99' AS id, 'token-xyz' AS token, 'job-123' AS owner_job_id, "
        "'image/png' AS content_type, 300.0 AS created_at, 1800 AS ttl_seconds"
    ).fetchone()
    art_rec = _artifact_from_row(art_row)
    assert art_rec.id == "art-99"
    assert art_rec.token == "token-xyz"
    assert art_rec.owner_job_id == "job-123"
    assert art_rec.content_type == "image/png"
    assert art_rec.created_at == 300.0
    assert art_rec.ttl_seconds == 1800

    # _artifact_from_row with dict
    art_dict = {
        "id": "art-100",
        "token": "token-abc",
        "owner_job_id": "job-789",
        "content_type": "application/json",
        "created_at": 400.0,
        "ttl_seconds": 3600,
    }
    art_rec_dict = _artifact_from_row(art_dict)
    assert art_rec_dict.id == "art-100"
    assert art_rec_dict.token == "token-abc"
    assert art_rec_dict.owner_job_id == "job-789"
    assert art_rec_dict.content_type == "application/json"
    assert art_rec_dict.created_at == 400.0
    assert art_rec_dict.ttl_seconds == 3600

    conn.close()


async def test_get_artifact_constant_time_compare_digest(
    backend: SQLiteStateBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    await backend.put_artifact(
        id="art-compare",
        token="super-secret-token",
        owner_job_id="job-sec",
        content_type="text/plain",
        blob=b"secret-data",
        ttl_seconds=3600,
    )
    compare_calls: list[tuple[str, str]] = []
    real_compare = secrets.compare_digest

    def spy_compare(a: Any, b: Any) -> bool:
        compare_calls.append((str(a), str(b)))
        return real_compare(a, b)

    monkeypatch.setattr(
        "omniscribe.plugins.state_backend_sqlite.secrets.compare_digest",
        spy_compare,
    )

    found = await backend.get_artifact("art-compare", "super-secret-token")
    assert found is not None
    assert found.blob == b"secret-data"
    assert ("super-secret-token", "super-secret-token") in compare_calls

    compare_calls.clear()
    not_found = await backend.get_artifact("art-compare", "wrong-secret-token")
    assert not_found is None
    assert ("super-secret-token", "wrong-secret-token") in compare_calls


def test_rowcount_helper() -> None:
    class DummyCursor:
        def __init__(self, rowcount: int) -> None:
            self.rowcount = rowcount

    assert _rowcount(DummyCursor(10)) == 10  # type: ignore[arg-type]
    assert _rowcount(DummyCursor(1)) == 1  # type: ignore[arg-type]
    assert _rowcount(DummyCursor(0)) == 0  # type: ignore[arg-type]
    assert _rowcount(DummyCursor(-1)) == 0  # type: ignore[arg-type]
    assert _rowcount(DummyCursor(-999)) == 0  # type: ignore[arg-type]

    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    # In sqlite3, unexecuted cursor rowcount is -1
    assert cur.rowcount == -1
    assert _rowcount(cur) == 0
    cur.execute("CREATE TABLE items (id INT)")
    assert _rowcount(cur) == 0
    cur.execute("INSERT INTO items VALUES (1), (2)")
    assert _rowcount(cur) == 2
    conn.close()


def test_job_record_frozen_and_unhashable() -> None:
    from dataclasses import FrozenInstanceError

    rec = JobRecord(job_id="test-job", status="queued")
    assert rec.__hash__ is None
    with pytest.raises(TypeError, match="unhashable type"):
        hash(rec)

    with pytest.raises(FrozenInstanceError):
        rec.status = "running"  # type: ignore[misc]


def test_state_backend_types_reexports(backend: SQLiteStateBackend) -> None:
    import omniscribe.plugins.state_backend as sb
    import omniscribe.plugins.state_backend_types as sbt

    assert sb.ArtifactBlob is sbt.ArtifactBlob
    assert sb.ArtifactRecord is sbt.ArtifactRecord
    assert sb.ChannelRecord is sbt.ChannelRecord
    assert sb.JobRecord is sbt.JobRecord
    assert sb.StateBackend is sbt.StateBackend
    assert sb.TERMINAL_JOB_STATUSES is sbt.TERMINAL_JOB_STATUSES

    assert isinstance(backend, sbt.StateBackend)
    assert isinstance(backend, sb.StateBackend)
