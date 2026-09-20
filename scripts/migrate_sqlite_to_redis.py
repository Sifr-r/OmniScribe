"""Profile 4 SQLite-to-Redis migration utility (RFC 003 / RFC 004).

Migrates jobs, artifacts, and progress channels from an existing SQLite state
backend database to a Redis state backend instance, preserving job history,
metadata, and index pagination.

Usage::

    # Dry-run inspection
    uv run python scripts/migrate_sqlite_to_redis.py --sqlite-path ./data/omniscribe.db --dry-run

    # Execute migration with TLS or plain Redis
    uv run python scripts/migrate_sqlite_to_redis.py \\
        --sqlite-path ./data/omniscribe.db \\
        --redis-url redis://localhost:6379/0

    # Execute migration with TLS
    REDIS_URL=rediss://:password@redis.internal:6380/0 \\
    OMNISCRIBE_REDIS_TLS=true \\
    uv run python scripts/migrate_sqlite_to_redis.py --sqlite-path ./data/omniscribe.db
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import redis.asyncio as redis_async

_KEY_PREFIX = "omniscribe:"


@dataclass
class MigrationCategorySummary:
    migrated: int = 0
    skipped: int = 0


@dataclass
class MigrationSummary:
    artifacts: MigrationCategorySummary = field(
        default_factory=MigrationCategorySummary
    )
    jobs: MigrationCategorySummary = field(default_factory=MigrationCategorySummary)
    channels: MigrationCategorySummary = field(default_factory=MigrationCategorySummary)
    dry_run: bool = False


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate OmniScribe SQLite state backend data to Redis."
    )
    parser.add_argument(
        "--sqlite-path",
        type=str,
        default="./data/omniscribe.db",
        help="Path to the source SQLite database file (default: ./data/omniscribe.db)",
    )
    parser.add_argument(
        "--redis-url",
        type=str,
        default=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        help="Target Redis URL (default: env REDIS_URL or redis://localhost:6379/0)",
    )
    parser.add_argument(
        "--tls",
        action="store_true",
        default=os.getenv("OMNISCRIBE_REDIS_TLS", "").lower() in ("1", "true", "yes"),
        help="Enable TLS for Redis connection (or set OMNISCRIBE_REDIS_TLS=true)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Batch size for database reads and pipeline operations.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Inspect SQLite records and count entries without writing to Redis.",
    )
    return parser.parse_args(argv)


def _read_rows(cursor: sqlite3.Cursor, table: str) -> list[dict[str, Any]]:
    try:
        cursor.execute(f"SELECT * FROM {table}")
        return [dict(row) for row in cursor.fetchall()]
    except sqlite3.OperationalError:
        return []


def _read_source_records(
    path: Path,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    with sqlite3.connect(str(path)) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        jobs = _read_rows(cursor, "jobs")
        artifacts = _read_rows(cursor, "artifacts")
        channels = _read_rows(cursor, "progress_channels")
    return jobs, artifacts, channels


async def _flush_pipeline(
    pipe: redis_async.client.Pipeline | None,
    queued_writes: int,
) -> int:
    if pipe is not None and queued_writes:
        await pipe.execute()
        return 0
    return queued_writes


async def _migrate_artifacts(
    artifacts: list[dict[str, Any]],
    *,
    pipe: redis_async.client.Pipeline | None,
    blob_dir: Path | None,
    dry_run: bool,
    current_time: float,
    batch_size: int,
    summary: MigrationSummary,
    queued_writes: int,
) -> int:
    for artifact in artifacts:
        artifact_id = str(artifact["id"])
        created_at = float(artifact.get("created_at") or current_time)
        ttl_seconds = int(artifact.get("ttl_seconds") or 86400)
        if created_at + ttl_seconds <= current_time:
            summary.artifacts.skipped += 1
            continue

        blob_path = artifact.get("blob_path")
        blob_bytes: bytes | None = None
        if blob_path:
            candidate_path = Path(blob_path)
            if candidate_path.is_file():
                blob_bytes = candidate_path.read_bytes()
            elif blob_dir is not None and (blob_dir / candidate_path.name).is_file():
                blob_bytes = (blob_dir / candidate_path.name).read_bytes()

        if blob_bytes is None:
            summary.artifacts.skipped += 1
            continue

        if not dry_run:
            assert pipe is not None
            remaining_ttl = max(1, int(created_at + ttl_seconds - current_time))
            artifact_data = {
                "id": artifact_id,
                "token": str(artifact["token"]),
                "owner_job_id": str(artifact["owner_job_id"]),
                "content_type": str(artifact["content_type"]),
                "created_at": created_at,
                "ttl_seconds": ttl_seconds,
            }
            pipe.set(
                f"{_KEY_PREFIX}artifact:{artifact_id}",
                json.dumps(artifact_data).encode("utf-8"),
                ex=remaining_ttl,
            )
            pipe.set(
                f"{_KEY_PREFIX}artifact:blob:{artifact_id}",
                blob_bytes,
                ex=remaining_ttl,
            )
            queued_writes += 2
            if queued_writes >= batch_size:
                queued_writes = await _flush_pipeline(pipe, queued_writes)
        summary.artifacts.migrated += 1
    return queued_writes


async def _migrate_jobs(
    jobs: list[dict[str, Any]],
    *,
    pipe: redis_async.client.Pipeline | None,
    dry_run: bool,
    current_time: float,
    batch_size: int,
    summary: MigrationSummary,
    queued_writes: int,
) -> int:
    for job in jobs:
        job_id = str(job["job_id"])
        created_at = float(job.get("created_at") or current_time)
        request_meta = job.get("request_meta")
        if isinstance(request_meta, str):
            try:
                request_meta = json.loads(request_meta)
            except Exception:
                request_meta = {}
        elif request_meta is None:
            request_meta = {}

        job_data = {
            "job_id": job_id,
            "status": str(job["status"]),
            "request_meta": request_meta,
            "result_artifact_id": job.get("result_artifact_id"),
            "result_artifact_token": job.get("result_artifact_token"),
            "input_path": job.get("input_path"),
            "created_at": created_at,
            "updated_at": float(job.get("updated_at") or created_at),
            "started_at": (
                float(job["started_at"]) if job.get("started_at") is not None else None
            ),
            "error": job.get("error"),
        }

        if not dry_run:
            assert pipe is not None
            pipe.set(
                f"{_KEY_PREFIX}job:{job_id}",
                json.dumps(job_data).encode("utf-8"),
            )
            pipe.zadd(f"{_KEY_PREFIX}job:index", {job_id: created_at})
            queued_writes += 2
            if queued_writes >= batch_size:
                queued_writes = await _flush_pipeline(pipe, queued_writes)
        summary.jobs.migrated += 1
    return queued_writes


async def _migrate_channels(
    channels: list[dict[str, Any]],
    *,
    pipe: redis_async.client.Pipeline | None,
    dry_run: bool,
    current_time: float,
    batch_size: int,
    summary: MigrationSummary,
    queued_writes: int,
) -> int:
    for channel in channels:
        channel_id = str(channel["channel_id"])
        created_at = float(channel.get("created_at") or current_time)
        ttl_seconds = int(channel.get("ttl_seconds") or 600)
        if created_at + ttl_seconds <= current_time:
            summary.channels.skipped += 1
            continue

        if not dry_run:
            assert pipe is not None
            channel_data = {
                "channel_id": channel_id,
                "session_token": str(channel["session_token"]),
                "job_id": str(channel["job_id"]),
                "created_at": created_at,
                "ttl_seconds": ttl_seconds,
                "consumed": bool(channel.get("consumed", False)),
            }
            remaining_ttl = max(1, int(created_at + ttl_seconds - current_time))
            pipe.set(
                f"{_KEY_PREFIX}channel:{channel_id}",
                json.dumps(channel_data).encode("utf-8"),
                ex=remaining_ttl,
            )
            pipe.zadd(
                f"{_KEY_PREFIX}channel:index",
                {channel_id: created_at + ttl_seconds},
            )
            queued_writes += 2
            if queued_writes >= batch_size:
                queued_writes = await _flush_pipeline(pipe, queued_writes)
        summary.channels.migrated += 1
    return queued_writes


async def migrate(
    sqlite_path: str | Path,
    redis_url: str | None = None,
    *,
    redis_client: redis_async.Redis | None = None,
    blob_dir: Path | None = None,
    dry_run: bool = False,
    now: float | None = None,
    use_tls: bool = False,
    batch_size: int = 100,
) -> MigrationSummary:
    path = Path(sqlite_path)
    if not path.is_file():
        raise FileNotFoundError(f"SQLite database not found at {path}")

    current_time = time.time() if now is None else now
    summary = MigrationSummary(dry_run=dry_run)
    jobs, artifacts, channels = _read_source_records(path)

    owns_client = False
    client = redis_client

    try:
        if not dry_run and client is None:
            target_url = (
                redis_url or os.getenv("REDIS_URL") or "redis://localhost:6379/0"
            )
            ssl_kwargs: dict[str, Any] = {}
            if (
                use_tls or target_url.startswith("rediss://")
            ) and not target_url.startswith("rediss://"):
                ssl_kwargs["ssl"] = True
            client = redis_async.from_url(
                target_url,
                encoding="utf-8",
                decode_responses=False,
                **ssl_kwargs,
            )
            owns_client = True
            await client.ping()

        pipe: redis_async.client.Pipeline | None
        if dry_run:
            pipe = None
        else:
            assert client is not None
            pipe = client.pipeline(transaction=False)
        queued_writes = await _migrate_artifacts(
            artifacts,
            pipe=pipe,
            blob_dir=blob_dir,
            dry_run=dry_run,
            current_time=current_time,
            batch_size=batch_size,
            summary=summary,
            queued_writes=0,
        )
        queued_writes = await _migrate_jobs(
            jobs,
            pipe=pipe,
            dry_run=dry_run,
            current_time=current_time,
            batch_size=batch_size,
            summary=summary,
            queued_writes=queued_writes,
        )
        queued_writes = await _migrate_channels(
            channels,
            pipe=pipe,
            dry_run=dry_run,
            current_time=current_time,
            batch_size=batch_size,
            summary=summary,
            queued_writes=queued_writes,
        )
        await _flush_pipeline(pipe, queued_writes)
        return summary
    finally:
        if owns_client and client is not None:
            await client.aclose()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    summary = asyncio.run(
        migrate(
            sqlite_path=args.sqlite_path,
            redis_url=args.redis_url,
            use_tls=args.tls,
            dry_run=args.dry_run,
            batch_size=args.batch_size,
        )
    )
    if summary.dry_run:
        print("\n--- DRY RUN SUMMARY ---")
    else:
        print("\n--- MIGRATION SUMMARY ---")
    print(
        f"Artifacts: Migrated={summary.artifacts.migrated} "
        f"Skipped={summary.artifacts.skipped}"
    )
    print(f"Jobs:      Migrated={summary.jobs.migrated} Skipped={summary.jobs.skipped}")
    print(
        f"Channels:  Migrated={summary.channels.migrated} "
        f"Skipped={summary.channels.skipped}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
