"""Quality repair loop — selective re-OCR of below-target blocks (spec §3.2).

Engine-agnostic: :class:`QualityRepairLoop` owns the
estimate → re-OCR → accept decision while each engine supplies the
per-block re-OCR coroutine (hybrid: crop → ``perform_ocr_on_crop``;
grounded: ``PromptedGroundedOCR.ocr_crop``). Observer callbacks carry
the ``block_retry`` / ``block_revised`` / ``quality_summary`` events to
whatever transport the API layer wires up (see ``core/callbacks.py``).

Default-off policy: engines only run the loop when they receive a
non-``None`` :class:`RepairOptions`, so every in-process
``OCRPipeline`` caller keeps the pre-loop behavior byte-for-byte.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from omniscribe.core.callbacks import (
    BlockCallbackSet,
    BlockRetryCallback,
    BlockRevisedCallback,
)
from omniscribe.core.ocr.resilience import CircuitOpenError
from omniscribe.core.recall.text_layer import (
    TEXT_LAYER_AGREEMENT_TARGET,
    token_agreement,
)
from omniscribe.core.workflows.utils import (
    WELL_FORMED_CONFIDENCE,
    _estimate_confidence,
)

logger = logging.getLogger(__name__)

#: Per-block re-OCR primitive supplied by the engine. Engines must
#: accept ``(block_idx, bbox, *, previous_text: str = "", attempt: int = 1)``;
#: the loop forwards the block's current text and the 1-based retry
#: number so the engine can build an informed repair prompt.
ReOcrBlock = Callable[..., Awaitable[str]]


@dataclass(frozen=True)
class RepairOptions:
    """Toggle and bounds for the quality repair loop."""

    #: Engines treat ``repair_options=None`` as off; constructing
    #: ``RepairOptions()`` enables the loop since this defaults to True.
    enabled: bool = True
    target: float = 0.98
    max_retries: int = 2


@dataclass(frozen=True)
class PageRepairSummary:
    """End-of-page repair statistics (feeds the ``quality_summary`` frame)."""

    page_idx: int
    target: float
    block_count: int
    avg_confidence: float
    repaired_count: int
    below_target_count: int


def _text_layer_mismatch(text: str, conf: float, layer_text: str | None) -> bool:
    """True when a well-formed block disagrees with the PDF text layer.

    The shape heuristic caps at :data:`WELL_FORMED_CONFIDENCE`, so a fluent
    hallucination is indistinguishable from real text to ``repair_page``'s
    confidence gate. When the page's embedded text layer is available, a
    token-agreement share below :data:`TEXT_LAYER_AGREEMENT_TARGET` marks
    the block as a likely hallucination worth one re-OCR pass.
    """
    if layer_text is None or conf < WELL_FORMED_CONFIDENCE:
        return False
    return token_agreement(text, layer_text) < TEXT_LAYER_AGREEMENT_TARGET


class QualityRepairLoop:
    """Re-OCRs below-target blocks until they reach the target, stall, or exhaust retries."""

    def __init__(
        self,
        options: RepairOptions | None = None,
        confidence_estimator: Callable[[str], float | None] | None = None,
    ) -> None:
        self.options = options if options is not None else RepairOptions()
        self._estimate = (
            confidence_estimator
            if confidence_estimator is not None
            else _estimate_confidence
        )

    async def repair_page(
        self,
        *,
        page_idx: int,
        page_blocks: list[tuple[tuple[float, float, float, float], str]],
        re_ocr: ReOcrBlock,
        on_block_retry: BlockRetryCallback | None = None,
        on_block_revised: BlockRevisedCallback | None = None,
        layer_text: str | None = None,
    ) -> PageRepairSummary:
        """Repair one page in place; ``page_blocks`` entries are updated on accept.

        Empty blocks are skipped entirely (the refine stage already owns
        empty-box recovery) and are excluded from the summary stats.

        ``layer_text`` (the PDF's embedded text layer for this page, when
        available) enables the fluent-hallucination trigger: a well-formed
        block whose OCR tokens barely appear in the layer is repaired even
        though the shape heuristic scores it 0.99.
        """
        opts = self.options
        if not opts.enabled:
            return PageRepairSummary(
                page_idx=page_idx,
                target=opts.target,
                block_count=0,
                avg_confidence=1.0,
                repaired_count=0,
                below_target_count=0,
            )

        repaired_count = 0
        below_target_count = 0
        confidences: list[float] = []
        for block_idx, (bbox, text) in enumerate(page_blocks):
            if not text or not text.strip():
                continue
            # F1.8 audit fix: a custom estimator may return None ("I
            # don't know"). Coerce to 0.0 (worst-case confidence) so
            # the downstream comparisons stay type-safe and the
            # block is processed with the most pessimistic score.
            estimated = self._estimate(text)
            conf: float = 0.0 if estimated is None else estimated
            if conf >= opts.target and not _text_layer_mismatch(text, conf, layer_text):
                confidences.append(conf)
                continue

            initial_conf = conf
            for attempt in range(1, opts.max_retries + 1):
                if on_block_retry is not None:
                    await on_block_retry(
                        page_idx, block_idx, attempt, conf, opts.target
                    )
                try:
                    new_text = await re_ocr(
                        block_idx, bbox, previous_text=text, attempt=attempt
                    )
                except CircuitOpenError:
                    # Infrastructure-level fail-fast: never swallow the
                    # breaker signal — the whole run aborts just like
                    # the refine stage does.
                    raise
                except Exception as e:
                    logger.warning(
                        "Quality repair failed for page %s block %s: %s: %s",
                        page_idx,
                        block_idx,
                        type(e).__name__,
                        e,
                    )
                    break

                new_estimated = self._estimate(new_text)
                new_conf: float = 0.0 if new_estimated is None else new_estimated
                # F1.8 audit fix: a custom estimator may return None
                # for "I don't know"; treating None as 0.0 here keeps
                # the stall guard from crashing on None ordering.
                if new_conf <= conf:
                    break  # stall guard: keep the best text seen so far
                page_blocks[block_idx] = (bbox, new_text.strip())
                # The accepted revision becomes the next attempt's
                # "previous text" so the re-OCR prompt stays informed.
                text = new_text.strip()
                if on_block_revised is not None:
                    await on_block_revised(
                        page_idx,
                        block_idx,
                        attempt,
                        list(bbox),
                        new_text.strip(),
                        "text",
                        new_conf,
                    )
                conf = new_conf
                if conf >= opts.target:
                    break

            confidences.append(conf)
            if conf > initial_conf:
                repaired_count += 1
            if conf < opts.target:
                below_target_count += 1

        avg_confidence = sum(confidences) / len(confidences) if confidences else 1.0
        return PageRepairSummary(
            page_idx=page_idx,
            target=opts.target,
            block_count=len(confidences),
            avg_confidence=avg_confidence,
            repaired_count=repaired_count,
            below_target_count=below_target_count,
        )


async def emit_job_repair_summary(
    cb: BlockCallbackSet | None, summaries: Sequence[PageRepairSummary]
) -> None:
    """Aggregate per-page repair stats into one job-scope summary frame.

    Block-weighted average: a page with more non-empty blocks counts
    more. A run with no non-empty blocks reports a perfect 1.0 (nothing
    below target). No-op unless a ``quality_summary`` observer is wired.
    Shared by both engines.
    """
    if cb is None or cb.on_quality_summary is None or not summaries:
        return
    total_blocks = sum(s.block_count for s in summaries)
    avg_confidence = (
        sum(s.avg_confidence * s.block_count for s in summaries) / total_blocks
        if total_blocks
        else 1.0
    )
    await cb.on_quality_summary(
        "job",
        None,
        summaries[0].target,
        avg_confidence,
        sum(s.repaired_count for s in summaries),
        sum(s.below_target_count for s in summaries),
    )


__all__ = [
    "PageRepairSummary",
    "QualityRepairLoop",
    "ReOcrBlock",
    "RepairOptions",
    "emit_job_repair_summary",
]
