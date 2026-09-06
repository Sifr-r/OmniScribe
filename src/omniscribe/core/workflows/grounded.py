from __future__ import annotations

import dataclasses
import logging
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING

from omniscribe.core.document import BBox, SpellcheckMode
from omniscribe.core.grounded import (
    GroundedBlock,
    GroundedOCRBackend,
    GroundedResponse,
    RepairableGroundedBackend,
)
from omniscribe.core.ocr.resilience import CircuitOpenError
from omniscribe.core.ocr_quality import TrustOrchestrator
from omniscribe.core.processors import DocumentProcessor
from omniscribe.core.workflows.base import (
    AnyOutputWriter,
    CancelCheck,
    EngineBase,
    OCRCancelled,
    PagesData,
    ProgressCallback,
    WarningCallback,
    notify,
)
from omniscribe.core.workflows.repair import (
    PageRepairSummary,
    QualityRepairLoop,
    RepairOptions,
    emit_job_repair_summary,
)
from omniscribe.core.workflows.utils import _estimate_confidence

if TYPE_CHECKING:
    from omniscribe.core.callbacks import BlockCallbackSet


logger = logging.getLogger(__name__)


class GroundedEngine(EngineBase):
    def __init__(
        self,
        grounded_backend: GroundedOCRBackend,
        output_writer: AnyOutputWriter,
        document_processors: Sequence[DocumentProcessor] | None = None,
        block_callbacks: BlockCallbackSet | None = None,
        trust_orchestrator: TrustOrchestrator | None = None,
    ) -> None:
        # Phase B (review M2) — the grounded path also accepts the
        # callback set for symmetry with HybridEngine. The current
        # execute() doesn't yet emit per-block events (only the
        # generic `progress` callback); that parity work is a
        # follow-up. Wiring the parameter through now means
        # `OCRPipeline(grounded_backend=..., block_callbacks=...)`
        # doesn't have to grow a special case.
        # Phase 2 — same ``trust_orchestrator`` passthrough. The
        # grounded backend doesn't surface page images, so the
        # orchestrator receives ``page_image=None`` per call — the
        # sub-modules that *need* pixel access (watermark, length
        # plausibility) degrade to their non-pixel fallback.
        super().__init__(
            output_writer=output_writer,
            document_processors=document_processors,
            block_callbacks=block_callbacks,
            trust_orchestrator=trust_orchestrator,
        )
        self.grounded_backend = grounded_backend

    async def _emit_block_callbacks(
        self,
        response: GroundedResponse,
    ) -> None:
        """Drive the per-block / per-page observer hooks from the backend response.

        Mirrors :meth:`HybridEngine._ocr_pages` so the UI sees the same
        ``block_complete`` + ``page_complete`` frames for both engines
        (Phase B review M2 wired the parameter through; this method is
        the parity work the docstring originally punted on).
        """
        pages_data = self._accumulate_pages(response.blocks)
        for page_index in sorted(pages_data):
            await self._emit_page_callbacks(page_index, pages_data[page_index])

    async def _finalize(
        self,
        *,
        input_path: str,
        output_path: str,
        response: GroundedResponse,
        pages_data: PagesData,
        page_nums: list[int],
        spellcheck: SpellcheckMode,
        cross_page: bool,
        dpi: int,
        progress: ProgressCallback | None,
        trust_model_id: str,
    ) -> dict[int, list[str]]:
        """Build the DocumentResult, apply trust, and emit.

        Folds the inline ``block_metadata_overlays`` build +
        ``_build_document_result`` + ``_apply_trust`` + ``_emit`` tail
        of :meth:`execute` into a single helper (audit catalog) so
        ``execute()`` is a phase driver rather than a bookkeeping
        owner.

        The grounded path produces ``block_metadata_overlays`` directly
        from the backend response instead of going through the
        ``_build_document_result`` indirection; the annotation here is
        the only place the overlay shape is documented in the
        codebase.
        """
        block_metadata_overlays: dict[int, list[dict[str, object]]] = {}
        for block in response.blocks:
            page_overlays = block_metadata_overlays.setdefault(block.page_index, [])
            page_overlays.append(
                {"label": block.label, "image_bytes": block.image_bytes}
            )

        document_result = await self._build_document_result(
            pages_data=pages_data,
            page_nums=page_nums,
            source_path=input_path,
            source_processor="grounded",
            spellcheck=spellcheck,
            cross_page=cross_page,
            page_metadata_overlays=None,
            block_metadata_overlays=block_metadata_overlays,
        )

        # The grounded path doesn't have ``trust_images_dict`` (the
        # backend never renders page images), so the orchestrator
        # receives ``page_image=None``; watermark / length-plausibility
        # sub-modules fall back to their non-pixel defaults.
        document_result = await self._apply_trust(
            document_result, model_id=trust_model_id
        )

        return await self._emit(
            input_path=input_path,
            output_path=output_path,
            document_result=document_result,
            dpi=dpi,
            progress=progress,
        )

    async def execute(
        self,
        input_path: str,
        output_path: str,
        *,
        dpi: int,
        spellcheck: SpellcheckMode = SpellcheckMode.NONE,
        cross_page: bool = False,
        progress: ProgressCallback | None = None,
        on_warning: WarningCallback | None = None,
        trust_model_id: str = "unknown",
        repair_options: RepairOptions | None = None,
        cancel_check: CancelCheck | None = None,
    ) -> dict[int, list[str]]:
        """
        Grounded path: the backend returns (bbox, text) pairs directly.
        No Surya, no DP, no refine — the model already knows where the text is.
        """
        self._reset_run_state()

        # Phase 3 fix (report §2.1) — same cooperative cancel check as
        # the hybrid path. The grounded backend may produce a single
        # large VLM call that internally fans out across pages; we
        # consult the cancel channel up front so an already-cancelled
        # request can short-circuit before paying for the call.
        if cancel_check is not None and cancel_check():
            raise OCRCancelled("Grounded OCR cancelled before backend call.")

        response = await self.grounded_backend.ocr_document(
            input_path, progress=progress, on_warning=on_warning
        )
        if response.failed_pages:
            self.last_failed_pages.extend(response.failed_pages)

        pages_data = self._accumulate_pages(response.blocks)
        page_nums = sorted(pages_data)

        # Phase B review M2 — drive per-block / per-page observers so
        # the grounded path emits the same WebSocket frames as the
        # hybrid path. Done before `_build_document_result` so the
        # block order matches what the backend produced.
        await self._emit_block_callbacks(response)

        # --- Quality repair (spec §3.2) ---
        # Grounded blocks carry no confidence, so the loop estimates it
        # from text quality. Only backends that match
        # :class:`RepairableGroundedBackend` (i.e. expose ``ocr_crop``)
        # can be repaired — others skip silently and keep their text.
        # F1.14 audit fix: replaced the ``hasattr(..., "ocr_crop")``
        # duck-type + ``# type: ignore[attr-defined]`` at the call site
        # with a typed ``isinstance`` check against the new Protocol,
        # so mypy validates the shape at construction time.
        if (
            repair_options is not None
            and repair_options.enabled
            and isinstance(self.grounded_backend, RepairableGroundedBackend)
        ):
            repair_summaries = await self._repair_blocks(
                input_path=input_path,
                response=response,
                repair_options=repair_options,
                progress=progress,
                on_warning=on_warning,
            )
            await emit_job_repair_summary(self.block_callbacks, repair_summaries)
            # Re-accumulate so DocumentResult and embedding see the
            # repaired text (blocks were mutated in place).
            pages_data = self._accumulate_pages(response.blocks)

        return await self._finalize(
            input_path=input_path,
            output_path=output_path,
            response=response,
            pages_data=pages_data,
            page_nums=page_nums,
            spellcheck=spellcheck,
            cross_page=cross_page,
            dpi=dpi,
            progress=progress,
            trust_model_id=trust_model_id,
        )

    @staticmethod
    def _accumulate_pages(
        blocks: Iterable[GroundedBlock],
    ) -> PagesData:
        """Group backend blocks by page index, preserving backend ordering."""
        pages_data: PagesData = {}
        for block in blocks:
            # GroundedBlock.bbox is list[float]; PagesData expects the canonical
            # BBox tuple. Unpack + repack here so the rest of the pipeline sees
            # the immutable shape (matches the public DocumentBlock contract).
            x0, y0, x1, y1 = block.bbox
            bbox: BBox = (float(x0), float(y0), float(x1), float(y1))
            pages_data.setdefault(block.page_index, []).append((bbox, block.text))
        return pages_data

    async def _repair_blocks(
        self,
        *,
        input_path: str,
        response: GroundedResponse,
        repair_options: RepairOptions,
        progress: ProgressCallback | None,
        on_warning: WarningCallback | None = None,
    ) -> list[PageRepairSummary]:
        """Re-OCR below-target grounded blocks via the backend's ``ocr_crop``.

        Accepted revisions are written back onto the ``GroundedBlock``
        objects so the caller can re-accumulate ``pages_data`` and every
        downstream stage sees the repaired text. The caller must have
        feature-detected ``ocr_crop`` already (``isinstance`` check
        against :class:`RepairableGroundedBackend` in ``execute``).
        """
        loop = QualityRepairLoop(repair_options)
        cb = self.block_callbacks
        from typing import cast

        crop_ocr = cast(RepairableGroundedBackend, self.grounded_backend).ocr_crop

        by_page: dict[int, list[GroundedBlock]] = {}
        for block in response.blocks:
            by_page.setdefault(block.page_index, []).append(block)

        targets = sum(
            1
            for blocks in by_page.values()
            for b in blocks
            if b.text.strip() and _estimate_confidence(b.text) < repair_options.target
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

        summaries: list[PageRepairSummary] = []
        completed = 0
        obj_to_idx = {id(b): idx for idx, b in enumerate(response.blocks)}
        for page_idx in sorted(by_page):
            page_blocks_objs = by_page[page_idx]
            page_blocks: list[tuple[tuple[float, float, float, float], str]] = [
                (
                    (
                        float(b.bbox[0]),
                        float(b.bbox[1]),
                        float(b.bbox[2]),
                        float(b.bbox[3]),
                    ),
                    b.text,
                )
                for b in page_blocks_objs
            ]

            async def re_ocr(
                block_idx: int,
                bbox: tuple[float, float, float, float],
                *,
                previous_text: str = "",
                attempt: int = 1,
                _page: int = page_idx,
            ) -> str:
                nonlocal completed
                try:
                    text: str = await crop_ocr(
                        input_path,
                        _page,
                        bbox,
                        previous_text=previous_text,
                        attempt=attempt,
                    )
                except CircuitOpenError:
                    raise
                except Exception as exc:
                    # Spec §3.2 graceful degradation: warning frame out,
                    # then re-raise so repair_page keeps the best-so-far
                    # text and the job continues.
                    if on_warning is not None:
                        await on_warning(_page, exc)
                    raise
                completed += 1
                await notify(
                    progress,
                    "refine",
                    min(completed, targets),
                    targets,
                    f"Repairing below-target blocks ({min(completed, targets)}/{targets})",
                )
                return text

            summary = await loop.repair_page(
                page_idx=page_idx,
                page_blocks=page_blocks,
                re_ocr=re_ocr,
                on_block_retry=cb.on_block_retry,
                on_block_revised=cb.on_block_revised,
            )
            # M9 audit fix: persist accepted revisions onto GroundedBlock
            # objects without in-place mutation. Use dataclasses.replace
            # so the block can be made ``frozen=True`` later without
            # rewriting this site. The new object is written back into
            # both the local per-page list and the canonical
            # ``response.blocks`` so downstream stages see the repair.
            for i, (obj, (_, text)) in enumerate(
                zip(page_blocks_objs, page_blocks, strict=True)
            ):
                new_obj = dataclasses.replace(obj, text=text)
                page_blocks_objs[i] = new_obj
                idx = obj_to_idx.get(id(obj))
                if idx is not None:
                    response.blocks[idx] = new_obj
                    obj_to_idx[id(new_obj)] = idx
            summaries.append(summary)
            if cb.on_quality_summary is not None:
                await cb.on_quality_summary(
                    "page",
                    page_idx,
                    summary.target,
                    summary.avg_confidence,
                    summary.repaired_count,
                    summary.below_target_count,
                )
        return summaries
