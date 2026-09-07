"""Tests for TableFallbackProcessor (RFC 004 R5)."""

from __future__ import annotations

import pytest

from omniscribe.core.block_tree import (
    BlockNode,
    BlockType,
    DocumentTree,
    PageTree,
    TableNode,
)
from omniscribe.core.document import DocumentBlock, DocumentPage, DocumentResult
from omniscribe.core.processors import (
    ProcessorContract,
    TableFallbackProcessor,
    build_document_processors,
    run_document_processors,
)


def test_table_fallback_initialization_and_validation() -> None:
    """Validate constructor parameters and boundaries."""
    proc = TableFallbackProcessor(confidence_threshold=0.80, min_columns=2)
    assert proc.name == "table_fallback"
    assert proc.contract == ProcessorContract.MAY_DELETE
    assert proc.confidence_threshold == 0.80
    assert proc.min_columns == 2

    # Invalid thresholds
    with pytest.raises(ValueError, match="confidence_threshold must be between"):
        TableFallbackProcessor(confidence_threshold=-0.1)
    with pytest.raises(ValueError, match="confidence_threshold must be between"):
        TableFallbackProcessor(confidence_threshold=1.05)

    # Invalid min_columns
    with pytest.raises(ValueError, match="min_columns must be >= 1"):
        TableFallbackProcessor(min_columns=0)

    # Invalid row_tolerance
    with pytest.raises(ValueError, match="row_tolerance must be positive"):
        TableFallbackProcessor(row_tolerance=-0.01)


def test_table_fallback_registration_via_registry() -> None:
    """Verify table_fallback is discoverable through build_document_processors."""
    processors = build_document_processors(["table_fallback"])
    assert len(processors) == 1
    assert isinstance(processors[0], TableFallbackProcessor)
    assert processors[0].contract == ProcessorContract.MAY_DELETE


@pytest.mark.asyncio
async def test_high_confidence_table_skips_fallback() -> None:
    """A high-confidence table (>= 0.80) should NOT trigger fallback."""
    cell1 = BlockNode(
        block_type=BlockType.TABLE,
        bbox=(0.1, 0.1, 0.4, 0.2),
        text="Header A",
        page_idx=0,
        confidence=0.95,
    )
    cell2 = BlockNode(
        block_type=BlockType.TABLE,
        bbox=(0.5, 0.1, 0.8, 0.2),
        text="Header B",
        page_idx=0,
        confidence=0.92,
    )
    cell3 = BlockNode(
        block_type=BlockType.TABLE,
        bbox=(0.1, 0.3, 0.4, 0.4),
        text="Val 1",
        page_idx=0,
        confidence=0.90,
    )
    cell4 = BlockNode(
        block_type=BlockType.TABLE,
        bbox=(0.5, 0.3, 0.8, 0.4),
        text="Val 2",
        page_idx=0,
        confidence=0.88,
    )

    table = TableNode(
        rows=2,
        cols=2,
        page_idx=0,
        bbox=(0.1, 0.1, 0.8, 0.4),
        cells=[[cell1, cell2], [cell3, cell4]],
    )

    doc = DocumentResult(
        pages=[
            DocumentPage(
                page_index=0,
                width=1000,
                height=1000,
                blocks=[],
                metadata={"tables": []},
            )
        ],
        tree=DocumentTree(
            pages=[PageTree(page_idx=0, children=[table])],
            tables=[table],
        ),
    )

    proc = TableFallbackProcessor(confidence_threshold=0.80)
    result = await proc.process(doc)

    assert result.tree is not None
    assert len(result.tree.tables) == 1
    t = result.tree.tables[0]
    assert t.rows == 2
    assert t.cols == 2
    # Metadata fallback_applied must NOT be set on skipped tables
    assert not any(
        t_meta.get("fallback_applied")
        for t_meta in result.pages[0].metadata.get("tables", [])
    )
    assert not cell1.metadata.get("fallback_refined")


@pytest.mark.asyncio
async def test_low_confidence_table_executes_fallback_refinement() -> None:
    """A low-confidence table (< 0.80) triggers fallback grid reconstruction and refinement."""
    # Construct low-confidence table with combined/unsplit pipe text
    cell_raw = BlockNode(
        block_type=BlockType.TABLE,
        bbox=(0.1, 0.1, 0.8, 0.5),
        text="| Col 1 | Col 2 |\n|---|---|\n| Val A | Val B |\n| Val C | Val D |",
        page_idx=0,
        confidence=0.55,  # Below 0.80 threshold
    )

    table = TableNode(
        rows=1,
        cols=1,
        page_idx=0,
        bbox=(0.1, 0.1, 0.8, 0.5),
        cells=[[cell_raw]],
    )

    doc = DocumentResult(
        pages=[
            DocumentPage(
                page_index=0,
                width=1000,
                height=1000,
                blocks=[],
                metadata={"tables": []},
            )
        ],
        tree=DocumentTree(
            pages=[PageTree(page_idx=0, children=[table])],
            tables=[table],
        ),
    )

    proc = TableFallbackProcessor(confidence_threshold=0.80)
    result = await proc.process(doc)

    assert result.tree is not None
    assert len(result.tree.tables) == 1
    refined_table = result.tree.tables[0]
    # Should reconstruct into 3 rows (Col 1/Col 2, Val A/Val B, Val C/Val D) and 2 cols
    assert refined_table.rows == 3
    assert refined_table.cols == 2

    # Check cell texts and confidences
    grid = refined_table.cells
    assert grid[0][0].text == "Col 1"
    assert grid[0][1].text == "Col 2"
    assert grid[1][0].text == "Val A"
    assert grid[1][1].text == "Val B"
    assert grid[2][0].text == "Val C"
    assert grid[2][1].text == "Val D"

    # Refined cells must have boosted confidence and metadata marker
    for row in grid:
        for cell in row:
            assert cell.confidence is not None and cell.confidence >= 0.85
            assert cell.metadata.get("fallback_refined") is True

    # Page metadata must record fallback execution
    tables_meta = result.pages[0].metadata.get("tables", [])
    assert len(tables_meta) >= 1
    assert any(m.get("fallback_applied") is True for m in tables_meta)


@pytest.mark.asyncio
async def test_low_confidence_unstructured_block_converts_to_table_node() -> None:
    """An unparsed block with kind='table' and confidence < threshold is converted to TableNode."""
    table_block = DocumentBlock(
        bbox=(0.1, 0.2, 0.7, 0.6),
        text="| Feature | Supported |\n|---|---|\n| Hybrid OCR | Yes |\n| Grounded | Yes |",
        confidence=0.62,
        kind="table",
        metadata={"label": "table"},
    )
    other_block = DocumentBlock(
        bbox=(0.1, 0.05, 0.5, 0.1),
        text="Normal intro text",
        confidence=0.95,
        kind="paragraph",
    )

    doc = DocumentResult(
        pages=[
            DocumentPage(
                page_index=0,
                width=1000,
                height=1000,
                blocks=[other_block, table_block],
                metadata={},
            )
        ]
    )

    # Run through processor (which will initialize DocumentTree)
    proc = TableFallbackProcessor(confidence_threshold=0.80)
    result = await proc.process(doc)

    assert result.tree is not None
    assert len(result.tree.tables) == 1
    t = result.tree.tables[0]
    assert t.rows == 3
    assert t.cols == 2
    assert t.cells[0][0].text == "Feature"
    assert t.cells[0][1].text == "Supported"
    assert t.cells[1][0].text == "Hybrid OCR"
    assert t.cells[1][1].text == "Yes"

    # In tree.pages[0].children, table_block should be replaced by TableNode
    child_types = [type(c) for c in result.tree.pages[0].children]
    assert TableNode in child_types


@pytest.mark.asyncio
async def test_fail_open_on_corrupted_table() -> None:
    """A corrupted or unparseable table should fail open and leave blocks untouched."""
    corrupted_cell = BlockNode(
        block_type=BlockType.TABLE,
        bbox=(0.1, 0.1, 0.5, 0.3),
        text="Malformed Non Table Text With Single Word",
        page_idx=0,
        confidence=0.40,
    )

    table = TableNode(
        rows=1,
        cols=1,
        page_idx=0,
        bbox=(0.1, 0.1, 0.5, 0.3),
        cells=[[corrupted_cell]],
    )

    doc = DocumentResult(
        pages=[
            DocumentPage(
                page_index=0,
                width=1000,
                height=1000,
                blocks=[],
                metadata={},
            )
        ],
        tree=DocumentTree(
            pages=[PageTree(page_idx=0, children=[table])],
            tables=[table],
        ),
    )

    # Also test with a parser that deliberately raises an unhandled exception
    def failing_parser(text: str) -> list[list[str]]:
        raise RuntimeError("Simulated internal parser crash!")

    proc = TableFallbackProcessor(
        confidence_threshold=0.80, fallback_parser=failing_parser
    )

    # Must NOT raise exception; fail open contract
    result = await proc.process(doc)

    assert result.tree is not None
    assert len(result.tree.tables) == 1
    # Table must remain unchanged
    t = result.tree.tables[0]
    assert t.rows == 1
    assert t.cols == 1
    assert t.cells[0][0].text == "Malformed Non Table Text With Single Word"


@pytest.mark.asyncio
async def test_table_fallback_passes_strict_pipeline_runner() -> None:
    """Verify TableFallbackProcessor operates cleanly within run_document_processors with strict=True."""
    doc = DocumentResult.from_pages_data(
        {
            0: [
                ((0.1, 0.1, 0.5, 0.2), "Regular text"),
                ((0.1, 0.3, 0.7, 0.7), "| Col A | Col B |\n| 1 | 2 |"),
            ]
        }
    )
    # Mark second block as low confidence table
    doc.pages[0].blocks[1].kind = "table"
    doc.pages[0].blocks[1].confidence = 0.50

    result = await run_document_processors(
        doc, [TableFallbackProcessor()], strict=True
    )
    assert result.tree is not None
    assert len(result.tree.tables) == 1
