"""Tests for ASGI rate-limiting middleware (Wave 13: Security & API Middleware)."""

from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from omniscribe.config import RuntimeSettings
from omniscribe.middleware.rate_limit import (
    EXEMPT_EXACT_PATHS,
    EXEMPT_METHODS,
    EXEMPT_PATH_PREFIXES,
    MAX_TRACKED_IPS,
    RateLimitMiddleware,
    _extract_client_ip,
    _is_exempt,
)
from omniscribe.server import (
    _detect_bind_host,
    _sanitize_value_error,
    _validate_runtime_settings,
    create_app,
)

# ---------------------------------------------------------------------------
# Scope helper
# ---------------------------------------------------------------------------


def _scope(
    path: str = "/api/jobs",
    method: str = "GET",
    scope_type: str = "http",
    client: tuple[str, int] | None = ("127.0.0.1", 54321),
    headers: list[tuple[bytes, bytes]] | None = None,
) -> dict[str, Any]:
    return {
        "type": scope_type,
        "method": method,
        "path": path,
        "client": client,
        "headers": headers or [],
    }


# ---------------------------------------------------------------------------
# Pure helper tests
# ---------------------------------------------------------------------------


def test_extract_client_ip_from_direct_socket() -> None:
    scope = _scope(client=("192.168.1.50", 12345))
    assert _extract_client_ip(scope) == "192.168.1.50"


def test_extract_client_ip_when_client_is_none() -> None:
    scope = _scope(client=None)
    assert _extract_client_ip(scope) == "unknown"


def test_extract_client_ip_unknown_direct_ip_ignores_forwarded() -> None:
    scope = _scope(
        client=None,
        headers=[(b"x-forwarded-for", b"203.0.113.195")],
    )
    assert _extract_client_ip(scope) == "unknown"


def test_extract_client_ip_trusted_proxy_uses_x_forwarded_for() -> None:
    scope = _scope(
        client=("127.0.0.1", 8000),
        headers=[(b"x-forwarded-for", b"203.0.113.195, 10.0.0.1")],
    )
    assert _extract_client_ip(scope) == "203.0.113.195"


def test_extract_client_ip_trusted_proxy_uses_x_real_ip() -> None:
    scope = _scope(
        client=("127.0.0.1", 8000),
        headers=[(b"x-real-ip", b"198.51.100.42")],
    )
    assert _extract_client_ip(scope) == "198.51.100.42"


def test_extract_client_ip_untrusted_direct_ip_ignores_forwarded() -> None:
    scope = _scope(
        client=("198.51.100.99", 54321),
        headers=[(b"x-forwarded-for", b"1.1.1.1")],
    )
    assert _extract_client_ip(scope) == "198.51.100.99"


def test_extract_client_ip_custom_trusted_proxies() -> None:
    trusted = frozenset({"10.0.0.2"})
    scope = _scope(
        client=("10.0.0.2", 12345),
        headers=[(b"x-forwarded-for", b"172.16.0.5")],
    )
    assert _extract_client_ip(scope, trusted_proxies=trusted) == "172.16.0.5"


def test_is_exempt_exact_paths() -> None:
    for path in EXEMPT_EXACT_PATHS:
        assert _is_exempt(path, "GET") is True, path
        assert _is_exempt(path, "POST") is True, path


def test_is_exempt_static_prefix_paths() -> None:
    for prefix in EXEMPT_PATH_PREFIXES:
        assert _is_exempt(prefix + "app.js", "GET") is True
        assert _is_exempt(prefix + "sub/style.css", "GET") is True
    assert _is_exempt("/static", "GET") is True


def test_is_exempt_options_method() -> None:
    for method in EXEMPT_METHODS:
        assert _is_exempt("/api/jobs", method) is True
        assert _is_exempt("/api/process", method) is True


def test_is_exempt_non_exempt_routes() -> None:
    for path in ("/api/jobs", "/api/process", "/api/extract", "/api/translate"):
        assert _is_exempt(path, "GET") is False, path
        assert _is_exempt(path, "POST") is False, path


# ---------------------------------------------------------------------------
# ASGI Test Driver & Recorder
# ---------------------------------------------------------------------------


class _CallRecorder:
    def __init__(self) -> None:
        self.upstream_called = False
        self.sent_start: dict[str, Any] | None = None
        self.sent_bodies: list[bytes] = []

    async def downstream(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        self.upstream_called = True
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b'{"status":"ok"}',
            }
        )

    async def receive(self) -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(self, message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            self.sent_start = message
        elif message["type"] == "http.response.body":
            self.sent_bodies.append(message.get("body", b""))


async def _drive(
    middleware: RateLimitMiddleware,
    recorder: _CallRecorder,
    scope: dict[str, Any],
) -> _CallRecorder:
    recorder.upstream_called = False
    recorder.sent_start = None
    recorder.sent_bodies = []
    await middleware(scope, recorder.receive, recorder.send)
    return recorder


# ---------------------------------------------------------------------------
# ASGI interface tests
# ---------------------------------------------------------------------------


async def test_rate_limit_disabled_when_none() -> None:
    recorder = _CallRecorder()
    middleware = RateLimitMiddleware(recorder.downstream, rate_limit_per_min=None)
    assert middleware.rate_limit_per_min is None

    for _ in range(10):
        rec = await _drive(middleware, recorder, _scope())
        assert rec.upstream_called is True
        assert rec.sent_start is not None
        assert rec.sent_start["status"] == 200


async def test_rate_limit_disabled_when_zero_or_negative() -> None:
    for limit in (0, -1, -50):
        recorder = _CallRecorder()
        middleware = RateLimitMiddleware(recorder.downstream, rate_limit_per_min=limit)
        assert middleware.rate_limit_per_min is None
        for _ in range(5):
            rec = await _drive(middleware, recorder, _scope())
            assert rec.upstream_called is True
            assert rec.sent_start is not None
            assert rec.sent_start["status"] == 200


async def test_requests_pass_through_when_below_limit() -> None:
    recorder = _CallRecorder()
    middleware = RateLimitMiddleware(recorder.downstream, rate_limit_per_min=3)

    for _ in range(3):
        rec = await _drive(middleware, recorder, _scope(client=("1.2.3.4", 1234)))
        assert rec.upstream_called is True
        assert rec.sent_start is not None
        assert rec.sent_start["status"] == 200


async def test_requests_return_429_when_exceeding_limit() -> None:
    recorder = _CallRecorder()
    middleware = RateLimitMiddleware(recorder.downstream, rate_limit_per_min=2)

    # First two requests pass
    for _ in range(2):
        rec = await _drive(middleware, recorder, _scope(client=("1.2.3.4", 1234)))
        assert rec.upstream_called is True
        assert rec.sent_start is not None
        assert rec.sent_start["status"] == 200

    # 3rd request exceeds limit
    rec3 = await _drive(middleware, recorder, _scope(client=("1.2.3.4", 1234)))
    assert rec3.upstream_called is False
    assert rec3.sent_start is not None
    assert rec3.sent_start["status"] == 429

    headers = dict(rec3.sent_start["headers"])
    assert b"retry-after" in headers
    retry_after = int(headers[b"retry-after"].decode("latin-1"))
    assert 1 <= retry_after <= 60

    assert headers[b"content-type"] == b"application/json"
    body = json.loads(b"".join(rec3.sent_bodies).decode("utf-8"))
    assert body == {"error": "rate_limited", "detail": "Rate limit exceeded"}


async def test_rate_limit_per_ip_isolation() -> None:
    recorder = _CallRecorder()
    middleware = RateLimitMiddleware(recorder.downstream, rate_limit_per_min=2)

    # Exhaust IP 1
    for _ in range(2):
        rec = await _drive(middleware, recorder, _scope(client=("10.0.0.1", 1000)))
        assert rec.upstream_called is True
    rec_blocked = await _drive(middleware, recorder, _scope(client=("10.0.0.1", 1000)))
    assert rec_blocked.upstream_called is False
    assert rec_blocked.sent_start is not None
    assert rec_blocked.sent_start["status"] == 429

    # IP 2 is unaffected and succeeds
    rec_ip2 = await _drive(middleware, recorder, _scope(client=("10.0.0.2", 2000)))
    assert rec_ip2.upstream_called is True
    assert rec_ip2.sent_start is not None
    assert rec_ip2.sent_start["status"] == 200


async def test_exempt_paths_bypass_rate_limiting() -> None:
    recorder = _CallRecorder()
    middleware = RateLimitMiddleware(recorder.downstream, rate_limit_per_min=1)

    # Exhaust normal route
    await _drive(middleware, recorder, _scope(path="/api/jobs", client=("1.1.1.1", 10)))
    blocked = await _drive(
        middleware, recorder, _scope(path="/api/jobs", client=("1.1.1.1", 10))
    )
    assert blocked.sent_start is not None
    assert blocked.sent_start["status"] == 429

    # Exempt paths continue to pass for the same IP
    for exempt_path in (
        "/",
        "/api/health",
        "/api/healthz",
        "/api/ready",
        "/ready",
        "/readyz",
        "/static/bundle.js",
        "/static/main.css",
        "/_static/favicon.ico",
    ):
        rec = await _drive(
            middleware, recorder, _scope(path=exempt_path, client=("1.1.1.1", 10))
        )
        assert rec.upstream_called is True, exempt_path
        assert rec.sent_start is not None, exempt_path
        assert rec.sent_start["status"] == 200, exempt_path

    # OPTIONS method bypasses rate limiting
    rec_options = await _drive(
        middleware,
        recorder,
        _scope(path="/api/jobs", method="OPTIONS", client=("1.1.1.1", 10)),
    )
    assert rec_options.upstream_called is True


async def test_sliding_window_resets_after_window_elapses() -> None:
    recorder = _CallRecorder()
    middleware = RateLimitMiddleware(recorder.downstream, rate_limit_per_min=1)

    fake_time = 1000.0

    with patch("time.monotonic", side_effect=lambda: fake_time):
        # 1st request at t=1000.0 passes
        rec1 = await _drive(middleware, recorder, _scope(client=("1.2.3.4", 1234)))
        assert rec1.upstream_called is True

        # 2nd request at t=1000.0 blocked
        rec2 = await _drive(middleware, recorder, _scope(client=("1.2.3.4", 1234)))
        assert rec2.upstream_called is False
        assert rec2.sent_start is not None
        assert rec2.sent_start["status"] == 429
        retry_after = int(dict(rec2.sent_start["headers"])[b"retry-after"])
        assert retry_after == 60

        # Advance time by 30 seconds
        fake_time = 1030.0
        rec3 = await _drive(middleware, recorder, _scope(client=("1.2.3.4", 1234)))
        assert rec3.sent_start is not None
        assert rec3.sent_start["status"] == 429
        retry_after_30 = int(dict(rec3.sent_start["headers"])[b"retry-after"])
        assert retry_after_30 == 30

        # Advance past 60-second window
        fake_time = 1061.0
        rec4 = await _drive(middleware, recorder, _scope(client=("1.2.3.4", 1234)))
        assert rec4.upstream_called is True
        assert rec4.sent_start is not None
        assert rec4.sent_start["status"] == 200


async def test_non_http_scope_passes_through() -> None:
    recorder = _CallRecorder()
    middleware = RateLimitMiddleware(recorder.downstream, rate_limit_per_min=1)

    scope = {"type": "websocket", "path": "/ws"}
    rec = await _drive(middleware, recorder, scope)
    assert rec.upstream_called is True


def test_max_tracked_ips_eviction() -> None:
    recorder = _CallRecorder()
    middleware = RateLimitMiddleware(recorder.downstream, rate_limit_per_min=10)

    # Seed records beyond MAX_TRACKED_IPS threshold with expired timestamps
    now = time.monotonic()
    for i in range(MAX_TRACKED_IPS + 5):
        ip = f"10.99.{i // 256}.{i % 256}"
        import collections

        q = collections.deque([now - 120.0])  # expired timestamps
        middleware._records[ip] = q

    assert len(middleware._records) > MAX_TRACKED_IPS

    # Checking an IP triggers eviction of stale entries
    allowed, _ = middleware._check_rate_limit("1.2.3.4")
    assert allowed is True
    # Pruned down
    assert len(middleware._records) <= MAX_TRACKED_IPS + 1


# ---------------------------------------------------------------------------
# TestClient & FastAPI Integration tests
# ---------------------------------------------------------------------------


def test_rate_limit_with_test_client() -> None:
    test_app = FastAPI()

    @test_app.get("/api/data")
    async def get_data() -> dict[str, str]:
        return {"data": "value"}

    @test_app.get("/api/health")
    async def get_health() -> dict[str, str]:
        return {"status": "ok"}

    @test_app.get("/static/app.css")
    async def get_static() -> dict[str, str]:
        return {"static": "css"}

    test_app.add_middleware(RateLimitMiddleware, rate_limit_per_min=2)

    client = TestClient(test_app)

    # 1st and 2nd requests pass
    r1 = client.get("/api/data")
    assert r1.status_code == 200
    assert r1.json() == {"data": "value"}

    r2 = client.get("/api/data")
    assert r2.status_code == 200

    # 3rd request is rate limited
    r3 = client.get("/api/data")
    assert r3.status_code == 429
    assert r3.json() == {"error": "rate_limited", "detail": "Rate limit exceeded"}
    assert "retry-after" in r3.headers
    assert int(r3.headers["retry-after"]) >= 1

    # Exempt health route passes even after rate limit reached
    r_health = client.get("/api/health")
    assert r_health.status_code == 200

    # Exempt static route passes
    r_static = client.get("/static/app.css")
    assert r_static.status_code == 200

    # Different IP forwarded via header passes
    r_diff = client.get("/api/data", headers={"X-Forwarded-For": "203.0.113.88"})
    assert r_diff.status_code == 200


def test_rate_limit_disabled_test_client() -> None:
    test_app = FastAPI()

    @test_app.get("/api/data")
    async def get_data() -> dict[str, str]:
        return {"data": "value"}

    test_app.add_middleware(RateLimitMiddleware, rate_limit_per_min=None)
    client = TestClient(test_app)

    for _ in range(10):
        res = client.get("/api/data")
        assert res.status_code == 200


# ---------------------------------------------------------------------------
# Server integration tests (create_app, value_error_handler, settings checks)
# ---------------------------------------------------------------------------


def test_create_app_wires_rate_limit_middleware(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OMNISCRIBE_RATE_LIMIT_PER_MIN", "100")
    settings = RuntimeSettings(rate_limit_per_min=100)
    with patch("omniscribe.server.load_settings", return_value=settings):
        app_instance = create_app()
        # Find middleware in user_middleware stack
        has_rate_limit = any(
            m.cls == RateLimitMiddleware
            for m in app_instance.user_middleware  # type: ignore[attr-defined]
        )
        assert has_rate_limit is True


def test_create_app_omits_rate_limit_middleware_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OMNISCRIBE_RATE_LIMIT_PER_MIN", raising=False)
    settings = RuntimeSettings(rate_limit_per_min=None)
    with patch("omniscribe.server.load_settings", return_value=settings):
        app_instance = create_app()
        has_rate_limit = any(
            m.cls == RateLimitMiddleware
            for m in app_instance.user_middleware  # type: ignore[attr-defined]
        )
        assert has_rate_limit is False


def test_sanitize_value_error_preserves_clean_messages() -> None:
    assert (
        _sanitize_value_error(ValueError("'text' is required")) == "'text' is required"
    )
    assert (
        _sanitize_value_error(ValueError("max 2 entries allowed"))
        == "max 2 entries allowed"
    )


def test_sanitize_value_error_sanitizes_paths_and_tracebacks() -> None:
    # Windows drive paths
    assert (
        _sanitize_value_error(ValueError("Failed to open C:\\secret\\key.pem"))
        == "Invalid input"
    )
    assert (
        _sanitize_value_error(ValueError("Could not access D:/data/private.db"))
        == "Invalid input"
    )
    # Unix absolute paths
    assert (
        _sanitize_value_error(ValueError("Failed reading /etc/shadow"))
        == "Invalid input"
    )
    assert (
        _sanitize_value_error(ValueError("Error in /home/user/app.py"))
        == "Invalid input"
    )
    # Tracebacks
    assert (
        _sanitize_value_error(
            ValueError("Traceback (most recent call last):\n  File 'a.py'")
        )
        == "Invalid input"
    )
    # Empty
    assert _sanitize_value_error(ValueError("")) == "Invalid input"


def test_server_value_error_handler_integration() -> None:
    test_app = FastAPI()

    @test_app.get("/api/test-error")
    async def trigger_error(path_leak: bool = False) -> None:
        if path_leak:
            raise ValueError("Failed to open C:\\Users\\Administrator\\data.txt")
        raise ValueError("'template' is required")

    @test_app.exception_handler(ValueError)
    async def value_error_handler(request: Any, exc: ValueError) -> Any:
        return JSONResponse(
            status_code=400,
            content={"error": "bad_request", "detail": _sanitize_value_error(exc)},
        )

    client = TestClient(test_app)

    # Clean message preserved
    res_clean = client.get("/api/test-error?path_leak=false")
    assert res_clean.status_code == 400
    assert res_clean.json() == {
        "error": "bad_request",
        "detail": "'template' is required",
    }

    # Path leak sanitized
    res_leak = client.get("/api/test-error?path_leak=true")
    assert res_leak.status_code == 400
    assert res_leak.json() == {
        "error": "bad_request",
        "detail": "Invalid input",
    }


def test_validate_runtime_settings_non_loopback_without_auth_raises() -> None:
    settings = RuntimeSettings(auth_token=None)
    with pytest.raises(SystemExit, match=r"(?i)auth"):
        _validate_runtime_settings(settings, host="0.0.0.0")


def test_validate_runtime_settings_placeholder_on_non_loopback_raises() -> None:
    settings = RuntimeSettings(auth_token="change-me-in-prod")
    with pytest.raises(SystemExit, match=r"(?i)placeholder"):
        _validate_runtime_settings(settings, host="0.0.0.0")


def test_validate_runtime_settings_placeholder_env_opt_out_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OMNISCRIBE_ALLOW_PLACEHOLDER_TOKEN", "true")
    settings = RuntimeSettings(auth_token="change-me-in-prod")
    # Should not raise SystemExit
    validated = _validate_runtime_settings(settings, host="0.0.0.0")
    assert validated.auth_token == "change-me-in-prod"


def test_validate_runtime_settings_detect_bind_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OMNISCRIBE_HOST", "0.0.0.0")
    assert _detect_bind_host() == "0.0.0.0"

    monkeypatch.delenv("OMNISCRIBE_HOST")
    monkeypatch.setenv("UVICORN_HOST", "192.168.1.100")
    assert _detect_bind_host() == "192.168.1.100"
