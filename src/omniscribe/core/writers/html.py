"""HTML export from :class:`DocumentTree`.

Implements the Azure Document Intelligence Markdown element vocabulary so the
output is round-trippable between HTML and DOCX:

- ``<h1>``..``<h6>`` for section headers
- ``<table>`` with ``<thead>`` / ``<tbody>`` (not pipe-tables)
- ``<figure>`` + ``<figcaption>`` for images
- ``<pre><code>`` for code blocks, ``<code>`` for inline
- ``<math>`` (MathML) for equations
- ``<!-- PageBreak -->`` markers between pages
- ``data-block-id`` and ``data-bbox`` on every block for the UI to re-bind
"""

from __future__ import annotations

import base64
import html
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, cast

from omniscribe.core.block_tree import TableNode, block_type_str
from omniscribe.core.writers._guard import TableDedup
from omniscribe.core.writers.exporter_base import BaseDocumentExporter

if TYPE_CHECKING:
    from omniscribe.core.block_tree import (
        BlockNode,
        DocumentTree,
        PageTree,
    )
    from omniscribe.core.document import DocumentResult


class HtmlExporter(BaseDocumentExporter):
    """Document exporter producing structured semantic HTML from DocumentTree or DocumentResult."""

    def export_tree(self, tree: DocumentTree, **kwargs: Any) -> str:
        """Render a DocumentTree to semantic HTML string."""
        return render_html(tree)

    def export_document(self, document: DocumentResult, **kwargs: Any) -> str:
        """Render a DocumentResult to semantic HTML string via DocumentTree."""
        from omniscribe.core.block_tree import from_document_result

        tree = from_document_result(document)
        return render_html(tree)


def render_html(tree: DocumentTree) -> str:
    """Render a :class:`DocumentTree` to a single HTML string."""
    out: list[str] = []
    out.append("<!DOCTYPE html>")
    out.append('<html lang="en">')
    out.append("<head>")
    out.append('<meta charset="utf-8">')
    title = tree.source_path or "Document"
    out.append(f"<title>{html.escape(title)}</title>")
    out.append(_embedded_css())
    out.append("</head><body>")
    rendered_table_ids = TableDedup()
    for i, page in enumerate(tree.pages):
        if i > 0:
            out.append("<!-- PageBreak -->")
        out.append(_render_page(page, rendered_table_ids))
    for table in tree.tables:
        if table in rendered_table_ids:
            continue
        out.append(_render_table(table))
        rendered_table_ids.add(table)
    out.append("</body></html>")
    return "\n".join(out)


def _embedded_css() -> str:
    return (
        "<style>"
        "body{font-family:Georgia,'Times New Roman',serif;max-width:780px;"
        "margin:2rem auto;padding:0 1rem;line-height:1.55;color:#111}"
        "h1,h2,h3,h4,h5,h6{margin-top:1.4em;line-height:1.2}"
        "table{border-collapse:collapse;margin:1em 0;width:100%}"
        "th,td{border:1px solid #ccc;padding:.4em .6em;text-align:left;vertical-align:top}"
        "th{background:#f3f3f3}"
        "figure{margin:1.2em 0;text-align:center}"
        "figcaption{font-size:.9em;color:#666;margin-top:.3em}"
        "pre{background:#f3f3f3;padding:.8em;overflow-x:auto;border-radius:4px}"
        "code{font-family:Menlo,Consolas,monospace;font-size:.9em}"
        "math{font-family:'Cambria Math',serif}"
        "[data-bbox]{display:block}"
        "ul,ol{padding-left:1.4em}"
        "</style>"
    )


def _render_page(page: PageTree, rendered_table_ids: TableDedup | None = None) -> str:
    parts: list[str] = []
    parts.append(f'<section data-page-idx="{page.page_idx}">')
    current_list: list[str] = []

    def flush_list() -> None:
        if current_list:
            parts.append("<ul>")
            parts.extend(current_list)
            parts.append("</ul>")
            current_list.clear()

    for child in page.children:
        if isinstance(child, TableNode):
            flush_list()
            if rendered_table_ids is not None:
                rendered_table_ids.add(child)
            parts.append(_render_table(child))
            continue

        bt = getattr(child, "block_type", None)
        bt_val = block_type_str(bt)
        if bt_val == "list_item":
            current_list.append(_render_block(child))
        else:
            flush_list()
            if bt_val == "table":
                if rendered_table_ids is not None:
                    rendered_table_ids.add(child)
                if hasattr(child, "cells") and getattr(child, "cells", None):
                    parts.append(_render_table(cast("TableNode", child)))
                elif getattr(child, "text", ""):
                    parts.append(f"<p>{html.escape(child.text)}</p>")
            else:
                parts.append(_render_block(child))

    flush_list()
    parts.append("</section>")
    return "\n".join(parts)


def _render_block(node: BlockNode | TableNode | Any) -> str:
    if isinstance(node, TableNode):
        return _render_table(node)
    bt = block_type_str(node.block_type)
    if bt == "table":
        if hasattr(node, "cells") and getattr(node, "cells", None):
            return _render_table(cast("TableNode", node))
        text = getattr(node, "text", "")
        return f"<p>{html.escape(text)}</p>" if text else ""
    data = (
        f' data-block-id="{node.block_id}"'
        f' data-bbox="{",".join(f"{v:.4f}" for v in node.bbox)}"'
    )
    if node.confidence is not None:
        data += f' data-confidence="{node.confidence:.3f}"'

    if bt == "section_header":
        level = max(1, min(6, getattr(node, "level", 1) or 1))
        tag = f"h{level}"
        return f"<{tag}{data}>{html.escape(node.text)}</{tag}>"
    if bt == "list_item":
        return f"<li{data}>{html.escape(node.text)}</li>"
    if bt == "code":
        return f"<pre{data}><code>{html.escape(node.text)}</code></pre>"
    if bt == "equation":
        latex = html.escape(getattr(node, "latex", None) or node.text)
        return f"<span{data}><code>{latex}</code></span>"
    if bt == "figure":
        img_html = ""
        # Figures can be either a BlockNode (caption-only) or a FigureNode
        # (image_bytes + caption). Handle both.
        image_bytes = getattr(node, "image_bytes", None)
        metadata = getattr(node, "metadata", {}) or {}
        if not image_bytes and metadata.get("image_bytes"):
            image_bytes = metadata["image_bytes"]
        if image_bytes:
            b64 = base64.b64encode(image_bytes).decode("ascii")
            img_html = f'<img src="data:image/png;base64,{b64}" alt="">'
        elif node.text and (
            node.text.startswith("http") or node.text.startswith("data:")
        ):
            img_html = f'<img src="{html.escape(node.text)}" alt="">'
        caption = getattr(node, "caption", None) or metadata.get("caption", "") or ""
        if (
            not caption
            and node.text
            and not (node.text.startswith("http") or node.text.startswith("data:"))
        ):
            caption = node.text
        cap = html.escape(caption)
        return f"<figure{data}>{img_html}<figcaption>{cap}</figcaption></figure>"
    if bt == "page_header":
        return f"<!-- PageHeader={html.escape(node.text)} -->"
    if bt == "page_footer":
        return f"<!-- PageFooter={html.escape(node.text)} -->"
    if bt == "page_number":
        return f"<!-- PageNumber={html.escape(node.text)} -->"

    text = _render_spans(node)
    return f"<p{data}>{text}</p>"


def _render_spans(node: BlockNode) -> str:
    if not getattr(node, "spans", None):
        return html.escape(node.text)
    out: list[str] = []
    for sp in node.spans:
        s = html.escape(sp.text)
        if sp.bold and sp.italic:
            s = f"<strong><em>{s}</em></strong>"
        elif sp.bold:
            s = f"<strong>{s}</strong>"
        elif sp.italic:
            s = f"<em>{s}</em>"
        if sp.code:
            s = f"<code>{s}</code>"
        out.append(s)
    return "".join(out)


def _render_table(table: TableNode | BlockNode | Any) -> str:
    cells = getattr(table, "cells", None)
    if not cells or not isinstance(cells, (list, tuple)):
        text = getattr(table, "text", "")
        if text:
            return f"<p>{html.escape(text)}</p>"
        return ""

    rows_count = getattr(table, "rows", len(cells))
    cols_count = getattr(
        table,
        "cols",
        max((len(r) for r in cells if isinstance(r, (list, tuple))), default=0),
    )
    block_id = getattr(table, "block_id", "")

    rows: list[str] = []
    for r_idx, row in enumerate(cells):
        if not isinstance(row, (list, tuple)):
            continue
        row_cells: list[str] = []
        for c in row:
            c_id = getattr(c, "block_id", "")
            c_text = getattr(c, "text", str(c) if c is not None else "")
            row_cells.append(
                "<td"
                + (f' data-block-id="{c_id}"' if c_id else "")
                + f">{html.escape(c_text)}</td>"
            )
        # for header row swap td->th
        if r_idx == 0:
            row_cells = [
                c.replace("<td", "<th").replace("</td>", "</th>") for c in row_cells
            ]
        rows.append(f"<tr>{''.join(row_cells)}</tr>")
    return (
        f'<table data-block-id="{block_id}" '
        f'data-rows="{rows_count}" data-cols="{cols_count}">'
        + f"<tbody>{''.join(rows)}</tbody></table>"
    )


def iter_blocks(tree: DocumentTree) -> Iterable[BlockNode]:
    for page in tree.pages:
        for child in page.children:
            if isinstance(child, TableNode):
                cells = getattr(child, "cells", [])
                for row in cells:
                    if isinstance(row, (list, tuple)):
                        for cell in row:
                            if isinstance(cell, BlockNode):
                                yield from _walk(cell)
            elif isinstance(child, BlockNode):
                yield from _walk(child)


def _walk(node: BlockNode) -> Iterable[BlockNode]:
    yield node
    for c in getattr(node, "children", []):
        yield from _walk(c)


__all__ = [
    "HtmlExporter",
    "iter_blocks",
    "render_html",
]
