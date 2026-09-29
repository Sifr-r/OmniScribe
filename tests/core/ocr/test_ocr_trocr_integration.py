"""Smoke test for the TrOCR dual-engine path (review M3).

Pre-fix, `OCRProcessor.perform_ocr_on_crop` called
`self.trocr_engine.ocr(image_base64)`, but `TrOCREngine` only exposes
`async def recognize(self, image_bytes: bytes) -> TrOCRResult`. The
mismatch was swallowed by the surrounding `try/except` so the bug was
invisible to the test suite. This test uses a fake TrOCREngine to
verify the correct method and argument type are used.
"""

from __future__ import annotations

import base64
import io
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from omniscribe.core.ocr import OCRProcessor
from omniscribe.core.ocr.exceptions import LLMBalanceError
from omniscribe.core.ocr.resilience import CircuitOpenError
from omniscribe.core.ocr.trocr import TrOCRResult


class _FakeTrOCREngine:
    """Stand-in for TrOCREngine.records every call so the test can
    assert that the OCRProcessor reaches into the engine the way the
    real one expects."""

    def __init__(self, result: TrOCRResult) -> None:
        self._result = result
        self.calls: list[bytes] = []

    async def recognize(self, image_bytes: bytes) -> TrOCRResult:
        self.calls.append(image_bytes)
        return self._result


def _tiny_png_base64() -> str:
    """A 1x1 white PNG encoded as base64 â€” enough to round-trip through
    b64decode and convince the OCRProcessor the crop is non-empty."""
    img = Image.new("RGB", (1, 1), "white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


async def test_trocr_fallback_uses_recognize_with_raw_bytes(monkeypatch):
    """When VLM confidence is low, OCRProcessor must call
    `TrOCREngine.recognize(image_bytes)` â€” not the non-existent
    `ocr(image_base64)` from the pre-fix code path."""

    # Replace `call_llm` inside the processor's namespace (not llm_client.py)
    # because OCRProcessor imports it directly: `from omniscribe.core
    # .llm_client import call_llm`. Patching the source module won't
    # affect the already-bound reference. After the P1 god-module split,
    # the binding lives in `omniscribe.core.ocr.chat_client`.

    async def _fake_call_llm(**kwargs) -> str:
        return "x"

    monkeypatch.setattr("omniscribe.core.ocr.chat_client.call_llm", _fake_call_llm)

    trocr = _FakeTrOCREngine(TrOCRResult(text="hello", confidence=0.9))
    processor = OCRProcessor(
        api_base="http://localhost:0/v1",  # never actually called
        handwriting_mode=True,
        trocr_engine=trocr,  # type: ignore[arg-type]
        confidence_threshold=0.75,
    )

    image_b64 = _tiny_png_base64()
    await processor.perform_ocr_on_crop(image_b64)

    # The TrOCR engine must have been called exactly once.
    assert len(trocr.calls) == 1
    # The argument must be raw bytes (decode of the base64 input), not
    # the base64 string. Pre-fix passed the base64 string, which would
    # have been rejected by the real `recognize` method.
    assert isinstance(trocr.calls[0], bytes)
    assert trocr.calls[0] == base64.b64decode(image_b64)


async def test_trocr_fallback_swallows_engine_errors(monkeypatch):
    """TrOCR is optional; an engine failure must not poison the OCR
    result. Pre-fix this branch was never reached because the call
    itself errored, so the error handling was untested."""

    class _BrokenTrOCREngine:
        async def recognize(self, image_bytes: bytes) -> TrOCRResult:
            raise RuntimeError("synthetic TrOCR failure")

    async def _fake_call_llm(**kwargs) -> str:
        return "x"

    monkeypatch.setattr("omniscribe.core.ocr.chat_client.call_llm", _fake_call_llm)

    processor = OCRProcessor(
        api_base="http://localhost:0/v1",
        handwriting_mode=True,
        trocr_engine=_BrokenTrOCREngine(),  # type: ignore[arg-type]
        confidence_threshold=0.75,
    )

    # No exception escapes; the VLM's "x" becomes the result.
    result = await processor.perform_ocr_on_crop(_tiny_png_base64())
    assert result == "x"


@pytest.mark.parametrize(
    "error", [LLMBalanceError("VLM unavailable"), CircuitOpenError(3, 1.0)]
)
async def test_trocr_correction_propagates_vlm_errors(error: Exception):
    processor = OCRProcessor(
        api_base="http://localhost:0/v1",
        handwriting_mode=True,
        trocr_engine=_FakeTrOCREngine(TrOCRResult(text="hello", confidence=0.9)),  # type: ignore[arg-type]
    )
    processor._chat = AsyncMock(side_effect=["x", error])  # type: ignore[method-assign]

    with pytest.raises(type(error)):
        await processor.perform_ocr_on_crop(_tiny_png_base64())

    assert processor._chat.await_count == 2
