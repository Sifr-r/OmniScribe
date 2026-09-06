"""SQLite ``StateBackend`` implementation (WAL mode, blob-on-disk).

Audit catalog (Sprint 6 long-file split): separated from
``state_backend.py``. Blob bytes live at
``<blob_dir>/<id>.bin``; the database holds paths and metadata
only, keeping the file small.

Public surface preserved: ``SQLiteStateBackend`` is re-exported
from ``omniscribe.plugins.state_backend``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any

from .state_backend_types import (
    ArtifactBlob,
    ArtifactRecord,
    ChannelRecord,
    JobRecord,
)

_LOGGER = logging.getLogger("omniscribe.plugins.state_backend_sqlite")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    token TEXT NOT NULL,
    owner_job_id TEXT NOT NULL,
    content_type TEXT NOT NULL,
    blob_path TEXT NOT NULL,
    created_at REAL NOT NULL,
    ttl_seconds INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    request_meta TEXT NOT NULL,
    result_artifact_id TEXT,
    result_artifact_token TEXT,
    input_path TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    started_at REAL,
    error TEXT
);
CREATE TABLE IF NOT EXISTS progress_channels (
    channel_id TEXT PRIMARY KEY,
    session_token TEXT NOT NULL,
    job_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    ttl_seconds INTEGER NOT NULL,
    consumed INTEGER NOT NULL DEFAULT 0
);
"""


def _rowcount(cursor: sqlite3.Cursor) -> int:
    return max(cursor.rowcount, 0)


def _job_from_row(row: Any) -> JobRecord:
    raw_meta = row["request_meta"]
    if isinstance(raw_meta, dict):
        req_meta = raw_meta
    elif raw_meta:
        req_meta = json.loads(raw_meta)
    else:
        req_meta = {}
    return JobRecord(
        job_id=row["job_id"],
        status=row["status"],
        request_meta=req_meta,
        result_artifact_id=row["result_artifact_id"],
        result_artifact_token=row["result_artifact_token"],
        # Tolerate rows (synthetic dicts, legacy tables) that lack the
        # later-added columns. Membership must go through ``row.keys()``:
        # ``sqlite3.Row`` implements ``in`` over its *values*, not column
        # names, so ``"col" in row`` is always False for real rows.
        input_path=row["input_path"] if "input_path" in row.keys() else None,  # noqa: SIM118
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        started_at=row["started_at"] if "started_at" in row.keys() else None,  # noqa: SIM118
        error=row["error"],
    )


def _channel_from_row(row: Any) -> ChannelRecord:
    return ChannelRecord(
        channel_id=row["channel_id"],
        session_token=row["session_token"],
        job_id=row["job_id"],
        created_at=row["created_at"],
        ttl_seconds=row["ttl_seconds"],
        consumed=bool(row["consumed"]),
    )


def _artifact_from_row(row: Any) -> ArtifactRecord:
    return ArtifactRecord(
        id=row["id"],
        token=row["token"],
        owner_job_id=row["owner_job_id"],
        content_type=row["content_type"],
        created_at=row["created_at"],
        ttl_seconds=row["ttl_seconds"],
    )


class SQLiteStateBackend:
    """Single-file persistent backend (WAL mode).

    Blob bytes live on disk at ``<blob_dir>/<id>.bin``; the database holds
    paths and metadata only, keeping the file small.
    """

    def __init__(self, db_path: Path | str, blob_dir: Path | str) -> None:
        self._db_path = Path(db_path)
        self._blob_dir = Path(blob_dir)
        self._lock = asyncio.Lock()
        self._conn: sqlite3.Connection | None = None

    async def open(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._open_sync)

    def _open_sync(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._blob_dir.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            for d in (self._db_path.parent, self._blob_dir):
                with contextlib.suppress(OSError):
                    os.chmod(d, 0o700)
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        res = conn.execute("PRAGMA journal_mode=WAL").fetchone()
        if res and str(res[0]).lower() != "wal":
            _LOGGER.warning("SQLite journal_mode is '%s', expected 'wal'", res[0])
        conn.executescript(_SCHEMA)
        # ``started_at`` was added after the first schema shipped; legacy
        # databases created by ``CREATE TABLE IF NOT EXISTS`` above keep
        # their original columns, so migrate them in place.
        job_columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
        if "started_at" not in job_columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN started_at REAL")
        conn.commit()
        self._conn = conn

    def _require_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("SQLiteStateBackend is not open")
        return self._conn

    def _blob_path(self, artifact_id: str) -> Path:
        return self._blob_dir / f"{artifact_id}.bin"

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
        async with self._lock:

            def _put() -> None:
                conn = self._require_conn()
                new_path = self._blob_path(id)
                # Capture the existing row's blob_path (if any) before
                # INSERT OR REPLACE so we can unlink the previous file on
                # replace. Without this, an existing row whose blob_path
                # points to a different file (operator cleanup, backup
                # restore, ad-hoc SQL) would leak that file: the new write
                # targets ``new_path`` only, and the DB row would switch
                # to ``new_path`` without unlinking the previous one.
                previous_path: Path | None = None
                existing = conn.execute(
                    "SELECT blob_path FROM artifacts WHERE id = ?", (id,)
                ).fetchone()
                if existing is not None:
                    previous_path = Path(existing["blob_path"])
                new_path.write_bytes(blob)
                conn.execute(
                    "INSERT OR REPLACE INTO artifacts "
                    "(id, token, owner_job_id, content_type, blob_path, created_at, ttl_seconds) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        id,
                        token,
                        owner_job_id,
                        content_type,
                        str(new_path),
                        time.time(),
                        ttl_seconds,
                    ),
                )
                conn.commit()
                if previous_path is not None and previous_path != new_path:
                    previous_path.unlink(missing_ok=True)

            await asyncio.to_thread(_put)

    async def get_artifact(self, id: str, token: str) -> ArtifactBlob | None:
        async with self._lock:

            def _get() -> ArtifactBlob | None:
                conn = self._require_conn()
                row = conn.execute(
                    "SELECT id, token, owner_job_id, content_type, blob_path, "
                    "created_at, ttl_seconds FROM artifacts WHERE id = ?",
                    (id,),
                ).fetchone()
                if row is None or not secrets.compare_digest(str(row["token"]), token):
                    return None
                record = _artifact_from_row(row)
                path = Path(row["blob_path"])
                if not path.is_file():
                    return None
                return ArtifactBlob(record=record, blob=path.read_bytes())

            return await asyncio.to_thread(_get)

    async def delete_artifact(self, id: str) -> None:
        async with self._lock:

            def _delete() -> None:
                conn = self._require_conn()
                row = conn.execute(
                    "SELECT blob_path FROM artifacts WHERE id = ?", (id,)
                ).fetchone()
                conn.execute("DELETE FROM artifacts WHERE id = ?", (id,))
                conn.commit()
                if row is not None:
                    Path(row["blob_path"]).unlink(missing_ok=True)

            await asyncio.to_thread(_delete)

    async def prune_expired_artifacts(self, now: float) -> int:
        async with self._lock:

            def _prune() -> int:
                conn = self._require_conn()
                rows = conn.execute(
                    "SELECT id, blob_path FROM artifacts WHERE created_at + ttl_seconds <= ?",
                    (now,),
                ).fetchall()
                if not rows:
                    return 0
                conn.execute(
                    "DELETE FROM artifacts WHERE created_at + ttl_seconds <= ?", (now,)
                )
                conn.commit()
                for _artifact_id, blob_path in rows:
                    Path(blob_path).unlink(missing_ok=True)
                return len(rows)

            return await asyncio.to_thread(_prune)

    # -- jobs -----------------------------------------------------------------

    async def upsert_job(self, record: JobRecord) -> None:
        async with self._lock:

            def _upsert() -> None:
                conn = self._require_conn()
                conn.execute(
                    "INSERT OR REPLACE INTO jobs "
                    "(job_id, status, request_meta, result_artifact_id, "
                    "result_artifact_token, input_path, created_at, updated_at, "
                    "started_at, error) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        record.job_id,
                        record.status,
                        json.dumps(record.request_meta),
                        record.result_artifact_id,
                        record.result_artifact_token,
                        record.input_path,
                        record.created_at,
                        record.updated_at,
                        record.started_at,
                        record.error,
                    ),
                )
                conn.commit()

            await asyncio.to_thread(_upsert)

    async def get_job(self, job_id: str) -> JobRecord | None:
        async with self._lock:

            def _get() -> JobRecord | None:
                row = (
                    self._require_conn()
                    .execute(
                        "SELECT job_id, status, request_meta, result_artifact_id, "
                        "result_artifact_token, input_path, created_at, updated_at, "
                        "started_at, error "
                        "FROM jobs WHERE job_id = ?",
                        (job_id,),
                    )
                    .fetchone()
                )
                return _job_from_row(row) if row is not None else None

            return await asyncio.to_thread(_get)

    async def list_jobs(self, *, limit: int = 100, offset: int = 0) -> list[JobRecord]:
        async with self._lock:

            def _list() -> list[JobRecord]:
                rows = (
                    self._require_conn()
                    .execute(
                        "SELECT job_id, status, request_meta, result_artifact_id, "
                        "result_artifact_token, input_path, created_at, updated_at, "
                        "started_at, error "
                        "FROM jobs ORDER BY created_at DESC, job_id DESC "
                        "LIMIT ? OFFSET ?",
                        (limit, offset),
                    )
                    .fetchall()
                )
                return [_job_from_row(row) for row in rows]

            return await asyncio.to_thread(_list)

    async def clear_jobs(self) -> int:
        async with self._lock:

            def _clear() -> int:
                conn = self._require_conn()
                cursor = conn.execute("DELETE FROM jobs")
                conn.commit()
                return _rowcount(cursor)

            return await asyncio.to_thread(_clear)

    async def delete_job(self, job_id: str) -> None:
        async with self._lock:

            def _delete() -> None:
                conn = self._require_conn()
                conn.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
                conn.commit()

            await asyncio.to_thread(_delete)

    # -- channels ---------------------------------------------------------------

    async def put_channel(
        self, channel_id: str, session_token: str, job_id: str, ttl_seconds: int
    ) -> None:
        async with self._lock:

            def _put() -> None:
                conn = self._require_conn()
                conn.execute(
                    "INSERT OR REPLACE INTO progress_channels "
                    "(channel_id, session_token, job_id, created_at, ttl_seconds, consumed) "
                    "VALUES (?, ?, ?, ?, ?, 0)",
                    (channel_id, session_token, job_id, time.time(), ttl_seconds),
                )
                conn.commit()

            await asyncio.to_thread(_put)

    async def get_channel(self, channel_id: str) -> ChannelRecord | None:
        async with self._lock:

            def _get() -> ChannelRecord | None:
                row = (
                    self._require_conn()
                    .execute(
                        "SELECT channel_id, session_token, job_id, created_at, "
                        "ttl_seconds, consumed FROM progress_channels WHERE channel_id = ?",
                        (channel_id,),
                    )
                    .fetchone()
                )
                return _channel_from_row(row) if row is not None else None

            return await asyncio.to_thread(_get)

    async def consume_channel(
        self, channel_id: str, session_token: str
    ) -> ChannelRecord | None:
        async with self._lock:

            def _consume() -> ChannelRecord | None:
                conn = self._require_conn()
                row = conn.execute(
                    "SELECT channel_id, session_token, job_id, created_at, "
                    "ttl_seconds, consumed FROM progress_channels "
                    "WHERE channel_id = ?",
                    (channel_id,),
                ).fetchone()
                if (
                    row is None
                    or row["consumed"]
                    or not secrets.compare_digest(
                        str(row["session_token"]), session_token
                    )
                ):
                    return None
                conn.execute(
                    "UPDATE progress_channels SET consumed = 1 WHERE channel_id = ?",
                    (channel_id,),
                )
                conn.commit()
                return _channel_from_row(row)

            return await asyncio.to_thread(_consume)

    async def delete_channel(self, channel_id: str) -> None:
        async with self._lock:

            def _delete() -> None:
                conn = self._require_conn()
                conn.execute(
                    "DELETE FROM progress_channels WHERE channel_id = ?", (channel_id,)
                )
                conn.commit()

            await asyncio.to_thread(_delete)

    async def prune_expired_channels(self, now: float) -> int:
        async with self._lock:

            def _prune() -> int:
                conn = self._require_conn()
                cursor = conn.execute(
                    "DELETE FROM progress_channels WHERE created_at + ttl_seconds <= ?",
                    (now,),
                )
                conn.commit()
                return _rowcount(cursor)

            return await asyncio.to_thread(_prune)

    async def aclose(self) -> None:
        async with self._lock:
            if self._conn is not None:
                conn = self._conn
                self._conn = None
                await asyncio.to_thread(conn.close)


__all__ = ["SQLiteStateBackend"]
