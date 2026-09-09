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
from typing import Any, cast

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

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Query jobs
    jobs: list[dict[str, Any]] = []
    try:
        cursor.execute("SELECT * FROM jobs")
        for row in cursor.fetchall():
            jobs.append(dict(row))
    except sqlite3.OperationalError:
        pass

    # Query artifacts
    artifacts: list[dict[str, Any]] = []
    try:
        cursor.execute("SELECT * FROM artifacts")
        for row in cursor.fetchall():
            artifacts.append(dict(row))
    except sqlite3.OperationalError:
        pass

    # Query progress channels
    channels: list[dict[str, Any]] = []
    try:
        cursor.execute("SELECT * FROM progress_channels")
        for row in cursor.fetchall():
            channels.append(dict(row))
    except sqlite3.OperationalError:
        pass

    conn.close()

    owns_client = False
    client = redis_client
    if not dry_run and client is None:
        target_url: str = (
            redis_url or os.getenv("REDIS_URL") or "redis://localhost:6379/0"
        )
        is_tls = use_tls or target_url.startswith("rediss://")
        ssl_kwargs: dict[str, Any] = {}
        if is_tls and not target_url.startswith("rediss://"):
            ssl_kwargs["ssl"] = True

        client = redis_async.from_url(
            target_url,
            encoding="utf-8",
            decode_responses=False,
            **ssl_kwargs,
        )
        owns_client = True
        await client.ping()

    if not dry_run:
        assert client is not None

    # ``--batch-size`` bounds how many Redis commands queue in one
    # pipeline before an EXEC round-trip; dry-run never opens a pipe.
    # ``pipe`` is None only in the dry-run path, and every ``pipe.set`` /
    # ``pipe.zadd`` below is gated on ``not dry_run`` so the runtime
    # value is always a live ``Pipeline`` at those call sites.
    pipe: redis_async.client.Pipeline | None
    if not dry_run:
        assert client is not None
        pipe = client.pipeline(transaction=False)
    else:
        pipe = None
    queued_writes = 0

    async def _flush_pipeline() -> None:
        nonlocal queued_writes
        if pipe is not None and queued_writes:
            await pipe.execute()
            queued_writes = 0

    try:
        # 1. Migrate artifacts
        for art in artifacts:
            art_id = str(art["id"])
            created_at = float(art.get("created_at") or current_time)
            ttl_seconds = int(art.get("ttl_seconds") or 86400)
            if created_at + ttl_seconds <= current_time:
                summary.artifacts.skipped += 1
                continue

            blob_path_str = art.get("blob_path")
            blob_bytes: bytes | None = None
            if blob_path_str:
                candidate_path = Path(blob_path_str)
                if candidate_path.is_file():
                    blob_bytes = candidate_path.read_bytes()
                elif (
                    blob_dir is not None and (blob_dir / candidate_path.name).is_file()
                ):
                    blob_bytes = (blob_dir / candidate_path.name).read_bytes()

            if blob_bytes is None:
                summary.artifacts.skipped += 1
                continue

            if dry_run:
                summary.artifacts.migrated += 1
            else:
                remaining_ttl = max(1, int(created_at + ttl_seconds - current_time))
                art_data = {
                    "id": art_id,
                    "token": str(art["token"]),
                    "owner_job_id": str(art["owner_job_id"]),
                    "content_type": str(art["content_type"]),
                    "created_at": created_at,
                    "ttl_seconds": ttl_seconds,
                }
                meta_key = f"{_KEY_PREFIX}artifact:{art_id}"
                blob_key = f"{_KEY_PREFIX}artifact:blob:{art_id}"
                assert pipe is not None
                pipe.set(
                    meta_key,
                    json.dumps(art_data).encode("utf-8"),
                    ex=remaining_ttl,
                )
                assert pipe is not None
                pipe.set(blob_key, blob_bytes, ex=remaining_ttl)
                queued_writes += 2
                if queued_writes >= batch_size:
                    await _flush_pipeline()
                summary.artifacts.migrated += 1

        # 2. Migrate jobs
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
                "started_at": float(job["started_at"])
                if job.get("started_at") is not None
                else None,
                "error": job.get("error"),
            }

            if dry_run:
                summary.jobs.migrated += 1
            else:
                job_key = f"{_KEY_PREFIX}job:{job_id}"
                assert pipe is not None
                pipe.set(job_key, json.dumps(job_data).encode("utf-8"))
                assert pipe is not None
                pipe.zadd(f"{_KEY_PREFIX}job:index", {job_id: created_at})
                queued_writes += 2
                if queued_writes >= batch_size:
                    await _flush_pipeline()
                summary.jobs.migrated += 1

        # 3. Migrate channels
        for ch in channels:
            ch_id = str(ch["channel_id"])
            created_at = float(ch.get("created_at") or current_time)
            ttl_seconds = int(ch.get("ttl_seconds") or 600)
            if created_at + ttl_seconds <= current_time:
                summary.channels.skipped += 1
                continue

            consumed = bool(ch.get("consumed", False))
            if dry_run:
                summary.channels.migrated += 1
            else:
                remaining_ttl = max(1, int(created_at + ttl_seconds - current_time))
                ch_data = {
                    "channel_id": ch_id,
                    "session_token": str(ch["session_token"]),
                    "job_id": str(ch["job_id"]),
                    "created_at": created_at,
                    "ttl_seconds": ttl_seconds,
                    "consumed": consumed,
                }
                ch_key = f"{_KEY_PREFIX}channel:{ch_id}"
                assert pipe is not None
                pipe.set(
                    ch_key,
                    json.dumps(ch_data).encode("utf-8"),
                    ex=remaining_ttl,
                )
                assert pipe is not None
                pipe.zadd(
                    f"{_KEY_PREFIX}channel:index",
                    {ch_id: created_at + ttl_seconds},
                )
                queued_writes += 2
                if queued_writes >= batch_size:
                    await _flush_pipeline()
                summary.channels.migrated += 1

        await _flush_pipeline()
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
