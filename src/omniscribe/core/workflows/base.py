from __future__ import annotations

import asyncio
import dataclasses
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from PIL import Image

    from omniscribe.core.callbacks import BlockCallbackSet
    from omniscribe.core.document import (
        BBox,
        DocumentPage,
        DocumentResult,
        SpellcheckMode,
    )
    from omniscribe.core.ocr_quality import TrustOrchestrator
    from omniscribe.core.postprocess import DictionaryPostProcessor
    from omniscribe.core.processors import DocumentProcessor

logger = logging.getLogger(__name__)

# Runtime import (no cycle: utils imports base only under TYPE_CHECKING) —
# feeds from_pages_data's confidence_fn so the trust layer sees real signal.
from omniscribe.core.workflows.utils import _estimate_confidence  # noqa: E402

ProgressCallback = Callable[[str, int, int, str], Awaitable[None]]
WarningCallback = Callable[[int, BaseException], Awaitable[None]]
OutputWriter = Callable[[str, str, dict, int], None]


@runtime_checkable
class DocumentResultWriter(Protocol):
    """Rich output writer that receives the full DocumentResult.

    Writers implementing this protocol get access to block kinds,
    confidence, metadata, and reading order — everything the legacy
    ``{page: [(bbox, text)]}`` conversion drops. The engine prefers
    this interface when the injected writer supports it and falls back
    to the legacy 4-arg callable otherwise.
    """

    def write_document_result(
        self,
        input_path: str,
        output_path: str,
        document_result: DocumentResult,
        dpi: int,
        page_nums: Sequence[int] | None = None,
    ) -> None: ...


#: Accepted output-writer shapes: the legacy 4-arg callable or a rich writer.
AnyOutputWriter = OutputWriter | DocumentResultWriter


async def notify(
    cb: ProgressCallback | None, stage: str, current: int, total: int, message: str
) -> None:
    if cb is not None:
        await cb(stage, current, total, message)


def _spellcheck_page_sync(
    processor: DictionaryPostProcessor, page_blocks: PageBoxes
) -> PageBoxes:
    """Sync helper for :meth:`EngineBase._run_spellcheck`.

    ``DictionaryPostProcessor.correct_text`` is a CPU-bound PyEnchant
    lookup, so the page-level correction is offloaded to a worker
    thread via :func:`asyncio.to_thread` in the async wrapper. Kept
    top-level (rather than nested) so :func:`asyncio.to_thread` can
    introspect it without holding a closure over ``self``.
    """
    corrected: PageBoxes = []
    for bbox, text in page_blocks:
        if text:
            corrected.append((bbox, processor.correct_text(text)))
        else:
            corrected.append((bbox, text))
    return corrected


PageBoxes = list[tuple[tuple[float, float, float, float], str]]
PagesData = dict[int, PageBoxes]


#: Type of the optional cancel-check callable engines accept on ``execute``.
#: Returns ``True`` when the in-flight run should abort cooperatively at the
#: next page boundary. See :class:`OCRCancelled` and report finding 2.1.
CancelCheck = Callable[[], bool]


class OCRCancelled(BaseException):
    """Raised by the OCR engines when the cooperative cancel-check fires.

    Inherits from :class:`BaseException` (not :class:`Exception`) so the
    per-page isolation blocks in :meth:`HybridEngine._ocr_pages` and
    :meth:`HybridEngine._refine_pages` do not swallow the signal as a
    page-level failure. The API layer catches it and translates it into
    a 503 Service Unavailable with ``cancelled: true`` so the WebSocket
    cancel handshake actually short-circuits the VLM spend.

    Phase 3 fix for report finding 2.1 (HIGH) — see
    ``docs/superpowers/specs/deep_refactor_report.md`` §2.1.
    """


class EngineBase:
    """
    Base class for OCR workflows (Hybrid and Grounded).

    Provides three pieces of shared machinery:

    1. Run-scoped state (``last_document_result``, ``last_failed_pages``) reset
       at the top of every ``execute`` call via :meth:`_reset_run_state`.
    2. Text-only post-processing helpers (``_cross_page_merge``,
       ``_run_spellcheck``).
    3. The post-process → assemble → emit pipeline (:meth:`_build_document_result`
       and :meth:`_emit`) that both engines route their final pages through so
       the output-writing code path lives in exactly one place.

    Subclasses are expected to accept ``output_writer`` and
    ``document_processors`` in their ``__init__`` and forward them via
    ``super().__init__(...)``.
    """

    def __init__(
        self,
        output_writer: AnyOutputWriter,
        document_processors: Sequence[DocumentProcessor] | None = None,
        block_callbacks: BlockCallbackSet | None = None,
        trust_orchestrator: TrustOrchestrator | None = None,
    ) -> None:
        self.output_writer = output_writer
        self.document_processors: tuple[DocumentProcessor, ...] = tuple(
            document_processors or ()
        )
        # Phase B (review M2) — the engine no longer imports the
        # WebSocket manager. Per-block / per-page events flow through
        # the injected callback set; the API layer wires those to
        # whatever transport the deployment uses. `None` means "no
        # observers," which is the right default for in-process
        # programmatic use of `OCRPipeline` (no WebSocket, no
        # listeners, pure engine output).
        from omniscribe.core.callbacks import BlockCallbackSet

        self.block_callbacks: BlockCallbackSet = (
            block_callbacks if block_callbacks is not None else BlockCallbackSet()
        )
        # Phase 2 — the OCR quality trust layer (design §11.2). ``None``
        # means the layer is off; engines treat that as a true no-op
        # (identity passthrough, identical bytes to the pre-Phase-2
        # path). See :func:`omniscribe.core.ocr_quality.build_trust_orchestrator`
        # for the factory. Subclasses drive ``_apply_trust`` (the
        # default is a no-op identity; the engines below override).
        self.trust_orchestrator: TrustOrchestrator | None = trust_orchestrator

        # State populated after a run. Reset by ``_reset_run_state`` at the top
        # of each ``execute``; lifting into the base keeps ``OCRPipeline`` honest
        # about which attributes belong to the engine contract.
        self.last_document_result: DocumentResult | None = None
        self.last_failed_pages: list[int] = []

    def _reset_run_state(self) -> None:
        """Clear run-scoped state. Call at the top of every ``execute``."""
        self.last_document_result = None
        self.last_failed_pages.clear()

    async def _apply_trust(
        self,
        document_result: DocumentResult,
        *,
        model_id: str,
        trust_images_dict: dict[int, str] | None = None,
    ) -> DocumentResult:
        """Apply the trust layer to ``document_result``.

        If ``trust_images_dict`` is provided, page images are decoded on-demand for
        pages present in the map. The orchestrator is invoked fail-open per page.
        """
        if self.trust_orchestrator is None or not document_result.pages:
            return document_result

        from omniscribe.core.document import DocumentResult
        from omniscribe.core.imaging.utils import decode_base64_image

        orchestrator = self.trust_orchestrator

        def _score_page(page: DocumentPage, page_b64: str | None) -> DocumentPage:
            # Audit P2-9: the orchestrator does CPU-heavy pixel work (the
            # watermark scan is pure Python over every pixel) — running it
            # on the event loop stalled every other in-flight page. The
            # whole per-page score now runs on a worker thread; the
            # orchestrator is invoked fail-open exactly as before.
            page_image: Image.Image | None = None
            if page_b64 is not None:
                try:
                    page_image = decode_base64_image(page_b64)
                except Exception:
                    page_image = None

            try:
                new_blocks = orchestrator(
                    list(page.blocks),
                    page_image,
                    model_id=model_id,
                    page_size=None,
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug(
                    "trust orchestrator failed on page %d; falling back: %s",
                    page.page_index,
                    exc,
                )
                new_blocks = list(page.blocks)
            return dataclasses.replace(page, blocks=list(new_blocks))

        scored_pages: list[DocumentPage] = []
        for page in document_result.pages:
            page_b64: str | None = None
            if trust_images_dict and page.page_index in trust_images_dict:
                page_b64 = trust_images_dict[page.page_index]
            scored_pages.append(await asyncio.to_thread(_score_page, page, page_b64))

        return DocumentResult(
            pages=scored_pages,
            source_path=document_result.source_path,
            tree=document_result.tree,
        )

    def _cross_page_merge(
        self,
        pages_structured: PagesData,
        page_nums: Sequence[int],
    ) -> None:
        """
        Post-processing step that inspects the end of each page and merges
        trailing sentences without terminal punctuation into the first line of the
        subsequent page.

        F1.15 audit fix: the previous implementation had two bugs.

        1. It mutated the caller's per-page list in place. A second
           call on the same ``pages_structured`` (e.g. an engine
           that pre-builds the dict, runs cross-page merge, then
           re-runs for a downstream stage) would re-merge an
           *earlier* line of page 1 (now the last non-empty line
           after the first call's tail was emptied) into page 2 —
           producing two empty leading lines on page 1. The fix
           is a shallow-copy + write-back: the outer dict's
           identity is preserved (callers with a reference still
           see updates), but each per-page list is replaced
           wholesale.

        2. The merge is **only** valid when the page-1 last
           non-empty line is also the literal last item in the
           per-page list (``last_idx == len(p1_boxes) - 1``). After
           a previous merge, the trailing empty box is at the end
           and the *next* non-empty line is somewhere earlier in
           the page; re-merging that earlier line is a no-op at
           best, double-empty at worst. The new guard skips the
           merge unless the candidate line is the actual last box
           on the page.
        """
        page_list = list(page_nums)
        for i in range(len(page_list) - 1):
            p1 = page_list[i]
            p2 = page_list[i + 1]

            # Shallow copy the per-page lists so the write-back below
            # does not alias the caller's list. The inner tuples are
            # immutable (bbox tuple, str) so shallow copy is
            # sufficient — no ``deepcopy`` needed.
            p1_boxes = list(pages_structured.get(p1, []))
            last_idx = -1
            for idx in range(len(p1_boxes) - 1, -1, -1):
                if p1_boxes[idx][1].strip():
                    last_idx = idx
                    break

            p2_boxes = list(pages_structured.get(p2, []))
            first_idx = -1
            for idx in range(len(p2_boxes)):
                if p2_boxes[idx][1].strip():
                    first_idx = idx
                    break

            if last_idx != -1 and first_idx != -1:
                # Re-entry guard: only merge when the candidate is the
                # literal last box on page 1. A trailing empty box (the
                # post-merge state from a previous call) means we have
                # already merged and should skip.
                if last_idx != len(p1_boxes) - 1:
                    continue
                _last_bbox, last_text = p1_boxes[last_idx]
                first_bbox, first_text = p2_boxes[first_idx]

                last_text_stripped = last_text.strip()
                # If the last box's text does not end with sentence-ending punctuation, merge them.
                if last_text_stripped and last_text_stripped[-1] not in (".", "!", "?"):
                    merged_text = last_text_stripped + " " + first_text.strip()
                    p2_boxes[first_idx] = (first_bbox, merged_text)
                    p1_boxes[last_idx] = (_last_bbox, "")
                # Write the mutated copies back. The dict identity is
                # preserved (callers with a reference see the change),
                # but the per-page list is a new object so a re-entry
                # would see the trailing empty line and skip.
                pages_structured[p1] = p1_boxes
                pages_structured[p2] = p2_boxes

    async def _emit_page_callbacks(
        self,
        page_index: int,
        page_blocks: Sequence[tuple[BBox, str]],
        confidence_estimator: Callable[[str], float | None] | None = None,
    ) -> None:
        """Drive per-block and per-page observer callbacks for a single page."""
        cb = self.block_callbacks
        if cb.on_block is None and cb.on_page_complete is None:
            return

        for block_idx, (bbox, text) in enumerate(page_blocks):
            if cb.on_block is not None and text and text.strip():
                conf = (
                    confidence_estimator(text)
                    if confidence_estimator is not None
                    else None
                )
                await cb.on_block(
                    page_index,
                    block_idx,
                    list(bbox),
                    text,
                    "text",
                    conf,
                )
        if cb.on_page_complete is not None:
            await cb.on_page_complete(page_index)

    async def _run_spellcheck(
        self,
        pages_structured: PagesData,
        page_nums: Sequence[int],
        lang: str,
    ) -> None:
        """
        Post-processing step that runs spelling auto-correction on each page.

        ``correct_text`` is a CPU-bound dict lookup; running it on the
        event loop would stall the asyncio thread for the full duration
        of a 200-page spellcheck pass. Phase 5 fix: offload each page's
        correction to a worker thread.
        """
        from omniscribe.core.postprocess import DictionaryPostProcessor

        processor = DictionaryPostProcessor(lang)
        await processor.ensure_loaded()

        async def correct_page(p: int) -> PageBoxes:
            return await asyncio.to_thread(
                _spellcheck_page_sync, processor, pages_structured[p]
            )

        corrected_pages = await asyncio.gather(*(correct_page(p) for p in page_nums))
        for p, corrected in zip(page_nums, corrected_pages, strict=True):
            pages_structured[p] = corrected

    async def _build_document_result(
        self,
        *,
        pages_data: PagesData,
        page_nums: Sequence[int],
        source_path: str,
        source_processor: str,
        spellcheck: SpellcheckMode,
        cross_page: bool,
        page_metadata_overlays: dict[int, dict[str, object]] | None = None,
        block_metadata_overlays: dict[int, list[dict[str, object]]] | None = None,
    ) -> DocumentResult:
        """Apply text-only post-processing and run document processors.

        Returns the resulting :class:`DocumentResult`. The caller is responsible
        for any engine-specific mutations (e.g. hybrid's quality-routing step)
        before handing the result to :meth:`_emit`.
        """
        from omniscribe.core.document import DocumentResult
        from omniscribe.core.processors import run_document_processors

        # Text-only passes first — they mutate ``pages_data`` in place.
        if cross_page:
            self._cross_page_merge(pages_data, page_nums)

        if spellcheck and spellcheck != "none":
            await self._run_spellcheck(pages_data, page_nums, spellcheck)

        document_result = DocumentResult.from_pages_data(
            pages_data,
            source_path=source_path,
            source_processor=source_processor,
            confidence_fn=_estimate_confidence,
        )

        if page_metadata_overlays:
            for page in document_result.pages:
                metadata = page_metadata_overlays.get(page.page_index)
                if metadata:
                    page.metadata.update(metadata)

        if block_metadata_overlays:
            for page in document_result.pages:
                block_overlays = block_metadata_overlays.get(page.page_index)
                if block_overlays:
                    # `strict=True`: the engine guarantees the
                    # backend emits one overlay per block, so
                    # length mismatches are a real bug and should
                    # surface loudly rather than silently drop the
                    # tail of either sequence.
                    for block, meta in zip(page.blocks, block_overlays, strict=True):
                        block.metadata.update(meta)

        return await run_document_processors(document_result, self.document_processors)

    async def _emit(
        self,
        *,
        input_path: str,
        output_path: str,
        document_result: DocumentResult,
        dpi: int,
        progress: ProgressCallback | None,
        page_nums: Sequence[int] | None = None,
    ) -> dict[int, list[str]]:
        """Write the final PDF and return the ``{page: [lines]}`` view.

        This is the single place where ``last_document_result`` is assigned and
        the output writer is invoked; both engines route through it so the
        end-of-pipeline contract lives in exactly one method.

        When the injected writer implements :class:`DocumentResultWriter` the
        full ``DocumentResult`` is passed through losslessly; legacy 4-arg
        callable writers receive the ``to_pages_data()`` conversion instead.

        ``page_nums`` (audit P2-9) is forwarded to rich writers so subset
        runs embed only the processed pages instead of re-rasterizing the
        whole source document. Legacy callable writers keep their 4-arg
        contract and ignore it.

        Phase 4 fix: ``to_pages_data()`` and the per-page text-collection loop
        now run inside the same ``asyncio.to_thread`` call as the writer.
        Previously they ran on the event loop before the thread dispatch —
        for a 1000-page document with ~50 blocks/page that was a 50k-iteration
        list walk on the asyncio thread that should have stayed out of it.
        """
        self.last_document_result = document_result
        await notify(progress, "embed", 0, 1, "Writing output...")
        writer = self.output_writer

        def _write_and_collect_text() -> dict[int, list[str]]:
            if isinstance(writer, DocumentResultWriter):
                import inspect

                sig = inspect.signature(writer.write_document_result)
                if "page_nums" in sig.parameters:
                    writer.write_document_result(
                        input_path,
                        output_path,
                        document_result,
                        dpi,
                        page_nums=page_nums,
                    )
                else:
                    writer.write_document_result(
                        input_path, output_path, document_result, dpi
                    )
                # Lossless path: build the text-only view straight from
                # the IR — no to_pages_data round-trip needed.
                return {
                    page.page_index: [
                        block.text for block in page.blocks if block.text.strip()
                    ]
                    for page in document_result.pages
                }
            # Legacy writer: needs pages_data anyway, so do the conversion
            # here in the worker thread and reuse it for the text view.
            pages_data = document_result.to_pages_data()
            writer(input_path, output_path, pages_data, dpi)
            return {
                p: [text for _, text in blocks if text.strip()]
                for p, blocks in pages_data.items()
            }

        pages_text = await asyncio.to_thread(_write_and_collect_text)
        await notify(progress, "embed", 1, 1, "Done.")
        return pages_text
