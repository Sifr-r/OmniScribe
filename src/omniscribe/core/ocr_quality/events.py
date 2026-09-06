"""Structured log channel for the OCR quality trust layer.

Every sub-module emits one event per call via :func:`emit`. The events
land on the ``omniscribe.core.ocr_quality.events`` logger so ops can
route them independently from the rest of the OCR pipeline's noise.
"""

from __future__ import annotations

import contextlib
import logging

_LOG = logging.getLogger("omniscribe.core.ocr_quality.events")


def emit(
    sub_module: str,
    *,
    doc_id: str,
    page: int,
    duration_ms: int,
    decision: str,
    fallback_used: bool,
) -> None:
    """Emit one structured event. Never raises.

    Parameters
    ----------
    sub_module:
        One of ``"watermark"``, ``"script_detect"``, ``"hallucination"``,
        ``"calibration"``, ``"trust_scorer"``, ``"orchestrator"``,
        ``"parsers"``.
    doc_id:
        Opaque document identifier (``"-"`` when not yet assigned).
    page:
        1-indexed page number (``-1`` for whole-document events).
    duration_ms:
        Wall-clock duration in milliseconds (rounded down).
    decision:
        Sub-module-specific outcome string (``"hit"``, ``"none"``,
        ``"identity"``, ``risk.value``, etc.).
    fallback_used:
        True when the sub-module failed and returned a passthrough.
    """
    # Logging must never crash callers — swallow every exception.
    with contextlib.suppress(Exception):
        _LOG.debug(
            "ocr_quality_event sub_module=%s doc_id=%s page=%s duration_ms=%s decision=%s fallback_used=%s",
            sub_module,
            doc_id,
            page,
            duration_ms,
            decision,
            fallback_used,
            extra={
                "ocr_quality_sub_module": sub_module,
                "ocr_quality_doc_id": doc_id,
                "ocr_quality_page": page,
                "ocr_quality_duration_ms": duration_ms,
                "ocr_quality_decision": decision,
                "ocr_quality_fallback_used": fallback_used,
            },
        )


__all__ = ["emit"]
