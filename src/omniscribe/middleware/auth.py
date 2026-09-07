"""ASGI auth middleware (audit 6.1a: ``OMNISCRIBE_AUTH_TOKEN``).

Audit 6.1a: bearer-token auth on the rebuilt harness route surface. The
``OMNISCRIBE_AUTH_TOKEN`` setting was wired into the startup guard back
in Sprint 5 / M-10 (placeholder detection + loopback-only enforcement),
but no request was ever rejected — the API surface was unauthenticated.
This module fills the gap: a single ASGI middleware that checks the
``Authorization: Bearer <token>`` header (or ``?token=`` query param for
SSE) on every protected route, using ``hmac.compare_digest`` for a
constant-time comparison (audit 4.13 / 4.16).

The middleware is opt-in: if ``auth_token`` is unset the request passes
through unchanged, so local-dev and CI environments do not need a token.
The startup guard in :mod:`omniscribe.server` is the operator-facing
backstop that refuses placeholder tokens on non-loopback binds; this
middleware is the request-time backstop.
"""

from __future__ import annotations

import hmac
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger("omniscribe.middleware.auth")

#: Path prefixes that are exempt from auth (the web UI index, static
#: assets, the readiness / liveness probes, the sample-PDF route, and
#: CORS preflight).
EXEMPT_PATH_PREFIXES: tuple[str, ...] = (
    "/static/",
    "/_static/",
    # Sprint 3 (RFC 002 §4 Option b, audit U12): the sample-PDF route
    # is always open. The canonical fixtures are public-domain test
    # assets, and the Profile 1 loopback Flutter client has no token
    # to send. Path-traversal protection comes from the route's
    # fixed allowlist (``ALLOWED_SAMPLE_PDFS`` in
    # ``omniscribe.plugins.sample_pdfs``), not from auth.
    "/api/sample-pdf/",
)

#: Exact paths that are exempt (probes, health, the bundled web UI index).
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

#: Path prefixes whose ``?token=`` query param is the documented channel
#: — the ``Authorization: Bearer`` header is also accepted, but the
#: browser's ``EventSource`` cannot send custom headers, so the
#: query-param fallback is required for SSE endpoints. The matcher in
#: :class:`BearerAuthMiddleware` requires the path to also end with
#: ``/events`` (see ``_matches_query_token_path``), so this tuple is a
#: prefix gate, not a free pass. Adding a non-SSE route here is a bug
#: — the URL-borne token would leak into nginx access logs, browser
#: history, and Referer headers.
#:
#: Phase 3.6 (4.2, 2026-09-05): narrowed from
#: ``("/api/process/", "/api/jobs/")`` to just the SSE prefix. The
#: previous tuple accepted ``?token=`` on every ``/api/process/*`` and
#: ``/api/jobs/*`` route (status, result, list, etc.), which is what
#: the audit caught as Medium (S5).
QUERY_TOKEN_PATHS: tuple[str, ...] = (
    "/api/process/",  # /api/process/{job_id}/events — SSE event stream
)

#: HTTP methods that do not require auth (CORS preflight).
EXEMPT_METHODS: frozenset[str] = frozenset({"OPTIONS"})


def _extract_bearer(headers: list[tuple[bytes, bytes]]) -> str | None:
    """Return the bearer token from an ASGI ``headers`` list, or None.

    ASGI headers are a list of ``(bytes, bytes)`` tuples; we look at
    case-insensitive ``authorization`` and parse the ``Bearer`` scheme.
    Leading / trailing whitespace is trimmed before the scheme split so
    a leading space (a common copy-paste error) does not silently
    reject a valid token.
    """
    for raw_name, raw_value in headers:
        if raw_name.lower() != b"authorization":
            continue
        try:
            value = raw_value.decode("latin-1").strip()
        except UnicodeDecodeError:
            return None
        if not value:
            return None
        scheme, _, token = value.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return None
        return token.strip()
    return None


def _extract_query_token(query_string: bytes) -> str | None:
    """Return the ``?token=`` value for SSE-style requests, or None."""
    if not query_string:
        return None
    try:
        from urllib.parse import parse_qs

        parsed = parse_qs(query_string.decode("latin-1"), keep_blank_values=False)
    except (UnicodeDecodeError, ValueError):
        return None
    values = parsed.get("token")
    if not values:
        return None
    return values[0].strip() or None


def _matches_query_token_path(path: str) -> bool:
    """Return True iff ``path`` is an SSE endpoint that accepts ``?token=``.

    The match is: any prefix in :data:`QUERY_TOKEN_PATHS` **and** the
    path ends with ``/events``. The two-clause form exists so that
    ``?token=`` is only accepted on the SSE event stream, not on
    status / result / list endpoints that share the same prefix. URL-
    borne tokens leak into nginx access logs, browser history, and
    ``Referer`` headers; the surface area must be the smallest that
    still lets ``EventSource`` (which cannot send custom headers)
    work.

    Phase 3.6 (4.2, 2026-09-05).
    """
    return any(
        path.startswith(prefix) and path.endswith("/events")
        for prefix in QUERY_TOKEN_PATHS
    )


def _is_exempt(path: str, method: str) -> bool:
    if method.upper() in EXEMPT_METHODS:
        return True
    if path in EXEMPT_EXACT_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in EXEMPT_PATH_PREFIXES)


def _token_matches(expected: str, provided: str) -> bool:
    """Constant-time token comparison (audit 4.13 / 4.16)."""
    if not expected or not provided:
        return False
    return hmac.compare_digest(expected.encode("utf-8"), provided.encode("utf-8"))


#: ASGI 3.0 receive / send type aliases for the middleware.
ASGIRecv = Callable[[], Awaitable[dict]]
ASGISend = Callable[[dict], Awaitable[None]]


class BearerAuthMiddleware:
    """ASGI 3.0 middleware that rejects requests without a valid bearer.

    Usage::

        web_app.add_middleware(
            BearerAuthMiddleware, expected_token=settings.auth_token
        )

    When ``expected_token`` is falsy the middleware is a no-op (local
    dev / CI). When set, every request to a non-exempt path must carry
    a matching ``Authorization: Bearer`` header (or, on SSE-style
    routes, a ``?token=`` query param). Failures return ``401`` with a
    stable ``WWW-Authenticate: Bearer`` challenge header so clients can
    react.
    """

    def __init__(self, app: Callable, expected_token: str | None) -> None:
        self._app = app
        self._expected = (expected_token or "").strip()
        if self._expected:
            logger.info(
                "BearerAuthMiddleware armed: protected routes require a valid "
                "OMNISCRIBE_AUTH_TOKEN (exempt: %s, %s)",
                sorted(EXEMPT_EXACT_PATHS),
                list(EXEMPT_PATH_PREFIXES),
            )

    async def __call__(self, scope: dict, receive: ASGIRecv, send: ASGISend) -> None:
        if scope.get("type") != "http" or not self._expected:
            await self._app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "GET")
        if _is_exempt(path, method):
            await self._app(scope, receive, send)
            return

        # Prefer the Authorization header; fall back to the SSE query-param
        # channel on routes that opt in to it. ``_matches_query_token_path``
        # requires both the prefix match AND a ``/events`` suffix so
        # query-param tokens never escape the SSE surface (Phase 3.6).
        token = _extract_bearer(scope.get("headers") or [])
        if not token and _matches_query_token_path(path):
            token = _extract_query_token(scope.get("query_string", b""))

        if not token or not _token_matches(self._expected, token):
            await self._send_unauthorized(scope, receive, send)
            return

        await self._app(scope, receive, send)

    @staticmethod
    async def _send_unauthorized(
        scope: dict, receive: ASGIRecv, send: ASGISend
    ) -> None:
        # Log the rejection at WARNING (operator signal) but never include
        # the path in any client-visible surface to avoid log injection.
        logger.warning(
            "auth.middleware: rejected %s %s (no / invalid bearer)",
            scope.get("method", "GET"),
            scope.get("path", ""),
        )
        body = b'{"error":"unauthorized","detail":"valid bearer token required"}'
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("latin-1")),
            (b"www-authenticate", b'Bearer realm="omniscribe"'),
        ]
        await send({"type": "http.response.start", "status": 401, "headers": headers})
        await send({"type": "http.response.body", "body": body})


__all__ = [
    "EXEMPT_EXACT_PATHS",
    "EXEMPT_METHODS",
    "EXEMPT_PATH_PREFIXES",
    "QUERY_TOKEN_PATHS",
    "BearerAuthMiddleware",
    "_extract_bearer",
    "_extract_query_token",
    "_is_exempt",
    "_matches_query_token_path",
    "_token_matches",
]
