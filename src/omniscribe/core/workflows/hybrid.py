from __future__ import annotations

import asyncio
import logging
import uuid
from collections import OrderedDict
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from PIL import Image

from omniscribe.core.aligner import HybridAligner
from omniscribe.core.document import BBox, DenseMode, SpellcheckMode
from omniscribe.core.imaging.page_preprocess import (
    PagePreprocessingOptions,
    PagePreprocessor,
)
from omniscribe.core.ocr import OCRProcessor
from omniscribe.core.ocr_quality import TrustOrchestrator
from omniscribe.core.ocr_quality.routing import (
    QualityRoutingOptions,
    QualityRoutingPolicy,
)
from omniscribe.core.pdf import PDFHandler
from omniscribe.core.processors import DocumentProcessor
from omniscribe.core.recall.text_layer import PdfTextLayerRecall
from omniscribe.core.recall.whitespace import WhitespaceRecallBooster
from omniscribe.core.workflows.base import (
    AnyOutputWriter,
    CancelCheck,
    EngineBase,
    OCRCancelled,
    PageBoxes,
    PagesData,
    ProgressCallback,
    WarningCallback,
)
from omniscribe.core.workflows.hybrid_repair import run_repair_phase
from omniscribe.core.workflows.repair import (
    RepairOptions,
    emit_job_repair_summary,
)
from omniscribe.core.workflows.stages import (
    HybridConverter,
    HybridLayoutDetector,
    HybridOcrRunner,
    HybridRefiner,
)
from omniscribe.core.workflows.utils import (
    DETECT_CHUNK_SIZE,
    _decode_page_image,
    _drop_refined_duplicates,
    _estimate_confidence,
    _is_refinable,
    parse_page_range,
    validate_bbox_coordinates,
)

if TYPE_CHECKING:
    from omniscribe.core.callbacks import BlockCallbackSet

logger = logging.getLogger(__name__)

# Phase 3 finding 2.3 — bound the per-page decoded-image LRU to keep
# long-document runs from holding a PIL.Image per page for the whole run.
# Invariant (CQ-4): must stay >= DETECT_CHUNK_SIZE (workflows.utils).
_DECODED_CACHE_MAX_ENTRIES = 16

__all__ = [
    "DETECT_CHUNK_SIZE",
    "_DECODED_CACHE_MAX_ENTRIES",
    "HybridEngine",
    "_decode_page_image",
    "_drop_refined_duplicates",
    "_estimate_confidence",
    "_is_refinable",
    "parse_page_range",
    "validate_bbox_coordinates",
]


class HybridEngine(EngineBase):
    def __init__(
        self,
        aligner: HybridAligner,
        ocr_processor: OCRProcessor,
        pdf_handler: PDFHandler,
        output_writer: AnyOutputWriter,
        document_processors: Sequence[DocumentProcessor] | None = None,
        page_preprocessor: PagePreprocessor | None = None,
        block_callbacks: BlockCallbackSet | None = None,
        trust_orchestrator: TrustOrchestrator | None = None,
        recall_booster: WhitespaceRecallBooster | None = None,
        text_layer_recall: PdfTextLayerRecall | None = None,
    ) -> None:
        super().__init__(
            output_writer=output_writer,
            document_processors=document_processors,
            block_callbacks=block_callbacks,
            trust_orchestrator=trust_orchestrator,
        )
        self.aligner = aligner
        self.ocr_processor = ocr_processor
        self.pdf_handler = pdf_handler
        self.page_preprocessor = page_preprocessor
        self.recall_booster = recall_booster
        self.text_layer_recall = text_layer_recall
        self._current_run_id: str = uuid.uuid4().hex
        self._decoded_cache: OrderedDict[tuple[str, int], Image.Image] = OrderedDict()

        self.converter = HybridConverter(
            pdf_handler=self.pdf_handler,
            page_preprocessor=self.page_preprocessor,
        )
        self.layout_detector = HybridLayoutDetector(
            aligner=self.aligner,
            recall_booster=self.recall_booster,
            text_layer_recall=self.text_layer_recall,
        )
        self.ocr_runner = HybridOcrRunner(
            aligner=self.aligner,
            ocr_processor=self.ocr_processor,
            block_callbacks=self.block_callbacks,
            last_failed_pages=self.last_failed_pages,
        )
        self.refiner = HybridRefiner(
            ocr_processor=self.ocr_processor,
        )

    def _decoded_get(
        self,
        page_num: int | tuple[str, int],
        *,
        run_id: str | None = None,
    ) -> Image.Image | None:
        """Return the cached image for ``(run_id, page_num)`` and mark it most-recently-used."""
        if isinstance(page_num, tuple):
            key = page_num
        else:
            rid = run_id if run_id is not None else self._current_run_id
            key = (rid, page_num)
        cached = self._decoded_cache.get(key)
        if cached is not None:
            self._decoded_cache.move_to_end(key)
        return cached

    def _decoded_put(
        self,
        page_num: int | tuple[str, int],
        image: Image.Image,
        *,
        run_id: str | None = None,
    ) -> None:
        """Cache ``image`` for ``(run_id, page_num)`` and evict the LRU entry if over capacity."""
        if isinstance(page_num, tuple):
            key = page_num
        else:
            rid = run_id if run_id is not None else self._current_run_id
            key = (rid, page_num)
        self._decoded_cache[key] = image
        self._decoded_cache.move_to_end(key)
        if len(self._decoded_cache) > _DECODED_CACHE_MAX_ENTRIES:
            self._decoded_cache.popitem(last=False)

    def _reset_run_state(self) -> None:
        """Clear run-scoped state. Call at the top of every ``execute``.

        Sub-stage handlers (converter: ``HybridConverter``,
        layout: ``HybridLayoutDetector``, runner: ``HybridOcrRunner``,
        refiner: ``HybridRefiner``) maintain no unreset mutable state
        across executions (§4.38). Stage dependencies are set once in
        ``__init__`` and not re-pushed per run (Phase 3.3, 4.5). The
        ``last_failed_pages`` list is shared by reference between
        ``HybridEngine`` and ``HybridOcrRunner``, so in-place
        mutations are visible without re-injection. Subclasses that
        reassign ``self.last_failed_pages`` must also update
        ``self.ocr_runner.last_failed_pages`` explicitly.
        """
        super()._reset_run_state()
        self.ocr_runner.last_failed_pages = self.last_failed_pages
        self._decoded_cache = OrderedDict()
        self._current_run_id = uuid.uuid4().hex

    async def execute(
        self,
        input_path: str,
        output_path: str,
        *,
        dpi: int = 200,
        pages: str | None = None,
        concurrency: int = 1,
        refine: bool = True,
        max_image_dim: int = 1024,
        dense_threshold: int = 60,
        dense_mode: DenseMode = DenseMode.AUTO,
        self_correction: bool = False,
        binarize: bool = False,
        dual_engine: bool = False,
        spellcheck: SpellcheckMode = SpellcheckMode.NONE,
        cross_page: bool = False,
        preprocessing_options: PagePreprocessingOptions | None = None,
        quality_routing_options: QualityRoutingOptions | None = None,
        progress: ProgressCallback | None = None,
        on_warning: WarningCallback | None = None,
        trust_model_id: str = "unknown",
        trust_images_dict: dict[int, str] | None = None,
        repair_options: RepairOptions | None = None,
        cancel_check: CancelCheck | None = None,
    ) -> dict[int, list[str]]:
        if not isinstance(dense_mode, DenseMode):
            raise ValueError(
                f"dense_mode must be a DenseMode instance; got {dense_mode!r}"
            )

        self._reset_run_state()
        run_id = uuid.uuid4().hex
        self._current_run_id = run_id

        def decoded_get(p_num: int) -> Image.Image | None:
            return self._decoded_get(p_num, run_id=run_id)

        def decoded_put(p_num: int, image: Image.Image) -> None:
            self._decoded_put(p_num, image, run_id=run_id)

        # --- Phase 1: convert + optional preprocessing ---
        images_dict, page_nums, preprocessing_metadata = await self._convert_pages(
            input_path=input_path,
            dpi=dpi,
            max_image_dim=max_image_dim,
            pages=pages,
            preprocessing_options=preprocessing_options,
            progress=progress,
        )

        # --- Phase 2: batched layout detection (cancel-gate lives inside) ---
        pages_structured = await self._detect_layout(
            images_dict=images_dict,
            page_nums=page_nums,
            progress=progress,
            input_path=input_path,
            cancel_check=cancel_check,
            decoded_put=decoded_put,
            decoded_get=decoded_get,
        )

        per_box_pages = self._select_dense_pages(
            pages_structured=pages_structured,
            page_nums=page_nums,
            dense_mode=dense_mode,
            dense_threshold=dense_threshold,
        )

        # --- Phase 3: concurrent OCR (sparse + dense) ---
        await self._ocr_pages(
            images_dict=images_dict,
            pages_structured=pages_structured,
            page_nums=page_nums,
            per_box_pages=per_box_pages,
            concurrency=concurrency,
            self_correction=self_correction,
            binarize=binarize,
            dual_engine=dual_engine,
            progress=progress,
            on_warning=on_warning,
            cancel_check=cancel_check,
            decoded_get=decoded_get,
        )

        # --- Phase 4: refine empty boxes on the sparse pages ---
        if refine:
            await self._refine_pages(
                pages_structured=pages_structured,
                images_dict=images_dict,
                page_nums=page_nums,
                per_box_pages=per_box_pages,
                concurrency=concurrency,
                self_correction=self_correction,
                binarize=binarize,
                dual_engine=dual_engine,
                progress=progress,
                cancel_check=cancel_check,
                decoded_get=decoded_get,
            )

        # --- Phase 4b: quality repair of below-target blocks (spec §3.2) ---
        if repair_options is not None and repair_options.enabled:
            text_layers = await self._collect_text_layers(input_path, page_nums)
            repair_summaries = await run_repair_phase(
                engine=self,
                pages_structured=pages_structured,
                images_dict=images_dict,
                page_nums=page_nums,
                repair_options=repair_options,
                concurrency=concurrency,
                progress=progress,
                on_warning=on_warning,
                decoded_get=decoded_get,
                text_layers=text_layers,
            )
            await emit_job_repair_summary(self.block_callbacks, repair_summaries)

        # --- Phase 5: assemble, post-process, route, emit (cancel-gate lives inside) ---
        return await self._finalize(
            input_path=input_path,
            output_path=output_path,
            pages_structured=pages_structured,
            page_nums=page_nums,
            preprocessing_metadata=preprocessing_metadata,
            spellcheck=spellcheck,
            cross_page=cross_page,
            quality_routing_options=quality_routing_options,
            dpi=dpi,
            progress=progress,
            trust_model_id=trust_model_id,
            trust_images_dict=images_dict.copy(),
            cancel_check=cancel_check,
        )

    async def _collect_text_layers(
        self,
        input_path: str,
        page_nums: Sequence[int],
    ) -> dict[int, str] | None:
        """Per-page embedded-text-layer text for the repair phase's
        fluent-hallucination trigger. ``None`` when unavailable (image
        inputs, disabled recall, scan-like PDFs) so the trigger stays off.

        Opens the layer fresh: the layout phase already closed it, and a
        second short-lived open is cheaper than holding the document
        across the whole OCR phase.
        """
        tl = self.text_layer_recall
        if tl is None or not tl.enabled or not input_path:
            return None
        opened = await asyncio.to_thread(tl.open, input_path)
        if not opened:
            return None
        try:
            layers: dict[int, str] = {}
            for p_num in page_nums:
                text = await asyncio.to_thread(tl.page_text, p_num)
                if text.strip():
                    layers[p_num] = text
            return layers or None
        finally:
            await asyncio.to_thread(tl.close)

    async def _convert_pages(
        self,
        *,
        input_path: str,
        dpi: int,
        max_image_dim: int,
        pages: str | None,
        preprocessing_options: PagePreprocessingOptions | None,
        progress: ProgressCallback | None,
        rasterize_batch_size: int = 8,
    ) -> tuple[dict[int, str], list[int], dict[int, dict[str, object]]]:
        # Phase 3.3 (4.5): removed ``self.converter.pdf_handler = self.pdf_handler``
        # and ``self.converter.page_preprocessor = self.page_preprocessor``.
        # The constructor passes both to ``HybridConverter`` (L111-114).
        return await self.converter.convert_pages(
            input_path=input_path,
            dpi=dpi,
            max_image_dim=max_image_dim,
            pages=pages,
            preprocessing_options=preprocessing_options,
            progress=progress,
            rasterize_batch_size=rasterize_batch_size,
        )

    async def _detect_layout(
        self,
        *,
        images_dict: dict[int, str],
        page_nums: Sequence[int],
        progress: ProgressCallback | None,
        input_path: str = "",
        cancel_check: CancelCheck | None = None,
        decoded_put: Callable[[int, Image.Image], None] | None = None,
        decoded_get: Callable[[int], Image.Image | None] | None = None,
    ) -> dict[int, PageBoxes]:
        # Audit catalog: between-phase cancel checks used to live in
        # execute(); folded into the next-phase helper so execute() is
        # a clean phase driver.
        if cancel_check is not None and cancel_check():
            raise OCRCancelled("OCR cancelled before layout detection.")
        # Phase 3.3 (4.5): removed three re-injections. The constructor
        # passes ``aligner``, ``recall_booster``, and ``text_layer_recall``
        # to ``HybridLayoutDetector`` (L115-119) — re-pushing per
        # ``detect_layout()`` call is decorative.
        tl = self.text_layer_recall
        tl_open = False
        if tl is not None and input_path:
            tl_open = await asyncio.to_thread(tl.open, input_path)
        try:
            return await self.layout_detector.detect_layout(
                images_dict=images_dict,
                page_nums=page_nums,
                progress=progress,
                decoded_put=decoded_put or self._decoded_put,
                decoded_get=decoded_get or self._decoded_get,
            )
        finally:
            if tl_open and tl is not None:
                await asyncio.to_thread(tl.close)

    async def _apply_recall(
        self,
        *,
        chunk_pages: Sequence[int],
        images_dict: dict[int, str],
        chunk_boxes: list[list[BBox]],
        decoded_get: Callable[[int], Image.Image | None] | None = None,
        decoded_put: Callable[[int, Image.Image], None] | None = None,
    ) -> tuple[list[list[BBox]], int, int]:
        # Phase 3.3 (4.5): removed re-injection of ``recall_booster``.
        return await self.layout_detector.apply_recall(
            chunk_pages=chunk_pages,
            images_dict=images_dict,
            chunk_boxes=chunk_boxes,
            decoded_get=decoded_get or self._decoded_get,
            decoded_put=decoded_put or self._decoded_put,
        )

    async def _apply_text_layer_recall(
        self,
        *,
        chunk_pages: Sequence[int],
        chunk_boxes: list[list[BBox]],
    ) -> tuple[list[list[BBox]], int, int]:
        # Phase 3.3 (4.5): removed re-injection of ``text_layer_recall``.
        return await self.layout_detector.apply_text_layer_recall(
            chunk_pages=chunk_pages,
            chunk_boxes=chunk_boxes,
        )

    def _select_dense_pages(
        self,
        *,
        pages_structured: PagesData,
        page_nums: Sequence[int],
        dense_mode: str,
        dense_threshold: int,
    ) -> set[int]:
        return self.layout_detector.select_dense_pages(
            pages_structured=pages_structured,
            page_nums=page_nums,
            dense_mode=dense_mode,
            dense_threshold=dense_threshold,
        )

    async def _ocr_pages(
        self,
        *,
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
    ) -> None:
        # Phase 3.3 (4.5): removed four re-injections. The constructor
        # passes ``aligner``, ``ocr_processor``, ``block_callbacks``,
        # and ``last_failed_pages`` (by reference) to ``HybridOcrRunner``
        # (L120-124). The list is shared by reference; in-place
        # mutations are visible without re-pushing.
        #
        # Phase 6 (D15, audit 4.15/6.45): removed the dead
        # ``trust_images_dict`` parameter that was accepted here only
        # to be discarded. The trust layer decodes page images
        # inside ``_apply_trust`` (see ``base.py``) when needed;
        # ``_ocr_pages`` never uses them.
        return await self.ocr_runner.ocr_pages(
            images_dict=images_dict.copy(),
            pages_structured=pages_structured,
            page_nums=page_nums,
            per_box_pages=per_box_pages,
            concurrency=concurrency,
            self_correction=self_correction,
            binarize=binarize,
            dual_engine=dual_engine,
            progress=progress,
            on_warning=on_warning,
            cancel_check=cancel_check,
            decoded_get=decoded_get or self._decoded_get,
            emit_page_callbacks=self._emit_page_callbacks,
        )

    async def _ocr_per_box(
        self,
        image_b64: str,
        structured: PageBoxes,
        semaphore: asyncio.Semaphore,
        self_correction: bool = False,
        binarize: bool = False,
        dual_engine: bool = False,
        page_image: Image.Image | None = None,
    ) -> PageBoxes:
        # Phase 3.3 (4.5): removed re-injection of ``ocr_processor``.
        return await self.ocr_runner.ocr_per_box(
            image_b64=image_b64,
            structured=structured,
            semaphore=semaphore,
            self_correction=self_correction,
            binarize=binarize,
            dual_engine=dual_engine,
            page_image=page_image,
        )

    async def _refine_pages(
        self,
        *,
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
        # Phase 3.3 (4.5): removed re-injection of ``ocr_processor``.
        return await self.refiner.refine_pages(
            pages_structured=pages_structured,
            images_dict=images_dict,
            page_nums=page_nums,
            per_box_pages=per_box_pages,
            concurrency=concurrency,
            self_correction=self_correction,
            binarize=binarize,
            dual_engine=dual_engine,
            progress=progress,
            cancel_check=cancel_check,
            decoded_get=decoded_get or self._decoded_get,
        )

    async def _refine_uncertain(
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
        # Phase 3.3 (4.5): removed re-injection of ``ocr_processor``.
        return await self.refiner.refine_uncertain(
            sparse_structured=sparse_structured,
            images_dict=images_dict,
            semaphore=semaphore,
            progress=progress,
            self_correction=self_correction,
            binarize=binarize,
            dual_engine=dual_engine,
            cancel_check=cancel_check,
            decoded_get=decoded_get or self._decoded_get,
        )

    async def _finalize(
        self,
        *,
        input_path: str,
        output_path: str,
        pages_structured: dict[int, PageBoxes],
        page_nums: Sequence[int],
        preprocessing_metadata: dict[int, dict[str, object]],
        spellcheck: SpellcheckMode,
        cross_page: bool,
        quality_routing_options: QualityRoutingOptions | None,
        dpi: int,
        progress: ProgressCallback | None,
        trust_model_id: str = "unknown",
        trust_images_dict: dict[int, str] | None = None,
        cancel_check: CancelCheck | None = None,
    ) -> dict[int, list[str]]:
        """Post-process, run document processors, apply hybrid-only quality routing, emit.

        ``cancel_check`` is consulted at entry — it acts as the
        post-OCR cancel gate that used to live inline in
        :meth:`execute` (audit catalog). Any later phase that wants to
        short-circuit (refine / repair) does its own per-chunk check
        via the underlying runner.
        """
        if cancel_check is not None and cancel_check():
            raise OCRCancelled("OCR cancelled after OCR loop.")
        document_result = await self._build_document_result(
            pages_data=pages_structured,
            page_nums=page_nums,
            source_path=input_path,
            source_processor="hybrid",
            spellcheck=spellcheck,
            cross_page=cross_page,
            page_metadata_overlays=preprocessing_metadata,
        )

        document_result = await self._apply_trust(
            document_result,
            model_id=trust_model_id,
            trust_images_dict=trust_images_dict,
        )

        if quality_routing_options is not None and quality_routing_options.enabled:
            document_result = QualityRoutingPolicy().apply(
                document_result, quality_routing_options
            )

        return await self._emit(
            input_path=input_path,
            output_path=output_path,
            document_result=document_result,
            dpi=dpi,
            progress=progress,
            page_nums=list(page_nums),
        )
