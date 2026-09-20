from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable, Sequence

from PIL import Image

from omniscribe.core.document import BBox
from omniscribe.core.imaging.utils import decode_base64_image
from omniscribe.core.ocr import OCRProcessor
from omniscribe.core.ocr.exceptions import LLMBalanceError
from omniscribe.core.ocr.resilience import CircuitOpenError
from omniscribe.core.workflows.base import (
    CancelCheck,
    OCRCancelled,
    PageBoxes,
    ProgressCallback,
    notify,
)
from omniscribe.core.workflows.utils import (
    _drop_refined_duplicates,
    _is_refinable,
    validate_bbox_coordinates,
)
from omniscribe.utils.image import crop_many_for_ocr_from_image

logger = logging.getLogger("omniscribe.core.workflows.hybrid")


class HybridRefiner:
    """Handles refinement of empty or uncertain boxes on sparse pages."""

    def __init__(self, ocr_processor: OCRProcessor) -> None:
        self.ocr_processor = ocr_processor

    async def refine_pages(
        self,
        pages_structured: dict[int, PageBoxes],
        images_dict: dict[int, str],
        page_nums: Sequence[int],
        per_box_pages: set[int],
        concurrency: int,
        self_correction: bool,
        binarize: bool,
        dual_engine: bool,
        progress: ProgressCallback | None,
        cancel_check: CancelCheck | None = None,
        decoded_get: Callable[[int], Image.Image | None] | None = None,
    ) -> None:
        """Crop-and-re-OCR empty boxes on sparse pages, then dedup nearby matches."""
        sparse_structured = {
            p: pages_structured[p] for p in page_nums if p not in per_box_pages
        }
        if not sparse_structured:
            return

        await self.refine_uncertain(
            sparse_structured,
            images_dict,
            asyncio.Semaphore(max(1, concurrency)),
            progress,
            self_correction,
            binarize,
            dual_engine,
            cancel_check=cancel_check,
            decoded_get=decoded_get,
        )

    async def refine_uncertain(
        self,
        sparse_structured: dict[int, PageBoxes],
        images_dict: dict[int, str],
        semaphore: asyncio.Semaphore,
        progress: ProgressCallback | None,
        self_correction: bool = False,
        binarize: bool = False,
        dual_engine: bool = False,
        cancel_check: CancelCheck | None = None,
        decoded_get: Callable[[int], Image.Image | None] | None = None,
    ) -> None:
        """Re-OCR empty refinable boxes across sparse pages."""
        targets: list[tuple[int, int, BBox]] = []
        for p_num, aligned in sparse_structured.items():
            for idx, (bbox, text) in enumerate(aligned):
                if not text.strip() and _is_refinable(bbox):
                    targets.append((p_num, idx, bbox))

        if not targets:
            return

        total = len(targets)
        await notify(
            progress, "refine", 0, total, f"Refining {total} uncertain boxes..."
        )

        # Audit M-domain 1: decode all needed pages in parallel rather than
        # serially awaiting each one. ``decoded_get`` short-circuits the
        # cache hit; remaining pages fan out across the thread pool.
        page_images: dict[int, Image.Image] = {}
        pages_needed = {p_num for p_num, _, _ in targets}
        to_decode: list[int] = []
        for p_num in pages_needed:
            if decoded_get is not None:
                cached = decoded_get(p_num)
                if cached is not None:
                    page_images[p_num] = cached
                    continue
            to_decode.append(p_num)
        if to_decode:
            decoded = await asyncio.gather(
                *(
                    asyncio.to_thread(
                        decode_base64_image, images_dict[p_num], mode="RGB"
                    )
                    for p_num in to_decode
                )
            )
            for p_num, image in zip(to_decode, decoded, strict=True):
                page_images[p_num] = image

        # Iteration 1 (perf): batch per-page crop+JPEG-encode instead of
        # submitting one ``asyncio.to_thread`` call per box. Group targets
        # by page, validate coordinates per target, then submit ONE
        # batched crop call per page. The executor round-trip drops from
        # N (per box) to 1 (per page); the VLM calls below still fan out
        # concurrently across all targets.
        per_page_targets: dict[int, list[tuple[int, BBox]]] = {}
        for p_num, idx, bbox in targets:
            per_page_targets.setdefault(p_num, []).append((idx, bbox))

        # ``slot_map[p_num][k]`` is the original ``(idx, bbox)`` for the
        # k-th crop result of that page. Built first so we can validate
        # all bboxes synchronously (cheap) and then issue one async
        # batched crop per page.
        slot_map: dict[int, list[tuple[int, BBox, BBox]]] = {}
        page_crops: dict[int, list[str | None]] = {}
        for p_num, items in per_page_targets.items():
            validated = [
                (idx, bbox, validate_bbox_coordinates(bbox, clamp=True))
                for idx, bbox in items
            ]
            slot_map[p_num] = validated
            page_crops[p_num] = await asyncio.to_thread(
                crop_many_for_ocr_from_image,
                page_images[p_num],
                [safe_bbox for _, _, safe_bbox in validated],
            )

        is_cancelled = False

        async def refine_one(
            p_num: int, idx: int, bbox: BBox, crop_b64: str | None
        ) -> tuple[int, int, str]:
            if is_cancelled:
                raise OCRCancelled("Refine cancelled before processing box.")
            try:
                if crop_b64 is None:
                    return p_num, idx, ""
                async with semaphore:
                    if is_cancelled:
                        raise OCRCancelled("Refine cancelled before processing box.")
                    text = await self.ocr_processor.perform_ocr_on_crop(
                        crop_b64,
                        self_correction=self_correction,
                        binarize=binarize,
                        dual_engine=dual_engine,
                    )
                return p_num, idx, text
            except CircuitOpenError:
                raise
            except LLMBalanceError:
                raise
            except OCRCancelled:
                raise
            except Exception as e:
                logger.warning(
                    "Refine failed for page %s box %s: %s: %s",
                    p_num,
                    idx,
                    type(e).__name__,
                    e,
                )
                return p_num, idx, ""

        completed = 0
        refined_indices: dict[int, set[int]] = defaultdict(set)
        try:
            async with asyncio.TaskGroup() as tg:
                tasks = [
                    tg.create_task(
                        refine_one(p_num, idx, bbox, page_crops[p_num][slot])
                    )
                    for p_num, items in slot_map.items()
                    for slot, (idx, bbox, _) in enumerate(items)
                ]
                for coro in asyncio.as_completed(tasks):
                    p_num, idx, text = await coro
                    bbox_cur, _ = sparse_structured[p_num][idx]
                    sparse_structured[p_num][idx] = (bbox_cur, text.strip())
                    refined_indices[p_num].add(idx)
                    completed += 1

                    if cancel_check is not None and cancel_check():
                        is_cancelled = True
                        raise OCRCancelled(
                            f"OCR cancelled after refine box {completed}/{total}."
                        )

                    await notify(
                        progress,
                        "refine",
                        completed,
                        total,
                        f"Refining boxes ({completed}/{total})",
                    )
        except* OCRCancelled as eg:
            raise eg.exceptions[0] from None
        except* CircuitOpenError as eg:
            raise eg.exceptions[0] from None
        except* LLMBalanceError as eg:
            raise eg.exceptions[0] from None

        for p_num, idxs in refined_indices.items():
            _drop_refined_duplicates(sparse_structured[p_num], idxs)
