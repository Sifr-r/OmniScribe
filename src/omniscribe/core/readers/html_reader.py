"""HTML document reader for digital ingest fast path.

Parses .html and .htm documents into canonical :class:`DocumentResult` and
:class:`DocumentTree` structures. Uses ``selectolax`` if installed; gracefully
falls back to the standard library :class:`html.parser.HTMLParser` so imports
and execution never fail even without optional parser dependencies.
"""

from __future__ import annotations

import io
from html.parser import HTMLParser as StdlibHTMLParser
from pathlib import Path
from typing import Any

from omniscribe.core.block_tree import (
    BlockNode,
    BlockType,
    DocumentTree,
    PageTree,
    Section,
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

try:
    from selectolax.parser import (
        HTMLParser as SelectolaxParser,
    )

    _HAS_SELECTOLAX = True
except ImportError:
    SelectolaxParser = None
    _HAS_SELECTOLAX = False

__all__ = ["HtmlReader"]


def _format_markdown_table(rows_data: list[list[str]]) -> str:
    """Format a 2D table grid into markdown table text."""
    if not rows_data or not rows_data[0]:
        return ""
    col_count = max(len(row) for row in rows_data)
    padded = [row + [""] * (col_count - len(row)) for row in rows_data]
    lines: list[str] = []
    header_row = padded[0]
    lines.append(
        "| " + " | ".join(cell.replace("\n", " ").strip() for cell in header_row) + " |"
    )
    lines.append("| " + " | ".join(["---"] * col_count) + " |")
    for row in padded[1:]:
        lines.append(
            "| " + " | ".join(cell.replace("\n", " ").strip() for cell in row) + " |"
        )
    return "\n".join(lines)


class _StdlibHTMLDocParser(StdlibHTMLParser):
    """Event-driven parser using the standard library html.parser."""

    _SKIP_TAGS = frozenset({"script", "style", "head", "svg", "noscript"})
    _HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.pages: list[list[dict[str, Any]]] = [[]]
        self._skip_depth = 0
        self._tag_stack: list[str] = []

        # Current element accumulation
        self._active_tag: str | None = None
        self._active_text: list[str] = []
        self._active_level: int = 0

        # Table state
        self._in_table = False
        self._table_rows: list[list[str]] = []
        self._current_row: list[str] = []
        self._cell_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        self._tag_stack.append(tag_lower)

        if tag_lower in self._SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth > 0:
            return

        if tag_lower == "hr":
            # Thematic break: start a new page if current page has items
            if self.pages[-1]:
                self.pages.append([])
            return

        if tag_lower in self._HEADING_TAGS:
            self._flush_active()
            level = int(tag_lower[1])
            # H1 starts a new page if current page has items
            if level == 1 and self.pages[-1]:
                self.pages.append([])
            self._active_tag = tag_lower
            self._active_level = level
            self._active_text = []

        elif tag_lower == "p":
            self._flush_active()
            self._active_tag = "p"
            self._active_text = []

        elif tag_lower == "li":
            self._flush_active()
            self._active_tag = "li"
            self._active_text = []

        elif tag_lower in ("pre", "code") and self._active_tag != "pre":
            self._flush_active()
            self._active_tag = tag_lower
            self._active_text = []

        elif tag_lower == "blockquote":
            self._flush_active()
            self._active_tag = "blockquote"
            self._active_text = []

        elif tag_lower == "table":
            self._flush_active()
            self._in_table = True
            self._table_rows = []

        elif tag_lower == "tr" and self._in_table:
            self._current_row = []

        elif tag_lower in ("th", "td") and self._in_table:
            self._cell_text = []

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if self._tag_stack and self._tag_stack[-1] == tag_lower:
            self._tag_stack.pop()

        if tag_lower in self._SKIP_TAGS:
            if self._skip_depth > 0:
                self._skip_depth -= 1
            return
        if self._skip_depth > 0:
            return

        if tag_lower in self._HEADING_TAGS and self._active_tag == tag_lower:
            text = "".join(self._active_text).strip()
            if text:
                self.pages[-1].append(
                    {
                        "type": "heading",
                        "kind": "section_header",
                        "level": self._active_level,
                        "text": text,
                    }
                )
            self._active_tag = None
            self._active_text = []

        elif tag_lower == "p" and self._active_tag == "p":
            text = "".join(self._active_text).strip()
            if text:
                self.pages[-1].append(
                    {
                        "type": "paragraph",
                        "kind": "paragraph",
                        "level": 0,
                        "text": text,
                    }
                )
            self._active_tag = None
            self._active_text = []

        elif tag_lower == "li" and self._active_tag == "li":
            text = "".join(self._active_text).strip()
            if text:
                self.pages[-1].append(
                    {
                        "type": "list_item",
                        "kind": "list_item",
                        "level": 0,
                        "text": text,
                    }
                )
            self._active_tag = None
            self._active_text = []

        elif tag_lower in ("pre", "code") and self._active_tag == tag_lower:
            text = "".join(self._active_text).strip()
            if text:
                self.pages[-1].append(
                    {
                        "type": "code",
                        "kind": "code",
                        "level": 0,
                        "text": text,
                    }
                )
            self._active_tag = None
            self._active_text = []

        elif tag_lower == "blockquote" and self._active_tag == "blockquote":
            text = "".join(self._active_text).strip()
            if text:
                self.pages[-1].append(
                    {
                        "type": "paragraph",
                        "kind": "paragraph",
                        "level": 0,
                        "text": text,
                    }
                )
            self._active_tag = None
            self._active_text = []

        elif tag_lower in ("th", "td") and self._in_table:
            self._current_row.append("".join(self._cell_text).strip())
            self._cell_text = []

        elif tag_lower == "tr" and self._in_table:
            if self._current_row:
                self._table_rows.append(self._current_row)
            self._current_row = []

        elif tag_lower == "table" and self._in_table:
            if self._table_rows and any(any(c for c in r) for r in self._table_rows):
                table_md = _format_markdown_table(self._table_rows)
                self.pages[-1].append(
                    {
                        "type": "table",
                        "kind": "table",
                        "level": 0,
                        "text": table_md,
                        "rows_data": self._table_rows,
                    }
                )
            self._in_table = False
            self._table_rows = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        if self._in_table and (self._tag_stack and self._tag_stack[-1] in ("th", "td")):
            self._cell_text.append(data)
        elif self._active_tag is not None:
            self._active_text.append(data)
        else:
            # Bare text outside known container tags
            cleaned = data.strip()
            if cleaned:
                self._active_tag = "p"
                self._active_text = [data]

    def _flush_active(self) -> None:
        if self._active_tag is not None and self._active_text:
            text = "".join(self._active_text).strip()
            if text:
                item_type = "paragraph"
                kind = "paragraph"
                if self._active_tag in self._HEADING_TAGS:
                    item_type = "heading"
                    kind = "section_header"
                elif self._active_tag == "li":
                    item_type = "list_item"
                    kind = "list_item"
                elif self._active_tag in ("pre", "code"):
                    item_type = "code"
                    kind = "code"
                self.pages[-1].append(
                    {
                        "type": item_type,
                        "kind": kind,
                        "level": self._active_level,
                        "text": text,
                    }
                )
        self._active_tag = None
        self._active_text = []
        self._active_level = 0

    def finalize(self) -> list[list[dict[str, Any]]]:
        self._flush_active()
        return [p for p in self.pages if p]


def _parse_with_selectolax(html_text: str) -> list[list[dict[str, Any]]]:
    """Parse HTML using selectolax if available."""
    assert SelectolaxParser is not None
    tree = SelectolaxParser(html_text)

    # Remove unwanted nodes
    for tag in ("script", "style", "head", "svg", "noscript"):
        for node in tree.css(tag):
            node.decompose()

    pages_raw: list[list[dict[str, Any]]] = [[]]

    # Walk body or root
    body = tree.body or tree.root
    if body is None:
        return []

    for node in body.iter():
        tag = (node.tag or "").lower()
        if tag == "hr":
            if pages_raw[-1]:
                pages_raw.append([])
            continue

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag[1])
            text = node.text(strip=True)
            if not text:
                continue
            if level == 1 and pages_raw[-1]:
                pages_raw.append([])
            pages_raw[-1].append(
                {
                    "type": "heading",
                    "kind": "section_header",
                    "level": level,
                    "text": text,
                }
            )

        elif tag == "p":
            text = node.text(strip=True)
            if text:
                pages_raw[-1].append(
                    {
                        "type": "paragraph",
                        "kind": "paragraph",
                        "level": 0,
                        "text": text,
                    }
                )

        elif tag == "li":
            text = node.text(strip=True)
            if text:
                pages_raw[-1].append(
                    {
                        "type": "list_item",
                        "kind": "list_item",
                        "level": 0,
                        "text": text,
                    }
                )

        elif tag in ("pre", "code"):
            # Avoid duplicating pre and nested code
            if tag == "code" and node.parent and node.parent.tag == "pre":
                continue
            text = node.text(strip=True)
            if text:
                pages_raw[-1].append(
                    {
                        "type": "code",
                        "kind": "code",
                        "level": 0,
                        "text": text,
                    }
                )

        elif tag == "table":
            rows_data: list[list[str]] = []
            for tr in node.css("tr"):
                cells = [td.text(strip=True) for td in tr.css("th, td")]
                if cells:
                    rows_data.append(cells)
            if rows_data and any(any(c for c in r) for r in rows_data):
                pages_raw[-1].append(
                    {
                        "type": "table",
                        "kind": "table",
                        "level": 0,
                        "text": _format_markdown_table(rows_data),
                        "rows_data": rows_data,
                    }
                )

    return [p for p in pages_raw if p]


class HtmlReader(BaseDocumentReader):
    """Parses .html / .htm files into canonical DocumentResult and DocumentTree."""

    def read(
        self,
        source: Path | bytes | io.BytesIO,
        filename: str = "",
    ) -> DocumentResult:
        raw_bytes = resolve_source_bytes(source)
        source_name = filename or (
            str(source) if isinstance(source, (str, Path)) else "document.html"
        )

        # Decode text with fallback
        try:
            html_text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            try:
                html_text = raw_bytes.decode("latin-1")
            except Exception as exc:
                raise MalformedDocumentError(
                    f"Failed to decode HTML: {exc}",
                    details={"filename": source_name},
                ) from exc

        try:
            if _HAS_SELECTOLAX:
                pages_raw = _parse_with_selectolax(html_text)
            else:
                parser = _StdlibHTMLDocParser()
                parser.feed(html_text)
                pages_raw = parser.finalize()
        except Exception as exc:
            raise MalformedDocumentError(
                f"Failed to parse HTML document: {exc}",
                details={"filename": source_name, "error": str(exc)},
            ) from exc

        if not pages_raw:
            return DocumentResult(
                pages=[create_synthetic_page(0, [])],
                source_path=source_name,
                tree=DocumentTree(
                    pages=[PageTree(page_idx=0)], source_path=source_name
                ),
            )

        doc_pages: list[DocumentPage] = []
        tree_pages: list[PageTree] = []
        all_sections: list[Section] = []
        all_tables: list[TableNode] = []

        for page_idx, raw_items in enumerate(pages_raw):
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
                    rows_data = raw_item.get("rows_data", [])
                    num_rows = len(rows_data)
                    num_cols = max(len(r) for r in rows_data) if num_rows else 0

                    cell_nodes: list[list[BlockNode]] = []
                    for r_idx, row in enumerate(rows_data):
                        row_nodes: list[BlockNode] = []
                        for c_idx, cell_text in enumerate(row):
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
                    elif item_type == "code":
                        b_type = BlockType.CODE

                    node = BlockNode(
                        block_type=b_type,
                        bbox=bbox,
                        text=raw_item["text"],
                        page_idx=page_idx,
                        confidence=1.0,
                        trust_score=1.0,
                        trust_flags=("source:digital",),
                        level=raw_item.get("level", 0),
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
            metadata={"source_format": "html", "ingest": "digital_fastpath"},
        )

        return DocumentResult(
            pages=doc_pages,
            source_path=source_name,
            tree=tree,
        )
