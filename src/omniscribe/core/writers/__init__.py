"""Document export writers (DOCX, HTML, Markdown, block-tree JSON)."""

from omniscribe.core.writers.markdown import (
    MarkdownExporter,  # Deprecated: alias for MarkdownWriter
    MarkdownWriter,
    render_markdown,
)

__all__ = [
    "MarkdownExporter",
    "MarkdownWriter",
    "render_markdown",
]
