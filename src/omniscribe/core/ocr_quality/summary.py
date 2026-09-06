"""Document-level trust summary for the ``X-Document-Trust`` response header.

Pure function over a :class:`~omniscribe.core.document.DocumentResult`; the
OCR service serializes the dict as JSON for the sync response and the
Flutter client's ``TrustSummary.fromJson`` consumes it.
"""

from __future__ import annotations

from typing import Any

from omniscribe.core.document import DocumentResult

_HISTOGRAM_BUCKETS: tuple[tuple[str, float], ...] = (
    ("low", 0.5),
    ("medium", 0.8),
)


def document_trust_summary(document_result: DocumentResult) -> dict[str, Any] | None:
    """Summarize block trust scores; ``None`` when nothing was scored.

    A block counts as flagged when it carries any trust flag. Blocks with
    ``trust_score=None`` (trust layer disabled) leave the whole summary
    ``None`` so the header is omitted rather than zero-filled.
    """
    scored: list[float] = []
    flagged = 0
    flag_counts: dict[str, int] = {}
    histogram: dict[str, int] = {"low": 0, "medium": 0, "high": 0}
    block_count = 0

    for page in document_result.pages:
        for block in page.blocks:
            block_count += 1
            if block.trust_score is None:
                continue
            scored.append(block.trust_score)
            bucket = "high"
            for name, upper in _HISTOGRAM_BUCKETS:
                if block.trust_score < upper:
                    bucket = name
                    break
            histogram[bucket] += 1
            if block.trust_flags:
                flagged += 1
                for flag in block.trust_flags:
                    flag_counts[flag] = flag_counts.get(flag, 0) + 1

    if not scored:
        return None

    return {
        "block_count": block_count,
        "scored_count": len(scored),
        "flagged_count": flagged,
        "average": round(sum(scored) / len(scored), 4),
        "histogram": histogram,
        "flag_counts": flag_counts,
    }


__all__ = ["document_trust_summary"]
