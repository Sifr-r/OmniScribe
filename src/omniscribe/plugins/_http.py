"""Shared HTTP helpers for plugin routes.

The :func:`envelope` and :func:`bearer_token` helpers used to live as private
duplicates inside each plugin's ``routes.py`` (audit findings F1 + F10). They
are now defined once here so every plugin can share the canonical
``{error, detail}`` shape the Flutter client parses and the canonical
``Authorization: Bearer <token>`` extraction.

:func:`plugin_error_exception_handler` complements :func:`envelope` by
letting any :class:`omniscribe.plugins.errors.PluginError` subclass be
converted to the same envelope shape by a single FastAPI exception handler,
removing the per-handler ``except XError as exc: return envelope(...)``
boilerplate (audit F2).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from omniscribe.plugins.errors import PluginError

__all__ = [
    "bearer_token",
    "envelope",
    "extract_token",
    "plugin_error_exception_handler",
]


def envelope(status_code: int, error: str, detail: str) -> JSONResponse:
    """Stable ``{error, detail}`` envelope the Flutter client parses.

    Use ``status_code`` + a short, snake-cased ``error`` tag + a human-readable
    ``detail`` string. The status code is passed straight through to the
    underlying :class:`JSONResponse`.
    """
    return JSONResponse(
        status_code=status_code, content={"error": error, "detail": detail}
    )


def bearer_token(authorization: str | None) -> str | None:
    """Return the token from an ``Authorization: Bearer <token>`` header.

    Returns ``None`` for missing headers, non-``Bearer`` schemes, or empty
    tokens. Whitespace around the token is trimmed.
    """
    if authorization and authorization.startswith("Bearer "):
        return authorization.removeprefix("Bearer ").strip()
    return None


def extract_token(
    *,
    token: str | None = None,
    authorization: str | None = None,
    x_artifact_token: str | None = None,
    x_job_token: str | None = None,
) -> str | None:
    """Extract capability token from query parameter or headers."""
    if token and token.strip():
        return token.strip()
    if x_job_token and x_job_token.strip():
        return x_job_token.strip()
    if x_artifact_token and x_artifact_token.strip():
        return x_artifact_token.strip()
    if authorization and authorization.strip():
        auth = authorization.strip()
        if auth.startswith("Bearer "):
            return auth.removeprefix("Bearer ").strip()
        return auth
    return None


async def plugin_error_exception_handler(
    request: Any, exc: PluginError
) -> JSONResponse:
    """FastAPI exception handler that turns :class:`PluginError` into :func:`envelope`.

    Drop-in replacement for the byte-identical
    ``except XError as exc: return envelope(exc.status_code, exc.error, exc.detail)``
    blocks that used to live in every plugin route (audit F2). Register once
    at app startup, e.g.::

        app.add_exception_handler(PluginError, plugin_error_exception_handler)

    The handler forwards the exception's own ``status_code``, ``error`` tag,
    and ``detail`` verbatim — including any tag customisation the raising
    site encoded via ``raise XError(status_code, "<custom_tag>", detail)``.
    """
    return envelope(exc.status_code, exc.error, exc.detail)
