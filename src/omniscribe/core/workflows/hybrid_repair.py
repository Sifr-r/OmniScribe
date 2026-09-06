"""Phase 4b — quality repair of below-target blocks for the hybrid engine.

Audit catalog (Sprint 6 long-file split): the repair phase logic
(``_count_repair_targets`` + ``_repair_single_page`` +
``_repair_pages``) used to live inside ``HybridEngine`` in
``core/workflows/hybrid.py``. It was 125 LOC of bespoke logic —
unlike the other phases which are thin delegators over the
stage classes (``HybridConverter``, ``HybridLayoutDetector``,
``HybridOcrRunner``, ``HybridRefiner``) — and had no test
coverage as methods of ``HybridEngine`` (it was only exercised
through the top-level ``execute()``).

This module is the repair phase half: a small driver function
``run_repair_phase`` that the engine calls from ``execute()``,
plus a ``repair_single_page`` helper that runs one page's
below-target blocks through the ``QualityRepairLoop``. The
non-public ``_count_repair_targets`` is folded into the driver
because it has no other caller.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol

from PIL import Image

from omniscribe.core.callbacks import BlockCallbackSet
from omniscribe.core.ocr import OCRProcessor
from omniscribe.core.ocr.resilience import CircuitOpenError
from omniscribe.core.workflows.base import (
    PageBoxes,
    ProgressCallback,
    WarningCallback,
    notify,
)
from omniscribe.core.workflows.repair import (
    PageRepairSummary,
    QualityRepairLoop,
    RepairOptions,
)
from omniscribe.core.workflows.utils import (
    _decode_page_image,
    _estimate_confidence,
)
from omniscribe.utils.image import crop_for_ocr_from_image

logger = logging.getLogger(__name__)


class _RepairEngineHost(Protocol):
    """Duck-typed surface the repair phase needs from the host engine.

    The repair phase is engine-agnostic in principle; the only
    attributes it actually reads are ``ocr_processor`` and
    ``block_callbacks`` (both public-ish on HybridEngine). Defining
    them as a Protocol keeps the mypy contract honest while still
    letting the host engine be any class that exposes these two
    attributes.
    """

    ocr_processor: OCRProcessor
    block_callbacks: BlockCallbackSet


async def run_repair_phase(
    *,
    engine: _RepairEngineHost,
    pages_structured: dict[int, PageBoxes],
    images_dict: dict[int, str],
    page_nums: Sequence[int],
    repair_options: RepairOptions,
    concurrency: int,
    progress: ProgressCallback | None,
    on_warning: WarningCallback | None = None,
    decoded_get: Callable[[int], Image.Image | None],
) -> list[PageRepairSummary]:
    """Phase 4b — re-OCR non-empty blocks below the quality target.

    Audit catalog: split out of ``HybridEngine._repair_pages`` so the
    engine's ``execute()`` is a clean phase driver. The shared
    ``completed`` counter is carried via a single-element list.

    ``engine`` is duck-typed against ``HybridEngine`` for the
    ``ocr_processor``, ``block_callbacks``, and ``_decoded_get`` /
    ``_decoded_put`` access. ``concurrency`` is currently a no-op
    (the per-page loop is sequential by design — repair re-OCR is
    one box at a time per page, and the box-level parallelism lives
    inside ``QualityRepairLoop``).
    """
    loop = QualityRepairLoop(repair_options)
    cb = engine.block_callbacks

    targets = _count_repair_targets(
        page_nums=page_nums,
        pages_structured=pages_structured,
        target=repair_options.target,
    )
    if not targets:
        return []
    await notify(
        progress,
        "refine",
        0,
        targets,
        f"Repairing {targets} below-target blocks...",
    )

    completed_box = [0]
    summaries: list[PageRepairSummary] = []
    # Memoized lazy decode: a page's image is decoded at most once, and
    # only when a below-target block on that page actually needs a crop.
    # Pages without repair targets are never decoded.
    decoded: dict[int, Image.Image] = {}

    async def decode_page(page_num: int) -> Image.Image:
        if page_num in decoded:
            return decoded[page_num]
        cached = decoded_get(page_num)
        if cached is None:
            cached = await asyncio.to_thread(_decode_page_image, images_dict[page_num])
        decoded[page_num] = cached
        return cached

    for p_num in page_nums:
        aligned = pages_structured.get(p_num)
        if not aligned:
            continue

        summary = await repair_single_page(
            engine=engine,
            p_num=p_num,
            aligned=aligned,
            get_page_image=decode_page,
            loop=loop,
            cb=cb,
            completed_box=completed_box,
            targets=targets,
            on_warning=on_warning,
            progress=progress,
        )
        summaries.append(summary)
    return summaries


def _count_repair_targets(
    *,
    page_nums: Sequence[int],
    pages_structured: dict[int, PageBoxes],
    target: float,
) -> int:
    """Count non-empty blocks whose estimated confidence is below ``target``."""
    return sum(
        1
        for p_num in page_nums
        for _, text in pages_structured.get(p_num, [])
        if text.strip() and _estimate_confidence(text) < target
    )


async def repair_single_page(
    *,
    engine: _RepairEngineHost,
    p_num: int,
    aligned: list,
    get_page_image: Callable[[int], Awaitable[Image.Image]],
    loop: QualityRepairLoop,
    cb: BlockCallbackSet,
    completed_box: list[int],
    targets: int,
    on_warning: WarningCallback | None,
    progress: ProgressCallback | None,
) -> PageRepairSummary:
    """Re-OCR one page's below-target blocks; emit per-page summary.

    ``completed_box`` is a single-element mutable counter shared
    across the per-page loop (audit catalog: nonlocal ``completed``
    carried the global count for the progress emit; the list
    pattern is the same one the OCR quality orchestrator uses
    for ``fallback_used_box``). ``get_page_image`` is lazy so pages
    without below-target blocks never decode their image.
    """

    async def re_ocr(
        block_idx: int,
        bbox: tuple[float, float, float, float],
        *,
        previous_text: str = "",
        attempt: int = 1,
    ) -> str:
        page_image = await get_page_image(p_num)
        crop_b64 = await asyncio.to_thread(
            crop_for_ocr_from_image, page_image, list(bbox)
        )
        if crop_b64 is None:
            return ""
        hint = (
            f"\nREPAIR PASS {attempt}: your previous reading of this region was:\n"
            f"{previous_text}\nIt was rejected as unreliable (confidence below "
            "target or garbled). Re-read the crop carefully; keep every line, "
            "fix misreads, do not add commentary."
            if previous_text
            else ""
        )
        temperature = 0.1 + 0.1 * (attempt - 1) if attempt > 1 else None
        try:
            text = await engine.ocr_processor.perform_ocr_on_crop(
                crop_b64, repair_hint=hint or None, temperature=temperature
            )
        except CircuitOpenError:
            raise
        except Exception as exc:
            if on_warning is not None:
                await on_warning(p_num, exc)
            raise
        completed_box[0] += 1
        await notify(
            progress,
            "refine",
            min(completed_box[0], targets),
            targets,
            f"Repairing below-target blocks ({min(completed_box[0], targets)}/{targets})",
        )
        return text

    summary = await loop.repair_page(
        page_idx=p_num,
        page_blocks=aligned,
        re_ocr=re_ocr,
        on_block_retry=cb.on_block_retry,
        on_block_revised=cb.on_block_revised,
    )
    if cb.on_quality_summary is not None:
        await cb.on_quality_summary(
            "page",
            p_num,
            summary.target,
            summary.avg_confidence,
            summary.repaired_count,
            summary.below_target_count,
        )
    return summary


__all__ = ["repair_single_page", "run_repair_phase"]
