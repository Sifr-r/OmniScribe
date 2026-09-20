"""Regression tests: upstream HTTP 402 must surface as LLMBalanceError.

A cloud provider that runs out of credits answers every completion call
with HTTP 402 (e.g. MiniMax "insufficient balance (1008)"). The error
must keep a dedicated type through the retry and circuit-breaker layers
(instead of being re-wrapped as a generic LLMCallError) so the API layer
can map it to a 402 ``payment_required`` envelope and the UI can show
the real cause instead of "circuit breaker is open".
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from omniscribe.core.llm.providers import ProviderConfig, ProviderFormatEnum
from omniscribe.core.ocr.chat_client import ChatClient
from omniscribe.core.ocr.exceptions import LLMBalanceError, LLMCallError
from omniscribe.core.ocr.multi_format_client import complete_vlm_prompt
from omniscribe.core.ocr.resilience import CircuitBreaker, is_transient_error


def _openai_provider() -> ProviderConfig:
    return ProviderConfig(
        id="openai-test",
        display_name="OpenAI Test",
        format=ProviderFormatEnum.OPENAI_COMPATIBLE,
        api_url="http://localhost:1234/v1",
        api_key="test-key",
        models=["test-model"],
    )


def _mock_client_returning_status(status_code: int, text: str) -> AsyncMock:
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.text = text
    client = AsyncMock()
    client.post.return_value = mock_response
    return client


async def test_http_402_raises_llm_balance_error() -> None:
    mock_client = _mock_client_returning_status(
        402,
        '{"error":{"type":"insufficient_balance_error",'
        '"message":"insufficient balance (1008)"}}',
    )
    with patch(
        "omniscribe.core.ocr.multi_format_client._get_shared_client",
        return_value=mock_client,
    ):
        with pytest.raises(LLMBalanceError) as exc_info:
            await complete_vlm_prompt(_openai_provider(), prompt="hello", max_retries=0)
    assert "402" in str(exc_info.value)
    assert "insufficient balance" in str(exc_info.value)


def test_llm_balance_error_is_llm_call_error_subclass() -> None:
    assert issubclass(LLMBalanceError, LLMCallError)


def test_llm_balance_error_is_not_transient() -> None:
    assert not is_transient_error(
        LLMBalanceError("Provider returned HTTP status 402: insufficient balance")
    )


async def test_chat_client_preserves_balance_error_identity() -> None:
    client = ChatClient(
        model="test-model",
        api_base="http://localhost:1234/v1",
        api_key="test-key",
        max_retries=2,
        retry_base_delay_s=0.0,
        retry_max_delay_s=0.0,
        circuit_breaker=CircuitBreaker(failure_threshold=5, cooldown_seconds=0.0),
    )
    with patch(
        "omniscribe.core.ocr.chat_client.call_llm",
        new=AsyncMock(side_effect=LLMBalanceError("Provider returned HTTP status 402")),
    ):
        with pytest.raises(LLMBalanceError):
            await client.chat("prompt", "aW1n", timeout=5.0, max_tokens=8)
