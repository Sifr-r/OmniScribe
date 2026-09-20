"""LLM-backed OCR.

Sub-package layout:

- :mod:`.exceptions` — :class:`LLMCallError`, :class:`ModelNotLoadedError`,
  :class:`LLMBalanceError`
- :mod:`.prompts` — prompt constants and selection/fill helpers
- :mod:`.filters` — output sanitization (YAML strip, fallback suppression,
  runaway-repetition clip)
- :mod:`.client` — pre-flight ``GET /v1/models`` helpers
- :mod:`.resilience` — retry classification and circuit breaker
- :mod:`.processor` — :class:`OCRProcessor` itself

This ``__init__`` re-exports the *public* surface exactly as it appeared
on the old ``omniscribe/core/ocr.py`` module — both the test suite
(``tests/core/ocr/test_ocr.py``) and downstream callers (the ground engine,
processors, routers) import against ``omniscribe.core.ocr`` and must
keep working without modification.
"""

from __future__ import annotations

from omniscribe.core.ocr.client import (
    _format_model_not_loaded,
    _list_loaded_model_ids,
    _model_in_loaded,
)
from omniscribe.core.ocr.exceptions import (
    LLMBalanceError,
    LLMCallError,
    ModelNotLoadedError,
)
from omniscribe.core.ocr.filters import (
    _HALLUCINATION_PATTERNS,
    _is_fallback_response,
    _strip_runaway_repetition,
    _strip_yaml_front_matter,
)
from omniscribe.core.ocr.multi_format_client import complete_vlm_prompt
from omniscribe.core.ocr.processor import OCRProcessor
from omniscribe.core.ocr.prompts import (
    CORRECTION_CROP_PROMPT,
    CORRECTION_PAGE_PROMPT,
    CROP_PROMPT,
    DUAL_ENGINE_CROP_PROMPT,
    DUAL_ENGINE_PAGE_PROMPT,
    HANDWRITING_CROP_PROMPT,
    HANDWRITING_PAGE_PROMPT,
    OLMOCR_PAGE_PROMPT,
    fill_correction_crop,
    fill_correction_page,
    fill_dual_engine_crop,
    fill_dual_engine_page,
    select_crop_prompt,
    select_page_prompt,
)
from omniscribe.core.ocr.resilience import (
    CircuitBreaker,
    CircuitOpenError,
    is_transient_error,
)

# Underscore-prefixed names are still re-exported for tests and the
# grounded engine, which share the pre-flight check and the filters.
__all__ = [
    "CORRECTION_CROP_PROMPT",
    "CORRECTION_PAGE_PROMPT",
    "CROP_PROMPT",
    "CircuitBreaker",
    "CircuitOpenError",
    "DUAL_ENGINE_CROP_PROMPT",
    "DUAL_ENGINE_PAGE_PROMPT",
    "HANDWRITING_CROP_PROMPT",
    "HANDWRITING_PAGE_PROMPT",
    "LLMBalanceError",
    "LLMCallError",
    "ModelNotLoadedError",
    "OCRProcessor",
    "OLMOCR_PAGE_PROMPT",
    "_HALLUCINATION_PATTERNS",
    "_format_model_not_loaded",
    "_is_fallback_response",
    "_list_loaded_model_ids",
    "_model_in_loaded",
    "_strip_runaway_repetition",
    "_strip_yaml_front_matter",
    "complete_vlm_prompt",
    "fill_correction_crop",
    "fill_correction_page",
    "fill_dual_engine_crop",
    "fill_dual_engine_page",
    "is_transient_error",
    "select_crop_prompt",
    "select_page_prompt",
]
