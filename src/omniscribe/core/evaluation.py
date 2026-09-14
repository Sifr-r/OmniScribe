from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from omniscribe.core.document import DocumentResult


@dataclass(frozen=True, slots=True)
class EvaluationMetrics:
    text_similarity: float
    block_count: int
    invalid_bbox_count: int
    reading_order_coverage: float
    table_count: int


def evaluate_document(
    document: DocumentResult,
    *,
    expected_text: str = "",
) -> EvaluationMetrics:
    actual_text = document.text()
    text_similarity = (
        SequenceMatcher(None, expected_text, actual_text).ratio()
        if expected_text
        else 0.0
    )
    blocks = [block for page in document.pages for block in page.blocks]
    ordered = sum(1 for block in blocks if block.reading_order is not None)
    tables = sum(
        len(tables)
        for page in document.pages
        if isinstance(tables := page.metadata.get("tables"), list)
    )
    return EvaluationMetrics(
        text_similarity=text_similarity,
        block_count=len(blocks),
        invalid_bbox_count=sum(1 for block in blocks if not _valid_bbox(block.bbox)),
        reading_order_coverage=ordered / len(blocks) if blocks else 0.0,
        table_count=tables,
    )


def _valid_bbox(bbox: tuple[float, float, float, float]) -> bool:
    """Return True iff bbox is normalized to ``[0..1]`` with non-negative area.

    M10 audit fix: matches the IoU semantics in :func:`confidence_eval.iou`
    so degenerate single-point boxes (area = 0, valid by IoU) are
    accepted. The previous ``< x1`` rejected these.
    """
    x0, y0, x1, y1 = bbox
    return 0.0 <= x0 <= x1 <= 1.0 and 0.0 <= y0 <= y1 <= 1.0
