"""Tests for :mod:`omniscribe.core.ocr_quality.summary`."""

from __future__ import annotations

from omniscribe.core.document import DocumentBlock, DocumentPage, DocumentResult
from omniscribe.core.ocr_quality.summary import document_trust_summary


def _result(blocks: list[DocumentBlock]) -> DocumentResult:
    return DocumentResult(pages=[DocumentPage(page_index=0, blocks=blocks)])


def test_summary_none_when_no_scored_blocks():
    result = _result(
        [
            DocumentBlock(bbox=(0.0, 0.0, 0.5, 0.1), text="a"),
            DocumentBlock(bbox=(0.0, 0.2, 0.5, 0.3), text="b"),
        ]
    )
    assert document_trust_summary(result) is None


def test_summary_counts_flags_and_average():
    result = _result(
        [
            DocumentBlock(
                bbox=(0.0, 0.0, 0.5, 0.1),
                text="flagged",
                trust_score=0.3,
                trust_flags=("LOW_CALIBRATED_CONF",),
            ),
            DocumentBlock(bbox=(0.0, 0.2, 0.5, 0.3), text="clean", trust_score=0.9),
            DocumentBlock(bbox=(0.0, 0.4, 0.5, 0.5), text="unscored"),
        ]
    )
    summary = document_trust_summary(result)
    assert summary is not None
    assert summary["block_count"] == 3
    assert summary["scored_count"] == 2
    assert summary["flagged_count"] == 1
    assert summary["average"] == (0.3 + 0.9) / 2
    assert summary["flag_counts"] == {"LOW_CALIBRATED_CONF": 1}
    assert set(summary["histogram"]) <= {"low", "medium", "high"}


def test_summary_multiple_flag_instances_accumulate():
    result = _result(
        [
            DocumentBlock(
                bbox=(0.0, 0.0, 0.5, 0.1),
                text="a",
                trust_score=0.2,
                trust_flags=("WATERMARK_HIT",),
            ),
            DocumentBlock(
                bbox=(0.0, 0.2, 0.5, 0.3),
                text="b",
                trust_score=0.4,
                trust_flags=("WATERMARK_HIT", "LOW_CALIBRATED_CONF"),
            ),
        ]
    )
    summary = document_trust_summary(result)
    assert summary is not None
    assert summary["flagged_count"] == 2
    assert summary["flag_counts"] == {"WATERMARK_HIT": 2, "LOW_CALIBRATED_CONF": 1}
