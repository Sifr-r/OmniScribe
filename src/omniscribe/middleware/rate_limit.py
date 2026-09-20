"""ASGI rate-limiting middleware (Wave 13: Security & API Middleware).

Enforces per-client IP sliding-window request rate limits. Uses an in-memory
sliding window (deque of monotonic timestamps per IP) guarded by a
`threading.Lock()`. If `rate_limit_per_min` is None or <= 0, requests
pass through immediately.

Exempt paths:
    ``/``, ``/api/health``, ``/api/healthz``, ``/api/ready``, ``/ready``,
    ``/readyz``, ``/static/*``, and HTTP OPTIONS preflight requests.

When the limit is exceeded, returns HTTP 429 JSONResponse:
    ``{"error": "rate_limited", "detail": "Rate limit exceeded"}``
with a ``Retry-After: <seconds>`` header.

Per-worker multiplier (audit finding S7):
    The sliding window is **per process**. The state is in-memory
    only, so when the server runs N uvicorn workers (or when a single
    process is restarted mid-window) each worker enforces the limit
    independently. The effective global limit is
    ``rate_limit_per_min * N``. Set ``rate_limit_per_min`` with this
    in mind: a value of 30/min on a 4-worker deployment gives an
    effective global ceiling of 120/min per IP. The audit's
    "consider Redis-backed sliding window" recommendation is a future
    story; the in-memory implementation is correct for the v0.2.0
    single-worker default (Profile 1) and good enough for the
    Profile 2 LAN bind with a small worker count. Multi-worker
    operators who need exact per-IP enforcement should set
    ``rate_limit_per_min`` lower than their actual target and
    monitor the 429 rate in the structured log.
"""

from __future__ import annotations

import collections
import json
import logging
import math
import threading
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

logger = logging.getLogger("omniscribe.middleware.rate_limit")

#: Path prefixes exempt from rate limiting (web UI static assets).
EXEMPT_PATH_PREFIXES: tuple[str, ...] = (
    "/static/",
    "/_static/",
)

#: Exact paths exempt from rate limiting (probes, health, root UI).
EXEMPT_EXACT_PATHS: frozenset[str] = frozenset(
    {
        "/",
        "/api/health",
        "/api/healthz",
        "/api/ready",
        "/ready",
        "/readyz",
    }
)

#: HTTP methods exempt from rate limiting (CORS preflight).
EXEMPT_METHODS: frozenset[str] = frozenset({"OPTIONS"})

#: Default proxies trusted to supply client IP via X-Forwarded-For.
DEFAULT_TRUSTED_PROXIES: frozenset[str] = frozenset(
    {
        "127.0.0.1",
        "::1",
        "localhost",
        "testclient",
    }
)

#: Maximum number of unique client IPs tracked concurrently to bound memory.
MAX_TRACKED_IPS: int = 10_000

#: ASGI 3.0 receive / send type aliases.
ASGIRecv = Callable[[], Awaitable[dict[str, Any]]]
ASGISend = Callable[[dict[str, Any]], Awaitable[None]]


def _extract_client_ip(
    scope: dict[str, Any],
    trusted_proxies: frozenset[str] = DEFAULT_TRUSTED_PROXIES,
) -> str:
    """Extract client IP from ASGI scope, honoring forwarded headers from trusted proxies.

    If the direct connection is from a trusted proxy (e.g. localhost reverse proxy
    or Starlette TestClient), the leftmost IP in ``X-Forwarded-For`` (or
    ``X-Real-IP``) is used. Unparseable or absent direct sockets without a client
    tuple remain ``"unknown"`` and ignore forwarded headers. Otherwise, the direct
    socket IP is returned.
    """
    client = scope.get("client")
    direct_ip = "unknown"
    if client and isinstance(client, (tuple, list)) and len(client) > 0:
        direct_ip = str(client[0]).strip()

    if direct_ip in trusted_proxies:
        headers: list[tuple[bytes, bytes]] = list(scope.get("headers") or [])
        for raw_name, raw_value in headers:
            if raw_name.lower() == b"x-forwarded-for":
                try:
                    val = raw_value.decode("latin-1")
                    parts = [p.strip() for p in val.split(",") if p.strip()]
                    if parts:
                        return str(parts[0])
                except UnicodeDecodeError:
                    pass
                break
            if raw_name.lower() == b"x-real-ip":
                try:
                    val = raw_value.decode("latin-1").strip()
                    if val:
                        return str(val)
                except UnicodeDecodeError:
                    pass
                break

    return direct_ip


def _is_exempt(path: str, method: str) -> bool:
    """Return True if the request path or method is exempt from rate limiting."""
    if method.upper() in EXEMPT_METHODS:
        return True
    if path in EXEMPT_EXACT_PATHS:
        return True
    if path == "/static":
        return True
    return any(path.startswith(prefix) for prefix in EXEMPT_PATH_PREFIXES)


class RateLimitMiddleware:
    """ASGI 3.0 middleware that enforces sliding-window rate limits per client IP.

    Usage::

        web_app.add_middleware(
            RateLimitMiddleware,
            rate_limit_per_min=settings.rate_limit_per_min,
        )

    When ``rate_limit_per_min`` is None or <= 0, the middleware acts as an
    immediate pass-through. When active, requests exceeding the limit within
    the rolling 60-second window receive HTTP 429 with a ``Retry-After`` header.
    """

    def __init__(
        self,
        app: Callable,
        rate_limit_per_min: int | None = None,
        trusted_proxies: Sequence[str] | frozenset[str] | set[str] | None = None,
    ) -> None:
        self._app = app
        self._rate_limit: int | None = (
            int(rate_limit_per_min)
            if rate_limit_per_min is not None and int(rate_limit_per_min) > 0
            else None
        )
        if trusted_proxies is None:
            self._trusted_proxies = DEFAULT_TRUSTED_PROXIES
        else:
            self._trusted_proxies = frozenset(trusted_proxies)

        self._lock = threading.Lock()
        self._records: dict[str, collections.deque[float]] = {}

        if self._rate_limit is not None:
            logger.info(
                "RateLimitMiddleware armed: limit=%d requests/min per IP "
                "(exempt: %s, %s)",
                self._rate_limit,
                sorted(EXEMPT_EXACT_PATHS),
                list(EXEMPT_PATH_PREFIXES),
            )

    @property
    def rate_limit_per_min(self) -> int | None:
        """Configured request rate limit per minute, or None if disabled."""
        return self._rate_limit

    def _check_rate_limit(self, ip: str) -> tuple[bool, int]:
        """Check if request from IP is allowed under the 60-second sliding window.

        Returns:
            A tuple of ``(allowed: bool, retry_after: int)``. When allowed is
            False, retry_after is the number of seconds until a request slot
            becomes available.
        """
        if self._rate_limit is None or self._rate_limit <= 0:
            return True, 0

        now = time.monotonic()
        window_start = now - 60.0

        with self._lock:
            # Bounded memory: prune stale IP entries if tracking exceeds ceiling
            if len(self._records) > MAX_TRACKED_IPS:
                stale = [
                    k
                    for k, q in self._records.items()
                    if not q or q[-1] <= window_start
                ]
                for k in stale:
                    del self._records[k]

            queue = self._records.get(ip)
            if queue is None:
                queue = collections.deque()
                self._records[ip] = queue

            # Evict timestamps outside the sliding 60-second window
            while queue and queue[0] <= window_start:
                queue.popleft()

            if len(queue) >= self._rate_limit:
                oldest = queue[0]
                remaining = (oldest + 60.0) - now
                retry_after = max(1, math.ceil(remaining))
                return False, retry_after

            queue.append(now)
            return True, 0

    async def __call__(
        self, scope: dict[str, Any], receive: ASGIRecv, send: ASGISend
    ) -> None:
        if (
            scope.get("type") != "http"
            or self._rate_limit is None
            or self._rate_limit <= 0
        ):
            await self._app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "GET")
        if _is_exempt(path, method):
            await self._app(scope, receive, send)
            return

        client_ip = _extract_client_ip(scope, self._trusted_proxies)
        allowed, retry_after = self._check_rate_limit(client_ip)
        if not allowed:
            await self._send_rate_limited(scope, receive, send, retry_after)
            return

        await self._app(scope, receive, send)

    @staticmethod
    async def _send_rate_limited(
        scope: dict[str, Any],
        receive: ASGIRecv,
        send: ASGISend,
        retry_after: int,
    ) -> None:
        logger.warning(
            "rate_limit.middleware: 429 rate limit exceeded for %s %s (retry_after=%ds)",
            scope.get("method", "GET"),
            scope.get("path", ""),
            retry_after,
        )
        body = json.dumps(
            {"error": "rate_limited", "detail": "Rate limit exceeded"}
        ).encode("utf-8")
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("latin-1")),
            (b"retry-after", str(retry_after).encode("latin-1")),
        ]
        await send({"type": "http.response.start", "status": 429, "headers": headers})
        await send({"type": "http.response.body", "body": body})


__all__ = [
    "DEFAULT_TRUSTED_PROXIES",
    "EXEMPT_EXACT_PATHS",
    "EXEMPT_METHODS",
    "EXEMPT_PATH_PREFIXES",
    "MAX_TRACKED_IPS",
    "RateLimitMiddleware",
    "_extract_client_ip",
    "_is_exempt",
]
