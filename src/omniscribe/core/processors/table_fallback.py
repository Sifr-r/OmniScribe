"""Table-structure fallback processor for low-confidence tables (RFC 004 R5).

Inspects table blocks and TableNodes across pages; if a table's confidence
falls below `confidence_threshold` (default 0.80), runs dedicated grid
reconstruction and cell alignment heuristics (or a fallback table parser).

Fails open: if fallback reconstruction or parsing fails, the original table
blocks and nodes are left completely intact.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

from omniscribe.core.block_tree import (
    BlockNode,
    BlockType,
    DocumentTree,
    TableNode,
)
from omniscribe.core.document import DocumentPage, DocumentResult
from omniscribe.core.processors.base import (
    ProcessorContract,
)

_LOG = logging.getLogger("omniscribe.core.processors.table_fallback")

# Match markdown table separator lines like |---|:---:|---:|
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?(\s*:?-+:?\s*\|)+\s*:?-+:?\s*\|?\s*$")


def _split_table_line(line: str) -> list[str]:
    """Split a table row line by pipe, tab, or multi-space delimiters."""
    line = line.strip()
    if not line:
        return []
    # Pipe delimiter format: | col1 | col2 |
    if "|" in line:
        raw_cells = line.split("|")
        # Strip leading/trailing empty cells resulting from outer pipes
        if raw_cells and not raw_cells[0].strip():
            raw_cells = raw_cells[1:]
        if raw_cells and not raw_cells[-1].strip():
            raw_cells = raw_cells[:-1]
        return [c.strip() for c in raw_cells]

    # Tab-separated
    if "\t" in line:
        return [c.strip() for c in line.split("\t") if c.strip()]

    # Multi-space separated (>= 2 spaces)
    parts = re.split(r"\s{2,}", line)
    return [p.strip() for p in parts if p.strip()]


def default_table_parser(text: str) -> list[list[str]]:
    """Parse raw table text (Markdown pipe table, TSV, or multi-space) into a 2D grid.

    Returns empty list if the text does not form a valid grid with at least 2 rows.
    """
    if not text or not isinstance(text, str):
        return []

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return []

    raw_grid: list[list[str]] = []
    for line in lines:
        if _TABLE_SEPARATOR_RE.match(line):
            continue
        cells = _split_table_line(line)
        if cells:
            raw_grid.append(cells)

    if len(raw_grid) < 2:
        return []

    max_cols = max(len(row) for row in raw_grid)
    if max_cols < 2:
        return []

    # Pad rows to ensure rectangular matrix
    normalized_grid: list[list[str]] = []
    for row in raw_grid:
        padded = list(row)
        while len(padded) < max_cols:
            padded.append("")
        normalized_grid.append(padded)

    return normalized_grid


class TableFallbackProcessor:
    """Fallback processor for repairing and reconstructing low-confidence tables.

    Implements :class:`~omniscribe.core.processors.base.DocumentProcessor`.
    Operates with ``ProcessorContract.MAY_DELETE`` because it may replace
    unstructured or poorly structured table blocks with rectangular
    ``TableNode`` grids.
    """

    name = "table_fallback"
    contract = ProcessorContract.MAY_DELETE

    def __init__(
        self,
        confidence_threshold: float = 0.80,
        min_columns: int = 2,
        row_tolerance: float = 0.02,
        fallback_parser: Callable[[str], list[list[str]]] | None = None,
    ) -> None:
        """Initialize TableFallbackProcessor.

        Args:
            confidence_threshold: Tables with confidence below this value trigger fallback.
            min_columns: Minimum column count required to accept a table grid.
            row_tolerance: Bounding-box vertical tolerance for cell row clustering.
            fallback_parser: Optional custom parser from text to 2D string grid.
        """
        if not (0.0 <= confidence_threshold <= 1.0):
            raise ValueError(
                f"confidence_threshold must be between 0.0 and 1.0, got {confidence_threshold}"
            )
        if min_columns < 1:
            raise ValueError(f"min_columns must be >= 1, got {min_columns}")
        if row_tolerance <= 0.0:
            raise ValueError(f"row_tolerance must be positive, got {row_tolerance}")

        self.confidence_threshold = confidence_threshold
        self.min_columns = min_columns
        self.row_tolerance = row_tolerance
        self.fallback_parser = fallback_parser or default_table_parser

    async def process(self, document: DocumentResult) -> DocumentResult:
        """Process document and apply table fallback where confidence < threshold."""
        if document.tree is None:
            from omniscribe.core.block_tree import from_document_result

            document.tree = from_document_result(document)

        tree = document.tree

        for page_idx, page in enumerate(document.pages):
            tree_page = (
                tree.pages[page_idx]
                if (tree and page_idx < len(tree.pages))
                else None
            )

            # 1. Refine existing low-confidence TableNodes
            if tree:
                page_tables = [t for t in tree.tables if t.page_idx == page_idx]
                for table_node in page_tables:
                    try:
                        self._process_table_node(table_node, page)
                    except Exception as exc:
                        _LOG.warning(
                            "TableFallbackProcessor: failed refining TableNode %s on page %d: %s; keeping original",
                            table_node.block_id,
                            page_idx,
                            exc,
                        )

            # 2. Inspect candidate table blocks not yet converted to TableNodes
            if tree_page:
                try:
                    self._process_unstructured_table_blocks(page, tree_page, tree)
                except Exception as exc:
                    _LOG.warning(
                        "TableFallbackProcessor: failed processing table blocks on page %d: %s; keeping original",
                        page_idx,
                        exc,
                    )

        return document

    def _compute_table_confidence(self, table_node: TableNode) -> float:
        """Compute the aggregate confidence of a TableNode."""
        cell_confs = [
            cell.confidence
            for row in table_node.cells
            for cell in row
            if cell.confidence is not None
        ]
        if not cell_confs:
            return 1.0
        return sum(cell_confs) / len(cell_confs)

    def _process_table_node(
        self, table_node: TableNode, page: DocumentPage
    ) -> None:
        """Inspect and refine a TableNode if its confidence is below threshold."""
        avg_conf = self._compute_table_confidence(table_node)
        if avg_conf >= self.confidence_threshold:
            return

        # Low-confidence TableNode detected: attempt grid reconstruction & cell alignment
        _LOG.info(
            "Low-confidence table detected (conf=%.2f < %.2f) on page %d; running fallback",
            avg_conf,
            self.confidence_threshold,
            table_node.page_idx,
        )

        # Concatenate cell texts to inspect if cells were bunched or misaligned
        cell_texts = [
            cell.text
            for row in table_node.cells
            for cell in row
            if cell.text.strip()
        ]
        joined_text = "\n".join(cell_texts)

        # Try parsing structured grid from cell contents
        parsed_grid = self.fallback_parser(joined_text)
        if (
            not parsed_grid
            or len(parsed_grid) < 2
            or max(len(r) for r in parsed_grid) < self.min_columns
        ):
            # Fallback parsing did not produce a better grid: perform geometric alignment
            self._align_existing_cells(table_node)
            return

        # Reconstruct grid using the parsed structure and table bbox
        new_rows = len(parsed_grid)
        new_cols = max(len(r) for r in parsed_grid)
        x0, y0, x1, y1 = table_node.bbox
        col_w = max(0.001, (x1 - x0) / new_cols)
        row_h = max(0.001, (y1 - y0) / new_rows)

        refined_cells: list[list[BlockNode]] = []
        for r_idx, row in enumerate(parsed_grid):
            row_nodes: list[BlockNode] = []
            for c_idx, cell_text in enumerate(row):
                c_bbox = (
                    x0 + c_idx * col_w,
                    y0 + r_idx * row_h,
                    x0 + (c_idx + 1) * col_w,
                    y0 + (r_idx + 1) * row_h,
                )
                node = BlockNode(
                    block_type=BlockType.TABLE,
                    bbox=c_bbox,
                    text=cell_text,
                    page_idx=table_node.page_idx,
                    confidence=0.85,
                    metadata={"fallback_refined": True, "row": r_idx, "col": c_idx},
                )
                row_nodes.append(node)
            refined_cells.append(row_nodes)

        # Update table node in place
        table_node.rows = new_rows
        table_node.cols = new_cols
        table_node.cells = refined_cells

        # Mark in page metadata
        page_tables = page.metadata.setdefault("tables", [])
        if isinstance(page_tables, list):
            page_tables.append(
                {
                    "table_index": len(page_tables),
                    "row_count": new_rows,
                    "column_count": new_cols,
                    "fallback_applied": True,
                }
            )

    def _align_existing_cells(self, table_node: TableNode) -> None:
        """Heuristic alignment: ensure rectangular grid and normalized coordinates."""
        all_cells = [c for row in table_node.cells for c in row if c.text.strip()]
        if not all_cells:
            return

        # Cluster cells by Y center
        rows: list[list[BlockNode]] = []
        row_centers: list[float] = []
        sorted_by_y = sorted(
            all_cells, key=lambda c: (c.bbox[1] + c.bbox[3]) / 2.0
        )

        for cell in sorted_by_y:
            cy = (cell.bbox[1] + cell.bbox[3]) / 2.0
            for i, r_center in enumerate(row_centers):
                if abs(cy - r_center) <= self.row_tolerance:
                    rows[i].append(cell)
                    row_centers[i] = sum(
                        (c.bbox[1] + c.bbox[3]) / 2.0 for c in rows[i]
                    ) / len(rows[i])
                    break
            else:
                rows.append([cell])
                row_centers.append(cy)

        # Sort each row horizontally by X0
        rows = [sorted(r, key=lambda c: c.bbox[0]) for r in rows]
        max_cols = max(len(r) for r in rows) if rows else 0
        if len(rows) < 2 or max_cols < self.min_columns:
            return

        # Pad rows to form a rectangular grid
        x0, y0, x1, y1 = table_node.bbox
        grid: list[list[BlockNode]] = []
        for _r_idx, row in enumerate(rows):
            padded_row = list(row)
            while len(padded_row) < max_cols:
                padded_row.append(
                    BlockNode(
                        block_type=BlockType.TABLE,
                        bbox=(x0, y0, x1, y1),
                        text="",
                        page_idx=table_node.page_idx,
                        confidence=0.85,
                        metadata={"fallback_refined": True, "empty_pad": True},
                    )
                )
            for c in padded_row:
                c.confidence = max(c.confidence or 0.0, 0.85)
                c.metadata["fallback_refined"] = True
            grid.append(padded_row)

        table_node.rows = len(grid)
        table_node.cols = max_cols
        table_node.cells = grid

    def _process_unstructured_table_blocks(
        self,
        page: DocumentPage,
        tree_page: Any,
        tree: DocumentTree,
    ) -> None:
        """Check for unparsed low-confidence table blocks and convert them to TableNodes."""
        new_children: list[Any] = []
        new_tables: list[TableNode] = []

        for child in tree_page.children:
            if isinstance(child, TableNode):
                new_children.append(child)
                continue

            if not isinstance(child, BlockNode):
                new_children.append(child)
                continue

            is_table_block = (
                child.block_type == BlockType.TABLE
                or child.metadata.get("label") == "table"
                or child.metadata.get("kind") == "table"
            )

            # Check if confidence < threshold
            conf = child.confidence if child.confidence is not None else 1.0
            if not is_table_block or conf >= self.confidence_threshold:
                new_children.append(child)
                continue

            # Attempt fallback parsing on the low-confidence table block
            try:
                grid_data = self.fallback_parser(child.text)
            except Exception as exc:
                _LOG.warning(
                    "TableFallbackProcessor: custom parser failed on block %s: %s; keeping original block",
                    child.block_id,
                    exc,
                )
                new_children.append(child)
                continue

            if (
                not grid_data
                or len(grid_data) < 2
                or max(len(r) for r in grid_data) < self.min_columns
            ):
                # Fail open: not parseable into minimum table dimensions, keep block intact
                new_children.append(child)
                continue

            # Construct TableNode and replacement BlockNodes
            num_rows = len(grid_data)
            num_cols = max(len(r) for r in grid_data)
            bx0, by0, bx1, by1 = child.bbox
            col_w = max(0.001, (bx1 - bx0) / num_cols)
            row_h = max(0.001, (by1 - by0) / num_rows)

            grid: list[list[BlockNode]] = []
            for r_idx, row in enumerate(grid_data):
                row_nodes: list[BlockNode] = []
                for c_idx, cell_text in enumerate(row):
                    c_bbox = (
                        bx0 + c_idx * col_w,
                        by0 + r_idx * row_h,
                        bx0 + (c_idx + 1) * col_w,
                        by0 + (r_idx + 1) * row_h,
                    )
                    cell_node = BlockNode(
                        block_type=BlockType.TABLE,
                        bbox=c_bbox,
                        text=cell_text,
                        page_idx=page.page_index,
                        confidence=0.85,
                        metadata={
                            "fallback_refined": True,
                            "row": r_idx,
                            "col": c_idx,
                            "source_block_id": child.block_id,
                        },
                    )
                    row_nodes.append(cell_node)
                grid.append(row_nodes)

            table_node = TableNode(
                rows=num_rows,
                cols=num_cols,
                page_idx=page.page_index,
                bbox=child.bbox,
                cells=grid,
            )
            new_tables.append(table_node)
            new_children.append(table_node)

            # Record in page metadata
            page_tables = page.metadata.setdefault("tables", [])
            if isinstance(page_tables, list):
                page_tables.append(
                    {
                        "table_index": len(page_tables),
                        "row_count": num_rows,
                        "column_count": num_cols,
                        "fallback_applied": True,
                    }
                )

        if new_tables:
            tree_page.children = new_children
            tree.tables.extend(new_tables)
