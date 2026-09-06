"""Prompted grounded OCR backend (Qwen-VL and friends).

:class:`PromptedGroundedOCR` is the default grounded backend for any
OpenAI-compatible VLM that emits ``{"bbox_2d": [...], "content": "..."}``
JSON when prompted — confirmed for Qwen2.5-VL (line-level, wrapped in
fences) and Qwen3-VL (line-level, bare JSON). Should also work for
MiniCPM-V, InternVL, etc.

It handles its own rasterization (one VLM call per page) and emits
per-page progress; the OCRPipeline calls ``ocr_document(pdf_path)``
directly and lets this backend double as its own PDF loader.

Reuses :mod:`omniscribe.core.ocr.client` for ``ensure_model_loaded``
so a typo in ``--model`` fails fast with the same diagnostic as the
hybrid path (issue #7 — the grounded path was the original
reproducer for that bug class).
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Sequence

from openai import AsyncOpenAI

from omniscribe.core.grounded.models import (
    GroundedBlock,
    GroundedResponse,
    ProgressCallback,
    WarningCallback,
)
from omniscribe.core.grounded.parsers import _parse_grounded_json
from omniscribe.core.grounded.rasterize import _rasterize_to_jpeg_pages
from omniscribe.core.llm.client import call_llm
from omniscribe.core.llm.temperatures import TEMPERATURE_GROUNDED
from omniscribe.core.ocr import (
    ModelNotLoadedError,
    _format_model_not_loaded,
    _list_loaded_model_ids,
    _model_in_loaded,
)
from omniscribe.core.ocr.prompts import (
    model_supports_system_role as _model_supports_system_role,
)
from omniscribe.core.ocr.resilience import (
    CircuitOpenError,
    get_default_circuit_breaker_registry,
    is_transient_error,
)
from omniscribe.core.workflows.base import OCRCancelled
from omniscribe.utils.prompt_safety import sanitize_prompt_input

logger = logging.getLogger(__name__)

# Bumped when the user-facing prompt body changes.
PROMPT_VERSION = "2026-08-15.v1"

DEFAULT_GROUNDING_PROMPT = (
    "You are an exhaustive OCR engine. Output a JSON array covering EVERY "
    "VISUAL LINE of text on this page: headers, form labels, field names, "
    "body paragraphs, numbered items, signatures, footnotes — all of it.\n"
    "\n"
    "CRITICAL — line segmentation: emit ONE element PER VISUAL LINE. If a "
    "phrase wraps onto two lines on the page, that is TWO elements, not "
    "one — even if the lines belong to the same sentence, paragraph, or "
    "phrase. Never join lines together. Never collapse a line break into "
    "a space. Hand-written notes especially have line breaks that printed "
    "text wouldn't — preserve every one of them. Each bbox must tightly "
    "enclose a SINGLE line.\n"
    "\n"
    "Worked example — if the page contains the four visual lines:\n"
    "  schwache Grenzen\n"
    "  im Kopf\n"
    "  Linke\n"
    "  weiblich\n"
    "emit FOUR elements, one per line. Do NOT emit one element with "
    'content "schwache Grenzen im Kopf" and another with "Linke '
    'weiblich" — joining lines is wrong even when the resulting phrase '
    "reads naturally.\n"
    "\n"
    "Each element must have this exact shape: "
    '{"bbox_2d": [x1, y1, x2, y2], "content": "<text of that one line>"} '
    "where bbox_2d is pixel coordinates in the image (x1<x2, y1<y2). The "
    "bbox height must match a single line of text. If your bbox is tall "
    "enough to contain two lines, you have joined two lines — split it "
    "into two elements.\n"
    "\n"
    "For multi-column layouts, read each column top-to-bottom before "
    "moving to the next column; never interleave lines across columns.\n"
    "\n"
    "If the page contains no readable text, emit an empty JSON array []. "
    "Do not synthesize a single placeholder element.\n"
    "\n"
    "Do not skip small labels. Do not summarize. Do not paraphrase. "
    "No markdown fences, no prose — only the raw JSON array."
)

# Companion system message. Prepended so the role identity sits in the
# system role and the user turn can stay focused on the line-segmentation
# rules above. Same "don't invent, emit empty array on blank pages" guard
# lives here as a belt-and-suspenders reinforcement.
GROUNDED_OCR_SYSTEM_MESSAGE = (
    "You are an exhaustive OCR engine. "
    "Your output is a JSON array of every visual line of text on the page, "
    "with tight bounding boxes. "
    "Never join two visual lines into one element. "
    "If the page has no readable text, emit [] — do not invent."
)

CROP_OCR_PROMPT = (
    "You are a precise OCR engine. Transcribe EVERY line of text visible "
    "in this cropped image, preserving line breaks. If the crop is blank "
    "or contains no readable text, return an empty string. Do not "
    "paraphrase, summarize, or add commentary. Output the text only — "
    "no JSON, no markdown fences, no leading or trailing prose."
)

REPAIR_CROP_PROMPT = (
    "You are a precise OCR engine. A previous attempt at reading this "
    "cropped region produced:\n\n{previous_text}\n\nThat reading was "
    "REJECTED ({rejection_reason}). Re-transcribe EVERY line of text in "
    "the image carefully; keep line breaks; return only the corrected "
    "text — no commentary. If the crop is blank, return an empty string."
)


def _extract_grounded_crops(
    b64: str, blocks: list[GroundedBlock], w: int, h: int
) -> None:
    if not any(b.label in ("image", "figure") for b in blocks):
        return
    import base64
    import io

    from PIL import Image

    img_data = base64.b64decode(b64)
    with Image.open(io.BytesIO(img_data)) as img:
        for b in blocks:
            if b.label in ("image", "figure"):
                crop_box = (
                    b.bbox[0] * w,
                    b.bbox[1] * h,
                    b.bbox[2] * w,
                    b.bbox[3] * h,
                )
                crop_box = (
                    max(0, min(w, crop_box[0])),
                    max(0, min(h, crop_box[1])),
                    max(0, min(w, crop_box[2])),
                    max(0, min(h, crop_box[3])),
                )
                if crop_box[2] > crop_box[0] and crop_box[3] > crop_box[1]:
                    cropped = img.crop(crop_box)
                    buf = io.BytesIO()
                    cropped.save(buf, format="PNG")
                    b.image_bytes = buf.getvalue()


def _crop_normalized(b64: str, bbox: Sequence[float], w: int, h: int) -> str | None:
    """Crop a normalized ``[x0, y0, x1, y1]`` bbox out of a page JPEG.

    F1.17 audit fix: the prior implementation used 5% padding and
    JPEG quality 90, while the hybrid path's
    :func:`omniscribe.utils.image.crop_for_ocr_from_image` uses 0.5%
    padding and quality 85. The two values produce measurably
    different JPEG/PSNR characteristics, which broke the OCR
    trust-score calibration parity — a block that scored 0.6 on the
    hybrid path might score 0.4 on the grounded path purely because
    of the JPEG/PSNR difference, not because the text quality
    differed. We now import the canonical constants from
    :mod:`omniscribe.utils.image` and use them here so a future
    maintainer changing one updates both at once.

    Returns ``None`` when the (clamped) box degenerates. Runs in a
    worker thread — PIL decode/crop/encode is blocking CPU work.
    """
    import base64 as _b64
    import io

    from PIL import Image

    from omniscribe.utils.image import (
        DEFAULT_CROP_PADDING,
        DEFAULT_CROP_QUALITY,
    )

    pad_x = DEFAULT_CROP_PADDING * max(bbox[2] - bbox[0], 0.0)
    pad_y = DEFAULT_CROP_PADDING * max(bbox[3] - bbox[1], 0.0)
    box = (
        max(0, int((bbox[0] - pad_x) * w)),
        max(0, int((bbox[1] - pad_y) * h)),
        min(w, int((bbox[2] + pad_x) * w)),
        min(h, int((bbox[3] + pad_y) * h)),
    )
    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    with Image.open(io.BytesIO(_b64.b64decode(b64))) as img:
        buf = io.BytesIO()
        img.crop(box).save(buf, format="JPEG", quality=DEFAULT_CROP_QUALITY)
    return _b64.b64encode(buf.getvalue()).decode("ascii")


class PromptedGroundedOCR:
    """Grounded backend built on an OpenAI-compatible vision LLM endpoint.

    Works with any VLM that emits ``{bbox_2d:[...], content:"..."}`` when asked.

    Usage::

        backend = PromptedGroundedOCR(
            api_base="http://localhost:1234/v1",
            model="qwen/qwen3-vl-8b",
        )
        pipe = OCRPipeline(pdf_handler=PDFHandler(), grounded_backend=backend)
        await pipe.run("in.pdf", "out.pdf")
    """

    def __init__(
        self,
        api_base: str | None = None,
        model: str | None = None,
        api_key: str = "lm-studio",
        max_image_dim: int = 1024,
        dpi: int = 150,
        prompt: str | None = None,
        timeout_s: float = 240.0,
        max_tokens: int = 8192,
        concurrency: int = 1,
    ):
        # H2/H4 audit fix: read LLM coordinates from load_settings()
        # rather than os.getenv so the centralised configuration is the
        # single source of truth. The retry/breaker knobs below still
        # use os.getenv for now (the audit flagged only the api_base /
        # api_key / model fields as a residual gap from the F1.9 fix).
        from omniscribe.config import load_settings

        settings = load_settings()
        # Honor .env / environment overrides the same way OCRProcessor does,
        # so a user with `LLM_API_BASE` set in .env doesn't have to also pass
        # `--api-base` when switching to --grounded.
        self.api_base: str = (
            api_base or settings.llm_api_base or "http://localhost:1234/v1"
        )
        self.model: str = model or settings.llm_model or "qwen/qwen3-vl-8b"
        self.api_key: str = api_key or settings.llm_api_key or "lm-studio"
        self.max_image_dim = max_image_dim
        self.dpi = dpi
        self.prompt = prompt or DEFAULT_GROUNDING_PROMPT
        self.timeout_s = timeout_s
        self.max_tokens = max_tokens
        self.concurrency = concurrency
        # Same resilience policy as the hybrid OCRProcessor: retry
        # transient errors with backoff, fail fast once the endpoint is
        # deemed down. Env overrides: OMNISCRIBE_LLM_MAX_RETRIES,
        # OMNISCRIBE_LLM_RETRY_BASE_DELAY, OMNISCRIBE_CB_*.
        self.max_retries = int(os.getenv("OMNISCRIBE_LLM_MAX_RETRIES", "2"))
        self.retry_base_delay_s = float(
            os.getenv("OMNISCRIBE_LLM_RETRY_BASE_DELAY", "1.0")
        )
        self.circuit_breaker = get_default_circuit_breaker_registry().get_or_create(
            self.api_base, self.model
        )
        # Audit P2-9: per-instance page-image cache. ``ocr_crop`` (quality
        # repair on the grounded path) used to re-rasterize the whole
        # document for every repaired block; ``ocr_document`` already paid
        # for the same rasterization seconds earlier. The cache is keyed by
        # path + file stat + raster settings (stat best-effort so test
        # doubles can pass dummy paths), and the backend instance is
        # constructed per request, so the cache lives exactly as long as
        # one run.
        self._raster_cache: dict[
            tuple[str, int, int, int, int], list[tuple[str, int, int]]
        ] = {}

    async def _get_page_images(self, input_path: str) -> list[tuple[str, int, int]]:
        """Rasterize ``input_path`` once per (path, stat, settings) tuple.

        Shared by :meth:`ocr_document` and :meth:`ocr_crop` so the repair
        loop reuses the main-pass rasterization instead of re-opening the
        PDF per below-target block.
        """
        try:
            st = os.stat(input_path)
            stat_key = (int(st.st_mtime_ns), int(st.st_size))
        except OSError:
            stat_key = (-1, -1)
        key = (input_path, stat_key[0], stat_key[1], self.max_image_dim, self.dpi)
        cached = self._raster_cache.get(key)
        if cached is not None:
            return cached
        page_imgs = await asyncio.to_thread(
            _rasterize_to_jpeg_pages,
            input_path,
            self.max_image_dim,
            self.dpi,
        )
        self._raster_cache[key] = page_imgs
        return page_imgs

    async def ensure_model_loaded(self) -> None:
        """Pre-flight check that ``self.model`` is loaded on the server.

        Mirrors :meth:`OCRProcessor.ensure_model_loaded` so users on
        ``--grounded`` get the same fail-fast safety net. The grounded
        path is in fact the path that originally surfaced this bug
        (issue #7) — the user had OlmOCR loaded but requested Qwen3-VL,
        and LM Studio silently served bad OCR from the wrong model.
        """
        client = AsyncOpenAI(base_url=self.api_base, api_key=self.api_key)
        try:
            loaded = await _list_loaded_model_ids(client, self.api_base)
            if not _model_in_loaded(self.model, loaded):
                raise ModelNotLoadedError(
                    _format_model_not_loaded(self.api_base, self.model, loaded)
                )
        finally:
            close_method = getattr(client, "close", None)
            if callable(close_method):
                res = close_method()
                if asyncio.iscoroutine(res):
                    await res

    async def ocr_crop(
        self,
        input_path: str,
        page_index: int,
        bbox: Sequence[float],
        *,
        previous_text: str = "",
        attempt: int = 1,
    ) -> str:
        """Re-OCR a single normalized-bbox crop from one page.

        Used by the quality repair loop on the grounded path. The engine
        feature-detects this method (``hasattr``) — it is intentionally
        NOT part of the :class:`GroundedOCRBackend` protocol, so adding
        it changes no existing backend contract.

        ``previous_text`` / ``attempt`` come from the repair loop: when a
        previous reading exists, the prompt switches to
        :data:`REPAIR_CROP_PROMPT` and the temperature rises with the
        attempt number (capped at 0.3) so retries explore instead of
        repeating the same misread.

        Audit P2-9: page images come from the per-instance raster cache
        populated by :meth:`ocr_document`, so repair no longer
        re-rasterizes the full PDF per repaired block.
        """
        page_imgs = await self._get_page_images(input_path)
        if page_index < 0 or page_index >= len(page_imgs):
            raise ValueError(
                f"page_index {page_index} out of range "
                f"({len(page_imgs)} pages rasterized)"
            )
        b64, w, h = page_imgs[page_index]
        crop_b64 = await asyncio.to_thread(_crop_normalized, b64, bbox, w, h)
        if crop_b64 is None:
            return ""
        if previous_text:
            prompt = REPAIR_CROP_PROMPT.format(
                previous_text=sanitize_prompt_input(previous_text),
                rejection_reason="confidence below target or garbled",
            )
            temperature = min(TEMPERATURE_GROUNDED + 0.1 * (attempt - 1), 0.3)
        else:
            prompt = CROP_OCR_PROMPT
            temperature = TEMPERATURE_GROUNDED
        text = await self._call_with_retry(
            crop_b64, prompt=prompt, temperature=temperature
        )
        return text.strip()

    async def _call_with_retry(
        self,
        image_b64: str,
        prompt: str | None = None,
        temperature: float = TEMPERATURE_GROUNDED,
    ) -> str:
        """One grounded VLM page call with retry + circuit-breaker protection.

        Same policy as :meth:`OCRProcessor._chat`: transient failures are
        retried with exponential backoff (capped at 8s); permanent failures
        raise immediately; the shared circuit breaker fails fast once the
        endpoint is deemed down so remaining pages don't each burn a timeout.
        """
        await self.circuit_breaker.check()

        last_exc: BaseException | None = None
        for attempt in range(self.max_retries + 1):
            if attempt > 0:
                await self.circuit_breaker.check()
            try:
                text = await call_llm(
                    model=self.model,
                    api_base=self.api_base,
                    api_key=self.api_key,
                    temperature=temperature,
                    max_tokens=self.max_tokens,
                    timeout=self.timeout_s,
                    system_prompt=(
                        GROUNDED_OCR_SYSTEM_MESSAGE
                        if _model_supports_system_role(self.model)
                        else None
                    ),
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt or self.prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/jpeg;base64,{image_b64}",
                                    },
                                },
                            ],
                        }
                    ],
                )
                await self.circuit_breaker.record_success()
                return text
            except Exception as e:
                last_exc = e
                await self.circuit_breaker.record_failure()
                if not is_transient_error(e):
                    break
                if attempt < self.max_retries:
                    delay = min(self.retry_base_delay_s * (2**attempt), 8.0)
                    logger.warning(
                        "Transient grounded OCR error (attempt %d/%d), "
                        "retrying in %.1fs: %s: %s",
                        attempt + 1,
                        self.max_retries + 1,
                        delay,
                        type(e).__name__,
                        e,
                    )
                    await asyncio.sleep(delay)

        if last_exc is None:
            raise RuntimeError("Exhausted retries without exception")
        raise last_exc

    async def ocr_document(
        self,
        pdf_path: str,
        progress: ProgressCallback | None = None,
        on_warning: WarningCallback | None = None,
    ) -> GroundedResponse:
        # 1. Rasterize every page, remembering dimensions.
        # Offloaded to a worker thread — fitz.open / get_pixmap are blocking
        # CPU+IO work that would otherwise stall the event loop. The result
        # is cached on the instance (audit P2-9) so the repair loop's
        # ``ocr_crop`` reuses it.
        page_imgs = await self._get_page_images(pdf_path)

        # 2. Call the VLM per page, streaming progress and isolating failures
        # so one bad page doesn't tank a multi-page document.
        sem = asyncio.Semaphore(max(1, self.concurrency))
        total_pages = len(page_imgs)

        async def run_one(
            page_idx: int,
        ) -> tuple[int, list[GroundedBlock], BaseException | None]:
            b64, w, h = page_imgs[page_idx]
            async with sem:
                try:
                    text = await self._call_with_retry(b64)
                    text = text.strip()
                    blocks = _parse_grounded_json(text, page_idx, w, h)

                    if any(b.label in ("image", "figure") for b in blocks):
                        await asyncio.to_thread(
                            _extract_grounded_crops, b64, blocks, w, h
                        )

                    return page_idx, blocks, None
                except (CircuitOpenError, OCRCancelled):
                    raise
                except Exception as e:
                    # Per-page isolation: log the failure and return zero
                    # blocks for this page so surviving pages still land in
                    # the output. The exception is bubbled up via the
                    # 3-tuple so the caller can surface it (e.g. via the
                    # pipeline's `on_warning` and the response's
                    # `failed_pages`).
                    logger.warning(
                        f"grounded OCR failed for page {page_idx}: "
                        f"{type(e).__name__}: {e}"
                    )
                    return page_idx, [], e

        tasks = [asyncio.create_task(run_one(i)) for i in range(total_pages)]
        blocks_by_page: dict[int, list[GroundedBlock]] = {}
        failed_pages: list[int] = []
        completed = 0
        if progress is not None:
            await progress("ocr", 0, total_pages, f"Grounded OCR (0/{total_pages})...")
        try:
            for fut in asyncio.as_completed(tasks):
                page_idx, blocks, page_error = await fut
                blocks_by_page[page_idx] = blocks
                completed += 1
                if progress is not None:
                    await progress(
                        "ocr",
                        completed,
                        total_pages,
                        f"Grounded OCR ({completed}/{total_pages})",
                    )
                if page_error is not None:
                    failed_pages.append(page_idx)
                    if on_warning is not None:
                        await on_warning(page_idx, page_error)
        finally:
            # Audit M-domain 5: cancellation must be awaited. The
            # previous loop only called ``task.cancel()``; the
            # gather() now lets pending tasks actually wind down so
            # the event loop doesn't log
            # "Task was destroyed but it is pending" on shutdown.
            for t in tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        # Flatten in page order for a stable, deterministic output.
        flat_blocks: list[GroundedBlock] = []
        for page_idx in range(total_pages):
            flat_blocks.extend(blocks_by_page.get(page_idx, []))
        return GroundedResponse(
            blocks=flat_blocks,
            page_sizes=[(w, h) for (_, w, h) in page_imgs],
            failed_pages=failed_pages,
        )


__all__ = [
    "CROP_OCR_PROMPT",
    "DEFAULT_GROUNDING_PROMPT",
    "GROUNDED_OCR_SYSTEM_MESSAGE",
    "PROMPT_VERSION",
    "REPAIR_CROP_PROMPT",
    "PromptedGroundedOCR",
]
