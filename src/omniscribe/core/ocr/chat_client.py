"""VLM chat client — retry + circuit-breaker wrapper around ``call_llm``.

Audit catalog (Sprint 6 long-file split): ``core/ocr/processor.py``
was 672 LOC mixing the ``OCRProcessor`` class, the prompt selection
helpers, the tesseract draft helper, the adaptive threshold helper,
and the chat-with-retry method (which was the only consumer of
``call_llm`` and the only place the circuit breaker + exponential
backoff were wired in). The chat method was 105 LOC of
retry-loop / error-translation logic that had no business
sitting on a class whose main responsibility is page / crop
OCR orchestration.

This module is the chat client half: the retry loop, the
circuit-breaker integration, the context-length error
translation, and the LLMCallError surface. ``OCRProcessor``
instantiates a ``ChatClient`` in ``__init__`` and replaces the
4 ``await self._chat(...)`` call sites with
``await self._chat_client.chat(...)``.

The client owns:

- the per-call timeout / max-tokens / system_prompt parameters
  (passed by the caller, who decides page vs crop budget)
- the circuit-breaker integration (one breaker per
  ``(api_base, model)`` tuple, shared across callers via the
  registry the processor was constructed with)
- the retry policy (transient-only; permanent failures raise
  immediately and the breaker counts them)
- the error translation (the context-size-exceeded substring
  match that turns a context-length LLM error into an
  actionable LM-Studio-context-length fix message)
"""

from __future__ import annotations

import asyncio
import logging

from omniscribe.core.llm.client import call_llm
from omniscribe.core.llm.temperatures import TEMPERATURE_OCR
from omniscribe.core.ocr.exceptions import LLMCallError
from omniscribe.core.ocr.resilience import (
    CircuitBreaker,
    is_transient_error,
)

logger = logging.getLogger(__name__)


class ChatClient:
    """Async VLM caller with retry-on-transient and per-endpoint circuit breaker.

    Transient failures (429, 5xx, connection resets, timeouts) are
    retried up to ``max_retries`` times with exponential backoff.
    Permanent failures (context-length exceeded, auth) raise
    immediately. The circuit breaker counts consecutive failures
    (across all attempts) and fails fast once the endpoint is deemed
    down, so a dead server doesn't serialize N page-timeouts.

    ``system_prompt``: when set, sent as a separate system-role
    message. The OLMOCR-2 page path leaves this ``None`` to keep
    the model's RL-trained distribution intact.
    """

    def __init__(
        self,
        *,
        model: str,
        api_base: str,
        api_key: str,
        max_retries: int,
        retry_base_delay_s: float,
        retry_max_delay_s: float,
        circuit_breaker: CircuitBreaker,
    ) -> None:
        self.model = model
        self.api_base = api_base
        self.api_key = api_key
        self.max_retries = max_retries
        self.retry_base_delay_s = retry_base_delay_s
        self.retry_max_delay_s = retry_max_delay_s
        self.circuit_breaker = circuit_breaker

    async def chat(
        self,
        prompt: str,
        image_base64: str,
        *,
        timeout: float,
        max_tokens: int,
        system_prompt: str | None = None,
        temperature: float | None = None,
    ) -> str:
        """Call the VLM with retry-on-transient and circuit-breaker protection.

        ``temperature=None`` keeps the default ``TEMPERATURE_OCR``; callers
        may pass an explicit value (e.g. the quality repair loop's per-retry
        bump) to override it for one call.

        Returns the model's stripped text. Raises :class:`LLMCallError`
        after exhausting retries on a transient error, or immediately on
        a permanent error (the context-size-exceeded branch translates
        the LLM error into an LM-Studio-context-length fix message).
        """
        # M3 audit fix: removed the redundant unconditional pre-loop
        # ``await self.circuit_breaker.check()``. The in-loop call is
        # now invoked on EVERY attempt (not just attempt > 0) so the
        # first attempt also consults the breaker — a previously OPEN
        # breaker fails fast without consuming an LLM call.
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            # Re-check on every attempt: a prior attempt may have
            # tripped the breaker, or the breaker may already be open
            # when this call started. CircuitOpenError propagates
            # directly (not an LLMCallError) so the engine's per-page
            # handler sees "endpoint down".
            await self.circuit_breaker.check()
            try:
                content = await call_llm(
                    model=self.model,
                    api_base=self.api_base,
                    api_key=self.api_key,
                    temperature=TEMPERATURE_OCR if temperature is None else temperature,
                    max_tokens=max_tokens,
                    timeout=timeout,
                    system_prompt=system_prompt,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/jpeg;base64,{image_base64}"
                                    },
                                },
                            ],
                        }
                    ],
                )
                await self.circuit_breaker.record_success()
                return content.strip()
            except Exception as e:
                last_exc = e
                await self.circuit_breaker.record_failure()

                if not is_transient_error(e):
                    break  # permanent failure — do not retry
                if attempt < self.max_retries:
                    delay = min(
                        self.retry_base_delay_s * (2**attempt),
                        self.retry_max_delay_s,
                    )
                    logger.warning(
                        "Transient LLM error (attempt %d/%d), retrying in "
                        "%.1fs: %s: %s",
                        attempt + 1,
                        self.max_retries + 1,
                        delay,
                        type(e).__name__,
                        e,
                    )
                    await asyncio.sleep(delay)

        if last_exc is None:  # pragma: no cover - unreachable defensive guard
            raise RuntimeError("retry loop exited without capturing an exception")
        err_msg = str(last_exc)
        if any(
            term in err_msg.lower()
            for term in (
                "context size",
                "context_length_exceeded",
                "context length",
            )
        ):
            raise LLMCallError(
                f"LLM OCR call failed due to Context Size Limit. "
                f"Please load the model in LM Studio and increase the 'Context Length' in the right-side panel "
                f"to at least 8192 or 16384 tokens. "
                f"Underlying error: {last_exc}"
            ) from last_exc
        raise LLMCallError(
            f"LLM OCR call failed against {self.api_base} "
            f"({type(last_exc).__name__}): {last_exc}"
        ) from last_exc
