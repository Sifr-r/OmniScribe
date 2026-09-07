"""Reader registry and factory for digital document formats.

Maps file extensions (.docx, .html, .htm, .md, .markdown) to their
corresponding :class:`BaseDocumentReader` implementations.
"""

from __future__ import annotations

from omniscribe.core.readers.base import BaseDocumentReader
from omniscribe.core.readers.docx_reader import DocxReader
from omniscribe.core.readers.html_reader import HtmlReader
from omniscribe.core.readers.markdown_reader import MarkdownReader

__all__ = [
    "get_reader_for_suffix",
    "register_reader",
    "supported_suffixes",
]

_REGISTRY: dict[str, BaseDocumentReader] = {}


def _init_registry() -> dict[str, BaseDocumentReader]:
    docx = DocxReader()
    html = HtmlReader()
    md = MarkdownReader()
    return {
        ".docx": docx,
        ".html": html,
        ".htm": html,
        ".md": md,
        ".markdown": md,
    }


def get_reader_for_suffix(suffix: str) -> BaseDocumentReader | None:
    """Return a BaseDocumentReader for the given file extension, or None if unsupported.

    Case-insensitive, normalizes leading dots.
    """
    global _REGISTRY
    if not _REGISTRY:
        _REGISTRY = _init_registry()
    if not suffix:
        return None
    s = suffix.strip().lower()
    if not s.startswith("."):
        s = f".{s}"
    return _REGISTRY.get(s)


def register_reader(suffix: str, reader: BaseDocumentReader) -> None:
    """Register a custom or specialized reader for an extension suffix."""
    global _REGISTRY
    if not _REGISTRY:
        _REGISTRY = _init_registry()
    s = suffix.strip().lower()
    if not s.startswith("."):
        s = f".{s}"
    _REGISTRY[s] = reader


def supported_suffixes() -> tuple[str, ...]:
    """Return a sorted tuple of all currently supported digital file extensions."""
    global _REGISTRY
    if not _REGISTRY:
        _REGISTRY = _init_registry()
    return tuple(sorted(_REGISTRY.keys()))
