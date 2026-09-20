"""Base document export base class and shared types.

Defines the typed boundary for document exporters transforming
:class:`~omniscribe.core.document.DocumentResult` and
:class:`~omniscribe.core.block_tree.DocumentTree` into diverse export
formats (e.g. DOCX, HTML, Markdown, JSON).
"""

from __future__ import annotations

import abc
import io
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from omniscribe.core.block_tree import DocumentTree
    from omniscribe.core.document import DocumentResult


class BaseDocumentExporter(abc.ABC):
    """Abstract base class providing common dispatch for document exporters."""

    @abc.abstractmethod
    def export_tree(
        self, tree: DocumentTree, **kwargs: Any
    ) -> bytes | str | io.BytesIO:
        """Export a structured DocumentTree into the target format."""
        raise NotImplementedError

    def export_document(
        self, document: DocumentResult, **kwargs: Any
    ) -> bytes | str | io.BytesIO:
        """Export a DocumentResult into the target format via DocumentTree conversion."""
        from omniscribe.core.block_tree import from_document_result

        tree = from_document_result(document)
        return self.export_tree(tree, **kwargs)

    def export(
        self, source: DocumentTree | DocumentResult, **kwargs: Any
    ) -> bytes | str | io.BytesIO:
        """Dispatch export to tree or document handler based on source type."""
        from omniscribe.core.block_tree import DocumentTree
        from omniscribe.core.document import DocumentResult

        if isinstance(source, DocumentTree):
            return self.export_tree(source, **kwargs)
        if isinstance(source, DocumentResult):
            return self.export_document(source, **kwargs)
        raise TypeError(
            f"Expected DocumentTree or DocumentResult, got {type(source).__name__}"
        )


__all__ = [
    "BaseDocumentExporter",
]
