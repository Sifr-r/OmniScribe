"""OCRProcessor — the main OCR class.

:class:`OCRProcessor` is the single class that performs OCR against a
local vision LLM (OlmOCR via LM Studio by default; any OpenAI-compatible
endpoint works, including GLM OCR via Ollama — set LLM_API_BASE/LLM_MODEL
or pass ``api_base``/``model``).

It composes four sibling modules:

- :mod:`omniscribe.core.ocr.prompts` — OlmOCR/page/crop/dual-engine/
  correction/handwriting prompt constants and selection helpers.
- :mod:`omniscribe.core.ocr.filters` — output sanitization
  (YAML front-matter strip, fallback-phrase suppression, runaway-
  repetition clip).
- :mod:`omniscribe.core.ocr.client` — pre-flight model-loaded checks
  (reused by :mod:`omniscribe.core.grounded.zai`).
- :mod:`omniscribe.core.ocr.exceptions` — :class:`LLMCallError` and
  :class:`ModelNotLoadedError`.

Call :meth:`ensure_model_loaded` once at pipeline startup before paying
for image conversion or detection (LM Studio silently falls back to
whatever model is currently loaded when the requested one is missing —
see :class:`ModelNotLoadedError` for why this matters). Then use
:meth:`perform_ocr` for full-page OCR or :meth:`perform_ocr_on_crop`
for single-box OCR.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import TYPE_CHECKING, Any

from openai import AsyncOpenAI

from omniscribe.config import load_settings
from omniscribe.core.ocr.chat_client import ChatClient
from omniscribe.core.ocr.client import (
    _format_model_not_loaded,
    _list_loaded_model_ids,
    _model_in_loaded,
)
from omniscribe.core.ocr.exceptions import ModelNotLoadedError
from omniscribe.core.ocr.filters import (
    _is_fallback_response,
    _strip_runaway_repetition,
    _strip_yaml_front_matter,
)
from omniscribe.core.ocr.prompts import (
    CROP_PROMPT,
    HANDWRITING_CROP_PROMPT,
    HANDWRITING_PAGE_PROMPT,
    OLMOCR_PAGE_PROMPT,
    fill_correction_crop,
    fill_correction_page,
    fill_dual_engine_crop,
    fill_dual_engine_page,
    model_supports_system_role,
    select_system_message,
)
from omniscribe.core.ocr.resilience import (
    CircuitBreakerRegistry,
    get_default_circuit_breaker_registry,
)
from omniscribe.utils.env import env_int
from omniscribe.utils.prompt_safety import sanitize_prompt_input

if TYPE_CHECKING:
    from omniscribe.core.ocr.trocr import TrOCREngine

logger = logging.getLogger(__name__)

# Map lowercase instance attr -> class-level default name for __getattr__ fallback.
_DEFAULTS: dict[str, str] = {
    "page_timeout_s": "PAGE_TIMEOUT_S",
    "crop_timeout_s": "CROP_TIMEOUT_S",
    "max_retries": "MAX_RETRIES",
    "retry_base_delay_s": "RETRY_BASE_DELAY_S",
    "retry_max_delay_s": "RETRY_MAX_DELAY_S",
    "page_max_tokens": "PAGE_MAX_TOKENS",
    "crop_max_tokens": "CROP_MAX_TOKENS",
}


class OCRProcessor:
    """LLM-based OCR processor over an OpenAI-compatible async client.

    Local VLMs occasionally fall into runaway-generation loops on dense
    or unusual pages — we bound both the per-call timeout and the
    response token budget so a single bad page can't hang the pipeline
    indefinitely. Tuned per-call (full-page vs single-line crop): a page
    can legitimately take longer than a crop, and warrants a higher token
    budget for paragraph-level content.
    """

    # Pedantic review 1.11 / 1.12: the audit-H3 knobs (page / crop
    # timeouts, max retries, retry base delay, page/crop max-tokens) used
    # to be class-level constants that read ``load_settings()`` and
    # ``env_int`` at module import. Two problems:
    #
    # 1. **Instance rebind on stale import-time value** (1.11):
    #    ``self.page_max_tokens = self.PAGE_MAX_TOKENS`` in ``__init__``
    #    captured the import-time env value, so an env-var change after
    #    import did not reach a freshly-constructed instance.
    # 2. **Module un-importable in subprocesses without env setup**
    #    (1.12): every import of :mod:`omniscribe.core.ocr.processor`
    #    parsed the full env and instantiated ``BaseSettings`` via
    #    ``load_settings()`` at class-body evaluation time, which made
    #    test runners and child processes fail when the env was empty
    #    or partial. The ``__getattr__`` workaround masked the failure
    #    for ``__new__``-based tests but every import still paid the cost.
    #
    # The class-level constants below are now **hardcoded defaults**
    # matching the values :func:`load_settings` would return when no env
    # override is present. ``__init__`` re-resolves the live values via
    # :func:`load_settings` and :func:`env_int` at instance construction
    # time, so a fresh ``OCRProcessor()`` always reflects the current
    # env. The class-level defaults are still the fallback used by
    # ``__getattr__`` for ``__new__``-built test instances.

    # Page-level OCR (full image): up to ~4 minutes, ~6k tokens of output.
    # Dense handwritten pages with tables can easily produce 2-3k tokens
    # of markdown, so 6k leaves headroom without enabling endless loops.
    # Override timeout via ``OMNISCRIBE_VLM_PAGE_TIMEOUT`` (audit A-11);
    # override the token budget via ``OMNISCRIBE_VLM_PAGE_MAX_TOKENS``
    # for tail-latency tuning on dense pages (Phase 5). Both flow
    # through :mod:`omniscribe.config` / :mod:`omniscribe.utils.env`
    # (audit H3) — no direct ``os.getenv`` in this module.
    PAGE_TIMEOUT_S: float = 240.0
    PAGE_MAX_TOKENS: int = 6144

    # Crop-level OCR (single box): a sentence at most. Capping much
    # tighter prevents a confused model from emitting a whole-page worth
    # of hallucinated text into one bbox during the refine stage.
    # Override via ``OMNISCRIBE_VLM_CROP_TIMEOUT`` (audit A-11); token
    # budget via ``OMNISCRIBE_VLM_CROP_MAX_TOKENS`` (Phase 5).
    CROP_TIMEOUT_S: float = 60.0
    CROP_MAX_TOKENS: int = 256

    # Retry policy for transient VLM errors (429, 5xx, connection drops).
    # Exponential backoff: base * 2^attempt, capped at MAX. Env overrides:
    # OMNISCRIBE_LLM_MAX_RETRIES, OMNISCRIBE_LLM_RETRY_BASE_DELAY.
    MAX_RETRIES: int = 2
    RETRY_BASE_DELAY_S: float = 1.0
    RETRY_MAX_DELAY_S: float = 8.0

    def __init__(
        self,
        api_base: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        trocr_engine: TrOCREngine | None = None,
        handwriting_mode: bool = False,
        confidence_threshold: float = 0.75,
        circuit_breaker_registry: CircuitBreakerRegistry | None = None,
    ):
        # Pedantic 1.11/1.12: resolve all env-driven settings at
        # instance construction time so the env is read once per
        # ``OCRProcessor()`` instead of once at module import. A long-
        # running uvicorn worker that picks up an env-var change after
        # the module was already imported will see the new value on the
        # next ``OCRProcessor()``. Importing the module no longer
        # touches ``load_settings()`` at all, so subprocesses and test
        # runners that lack the full env set can import freely.
        settings = load_settings()
        self.page_timeout_s: float = settings.vlm_page_timeout
        self.crop_timeout_s: float = settings.vlm_crop_timeout
        self.max_retries: int = settings.llm_max_retries
        self.retry_base_delay_s: float = settings.llm_retry_base_delay
        self.retry_max_delay_s: float = self.RETRY_MAX_DELAY_S
        self.page_max_tokens: int = env_int(
            "OMNISCRIBE_VLM_PAGE_MAX_TOKENS", self.PAGE_MAX_TOKENS
        )
        self.crop_max_tokens: int = env_int(
            "OMNISCRIBE_VLM_CROP_MAX_TOKENS", self.CROP_MAX_TOKENS
        )

        # H2/H4 audit fix: read LLM coordinates from load_settings()
        # rather than os.getenv so the centralised configuration is the
        # single source of truth. F1.9 already moved the timeout/retry
        # knobs to load_settings; this closes the residual gap.
        self.api_base: str = (
            api_base or settings.llm_api_base or "http://localhost:1234/v1"
        )
        self.api_key: str = api_key or settings.llm_api_key or "lm-studio"
        self.model: str = model or settings.llm_model or "allenai/olmocr-2-7b"
        self.client: AsyncOpenAI | None = None
        # Optional TrOCR specialist (lazy-loaded). When set, low-confidence
        # crops are re-OCR'd with TrOCR and the higher-confidence candidate wins.
        self.trocr_engine = trocr_engine
        self.handwriting_mode = handwriting_mode
        self.confidence_threshold = confidence_threshold
        # Per-(api_base, model) circuit breaker. Without an injected
        # registry each OCRProcessor gets a private breaker; with one,
        # processors targeting the same endpoint share one breaker so a
        # tripped breaker is visible to every concurrent caller.
        registry = circuit_breaker_registry or get_default_circuit_breaker_registry()
        self.circuit_breaker = registry.get_or_create(
            api_base=self.api_base, model=self.model
        )
        # The actual VLM call (retry + breaker) lives in ChatClient. The
        # processor just hands it a prompt + image and a per-call
        # timeout / max-tokens budget; the client owns the loop.
        self._chat_client = ChatClient(
            model=self.model,
            api_base=self.api_base,
            api_key=self.api_key,
            max_retries=self.max_retries,
            retry_base_delay_s=self.retry_base_delay_s,
            retry_max_delay_s=self.retry_max_delay_s,
            circuit_breaker=self.circuit_breaker,
        )
        # F1.13 audit fix (PARTIAL -> FULL): track the number of Tesseract
        # fallback failures over the lifetime of this processor. The
        # previous code logged the failure (with ``exc_info=True`` after
        # the F1.4 fix) but had no per-run counter, so an operator
        # running 200 pages with a broken Tesseract install would see
        # 200 log lines and have no aggregate. The counter is read by
        # the API layer for the job-completion summary so a stuck
        # dual-engine path surfaces in the UI without log scraping.
        self.tesseract_error_count: int = 0

    def __getattr__(self, name: str) -> object:
        """F1.9 fallback: resolve the audit-H3 setting attributes that
        ``__init__`` would normally set, falling back to the
        class-level constants.

        A few pre-existing tests construct ``OCRProcessor`` via
        ``OCRProcessor.__new__(OCRProcessor)`` to skip the real
        ``__init__`` (which would otherwise build an ``AsyncOpenAI``
        client and load the runtime settings). Those tests still
        expect ``self.crop_timeout_s`` etc. to resolve to the
        class-level defaults. This ``__getattr__`` is only invoked
        when the attribute is missing on the instance, so the
        ``__init__``-set values still win in production. The fallback
        list mirrors the attributes ``__init__`` sets.
        """
        class_attr = _DEFAULTS.get(name)
        if class_attr is not None and hasattr(self.__class__, class_attr):
            return getattr(self.__class__, class_attr)
        raise AttributeError(
            f"{self.__class__.__name__!r} object has no attribute {name!r}"
        )

    async def ensure_model_loaded(self) -> None:
        """Pre-flight check that ``self.model`` is loaded on the server.

        Hits ``GET /v1/models`` via the OpenAI SDK and verifies the
        configured model ID appears in the loaded list (case-insensitive).
        Raises :class:`ModelNotLoadedError` on mismatch with a message
        that names what's loaded and how to fix it. Wraps any underlying
        transport / auth failure in :class:`LLMCallError`.

        Why we do this: see :class:`ModelNotLoadedError`. Cheap call (one
        GET, no inference); call once at pipeline startup before paying
        for image conversion or detection.
        """
        # Pedantic 2.2: Use an ephemeral AsyncOpenAI client (or test fake
        # attached to self.client) so ensure_model_loaded is self-contained
        # and avoids mutating or closing any client instance on self.
        client = getattr(self, "client", None)
        is_ephemeral = False
        if client is None or isinstance(client, AsyncOpenAI):
            client = AsyncOpenAI(
                base_url=self.api_base,
                api_key=getattr(self, "api_key", None) or "lm-studio",
            )
            is_ephemeral = True
        try:
            loaded = await _list_loaded_model_ids(client, self.api_base)
            if not _model_in_loaded(self.model, loaded):
                raise ModelNotLoadedError(
                    _format_model_not_loaded(self.api_base, self.model, loaded)
                )
        finally:
            if is_ephemeral:
                close_method = getattr(client, "close", None)
                if callable(close_method):
                    res = close_method()
                    if asyncio.iscoroutine(res):
                        await res

    async def aclose(self) -> None:
        """Close the underlying :class:`AsyncOpenAI` client if one is owned.

        Audit M-domain 2: the long-lived ``AsyncOpenAI`` instance built in
        :meth:`__init__` was previously never released, so every request
        that constructed an ``OCRProcessor`` left a connection pool in
        use until the worker process exited. The hybrid path builds a
        fresh processor per request via :func:`build_pipeline`, so the
        per-request socket churn was real.

        Safe to call multiple times — the second call is a no-op once
        ``self.client`` has been cleared. Test instances built via
        :meth:`__new__` (which skip :meth:`__init__`) do not have a
        ``client`` attribute; ``getattr`` with a default keeps the
        call free.
        """
        if getattr(self, "client", None) is not None:
            client = self.client
            try:
                close_method = getattr(client, "aclose", None) or getattr(
                    client, "close", None
                )
                if close_method is not None:
                    result = close_method()
                    if asyncio.iscoroutine(result):
                        await result
            finally:
                # Drop the reference so a follow-up aclose() call is a no-op
                # even if the close itself raised.
                self.client = None

    async def __aenter__(self) -> OCRProcessor:
        """Enter the async context manager; returns ``self``."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> None:
        """Exit the async context manager; closes the OpenAI client."""
        await self.aclose()

    async def perform_ocr(
        self,
        image_base64: str,
        self_correction: bool = False,
        binarize: bool = False,
        dual_engine: bool = False,
    ) -> list[str]:
        """OCR a full page image. Returns non-empty lines in reading order.

        YAML front matter emitted by OlmOCR (rotation/language/is_table flags)
        is stripped before returning. Runaway repetition (the model getting
        stuck emitting the same line over and over) is detected and clipped
        — this happens occasionally on dense handwritten pages even with
        max_tokens set, and pollutes downstream alignment with junk lines.

        The OLMOCR-2 page prompt is sent as a plain user message with no
        system role (pinned by tests/core/ocr/test_ocr.py::TestPromptConstants)
        — the model was RL-trained on this exact distribution and a system
        message would shift it. Dual-engine and correction paths wrap a
        system message around their user turns.

        ``self_correction`` runs a second full-budget VLM pass over the same
        image with the correction prompt and unconditionally replaces the
        first-pass text (accept-always; unlike TrOCR arbitration there is no
        confidence comparison). An empty correction pass returns no lines.
        """
        if binarize:
            image_base64 = await asyncio.to_thread(
                self._apply_adaptive_threshold, image_base64
            )

        handwriting_mode = getattr(self, "handwriting_mode", False)
        prompt = HANDWRITING_PAGE_PROMPT if handwriting_mode else OLMOCR_PAGE_PROMPT
        if dual_engine:
            draft = await asyncio.to_thread(self._get_tesseract_draft, image_base64)
            if draft:
                prompt = fill_dual_engine_page(draft)

        # OlmOCR-2 page path: pure user message, no system role (pinned
        # by tests/core/ocr/test_ocr.py::TestPromptConstants). Every
        # other page path gets a system message — *unless* the active
        # model is one of the system-role-excluded families
        # (e.g. allenai/olmocr-2-7b), in which case we drop the system
        # message entirely to keep the model's RL-trained distribution
        # intact.
        page_system = self._resolve_page_system(
            prompt=prompt,
            handwriting_mode=handwriting_mode,
            dual_engine=dual_engine,
        )

        text = await self._chat(
            prompt,
            image_base64,
            timeout=self.page_timeout_s,
            max_tokens=self.page_max_tokens,
            system_prompt=page_system,
        )
        if not text:
            return []

        if self_correction:
            correction_prompt = fill_correction_page(text)
            text = await self._chat(
                correction_prompt,
                image_base64,
                timeout=self.page_timeout_s,
                max_tokens=self.page_max_tokens,
                system_prompt=self._resolve_page_system(
                    prompt=correction_prompt,
                    handwriting_mode=handwriting_mode,
                    dual_engine=dual_engine,
                ),
            )
            if not text:
                return []

        body = _strip_yaml_front_matter(text)
        lines = [line.strip() for line in body.split("\n") if line.strip()]
        return _strip_runaway_repetition(lines)

    async def _run_trocr_arbitration(
        self,
        vlm_result: str,
        image_base64: str,
        vlm_confidence: float,
    ) -> str:
        """Arbitrate between VLM and TrOCR outputs; return the higher-confidence text.

        Args:
            vlm_result: Text from VLM model
            image_base64: Base64-encoded image for TrOCR
            vlm_confidence: Confidence score from VLM heuristic

        Returns:
            Winning text (VLM, TrOCR, or VLM-corrected)
            Returns vlm_result if TrOCR is unavailable or fails.
        """
        if self.trocr_engine is None:
            return vlm_result

        try:
            from omniscribe.core.ocr.trocr import _heuristic_confidence

            image_bytes = base64.b64decode(image_base64)
            trocr_res = await self.trocr_engine.recognize(image_bytes)
            if trocr_res.confidence > vlm_confidence:
                correction_prompt = fill_dual_engine_crop(trocr_res.text)
                vlm_corrected = await self._chat(
                    correction_prompt,
                    image_base64,
                    timeout=self.crop_timeout_s,
                    max_tokens=self.crop_max_tokens,
                    system_prompt=self._resolve_crop_system(
                        handwriting_mode=getattr(self, "handwriting_mode", False),
                        dual_engine=True,
                    ),
                )
                vlm_corrected_body = _strip_yaml_front_matter(vlm_corrected)
                vlm_corrected_res = " ".join(
                    line.strip()
                    for line in vlm_corrected_body.split("\n")
                    if line.strip()
                )
                if _is_fallback_response(vlm_corrected_res):
                    vlm_corrected_res = ""

                vlm_corr_conf = _heuristic_confidence(vlm_corrected_res)
                if trocr_res.confidence > vlm_corr_conf:
                    return trocr_res.text
                else:
                    return vlm_corrected_res
            else:
                return vlm_result
        except Exception as e:
            # TrOCR is optional; a failure here must not poison the
            # surrounding OCR result. Log and return the VLM's best effort.
            logger.warning("TrOCR arbitration failed: %s", e)
            return vlm_result

    async def perform_ocr_on_crop(
        self,
        image_base64: str,
        self_correction: bool = False,
        binarize: bool = False,
        dual_engine: bool = False,
        repair_hint: str | None = None,
        temperature: float | None = None,
    ) -> str:
        """OCR a single cropped box region. Returns a single whitespace-joined string.

        Empty-string for blank/uncertain crops (filtered hallucination).

        ``self_correction`` runs a second VLM pass with the correction prompt
        and unconditionally replaces the first-pass text (accept-always); an
        empty correction pass returns "".

        ``repair_hint`` (quality repair loop) is appended verbatim after
        sanitization so the VLM sees its rejected previous attempt;
        ``temperature`` overrides the crop-call default (the repair loop
        bumps it per retry attempt).
        """
        if binarize:
            image_base64 = await asyncio.to_thread(
                self._apply_adaptive_threshold, image_base64
            )

        handwriting_mode = getattr(self, "handwriting_mode", False)
        prompt = HANDWRITING_CROP_PROMPT if handwriting_mode else CROP_PROMPT
        if dual_engine:
            draft = await asyncio.to_thread(self._get_tesseract_draft, image_base64)
            if draft:
                prompt = fill_dual_engine_crop(draft)

        if repair_hint:
            prompt = prompt + sanitize_prompt_input(repair_hint)

        crop_system = self._resolve_crop_system(
            handwriting_mode=handwriting_mode, dual_engine=dual_engine
        )

        # ``temperature`` is only forwarded when the caller overrides it so
        # legacy ``_chat`` overrides (which predate the kwarg) keep working.
        chat_kwargs: dict[str, float] = {}
        if temperature is not None:
            chat_kwargs["temperature"] = temperature
        text = await self._chat(
            prompt,
            image_base64,
            timeout=self.crop_timeout_s,
            max_tokens=self.crop_max_tokens,
            system_prompt=crop_system,
            **chat_kwargs,
        )
        if not text:
            return ""

        if self_correction:
            correction_prompt = fill_correction_crop(text)
            text = await self._chat(
                correction_prompt,
                image_base64,
                timeout=self.crop_timeout_s,
                max_tokens=self.crop_max_tokens,
                system_prompt=crop_system,
            )
            if not text:
                return ""

        body = _strip_yaml_front_matter(text)
        result = " ".join(line.strip() for line in body.split("\n") if line.strip())
        if _is_fallback_response(result):
            result = ""

        # Phase A.2 (review M3) — TrOCR dual-engine arbitration.
        # See _run_trocr_arbitration() for details on the arbitration logic.
        if getattr(self, "handwriting_mode", False) and self.trocr_engine is not None:
            from omniscribe.core.ocr.trocr import _heuristic_confidence

            vlm_conf = _heuristic_confidence(result)
            if vlm_conf < self.confidence_threshold:
                result = await self._run_trocr_arbitration(
                    result, image_base64, vlm_conf
                )

        return result

    async def _chat(
        self,
        prompt: str,
        image_base64: str,
        *,
        timeout: float,
        max_tokens: int,
        system_prompt: str | None = None,
        temperature: float | None = None,
    ) -> str:
        """Backward-compat thin wrapper around the :class:`ChatClient`.

        The retry / circuit-breaker / context-length-error-translation
        logic lives in ``omniscribe.core.ocr.chat_client.ChatClient``;
        OCRProcessor only owns the prompt construction + post-OCR
        filtering pipeline. This thin method exists so that:

        - legacy test suites that do ``ocr._chat = fake`` (the
          ``OCRProcessor.__new__`` path) keep overriding a callable on
          the instance.
        - the per-call timeout / max_tokens / system_prompt parameters
          that callers already pass continue to flow through unchanged.

        The wrapper also re-syncs ``self._chat_client.circuit_breaker``
        from ``self.circuit_breaker`` before each call so a test that
        swaps the breaker on the processor (e.g.
        ``p.circuit_breaker = CircuitBreaker(...)``) takes effect
        without rebuilding the client.
        """
        self._chat_client.circuit_breaker = self.circuit_breaker
        return await self._chat_client.chat(
            prompt,
            image_base64,
            timeout=timeout,
            max_tokens=max_tokens,
            system_prompt=system_prompt,
            temperature=temperature,
        )

    def _get_tesseract_draft(self, image_base64: str) -> str:
        try:
            import io

            import pytesseract
            from PIL import Image

            image_bytes = base64.b64decode(image_base64)
            # H1 audit fix: ``with`` block guarantees buffer close.
            with Image.open(io.BytesIO(image_bytes)) as img:
                # Fallback to multiple common languages (or just Arabic/English for this workload)
                draft: str = pytesseract.image_to_string(img, lang="ara+eng")
                return draft.strip()
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            # ``ImportError`` covers the soft-dep case where pytesseract or
            # PIL is not installed in this environment; ``RuntimeError``
            # covers ``pytesseract.TesseractError`` (TesseractError subclasses
            # RuntimeError) and any subprocess failure; ``OSError`` covers
            # PIL file errors and tesseract binary-not-found; ``ValueError``
            # covers malformed base64 input.
            #
            # F1.13 audit fix: increment the per-instance counter so the
            # API layer can surface a stuck dual-engine path in the
            # job-completion summary without log scraping. The counter
            # is process-local; the API layer aggregates per-job counts
            # when constructing the summary.
            self.tesseract_error_count += 1
            logger.warning(
                "OCR pytesseract fallback failed: %s",
                exc,
                exc_info=True,
            )
            return ""

    def _resolve_page_system(
        self,
        *,
        prompt: str,
        handwriting_mode: bool,
        dual_engine: bool,
    ) -> str | None:
        """Pick the right system message for a page-level OCR call.

        Two reasons to return ``None``:

        1. The canonical OLMOCR-2 page prompt is in use — the model
           was RL-trained on it as a pure user message; a system role
           would shift the distribution.
        2. The active model is one of the system-role-excluded
           families (currently just OlmOCR). Sending a system role
           causes LM Studio + OlmOCR-2 to misbehave on the crop /
           handwriting / dual-engine paths.
        """
        if prompt is OLMOCR_PAGE_PROMPT:
            return None
        if not model_supports_system_role(self.model):
            return None
        return select_system_message(
            handwriting_mode=handwriting_mode, dual_engine=dual_engine
        )

    def _resolve_crop_system(
        self,
        *,
        handwriting_mode: bool,
        dual_engine: bool,
    ) -> str | None:
        """Pick the right system message for a crop-level OCR call.

        Crop calls never use the canonical OLMOCR page prompt, so
        reason #1 from :meth:`_resolve_page_system` doesn't apply.
        The only thing that can suppress the system message here
        is the active model being system-role-excluded.
        """
        if not model_supports_system_role(self.model):
            return None
        return select_system_message(
            handwriting_mode=handwriting_mode, dual_engine=dual_engine
        )

    def _apply_adaptive_threshold(self, image_base64: str) -> str:
        """Adaptive mean threshold using only PIL (no OpenCV dependency).

        Approximates ``cv2.adaptiveThreshold(..., ADAPTIVE_THRESH_GAUSSIAN_C,
        THRESH_BINARY, 21, 15)`` with a box-blur local mean. The Gaussian
        vs uniform kernel difference is negligible for handwriting
        binarization at block_size=21.
        """
        try:
            import io

            import numpy as np
            from PIL import Image, ImageFilter

            img = Image.open(io.BytesIO(base64.b64decode(image_base64))).convert("L")

            # Local mean via box blur (radius 10 ~ block_size 21).
            local_mean = img.filter(ImageFilter.BoxBlur(radius=10))

            # Adaptive threshold: pixel is white (255) if src > local_mean - C.
            # C=15 matches the old cv2.adaptiveThreshold constant parameter.
            src_arr = np.asarray(img, dtype=np.int16)
            mean_arr = np.asarray(local_mean, dtype=np.int16)
            binary_arr = np.where(src_arr > mean_arr - 15, 255, 0).astype(np.uint8)
            binary = Image.fromarray(binary_arr, mode="L")

            buf = io.BytesIO()
            binary.save(buf, format="PNG")
            return base64.b64encode(buf.getvalue()).decode("utf-8")
        except (ImportError, OSError, ValueError) as exc:
            # ``ImportError`` covers the case where numpy or PIL is not
            # installed in this environment; ``OSError`` covers PIL file
            # errors and array-to-image conversion failures; ``ValueError``
            # covers malformed base64 input and array-shape mismatches.
            logger.warning(
                "OCR adaptive threshold fallback failed: %s",
                exc,
                exc_info=True,
            )
            return image_base64


__all__ = ["OCRProcessor"]
