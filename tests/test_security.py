"""Tests for security utilities, including fail-closed URL origin checks."""

from __future__ import annotations

import pytest

from omniscribe.utils.security import is_same_origin


def test_is_same_origin_valid_matches() -> None:
    assert is_same_origin("http://localhost:8000/v1", "http://localhost:8000/v2")
    assert is_same_origin("http://example.com", "http://example.com:80")
    assert is_same_origin("https://example.com:443", "https://example.com")
    assert is_same_origin("https://api.openai.com/v1", "https://api.openai.com:443")


def test_is_same_origin_valid_mismatches() -> None:
    assert not is_same_origin("http://example.com", "https://example.com")
    assert not is_same_origin("http://example.com:80", "http://example.com:8080")
    assert not is_same_origin("http://example.com", "http://attacker.com")
    assert not is_same_origin("", "http://example.com")
    assert not is_same_origin(None, "http://example.com")
    assert not is_same_origin("http://example.com", None)


@pytest.mark.parametrize(
    "malformed_url",
    [
        "http://example.com:bad",
        "http://example.com:99999",
        "http://example.com:-80",
        "http://[invalid-ipv6",
        "http://:8080",
        "http://example.com:0x10",
        "http://example.com:port",
    ],
)
def test_is_same_origin_malformed_ports_and_hosts_fail_closed(
    malformed_url: str,
) -> None:
    """[P2-16] Malformed ports or hosts must return False instead of raising ValueError / crashing."""
    assert not is_same_origin(malformed_url, "http://example.com:80")
    assert not is_same_origin("http://example.com:80", malformed_url)
    assert not is_same_origin(malformed_url, malformed_url)


def test_create_pinned_client_initialization() -> None:
    import httpx

    from omniscribe.utils.security import _PinnedIPTransport, create_pinned_client

    client = create_pinned_client("https://api.openai.com/v1", "93.184.216.34", timeout=45.0)
    assert isinstance(client, httpx.AsyncClient)
    assert client.timeout.read == 45.0
    assert isinstance(client._transport, _PinnedIPTransport)
    pool = getattr(client._transport, "_pool", None)
    assert pool is not None
    backend = getattr(pool, "_network_backend", None)
    assert backend is not None
    assert backend._target_host == "api.openai.com"
    assert backend._resolved_ip == "93.184.216.34"


async def test_pinned_network_backend_redirects_matching_host() -> None:
    from unittest.mock import AsyncMock

    from omniscribe.utils.security import _PinnedNetworkBackend

    backend = _PinnedNetworkBackend("api.openai.com", "93.184.216.34")
    mock_connect = AsyncMock()
    setattr(backend._backend, "connect_tcp", mock_connect)

    await backend.connect_tcp("api.openai.com", 443)
    mock_connect.assert_called_once_with(
        "93.184.216.34",
        443,
        timeout=None,
        local_address=None,
        socket_options=None,
    )

    mock_connect.reset_mock()
    await backend.connect_tcp("other.example.com", 80)
    mock_connect.assert_called_once_with(
        "other.example.com",
        80,
        timeout=None,
        local_address=None,
        socket_options=None,
    )


async def test_call_llm_uses_provided_http_client() -> None:
    from unittest.mock import AsyncMock, MagicMock

    import httpx

    from omniscribe.core.llm.client import call_llm

    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "pinned response"}}]
    }
    mock_client.post = AsyncMock(return_value=mock_resp)

    res = await call_llm(
        api_base="https://api.openai.com/v1",
        api_key="test-key",
        model="gpt-4o",
        prompt="hello",
        http_client=mock_client,
    )
    assert res == "pinned response"
    mock_client.post.assert_awaited_once()
    assert mock_client.post.call_args[0][0] == "https://api.openai.com/v1/chat/completions"


async def test_run_extraction_uses_pinned_client_when_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    import omniscribe.plugins.documents.service as doc_service
    from omniscribe.config import RuntimeSettings
    from omniscribe.plugins.documents.schemas import (
        ExtractionRequest,
        ExtractionTemplate,
    )
    from omniscribe.plugins.documents.service import run_extraction
    from omniscribe.utils.security import SSRFCheckResult

    created_clients = []
    original_create_pinned = doc_service.create_pinned_client

    def fake_create_pinned(url: str, resolved_ip: str, timeout: float = 60.0):
        c = original_create_pinned(url, resolved_ip, timeout)
        created_clients.append((c, url, resolved_ip))
        return c

    monkeypatch.setattr(doc_service, "create_pinned_client", fake_create_pinned)
    monkeypatch.setattr(
        doc_service,
        "check_ssrf_target_sync",
        lambda url: SSRFCheckResult(allowed=True, resolved_ip="93.184.216.34"),
    )

    async def fake_call_llm(**kwargs):
        assert kwargs.get("http_client") is not None
        assert kwargs["http_client"] is created_clients[0][0]
        return '{"result": "extracted"}'

    monkeypatch.setattr(doc_service, "call_llm", fake_call_llm)

    req = ExtractionRequest(
        text="Sample document text",
        template=ExtractionTemplate.INVOICE,
        api_base="https://api.openai.com/v1",
    )
    settings = RuntimeSettings(
        llm_model="test-model",
        llm_api_base="https://api.openai.com/v1",
        llm_api_key="key",
    )
    res = await run_extraction(req, settings)
    assert res == {"result": "extracted"}
    assert len(created_clients) == 1
    assert created_clients[0][1] == "https://api.openai.com/v1"
    assert created_clients[0][2] == "93.184.216.34"


async def test_translate_text_uses_pinned_client_when_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    import omniscribe.plugins.translate.service as trans_service
    from omniscribe.config import RuntimeSettings
    from omniscribe.plugins.translate.schemas import TranslationRequest
    from omniscribe.utils.security import SSRFCheckResult

    trans_service._translation_cache.clear()
    created_clients = []
    original_create_pinned = trans_service.create_pinned_client

    def fake_create_pinned(url: str, resolved_ip: str, timeout: float = 60.0):
        c = original_create_pinned(url, resolved_ip, timeout)
        created_clients.append((c, url, resolved_ip))
        return c

    monkeypatch.setattr(trans_service, "create_pinned_client", fake_create_pinned)
    monkeypatch.setattr(
        trans_service,
        "check_ssrf_target_sync",
        lambda url: SSRFCheckResult(allowed=True, resolved_ip="93.184.216.34"),
    )

    async def fake_call_llm(**kwargs):
        assert kwargs.get("http_client") is not None
        assert kwargs["http_client"] is created_clients[0][0]
        return "Bonjour"

    monkeypatch.setattr(trans_service, "call_llm", fake_call_llm)

    req = TranslationRequest(
        text="Hello world",
        target_language="French",
        api_base="https://api.openai.com/v1",
    )
    settings = RuntimeSettings(
        llm_model="test-model",
        llm_api_base="https://api.openai.com/v1",
        llm_api_key="key",
    )
    res = await trans_service.translate_text(req, settings)
    assert res == "Bonjour"
    assert len(created_clients) == 1
    assert created_clients[0][1] == "https://api.openai.com/v1"
    assert created_clients[0][2] == "93.184.216.34"

