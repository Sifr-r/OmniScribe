from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import logging
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING

from PIL import Image

from omniscribe.core.aligner import HybridAligner
from omniscribe.core.document import BBox, DenseMode
from omniscribe.core.workflows.base import (
    PageBoxes,
    PagesData,
    ProgressCallback,
    notify,
)
from omniscribe.core.workflows.utils import (
    DETECT_CHUNK_SIZE,
    _decode_page_image,
    validate_bbox_coordinates,
)

if TYPE_CHECKING:
    from omniscribe.core.recall.text_layer import PdfTextLayerRecall
    from omniscribe.core.recall.whitespace import WhitespaceRecallBooster

logger = logging.getLogger("omniscribe.core.workflows.hybrid")


def decode_chunk_bytes(
    images_dict: Mapping[int, str],
    chunk_pages: Sequence[int],
    on_decoded: Callable[[int, Image.Image], None] | None = None,
) -> list[bytes]:
    """Decode a batch of base64 page images to bytes (synchronous helper).

    Runs inside ``asyncio.to_thread`` so the CPU-bound decode does not block the
    event loop. See refactor §1.3 in ``docs/superpowers/specs/deep_refactor_report.md``.

    When ``on_decoded`` is provided, it is invoked with each ``(page_num, image)``
    so downstream stages (``_ocr_per_box``, ``_refine_uncertain``) can skip a
    second base64 → image decode.
    """
    result: list[bytes] = []
    for p in chunk_pages:
        b64 = images_dict[p]
        raw = base64.b64decode(b64)
        result.append(raw)
        if on_decoded is not None:
            with contextlib.suppress(Exception):
                on_decoded(p, Image.open(io.BytesIO(raw)).convert("RGB"))
    return result


class HybridLayoutDetector:
    """Handles layout detection and recall boosting for the hybrid workflow."""

    def __init__(
        self,
        aligner: HybridAligner,
        recall_booster: WhitespaceRecallBooster | None = None,
        text_layer_recall: PdfTextLayerRecall | None = None,
    ) -> None:
        self.aligner = aligner
        self.recall_booster = recall_booster
        self.text_layer_recall = text_layer_recall

    async def detect_layout(
        self,
        *,
        images_dict: dict[int, str],
        page_nums: Sequence[int],
        progress: ProgressCallback | None,
        decoded_put: Callable[[int, Image.Image], None] | None = None,
        decoded_get: Callable[[int], Image.Image | None] | None = None,
    ) -> dict[int, PageBoxes]:
        """Run batched layout detection and seed each page with empty text."""
        await notify(
            progress, "detect", 0, 1, f"Detecting layout for {len(page_nums)} pages..."
        )

        batch_boxes: list[list[BBox]] = []
        booster = self.recall_booster
        recall_pages_touched = 0
        recall_boxes_added = 0
        dropped_before = getattr(booster, "candidates_dropped", 0)
        text_layer = self.text_layer_recall
        tl_pages_touched = 0
        tl_boxes_added = 0
        tl_dropped_before = getattr(text_layer, "candidates_dropped", 0)

        tl_open = (
            text_layer is not None
            and (
                getattr(text_layer, "_doc", None) is not None
                or getattr(text_layer, "opened", False)
                or (
                    getattr(text_layer, "opened_with", None) is not None
                    and not getattr(text_layer, "closed", False)
                )
            )
            and getattr(text_layer, "enabled", True)
        )
        try:
            for i in range(0, len(page_nums), DETECT_CHUNK_SIZE):
                chunk_pages = page_nums[i : i + DETECT_CHUNK_SIZE]
                chunk_bytes = await asyncio.to_thread(
                    decode_chunk_bytes, images_dict, chunk_pages, decoded_put
                )
                chunk_boxes = await asyncio.to_thread(
                    self.aligner.get_detected_boxes_batch, chunk_bytes
                )
                if booster is not None and getattr(booster, "enabled", True):
                    chunk_boxes, touched, added = await self.apply_recall(
                        chunk_pages=chunk_pages,
                        images_dict=images_dict,
                        chunk_boxes=chunk_boxes,
                        decoded_get=decoded_get,
                        decoded_put=decoded_put,
                    )
                    recall_pages_touched += touched
                    recall_boxes_added += added
                if tl_open:
                    chunk_boxes, touched, added = await self.apply_text_layer_recall(
                        chunk_pages=chunk_pages,
                        chunk_boxes=chunk_boxes,
                    )
                    tl_pages_touched += touched
                    tl_boxes_added += added
                batch_boxes.extend(chunk_boxes)
                await notify(
                    progress,
                    "detect",
                    min(i + DETECT_CHUNK_SIZE, len(page_nums)),
                    len(page_nums),
                    f"Detecting layout ({min(i + DETECT_CHUNK_SIZE, len(page_nums))}/{len(page_nums)})...",
                )
        finally:
            if tl_open and text_layer is not None:
                await asyncio.to_thread(text_layer.close)

        pages_structured: dict[int, PageBoxes] = {
            p: [(box, "") for box in batch_boxes[i]] for i, p in enumerate(page_nums)
        }
        if booster is not None:
            dropped = getattr(booster, "candidates_dropped", 0) - dropped_before
            logger.info(
                "Whitespace recall summary: %d box(es) added on %d of %d page(s); "
                "%d candidate(s) dropped by filters",
                recall_boxes_added,
                recall_pages_touched,
                len(page_nums),
                dropped,
            )
        if text_layer is not None:
            tl_dropped = (
                getattr(text_layer, "candidates_dropped", 0) - tl_dropped_before
            )
            logger.info(
                "Text-layer recall summary: %d box(es) added on %d of %d page(s); "
                "%d line(s) dropped by dedup/cap",
                tl_boxes_added,
                tl_pages_touched,
                len(page_nums),
                tl_dropped,
            )
        await notify(progress, "detect", 1, 1, "Layout detection complete.")
        return pages_structured

    async def apply_recall(
        self,
        *,
        chunk_pages: Sequence[int],
        images_dict: dict[int, str],
        chunk_boxes: list[list[BBox]],
        decoded_get: Callable[[int], Image.Image | None] | None = None,
        decoded_put: Callable[[int, Image.Image], None] | None = None,
    ) -> tuple[list[list[BBox]], int, int]:
        """Merge whitespace-recall boxes into each page's Surya boxes."""
        booster = self.recall_booster
        if booster is None:
            return chunk_boxes, 0, 0
        merged: list[list[BBox]] = []
        pages_touched = 0
        boxes_added = 0
        for p_num, boxes in zip(chunk_pages, chunk_boxes, strict=False):
            try:
                image = decoded_get(p_num) if decoded_get is not None else None
                if image is None:
                    image = await asyncio.to_thread(
                        _decode_page_image, images_dict[p_num]
                    )
                    if decoded_put is not None:
                        decoded_put(p_num, image)
                extra = await asyncio.to_thread(booster.supplement, image, boxes)
                extra = [validate_bbox_coordinates(b, clamp=True) for b in extra]
            except Exception as e:
                logger.warning(
                    "Whitespace recall failed for page %s: %s: %s",
                    p_num,
                    type(e).__name__,
                    e,
                )
                merged.append(boxes)
                continue
            if extra:
                logger.debug(
                    "Whitespace recall added %d box(es) on page %s",
                    len(extra),
                    p_num,
                )
                pages_touched += 1
                boxes_added += len(extra)
                boxes = sorted([*boxes, *extra], key=lambda b: (b[1], b[0]))
            merged.append(boxes)
        return merged, pages_touched, boxes_added

    async def apply_text_layer_recall(
        self,
        *,
        chunk_pages: Sequence[int],
        chunk_boxes: list[list[BBox]],
    ) -> tuple[list[list[BBox]], int, int]:
        """Merge text-layer recall boxes into each page's merged boxes."""
        source = self.text_layer_recall
        if source is None:
            return chunk_boxes, 0, 0
        merged: list[list[BBox]] = []
        pages_touched = 0
        boxes_added = 0
        for p_num, boxes in zip(chunk_pages, chunk_boxes, strict=False):
            try:
                extra = await asyncio.to_thread(source.supplement, p_num, boxes)
                extra = [validate_bbox_coordinates(b, clamp=True) for b in extra]
            except Exception as e:
                logger.warning(
                    "Text-layer recall failed for page %s: %s: %s",
                    p_num,
                    type(e).__name__,
                    e,
                )
                merged.append(boxes)
                continue
            if extra:
                logger.debug(
                    "Text-layer recall added %d box(es) on page %s",
                    len(extra),
                    p_num,
                )
                pages_touched += 1
                boxes_added += len(extra)
                boxes = sorted([*boxes, *extra], key=lambda b: (b[1], b[0]))
            merged.append(boxes)
        return merged, pages_touched, boxes_added

    def select_dense_pages(
        self,
        pages_structured: PagesData,
        page_nums: Sequence[int],
        dense_mode: DenseMode = DenseMode.AUTO,
        dense_threshold: int = 60,
    ) -> set[int]:
        """Decide which pages take the per-box OCR path (vs full-page OCR)."""
        per_box_pages: set[int] = set()
        for p_num in page_nums:
            n_boxes = len(pages_structured[p_num])
            if dense_mode == DenseMode.ALWAYS or (
                dense_mode == DenseMode.AUTO and n_boxes > dense_threshold
            ):
                per_box_pages.add(p_num)
        return per_box_pages
