"""Markdown export from :class:`DocumentTree` and :class:`DocumentResult`.

Renders document IR to GitHub-Flavored Markdown (GFM):
- Section headings: `#` .. `######` via `BlockNode.level` / `section_hierarchy`
- Tables: GFM pipe tables (`| col1 | col2 |`)
- Figures: `![caption](artifact_ref)` (or caption / bbox reference)
- Equations: `$$...$$` (block) or `$...$` (inline)
- Page breaks: `<!-- PageBreak: <page_idx> -->`
- Block text normalized and clean
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from omniscribe.core.block_tree import TableNode
from omniscribe.core.writers.exporter_base import BaseDocumentExporter

if TYPE_CHECKING:
    from omniscribe.core.block_tree import (
        BlockNode,
        DocumentTree,
        EquationNode,
        FigureNode,
        PageTree,
    )
    from omniscribe.core.document import DocumentResult


class MarkdownWriter(BaseDocumentExporter):
    """Document exporter producing structured GitHub-Flavored Markdown."""

    def export_tree(self, tree: DocumentTree, **kwargs: Any) -> str:
        """Render a DocumentTree to a Markdown string."""
        return render_markdown(tree, **kwargs)

    def export_document(self, document: DocumentResult, **kwargs: Any) -> str:
        """Render a DocumentResult to a Markdown string via DocumentTree."""
        from omniscribe.core.block_tree import from_document_result

        tree = from_document_result(document)
        return render_markdown(tree, **kwargs)


MarkdownExporter = MarkdownWriter


def render_markdown(
    tree: DocumentTree,
    *,
    include_page_breaks: bool = True,
    **kwargs: Any,
) -> str:
    """Render a :class:`DocumentTree` to a clean GFM string."""
    out: list[str] = []
    rendered_table_ids: set[str | int] = set()

    for i, page in enumerate(tree.pages):
        if i > 0 and include_page_breaks:
            out.append(f"<!-- PageBreak: {page.page_idx} -->")
        page_content = _render_page(page, rendered_table_ids)
        if page_content:
            out.append(page_content)

    # Render any unrendered tables from tree.tables
    for table in tree.tables:
        t_id = getattr(table, "block_id", "")
        if (not t_id or t_id not in rendered_table_ids) and id(
            table
        ) not in rendered_table_ids:
            tbl_str = _render_table(table)
            if tbl_str:
                out.append(tbl_str)
            if t_id:
                rendered_table_ids.add(t_id)
            rendered_table_ids.add(id(table))

    # Also render any unrendered figures from tree.figures not yet covered
    rendered_figure_ids: set[str | int] = {
        node.block_id
        for page in tree.pages
        for node in page.children
        if getattr(node, "block_id", None)
    }
    for fig in getattr(tree, "figures", []):
        if (
            fig.block_id not in rendered_figure_ids
            and id(fig) not in rendered_figure_ids
        ):
            fig_str = _render_figure_node(fig)
            if fig_str:
                out.append(fig_str)
            rendered_figure_ids.add(fig.block_id)
            rendered_figure_ids.add(id(fig))

    # Also render any unrendered equations from tree.equations
    rendered_equation_ids: set[str | int] = {
        node.block_id
        for page in tree.pages
        for node in page.children
        if getattr(node, "block_id", None)
    }
    for eq in getattr(tree, "equations", []):
        if (
            eq.block_id not in rendered_equation_ids
            and id(eq) not in rendered_equation_ids
        ):
            eq_str = _render_equation_node(eq)
            if eq_str:
                out.append(eq_str)
            rendered_equation_ids.add(eq.block_id)
            rendered_equation_ids.add(id(eq))

    nl2 = chr(10) + chr(10)
    nl = chr(10)
    result = nl2.join(b for b in out if b.strip()).strip()
    return f"{result}{nl}" if result else ""


def _render_page(
    page: PageTree,
    rendered_table_ids: set[str | int],
) -> str:
    blocks: list[str] = []
    current_list: list[str] = []
    nl = chr(10)
    nl2 = chr(10) + chr(10)

    def flush_list() -> None:
        if current_list:
            blocks.append(nl.join(current_list))
            current_list.clear()

    for child in page.children:
        if isinstance(child, TableNode):
            flush_list()
            if child.block_id:
                rendered_table_ids.add(child.block_id)
            rendered_table_ids.add(id(child))
            tbl_str = _render_table(child)
            if tbl_str:
                blocks.append(tbl_str)
            continue

        bt = getattr(child, "block_type", None)
        bt_val = (
            bt.value if (bt is not None and hasattr(bt, "value")) else str(bt or "")
        )

        if bt_val == "list_item":
            item_str = _render_block(child)
            if item_str:
                current_list.append(item_str)
        else:
            flush_list()
            if bt_val == "table":
                if getattr(child, "block_id", None):
                    rendered_table_ids.add(child.block_id)
                rendered_table_ids.add(id(child))
                tbl_str = _render_table(child)
                if tbl_str:
                    blocks.append(tbl_str)
            else:
                rendered = _render_block(child)
                if rendered:
                    blocks.append(rendered)

    flush_list()
    return nl2.join(blocks)


def _render_block(node: BlockNode | TableNode | Any) -> str:
    if isinstance(node, TableNode):
        return _render_table(node)

    bt = (
        node.block_type.value
        if hasattr(node.block_type, "value")
        else str(node.block_type)
    )

    if bt == "section_header":
        level = getattr(node, "level", 0) or 0
        if level <= 0 and getattr(node, "section_hierarchy", None):
            level = len(node.section_hierarchy)
        heading_level = max(1, min(6, level if level > 0 else 1))
        prefix = "#" * heading_level
        text = _clean_text(node.text)
        return f"{prefix} {text}"

    if bt == "list_item":
        level = max(0, getattr(node, "level", 0) or 0)
        indent = "  " * level
        text = _render_spans_or_text(node)
        return f"{indent}- {text}"

    if bt == "code":
        code_text = node.text.strip("\n")
        nl = chr(10)
        return f"```{nl}{code_text}{nl}```"

    if bt == "equation":
        latex = getattr(node, "latex", None) or node.text
        return f"$${latex.strip()}$$"

    if bt == "figure":
        return _render_figure_node(node)

    if bt == "table":
        return _render_table(node)

    if bt == "key_value":
        metadata = getattr(node, "metadata", {}) or {}
        key = metadata.get("key", "").strip()
        val = _render_spans_or_text(node)
        if key:
            return f"**{key}:** {val}"
        return val

    if bt == "page_header":
        clean = _clean_text(node.text)
        return f"<!-- PageHeader: {clean} -->" if clean else ""

    if bt == "page_footer":
        clean = _clean_text(node.text)
        return f"<!-- PageFooter: {clean} -->" if clean else ""

    if bt == "page_number":
        clean = _clean_text(node.text)
        return f"<!-- PageNumber: {clean} -->" if clean else ""

    text = _render_spans_or_text(node)
    return text


def _clean_text(text: str | None) -> str:
    if not text:
        return ""
    lines = [line.strip() for line in text.splitlines()]
    return " ".join(line for line in lines if line)


def _render_spans_or_text(node: BlockNode | Any) -> str:
    spans = getattr(node, "spans", None)
    if not spans:
        return _clean_text(getattr(node, "text", ""))

    out: list[str] = []
    for sp in spans:
        s = sp.text
        if sp.bold and sp.italic:
            s = f"***{s}***"
        elif sp.bold:
            s = f"**{s}**"
        elif sp.italic:
            s = f"*{s}*"
        if sp.code:
            s = f"`{s}`"
        out.append(s)
    return "".join(out).strip()


def _render_figure_node(node: FigureNode | BlockNode | Any) -> str:
    metadata = getattr(node, "metadata", {}) or {}
    caption = getattr(node, "caption", "") or metadata.get("caption", "") or ""
    text = getattr(node, "text", "") or ""

    if (
        not caption
        and text
        and not (
            text.startswith("http://")
            or text.startswith("https://")
            or text.startswith("data:")
            or text.startswith("artifact:")
        )
    ):
        caption = text

    ref = (
        getattr(node, "artifact_ref", None)
        or metadata.get("artifact_ref")
        or metadata.get("image_url")
        or metadata.get("image_path")
    )
    if not ref:
        if (
            text.startswith("http://")
            or text.startswith("https://")
            or text.startswith("data:")
            or text.startswith("artifact:")
        ):
            ref = text
        elif metadata.get("artifact_id"):
            ref = f"artifact:{metadata['artifact_id']}"
        else:
            bbox = getattr(node, "bbox", None)
            if bbox:
                bbox_str = ",".join(f"{v:.4f}" for v in bbox)
                ref = f"bbox:{bbox_str}"
            else:
                block_id = getattr(node, "block_id", "figure")
                ref = f"block:{block_id}"

    cap_clean = _clean_text(caption)
    return f"![{cap_clean}]({ref})"


def _render_equation_node(node: EquationNode | BlockNode | Any) -> str:
    latex = getattr(node, "latex", None) or getattr(node, "text", "") or ""
    return f"$${str(latex).strip()}$$"


def _render_table(table: TableNode | BlockNode | Any) -> str:
    cells = getattr(table, "cells", None)
    if not cells or not isinstance(cells, (list, tuple)):
        text = getattr(table, "text", "")
        if not text:
            return ""
        if "|" in text:
            return text.strip()
        return text.strip()

    rows: list[list[str]] = []
    max_cols = 0
    for row in cells:
        if not isinstance(row, (list, tuple)):
            continue
        row_vals = [_clean_table_cell(getattr(c, "text", str(c))) for c in row]
        if len(row_vals) > max_cols:
            max_cols = len(row_vals)
        rows.append(row_vals)

    if not rows or max_cols == 0:
        return ""

    for row in rows:
        while len(row) < max_cols:
            row.append("")

    header_row = rows[0]
    separator_row = ["---"] * max_cols
    data_rows = rows[1:]

    lines: list[str] = [
        "| " + " | ".join(header_row) + " |",
        "| " + " | ".join(separator_row) + " |",
    ]
    for r in data_rows:
        lines.append("| " + " | ".join(r) + " |")

    nl = chr(10)
    return nl.join(lines)


def _clean_table_cell(text: str | None) -> str:
    if not text:
        return ""
    cleaned = " ".join(text.splitlines()).strip()
    return cleaned.replace("|", r"\|")


__all__ = [
    "MarkdownExporter",
    "MarkdownWriter",
    "render_markdown",
]
