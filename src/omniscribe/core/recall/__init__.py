"""Secondary text-recall sources merged into hybrid detection."""

from __future__ import annotations

from typing import Final

from omniscribe.core.recall.base import BaseRecallOptions

# Standard recall constants shared across recall sources (audit 3.9, 6.5, 6.25).
STRADDLE_MIN_OVERLAP: Final[float] = 0.15
MAX_RECALL_BOXES_PER_PAGE: Final[int] = 10

__all__ = [
    "MAX_RECALL_BOXES_PER_PAGE",
    "STRADDLE_MIN_OVERLAP",
    "BaseRecallOptions",
]
