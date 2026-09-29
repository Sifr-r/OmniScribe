"""Tests for the ASGI bearer-token auth middleware (audit 6.1a)."""

from __future__ import annotations

import asyncio
from typing import Any

from omniscribe.middleware.auth import (
    EXEMPT_EXACT_PATHS,
    EXEMPT_METHODS,
    EXEMPT_PATH_PREFIXES,
    QUERY_TOKEN_PATHS,
    BearerAuthMiddleware,
    _extract_bearer,
    _extract_query_token,
    _extract_ws_query_token,
    _is_exempt,
    _token_matches,
)

# ---------------------------------------------------------------------------
# Pure helper tests
# ---------------------------------------------------------------------------


def test_extract_bearer_parses_standard_header() -> None:
    headers = [(b"authorization", b"Bearer abc.def.ghi")]
    assert _extract_bearer(headers) == "abc.def.ghi"


def test_extract_bearer_handles_lowercase_scheme() -> None:
    headers = [(b"authorization", b"bearer my-token")]
    assert _extract_bearer(headers) == "my-token"


def test_extract_bearer_trims_whitespace() -> None:
    headers = [(b"authorization", b"  Bearer   spaced-token  ")]
    assert _extract_bearer(headers) == "spaced-token"


def test_extract_bearer_returns_none_on_wrong_scheme() -> None:
    headers = [(b"authorization", b"Basic dXNlcjpwYXNz")]
    assert _extract_bearer(headers) is None


def test_extract_bearer_returns_none_on_empty_token() -> None:
    headers = [(b"authorization", b"Bearer ")]
    assert _extract_bearer(headers) is None


def test_extract_bearer_returns_none_when_absent() -> None:
    assert _extract_bearer([(b"content-type", b"application/json")]) is None


def test_extract_bearer_ignores_other_headers() -> None:
    headers = [
        (b"x-forwarded-for", b"127.0.0.1"),
        (b"authorization", b"Bearer real-token"),
        (b"user-agent", b"test"),
    ]
    assert _extract_bearer(headers) == "real-token"


def test_extract_query_token_parses_token_param() -> None:
    assert _extract_query_token(b"token=abc&page=1") == "abc"


def test_extract_query_token_handles_url_encoded_value() -> None:
    from urllib.parse import quote

    encoded = quote("abc/def+ghi=", safe="")
    assert _extract_query_token(f"token={encoded}".encode("latin-1")) == "abc/def+ghi="


def test_extract_query_token_returns_none_when_missing() -> None:
    assert _extract_query_token(b"page=1&size=10") is None


def test_extract_query_token_returns_none_for_empty() -> None:
    assert _extract_query_token(b"") is None


def test_extract_ws_query_token_supports_auth_token_and_token() -> None:
    assert _extract_ws_query_token(b"auth_token=abc123") == "abc123"
    assert _extract_ws_query_token(b"token=xyz456") == "xyz456"
    assert _extract_ws_query_token(b"auth_token=first&token=second") == "first"
    assert _extract_ws_query_token(b"other=123") is None
    assert _extract_ws_query_token(b"") is None


def test_is_exempt_lists_exact_paths() -> None:
    for path in EXEMPT_EXACT_PATHS:
        assert _is_exempt(path, "GET"), path
    for path in ("/ready", "/readyz", "/api/healthz"):
        assert _is_exempt(path, "GET")


def test_is_exempt_lists_prefix_paths() -> None:
    for prefix in EXEMPT_PATH_PREFIXES:
        assert _is_exempt(prefix + "anything.css", "GET"), prefix


def test_is_exempt_options_method_always_passes() -> None:
    for method in EXEMPT_METHODS:
        assert _is_exempt("/api/jobs", method), method


def test_is_exempt_rejects_protected_route() -> None:
    assert not _is_exempt("/api/process", "POST")
    assert not _is_exempt("/api/jobs", "GET")
    assert not _is_exempt("/api/config", "PUT")


def test_token_matches_true_on_equal() -> None:
    assert _token_matches("secret-123", "secret-123") is True


def test_token_matches_false_on_difference() -> None:
    assert _token_matches("secret-123", "secret-124") is False


def test_token_matches_false_on_prefix() -> None:
    """The constant-time check is not fooled by a one-sided prefix."""
    assert _token_matches("secret-123", "secret") is False


def test_token_matches_false_on_empty_inputs() -> None:
    assert _token_matches("", "secret") is False
    assert _token_matches("secret", "") is False
    assert _token_matches("", "") is False


# ---------------------------------------------------------------------------
# Middleware behavioural tests (ASGI 3.0 direct invocation)
# ---------------------------------------------------------------------------


def _scope(
    path: str = "/api/process",
    method: str = "GET",
    headers: list[tuple[bytes, bytes]] | None = None,
    query_string: bytes = b"",
) -> dict[str, Any]:
    return {
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode("latin-1"),
        "query_string": query_string,
        "headers": headers or [],
    }


class _CallRecorder:
    """Captures the upstream app call (or, on rejection, the 401 send)."""

    def __init__(self, *, expected_called: bool = True) -> None:
        self.upstream_called = False
        self.expected_called = expected_called
        self.sent_start: dict | None = None
        self.sent_bodies: list[bytes] = []

    async def downstream(self, scope: dict, receive: Any, send: Any) -> None:
        self.upstream_called = True

    async def receive(self) -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(self, message: dict) -> None:
        if message["type"] == "http.response.start":
            self.sent_start = message
        elif message["type"] == "http.response.body":
            self.sent_bodies.append(message.get("body", b""))


async def _drive(
    expected_token: str | None,
    scope: dict,
) -> _CallRecorder:
    recorder = _CallRecorder()
    middleware = BearerAuthMiddleware(recorder.downstream, expected_token)
    await middleware(scope, recorder.receive, recorder.send)
    return recorder


async def test_middleware_is_noop_when_token_unset() -> None:
    recorder = await _drive(None, _scope(path="/api/process", method="POST"))
    assert recorder.upstream_called is True
    assert recorder.sent_start is None


async def test_middleware_passes_exempt_index_without_token() -> None:
    recorder = await _drive("secret-123", _scope(path="/", method="GET"))
    assert recorder.upstream_called is True
    assert recorder.sent_start is None


async def test_middleware_passes_exempt_health_without_token() -> None:
    for path in EXEMPT_EXACT_PATHS:
        if path == "/":
            continue
        recorder = await _drive("secret-123", _scope(path=path, method="GET"))
        assert recorder.upstream_called is True, path
        assert recorder.sent_start is None, path


async def test_middleware_passes_probes_without_bearer_token() -> None:
    for path in ("/ready", "/readyz", "/api/healthz"):
        recorder = await _drive("secret-123", _scope(path=path, method="GET"))
        assert recorder.upstream_called is True, path
        assert recorder.sent_start is None, path


async def test_middleware_passes_static_assets_without_token() -> None:
    recorder = await _drive("secret-123", _scope(path="/static/app.css", method="GET"))
    assert recorder.upstream_called is True


async def test_middleware_passes_options_preflight_without_token() -> None:
    recorder = await _drive("secret-123", _scope(path="/api/jobs", method="OPTIONS"))
    assert recorder.upstream_called is True
    assert recorder.sent_start is None


async def test_middleware_rejects_protected_route_without_token() -> None:
    recorder = await _drive("secret-123", _scope(path="/api/jobs", method="GET"))
    assert recorder.upstream_called is False
    assert recorder.sent_start is not None
    assert recorder.sent_start["status"] == 401
    headers = dict(recorder.sent_start["headers"])
    assert headers[b"content-type"] == b"application/json"
    assert b"www-authenticate" in headers
    # The response must carry a content-length (paired with the JSON body
    # the middleware writes) and must NOT carry a content-disposition
    # (this is a 401 error, not a file download).
    assert b"content-length" in headers
    assert b"content-disposition" not in headers


async def test_middleware_rejects_protected_route_with_wrong_token() -> None:
    headers = [(b"authorization", b"Bearer wrong-token")]
    recorder = await _drive(
        "secret-123", _scope(path="/api/jobs", method="GET", headers=headers)
    )
    assert recorder.upstream_called is False
    assert recorder.sent_start is not None
    assert recorder.sent_start["status"] == 401


async def test_middleware_accepts_protected_route_with_matching_token() -> None:
    headers = [(b"authorization", b"Bearer secret-123")]
    recorder = await _drive(
        "secret-123", _scope(path="/api/jobs", method="GET", headers=headers)
    )
    assert recorder.upstream_called is True
    assert recorder.sent_start is None


async def test_middleware_supports_sse_query_token_fallback() -> None:
    """EventSource in the browser cannot send Authorization; the
    query-param channel is the documented fallback for SSE endpoints.
    """
    path = next(iter(QUERY_TOKEN_PATHS)) + "abc-123/events"
    scope = _scope(path=path, method="GET", query_string=b"token=secret-123")
    recorder = await _drive("secret-123", scope)
    assert recorder.upstream_called is True
    assert recorder.sent_start is None


async def test_middleware_rejects_sse_path_with_wrong_query_token() -> None:
    path = next(iter(QUERY_TOKEN_PATHS)) + "abc-123/events"
    scope = _scope(path=path, method="GET", query_string=b"token=wrong")
    recorder = await _drive("secret-123", scope)
    assert recorder.upstream_called is False
    assert recorder.sent_start is not None
    assert recorder.sent_start["status"] == 401


async def test_middleware_rejects_query_token_on_non_events_paths() -> None:
    """Phase 3.6 (4.2, 2026-09-05): ``?token=`` is accepted only on the
    SSE event stream, not on status / result / list endpoints that
    share the same prefix. URL-borne tokens leak into nginx access
    logs, browser history, and ``Referer`` headers — keep the surface
    small.
    """
    # /api/process/{job_id}/status does NOT end with /events.
    status_path = next(iter(QUERY_TOKEN_PATHS)) + "abc-123/status"
    recorder = await _drive(
        "secret-123",
        _scope(path=status_path, method="GET", query_string=b"token=secret-123"),
    )
    assert recorder.upstream_called is False
    assert recorder.sent_start is not None
    assert recorder.sent_start["status"] == 401

    # /api/jobs/{job_id}/result does NOT end with /events and is no
    # longer in QUERY_TOKEN_PATHS at all (removed in Phase 3.6).
    jobs_path = "/api/jobs/abc-123/result"
    recorder = await _drive(
        "secret-123",
        _scope(path=jobs_path, method="GET", query_string=b"token=secret-123"),
    )
    assert recorder.upstream_called is False
    assert recorder.sent_start is not None
    assert recorder.sent_start["status"] == 401


async def test_middleware_401_body_is_stable_envelope() -> None:
    recorder = await _drive("secret-123", _scope(path="/api/jobs", method="GET"))
    body = b"".join(recorder.sent_bodies)
    assert b'"error":"unauthorized"' in body
    assert b'"detail"' in body


# ---------------------------------------------------------------------------
# Constant-time comparison (audit 4.13 / 4.16)
# ---------------------------------------------------------------------------


def test_compare_digest_is_used_for_token_comparison() -> None:
    """Verify the middleware uses hmac.compare_digest, not ``==``.

    We patch ``hmac.compare_digest`` to record the call; if the middleware
    bypasses it for any reason, the mock never fires and the assertion
    below fails.
    """
    import hmac as _hmac
    from unittest.mock import patch

    from omniscribe.middleware import auth as auth_mod

    real = _hmac.compare_digest
    calls: list[tuple[bytes, bytes]] = []

    def spy(a: bytes, b: bytes) -> bool:
        calls.append((a, b))
        return real(a, b)

    async def _exercise() -> None:
        headers = [(b"authorization", b"Bearer real-token")]
        middleware = auth_mod.BearerAuthMiddleware(
            (lambda *a, **kw: asyncio.sleep(0)), "expected-token"
        )
        await middleware(
            _scope(path="/api/jobs", headers=headers), _noop_recv, _noop_send
        )

    with patch.object(_hmac, "compare_digest", side_effect=spy):
        asyncio.run(_exercise())

    assert calls, "compare_digest must be invoked for the token check"
    a, b = calls[0]
    assert a == b"expected-token"
    assert b == b"real-token"


async def _noop_recv() -> dict:
    return {"type": "http.request", "body": b"", "more_body": False}


async def _noop_send(message: dict) -> None:
    return None


async def test_middleware_rejects_websocket_without_token() -> None:
    sent: list[dict] = []

    async def send(msg: dict) -> None:
        sent.append(msg)

    recorder = _CallRecorder()
    middleware = BearerAuthMiddleware(recorder.downstream, "secret-123")
    ws_scope = {
        "type": "websocket",
        "path": "/api/ws/test",
        "headers": [],
        "query_string": b"",
    }
    await middleware(ws_scope, _noop_recv, send)
    assert recorder.upstream_called is False
    assert sent == [{"type": "websocket.close", "code": 4401, "reason": "unauthorized"}]


async def test_middleware_accepts_websocket_with_auth_token_query() -> None:
    sent: list[dict] = []

    async def send(msg: dict) -> None:
        sent.append(msg)

    recorder = _CallRecorder()
    middleware = BearerAuthMiddleware(recorder.downstream, "secret-123")
    ws_scope = {
        "type": "websocket",
        "path": "/api/ws/test",
        "headers": [],
        "query_string": b"auth_token=secret-123",
    }
    await middleware(ws_scope, _noop_recv, send)
    assert recorder.upstream_called is True
    assert sent == []


async def test_middleware_accepts_websocket_on_exempt_path() -> None:
    sent: list[dict] = []

    async def send(msg: dict) -> None:
        sent.append(msg)

    recorder = _CallRecorder()
    middleware = BearerAuthMiddleware(recorder.downstream, "secret-123")
    ws_scope = {
        "type": "websocket",
        "path": "/api/health",
        "headers": [],
        "query_string": b"",
    }
    await middleware(ws_scope, _noop_recv, send)
    assert recorder.upstream_called is True
    assert sent == []
