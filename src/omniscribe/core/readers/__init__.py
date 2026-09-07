"""Digital document readers for OmniScribe (RFC 004 R2 Fast Path).

Enables direct parsing of structured digital document formats (DOCX, HTML,
Markdown) into canonical :class:`~omniscribe.core.document.DocumentResult`
and :class:`~omniscribe.core.block_tree.DocumentTree` representations,
completely bypassing VLM and Surya layout detection.
"""

from __future__ import annotations

from omniscribe.core.readers.base import (
    BaseDocumentReader,
    MalformedDocumentError,
    ReaderError,
    UnsupportedDocumentError,
    create_synthetic_block,
    create_synthetic_page,
    layout_synthetic_blocks,
    resolve_source_bytes,
)
from omniscribe.core.readers.dispatch import (
    get_reader_for_suffix,
    register_reader,
    supported_suffixes,
)
from omniscribe.core.readers.docx_reader import DocxReader
from omniscribe.core.readers.html_reader import HtmlReader
from omniscribe.core.readers.markdown_reader import MarkdownReader
from omniscribe.core.readers.pdf_renderer import render_synthetic_pdf

__all__ = [
    "BaseDocumentReader",
    "DocxReader",
    "HtmlReader",
    "MalformedDocumentError",
    "MarkdownReader",
    "ReaderError",
    "UnsupportedDocumentError",
    "create_synthetic_block",
    "create_synthetic_page",
    "get_reader_for_suffix",
    "layout_synthetic_blocks",
    "register_reader",
    "render_synthetic_pdf",
    "resolve_source_bytes",
    "supported_suffixes",
]
