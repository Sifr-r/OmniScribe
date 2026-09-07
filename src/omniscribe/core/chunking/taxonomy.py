"""Standardized RAG element category taxonomy and mapping.

Maps OCR and layout detection block types (:class:`~omniscribe.core.block_tree.BlockType`,
:class:`~omniscribe.core.document.DocumentBlock.kind`, layout roles) to standard RAG
element categories:
- ``title``
- ``narrative``
- ``table``
- ``figure``
- ``formula``
- ``list_item``
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from omniscribe.core.block_tree import BlockType


class RAGElementCategory(StrEnum):
    """Standardized RAG element categories."""

    TITLE = "title"
    NARRATIVE = "narrative"
    TABLE = "table"
    FIGURE = "figure"
    FORMULA = "formula"
    LIST_ITEM = "list_item"


_TITLE_TYPES = frozenset({
    "title",
    "section_header",
    "section_heading",
    "header",
    "heading",
    "sub_header",
    "sub_heading",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "doc_title",
})

_TABLE_TYPES = frozenset({
    "table",
    "table_cell",
    "table_row",
    "grid",
    "dataframe",
})

_FIGURE_TYPES = frozenset({
    "figure",
    "image",
    "picture",
    "photo",
    "diagram",
    "chart",
    "plot",
    "illustration",
    "graphic",
})

_FORMULA_TYPES = frozenset({
    "formula",
    "equation",
    "math",
    "latex",
    "mathml",
})

_LIST_TYPES = frozenset({
    "list",
    "list_item",
    "bullet",
    "bullet_point",
    "ordered_list",
    "unordered_list",
})

_NARRATIVE_TYPES = frozenset({
    "narrative",
    "paragraph",
    "text",
    "body",
    "content",
    "caption",
    "footnote",
    "code",
    "key_value",
    "page_header",
    "page_footer",
    "page_number",
})


def map_element_type(
    block_type: BlockType | str,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Map an OCR/layout block type and optional metadata to a RAG element category.

    Deterministic: always returns one of:
    ``"title"``, ``"narrative"``, ``"table"``, ``"figure"``, ``"formula"``, ``"list_item"``.
    """
    # 1. Metadata role / label check first
    if metadata:
        if metadata.get("is_title") or metadata.get("is_heading"):
            return RAGElementCategory.TITLE.value

        role = (
            metadata.get("layout_role")
            or metadata.get("role")
            or metadata.get("label")
            or metadata.get("element_type")
        )
        if isinstance(role, str) and role.strip():
            role_norm = role.strip().lower()
            if role_norm in _TITLE_TYPES:
                return RAGElementCategory.TITLE.value
            if role_norm in _TABLE_TYPES:
                return RAGElementCategory.TABLE.value
            if role_norm in _FIGURE_TYPES:
                return RAGElementCategory.FIGURE.value
            if role_norm in _FORMULA_TYPES:
                return RAGElementCategory.FORMULA.value
            if role_norm in _LIST_TYPES:
                return RAGElementCategory.LIST_ITEM.value
            if role_norm in _NARRATIVE_TYPES:
                return RAGElementCategory.NARRATIVE.value

    # 2. BlockType enum match
    if isinstance(block_type, BlockType):
        if block_type == BlockType.SECTION_HEADER:
            return RAGElementCategory.TITLE.value
        if block_type == BlockType.LIST_ITEM:
            return RAGElementCategory.LIST_ITEM.value
        if block_type == BlockType.TABLE:
            return RAGElementCategory.TABLE.value
        if block_type == BlockType.FIGURE:
            return RAGElementCategory.FIGURE.value
        if block_type == BlockType.EQUATION:
            return RAGElementCategory.FORMULA.value
        return RAGElementCategory.NARRATIVE.value

    # 3. String normalization match
    raw_str = (
        block_type.value
        if hasattr(block_type, "value")
        else str(block_type or "")
    )
    norm = raw_str.strip().lower()

    if norm in _TITLE_TYPES:
        return RAGElementCategory.TITLE.value
    if norm in _TABLE_TYPES:
        return RAGElementCategory.TABLE.value
    if norm in _FIGURE_TYPES:
        return RAGElementCategory.FIGURE.value
    if norm in _FORMULA_TYPES:
        return RAGElementCategory.FORMULA.value
    if norm in _LIST_TYPES:
        return RAGElementCategory.LIST_ITEM.value
    if norm in _NARRATIVE_TYPES:
        return RAGElementCategory.NARRATIVE.value

    # Substring heuristics for compound names like "table_caption", "figure_box", "title_main"
    if "table" in norm:
        return RAGElementCategory.TABLE.value
    if "figure" in norm or "image" in norm:
        return RAGElementCategory.FIGURE.value
    if "equation" in norm or "formula" in norm or "math" in norm:
        return RAGElementCategory.FORMULA.value
    if "title" in norm or "header" in norm or "heading" in norm:
        return RAGElementCategory.TITLE.value
    if "list" in norm or "bullet" in norm:
        return RAGElementCategory.LIST_ITEM.value

    return RAGElementCategory.NARRATIVE.value


__all__ = [
    "RAGElementCategory",
    "map_element_type",
]
