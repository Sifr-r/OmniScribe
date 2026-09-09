"""Unit tests for the centralized LLM client module (``omniscribe.core.llm.client``).

Tests cover:
- ``_resolve_provider_config`` resolution rules (explicit provider_config, fallback custom OpenAI config, missing config error).
- ``_extract_prompt_and_image`` parsing (direct args, user strings, multi-message strings, structured content blocks, data URLs, system-role dropping).
- ``call_vlm`` invocation and parameter forwarding to ``complete_vlm_prompt``.
- ``call_llm`` invocation and parameter forwarding to ``complete_vlm_prompt``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from omniscribe.core.llm.client import (
    _extract_prompt_and_image,
    _resolve_provider_config,
    call_llm,
    call_vlm,
)
from omniscribe.core.llm.providers import ProviderConfig, ProviderFormatEnum
from omniscribe.core.ocr.exceptions import LLMCallError


class TestResolveProviderConfig:
    """Tests for ``_resolve_provider_config``."""

    def test_explicit_provider_config_is_returned_directly(self) -> None:
        cfg = ProviderConfig(
            id="ollama-local",
            display_name="Ollama Local",
            format=ProviderFormatEnum.OLLAMA_COMPATIBLE,
            api_url="http://localhost:11434",
            models=["llama3.2-vision"],
        )
        resolved = _resolve_provider_config(
            provider_config=cfg,
            api_base="http://localhost:1234/v1",  # should be ignored
            api_key="ignored-key",
            model="ignored-model",
        )
        assert resolved is cfg
        assert resolved.id == "ollama-local"
        assert resolved.format == ProviderFormatEnum.OLLAMA_COMPATIBLE

    def test_api_base_builds_inferred_lmstudio_config(self) -> None:
        resolved = _resolve_provider_config(
            provider_config=None,
            api_base="http://localhost:1234/v1",
            api_key="secret-key",
            model="qwen2.5-vl-7b",
        )
        assert resolved.id == "lmstudio"
        assert resolved.display_name == "LM Studio"
        assert resolved.format == ProviderFormatEnum.OPENAI_COMPATIBLE
        assert resolved.api_url == "http://localhost:1234/v1"
        assert resolved.api_key == "secret-key"
        assert resolved.models == ["qwen2.5-vl-7b"]

    @pytest.mark.parametrize(
        "api_base,expected_id,expected_name,expected_fmt",
        [
            (
                "http://localhost:1234/v1",
                "lmstudio",
                "LM Studio",
                ProviderFormatEnum.OPENAI_COMPATIBLE,
            ),
            (
                "http://localhost:11434",
                "ollama",
                "Ollama",
                ProviderFormatEnum.OLLAMA_COMPATIBLE,
            ),
            (
                "https://api.anthropic.com/v1",
                "anthropic",
                "Anthropic",
                ProviderFormatEnum.ANTHROPIC_COMPATIBLE,
            ),
            (
                "https://api.openai.com/v1",
                "openai",
                "OpenAI",
                ProviderFormatEnum.OPENAI_COMPATIBLE,
            ),
            (
                "https://openrouter.ai/api/v1",
                "openrouter",
                "OpenRouter",
                ProviderFormatEnum.OPENAI_COMPATIBLE,
            ),
            (
                "https://api.groq.com/openai/v1",
                "groq",
                "Groq",
                ProviderFormatEnum.OPENAI_COMPATIBLE,
            ),
            (
                "https://api.deepseek.com",
                "deepseek",
                "DeepSeek",
                ProviderFormatEnum.OPENAI_COMPATIBLE,
            ),
            (
                "http://10.0.0.1:8000/v1",
                "custom",
                "Custom",
                ProviderFormatEnum.OPENAI_COMPATIBLE,
            ),
        ],
    )
    def test_api_base_inference(
        self,
        api_base: str,
        expected_id: str,
        expected_name: str,
        expected_fmt: ProviderFormatEnum,
    ) -> None:
        resolved = _resolve_provider_config(
            provider_config=None,
            api_base=api_base,
            api_key="test-key",
            model="test-model",
        )
        assert resolved.id == expected_id
        assert resolved.display_name == expected_name
        assert resolved.format == expected_fmt
        assert resolved.api_url == api_base

    def test_api_base_without_model_produces_empty_models_list(self) -> None:
        resolved = _resolve_provider_config(
            provider_config=None,
            api_base="http://localhost:1234/v1",
            api_key=None,
            model=None,
        )
        assert resolved.models == []
        assert resolved.api_key is None

    def test_missing_provider_config_and_api_base_raises_llm_call_error(self) -> None:
        with pytest.raises(
            LLMCallError, match="requires either `provider_config` or `api_base`"
        ):
            _resolve_provider_config(
                provider_config=None,
                api_base=None,
                api_key=None,
                model=None,
            )

    def test_empty_string_api_base_raises_llm_call_error(self) -> None:
        with pytest.raises(
            LLMCallError, match="requires either `provider_config` or `api_base`"
        ):
            _resolve_provider_config(
                provider_config=None,
                api_base="",
                api_key=None,
                model=None,
            )


class TestExtractPromptAndImage:
    """Tests for ``_extract_prompt_and_image``."""

    def test_direct_prompt_and_image(self) -> None:
        p, img = _extract_prompt_and_image(
            messages=None,
            prompt="Transcribe this text",
            image_base64="aW1hZ2VkYXRh",
        )
        assert p == "Transcribe this text"
        assert img == "aW1hZ2VkYXRh"

    def test_defaults_when_empty(self) -> None:
        p, img = _extract_prompt_and_image(messages=None)
        assert p == ""
        assert img is None

    def test_single_user_message_string_content(self) -> None:
        messages = [{"role": "user", "content": "Extract page layout"}]
        p, img = _extract_prompt_and_image(messages=messages)
        assert p == "Extract page layout"
        assert img is None

    def test_multiple_user_messages_joined_with_newlines(self) -> None:
        messages = [
            {"role": "user", "content": "First instruction."},
            {"role": "user", "content": "Second instruction."},
        ]
        p, img = _extract_prompt_and_image(messages=messages)
        assert p == "First instruction.\nSecond instruction."
        assert img is None

    def test_system_role_is_dropped(self) -> None:
        messages = [
            {"role": "system", "content": "You are a helpful OCR assistant."},
            {"role": "user", "content": "User question here."},
        ]
        p, img = _extract_prompt_and_image(messages=messages)
        assert p == "User question here."
        assert "helpful OCR assistant" not in p
        assert img is None

    def test_content_list_with_strings(self) -> None:
        messages = [
            {"role": "user", "content": ["Line 1", "Line 2"]},
        ]
        p, img = _extract_prompt_and_image(messages=messages)
        assert p == "Line 1\nLine 2"
        assert img is None

    def test_content_list_with_text_dicts(self) -> None:
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Part A"},
                    {"type": "text", "text": "Part B"},
                ],
            }
        ]
        p, _ = _extract_prompt_and_image(messages=messages)
        assert p == "Part A\nPart B"

    def test_image_url_with_data_prefix(self) -> None:
        data_url = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Read text from image"},
                    {"type": "image_url", "image_url": data_url},
                ],
            }
        ]
        p, img = _extract_prompt_and_image(messages=messages)
        assert p == "Read text from image"
        assert img == "iVBORw0KGgoAAAANSUhEUg=="

    def test_image_url_with_raw_string(self) -> None:
        raw_b64 = "rawbase64encodedbytes=="
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": raw_b64},
                ],
            }
        ]
        p, img = _extract_prompt_and_image(messages=messages)
        assert p == ""
        assert img == raw_b64

    def test_image_url_with_dict_url(self) -> None:
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQ=="
                        },
                    }
                ],
            }
        ]
        _, img = _extract_prompt_and_image(messages=messages)
        assert img == "/9j/4AAQSkZJRgABAQ=="

    def test_image_type_source_dict(self) -> None:
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"data": "anthropic_source_b64=="},
                    }
                ],
            }
        ]
        _, img = _extract_prompt_and_image(messages=messages)
        assert img == "anthropic_source_b64=="

    def test_explicit_prompt_takes_precedence_over_messages(self) -> None:
        messages = [{"role": "user", "content": "from message"}]
        p, _ = _extract_prompt_and_image(messages=messages, prompt="explicit prompt")
        assert p == "explicit prompt"

    def test_explicit_image_takes_precedence_over_messages(self) -> None:
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": "data:image/png;base64,from_msg"}
                ],
            }
        ]
        _, img = _extract_prompt_and_image(
            messages=messages, image_base64="from_explicit"
        )
        assert img == "from_explicit"


class TestCallVlm:
    """Tests for ``call_vlm``."""

    async def test_call_vlm_dispatches_with_resolved_config(self) -> None:
        with patch(
            "omniscribe.core.llm.client.complete_vlm_prompt",
            new_callable=AsyncMock,
            return_value="OCR result text",
        ) as mock_complete:
            result = await call_vlm(
                prompt="Read this crop",
                image_base64="b64data",
                api_base="http://localhost:1234/v1",
                api_key="key-123",
                model="qwen-vl",
                temperature=0.0,
                max_tokens=2048,
                timeout=30.0,
                system_prompt="OCR system instructions",
            )

            assert result == "OCR result text"
            mock_complete.assert_awaited_once()
            call_kwargs = mock_complete.await_args.kwargs  # type: ignore[union-attr]
            assert call_kwargs["prompt"] == "Read this crop"
            assert call_kwargs["image_base64"] == "b64data"
            assert call_kwargs["model"] == "qwen-vl"
            assert call_kwargs["temperature"] == 0.0
            assert call_kwargs["max_tokens"] == 2048
            assert call_kwargs["timeout"] == 30.0
            assert call_kwargs["system_prompt"] == "OCR system instructions"

            cfg = call_kwargs["provider_config"]
            assert isinstance(cfg, ProviderConfig)
            assert cfg.api_url == "http://localhost:1234/v1"
            assert cfg.api_key == "key-123"

    async def test_call_vlm_with_provider_config(self) -> None:
        cfg = ProviderConfig(
            id="anthropic-main",
            display_name="Anthropic",
            format=ProviderFormatEnum.ANTHROPIC_COMPATIBLE,
            api_url="https://api.anthropic.com",
            api_key="sk-ant-test",
            models=["claude-3-5-sonnet"],
        )
        with patch(
            "omniscribe.core.llm.client.complete_vlm_prompt",
            new_callable=AsyncMock,
            return_value="Transcribed document",
        ) as mock_complete:
            result = await call_vlm(
                prompt="Transcribe page",
                provider_config=cfg,
                model="claude-3-5-sonnet",
            )
            assert result == "Transcribed document"
            call_kwargs = mock_complete.await_args.kwargs  # type: ignore[union-attr]
            assert call_kwargs["provider_config"] is cfg

    async def test_call_vlm_without_config_or_api_base_raises(self) -> None:
        with pytest.raises(
            LLMCallError, match="requires either `provider_config` or `api_base`"
        ):
            await call_vlm(prompt="Hello")


class TestCallLlm:
    """Tests for ``call_llm``."""

    async def test_call_llm_dispatches_with_messages_extraction(self) -> None:
        with patch(
            "omniscribe.core.llm.client.complete_vlm_prompt",
            new_callable=AsyncMock,
            return_value="Chat response text",
        ) as mock_complete:
            messages = [
                {"role": "system", "content": "Ignored system message"},
                {"role": "user", "content": "Translate this sentence"},
            ]
            result = await call_llm(
                messages=messages,
                api_base="http://localhost:1234/v1",
                model="llama-3.1-8b",
                temperature=0.1,
                system_prompt="Explicit system prompt",
            )

            assert result == "Chat response text"
            mock_complete.assert_awaited_once()
            call_kwargs = mock_complete.await_args.kwargs  # type: ignore[union-attr]
            assert call_kwargs["prompt"] == "Translate this sentence"
            assert call_kwargs["image_base64"] is None
            assert call_kwargs["model"] == "llama-3.1-8b"
            assert call_kwargs["temperature"] == 0.1
            assert call_kwargs["max_tokens"] == 4096  # default fallback
            assert call_kwargs["system_prompt"] == "Explicit system prompt"

    async def test_call_llm_with_explicit_max_tokens_and_image(self) -> None:
        with patch(
            "omniscribe.core.llm.client.complete_vlm_prompt",
            new_callable=AsyncMock,
            return_value="Vision response",
        ) as mock_complete:
            result = await call_llm(
                prompt="Describe image",
                image_base64="img123",
                api_base="http://localhost:1234/v1",
                max_tokens=1024,
                timeout=15.0,
            )

            assert result == "Vision response"
            call_kwargs = mock_complete.await_args.kwargs  # type: ignore[union-attr]
            assert call_kwargs["prompt"] == "Describe image"
            assert call_kwargs["image_base64"] == "img123"
            assert call_kwargs["max_tokens"] == 1024
            assert call_kwargs["timeout"] == 15.0

    async def test_call_llm_with_provider_config(self) -> None:
        cfg = ProviderConfig(
            id="ollama-local",
            display_name="Ollama",
            format=ProviderFormatEnum.OLLAMA_COMPATIBLE,
            api_url="http://localhost:11434",
            models=["llama3.2-vision"],
        )
        with patch(
            "omniscribe.core.llm.client.complete_vlm_prompt",
            new_callable=AsyncMock,
            return_value="Local response",
        ) as mock_complete:
            result = await call_llm(
                prompt="Test prompt",
                provider_config=cfg,
            )
            assert result == "Local response"
            call_kwargs = mock_complete.await_args.kwargs  # type: ignore[union-attr]
            assert call_kwargs["provider_config"] is cfg

    async def test_call_llm_without_config_or_api_base_raises(self) -> None:
        with pytest.raises(
            LLMCallError, match="requires either `provider_config` or `api_base`"
        ):
            await call_llm(prompt="Hello")
