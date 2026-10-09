"""HTTP→pipeline bridge: build an ``OCRPipeline`` from an ``OCRRequest``.

Three public surfaces:

- :func:`build_pipeline` — assembles the engine components for the request
  (hybrid vs grounded, processors, page preprocessor, trust orchestrator,
  block callbacks).
- :func:`resolve_run_kwargs` — translates request fields into the keyword
  arguments for :meth:`OCRPipeline.run` (dense mode, spellcheck, repair
  options, preprocessing options, quality routing).
- :func:`run_pipeline` — executes one upload, adapting the core progress
  callback into percent/stage frames for the caller's ``on_progress``.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

from omniscribe.config import RuntimeSettings
from omniscribe.core.callbacks import BlockCallbackSet
from omniscribe.core.document import SpellcheckMode
from omniscribe.core.imaging.page_preprocess import (
    PagePreprocessingOptions,
    PagePreprocessor,
)
from omniscribe.core.ocr_quality import (
    TrustOrchestrator,
    build_trust_orchestrator,
)
from omniscribe.core.ocr_quality.config import OCrQualitySettings
from omniscribe.core.ocr_quality.routing import QualityRoutingOptions
from omniscribe.core.workflows.repair import RepairOptions
from omniscribe.pipeline import OCRPipeline
from omniscribe.plugins.errors import PluginError
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


def build_trust_orchestrator_for_request(
    request: OCRRequest,
) -> TrustOrchestrator | None:
    """Build the trust orchestrator this request asks for.

    Returns ``None`` when the request carries no ``quality_options.trust``
    block or when every sub-module it enables is off — the factory's own
    short-circuit — so an upload without quality options keeps the
    pre-existing no-op path. ``trust_model_id`` alone never enables the
    layer; the orchestrator is what turns it on, and the bridge
    injects one here.
    """
    quality_options = request.quality_options
    if quality_options is None or quality_options.trust is None:
        return None
    settings = OCrQualitySettings.model_validate(quality_options.trust.model_dump())
    return build_trust_orchestrator(settings)


def build_quality_routing_options(
    request: OCRRequest,
) -> QualityRoutingOptions | None:
    """Return the routing options when the request enables routing, else ``None``."""
    quality_options = request.quality_options
    if quality_options is None or quality_options.routing is None:
        return None
    if not quality_options.routing.enabled:
        return None
    return QualityRoutingOptions(enabled=True)


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
            raise PluginError(
                400,
                "bad_request",
                f"Invalid api_base URL (SSRF blocked: {check.reason})",
            )
        resolved_ip = check.resolved_ip
        if not is_same_origin(clean_base, settings.llm_api_base):
            api_key = (request.api_key or "").strip()
        else:
            api_key = (request.api_key or settings.llm_api_key).strip()
        api_base = clean_base
        if urlsplit(api_base).scheme.lower() == "http" and resolved_ip:
            # Plain HTTP can carry the address in the URL itself. HTTPS
            # cannot — rewriting the host to a literal IP breaks SNI and
            # certificate validation — so ``resolved_ip`` is additionally
            # threaded into the backend constructors below, which build an
            # IP-pinned transport instead. Without that, an https api_base
            # kept its bare hostname and was re-resolved on connect, leaving
            # a DNS-rebinding TOCTOU window.
            api_base = _rewrite_url_with_resolved_ip(api_base, resolved_ip)
    else:
        api_base = settings.llm_api_base.strip()
        api_key = (request.api_key or settings.llm_api_key).strip()

    model = (request.model or settings.llm_model).strip()
    processors = build_document_processors(request.document_processors)
    # Both construction branches take the same orchestrator: the trust
    # layer is engine-agnostic (the grounded path passes page_image=None,
    # so pixel sub-modules degrade to their non-pixel behaviour).
    trust_orchestrator = build_trust_orchestrator_for_request(request)

    if request.pipeline_mode == "grounded":
        backend = PromptedGroundedOCR(
            api_base=api_base,
            api_key=api_key,
            model=model,
            max_image_dim=request.max_image_dim or settings.ocr_max_image_dim,
            concurrency=request.concurrency or settings.ocr_concurrency,
            dpi=request.dpi or settings.ocr_dpi,
            resolved_ip=resolved_ip,
        )
        return OCRPipeline(
            pdf_handler=PDFHandler(),
            grounded_backend=backend,
            document_processors=processors,
            block_callbacks=block_callbacks,
            trust_orchestrator=trust_orchestrator,
        )

    from omniscribe.core.aligner import get_shared_hybrid_aligner

    ocr_processor = OCRProcessor(
        api_base=api_base, api_key=api_key, model=model, resolved_ip=resolved_ip
    )
    return OCRPipeline(
        # Process-wide singleton: constructing a fresh aligner would reload
        # the Surya model weights on every request.
        aligner=get_shared_hybrid_aligner(),
        ocr_processor=ocr_processor,
        pdf_handler=PDFHandler(),
        document_processors=processors,
        page_preprocessor=_build_page_preprocessor(request),
        block_callbacks=block_callbacks,
        trust_orchestrator=trust_orchestrator,
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
        # Only present when the request opted in — an absent key keeps the
        # engine's pre-existing default (no routing policy applied).
        routing_options = build_quality_routing_options(request)
        if routing_options is not None:
            kwargs["quality_routing_options"] = routing_options
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
