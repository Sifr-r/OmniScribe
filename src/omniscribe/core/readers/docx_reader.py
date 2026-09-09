"""DOCX document reader for digital ingest fast path.

Parses Microsoft Word (.docx) documents into canonical :class:`DocumentResult`
and :class:`DocumentTree` structures with full fidelity (headings, paragraphs,
tables, lists, and runs).
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from docx import Document
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph

from omniscribe.core.block_tree import (
    BlockNode,
    BlockType,
    DocumentTree,
    PageTree,
    Section,
    Span,
    TableNode,
)
from omniscribe.core.document import (
    BBox,
    DocumentPage,
    DocumentResult,
)
from omniscribe.core.readers.base import (
    BaseDocumentReader,
    MalformedDocumentError,
    create_synthetic_page,
    layout_synthetic_blocks,
    resolve_source_bytes,
)

__all__ = ["DocxReader"]


def _format_markdown_table(rows_data: list[list[str]]) -> str:
    """Format a 2D grid of strings into a markdown table representation."""
    if not rows_data or not rows_data[0]:
        return ""
    col_count = max(len(row) for row in rows_data)
    padded = [row + [""] * (col_count - len(row)) for row in rows_data]
    lines: list[str] = []
    # Header row
    header_row = padded[0]
    lines.append(
        "| " + " | ".join(cell.replace("\n", " ") for cell in header_row) + " |"
    )
    lines.append("| " + " | ".join(["---"] * col_count) + " |")
    # Body rows
    for row in padded[1:]:
        lines.append("| " + " | ".join(cell.replace("\n", " ") for cell in row) + " |")
    return "\n".join(lines)


def _has_page_break(p_elem: CT_P) -> bool:
    """Check if paragraph element contains an explicit page break."""
    for node in p_elem.iter():
        if (
            node.tag.endswith("br")
            and node.attrib.get(
                "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}type"
            )
            == "page"
        ):
            return True
        if node.tag.endswith("pageBreakBefore"):
            val = node.attrib.get(
                "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val"
            )
            if val is None or val in ("1", "true", "on"):
                return True
    return False


def _parse_heading_level(style_name: str) -> int | None:
    """Return heading level (1-6) if style represents a heading/title, else None."""
    name = style_name.strip().lower()
    if name == "title":
        return 1
    if name == "subtitle":
        return 2
    if name.startswith("heading"):
        parts = name.split()
        if len(parts) >= 2 and parts[1].isdigit():
            lvl = int(parts[1])
            return max(1, min(6, lvl))
    return None


def _is_list_paragraph(p: Paragraph) -> bool:
    """Detect if paragraph is formatted as a list item."""
    style = p.style
    name = (style.name or "").lower() if style is not None else ""
    if "list" in name or "bullet" in name:
        return True
    p_pr = p._element.pPr
    return (
        p_pr is not None
        and p_pr.find(
            "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}numPr"
        )
        is not None
    )


class DocxReader(BaseDocumentReader):
    """Parses .docx files into canonical DocumentResult and DocumentTree."""

    def read(
        self,
        source: Path | bytes | io.BytesIO,
        filename: str = "",
    ) -> DocumentResult:
        raw_bytes = resolve_source_bytes(source)
        source_name = filename or (
            str(source) if isinstance(source, (str, Path)) else "document.docx"
        )

        try:
            doc = Document(io.BytesIO(raw_bytes))
        except Exception as exc:
            raise MalformedDocumentError(
                f"Failed to parse DOCX document: {exc}",
                details={"filename": source_name, "error": str(exc)},
            ) from exc

        # Collect blocks grouped into synthetic pages (delimited by H1 or page breaks)
        pages_raw: list[list[dict[str, Any]]] = [[]]
        current_page = pages_raw[0]

        for child in doc.element.body:
            if isinstance(child, CT_P):
                p = Paragraph(child, doc)
                text = p.text.strip()
                if not text:
                    if _has_page_break(child) and current_page:
                        current_page = []
                        pages_raw.append(current_page)
                    continue

                if _has_page_break(child) and current_page:
                    current_page = []
                    pages_raw.append(current_page)

                style_name = p.style.name if p.style else "Normal"
                heading_level = _parse_heading_level(style_name)

                # Heading 1 triggers a new logical page if current page already has content
                if heading_level == 1 and current_page:
                    current_page = []
                    pages_raw.append(current_page)

                spans = [
                    Span(
                        text=run.text,
                        bold=bool(run.bold),
                        italic=bool(run.italic),
                        code=bool(run.font.name and "courier" in run.font.name.lower()),
                    )
                    for run in p.runs
                    if run.text
                ]

                if heading_level is not None:
                    current_page.append(
                        {
                            "type": "heading",
                            "kind": "section_header",
                            "level": heading_level,
                            "text": text,
                            "spans": spans,
                        }
                    )
                elif _is_list_paragraph(p):
                    current_page.append(
                        {
                            "type": "list_item",
                            "kind": "list_item",
                            "level": 0,
                            "text": text,
                            "spans": spans,
                        }
                    )
                else:
                    current_page.append(
                        {
                            "type": "paragraph",
                            "kind": "paragraph",
                            "level": 0,
                            "text": text,
                            "spans": spans,
                        }
                    )

            elif isinstance(child, CT_Tbl):
                tbl = Table(child, doc)
                rows_data: list[list[str]] = []
                for row in tbl.rows:
                    row_cells = [cell.text.strip() for cell in row.cells]
                    rows_data.append(row_cells)

                if rows_data and any(any(c for c in r) for r in rows_data):
                    table_md = _format_markdown_table(rows_data)
                    current_page.append(
                        {
                            "type": "table",
                            "kind": "table",
                            "level": 0,
                            "text": table_md,
                            "rows_data": rows_data,
                        }
                    )

        # Remove trailing empty pages if any
        pages_raw = [p for p in pages_raw if p]
        if not pages_raw:
            # Document had no text; emit single empty page
            return DocumentResult(
                pages=[create_synthetic_page(0, [])],
                source_path=source_name,
                tree=DocumentTree(
                    pages=[PageTree(page_idx=0)], source_path=source_name
                ),
            )

        # Build DocumentPage and DocumentTree
        doc_pages: list[DocumentPage] = []
        tree_pages: list[PageTree] = []
        all_sections: list[Section] = []
        all_tables: list[TableNode] = []

        for page_idx, raw_items in enumerate(pages_raw):
            # Layout blocks with valid normalized bboxes
            spec = [
                (item["text"], item["kind"], {"level": item.get("level", 0)})
                for item in raw_items
            ]
            doc_blocks = layout_synthetic_blocks(spec)

            tree_children: list[BlockNode | TableNode] = []

            for doc_block, raw_item in zip(doc_blocks, raw_items, strict=True):
                item_type = raw_item["type"]
                bbox = doc_block.bbox

                if item_type == "table":
                    rows_data = raw_item["rows_data"]
                    num_rows = len(rows_data)
                    num_cols = max(len(r) for r in rows_data) if num_rows else 0

                    cell_nodes: list[list[BlockNode]] = []
                    for r_idx, row in enumerate(rows_data):
                        row_nodes: list[BlockNode] = []
                        for c_idx, cell_text in enumerate(row):
                            # Synthetic cell bbox within the table bbox
                            cell_bbox: BBox = (
                                bbox[0]
                                + (c_idx / max(num_cols, 1)) * (bbox[2] - bbox[0]),
                                bbox[1]
                                + (r_idx / max(num_rows, 1)) * (bbox[3] - bbox[1]),
                                bbox[0]
                                + ((c_idx + 1) / max(num_cols, 1))
                                * (bbox[2] - bbox[0]),
                                bbox[1]
                                + ((r_idx + 1) / max(num_rows, 1))
                                * (bbox[3] - bbox[1]),
                            )
                            c_node = BlockNode(
                                block_type=BlockType.TEXT,
                                bbox=cell_bbox,
                                text=cell_text,
                                page_idx=page_idx,
                                confidence=1.0,
                                trust_score=1.0,
                                trust_flags=("source:digital",),
                            )
                            row_nodes.append(c_node)
                        cell_nodes.append(row_nodes)

                    table_node = TableNode(
                        rows=num_rows,
                        cols=num_cols,
                        page_idx=page_idx,
                        bbox=bbox,
                        cells=cell_nodes,
                    )
                    tree_children.append(table_node)
                    all_tables.append(table_node)

                else:
                    b_type = BlockType.PARAGRAPH
                    if item_type == "heading":
                        b_type = BlockType.SECTION_HEADER
                    elif item_type == "list_item":
                        b_type = BlockType.LIST_ITEM

                    node = BlockNode(
                        block_type=b_type,
                        bbox=bbox,
                        text=raw_item["text"],
                        page_idx=page_idx,
                        confidence=1.0,
                        trust_score=1.0,
                        trust_flags=("source:digital",),
                        level=raw_item.get("level", 0),
                        spans=raw_item.get("spans", []),
                        metadata={"level": raw_item.get("level", 0)},
                    )
                    tree_children.append(node)

                    if item_type == "heading":
                        all_sections.append(
                            Section(
                                title=raw_item["text"],
                                level=raw_item.get("level", 1),
                                start_page=page_idx,
                                block_id=node.block_id,
                            )
                        )

            doc_pages.append(create_synthetic_page(page_idx, doc_blocks))
            tree_pages.append(PageTree(page_idx=page_idx, children=tree_children))

        tree = DocumentTree(
            pages=tree_pages,
            sections=all_sections,
            tables=all_tables,
            source_path=source_name,
            metadata={"source_format": "docx", "ingest": "digital_fastpath"},
        )

        return DocumentResult(
            pages=doc_pages,
            source_path=source_name,
            tree=tree,
        )
