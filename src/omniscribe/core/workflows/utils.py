from __future__ import annotations

import math
import re
from collections.abc import Sequence
from typing import TYPE_CHECKING

from omniscribe.core.pdf.page_range import (
    parse_page_range_with_total as parse_page_range,
)

if TYPE_CHECKING:
    from omniscribe.core.workflows.base import PageBoxes


__all__ = [
    "DETECT_CHUNK_SIZE",
    "REFINABLE_MIN_HEIGHT",
    "REFINABLE_MIN_WIDTH",
    "WELL_FORMED_CONFIDENCE",
    "_drop_refined_duplicates",
    "_estimate_confidence",
    "_is_refinable",
    "_normalize_for_dedup",
    "parse_page_range",
    "validate_bbox_coordinates",
]


# Confidence awarded to well-formed OCR output (multiple words, mostly
# alphabetic). Deliberately above the default quality-loop target (0.98)
# so healthy blocks are never repair candidates; noisy text lands in the
# 0.85 band below it. See spec §3.2.
WELL_FORMED_CONFIDENCE = 0.99

_NUMERIC_EXPR_PATTERN = re.compile(r"^[\d\s.,:/\-+$€£%()#№]+$")


def validate_bbox_coordinates(
    bbox: Sequence[float],
    *,
    clamp: bool = True,
) -> tuple[float, float, float, float]:
    """Validate and optionally clamp a 4-element normalized bounding box [x0, y0, x1, y1].

    Ensures coordinates are 4 finite numbers, ordered such that x0 <= x1 and y0 <= y1.
    If ``clamp`` is True (default), coordinates are bounded to the [0.0, 1.0] interval.
    """
    if len(bbox) != 4:
        raise ValueError(f"Expected 4 coordinate values for bbox, got {len(bbox)}")
    try:
        x0, y0, x1, y1 = float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])
    except (ValueError, TypeError) as exc:
        raise ValueError(f"BBox coordinates must be real numbers, got {bbox}") from exc

    if any(math.isnan(c) or math.isinf(c) for c in (x0, y0, x1, y1)):
        raise ValueError(
            f"BBox coordinates must be finite numbers, got {(x0, y0, x1, y1)}"
        )

    if clamp:
        x0 = max(0.0, min(1.0, x0))
        y0 = max(0.0, min(1.0, y0))
        x1 = max(0.0, min(1.0, x1))
        y1 = max(0.0, min(1.0, y1))

    if x0 > x1:
        x0, x1 = x1, x0
    if y0 > y1:
        y0, y1 = y1, y0

    return (x0, y0, x1, y1)


def _estimate_confidence(text: str) -> float:
    """Cheap confidence proxy for OCR output.

    Returns a value in [0, 1] based on text quality signals:
    - empty / whitespace => 0.0
    - valid numeric / date / currency / phone / punctuation expressions => 0.99
    - no alphabetic characters at all => 0.3
    - 3+ words that are mostly alphabetic => 0.99 (clears the default
      0.98 quality target so healthy text is not re-OCRed)
    - 3+ words with heavy digit/punctuation noise => 0.85
    - 1-2 words with enough alpha => 0.7
    - anything else => 0.4
    """
    if not text or not text.strip():
        return 0.0
    stripped = text.strip()
    alpha = sum(1 for c in stripped if c.isalpha())
    if alpha == 0:
        if _NUMERIC_EXPR_PATTERN.match(stripped):
            return WELL_FORMED_CONFIDENCE
        return 0.3
    words = stripped.split()
    if len(words) >= 3:
        return WELL_FORMED_CONFIDENCE if alpha / len(stripped) >= 0.6 else 0.85
    if len(words) >= 1 and alpha >= 3:
        return 0.7
    return 0.4


# A bbox is "refinable" if it has enough normalized area to be worth a
# per-crop re-OCR pass; below these sizes the LLM round-trip costs more
# than it gains.
REFINABLE_MIN_WIDTH = 0.03
REFINABLE_MIN_HEIGHT = 0.008

# Surya layout detection is batched; this chunk size keeps memory + GPU pressure
# predictable without dominating the detect stage wall clock.
DETECT_CHUNK_SIZE = 10


def _normalize_for_dedup(text: str) -> str:
    return " ".join(text.lower().split())


def _drop_refined_duplicates(
    page_boxes: PageBoxes,
    refined_indices: set[int],
    *,
    radius: int = 4,
) -> None:
    for r_idx in sorted(refined_indices):
        r_bbox, r_text = page_boxes[r_idx]
        if not r_text:
            continue
        r_norm = _normalize_for_dedup(r_text)
        if not r_norm:
            continue
        lo = max(0, r_idx - radius)
        hi = min(len(page_boxes), r_idx + radius + 1)
        for o_idx in range(lo, hi):
            if o_idx == r_idx or o_idx in refined_indices:
                continue
            _, o_text = page_boxes[o_idx]
            if not o_text:
                continue
            o_norm = _normalize_for_dedup(o_text)
            o_tokens = o_norm.split()
            r_tokens = r_norm.split()
            if (
                r_norm == o_norm
                or (
                    r_tokens
                    and len(r_tokens) >= 2
                    and all(tok in o_tokens for tok in r_tokens)
                )
                or (len(r_tokens) == 1 and r_tokens[0] in o_tokens)
            ):
                page_boxes[r_idx] = (r_bbox, "")
                break


def _is_refinable(bbox: Sequence[float]) -> bool:
    if len(bbox) != 4:
        return False
    try:
        x0, y0, x1, y1 = float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])
    except (ValueError, TypeError):
        return False
    if any(math.isnan(c) or math.isinf(c) for c in (x0, y0, x1, y1)):
        return False
    width = x1 - x0
    height = y1 - y0
    return width > REFINABLE_MIN_WIDTH and height > REFINABLE_MIN_HEIGHT
