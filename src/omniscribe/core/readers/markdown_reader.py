"""Markdown document reader for digital ingest fast path.

Parses .md and .markdown documents into canonical :class:`DocumentResult` and
:class:`DocumentTree` structures. Uses ``mistune`` if available; gracefully
falls back to a robust line-based / regex parser when ``mistune`` is not
installed, ensuring imports and execution never fail.
"""

from __future__ import annotations

import io
import re
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
    import mistune

    _HAS_MISTUNE = True
except ImportError:
    mistune = None
    _HAS_MISTUNE = False

__all__ = ["MarkdownReader"]

_RE_ATX_HEADING = re.compile(r"^(#{1,6})\s+(.*?)(?:\s+#+)?$")
_RE_THEMATIC_BREAK = re.compile(r"^(\*{3,}|-{3,}|_{3,})\s*$")
_RE_LIST_ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
_RE_TABLE_SEP = re.compile(r"^\|?(\s*:?-{2,}:?\s*\|?)+\s*$")


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


def _split_table_row(row_str: str) -> list[str]:
    """Split a pipe-delimited table row string into individual cells."""
    s = row_str.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    # Split by unescaped pipe
    parts = re.split(r"(?<!\\)\|", s)
    return [p.strip().replace(r"\|", "|") for p in parts]


def _parse_markdown_fallback(md_text: str) -> list[list[dict[str, Any]]]:
    """Line-based fallback parser for Markdown documents."""
    lines = md_text.splitlines()
    pages_raw: list[list[dict[str, Any]]] = [[]]

    i = 0
    num_lines = len(lines)

    while i < num_lines:
        line = lines[i]
        stripped = line.strip()

        # Blank line
        if not stripped:
            i += 1
            continue

        # Code fence (``` or ~~~)
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fence = stripped[:3]
            code_lines: list[str] = []
            i += 1
            while i < num_lines and not lines[i].strip().startswith(fence):
                code_lines.append(lines[i])
                i += 1
            if i < num_lines:
                i += 1  # consume closing fence
            code_text = "\n".join(code_lines)
            pages_raw[-1].append(
                {
                    "type": "code",
                    "kind": "code",
                    "level": 0,
                    "text": code_text,
                }
            )
            continue

        # ATX Heading (# Heading)
        atx_match = _RE_ATX_HEADING.match(stripped)
        if atx_match:
            level = len(atx_match.group(1))
            heading_text = atx_match.group(2).strip()
            if level == 1 and pages_raw[-1]:
                pages_raw.append([])
            pages_raw[-1].append(
                {
                    "type": "heading",
                    "kind": "section_header",
                    "level": level,
                    "text": heading_text,
                }
            )
            i += 1
            continue

        # Check for Setext heading: line followed by === or ---
        if i + 1 < num_lines:
            next_line = lines[i + 1].strip()
            if re.match(r"^={2,}\s*$", next_line):
                heading_text = stripped
                if pages_raw[-1]:
                    pages_raw.append([])
                pages_raw[-1].append(
                    {
                        "type": "heading",
                        "kind": "section_header",
                        "level": 1,
                        "text": heading_text,
                    }
                )
                i += 2
                continue
            if re.match(r"^-{2,}\s*$", next_line) and not _RE_TABLE_SEP.match(
                next_line
            ):
                heading_text = stripped
                pages_raw[-1].append(
                    {
                        "type": "heading",
                        "kind": "section_header",
                        "level": 2,
                        "text": heading_text,
                    }
                )
                i += 2
                continue

        # Thematic break (--- or *** or ___)
        if _RE_THEMATIC_BREAK.match(stripped):
            if pages_raw[-1]:
                pages_raw.append([])
            i += 1
            continue

        # Table detection (line containing '|' followed by a table separator line)
        if (
            "|" in line
            and i + 1 < num_lines
            and _RE_TABLE_SEP.match(lines[i + 1].strip())
        ):
            table_rows: list[list[str]] = [_split_table_row(line)]
            i += 2  # skip header and separator
            while i < num_lines:
                curr = lines[i].strip()
                if not curr or "|" not in curr:
                    break
                table_rows.append(_split_table_row(curr))
                i += 1

            if table_rows and any(any(c for c in r) for r in table_rows):
                pages_raw[-1].append(
                    {
                        "type": "table",
                        "kind": "table",
                        "level": 0,
                        "text": _format_markdown_table(table_rows),
                        "rows_data": table_rows,
                    }
                )
            continue

        # Blockquote (> text)
        if stripped.startswith(">"):
            quote_lines: list[str] = []
            while i < num_lines and lines[i].strip().startswith(">"):
                quote_lines.append(re.sub(r"^>\s?", "", lines[i].strip()))
                i += 1
            quote_text = " ".join(quote_lines).strip()
            if quote_text:
                pages_raw[-1].append(
                    {
                        "type": "paragraph",
                        "kind": "paragraph",
                        "level": 0,
                        "text": quote_text,
                    }
                )
            continue

        # List items (- item, * item, + item, 1. item)
        list_match = _RE_LIST_ITEM.match(line)
        if list_match:
            item_text = list_match.group(3).strip()
            pages_raw[-1].append(
                {
                    "type": "list_item",
                    "kind": "list_item",
                    "level": len(list_match.group(1)) // 2,
                    "text": item_text,
                }
            )
            i += 1
            continue

        # Regular paragraph: accumulate non-blank lines until blank, heading, table, or fence
        para_lines: list[str] = [stripped]
        i += 1
        while i < num_lines:
            curr = lines[i]
            c_stripped = curr.strip()
            if not c_stripped:
                break
            if (
                _RE_ATX_HEADING.match(c_stripped)
                or c_stripped.startswith("```")
                or c_stripped.startswith("~~~")
                or _RE_THEMATIC_BREAK.match(c_stripped)
                or _RE_LIST_ITEM.match(curr)
                or c_stripped.startswith(">")
            ):
                break
            if (
                "|" in curr
                and i + 1 < num_lines
                and _RE_TABLE_SEP.match(lines[i + 1].strip())
            ):
                break
            if i + 1 < num_lines and (
                re.match(r"^={2,}\s*$", lines[i + 1].strip())
                or re.match(r"^-{2,}\s*$", lines[i + 1].strip())
            ):
                break
            para_lines.append(c_stripped)
            i += 1

        para_text = " ".join(para_lines).strip()
        if para_text:
            pages_raw[-1].append(
                {
                    "type": "paragraph",
                    "kind": "paragraph",
                    "level": 0,
                    "text": para_text,
                }
            )

    return [p for p in pages_raw if p]


def _parse_with_mistune(md_text: str) -> list[list[dict[str, Any]]]:
    """Parse Markdown using mistune if available, mapping tokens to raw items."""
    assert mistune is not None
    # mistune v3/v2 AST rendering
    try:
        markdown_parser = mistune.create_markdown(renderer="ast")
        ast = markdown_parser(md_text)
    except Exception:
        # Fallback to line parser if mistune version AST differs
        return _parse_markdown_fallback(md_text)

    pages_raw: list[list[dict[str, Any]]] = [[]]

    for token in ast:
        t_type = token.get("type", "")
        if t_type == "heading":
            level = int(token.get("level", 1) or 1)
            # Extract plain text from children
            text = "".join(c.get("text", "") for c in token.get("children", []))
            if level == 1 and pages_raw[-1]:
                pages_raw.append([])
            pages_raw[-1].append(
                {
                    "type": "heading",
                    "kind": "section_header",
                    "level": level,
                    "text": text.strip(),
                }
            )
        elif t_type == "paragraph":
            text = "".join(c.get("text", "") for c in token.get("children", []))
            if text.strip():
                pages_raw[-1].append(
                    {
                        "type": "paragraph",
                        "kind": "paragraph",
                        "level": 0,
                        "text": text.strip(),
                    }
                )
        elif t_type == "block_code":
            code_text = token.get("text", "")
            if code_text.strip():
                pages_raw[-1].append(
                    {
                        "type": "code",
                        "kind": "code",
                        "level": 0,
                        "text": code_text.strip(),
                    }
                )
        elif t_type == "list":
            for item in token.get("children", []):
                item_text = "".join(
                    "".join(c.get("text", "") for c in child.get("children", []))
                    for child in item.get("children", [])
                )
                if item_text.strip():
                    pages_raw[-1].append(
                        {
                            "type": "list_item",
                            "kind": "list_item",
                            "level": 0,
                            "text": item_text.strip(),
                        }
                    )
        elif t_type == "thematic_break":
            if pages_raw[-1]:
                pages_raw.append([])

    # Fallback to line parser if mistune emitted empty or missed tables
    if not any(pages_raw) or any(t.get("type") == "table" for t in ast):
        # Line parser supports pipe tables natively
        return _parse_markdown_fallback(md_text)

    return [p for p in pages_raw if p]


class MarkdownReader(BaseDocumentReader):
    """Parses .md and .markdown documents into DocumentResult and DocumentTree."""

    def read(
        self,
        source: Path | bytes | io.BytesIO,
        filename: str = "",
    ) -> DocumentResult:
        raw_bytes = resolve_source_bytes(source)
        source_name = filename or (
            str(source) if isinstance(source, (str, Path)) else "document.md"
        )

        try:
            md_text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            try:
                md_text = raw_bytes.decode("latin-1")
            except Exception as exc:
                raise MalformedDocumentError(
                    f"Failed to decode Markdown text: {exc}",
                    details={"filename": source_name},
                ) from exc

        try:
            if _HAS_MISTUNE:
                pages_raw = _parse_with_mistune(md_text)
            else:
                pages_raw = _parse_markdown_fallback(md_text)
        except Exception as exc:
            raise MalformedDocumentError(
                f"Failed to parse Markdown document: {exc}",
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
            metadata={"source_format": "markdown", "ingest": "digital_fastpath"},
        )

        return DocumentResult(
            pages=doc_pages,
            source_path=source_name,
            tree=tree,
        )
