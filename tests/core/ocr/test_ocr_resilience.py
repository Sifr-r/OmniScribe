"""Tests for LLM call resilience: retry with backoff + circuit breaker (R-O1/R-O2)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from omniscribe.core.ocr.exceptions import LLMCallError
from omniscribe.core.ocr.processor import OCRProcessor
from omniscribe.core.ocr.resilience import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    is_transient_error,
)

# ---------------------------------------------------------------------------
# is_transient_error classification
# ---------------------------------------------------------------------------


class _FakeHTTPError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code


@pytest.mark.parametrize(
    "exc",
    [
        _FakeHTTPError("rate limit reached", 429),
        _FakeHTTPError("Internal Server Error", 500),
        _FakeHTTPError("Bad Gateway", 502),
        _FakeHTTPError("Service Unavailable", 503),
        _FakeHTTPError("Gateway Timeout", 504),
        _FakeHTTPError("Connection reset by peer"),
        _FakeHTTPError("Connection refused"),
        _FakeHTTPError("ReadTimeout: timed out after 240s"),
        _FakeHTTPError("The server is overloaded"),
        _FakeHTTPError("some unknown vendor quirk"),  # default: retryable
    ],
)
def test_transient_errors_are_retryable(exc):
    assert is_transient_error(exc) is True


@pytest.mark.parametrize(
    "exc",
    [
        _FakeHTTPError("context_length_exceeded: max 8192 tokens"),
        _FakeHTTPError("This model's maximum context size is 8192"),
        _FakeHTTPError("Invalid API key provided", 401),
        _FakeHTTPError("permission denied for this resource", 403),
        _FakeHTTPError("model not found: typo/model", 404),
        _FakeHTTPError("Unauthorized"),
        _FakeHTTPError("invalid request: bad image format", 400),
    ],
)
def test_permanent_errors_are_not_retryable(exc):
    assert is_transient_error(exc) is False


# ---------------------------------------------------------------------------
# Audit D13 (ValueError split) + D19 (RuntimeError default = retryable)
# ---------------------------------------------------------------------------


def test_value_error_is_permanent_caller_side_error():
    """Audit D13: ValueError in our call site is caller-side (bad
    payload shape, bad image format, bad page number). Retrying the
    same payload produces the same ValueError; the operator should
    see the failure, not a silent retry loop.
    """
    assert is_transient_error(ValueError("bad image format")) is False
    assert (
        is_transient_error(ValueError("page number must be a positive integer"))
        is False
    )
    assert is_transient_error(ValueError("invalid json field")) is False


def test_unknown_runtime_error_defaults_to_retryable():
    """Audit D19: a bare ``RuntimeError`` with a custom message (not
    matching any transient/permanent term) should still be retried.
    The previous default ``return not isinstance(exc, RuntimeError)``
    misclassified transport errors like ``httpx.ConnectError`` (``All
    connection attempts failed`` — a RuntimeError subclass with a
    transient message that the substring check above may miss on
    edge-case wording) as permanent. Retrying once is cheaper than
    degrading a page.
    """

    # httpx.ConnectError is a RuntimeError subclass with a
    # non-standard message; the previous default classified it as
    # permanent. The new default retries it.
    class _HttpxConnectError(RuntimeError):
        pass

    assert (
        is_transient_error(_HttpxConnectError("All connection attempts failed")) is True
    )
    assert is_transient_error(RuntimeError("some vendor quirk")) is True
    # The substring check still catches the common transport signals:
    assert is_transient_error(RuntimeError("connection refused")) is True
    assert is_transient_error(RuntimeError("ReadTimeout: timed out after 240s")) is True
    # But permanent substrings still classify as permanent even on
    # RuntimeError:
    assert is_transient_error(RuntimeError("context_length_exceeded: 12000")) is False
    assert is_transient_error(RuntimeError("Invalid API key provided")) is False


# ---------------------------------------------------------------------------
# CircuitBreaker state machine
# ---------------------------------------------------------------------------


async def test_breaker_stays_closed_below_threshold():
    cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=10.0)
    await cb.record_failure()
    await cb.record_failure()
    await cb.check()  # should not raise
    assert cb.consecutive_failures == 2


async def test_breaker_opens_at_threshold():
    cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=10.0)
    for _ in range(3):
        await cb.record_failure()
    with pytest.raises(CircuitOpenError, match="3 consecutive"):
        await cb.check()


async def test_breaker_resets_on_success():
    cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=10.0)
    await cb.record_failure()
    await cb.record_success()
    assert cb.consecutive_failures == 0
    await cb.record_failure()  # only 1 consecutive failure â€” still closed
    await cb.check()


async def test_breaker_half_open_after_cooldown():
    now = [0.0]
    cb = CircuitBreaker(
        failure_threshold=2, cooldown_seconds=30.0, clock=lambda: now[0]
    )
    await cb.record_failure()
    await cb.record_failure()
    assert cb.is_open

    # Advance past cooldown â†’ half-open: check() passes a probe through.
    now[0] = 31.0
    assert not cb.is_open
    await cb.check()  # probe allowed


async def test_breaker_closes_after_successful_probe():
    now = [0.0]
    cb = CircuitBreaker(
        failure_threshold=2, cooldown_seconds=30.0, clock=lambda: now[0]
    )
    await cb.record_failure()
    await cb.record_failure()
    now[0] = 31.0
    await cb.record_success()
    assert cb.consecutive_failures == 0
    await cb.check()  # fully closed


async def test_breaker_reopens_after_failed_probe():
    now = [0.0]
    cb = CircuitBreaker(
        failure_threshold=2, cooldown_seconds=30.0, clock=lambda: now[0]
    )
    await cb.record_failure()
    await cb.record_failure()
    now[0] = 31.0
    await cb.record_failure()  # probe failed
    with pytest.raises(CircuitOpenError):
        await cb.check()


# ---------------------------------------------------------------------------
# CircuitState enum + state property
# ---------------------------------------------------------------------------


def test_breaker_state_starts_closed():
    cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=10.0)
    assert cb.state is CircuitState.CLOSED
    assert cb.is_open is False


async def test_breaker_state_transitions_closed_to_open():
    now = [0.0]
    cb = CircuitBreaker(
        failure_threshold=2, cooldown_seconds=30.0, clock=lambda: now[0]
    )
    assert cb.state is CircuitState.CLOSED
    await cb.record_failure()
    assert cb.state is CircuitState.CLOSED  # under threshold
    await cb.record_failure()
    assert cb.state is CircuitState.OPEN  # type: ignore[comparison-overlap]
    assert cb.is_open is True


async def test_breaker_state_transitions_open_to_half_open():
    now = [0.0]
    cb = CircuitBreaker(
        failure_threshold=2, cooldown_seconds=30.0, clock=lambda: now[0]
    )
    await cb.record_failure()
    await cb.record_failure()
    assert cb.state is CircuitState.OPEN
    # Cooldown elapsed â†’ half-open: a probe is allowed.
    now[0] = 31.0
    assert cb.state is CircuitState.HALF_OPEN  # type: ignore[comparison-overlap]
    # is_open is False during half-open (a probe is allowed through).
    assert cb.is_open is False
    await cb.check()  # should not raise


async def test_breaker_state_transitions_half_open_to_closed_on_success():
    now = [0.0]
    cb = CircuitBreaker(
        failure_threshold=2, cooldown_seconds=30.0, clock=lambda: now[0]
    )
    await cb.record_failure()
    await cb.record_failure()
    now[0] = 31.0
    assert cb.state is CircuitState.HALF_OPEN
    await cb.record_success()
    assert cb.state is CircuitState.CLOSED  # type: ignore[comparison-overlap]
    assert cb.consecutive_failures == 0


# ---------------------------------------------------------------------------
# OCRProcessor._chat retry integration
# ---------------------------------------------------------------------------


def _make_processor() -> OCRProcessor:
    p = OCRProcessor(api_base="http://test:1234/v1", api_key="k", model="m")
    p.MAX_RETRIES = 2
    p.RETRY_BASE_DELAY_S = 0.001  # keep tests fast
    p.RETRY_MAX_DELAY_S = 0.01
    p.circuit_breaker = CircuitBreaker(failure_threshold=50, cooldown_seconds=1.0)
    return p


async def test_chat_retries_transient_error_and_succeeds():
    p = _make_processor()
    mock_call = AsyncMock(
        side_effect=[
            _FakeHTTPError("Internal Server Error", 500),
            "  recovered text  ",
        ]
    )
    with patch("omniscribe.core.ocr.chat_client.call_llm", mock_call):
        result = await p._chat("prompt", "aW1n", timeout=10, max_tokens=100)

    assert result == "recovered text"
    assert mock_call.call_count == 2


async def test_chat_exhausts_retries_then_raises_llm_call_error():
    p = _make_processor()
    mock_call = AsyncMock(side_effect=_FakeHTTPError("Service Unavailable", 503))
    with patch("omniscribe.core.ocr.chat_client.call_llm", mock_call):
        with pytest.raises(LLMCallError, match="Service Unavailable"):
            await p._chat("prompt", "aW1n", timeout=10, max_tokens=100)

    assert mock_call.call_count == 3  # 1 initial + 2 retries


async def test_chat_does_not_retry_permanent_context_size_error():
    p = _make_processor()
    mock_call = AsyncMock(
        side_effect=_FakeHTTPError("context_length_exceeded: 12000 > 8192")
    )
    with patch("omniscribe.core.ocr.chat_client.call_llm", mock_call):
        with pytest.raises(LLMCallError, match="Context Size Limit"):
            await p._chat("prompt", "aW1n", timeout=10, max_tokens=100)

    assert mock_call.call_count == 1  # no retries


async def test_chat_does_not_retry_auth_error():
    p = _make_processor()
    mock_call = AsyncMock(side_effect=_FakeHTTPError("Invalid API key", 401))
    with patch("omniscribe.core.ocr.chat_client.call_llm", mock_call):
        with pytest.raises(LLMCallError, match="Invalid API key"):
            await p._chat("prompt", "aW1n", timeout=10, max_tokens=100)

    assert mock_call.call_count == 1


async def test_circuit_breaker_fails_fast_after_consecutive_failures():
    p = _make_processor()
    p.circuit_breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=60.0)
    mock_call = AsyncMock(side_effect=_FakeHTTPError("Connection refused"))

    with patch("omniscribe.core.ocr.chat_client.call_llm", mock_call):
        # First call: 3 attempts all fail â†’ breaker at 3 â†’ open.
        with pytest.raises(LLMCallError):
            await p._chat("prompt", "aW1n", timeout=10, max_tokens=100)
        assert mock_call.call_count == 3

        # Second call: circuit open â†’ fails fast, zero LLM calls.
        with pytest.raises(CircuitOpenError, match="circuit breaker open"):
            await p._chat("prompt", "aW1n", timeout=10, max_tokens=100)
        assert mock_call.call_count == 3  # unchanged


async def test_successful_call_resets_breaker_for_next_page():
    p = _make_processor()
    p.circuit_breaker = CircuitBreaker(failure_threshold=4, cooldown_seconds=60.0)

    transient = _FakeHTTPError("Bad Gateway", 502)
    mock_call = AsyncMock(side_effect=[transient, transient, "ok", "ok"])

    with patch("omniscribe.core.ocr.chat_client.call_llm", mock_call):
        result = await p._chat("prompt", "aW1n", timeout=10, max_tokens=100)
        assert result == "ok"
        # 2 failures then success â†’ breaker reset to 0.
        assert p.circuit_breaker.consecutive_failures == 0

        result = await p._chat("prompt", "aW1n", timeout=10, max_tokens=100)
        assert result == "ok"
