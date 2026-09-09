"""Multi-format asynchronous LLM completion dispatcher.

Supports openai_compatible, anthropic_compatible, and ollama_compatible formats
with retry, timeout, and contextual domain error handling.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

import httpx

from omniscribe.core.llm.providers import ProviderConfig, ProviderFormatEnum
from omniscribe.core.ocr.exceptions import LLMCallError
from omniscribe.core.ocr.resilience import RETRYABLE_STATUS_CODES, is_transient_error

logger = logging.getLogger(__name__)

# A single shared AsyncClient reuses its connection pool across every call,
# so we stop paying the TCP+TLS handshake on every VLM page. Per-request
# timeout still flows through ``client.post(..., timeout=...)`` so callers
# can pick a slow-page budget independently of the pool's default.
_DEFAULT_CLIENT_TIMEOUT_S = 60.0
_client_lock = threading.Lock()
_shared_client: httpx.AsyncClient | None = None
# httpx.AsyncClient is bound to the event loop on which it is first
# awaited. If a different loop later reuses the cache (tests run on
# a fresh loop, Celery tasks, multi-worker servers), the old client
# raises ``RuntimeError: ... bound to a different event loop`` on
# every await. Track the loop we created the client on; on mismatch,
# invalidate the cache and lazily create a new client for the
# current loop. The abandoned client is GC'd (its sockets eventually
# close via httpx's transport teardown).
_shared_client_loop: asyncio.AbstractEventLoop | None = None


def _get_shared_client() -> httpx.AsyncClient:
    """Return the process-wide :class:`httpx.AsyncClient`, creating it on first use.

    A single client keeps its connection pool warm across the entire run,
    which is the only reason this exists — a fresh ``AsyncClient`` per call
    re-handshakes TCP+TLS to LM Studio on every page. Timeout is set on the
    pool to a safe default; per-request overrides are passed to ``post()``.

    F1.4 audit fix: the client is bound to the event loop on which it is
    first awaited. If a different loop later reuses the cache (tests,
    Celery tasks, multi-worker servers), the cache is invalidated and
    a new client is created for the current loop.
    """
    global _shared_client, _shared_client_loop
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        # Not inside an async context (e.g. sync helper at startup).
        # Pass ``None`` so the cache check below treats this as
        # "definitely a different loop" if a client was created in a
        # previous async context.
        current_loop = None

    if (
        _shared_client is not None
        and _shared_client_loop is current_loop
        and not _shared_client.is_closed
    ):
        return _shared_client

    with _client_lock:
        if (
            _shared_client is None
            or _shared_client_loop is not current_loop
            or _shared_client.is_closed
        ):
            _shared_client = httpx.AsyncClient(timeout=_DEFAULT_CLIENT_TIMEOUT_S)
            _shared_client_loop = current_loop
    return _shared_client


async def aclose_shared_client() -> None:
    """Close the shared client (call on FastAPI shutdown to release sockets)."""
    global _shared_client, _shared_client_loop
    client = _shared_client
    _shared_client = None
    _shared_client_loop = None
    if client is not None:
        await client.aclose()


async def complete_vlm_prompt(
    provider_config: ProviderConfig,
    prompt: str,
    image_base64: str | None = None,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 4096,
    timeout: float | None = None,
    system_prompt: str | None = None,
    max_retries: int = 0,
    retry_base_delay: float = 1.0,
) -> str:
    """Execute asynchronous LLM completion based on provider configuration format.

    Args:
        provider_config: Configuration for target LLM provider.
        prompt: Text prompt / instruction.
        image_base64: Optional base64-encoded image string.
        model: Model identifier override.
        temperature: Generation temperature (default 0.0).
        max_tokens: Maximum tokens to generate (default 4096).
        timeout: Per-request timeout in seconds. ``None`` falls back to the
            shared client's default (60s). Callers that need a longer budget
            (e.g. ``OMNISCRIBE_VLM_PAGE_TIMEOUT=240``) pass it here.
        system_prompt: Optional system-role instruction. When set, it is
            prepended to the messages array as a ``role: system`` entry
            before the user turn. Kept separate from the user prompt so
            OlmOCR-2's RL-trained prompt string stays a pure user message
            (do not set this for the canonical OLMOCR_PAGE_PROMPT).
        max_retries: Number of retry attempts on transient errors (5xx,
            429, connection resets). Defaults to ``0`` (single POST) — the
            caller owns the retry policy. ``OCRProcessor._chat`` is the
            single retry authority for the OCR pipeline; it sets
            ``self.MAX_RETRIES`` on its outer loop. Direct callers that want
            retries must opt in explicitly.
        retry_base_delay: Base delay in seconds for exponential backoff
            between retries. Only used when ``max_retries > 0``.

    Returns:
        Generated text completion.

    Raises:
        LLMCallError: On non-200 responses, permanent API errors, or unrecoverable transient errors.
    """
    fmt = (
        provider_config.format.value
        if isinstance(provider_config.format, ProviderFormatEnum)
        else str(provider_config.format)
    )

    if model and model.strip():
        target_model = model.strip()
    elif provider_config.models:
        target_model = provider_config.models[0]
    else:
        # F1.3 audit fix (P0): defensive fail-fast. Silently defaulting to a
        # specific cloud model id (e.g. "gpt-4o") would route requests to a
        # model the local endpoint doesn't serve, or worse, hit a cloud
        # provider the user never intended to call.
        raise LLMCallError(
            f"Cannot resolve target model for provider '{provider_config.id}': "
            f"no model passed and provider_config.models is empty. "
            f"Set the model argument, list a default under the provider config, "
            f"or set OMNISCRIBE_MODEL."
        )

    api_url = provider_config.api_url.rstrip("/")
    if provider_config.base_path:
        b_path = provider_config.base_path.strip("/")
        if b_path:
            api_url = f"{api_url}/{b_path}"

    headers: dict[str, str] = {"Content-Type": "application/json"}
    headers.update(provider_config.headers)

    if fmt == ProviderFormatEnum.OPENAI_COMPATIBLE.value:
        if api_url.endswith("/chat/completions"):
            endpoint = api_url
        elif api_url.endswith("/v1"):
            endpoint = f"{api_url}/chat/completions"
        else:
            endpoint = f"{api_url}/v1/chat/completions"

        if provider_config.api_key and "Authorization" not in headers:
            headers["Authorization"] = f"Bearer {provider_config.api_key}"

        if image_base64:
            content: Any = [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"},
                },
            ]
        else:
            content = prompt

        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": content})

        payload: dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

    elif fmt == ProviderFormatEnum.ANTHROPIC_COMPATIBLE.value:
        if api_url.endswith("/v1/messages") or api_url.endswith("/messages"):
            endpoint = api_url
        elif api_url.endswith("/v1"):
            endpoint = f"{api_url}/messages"
        else:
            endpoint = f"{api_url}/v1/messages"

        headers["x-api-key"] = provider_config.api_key or ""
        headers["anthropic-version"] = "2023-06-01"

        if image_base64:
            anthropic_content: list[dict[str, Any]] = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": image_base64,
                    },
                },
                {"type": "text", "text": prompt},
            ]
        else:
            anthropic_content = [{"type": "text", "text": prompt}]

        anthropic_messages: list[dict[str, Any]] = [
            {"role": "user", "content": anthropic_content}
        ]

        payload = {
            "model": target_model,
            "messages": anthropic_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_prompt:
            payload["system"] = system_prompt

    elif fmt == ProviderFormatEnum.OLLAMA_COMPATIBLE.value:
        endpoint = api_url if api_url.endswith("/api/chat") else f"{api_url}/api/chat"

        if provider_config.api_key and "Authorization" not in headers:
            headers["Authorization"] = f"Bearer {provider_config.api_key}"

        ollama_messages: list[dict[str, Any]] = []
        if system_prompt:
            ollama_messages.append({"role": "system", "content": system_prompt})

        user_msg: dict[str, Any] = {"role": "user", "content": prompt}
        if image_base64:
            user_msg["images"] = [image_base64]
        ollama_messages.append(user_msg)

        payload = {
            "model": target_model,
            "messages": ollama_messages,
            "stream": False,
            "options": {"temperature": temperature},
        }

    else:
        raise LLMCallError(f"Unsupported provider format: '{provider_config.format}'")

    max_retries = max(0, int(max_retries))
    if max_retries > 0 and retry_base_delay < 0:
        retry_base_delay = 0.0

    client = _get_shared_client()
    request_timeout: float = (
        timeout if timeout is not None else _DEFAULT_CLIENT_TIMEOUT_S
    )

    last_error: Exception | None = None
    for attempt in range(1, max_retries + 2):
        try:
            resp = await client.post(
                endpoint, json=payload, headers=headers, timeout=request_timeout
            )

            if resp.status_code == 200:
                data = resp.json()
                if fmt == ProviderFormatEnum.OPENAI_COMPATIBLE.value:
                    choices = data.get("choices", [])
                    if choices and isinstance(choices, list):
                        msg = choices[0].get("message", {})
                        if isinstance(msg, dict):
                            val = msg.get("content")
                            if isinstance(val, str) and val.strip():
                                return val
                            reasoning = msg.get("reasoning_content")
                            if isinstance(reasoning, str) and reasoning.strip():
                                return reasoning
                            if isinstance(val, str):
                                return val
                            logger.warning(
                                "Provider '%s' (%s): choices[0].message.content "
                                "is not a string (got %s); returning empty result.",
                                provider_config.id,
                                fmt,
                                type(val).__name__,
                            )
                            return ""
                    logger.warning(
                        "Provider '%s' (%s): response missing or malformed "
                        "'choices[0].message.content'; got keys=%s; returning empty.",
                        provider_config.id,
                        fmt,
                        sorted(data.keys())
                        if isinstance(data, dict)
                        else type(data).__name__,
                    )
                    return ""

                elif fmt == ProviderFormatEnum.ANTHROPIC_COMPATIBLE.value:
                    content_list = data.get("content", [])
                    if content_list and isinstance(content_list, list):
                        first_item = content_list[0]
                        if isinstance(first_item, dict):
                            val = first_item.get("text", "")
                            if isinstance(val, str):
                                return val
                            logger.warning(
                                "Provider '%s' (%s): content[0].text is not a "
                                "string (got %s); returning empty result.",
                                provider_config.id,
                                fmt,
                                type(val).__name__,
                            )
                            return ""
                    logger.warning(
                        "Provider '%s' (%s): response missing or malformed "
                        "'content[0].text'; got keys=%s; returning empty.",
                        provider_config.id,
                        fmt,
                        sorted(data.keys())
                        if isinstance(data, dict)
                        else type(data).__name__,
                    )
                    return ""

                elif fmt == ProviderFormatEnum.OLLAMA_COMPATIBLE.value:
                    msg_obj = data.get("message", {})
                    if isinstance(msg_obj, dict):
                        val = msg_obj.get("content", "")
                        if isinstance(val, str):
                            return val
                        logger.warning(
                            "Provider '%s' (%s): message.content is not a "
                            "string (got %s); returning empty result.",
                            provider_config.id,
                            fmt,
                            type(val).__name__,
                        )
                        return ""
                    logger.warning(
                        "Provider '%s' (%s): response missing or malformed "
                        "'message.content'; got keys=%s; returning empty.",
                        provider_config.id,
                        fmt,
                        sorted(data.keys())
                        if isinstance(data, dict)
                        else type(data).__name__,
                    )
                    return ""

            # Non-200 response handling
            err_msg = (
                f"Provider '{provider_config.id}' ({fmt}) returned HTTP status {resp.status_code}: "
                f"{resp.text[:500]}"
            )
            if resp.status_code in RETRYABLE_STATUS_CODES and attempt <= max_retries:
                logger.warning(
                    "Transient HTTP %d from provider '%s' (attempt %d/%d), retrying in %.1fs...",
                    resp.status_code,
                    provider_config.id,
                    attempt,
                    max_retries + 1,
                    retry_base_delay * (2 ** (attempt - 1)),
                )
                await asyncio.sleep(retry_base_delay * (2 ** (attempt - 1)))
                continue

            raise LLMCallError(err_msg)

        except Exception as exc:
            if isinstance(exc, LLMCallError):
                raise exc
            if is_transient_error(exc) and attempt <= max_retries:
                logger.warning(
                    "Transient transport error calling provider '%s' (attempt %d/%d): %s",
                    provider_config.id,
                    attempt,
                    max_retries + 1,
                    exc,
                )
                last_error = exc
                await asyncio.sleep(retry_base_delay * (2 ** (attempt - 1)))
                continue

            if last_error is not None:
                last_error = exc
                break

            if not str(exc).strip():
                exc_detail = (
                    f"{type(exc).__name__} (request timed out after {request_timeout:.1f}s)"
                    if isinstance(exc, (httpx.TimeoutException, asyncio.TimeoutError))
                    else f"{type(exc).__name__}"
                )
            else:
                exc_detail = str(exc).strip()

            raise LLMCallError(
                f"VLM call failed for provider '{provider_config.id}' ({fmt}): {exc_detail}"
            ) from exc

    if last_error:
        if not str(last_error).strip():
            last_error_detail = (
                f"{type(last_error).__name__} (request timed out after {request_timeout:.1f}s)"
                if isinstance(
                    last_error, (httpx.TimeoutException, asyncio.TimeoutError)
                )
                else f"{type(last_error).__name__}"
            )
        else:
            last_error_detail = str(last_error).strip()

        raise LLMCallError(
            f"VLM call failed for provider '{provider_config.id}' after {max_retries + 1} attempts: {last_error_detail}"
        ) from last_error

    raise LLMCallError(f"VLM call failed for provider '{provider_config.id}'")


__all__ = ["aclose_shared_client", "complete_vlm_prompt"]
