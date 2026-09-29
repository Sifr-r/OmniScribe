"""
Embedder module for invisible text layer PDF rendering.

Handles embedding selectable invisible text over rasterized background pages
matching normalized bbox coordinates ([x0, y0, x1, y1] in 0..1), font sizing,
and searchable PDF output generation.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

import pymupdf as fitz  # PyMuPDF

from omniscribe.core.document import BBox
from omniscribe.core.pdf.embedder_helpers import (
    # Thread-pool executor (used by embed_structured_text)
    _EMBED_RASTER_WORKERS,
    # Font probing and log helpers
    _PROBE_CODEPOINTS,
    # Image-input branch
    _build_image_sandwich_pdf,
    # Per-page rendering pipeline
    _draw_invisible_text,
    _embed_from_image_input,
    _log_once,
    # Per-page rasterization
    _rasterize_embed_page,
)
from omniscribe.core.pdf.rasterizer import (
    _emit_pymupdf_agpl_notice,
    _is_image_path,
)

logger = logging.getLogger(__name__)


def embed_structured_text(
    input_pdf_path: str | Path,
    output_pdf_path: str | Path,
    pages_data: dict[int, list[tuple[BBox, str]]],
    dpi: int = 200,
    parallelism: int = _EMBED_RASTER_WORKERS,
    page_nums: Sequence[int] | None = None,
) -> None:
    """
    Build a searchable "sandwich" PDF: rasterize each page as a background
    image and overlay invisible text positioned to match the source layout.

    Accepts either a PDF or a raw image (JPEG/PNG/TIFF/BMP/WebP/AVIF)
    as input. Per-page rasterization and embedding run serially because
    PyMuPDF documents are not thread-safe.

    ``page_nums`` (audit P2-9) restricts the output to the given source
    page indices, in the given order. ``None`` (the default) rasterizes
    the whole document — the pre-P2 behaviour. Subset runs (``pages="1-3"``
    on a 100-page PDF) pass the processed pages here so the embed pass
    no longer re-rasterizes pages that were never OCR'd.
    """
    if _is_image_path(input_pdf_path):
        _embed_from_image_input(
            input_pdf_path, output_pdf_path, pages_data, page_nums=page_nums
        )
        return

    _emit_pymupdf_agpl_notice()

    doc = fitz.open(input_pdf_path)
    new_doc = fitz.open()

    try:
        page_nums = (
            [pn for pn in page_nums if 0 <= pn < len(doc)]
            if page_nums is not None
            else list(range(len(doc)))
        )
        if not page_nums:
            if len(new_doc) == 0:
                new_doc.new_page(width=595.0, height=842.0)
            new_doc.save(output_pdf_path, garbage=3, deflate=True)
            return

        for pn in page_nums:
            width, height, img_data = _rasterize_embed_page(doc[pn], dpi)
            new_page = new_doc.new_page(width=width, height=height)
            new_page.insert_image(new_page.rect, stream=img_data)
            for rect_coords, text in pages_data.get(pn, []):
                _draw_invisible_text(new_page, rect_coords, text, width, height)

        if len(new_doc) == 0:
            new_doc.new_page(width=595.0, height=842.0)
        new_doc.save(output_pdf_path, garbage=3, deflate=True)
    finally:
        new_doc.close()
        doc.close()


__all__ = [
    "_PROBE_CODEPOINTS",
    "_build_image_sandwich_pdf",
    "_log_once",
    "embed_structured_text",
]
