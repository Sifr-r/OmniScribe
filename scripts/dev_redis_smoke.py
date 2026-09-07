"""Sprint 4 (RFC 003) end-to-end smoke for the redis state backend.

This is a maintainer-run recipe, not part of CI. It exercises the
redis backend end-to-end against a real Redis server. The
``state_backend_redis.py`` pytest tests use fakeredis, which is
fast and in-process, but doesn't catch issues that only show up
against a real Redis (cluster routing, network failures,
persistence semantics, etc.).

Usage::

    # 1. Start a local Redis with no persistence (smoke only)
    redis-server --save "" --appendonly no --port 6379

    # 2. In one terminal, boot the server with the redis backend
    OMNISCRIBE_STATE_BACKEND=redis \\
    REDIS_URL=redis://localhost:6379/0 \\
    uv run omniscribe-server --port 8000

    # 3. In another terminal, run this recipe
    uv run python scripts/dev_redis_smoke.py --base http://127.0.0.1:8000

    # 4. After the script completes, verify with redis-cli
    redis-cli -p 6379 KEYS 'omniscribe:*'

The script will:
1. Confirm ``/api/health`` returns 200 (basic boot check).
2. Inspect the Redis keyspace (the boot may not have written
   any keys yet; the recipe explains what to do next).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import urllib.request

import redis.asyncio as redis_async


def _hit_health(base: str, timeout_s: int = 30) -> None:
    """Confirm the server's /api/health returns 200."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base}/api/health", timeout=2) as r:
                if r.status == 200:
                    body = r.read().decode("utf-8", errors="replace")
                    if "ok" in body:
                        print(f"  /api/health OK  ({body.strip()[:80]})")
                        return
        except Exception:
            pass
        time.sleep(0.5)
    print(f"  /api/health FAILED  (no 200 within {timeout_s}s)")
    sys.exit(1)


async def _dump_keys(redis_url: str) -> int:
    """Print the omniscribe:* keyspace from Redis. Returns the count."""
    r = redis_async.from_url(redis_url, decode_responses=True)
    keys = [k async for k in r.scan_iter(match="omniscribe:*", count=100)]
    print(f"  redis keyspace: {len(keys)} omniscribe:* keys")
    for k in sorted(keys)[:20]:
        ttl = await r.ttl(k)
        kind = await r.type(k)
        print(f"    {k}  (type={kind}, ttl={ttl}s)")
    if len(keys) > 20:
        print(f"    ... and {len(keys) - 20} more")
    await r.aclose()
    return len(keys)


async def main_async(args: argparse.Namespace) -> int:
    base = args.base.rstrip("/")
    print(f"Smoke-testing {base} with redis backend at {args.redis_url}")
    print()

    print("[1/3] Health check")
    _hit_health(base)
    print()

    print("[2/3] Inspecting Redis keyspace after server boot")
    n = await _dump_keys(args.redis_url)
    if n == 0:
        print(
            "  NOTE: 0 keys yet — that's expected. The redis backend "
            "writes to Redis on the first job / artifact / channel "
            "operation. Submit a job via the Workstation to see keys "
            "appear; rerun this script after that to confirm."
        )
    print()

    print("[3/3] Done")
    print("  Manual verification next:")
    print(f"    redis-cli -p {args.port} KEYS 'omniscribe:*'")
    print(f"    redis-cli -p {args.port} GET omniscribe:job:<id>")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--base", default="http://127.0.0.1:8000", help="server base URL"
    )
    parser.add_argument(
        "--redis-url",
        default="redis://localhost:6379/0",
        help="redis URL the server was started with",
    )
    parser.add_argument("--port", type=int, default=6379, help="redis port (for hints)")
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
