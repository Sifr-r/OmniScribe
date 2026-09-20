"""Regression coverage for permanent provider-balance failures in OCR stages."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image

from omniscribe.core.ocr.exceptions import LLMBalanceError
from omniscribe.core.workflows.stages.ocr import HybridOcrRunner


async def test_dense_ocr_propagates_balance_error() -> None:
    processor = MagicMock()
    processor.perform_ocr_on_crop = AsyncMock(
        side_effect=LLMBalanceError("Provider returned HTTP status 402")
    )
    runner = HybridOcrRunner(aligner=MagicMock(), ocr_processor=processor)

    img = Image.new("RGB", (100, 100), color="white")
    for x in range(50):
        for y in range(50):
            img.putpixel((x, y), (0, 0, 0))

    with pytest.raises(LLMBalanceError):
        await runner.ocr_per_box(
            "",
            [((0.1, 0.1, 0.9, 0.9), "")],
            asyncio.Semaphore(1),
            page_image=img,
        )
