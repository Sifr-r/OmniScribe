"""Shared bbox-overlap geometry for the recall sources.

The whitespace and text-layer recall sources previously carried
line-for-line copies of the containment / IoU / straddle checks,
differing only in the containment threshold (0.5 vs 0.6). The math
lives here once; each source passes its own thresholds.

``is_duplicate`` is the fused hot-path predicate: it computes each
candidate/existing intersection once and applies the containment, IoU,
and straddle conditions together, instead of walking the existing-box
list twice (once for overlap, once for straddle) and recomputing the
same intersections.
"""

from __future__ import annotations

from omniscribe.core.document import BBox


def overlaps(
    candidate: BBox,
    existing_boxes: list[BBox],
    *,
    max_containment: float,
    max_iou: float,
) -> bool:
    """True when the candidate is already explained by an existing box
    (containment of the candidate, or IoU, beyond the thresholds)."""
    cx0, cy0, cx1, cy1 = candidate
    c_area = max(1e-9, (cx1 - cx0) * (cy1 - cy0))
    for bx0, by0, bx1, by1 in existing_boxes:
        ix0, iy0 = max(cx0, bx0), max(cy0, by0)
        ix1, iy1 = min(cx1, bx1), min(cy1, by1)
        if ix1 <= ix0 or iy1 <= iy0:
            continue
        inter = (ix1 - ix0) * (iy1 - iy0)
        if inter / c_area >= max_containment:
            return True
        b_area = max(1e-9, (bx1 - bx0) * (by1 - by0))
        if inter / (c_area + b_area - inter) >= max_iou:
            return True
    return False


def straddles(
    candidate: BBox,
    existing_boxes: list[BBox],
    *,
    straddle_min_overlap: float,
) -> bool:
    """True when the candidate spans >= 2 existing boxes (gutter/stacked lines)."""
    cx0, cy0, cx1, cy1 = candidate
    c_area = max(1e-9, (cx1 - cx0) * (cy1 - cy0))
    overlapped = 0
    for bx0, by0, bx1, by1 in existing_boxes:
        ix0, iy0 = max(cx0, bx0), max(cy0, by0)
        ix1, iy1 = min(cx1, bx1), min(cy1, by1)
        if ix1 <= ix0 or iy1 <= iy0:
            continue
        if (ix1 - ix0) * (iy1 - iy0) / c_area >= straddle_min_overlap:
            overlapped += 1
            if overlapped >= 2:
                return True
    return False


def is_duplicate(
    candidate: BBox,
    existing_boxes: list[BBox],
    *,
    max_containment: float,
    max_iou: float,
    straddle_min_overlap: float,
) -> bool:
    """Single-pass fused equivalent of ``overlaps(...) or straddles(...)``.

    Each intersection is computed once; the per-box containment / IoU
    checks double as the straddle-count gate, matching the split
    predicates' outcome exactly.
    """
    cx0, cy0, cx1, cy1 = candidate
    c_area = max(1e-9, (cx1 - cx0) * (cy1 - cy0))
    overlapped = 0
    for bx0, by0, bx1, by1 in existing_boxes:
        ix0, iy0 = max(cx0, bx0), max(cy0, by0)
        ix1, iy1 = min(cx1, bx1), min(cy1, by1)
        if ix1 <= ix0 or iy1 <= iy0:
            continue
        inter = (ix1 - ix0) * (iy1 - iy0)
        if inter / c_area >= max_containment:
            return True
        b_area = max(1e-9, (bx1 - bx0) * (by1 - by0))
        if inter / (c_area + b_area - inter) >= max_iou:
            return True
        if inter / c_area >= straddle_min_overlap:
            overlapped += 1
            if overlapped >= 2:
                return True
    return False


__all__ = ["is_duplicate", "overlaps", "straddles"]
