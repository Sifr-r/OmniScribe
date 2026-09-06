"""Unit and security tests for SSRF-guarded glossary HTTP fetch."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from omniscribe.plugins.glossary.http_fetch import (
    MAX_FETCH_BYTES,
    MAX_GLOSSARY_BYTES,
    _sanitize_url,
    fetch_glossary_url,
    fetch_url_bytes,
)
from omniscribe.plugins.glossary.service import GlossaryError
from omniscribe.utils.security import SSRFCheckResult

# =============================================================================
# Constants & Aliases
# =============================================================================


def test_constants_and_aliases() -> None:
    """Verify exported constants and backward-compatible alias."""
    assert MAX_FETCH_BYTES == MAX_GLOSSARY_BYTES
    assert MAX_FETCH_BYTES == 50 * 1024 * 1024
    assert fetch_url_bytes is fetch_glossary_url


# =============================================================================
# _sanitize_url Tests
# =============================================================================


@pytest.mark.parametrize(
    ("raw_url", "expected"),
    [
        ("http://example.com", "http://example.com"),
        ("https://example.com/glossary.csv", "https://example.com/glossary.csv"),
        ("  https://example.com/path  ", "https://example.com/path"),
        (
            "http://example.com:8080/data?format=csv#section",
            "http://example.com:8080/data?format=csv#section",
        ),
        (
            "https://sub.domain.example.org/api/v1/glossary.json",
            "https://sub.domain.example.org/api/v1/glossary.json",
        ),
        ("http://93.184.216.34/glossary.tsv", "http://93.184.216.34/glossary.tsv"),
        ("HTTP://EXAMPLE.COM/CAPS", "HTTP://EXAMPLE.COM/CAPS"),
    ],
)
def test_sanitize_url_valid(raw_url: str, expected: str) -> None:
    """Verify valid http/https URLs are sanitized and trimmed correctly."""
    assert _sanitize_url(raw_url) == expected


@pytest.mark.parametrize(
    "invalid_scheme_url",
    [
        "ftp://example.com/data.csv",
        "file:///etc/passwd",
        "file://localhost/etc/passwd",
        "gopher://gopher.floodgap.com",
        "javascript:alert(1)",
        "data:text/csv;base64,YWJjLDEyMw==",
        "ws://example.com/socket",
        "wss://example.com/socket",
        "ssh://git@github.com/repo.git",
    ],
)
def test_sanitize_url_invalid_schemes(invalid_scheme_url: str) -> None:
    """Verify non-http/https schemes are rejected with 400 bad_request."""
    with pytest.raises(GlossaryError) as exc_info:
        _sanitize_url(invalid_scheme_url)
    assert exc_info.value.status_code == 400
    assert exc_info.value.error == "bad_request"
    assert "Unsupported URL scheme" in exc_info.value.detail


@pytest.mark.parametrize(
    "malformed_url",
    [
        "",
        "   ",
        "\t\n",
        "http://",
        "https://",
        "http:///path",
        "https:///only-path",
        "http://:80",
        "not-a-url",
        "://missing-scheme",
    ],
)
def test_sanitize_url_malformed_and_empty(malformed_url: str) -> None:
    """Verify empty and malformed URLs are rejected with 400 bad_request."""
    with pytest.raises(GlossaryError) as exc_info:
        _sanitize_url(malformed_url)
    assert exc_info.value.status_code == 400
    assert exc_info.value.error == "bad_request"


def test_sanitize_url_non_string() -> None:
    """Verify non-string inputs are rejected with 400 bad_request."""
    with pytest.raises(GlossaryError) as exc_info:
        _sanitize_url(None)  # type: ignore[arg-type]
    assert exc_info.value.status_code == 400
    assert exc_info.value.error == "bad_request"


# =============================================================================
# SSRF Protection Tests
# =============================================================================


@pytest.mark.parametrize(
    "blocked_ip",
    [
        "127.0.0.1",
        "10.0.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "0.0.0.0",
    ],
)
async def test_fetch_ssrf_blocked_private_ips(
    blocked_ip: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify private, loopback, and metadata IPs are rejected when ALLOW_SSRF_LOCAL=false."""
    monkeypatch.setenv("ALLOW_SSRF_LOCAL", "false")
    url = f"http://{blocked_ip}/glossary.csv"
    with pytest.raises(GlossaryError) as exc_info:
        await fetch_glossary_url(url)
    assert exc_info.value.status_code == 403
    assert exc_info.value.error == "ssrf_blocked"
    assert "URL targets a blocked address" in exc_info.value.detail


async def test_fetch_ssrf_resolved_ip_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify URLs where DNS returns no resolved IP are rejected with 403 ssrf_blocked."""

    async def fake_ssrf(target_url: str | None) -> SSRFCheckResult:
        return SSRFCheckResult(allowed=True, resolved_ip=None, reason=None)

    monkeypatch.setattr(
        "omniscribe.plugins.glossary.http_fetch.is_ssrf_target", fake_ssrf
    )
    with pytest.raises(GlossaryError) as exc_info:
        await fetch_glossary_url("https://example.com/glossary.csv")
    assert exc_info.value.status_code == 403
    assert exc_info.value.error == "ssrf_blocked"
    assert "URL resolved to no address" in exc_info.value.detail


# =============================================================================
# Size Limit Enforcement Tests
# =============================================================================


async def test_fetch_content_length_exceeds_max_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify response with Content-Length exceeding limit is rejected with 400 bad_request."""

    async def fake_ssrf(target_url: str | None) -> SSRFCheckResult:
        return SSRFCheckResult(allowed=True, resolved_ip="93.184.216.34")

    monkeypatch.setattr(
        "omniscribe.plugins.glossary.http_fetch.is_ssrf_target", fake_ssrf
    )

    async def fake_get(
        self: httpx.AsyncClient, url: str, **kwargs: Any
    ) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Length": "10000"},
            content=b"abc",
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    with pytest.raises(GlossaryError) as exc_info:
        await fetch_glossary_url("https://example.com/glossary.csv", max_bytes=5000)
    assert exc_info.value.status_code == 400
    assert exc_info.value.error == "bad_request"
    assert "exceeds 5000 bytes" in exc_info.value.detail


async def test_fetch_content_length_exceeds_max_fetch_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify default MAX_FETCH_BYTES limit is enforced against Content-Length header."""

    async def fake_ssrf(target_url: str | None) -> SSRFCheckResult:
        return SSRFCheckResult(allowed=True, resolved_ip="93.184.216.34")

    monkeypatch.setattr(
        "omniscribe.plugins.glossary.http_fetch.is_ssrf_target", fake_ssrf
    )

    oversized_length = str(MAX_FETCH_BYTES + 1024)

    async def fake_get(
        self: httpx.AsyncClient, url: str, **kwargs: Any
    ) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Length": oversized_length},
            content=b"",
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    with pytest.raises(GlossaryError) as exc_info:
        await fetch_glossary_url("https://example.com/glossary.csv")
    assert exc_info.value.status_code == 400
    assert exc_info.value.error == "bad_request"
    assert f"exceeds {MAX_FETCH_BYTES} bytes" in exc_info.value.detail


async def test_fetch_body_exceeds_max_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify response body exceeding limit without Content-Length is rejected with 400 bad_request."""

    async def fake_ssrf(target_url: str | None) -> SSRFCheckResult:
        return SSRFCheckResult(allowed=True, resolved_ip="93.184.216.34")

    monkeypatch.setattr(
        "omniscribe.plugins.glossary.http_fetch.is_ssrf_target", fake_ssrf
    )

    async def fake_get(
        self: httpx.AsyncClient, url: str, **kwargs: Any
    ) -> httpx.Response:
        resp = httpx.Response(
            200,
            content=b"x" * 200,
            request=httpx.Request("GET", url),
        )
        del resp.headers["Content-Length"]
        return resp

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    with pytest.raises(GlossaryError) as exc_info:
        await fetch_glossary_url("https://example.com/glossary.csv", max_bytes=100)
    assert exc_info.value.status_code == 400
    assert exc_info.value.error == "bad_request"
    assert "URL body exceeds 100 bytes" in exc_info.value.detail


# =============================================================================
# Redirect Limits & Location Tests
# =============================================================================


async def test_fetch_redirect_follows_up_to_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify clean redirect following up to _MAX_REDIRECTS (5 hops) succeeds."""

    async def fake_ssrf(target_url: str | None) -> SSRFCheckResult:
        return SSRFCheckResult(allowed=True, resolved_ip="93.184.216.34")

    monkeypatch.setattr(
        "omniscribe.plugins.glossary.http_fetch.is_ssrf_target", fake_ssrf
    )

    visited_urls: list[str] = []

    async def fake_get(
        self: httpx.AsyncClient, url: str, **kwargs: Any
    ) -> httpx.Response:
        visited_urls.append(url)
        hop_num = len(visited_urls)
        if hop_num <= 5:
            return httpx.Response(
                302,
                headers={"Location": f"https://example.com/hop{hop_num}"},
                request=httpx.Request("GET", url),
            )
        return httpx.Response(
            200,
            content=b"final_destination_content",
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    result = await fetch_glossary_url("https://example.com/start")
    assert result == b"final_destination_content"
    assert len(visited_urls) == 6
    assert visited_urls[0] == "https://example.com/start"
    assert visited_urls[-1] == "https://example.com/hop5"


async def test_fetch_redirect_exceeded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify exceeding _MAX_REDIRECTS raises 502 ai_error."""

    async def fake_ssrf(target_url: str | None) -> SSRFCheckResult:
        return SSRFCheckResult(allowed=True, resolved_ip="93.184.216.34")

    monkeypatch.setattr(
        "omniscribe.plugins.glossary.http_fetch.is_ssrf_target", fake_ssrf
    )

    hop_counter: list[int] = [0]

    async def fake_get(
        self: httpx.AsyncClient, url: str, **kwargs: Any
    ) -> httpx.Response:
        hop_counter[0] += 1
        return httpx.Response(
            302,
            headers={"Location": f"https://example.com/loop{hop_counter[0]}"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    with pytest.raises(GlossaryError) as exc_info:
        await fetch_glossary_url("https://example.com/start")
    assert exc_info.value.status_code == 502
    assert exc_info.value.error == "ai_error"
    assert "Exceeded 5 redirects" in exc_info.value.detail


async def test_fetch_redirect_missing_location_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify redirect missing Location header raises 502 ai_error."""

    async def fake_ssrf(target_url: str | None) -> SSRFCheckResult:
        return SSRFCheckResult(allowed=True, resolved_ip="93.184.216.34")

    monkeypatch.setattr(
        "omniscribe.plugins.glossary.http_fetch.is_ssrf_target", fake_ssrf
    )

    async def fake_get(
        self: httpx.AsyncClient, url: str, **kwargs: Any
    ) -> httpx.Response:
        return httpx.Response(301, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    with pytest.raises(GlossaryError) as exc_info:
        await fetch_glossary_url("https://example.com/redirect-no-location")
    assert exc_info.value.status_code == 502
    assert exc_info.value.error == "ai_error"
    assert "missing Location header" in exc_info.value.detail


# =============================================================================
# HTTP Error & Network Exception Handling Tests
# =============================================================================


@pytest.mark.parametrize("status_code", [404, 500, 502, 503])
async def test_fetch_http_status_errors(
    status_code: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify HTTP status errors (404, 500, etc.) are translated to 502 ai_error."""

    async def fake_ssrf(target_url: str | None) -> SSRFCheckResult:
        return SSRFCheckResult(allowed=True, resolved_ip="93.184.216.34")

    monkeypatch.setattr(
        "omniscribe.plugins.glossary.http_fetch.is_ssrf_target", fake_ssrf
    )

    async def fake_get(
        self: httpx.AsyncClient, url: str, **kwargs: Any
    ) -> httpx.Response:
        return httpx.Response(
            status_code,
            content=b"error details",
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    with pytest.raises(GlossaryError) as exc_info:
        await fetch_glossary_url("https://example.com/not-found")
    assert exc_info.value.status_code == 502
    assert exc_info.value.error == "ai_error"
    assert f"HTTP {status_code} error" in exc_info.value.detail


async def test_fetch_timeout_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify httpx.TimeoutException maps to 504 timeout."""

    async def fake_ssrf(target_url: str | None) -> SSRFCheckResult:
        return SSRFCheckResult(allowed=True, resolved_ip="93.184.216.34")

    monkeypatch.setattr(
        "omniscribe.plugins.glossary.http_fetch.is_ssrf_target", fake_ssrf
    )

    async def fake_get(
        self: httpx.AsyncClient, url: str, **kwargs: Any
    ) -> httpx.Response:
        raise httpx.ReadTimeout("Read timed out after 30.0s")

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    with pytest.raises(GlossaryError) as exc_info:
        await fetch_glossary_url("https://example.com/slow")
    assert exc_info.value.status_code == 504
    assert exc_info.value.error == "timeout"
    assert "Request timed out" in exc_info.value.detail


async def test_fetch_request_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify general httpx.RequestError maps to 502 ai_error."""

    async def fake_ssrf(target_url: str | None) -> SSRFCheckResult:
        return SSRFCheckResult(allowed=True, resolved_ip="93.184.216.34")

    monkeypatch.setattr(
        "omniscribe.plugins.glossary.http_fetch.is_ssrf_target", fake_ssrf
    )

    async def fake_get(
        self: httpx.AsyncClient, url: str, **kwargs: Any
    ) -> httpx.Response:
        raise httpx.ConnectError("Connection refused by peer")

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    with pytest.raises(GlossaryError) as exc_info:
        await fetch_glossary_url("https://example.com/broken")
    assert exc_info.value.status_code == 502
    assert exc_info.value.error == "ai_error"
    assert "Network request error" in exc_info.value.detail


# =============================================================================
# Success Content Tests (Text & Binary)
# =============================================================================


async def test_fetch_success_text_content(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify successful fetch with UTF-8 text glossary content."""

    async def fake_ssrf(target_url: str | None) -> SSRFCheckResult:
        return SSRFCheckResult(allowed=True, resolved_ip="93.184.216.34")

    monkeypatch.setattr(
        "omniscribe.plugins.glossary.http_fetch.is_ssrf_target", fake_ssrf
    )

    csv_data = b"source,target\nHello,Bonjour\nGoodbye,Au revoir\n"

    async def fake_get(
        self: httpx.AsyncClient, url: str, **kwargs: Any
    ) -> httpx.Response:
        return httpx.Response(
            200,
            content=csv_data,
            headers={"Content-Type": "text/csv; charset=utf-8"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    content = await fetch_glossary_url("https://example.com/terms.csv")
    assert content == csv_data


async def test_fetch_success_binary_content(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify successful fetch with arbitrary binary content."""

    async def fake_ssrf(target_url: str | None) -> SSRFCheckResult:
        return SSRFCheckResult(allowed=True, resolved_ip="93.184.216.34")

    monkeypatch.setattr(
        "omniscribe.plugins.glossary.http_fetch.is_ssrf_target", fake_ssrf
    )

    binary_data = bytes([0x00, 0x01, 0x80, 0xFE, 0xFF]) * 20

    async def fake_get(
        self: httpx.AsyncClient, url: str, **kwargs: Any
    ) -> httpx.Response:
        return httpx.Response(
            200,
            content=binary_data,
            headers={"Content-Type": "application/octet-stream"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    content = await fetch_glossary_url("https://example.com/data.bin")
    assert content == binary_data


async def test_fetch_with_mock_transport_directly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify passing transport explicitly into fetch_glossary_url works completely offline."""

    async def fake_ssrf(target_url: str | None) -> SSRFCheckResult:
        return SSRFCheckResult(allowed=True, resolved_ip="93.184.216.34")

    monkeypatch.setattr(
        "omniscribe.plugins.glossary.http_fetch.is_ssrf_target", fake_ssrf
    )

    def transport_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"custom_mock_transport_payload",
            request=request,
        )

    transport = httpx.MockTransport(transport_handler)
    content = await fetch_glossary_url(
        "https://example.com/custom-transport.csv", transport=transport
    )
    assert content == b"custom_mock_transport_payload"


async def test_fetch_redirect_relative_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify redirect with a relative Location header is resolved correctly."""

    async def fake_ssrf(target_url: str | None) -> SSRFCheckResult:
        return SSRFCheckResult(allowed=True, resolved_ip="93.184.216.34")

    monkeypatch.setattr(
        "omniscribe.plugins.glossary.http_fetch.is_ssrf_target", fake_ssrf
    )

    visited: list[str] = []

    async def fake_get(
        self: httpx.AsyncClient, url: str, **kwargs: Any
    ) -> httpx.Response:
        visited.append(url)
        if url == "https://example.com/base/index.html":
            return httpx.Response(
                302,
                headers={"Location": "terms.csv"},
                request=httpx.Request("GET", url),
            )
        return httpx.Response(
            200,
            content=b"relative_target_ok",
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    result = await fetch_glossary_url("https://example.com/base/index.html")
    assert result == b"relative_target_ok"
    assert visited == [
        "https://example.com/base/index.html",
        "https://example.com/base/terms.csv",
    ]


async def test_fetch_redirect_hop_blocked_by_ssrf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify SSRF protection catches blocked destinations introduced on redirect hops."""
    monkeypatch.setenv("ALLOW_SSRF_LOCAL", "false")

    async def fake_ssrf(target_url: str | None) -> SSRFCheckResult:
        if target_url == "https://example.com/redirect-to-private":
            return SSRFCheckResult(allowed=True, resolved_ip="93.184.216.34")
        return SSRFCheckResult(
            allowed=False, resolved_ip=None, reason="literal-blocked-ip"
        )

    monkeypatch.setattr(
        "omniscribe.plugins.glossary.http_fetch.is_ssrf_target", fake_ssrf
    )

    async def fake_get(
        self: httpx.AsyncClient, url: str, **kwargs: Any
    ) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"Location": "http://127.0.0.1/secret.csv"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    with pytest.raises(GlossaryError) as exc_info:
        await fetch_glossary_url("https://example.com/redirect-to-private")
    assert exc_info.value.status_code == 403
    assert exc_info.value.error == "ssrf_blocked"
