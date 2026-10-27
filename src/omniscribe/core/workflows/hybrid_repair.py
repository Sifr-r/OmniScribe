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
from omniscribe.core.imaging.utils import decode_base64_image
from omniscribe.core.ocr import OCRProcessor
from omniscribe.core.ocr.exceptions import LLMBalanceError
from omniscribe.core.ocr.resilience import CircuitOpenError
from omniscribe.core.workflows.base import (
    OCRCancelled,
    PageBoxes,
    ProgressCallback,
    WarningCallback,
    notify,
)
from omniscribe.core.workflows.repair import (
    PageRepairSummary,
    QualityRepairLoop,
    RepairOptions,
    _text_layer_mismatch,
)
from omniscribe.core.workflows.utils import (
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


def _repair_failure(group: BaseExceptionGroup) -> BaseException:
    """Pick the exception a failed page repair should surface as.

    ``asyncio.TaskGroup`` wraps every child failure in an exception group,
    so without unwrapping the caller sees a group rather than the original
    exception. A group also defeats the type-specific handlers upstream:
    ``except OCRCancelled`` drives the 503 cancel handshake in
    ``plugins/ocr/routes.py`` and ``except CircuitOpenError`` drives the
    breaker. Audit P0-1 already recorded a group leaking as a generic 500
    from the OCR stage for exactly this reason.

    The control-flow exceptions keep precedence, in the same order the OCR
    stage unwraps them, so a cancellation still short-circuits the VLM
    spend. Everything else surfaces as its own type — which is what the
    serial loop this fan-out replaced raised.
    """
    leaves: list[BaseException] = []
    pending: list[BaseException] = list(group.exceptions)
    while pending:
        item = pending.pop(0)
        if isinstance(item, BaseExceptionGroup):
            pending = list(item.exceptions) + pending
        else:
            leaves.append(item)
    for exc_type in (OCRCancelled, CircuitOpenError, LLMBalanceError):
        for leaf in leaves:
            if isinstance(leaf, exc_type):
                return leaf
    return leaves[0] if leaves else group


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
    text_layers: dict[int, str] | None = None,
) -> list[PageRepairSummary]:
    """Phase 4b — re-OCR non-empty blocks below the quality target.

    Audit catalog: split out of ``HybridEngine._repair_pages`` so the
    engine's ``execute()`` is a clean phase driver. The shared
    ``completed`` counter is carried via a single-element list.

    ``text_layers`` maps page number → the PDF's embedded text-layer text
    for that page (when the input is a digital PDF). It enables the
    fluent-hallucination trigger: well-formed blocks whose tokens don't
    appear in the layer become repair targets too.

    ``engine`` is duck-typed against ``HybridEngine`` for the
    ``ocr_processor``, ``block_callbacks``, and ``_decoded_get`` /
    ``_decoded_put`` access.

    ``concurrency`` bounds how many pages repair at once. It used to be
    a no-op: the per-page loop was sequential by design, on the
    reasoning that repair re-OCR is one box at a time per page and the
    box-level parallelism lives inside ``QualityRepairLoop``. That
    reasoning conflated the two levels — the per-page loop was serial
    too, so a run's repair wall-clock was the SUM of every page's VLM
    latency while those network-bound calls sat idle waiting on a
    response. The loop now fans out through the same ``Semaphore`` +
    ``asyncio.as_completed`` shape the main OCR loop in ``stages/ocr.py``
    already uses, keeping ``QualityRepairLoop``'s in-page parallelism
    untouched.

    The bound is load-bearing, not decorative: every in-flight page
    holds its decoded image plus a per-bbox crop cache, so an unbounded
    fan-out would trade the latency win for a memory blow-up.
    ``concurrency <= 1`` collapses back to the old one-page-at-a-time
    behaviour, which callers that deliberately pass 1 still rely on.

    Ordering: results are written back into a slot array indexed by
    ``page_nums`` position, so the returned list keeps its positional
    correspondence to ``page_nums`` no matter which page finishes
    first. Pages with no aligned blocks are skipped, as before.

    Cancellation: this phase takes no ``cancel_check`` of its own — a
    cancelled run arrives as ``asyncio`` cancellation of the awaiting
    task. ``TaskGroup`` cancels and awaits every in-flight page before
    re-raising, so a cancelled run terminates promptly and leaves no
    dangling tasks.
    """
    loop = QualityRepairLoop(repair_options)
    cb = engine.block_callbacks

    targets = _count_repair_targets(
        page_nums=page_nums,
        pages_structured=pages_structured,
        target=repair_options.target,
        text_layers=text_layers,
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
    # Serialises the shared counter and its progress emit across the
    # concurrent per-page tasks; see ``repair_single_page``.
    counter_lock = asyncio.Lock()
    # Memoized lazy decode: a page's image is decoded at most once, and
    # only when a below-target block on that page actually needs a crop.
    # Pages without repair targets are never decoded. Concurrent pages
    # never contend for the same key — a page only ever asks for its own
    # image and repairs its blocks sequentially.
    decoded: dict[int, Image.Image] = {}

    async def decode_page(page_num: int) -> Image.Image:
        if page_num in decoded:
            return decoded[page_num]
        cached = decoded_get(page_num)
        if cached is None:
            cached = await asyncio.to_thread(
                decode_base64_image, images_dict[page_num], mode="RGB"
            )
        decoded[page_num] = cached
        return cached

    # (slot, page number, aligned blocks) in ``page_nums`` order. ``slot``
    # is the page's position among the repairable pages and is what the
    # summary is filed under, restoring the ordering the serial loop got
    # for free from append order.
    ordered: list[tuple[int, int, PageBoxes]] = []
    for p_num in page_nums:
        aligned = pages_structured.get(p_num)
        if not aligned:
            continue
        ordered.append((len(ordered), p_num, aligned))
    if not ordered:
        return []

    # A self-hosted VLM (localhost / LAN) is serialised by the OCR stage
    # because it cannot absorb concurrent requests. The repair fan-out
    # re-OCRs the same pages, so it has to honour the same guard — otherwise
    # a local backend that the OCR stage deliberately serialises now
    # receives up to ``concurrency`` concurrent re-OCR calls.
    api_base = str(getattr(engine.ocr_processor, "api_base", "") or "").lower()
    effective_concurrency = concurrency
    if any(h in api_base for h in ("localhost", "127.0.0.1", "::1", "192.168.")):
        effective_concurrency = min(concurrency, 1)
    semaphore = asyncio.Semaphore(max(1, effective_concurrency))

    async def repair_slot(
        slot: int, p_num: int, aligned: PageBoxes
    ) -> tuple[int, PageRepairSummary]:
        async with semaphore:
            summary = await repair_single_page(
                engine=engine,
                p_num=p_num,
                aligned=aligned,
                get_page_image=decode_page,
                loop=loop,
                cb=cb,
                completed_box=completed_box,
                counter_lock=counter_lock,
                targets=targets,
                on_warning=on_warning,
                progress=progress,
                text_layers=text_layers,
            )
            return slot, summary

    results: list[PageRepairSummary | None] = [None] * len(ordered)
    try:
        async with asyncio.TaskGroup() as tg:
            tasks = [
                tg.create_task(repair_slot(slot, p_num, aligned))
                for slot, p_num, aligned in ordered
            ]
            for coro in asyncio.as_completed(tasks):
                slot, summary = await coro
                results[slot] = summary
    except BaseExceptionGroup as group:
        raise _repair_failure(group) from None

    # The TaskGroup only exits normally when every page completed, so a
    # hole means a page task was cancelled on its own. Dropping it would
    # silently misalign the list callers index positionally against
    # ``page_nums``; fail loudly instead.
    stranded = [slot for slot, entry in enumerate(results) if entry is None]
    if stranded:
        raise OCRCancelled(
            f"Repair cancelled for {len(stranded)} of {len(ordered)} page(s) "
            f"before completing."
        )

    summaries: list[PageRepairSummary] = [s for s in results if s is not None]
    return summaries


def _count_repair_targets(
    *,
    page_nums: Sequence[int],
    pages_structured: dict[int, PageBoxes],
    target: float,
    text_layers: dict[int, str] | None = None,
) -> int:
    """Count blocks that need re-OCR.

    A block counts when its estimated confidence is below ``target``, or —
    when the page's text layer is known — when the block is well-formed
    (shape heuristic 0.99) but its tokens barely appear in the layer
    (fluent-hallucination trigger).
    """
    count = 0
    for p_num in page_nums:
        layer_text = text_layers.get(p_num) if text_layers is not None else None
        for _, text in pages_structured.get(p_num, []):
            if not text.strip():
                continue
            est = _estimate_confidence(text)
            if est < target or (
                layer_text is not None and _text_layer_mismatch(text, est, layer_text)
            ):
                count += 1
    return count


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
    text_layers: dict[int, str] | None = None,
    counter_lock: asyncio.Lock | None = None,
) -> PageRepairSummary:
    """Re-OCR one page's below-target blocks; emit per-page summary.

    ``completed_box`` is a single-element mutable counter shared
    across the per-page loop (audit catalog: nonlocal ``completed``
    carried the global count for the progress emit; the list
    pattern is the same one the OCR quality orchestrator uses
    for ``fallback_used_box``). ``get_page_image`` is lazy so pages
    without below-target blocks never decode their image.

    ``counter_lock`` guards ``completed_box`` and its progress emit
    when the caller fans out across pages. The single-element list is
    only safe by construction while the caller is serial: concurrent
    pages would otherwise sit inside the progress callback at the
    same time and a stale count could be published after a newer one.
    The increment and the emit are therefore one critical section. A
    standalone call with no lock gets a private uncontended one, which
    never blocks and never yields — the pre-fan-out behaviour.

    Iteration 2 (perf): ``loop.repair_page`` retries each below-target
    block up to ``max_retries`` times with the same bbox. The previous
    implementation re-cropped the same bbox on every retry
    (``asyncio.to_thread(crop_for_ocr_from_image, ...)`` per
    ``re_ocr`` call). The crop work is now memoized per bbox — the
    first ``re_ocr`` for a bbox crops+JPEG-encodes and caches the
    result; retries (and any duplicate-block retries on the same
    page) hit the cache. ``crop_b64`` is byte-identical for retries
    because the bbox is unchanged across attempts (only the prompt
    ``previous_text`` changes).

    The lazy-decode contract is preserved: pages where every block
    is above-target (the common case) never call ``get_page_image``
    and never crop, identical to the previous behaviour.
    """

    crop_cache: dict[tuple[float, float, float, float], str | None] = {}
    lock = counter_lock if counter_lock is not None else asyncio.Lock()

    async def re_ocr(
        block_idx: int,
        bbox: tuple[float, float, float, float],
        *,
        previous_text: str = "",
        attempt: int = 1,
    ) -> str:
        # Memoize crop by bbox. Retries within the same page share the
        # same bbox so the cache hits. ``bbox not in crop_cache``
        # distinguishes "cache miss" from "cached blank crop".
        # ``aligner.align_text`` may return bboxes as lists (the
        # HybridAligner does, and so do test stubs) so normalize to a
        # tuple before using as a dict key — lists are unhashable and
        # would raise ``TypeError`` here, which the repair loop's
        # generic-except handler would then swallow and silently skip
        # the retry (the audit-flagged bug fixed below).
        bbox_key: tuple[float, float, float, float] = (
            tuple(bbox) if not isinstance(bbox, tuple) else bbox
        )
        if bbox_key not in crop_cache:
            page_image = await get_page_image(p_num)
            crop_b64 = await asyncio.to_thread(
                crop_for_ocr_from_image, page_image, list(bbox_key)
            )
            crop_cache[bbox_key] = crop_b64
        crop_b64 = crop_cache[bbox_key]
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
        except LLMBalanceError:
            raise
        except Exception as exc:
            if on_warning is not None:
                await on_warning(p_num, exc)
            raise
        async with lock:
            completed_box[0] += 1
            done = min(completed_box[0], targets)
            await notify(
                progress,
                "refine",
                done,
                targets,
                f"Repairing below-target blocks ({done}/{targets})",
            )
        return text

    summary = await loop.repair_page(
        page_idx=p_num,
        page_blocks=aligned,
        re_ocr=re_ocr,
        on_block_retry=cb.on_block_retry,
        on_block_revised=cb.on_block_revised,
        layer_text=text_layers.get(p_num) if text_layers is not None else None,
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
