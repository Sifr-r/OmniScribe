from __future__ import annotations

import base64

import pytest

from omniscribe.core import document as document_module
from omniscribe.core.block_tree import BlockNode, _as_bbox
from omniscribe.core.document import BBox, DocumentResult
from omniscribe.core.processors import (
    DocumentProcessorRegistry,
    QualityAnalysisProcessor,
    ReadingOrderProcessor,
    SectionAnalysisProcessor,
    StructureAnalysisProcessor,
    run_document_processors,
)
from omniscribe.pipeline import OCRPipeline


def test_document_result_from_pages_data_preserves_order_and_text():
    result = DocumentResult.from_pages_data(
        {0: [((0.1, 0.2, 0.3, 0.4), "alpha"), ((0.1, 0.5, 0.3, 0.6), "beta")]},
        source_path="scan.pdf",
        source_processor="hybrid",
    )

    page = result.pages[0]
    assert result.source_path == "scan.pdf"
    assert result.text() == "alpha\nbeta"
    assert [block.reading_order for block in page.blocks] == [0, 1]
    assert [block.source_processor for block in page.blocks] == ["hybrid", "hybrid"]


def test_document_result_rejects_non_normalized_bbox():
    with pytest.raises(ValueError, match="normalized bbox"):
        DocumentResult.from_pages_data({0: [((0.0, 0.0, 2.0, 1.0), "bad")]})


def test_from_pages_data_estimates_confidence() -> None:
    result = DocumentResult.from_pages_data(
        {0: [((0.0, 0.0, 1.0, 0.1), "Several well formed words here")]},
        confidence_fn=lambda t: 0.99 if len(t.split()) >= 3 else 0.4,
    )
    assert result.pages[0].blocks[0].confidence == 0.99


def test_from_pages_data_confidence_fn_skips_blank_text() -> None:
    result = DocumentResult.from_pages_data(
        {0: [((0.0, 0.0, 1.0, 0.1), "   ")]},
        confidence_fn=lambda t: 0.99,
    )
    assert result.pages[0].blocks[0].confidence is None


def test_from_pages_data_without_confidence_fn_keeps_none() -> None:
    result = DocumentResult.from_pages_data({0: [((0.0, 0.0, 1.0, 0.1), "text")]})
    assert result.pages[0].blocks[0].confidence is None


def test_bbox_alias_is_fixed_length_tuple():
    """Regression for §4.6: ``BBox`` must be ``tuple[float, float, float, float]``.

    Mutable ``list[float]`` allowed accidental mutation and accepted
    wrong-length values; the fixed tuple type enforces both invariants at
    type-check time and at runtime via :func:`_normalize_bbox`.
    """
    # ``BBox = tuple[float, float, float, float]`` is a plain generic-alias
    # assignment (PEP 585). Compare on origin + args rather than identity.
    assert BBox.__origin__ is tuple  # type: ignore[attr-defined]
    assert BBox.__args__ == (float, float, float, float)  # type: ignore[attr-defined]
    assert document_module._normalize_bbox([0.1, 0.2, 0.3, 0.4]) == (0.1, 0.2, 0.3, 0.4)
    assert isinstance(document_module._normalize_bbox((0.0, 0.0, 1.0, 1.0)), tuple)
    with pytest.raises(ValueError, match="4 values"):
        document_module._normalize_bbox([0.0, 0.0, 1.0])
    with pytest.raises(ValueError, match="normalized bbox"):
        document_module._normalize_bbox([0.0, 0.0, 2.0, 1.0])
    # BlockNode's bbox is the same fixed-length tuple shape.
    from omniscribe.core.block_tree import BlockType

    node = BlockNode(
        block_type=BlockType.PARAGRAPH,
        bbox=(0.1, 0.2, 0.3, 0.4),
        text="hello",
        page_idx=0,
    )
    assert isinstance(node.bbox, tuple)
    assert node.bbox == (0.1, 0.2, 0.3, 0.4)
    # Mutating in place must fail — that's the whole point of the refactor.
    with pytest.raises(TypeError):
        node.bbox[0] = 0.99  # type: ignore[index]


def test_block_tree_as_bbox_enforces_length_and_coerces_floats():
    """The new :func:`_as_bbox` helper produces fixed-length tuples from sequences."""
    assert _as_bbox([0.1, 0.2, 0.3, 0.4]) == (0.1, 0.2, 0.3, 0.4)
    assert _as_bbox((0, 0, 1, 1)) == (0.0, 0.0, 1.0, 1.0)
    with pytest.raises(ValueError, match="4 values"):
        _as_bbox([0.0, 0.0, 1.0])
    with pytest.raises(ValueError, match="4 values"):
        _as_bbox([0.0] * 5)


async def test_pipeline_records_document_result_without_changing_return_value():
    pipe = OCRPipeline(_Aligner(), _OCR(), _PDF())

    pages_text = await pipe.run("in.pdf", "out.pdf", refine=False)

    assert pages_text == {0: ["hello"]}
    assert pipe.last_document_result is not None
    block = pipe.last_document_result.pages[0].blocks[0]
    assert block.text == "hello"
    assert block.bbox == (0.1, 0.2, 0.3, 0.4)


async def test_registry_runs_processors_in_order():
    registry = DocumentProcessorRegistry()
    registry.register("suffix", lambda: _SuffixProcessor("!"))  # type: ignore[arg-type, return-value]
    registry.register("again", lambda: _SuffixProcessor("?"))  # type: ignore[arg-type, return-value]

    result = await run_document_processors(
        DocumentResult.from_pages_data({0: [((0.1, 0.2, 0.3, 0.4), "hello")]}),
        registry.create_many(["suffix", "again"]),
    )

    assert registry.names == ["again", "suffix"]
    assert result.pages[0].blocks[0].text == "hello!?"


async def test_pipeline_processors_feed_existing_output_writer():
    pdf = _PDF()
    pipe = OCRPipeline(
        _Aligner(),
        _OCR(),
        pdf,
        document_processors=[_SuffixProcessor("!")],  # type: ignore[list-item]
    )

    pages_text = await pipe.run("in.pdf", "out.pdf", refine=False)

    assert pages_text == {0: ["hello!"]}
    assert pdf.pages_data[0][0][1] == "hello!"


async def test_reading_order_processor_normalizes_blocks():
    result = DocumentResult.from_pages_data(
        {
            0: [
                ((0.1, 0.5, 0.3, 0.6), "second"),
                ((0.1, 0.1, 0.3, 0.2), "first"),
                ((0.5, 0.1, 0.7, 0.2), "right"),
            ]
        }
    )

    ordered = await ReadingOrderProcessor().process(result)

    assert [block.text for block in ordered.pages[0].blocks] == [
        "first",
        "right",
        "second",
    ]
    assert [block.reading_order for block in ordered.pages[0].blocks] == [0, 1, 2]


async def test_quality_analysis_processor_records_page_findings():
    boxes = [((0.0, 0.0, 0.4, 0.4), "")] + [
        ((0.0, 0.0, 0.01, 0.01), "") for _ in range(20)
    ]
    result = DocumentResult.from_pages_data({0: boxes})
    quality = (
        (await QualityAnalysisProcessor().process(result)).pages[0].metadata["quality"]
    )

    assert quality["block_count"] == 21  # type: ignore[index]
    assert quality["text_char_count"] == 0  # type: ignore[index]
    assert {finding["code"] for finding in quality["findings"]} == {  # type: ignore[index]
        "empty_large_block",
        "empty_page",
        "sparse_text",
    }


async def test_structure_analysis_processor_classifies_blocks_without_rewriting_text():
    result = DocumentResult.from_pages_data(
        {
            0: [
                ((0.1, 0.1, 0.5, 0.15), "INVOICE SUMMARY"),
                ((0.1, 0.2, 0.5, 0.25), "Invoice No: 12345"),
                ((0.1, 0.3, 0.8, 0.35), "Item  Qty  Price"),
                ((0.1, 0.4, 0.8, 0.45), "- Paid by bank transfer"),
                ((0.1, 0.5, 0.8, 0.55), "This invoice is payable within 30 days."),
            ]
        }
    )

    analyzed = await StructureAnalysisProcessor().process(result)

    page = analyzed.pages[0]
    assert [block.text for block in page.blocks] == [
        "INVOICE SUMMARY",
        "Invoice No: 12345",
        "Item  Qty  Price",
        "- Paid by bank transfer",
        "This invoice is payable within 30 days.",
    ]
    assert [block.kind for block in page.blocks] == [
        "heading",
        "key_value",
        "table_candidate",
        "list_item",
        "paragraph",
    ]
    assert page.metadata["structure"] == {
        "block_kinds": {
            "heading": 1,
            "key_value": 1,
            "list_item": 1,
            "paragraph": 1,
            "table_candidate": 1,
        },
        "has_key_values": True,
        "has_tables": True,
    }
    assert page.blocks[0].metadata["structure"]["signals"] == ["short_prominent_text"]  # type: ignore[index]


async def test_section_analysis_processor_groups_blocks_under_headings():
    result = DocumentResult.from_pages_data(
        {
            0: [
                ((0.1, 0.05, 0.6, 0.1), "Prepared for internal review."),
                ((0.1, 0.15, 0.5, 0.2), "Overview"),
                ((0.1, 0.22, 0.8, 0.3), "This section describes the document."),
                ((0.1, 0.36, 0.5, 0.41), "Financial Details"),
            ],
            1: [
                ((0.1, 0.1, 0.8, 0.16), "Revenue increased this quarter."),
            ],
        }
    )

    analyzed = await SectionAnalysisProcessor().process(result)

    first_page = analyzed.pages[0]
    second_page = analyzed.pages[1]
    assert [block.text for block in first_page.blocks] == [
        "Prepared for internal review.",
        "Overview",
        "This section describes the document.",
        "Financial Details",
    ]
    assert first_page.blocks[0].metadata["section"]["role"] == "unsectioned"  # type: ignore[index]
    assert first_page.blocks[1].metadata["section"] == {
        "section_index": 0,
        "title": "Overview",
        "heading_page_index": 0,
        "heading_block_index": 1,
        "role": "heading",
    }
    assert first_page.blocks[2].metadata["section"]["title"] == "Overview"  # type: ignore[index]
    assert first_page.blocks[2].metadata["section"]["role"] == "body"  # type: ignore[index]
    assert first_page.blocks[3].metadata["section"]["title"] == "Financial Details"  # type: ignore[index]
    assert second_page.blocks[0].metadata["section"]["title"] == "Financial Details"  # type: ignore[index]
    assert second_page.blocks[0].metadata["section"]["heading_page_index"] == 0  # type: ignore[index]
    assert first_page.metadata["sections"]["section_count"] == 2  # type: ignore[index]
    assert second_page.metadata["sections"] == {
        "headings": [],
        "section_count": 0,
        "active_section": "Financial Details",
    }


async def test_pipeline_reading_order_processor_feeds_output_writer():
    pdf = _PDF()
    pipe = OCRPipeline(
        _ReverseAligner(), _OCR(), pdf, document_processors=[ReadingOrderProcessor()]
    )

    pages_text = await pipe.run("in.pdf", "out.pdf", refine=False)

    assert pages_text == {0: ["top", "bottom"]}
    assert [text for _, text in pdf.pages_data[0]] == ["top", "bottom"]


class _Aligner:
    def get_detected_boxes_batch(self, images):
        return [[[0.1, 0.2, 0.3, 0.4]] for _ in images]

    def align_text(self, structured, lines):
        return [(bbox, "\n".join(lines)) for bbox, _ in structured]


class _ReverseAligner:
    def get_detected_boxes_batch(self, images):
        return [
            [
                [0.1, 0.5, 0.3, 0.6],
                [0.1, 0.1, 0.3, 0.2],
            ]
            for _ in images
        ]

    def align_text(self, structured, lines):
        texts = ["bottom", "top"]
        return [(bbox, texts[index]) for index, (bbox, _) in enumerate(structured)]


class _OCR:
    async def perform_ocr(self, image_base64, **kwargs):
        return ["hello"]


class _PDF:
    def convert_to_images(self, input_path, dpi=200, max_image_dim=1024):
        return {0: base64.b64encode(b"image").decode()}

    def embed_structured_text(self, input_path, output_path, pages_data, dpi=200):
        self.pages_data = pages_data


class _SuffixProcessor:
    name = "suffix"

    def __init__(self, suffix: str) -> None:
        self.suffix = suffix

    async def process(self, document: DocumentResult) -> DocumentResult:
        for page in document.pages:
            for block in page.blocks:
                block.text += self.suffix
        return document


def test_normalize_bbox_handles_degenerate_boxes_gracefully():
    # x1 <= x0 should expand x1 slightly without error
    norm = document_module._normalize_bbox([0.2, 0.3, 0.2, 0.5])
    assert norm[0] == 0.2
    assert norm[2] > norm[0]
    assert norm[1] == 0.3
    assert norm[3] == 0.5

    # y1 <= y0 should expand y1 slightly without error
    norm_y = document_module._normalize_bbox([0.1, 0.4, 0.3, 0.4])
    assert norm_y[3] > norm_y[1]
