"""Async LLM completion client for OmniScribe.

Directs call_llm / call_vlm to use the active provider configuration from ProviderManager
and multi_format_client for completion dispatch across OpenAI, Anthropic, and Ollama formats.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit

import httpx

from omniscribe.core.llm.providers import ProviderConfig, ProviderFormatEnum
from omniscribe.core.ocr.exceptions import LLMCallError
from omniscribe.core.ocr.multi_format_client import complete_vlm_prompt

logger = logging.getLogger(__name__)


_EXPLICIT_PROVIDERS: dict[str, tuple[str, str, ProviderFormatEnum, str]] = {
    "lmstudio": (
        "lmstudio",
        "LM Studio",
        ProviderFormatEnum.OPENAI_COMPATIBLE,
        "http://localhost:1234/v1",
    ),
    "ollama": (
        "ollama",
        "Ollama",
        ProviderFormatEnum.OLLAMA_COMPATIBLE,
        "http://localhost:11434",
    ),
    "anthropic": (
        "anthropic",
        "Anthropic",
        ProviderFormatEnum.ANTHROPIC_COMPATIBLE,
        "https://api.anthropic.com",
    ),
    "openai": (
        "openai",
        "OpenAI",
        ProviderFormatEnum.OPENAI_COMPATIBLE,
        "https://api.openai.com/v1",
    ),
    "openrouter": (
        "openrouter",
        "OpenRouter",
        ProviderFormatEnum.OPENAI_COMPATIBLE,
        "https://openrouter.ai/api/v1",
    ),
    "groq": (
        "groq",
        "Groq",
        ProviderFormatEnum.OPENAI_COMPATIBLE,
        "https://api.groq.com/openai/v1",
    ),
    "deepseek": (
        "deepseek",
        "DeepSeek",
        ProviderFormatEnum.OPENAI_COMPATIBLE,
        "https://api.deepseek.com/v1",
    ),
    "custom": (
        "custom",
        "Custom",
        ProviderFormatEnum.OPENAI_COMPATIBLE,
        "",
    ),
    ProviderFormatEnum.OPENAI_COMPATIBLE.value: (
        "custom",
        "Custom",
        ProviderFormatEnum.OPENAI_COMPATIBLE,
        "",
    ),
    ProviderFormatEnum.ANTHROPIC_COMPATIBLE.value: (
        "anthropic",
        "Anthropic",
        ProviderFormatEnum.ANTHROPIC_COMPATIBLE,
        "https://api.anthropic.com",
    ),
    ProviderFormatEnum.OLLAMA_COMPATIBLE.value: (
        "ollama",
        "Ollama",
        ProviderFormatEnum.OLLAMA_COMPATIBLE,
        "http://localhost:11434",
    ),
}


# Explicit hostname -> provider lookup for ``_resolve_provider_config`` when the
# caller supplies an ``api_base`` URL without an explicit provider.
#
# SECURITY: This mapping MUST stay an exact-equality dict lookup. Do NOT
# replace it with ``endswith()``, ``in``, or regex matching. Substring
# matching on hostnames would let a host like ``anthropic.com.attacker.tld``
# or ``evil-anthropic.com`` be misclassified as the Anthropic provider,
# routing credentials and traffic to an attacker-controlled endpoint.
# Only the literal hostnames below are trusted; everything else falls
# through to the ``custom`` provider branch.
_PROVIDER_HOSTS: dict[str, tuple[str, str, ProviderFormatEnum]] = {
    "anthropic.com": (
        "anthropic",
        "Anthropic",
        ProviderFormatEnum.ANTHROPIC_COMPATIBLE,
    ),
    "api.anthropic.com": (
        "anthropic",
        "Anthropic",
        ProviderFormatEnum.ANTHROPIC_COMPATIBLE,
    ),
    "openai.com": ("openai", "OpenAI", ProviderFormatEnum.OPENAI_COMPATIBLE),
    "api.openai.com": ("openai", "OpenAI", ProviderFormatEnum.OPENAI_COMPATIBLE),
    "openrouter.ai": ("openrouter", "OpenRouter", ProviderFormatEnum.OPENAI_COMPATIBLE),
    "api.openrouter.ai": (
        "openrouter",
        "OpenRouter",
        ProviderFormatEnum.OPENAI_COMPATIBLE,
    ),
    "groq.com": ("groq", "Groq", ProviderFormatEnum.OPENAI_COMPATIBLE),
    "api.groq.com": ("groq", "Groq", ProviderFormatEnum.OPENAI_COMPATIBLE),
    "deepseek.com": ("deepseek", "DeepSeek", ProviderFormatEnum.OPENAI_COMPATIBLE),
    "api.deepseek.com": ("deepseek", "DeepSeek", ProviderFormatEnum.OPENAI_COMPATIBLE),
}


def _resolve_provider_config(
    provider_config: ProviderConfig | None,
    api_base: str | None,
    api_key: str | None,
    model: str | None,
    *,
    provider: str | ProviderFormatEnum | None = None,
) -> ProviderConfig:
    """Build a ``ProviderConfig`` for the in-process LLM call.

    The API layer is responsible for resolving the active provider via
    ``ProviderManager`` before calling this module. Core never reaches
    upward into ``omniscribe.api`` to look up provider state.

    If ``provider`` is explicitly passed, it is mapped to a ``ProviderFormatEnum``
    and ``ProviderConfig``.

    If the caller passes an explicit ``api_base`` we construct a
    one-shot config so the OCR pipeline can run end-to-end without
    touching the API layer (tests, embedded workflows, CLI use),
    inferring provider format from hostname and port.

    If neither is provided we fail fast with ``LLMCallError``.
    """
    if provider_config is not None:
        return provider_config

    if provider is not None:
        p_str = (
            provider.value
            if isinstance(provider, ProviderFormatEnum)
            else str(provider).strip().lower()
        )
        if p_str in _EXPLICIT_PROVIDERS:
            p_id, p_name, p_format, default_url = _EXPLICIT_PROVIDERS[p_str]
        else:
            p_id = p_str
            p_name = p_str.title()
            p_format = ProviderFormatEnum.OPENAI_COMPATIBLE
            default_url = ""

        effective_url = api_base or default_url
        if not effective_url:
            raise LLMCallError(f"Provider {provider!r} requires an `api_base` URL.")

        return ProviderConfig(
            id=p_id,
            display_name=p_name,
            format=p_format,
            api_url=effective_url,
            api_key=api_key,
            models=[model] if model else [],
        )

    if api_base:
        parsed = urlsplit(api_base)
        host = (parsed.hostname or "").lower()
        port = parsed.port

        if port == 1234:
            p_id = "lmstudio"
            p_name = "LM Studio"
            p_format = ProviderFormatEnum.OPENAI_COMPATIBLE
        elif port == 11434:
            p_id = "ollama"
            p_name = "Ollama"
            p_format = ProviderFormatEnum.OLLAMA_COMPATIBLE
        elif host in _PROVIDER_HOSTS:
            # Explicit equality lookup against _PROVIDER_HOSTS — NOT a
            # substring or endswith match. See security note on the
            # _PROVIDER_HOSTS constant above.
            p_id, p_name, p_format = _PROVIDER_HOSTS[host]
        else:
            p_id = "custom"
            p_name = "Custom"
            p_format = ProviderFormatEnum.OPENAI_COMPATIBLE

        logger.info("Auto-detected provider %r from api_base hostname %r", p_id, host)

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


def _parse_dict_item(item: dict[str, Any]) -> tuple[str, str | None]:
    """Extract (text, image_b64) from a single content dictionary item."""
    item_type = item.get("type")
    if item_type == "text":
        text_val = item.get("text")
        return (str(text_val) if text_val else "", None)

    if item_type == "image_url":
        img_obj = item.get("image_url")
        url_str = ""
        if isinstance(img_obj, str):
            url_str = img_obj
        elif isinstance(img_obj, dict):
            url_str = str(img_obj.get("url", ""))

        if not url_str:
            return "", None

        if "base64," in url_str:
            return "", url_str.split("base64,", 1)[1]
        return "", url_str

    if item_type == "image":
        src = item.get("source", {})
        if isinstance(src, dict):
            data = src.get("data")
            if data is not None:
                return "", str(data)

    return "", None


def _parse_content_items(items: list[Any]) -> tuple[str, str | None]:
    """Extract (text_content, image_b64) from a list of content items."""
    p_parts: list[str] = []
    extracted_image: str | None = None

    for item in items:
        if isinstance(item, str):
            p_parts.append(item)
            continue
        if not isinstance(item, dict):
            continue

        text, img = _parse_dict_item(item)
        if text:
            p_parts.append(text)
        if img and extracted_image is None:
            extracted_image = img

    return "\n".join(p_parts), extracted_image


def _extract_prompt_and_image(
    messages: list[dict[str, Any]] | None,
    prompt: str | None = None,
    image_b64: str | None = None,
) -> tuple[str, str | None]:
    """Parse messages payload or direct args into ``(text_prompt, image_b64)``.

    Only user-role entries contribute to the returned prompt and image;
    system-role entries (if any) are silently dropped — the explicit
    ``system_prompt`` parameter on :func:`call_llm` is the only
    supported way to attach a system message. Centralizing the
    system role there means a single parameter is the source of
    truth, instead of having to reason about every possible
    messages-list shape the caller might construct.
    """
    extracted_prompt = prompt or ""
    extracted_image = image_b64

    if not messages:
        return extracted_prompt, extracted_image

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
            text, img = _parse_content_items(content)
            if text:
                p_parts.append(text)
            if img and extracted_image is None:
                extracted_image = img

    if p_parts and not extracted_prompt:
        extracted_prompt = "\n".join(p_parts)

    return extracted_prompt, extracted_image


async def call_vlm(
    prompt: str,
    image_b64: str | None = None,
    *,
    model: str | None = None,
    api_base: str | None = None,
    api_key: str | None = None,
    provider: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 4096,
    timeout: float | None = None,
    provider_config: ProviderConfig | None = None,
    system_prompt: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> str:
    """Make an asynchronous VLM call using active ProviderManager configuration or explicit settings."""
    provider_config = _resolve_provider_config(
        provider_config, api_base, api_key, model, provider=provider
    )

    return await complete_vlm_prompt(
        provider_config=provider_config,
        prompt=prompt,
        image_b64=image_b64,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        system_prompt=system_prompt,
        http_client=http_client,
    )


async def call_llm(
    *,
    model: str | None = None,
    api_base: str | None = None,
    api_key: str | None = None,
    provider: str | None = None,
    messages: list[dict[str, Any]] | None = None,
    prompt: str | None = None,
    image_b64: str | None = None,
    temperature: float = 0.1,
    max_tokens: int | None = None,
    timeout: float | None = None,
    provider_config: ProviderConfig | None = None,
    system_prompt: str | None = None,
    http_client: httpx.AsyncClient | None = None,
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
        image_b64=image_b64,
    )

    provider_config = _resolve_provider_config(
        provider_config, api_base, api_key, model, provider=provider
    )

    return await complete_vlm_prompt(
        provider_config=provider_config,
        prompt=extracted_prompt,
        image_b64=extracted_image,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens or 4096,
        timeout=timeout,
        system_prompt=system_prompt,
        http_client=http_client,
    )


__all__ = [
    "call_llm",
    "call_vlm",
]
