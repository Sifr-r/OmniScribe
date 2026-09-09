from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine, Sequence
from typing import TYPE_CHECKING, Any

from PIL import Image

from omniscribe.core.aligner import HybridAligner
from omniscribe.core.document import BBox
from omniscribe.core.ocr import OCRProcessor
from omniscribe.core.ocr.resilience import CircuitOpenError
from omniscribe.core.workflows.base import (
    CancelCheck,
    OCRCancelled,
    PageBoxes,
    ProgressCallback,
    WarningCallback,
    notify,
)
from omniscribe.core.workflows.utils import (
    _decode_page_image,
    _estimate_confidence,
    _is_refinable,
    validate_bbox_coordinates,
)
from omniscribe.utils.image import crop_for_ocr_from_image

if TYPE_CHECKING:
    from omniscribe.core.callbacks import BlockCallbackSet

logger = logging.getLogger("omniscribe.core.workflows.hybrid")


class HybridOcrRunner:
    """Handles sparse and dense OCR dispatch for the hybrid workflow."""

    def __init__(
        self,
        aligner: HybridAligner,
        ocr_processor: OCRProcessor,
        block_callbacks: BlockCallbackSet | None = None,
        last_failed_pages: list[int] | None = None,
    ) -> None:
        self.aligner = aligner
        self.ocr_processor = ocr_processor
        self.block_callbacks = block_callbacks
        self.last_failed_pages: list[int] = (
            last_failed_pages if last_failed_pages is not None else []
        )

    async def ocr_pages(
        self,
        images_dict: dict[int, str],
        pages_structured: dict[int, PageBoxes],
        page_nums: Sequence[int],
        per_box_pages: set[int],
        concurrency: int,
        self_correction: bool,
        binarize: bool,
        dual_engine: bool,
        progress: ProgressCallback | None,
        on_warning: WarningCallback | None,
        cancel_check: CancelCheck | None = None,
        decoded_get: Callable[[int], Image.Image | None] | None = None,
        emit_page_callbacks: Callable[..., Coroutine[Any, Any, None]] | None = None,
    ) -> None:
        """Fan out OCR across pages, dispatching sparse vs dense per page."""
        api_base = str(getattr(self.ocr_processor, "api_base", "") or "").lower()
        effective_concurrency = concurrency
        if any(h in api_base for h in ("localhost", "127.0.0.1", "::1", "192.168.")):
            effective_concurrency = min(concurrency, 1)
        semaphore = asyncio.Semaphore(max(1, effective_concurrency))
        total = len(page_nums)

        async def process_page(
            p_num: int,
        ) -> tuple[int, PageBoxes, Exception | None]:
            try:
                if p_num in per_box_pages:
                    cached_image = (
                        decoded_get(p_num) if decoded_get is not None else None
                    )
                    aligned = await self.ocr_per_box(
                        images_dict[p_num],
                        pages_structured[p_num],
                        semaphore,
                        self_correction,
                        binarize,
                        dual_engine,
                        page_image=cached_image,
                    )
                    return p_num, aligned, None
                async with semaphore:
                    llm_lines = await self.ocr_processor.perform_ocr(
                        images_dict[p_num],
                        self_correction=self_correction,
                        binarize=binarize,
                        dual_engine=dual_engine,
                    )
                    if llm_lines:
                        aligned = await asyncio.to_thread(
                            self.aligner.align_text, pages_structured[p_num], llm_lines
                        )
                    else:
                        aligned = pages_structured[p_num]
                    return p_num, aligned, None
            except CircuitOpenError:
                raise
            except OCRCancelled:
                raise
            except Exception as e:
                logger.warning(
                    "OCR failed for page %s: %s: %s", p_num, type(e).__name__, e
                )
                return p_num, pages_structured[p_num], e

        if hasattr(self.ocr_processor, "ensure_model_loaded"):
            try:
                await self.ocr_processor.ensure_model_loaded()
            except Exception as e:
                logger.warning("Preflight ensure_model_loaded failed: %s", e)
                # Let ModelNotLoadedError propagate so user sees loaded models diagnostic immediately
                if type(e).__name__ == "ModelNotLoadedError":
                    raise

        completed = 0
        ocr_label = (
            "OCR"
            if not per_box_pages
            else f"OCR ({len(per_box_pages)} dense / {total - len(per_box_pages)} sparse)"
        )
        await notify(progress, "ocr", 0, total, f"{ocr_label} (0/{total})...")
        try:
            async with asyncio.TaskGroup() as tg:
                tasks = [tg.create_task(process_page(p)) for p in page_nums]
                for coro in asyncio.as_completed(tasks):
                    p_num, aligned, page_error = await coro

                    pages_structured[p_num] = aligned
                    completed += 1

                    if cancel_check is not None and cancel_check():
                        raise OCRCancelled(
                            f"OCR cancelled after page {p_num} ({completed}/{total})."
                        )

                    if emit_page_callbacks is not None:
                        await emit_page_callbacks(
                            p_num,
                            aligned,
                            _estimate_confidence,
                        )

                    await notify(
                        progress,
                        "ocr",
                        completed,
                        total,
                        f"{ocr_label} ({completed}/{total})",
                    )
                    if page_error is not None:
                        self.last_failed_pages.append(p_num)
                        if on_warning is not None:
                            await on_warning(p_num, page_error)
        except* OCRCancelled as eg:
            raise eg.exceptions[0] from None
        except* CircuitOpenError as eg:
            raise eg.exceptions[0] from None

    async def ocr_per_box(
        self,
        image_b64: str,
        structured: PageBoxes,
        semaphore: asyncio.Semaphore,
        self_correction: bool = False,
        binarize: bool = False,
        dual_engine: bool = False,
        page_image: Image.Image | None = None,
    ) -> PageBoxes:
        """Run per-crop OCR across all boxes for a dense page."""
        if page_image is None:
            page_image = await asyncio.to_thread(_decode_page_image, image_b64)

        async def ocr_one(idx: int, bbox: BBox) -> tuple[int, str]:
            try:
                async with semaphore:
                    if not _is_refinable(bbox):
                        return idx, ""
                    safe_bbox = validate_bbox_coordinates(bbox, clamp=True)
                    crop_b64 = await asyncio.to_thread(
                        crop_for_ocr_from_image, page_image, safe_bbox
                    )
                    if crop_b64 is None:
                        return idx, ""
                    text = await self.ocr_processor.perform_ocr_on_crop(
                        crop_b64,
                        self_correction=self_correction,
                        binarize=binarize,
                        dual_engine=dual_engine,
                    )
                    return idx, text
            except CircuitOpenError:
                raise
            except Exception as e:
                logger.warning(
                    "Dense OCR failed for box %s: %s: %s", idx, type(e).__name__, e
                )
                return idx, ""

        results: dict[int, str] = {}
        try:
            async with asyncio.TaskGroup() as tg:
                tasks = [
                    tg.create_task(ocr_one(i, bbox))
                    for i, (bbox, _) in enumerate(structured)
                ]
                for fut in asyncio.as_completed(tasks):
                    idx, text = await fut
                    results[idx] = text.strip()
        except* CircuitOpenError as eg:
            raise eg.exceptions[0] from None
        return [(bbox, results.get(i, "")) for i, (bbox, _) in enumerate(structured)]
