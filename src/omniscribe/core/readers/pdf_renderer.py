"""Synthetic PDF renderer for digital document ingest.

Converts a :class:`~omniscribe.core.document.DocumentResult` into a clean
PDF with a real text layer using PyMuPDF (fitz), suitable for downstream
searchable PDF viewers and artifact stores.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from omniscribe.core.document import DocumentResult

__all__ = ["render_synthetic_pdf"]


def render_synthetic_pdf(document_result: DocumentResult) -> bytes:
    """Render a DocumentResult into PDF bytes with an embedded text layer."""
    import pymupdf as fitz

    doc = fitz.open()
    try:
        pages = document_result.pages
        if not pages:
            doc.new_page(width=595, height=842)
            return bytes(doc.tobytes())

        for page_data in pages:
            page_w = float(page_data.width or 595)
            page_h = float(page_data.height or 842)
            pdf_page = doc.new_page(width=page_w, height=page_h)

            margin_x = 50.0
            content_w = max(100.0, page_w - 2 * margin_x)
            y_cursor = 50.0

            for block in page_data.blocks:
                text = (block.text or "").strip()
                if not text:
                    continue

                # Determine font style and sizing
                kind = block.kind.lower()
                fontname = "Helvetica"
                fontsize = 10.5
                spacing = 6.0

                if kind in ("section_header", "heading"):
                    level = 1
                    level_val = block.metadata.get("level")
                    if isinstance(level_val, (int, float)):
                        level = max(1, min(6, int(level_val)))
                    fontsize = max(11.0, 19.0 - (level * 1.5))
                    fontname = "Helvetica-Bold"
                    spacing = 8.0
                elif kind == "code":
                    fontsize = 9.0
                    fontname = "Courier"
                    spacing = 5.0
                elif kind == "table":
                    fontsize = 8.5
                    fontname = "Courier"
                    spacing = 6.0
                elif kind == "list_item":
                    fontsize = 10.0
                    spacing = 4.0

                # Check if block has non-default custom bbox
                bx0, by0, bx1, by1 = block.bbox
                is_full_page = (
                    abs(bx0) < 1e-4
                    and abs(by0) < 1e-4
                    and abs(bx1 - 1.0) < 1e-4
                    and abs(by1 - 1.0) < 1e-4
                )

                if not is_full_page and bx1 > bx0 and by1 > by0:
                    rect = fitz.Rect(
                        bx0 * page_w,
                        by0 * page_h,
                        bx1 * page_w,
                        by1 * page_h,
                    )
                    rc = pdf_page.insert_textbox(
                        rect,
                        text,
                        fontsize=fontsize,
                        fontname=fontname,
                    )
                    if rc < 0:
                        # Fallback if box is too tight: draw at start point
                        pdf_page.insert_text(
                            (rect.x0, min(page_h - 20.0, rect.y0 + fontsize)),
                            text.splitlines()[0],
                            fontsize=fontsize,
                            fontname=fontname,
                        )
                else:
                    # Flow sequentially down the page
                    if y_cursor + 25.0 > page_h - 40.0:
                        pdf_page = doc.new_page(width=page_w, height=page_h)
                        y_cursor = 50.0

                    rect = fitz.Rect(
                        margin_x,
                        y_cursor,
                        margin_x + content_w,
                        page_h - 35.0,
                    )
                    rc = pdf_page.insert_textbox(
                        rect,
                        text,
                        fontsize=fontsize,
                        fontname=fontname,
                    )
                    if rc >= 0:
                        used_h = (page_h - 35.0 - y_cursor) - rc
                        y_cursor += max(fontsize + 2.0, used_h) + spacing
                    else:
                        # Overflow: increment cursor by conservative estimate
                        lines = len(text.splitlines())
                        y_cursor += (lines * (fontsize + 2.0)) + spacing

        return bytes(doc.tobytes())
    finally:
        doc.close()
