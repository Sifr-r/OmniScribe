"""SSRF-guarded URL fetch for glossary imports.

Adapted from the deleted `api/services/http_fetch.py` (`44ef123^`): the
URL and every redirect hop are validated against `is_ssrf_target`, the
TCP connection is pinned to the resolved IP (DNS-rebinding defense), and
redirects are followed manually up to ``_MAX_REDIRECTS``. Fetch failures
map to 502 `ai_error` (spec §8.3) and SSRF denials to 403 `ssrf_blocked`
via `GlossaryError`.
"""

from __future__ import annotations

from urllib.parse import urljoin, urlparse

import httpx

from omniscribe.plugins.glossary.service import GlossaryError
from omniscribe.utils.security import _PinnedIPTransport, is_ssrf_target

_MAX_REDIRECTS = 5
MAX_GLOSSARY_BYTES: int = 50 * 1024 * 1024
MAX_FETCH_BYTES: int = MAX_GLOSSARY_BYTES


def _sanitize_url(url: str) -> str:
    """Validate and sanitize URL for glossary fetching.

    Ensures the URL is a non-empty string, strips surrounding whitespace,
    enforces http or https scheme, and verifies a non-empty hostname is present.
    Raises GlossaryError(400, "bad_request", ...) on any validation failure.
    """
    if not isinstance(url, str) or not url.strip():
        raise GlossaryError(400, "bad_request", "URL must be a non-empty string.")

    cleaned = url.strip()
    try:
        parsed = urlparse(cleaned)
    except Exception as exc:
        raise GlossaryError(400, "bad_request", f"Malformed URL: {exc}") from exc

    scheme = (parsed.scheme or "").lower()
    if scheme not in {"http", "https"}:
        raise GlossaryError(
            400,
            "bad_request",
            f"Unsupported URL scheme: {scheme or 'none'}. Only http and https are allowed.",
        )

    hostname = parsed.hostname
    if not hostname:
        raise GlossaryError(400, "bad_request", "URL must include a valid hostname.")

    return cleaned


async def fetch_glossary_url(
    url: str,
    *,
    timeout: float = 30.0,
    max_bytes: int = MAX_GLOSSARY_BYTES,
    transport: httpx.AsyncBaseTransport | None = None,
) -> bytes:
    """Fetch a URL's body as bytes with SSRF protection on every hop."""
    current_url = _sanitize_url(url)

    for _ in range(_MAX_REDIRECTS + 1):
        check = await is_ssrf_target(current_url)
        if not check.allowed:
            raise GlossaryError(
                403,
                "ssrf_blocked",
                f"URL targets a blocked address: {check.reason or 'blocked'}",
            )
        if check.resolved_ip is None:
            raise GlossaryError(403, "ssrf_blocked", "URL resolved to no address.")

        parsed_target = urlparse(current_url)
        target_host = parsed_target.hostname or ""
        effective_transport = (
            transport
            if transport is not None
            else _PinnedIPTransport(
                target_host=target_host, resolved_ip=check.resolved_ip
            )
        )
        client = httpx.AsyncClient(
            transport=effective_transport,
            timeout=timeout,
            follow_redirects=False,
        )
        try:
            response = await client.get(current_url)

            if response.is_redirect:
                location = response.headers.get("Location")
                if not location:
                    raise GlossaryError(
                        502,
                        "ai_error",
                        f"Redirect response missing Location header for {current_url}",
                    )
                redirected_url = urljoin(current_url, location)
                current_url = _sanitize_url(redirected_url)
                continue

            response.raise_for_status()

            # Enforce max_bytes on Content-Length header if present
            content_length_header = response.headers.get("Content-Length")
            if content_length_header is not None:
                try:
                    content_length = int(content_length_header)
                    if content_length > max_bytes:
                        raise GlossaryError(
                            400,
                            "bad_request",
                            f"URL Content-Length {content_length} exceeds {max_bytes} bytes.",
                        )
                except ValueError:
                    pass

            content = response.content
            if len(content) > max_bytes:
                raise GlossaryError(
                    400,
                    "bad_request",
                    f"URL body exceeds {max_bytes} bytes.",
                )
            return content
        except GlossaryError:
            raise
        except httpx.TimeoutException as exc:
            raise GlossaryError(
                504, "timeout", f"Request timed out fetching {current_url}: {exc}"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise GlossaryError(
                502,
                "ai_error",
                f"HTTP {exc.response.status_code} error fetching {current_url}: {exc}",
            ) from exc
        except httpx.RequestError as exc:
            raise GlossaryError(
                502,
                "ai_error",
                f"Network request error fetching {current_url}: {exc}",
            ) from exc
        finally:
            await client.aclose()

    raise GlossaryError(
        502, "ai_error", f"Exceeded {_MAX_REDIRECTS} redirects for {url}"
    )


fetch_url_bytes = fetch_glossary_url

__all__ = [
    "MAX_FETCH_BYTES",
    "MAX_GLOSSARY_BYTES",
    "_sanitize_url",
    "fetch_glossary_url",
    "fetch_url_bytes",
]
