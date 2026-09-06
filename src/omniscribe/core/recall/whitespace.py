"""Secondary whitespace-based text discovery for the hybrid pipeline.

Surya layout detection occasionally misses individual text lines; a missed
box means lost text on dense pages and mis-placed text on sparse pages.
This module masks away a rendered page's whitespace (binarize + invert),
merges the remaining ink into line blobs via horizontal dilation, and
returns conservative candidate boxes for regions Surya did not detect.
``HybridEngine._detect_layout`` merges them into the detected boxes before
dense selection, OCR, and DP alignment.

Known limitation (premise-gate P2, 2026-08): the pass discovers
line-shaped ink blobs; it cannot distinguish text from text-like
noise (photo edges, figure borders, thin form rules), and it merges
stacked lines when the inter-line gap is smaller than the dilation
kernel height. Conservative filters trade recall for precision here;
see ``docs/ARCHITECTURE.md`` and
``.autoplan/phase1-ceo-report.md`` (gate addendum G2/G4).
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass

from PIL import Image

from omniscribe.core.document import BBox
from omniscribe.core.recall import (
    MAX_RECALL_BOXES_PER_PAGE,
    STRADDLE_MIN_OVERLAP,
    geometry,
)
from omniscribe.utils.env import DISABLE_STRINGS, env_str

logger = logging.getLogger(__name__)

_ENV_RECALL = "OMNISCRIBE_WHITESPACE_RECALL"

# Horizontal dilation kernel sizing (ratios of page dimensions, clamped).
# The kernel must bridge inter-character gaps without fusing stacked lines.
_DILATION_WIDTH_DIVISOR = 48
_DILATION_HEIGHT_DIVISOR = 150
_KERNEL_W_RANGE = (7, 35)
_KERNEL_H_RANGE = (3, 11)

# Conservative candidate filters.
_MIN_ASPECT_RATIO = 2.0
# T7 retune attempt (2026-08-15) lowered this floor 0.10 -> 0.06 to admit
# the digital.pdf faculty-name block (density 0.08); measured cost was +15
# junk boxes corpus-wide vs +1 recovery, so the floor stays at 0.10. The
# faculty block remains unrecovered: documented limitation, not worth the
# precision trade (see scripts/measure_recall_delta.py).
_MIN_INK_DENSITY = 0.10
_MAX_INK_DENSITY = 0.75
_MIN_HEIGHT_FRACTION = 0.45
_FALLBACK_MIN_HEIGHT = 0.006
# Merged-blob guard: horizontal dilation bridges inter-line gaps smaller
# than the kernel height, so stacked lines and diagram regions fuse into
# one tall component. T7 measured this as the dominant junk class on the
# examples corpus (extras 3-10x the median line height). Real text lines
# sit near the median Surya height; cap candidates at 2.5x that median.
# Sensitivity measured on the harness: 5.0 admits +3 extras (+2 junk) and
# recovers nothing extra, so 2.5 stays.
_MAX_HEIGHT_FRACTION = 2.5
_FALLBACK_MAX_HEIGHT = 0.06
_MAX_AREA_FRACTION = 0.25
# Post-dilation hairline rules land at ~3-8 px while real text lines render
# at ~10 px or more at every supported rasterization size, so an absolute
# pixel floor rejects rules that survive the density check. T7 measured
# that form-blank underscore segments render at ~6 px and cannot be
# separated from hairline rules on pixel statistics alone; they stay
# excluded (documented limitation; re-measure impact via
# ``scripts/measure_recall_delta.py`` before changing this constant).
_MIN_COMPONENT_HEIGHT_PX = 10
# Otsu-invert models whitespace as background. When the foreground fraction
# is this large the model has inverted (dark-mode / inverted scan page) and
# every filter assumption breaks, so the page is skipped wholesale.
_MAX_FOREGROUND_FRACTION = 0.5
# Junk-blast-radius bound: at most this many recall boxes per page, keeping
# the rest by ink density (most text-like first). Measured worst case on
# the examples corpus was 6 extras/page; the cap protects pathological
# layouts and bounds n_boxes inflation toward dense_threshold.
_MAX_RECALL_BOXES_PER_PAGE = MAX_RECALL_BOXES_PER_PAGE
_MAX_WHITESPACE_BOXES_PER_PAGE = MAX_RECALL_BOXES_PER_PAGE

# Dedup against Surya boxes. Containment catches a candidate that is mostly
# inside an existing box (a partial duplicate); IoU catches a candidate that
# nearly coincides with one even if neither fully contains the other.
_MAX_CONTAINMENT = 0.5
_MAX_IOU = 0.3
# Straddle guard: a candidate intersecting >= 2 Surya boxes this much (as a
# fraction of its own area) spans a gutter or stacked detected lines; it is
# rejected outright (never split) — a wide cross-column box would feed
# garbled two-column text to per-box OCR.
_STRADDLE_MIN_OVERLAP = STRADDLE_MIN_OVERLAP


@dataclass(frozen=True, slots=True)
class WhitespaceRecallOptions:
    enabled: bool = True

    @classmethod
    def from_env(cls) -> WhitespaceRecallOptions:
        """Seed from ``OMNISCRIBE_WHITESPACE_RECALL`` (default on).

        Only explicit disable values (``0``/``false``/``no``/``off``/
        ``n``/``disabled``, case-insensitive) turn the pass off; unset
        or unrecognized values keep it enabled.

        The env read goes through :func:`omniscribe.utils.env.env_str`
        (audit H3) so this module no longer imports ``os``.
        """
        raw = (env_str(_ENV_RECALL) or "").strip().lower()
        return cls(enabled=raw not in DISABLE_STRINGS)


class WhitespaceRecallBooster:
    """Recovers text-line boxes Surya missed via whitespace masking.

    Requires ``opencv-python-headless`` and ``numpy`` at runtime (the
    ``preprocessing`` extra). When they are missing the booster logs one
    warning and returns no boxes, leaving pipeline output unchanged.
    """

    def __init__(self, options: WhitespaceRecallOptions | None = None) -> None:
        self.options = options or WhitespaceRecallOptions()
        self._cv2_warned = False
        # Run-level observability counter (plan task T2): components seen by
        # ``connectedComponentsWithStats`` minus boxes finally returned, so
        # ``HybridEngine`` can report how much the filters dropped at INFO
        # without re-running the pass. Cumulative across ``supplement`` calls.
        self.candidates_dropped = 0

    @property
    def enabled(self) -> bool:
        """Kill-switch state — ``HybridEngine`` skips the pass when off (T6)."""
        return self.options.enabled

    def supplement(self, image: Image.Image, surya_boxes: list[BBox]) -> list[BBox]:
        """Return new text-line boxes not already covered by ``surya_boxes``.

        Returns only the *additional* boxes; the caller appends them. Empty
        when disabled, when cv2 is unavailable, or when nothing survives
        the filters.
        """
        if not self.options.enabled:
            return []
        try:
            import cv2
            import numpy as np
        except ImportError:
            if not self._cv2_warned:
                logger.warning(
                    "Whitespace recall disabled: opencv is not installed "
                    "(install the `preprocessing` extra to enable)."
                )
                self._cv2_warned = True
            return []

        gray = np.array(image.convert("L"))
        if gray.size == 0:
            return []
        h, w = gray.shape
        # Invert so ink is foreground and whitespace is masked away.
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        if cv2.countNonZero(binary) / binary.size > _MAX_FOREGROUND_FRACTION:
            # Inverted/dark page: the whitespace model no longer holds.
            return []

        kw = _clamp(w // _DILATION_WIDTH_DIVISOR, _KERNEL_W_RANGE)
        kh = _clamp(h // _DILATION_HEIGHT_DIVISOR, _KERNEL_H_RANGE)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kw, kh))
        dilated = cv2.dilate(binary, kernel)

        count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(
            dilated, connectivity=8
        )
        if count <= 1:
            return []

        if surya_boxes:
            # Pedantic 1.15: filter out zero-height boxes before taking
            # the median. The previous code used ``_FALLBACK_MIN_HEIGHT``
            # as the median when the filtered list was empty, which then
            # got multiplied by ``_MIN_HEIGHT_FRACTION`` and collapsed
            # the min-height floor to ~0.27% of page height (~3 px at
            # 1024 px) — accepting basically any horizontal stripe and
            # breaching the booster's documented precision stance. When
            # *every* surya box has zero height, fall through to the
            # absolute fallback band (the same one used for ``[]``).
            heights = [b[3] - b[1] for b in surya_boxes if b[3] - b[1] > 0]
            if heights:
                median_h = statistics.median(heights)
                min_height = _MIN_HEIGHT_FRACTION * median_h
                max_height = _MAX_HEIGHT_FRACTION * median_h
            else:
                min_height = _FALLBACK_MIN_HEIGHT
                max_height = _FALLBACK_MAX_HEIGHT
        else:
            min_height = _FALLBACK_MIN_HEIGHT
            max_height = _FALLBACK_MAX_HEIGHT

        candidates: list[tuple[BBox, float]] = []
        for i in range(1, count):
            x, y, bw, bh = (int(v) for v in stats[i, :4])
            if bh < _MIN_COMPONENT_HEIGHT_PX:
                continue
            nx0, ny0 = x / w, y / h
            nx1, ny1 = (x + bw) / w, (y + bh) / h
            nw, nh = nx1 - nx0, ny1 - ny0
            if nw < _MIN_ASPECT_RATIO * nh:
                continue
            if nh < min_height or nh > max_height or nw * nh > _MAX_AREA_FRACTION:
                continue
            # Ink density on the PRE-dilation mask: dilated blobs are nearly
            # solid, real glyph lines sit ~0.2-0.6, solid rules ~1.0.
            rect = binary[y : y + bh, x : x + bw]
            density = cv2.countNonZero(rect) / max(1, bw * bh)
            if not _MIN_INK_DENSITY <= density <= _MAX_INK_DENSITY:
                continue
            candidates.append(((nx0, ny0, nx1, ny1), density))

        kept = [
            (box, density)
            for box, density in candidates
            if not geometry.is_duplicate(
                box,
                surya_boxes,
                max_containment=_MAX_CONTAINMENT,
                max_iou=_MAX_IOU,
                straddle_min_overlap=_STRADDLE_MIN_OVERLAP,
            )
        ]
        if len(kept) > _MAX_RECALL_BOXES_PER_PAGE:
            # Most text-like (highest ink density) candidates win the cap.
            kept.sort(key=lambda item: item[1], reverse=True)
            kept = kept[:_MAX_RECALL_BOXES_PER_PAGE]
        # Every non-background component that did not become a returned box
        # was dropped by the filter family (T2 run-summary counter).
        self.candidates_dropped += (count - 1) - len(kept)
        return [box for box, _density in kept]


def _clamp(value: int, bounds: tuple[int, int]) -> int:
    lo, hi = bounds
    return max(lo, min(hi, value))


def _overlaps_surya(candidate: BBox, surya_boxes: list[BBox]) -> bool:
    """True when the candidate is already explained by a Surya box."""
    return geometry.overlaps(
        candidate,
        surya_boxes,
        max_containment=_MAX_CONTAINMENT,
        max_iou=_MAX_IOU,
    )


def _straddles_surya(candidate: BBox, surya_boxes: list[BBox]) -> bool:
    """True when the candidate spans >= 2 Surya boxes (gutter/stacked lines).

    Such a box merges text from separate detected regions; rejecting it
    outright is fail-safe — splitting at ink gaps would need a second
    analysis pass for marginal gain (revisit if T7 harness data shows
    split candidates recovering real lines).
    """
    return geometry.straddles(
        candidate,
        surya_boxes,
        straddle_min_overlap=_STRADDLE_MIN_OVERLAP,
    )
