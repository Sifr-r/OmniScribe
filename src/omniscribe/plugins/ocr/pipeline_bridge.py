"""HTTP→pipeline bridge: build an ``OCRPipeline`` from an ``OCRRequest``.

Three public surfaces:

- :func:`build_pipeline` — assembles the engine components for the request
  (hybrid vs grounded, processors, page preprocessor, block callbacks).
- :func:`resolve_run_kwargs` — translates request fields into the keyword
  arguments for :meth:`OCRPipeline.run` (dense mode, spellcheck, repair
  options, preprocessing options).
- :func:`run_pipeline` — executes one upload, adapting the core progress
  callback into percent/stage frames for the caller's ``on_progress``.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException

from omniscribe.config import RuntimeSettings
from omniscribe.core.callbacks import BlockCallbackSet
from omniscribe.core.document import SpellcheckMode
from omniscribe.core.imaging.page_preprocess import (
    PagePreprocessingOptions,
    PagePreprocessor,
)
from omniscribe.core.workflows.repair import RepairOptions
from omniscribe.pipeline import OCRPipeline
from omniscribe.plugins.ocr.schemas import OCRRequest
from omniscribe.utils.security import (
    _rewrite_url_with_resolved_ip,
    check_ssrf_target_sync,
    is_same_origin,
)

_LOGGER = logging.getLogger("omniscribe.plugins.ocr.bridge")

#: Progress adapter: ``(percent, stage, message)`` per frame.
OnProgress = Callable[[int, str, str], Awaitable[None]]
OnWarning = Callable[[str], Awaitable[None]]
CancelCheck = Callable[[], bool]


def build_pipeline(
    settings: RuntimeSettings,
    request: OCRRequest,
    *,
    block_callbacks: BlockCallbackSet | None = None,
) -> OCRPipeline:
    """Assemble the full pipeline for one request (no execution)."""
    from omniscribe import (
        OCRProcessor,
        PDFHandler,
        PromptedGroundedOCR,
        build_document_processors,
    )

    clean_base = (request.api_base or "").strip()
    resolved_ip: str | None = None
    if clean_base:
        check = check_ssrf_target_sync(clean_base)
        if not check.allowed:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid api_base URL (SSRF blocked: {check.reason})",
            )
        resolved_ip = check.resolved_ip
        if not is_same_origin(clean_base, settings.llm_api_base):
            api_key = (request.api_key or "").strip()
        else:
            api_key = (request.api_key or settings.llm_api_key).strip()
        api_base = clean_base
        if urlsplit(api_base).scheme.lower() == "http" and resolved_ip:
            api_base = _rewrite_url_with_resolved_ip(api_base, resolved_ip)
    else:
        api_base = settings.llm_api_base.strip()
        api_key = (request.api_key or settings.llm_api_key).strip()

    model = (request.model or settings.llm_model).strip()
    processors = build_document_processors(request.document_processors)

    if request.pipeline_mode == "grounded":
        backend = PromptedGroundedOCR(
            api_base=api_base,
            api_key=api_key,
            model=model,
            max_image_dim=request.max_image_dim or settings.ocr_max_image_dim,
            concurrency=request.concurrency or settings.ocr_concurrency,
            dpi=request.dpi or settings.ocr_dpi,
        )
        return OCRPipeline(
            pdf_handler=PDFHandler(),
            grounded_backend=backend,
            document_processors=processors,
            block_callbacks=block_callbacks,
        )

    from omniscribe.core.aligner import get_shared_hybrid_aligner

    ocr_processor = OCRProcessor(api_base=api_base, api_key=api_key, model=model)
    return OCRPipeline(
        # Process-wide singleton: constructing a fresh aligner would reload
        # the Surya model weights on every request.
        aligner=get_shared_hybrid_aligner(),
        ocr_processor=ocr_processor,
        pdf_handler=PDFHandler(),
        document_processors=processors,
        page_preprocessor=_build_page_preprocessor(request),
        block_callbacks=block_callbacks,
    )


def _build_page_preprocessor(request: OCRRequest) -> PagePreprocessor | None:
    if not request.preprocessing_enabled:
        return None
    from omniscribe.core.imaging.page_preprocess import LocalPagePreprocessor

    return LocalPagePreprocessor()


def resolve_run_kwargs(
    settings: RuntimeSettings, request: OCRRequest
) -> dict[str, Any]:
    """Translate request fields into ``OCRPipeline.run`` keyword arguments."""
    try:
        spellcheck = SpellcheckMode((request.spellcheck or "none").strip() or "none")
    except ValueError:
        spellcheck = SpellcheckMode.NONE

    repair_options: RepairOptions | None = None
    if request.quality_loop_enabled is not False:
        # The API layer defaults the loop ON; only an explicit "false" from
        # the form disables it (mirrors the historical /api/process contract).
        repair_options = RepairOptions(
            enabled=True,
            target=request.quality_target,
            max_retries=request.quality_max_retries,
        )

    kwargs: dict[str, Any] = {
        "dpi": request.dpi or settings.ocr_dpi,
        "concurrency": request.concurrency or settings.ocr_concurrency,
        "dense_threshold": request.dense_threshold or settings.ocr_dense_threshold,
        "max_image_dim": request.max_image_dim or settings.ocr_max_image_dim,
        "pages": request.pages,
        "dense_mode": request.dense_mode,
        "spellcheck": spellcheck,
        "repair_options": repair_options,
    }
    if request.pipeline_mode != "grounded":
        kwargs["preprocessing_options"] = PagePreprocessingOptions(
            enabled=request.preprocessing_enabled,
            orientation_detection=request.orientation_detection,
            deskew=request.deskew,
            denoise=request.denoise,
            normalize_contrast=request.normalize_contrast,
            crop_cleanup=request.crop_cleanup,
        )
    return kwargs


async def run_pipeline(
    pipeline: OCRPipeline,
    *,
    settings: RuntimeSettings,
    request: OCRRequest,
    input_path: str,
    output_path: str,
    on_progress: OnProgress | None = None,
    on_warning: OnWarning | None = None,
    cancel_check: CancelCheck | None = None,
) -> dict[int, list[str]]:
    """Run one upload and adapt core callbacks into simple frames."""

    async def _progress(stage: str, current: int, total: int, message: str) -> None:
        if on_progress is None:
            return
        percent = int(current / total * 100) if total > 0 else 0
        await on_progress(min(percent, 100), stage, message)

    async def _warning(page_idx: int, exc: BaseException) -> None:
        if on_warning is None:
            return
        await on_warning(f"Warning on page {page_idx + 1}: {exc}")

    run_kwargs = resolve_run_kwargs(settings, request)
    # Audit M-domain 2: ensure the per-request pipeline releases its
    # long-lived AsyncOpenAI client (built in OCRProcessor.__init__) even
    # when the run raises. ``aclose`` is a no-op for the grounded path
    # and idempotent for the hybrid path.
    try:
        return await pipeline.run(
            input_path,
            output_path,
            progress=_progress,
            on_warning=_warning,
            cancel_check=cancel_check,
            trust_model_id=(request.model or settings.llm_model),
            **run_kwargs,
        )
    finally:
        await pipeline.aclose()
