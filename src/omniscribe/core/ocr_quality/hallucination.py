"""Hallucination detection for the OCR quality trust layer.

Three cheap heuristics tuned for VLM OCR output:

1. **Repetition** — VLMs often loop on a single token. We scan the
   block for any substring of length ``repetition_window`` repeated
   ``>= 3`` times within the block.
2. **Length plausibility** — blocks with near-empty text but a large
   bbox (very few characters per pixel) are suspicious.
3. **Cross-check** *(opt-in)* — re-OCR the crop with a second prompt
   and compute normalised Levenshtein divergence against the original.
   Divergence above ``cross_check_threshold`` bumps the risk level by
   one. Disabled by default — the second VLM call is expensive.

The guard never raises. Any failure returns ``HallucinationRisk.LOW``
which carries zero trust penalty (NONE/LOW share the same value).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable

from .events import emit
from .types import HallucinationRisk

_LOG = logging.getLogger(__name__)


_REPETITION_DEFAULT = 6
_LENGTH_MIN_DEFAULT = 0.0001
_CROSS_CHECK_THRESHOLD_DEFAULT = 0.4

# Markers VLMs emit when they "give up" (per HalluText, 2024).
_GIVEUP_MARKERS: tuple[str, ...] = (
    "▢▢▢",
    "[unreadable]",
    "[illegible]",
    "<unclear>",
    "????",
    ".....",
    "-----",
)

# Order matters for the bump-one-level ladder.
_LEVELS: tuple[HallucinationRisk, ...] = (
    HallucinationRisk.NONE,
    HallucinationRisk.LOW,
    HallucinationRisk.MEDIUM,
    HallucinationRisk.HIGH,
)


def _strong_bump(risk: HallucinationRisk) -> HallucinationRisk:
    """Bump risk two levels (e.g., ``NONE`` → ``MEDIUM``).

    Used for high-confidence signals like repetition loops and
    implausible length density that warrant a larger penalty on their
    own than a faint giveup marker.
    """
    idx = _LEVELS.index(risk)
    return _LEVELS[min(idx + 2, len(_LEVELS) - 1)]


def _has_repetition(text: str, window: int) -> bool:
    if len(text) < window * 3:
        return False
    seen: dict[str, int] = {}
    for i in range(len(text) - window + 1):
        chunk = text[i : i + window]
        seen[chunk] = seen.get(chunk, 0) + 1
        if seen[chunk] >= 3:
            return True
    return False


def _has_giveup_marker(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _GIVEUP_MARKERS)


def _length_plausibility(
    text: str,
    bbox: tuple[float, float, float, float] | None,
    min_chars_per_pixel_sq: float,
    page_size: tuple[int, int] | None = None,
) -> bool:
    """Return True when the text density looks reasonable for ``bbox``."""
    if bbox is None or page_size is None:
        return True
    x0, y0, x1, y1 = bbox
    page_w, page_h = page_size
    # Audit P2-9: bbox area is ``(x1-x0) * (y1-y0)``, not ``x1 * y1``.
    # The old formula measured the rectangle from the page origin, so any
    # box away from (0, 0) inflated the pixel area and flagged legitimate
    # blocks as implausible.
    pixel_area = max(1.0, (x1 - x0) * page_w * (y1 - y0) * page_h)
    return len(text.strip()) >= pixel_area * min_chars_per_pixel_sq


def _normalised_levenshtein(a: str, b: str, max_len: int = 1024) -> float:
    """Edit distance divided by the longer string length, clamped to ``[0, 1]``."""
    if not a and not b:
        return 0.0
    if len(a) > max_len:
        a = a[:max_len]
    if len(b) > max_len:
        b = b[:max_len]
    if not a or not b:
        return 1.0
    # Pure-Python Wagner-Fischer - fast enough for OCR-block-size strings.
    n, m = len(a), len(b)
    prev = list(range(m + 1))
    for i in range(1, n + 1):
        cur = [i] + [0] * m
        for j in range(1, m + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(
                cur[j - 1] + 1,
                prev[j] + 1,
                prev[j - 1] + cost,
            )
        prev = cur
    return prev[m] / max(n, m)


def evaluate(
    text: str,
    bbox: tuple[float, float, float, float] | None,
    *,
    page_size: tuple[int, int] | None = None,
    repetition_window: int = _REPETITION_DEFAULT,
    length_plausibility_min: float = _LENGTH_MIN_DEFAULT,
    cross_check: bool = False,
    cross_check_fn: Callable[[str, tuple[float, float, float, float]], str]
    | None = None,
    cross_check_threshold: float = _CROSS_CHECK_THRESHOLD_DEFAULT,
) -> HallucinationRisk:
    """Return the :class:`HallucinationRisk` for ``text``.

    The function is total — it never raises. Returns ``NONE`` for empty
    text and ``LOW`` on any unexpected exception (zero trust penalty).
    """
    started = _now_ms()
    try:
        if not text or not text.strip():
            return HallucinationRisk.NONE
        risk = HallucinationRisk.NONE
        if _has_giveup_marker(text):
            risk = _strong_bump(risk)
        if _has_repetition(text, repetition_window):
            risk = _strong_bump(risk)
        if not _length_plausibility(text, bbox, length_plausibility_min, page_size):
            risk = _strong_bump(risk)
        if cross_check and cross_check_fn is not None and bbox is not None:
            try:
                second = cross_check_fn(text, bbox)
                divergence = _normalised_levenshtein(text, second)
                if divergence > cross_check_threshold:
                    risk = _strong_bump(risk)
            except Exception as exc:
                _LOG.debug("cross-check failed: %s", exc)
                emit(
                    "hallucination",
                    doc_id="-",
                    page=-1,
                    duration_ms=_now_ms() - started,
                    decision="cross_check_error",
                    fallback_used=True,
                )
                # Cross-check failed: can't trust the signal, flag at
                # least LOW (zero penalty but recorded in metadata).
                if risk is HallucinationRisk.NONE:
                    risk = HallucinationRisk.LOW
        emit(
            "hallucination",
            doc_id="-",
            page=-1,
            duration_ms=_now_ms() - started,
            decision=risk.value,
            fallback_used=False,
        )
        return risk
    except Exception as exc:
        _LOG.debug("hallucination guard failed: %s", exc)
        emit(
            "hallucination",
            doc_id="-",
            page=-1,
            duration_ms=_now_ms() - started,
            decision="error",
            fallback_used=True,
        )
        return HallucinationRisk.LOW


def evaluate_many(
    blocks: Iterable[tuple[str, tuple[float, float, float, float] | None]],
    **kwargs: object,
) -> list[HallucinationRisk]:
    """Vectorised convenience wrapper.

    Each item is ``(text, bbox)``. Useful for the orchestrator's
    per-page batch.
    """
    return [evaluate(text, bbox, **kwargs) for text, bbox in blocks]  # type: ignore[arg-type]


# Backwards-compat alias for the original Task 5 spec name.
run = evaluate


def _now_ms() -> int:
    import time

    return int(time.monotonic() * 1000)


# ``re`` is imported for symmetry with planned future regex-based markers.
_ = re

__all__ = ["evaluate", "evaluate_many", "run"]
