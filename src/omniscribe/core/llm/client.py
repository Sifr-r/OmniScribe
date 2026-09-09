"""Async LLM completion client for OmniScribe.

Directs call_llm / call_vlm to use the active provider configuration from ProviderManager
and multi_format_client for completion dispatch across OpenAI, Anthropic, and Ollama formats.
"""

from __future__ import annotations

import logging
from typing import Any

from omniscribe.core.llm.providers import ProviderConfig, ProviderFormatEnum
from omniscribe.core.ocr.exceptions import LLMCallError
from omniscribe.core.ocr.multi_format_client import complete_vlm_prompt

logger = logging.getLogger(__name__)


def _resolve_provider_config(
    provider_config: ProviderConfig | None,
    api_base: str | None,
    api_key: str | None,
    model: str | None,
) -> ProviderConfig:
    """Build a ``ProviderConfig`` for the in-process LLM call.

    The API layer is responsible for resolving the active provider via
    ``ProviderManager`` before calling this module. Core never reaches
    upward into ``omniscribe.api`` to look up provider state.

    If the caller passes an explicit ``api_base`` we construct a
    one-shot OPENAI_COMPATIBLE config so the OCR pipeline can run
    end-to-end without touching the API layer (tests, embedded
    workflows, CLI use).

    If neither is provided we fail fast with ``LLMCallError`` — the
    caller must pass ``provider_config`` or ``api_base``.
    """
    if provider_config is not None:
        return provider_config
    if api_base:
        base = api_base.lower()
        if ":1234" in base:
            p_id = "lmstudio"
            p_name = "LM Studio"
            p_format = ProviderFormatEnum.OPENAI_COMPATIBLE
        elif ":11434" in base:
            p_id = "ollama"
            p_name = "Ollama"
            p_format = ProviderFormatEnum.OLLAMA_COMPATIBLE
        elif "anthropic.com" in base:
            p_id = "anthropic"
            p_name = "Anthropic"
            p_format = ProviderFormatEnum.ANTHROPIC_COMPATIBLE
        elif "openai.com" in base:
            p_id = "openai"
            p_name = "OpenAI"
            p_format = ProviderFormatEnum.OPENAI_COMPATIBLE
        elif "openrouter.ai" in base:
            p_id = "openrouter"
            p_name = "OpenRouter"
            p_format = ProviderFormatEnum.OPENAI_COMPATIBLE
        elif "groq.com" in base:
            p_id = "groq"
            p_name = "Groq"
            p_format = ProviderFormatEnum.OPENAI_COMPATIBLE
        elif "deepseek.com" in base:
            p_id = "deepseek"
            p_name = "DeepSeek"
            p_format = ProviderFormatEnum.OPENAI_COMPATIBLE
        else:
            p_id = "custom"
            p_name = "Custom"
            p_format = ProviderFormatEnum.OPENAI_COMPATIBLE

        return ProviderConfig(
            id=p_id,
            display_name=p_name,
            format=p_format,
            api_url=api_base,
            api_key=api_key,
            models=[model] if model else [],
        )
    raise LLMCallError(
        "call_llm / call_vlm requires either `provider_config` or `api_base`. "
        "Resolve the active provider at the API layer via ProviderManager "
        "before calling the core OCR pipeline."
    )


def _extract_prompt_and_image(
    messages: list[dict[str, Any]] | None,
    prompt: str | None = None,
    image_base64: str | None = None,
) -> tuple[str, str | None]:
    """Parse messages payload or direct args into ``(text_prompt, image_base64)``.

    Only user-role entries contribute to the returned prompt and image;
    system-role entries (if any) are silently dropped — the explicit
    ``system_prompt`` parameter on :func:`call_llm` is the only
    supported way to attach a system message. Centralizing the
    system role there means a single parameter is the source of
    truth, instead of having to reason about every possible
    messages-list shape the caller might construct.
    """
    extracted_prompt = prompt or ""
    extracted_image = image_base64

    if messages:
        p_parts: list[str] = []
        for msg in messages:
            if msg.get("role") == "system":
                # Drop system entries — use the ``system_prompt``
                # parameter on call_llm / call_vlm instead.
                continue
            content = msg.get("content")
            if isinstance(content, str):
                p_parts.append(content)
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, str):
                        p_parts.append(item)
                    elif isinstance(item, dict):
                        item_type = item.get("type")
                        if item_type == "text":
                            text_val = item.get("text")
                            if text_val:
                                p_parts.append(str(text_val))
                        elif item_type == "image_url":
                            img_obj = item.get("image_url")
                            url_str = ""
                            if isinstance(img_obj, str):
                                url_str = img_obj
                            elif isinstance(img_obj, dict):
                                url_str = str(img_obj.get("url", ""))
                            if url_str and extracted_image is None:
                                if "base64," in url_str:
                                    extracted_image = url_str.split("base64,", 1)[1]
                                else:
                                    extracted_image = url_str
                        elif item_type == "image":
                            src = item.get("source", {})
                            if isinstance(src, dict) and extracted_image is None:
                                extracted_image = str(src.get("data", ""))
        if p_parts and not extracted_prompt:
            extracted_prompt = "\n".join(p_parts)

    return extracted_prompt, extracted_image


async def call_vlm(
    prompt: str,
    image_base64: str | None = None,
    *,
    model: str | None = None,
    api_base: str | None = None,
    api_key: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 4096,
    timeout: float | None = None,
    provider_config: ProviderConfig | None = None,
    system_prompt: str | None = None,
) -> str:
    """Make an asynchronous VLM call using active ProviderManager configuration or explicit settings."""
    provider_config = _resolve_provider_config(
        provider_config, api_base, api_key, model
    )

    return await complete_vlm_prompt(
        provider_config=provider_config,
        prompt=prompt,
        image_base64=image_base64,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        system_prompt=system_prompt,
    )


async def call_llm(
    *,
    model: str | None = None,
    api_base: str | None = None,
    api_key: str | None = None,
    messages: list[dict[str, Any]] | None = None,
    prompt: str | None = None,
    image_base64: str | None = None,
    temperature: float = 0.1,
    max_tokens: int | None = None,
    timeout: float | None = None,
    provider_config: ProviderConfig | None = None,
    system_prompt: str | None = None,
) -> str:
    """Make an asynchronous LLM chat completion call using active ProviderManager config.

    The system role is set exclusively through the ``system_prompt``
    parameter — system entries inside ``messages`` are dropped by
    :func:`_extract_prompt_and_image`. OlmOCR-2's RL-trained prompt
    string stays a pure user message, so most callers leave
    ``system_prompt=None`` and pass the prompt as user content.
    """
    extracted_prompt, extracted_image = _extract_prompt_and_image(
        messages=messages,
        prompt=prompt,
        image_base64=image_base64,
    )

    provider_config = _resolve_provider_config(
        provider_config, api_base, api_key, model
    )

    return await complete_vlm_prompt(
        provider_config=provider_config,
        prompt=extracted_prompt,
        image_base64=extracted_image,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens or 4096,
        timeout=timeout,
        system_prompt=system_prompt,
    )


__all__ = [
    "call_llm",
    "call_vlm",
]
