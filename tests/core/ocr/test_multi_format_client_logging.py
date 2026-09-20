"""Regression tests for C1: silent empty-string returns on malformed upstream responses.

The audit found that all three provider branches (OpenAI / Anthropic / Ollama) silently
returned ``""`` when an HTTP 200 came back with an unexpected JSON shape. This module
locks down the fix: each branch must log a WARNING that names the provider and the
missing key before returning empty.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from omniscribe.core.llm.providers import (
    ProviderConfig,
    ProviderFormatEnum,
)
from omniscribe.core.ocr.multi_format_client import complete_vlm_prompt


def _openai_provider() -> ProviderConfig:
    return ProviderConfig(
        id="openai-test",
        display_name="OpenAI Test",
        format=ProviderFormatEnum.OPENAI_COMPATIBLE,
        api_url="http://localhost:1234/v1",
        api_key="test-key",
        models=["test-model"],
    )


def _anthropic_provider() -> ProviderConfig:
    return ProviderConfig(
        id="anthropic-test",
        display_name="Anthropic Test",
        format=ProviderFormatEnum.ANTHROPIC_COMPATIBLE,
        api_url="http://localhost:8080",
        api_key="test-key",
        models=["test-model"],
    )


def _ollama_provider() -> ProviderConfig:
    return ProviderConfig(
        id="ollama-test",
        display_name="Ollama Test",
        format=ProviderFormatEnum.OLLAMA_COMPATIBLE,
        api_url="http://localhost:11434",
        api_key="",
        models=["test-model"],
    )


def _mock_client_returning(data: Any) -> AsyncMock:
    """Build an AsyncMock for the shared httpx client that returns ``data``."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = data
    client = AsyncMock()
    client.post.return_value = mock_response
    return client


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"choices": []},
    ],
)
async def test_C1_openai_logs_warning_on_malformed_response(
    data: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    """C1 audit fix: missing choices must log a WARNING naming the provider.

    Cases covered:
    - ``{}`` (no top-level ``choices`` key)
    - ``{"choices": []}`` (empty choices list)

    Cases intentionally excluded (legitimate payloads, not malformed):
    - ``{"choices": [{"message": {"content": ""}}]}`` — empty content string is valid
    - ``{"choices": [{}]}`` — empty message dict is ambiguous (default fallback vs provided)
    """
    mock_client = _mock_client_returning(data)

    with (
        patch(
            "omniscribe.core.ocr.multi_format_client._get_shared_client",
            return_value=mock_client,
        ),
        caplog.at_level(
            logging.WARNING, logger="omniscribe.core.ocr.multi_format_client"
        ),
    ):
        result = await complete_vlm_prompt(
            _openai_provider(),
            prompt="hello",
            max_retries=0,
        )

    assert result == ""
    assert any("openai-test" in rec.message for rec in caplog.records), (
        f"Expected WARNING referencing provider id 'openai-test', "
        f"got: {[r.message for r in caplog.records]}"
    )
    assert any(
        "missing" in rec.message.lower() or "malformed" in rec.message.lower()
        for rec in caplog.records
    ), (
        f"Expected WARNING with 'missing' or 'malformed', "
        f"got: {[r.message for r in caplog.records]}"
    )


async def test_C1_anthropic_logs_warning_on_malformed_response(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """C1 audit fix: missing/malformed content list must log a WARNING."""
    mock_client = _mock_client_returning({"content": "not-a-list"})

    with (
        patch(
            "omniscribe.core.ocr.multi_format_client._get_shared_client",
            return_value=mock_client,
        ),
        caplog.at_level(
            logging.WARNING, logger="omniscribe.core.ocr.multi_format_client"
        ),
    ):
        result = await complete_vlm_prompt(
            _anthropic_provider(), prompt="hello", max_retries=0
        )

    assert result == ""
    assert any("anthropic-test" in rec.message for rec in caplog.records), (
        f"Expected WARNING referencing 'anthropic-test', "
        f"got: {[r.message for r in caplog.records]}"
    )


async def test_C1_ollama_logs_warning_on_malformed_response(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """C1 audit fix: missing/malformed message must log a WARNING."""
    mock_client = _mock_client_returning({"message": "not-a-dict"})

    with (
        patch(
            "omniscribe.core.ocr.multi_format_client._get_shared_client",
            return_value=mock_client,
        ),
        caplog.at_level(
            logging.WARNING, logger="omniscribe.core.ocr.multi_format_client"
        ),
    ):
        result = await complete_vlm_prompt(
            _ollama_provider(), prompt="hello", max_retries=0
        )

    assert result == ""
    assert any("ollama-test" in rec.message for rec in caplog.records), (
        f"Expected WARNING referencing 'ollama-test', "
        f"got: {[r.message for r in caplog.records]}"
    )


def test_get_shared_client_cross_loop_closes_old_client() -> None:
    import asyncio

    from omniscribe.core.ocr import multi_format_client

    loop1 = asyncio.new_event_loop()
    loop2 = asyncio.new_event_loop()
    try:

        async def _get() -> Any:
            return multi_format_client._get_shared_client()

        client1 = loop1.run_until_complete(_get())
        assert multi_format_client._shared_client is client1
        assert multi_format_client._shared_client_loop is loop1

        with patch.object(multi_format_client, "_safe_close_client") as mock_close:
            client2 = loop2.run_until_complete(_get())
            assert client2 is not client1
            mock_close.assert_called_once_with(client1, loop1)
    finally:
        loop1.close()
        loop2.close()
        multi_format_client._shared_client = None
        multi_format_client._shared_client_loop = None
