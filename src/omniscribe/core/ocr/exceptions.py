"""Exception types for the LLM-based OCR processor."""

from __future__ import annotations


class LLMCallError(RuntimeError):
    """Raised when a call to the local LLM OCR endpoint fails.

    Wraps the underlying exception (connection refused, model not loaded,
    timeout, auth, ...) with a message that names the api-base and model
    so the user can diagnose without digging through a stack trace.
    """


class ModelNotLoadedError(LLMCallError):
    """Raised when the requested model is not loaded on the LLM server.

    LM Studio silently falls back to whatever model is currently loaded
    when an OpenAI-compat client requests an unavailable model ID — so a
    typo in --model or a forgotten model swap produces subtly wrong OCR
    output with no surface error. This exception is raised by
    :meth:`OCRProcessor.ensure_model_loaded` (and the grounded equivalent)
    *before* any OCR work starts so the user sees the mismatch immediately
    instead of debugging strange output later.
    """


class LLMBalanceError(LLMCallError):
    """Raised when the provider rejects a completion call with HTTP 402.

    Account-level exhaustion (cloud provider out of credits) fails every
    subsequent call deterministically, so it must surface as its own type:
    the OCR engines treat it like :class:`CircuitOpenError` (fail fast
    instead of burning the remaining pages), and the API layer maps it to
    a 402 ``payment_required`` envelope so the UI shows "top up your
    provider account" instead of a generic 5xx.
    """
