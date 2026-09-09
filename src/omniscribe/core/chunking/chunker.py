"""Provenance-preserving, section-aware document chunker for RAG.

Splits a :class:`~omniscribe.core.block_tree.DocumentTree` into structured, size-bounded,
provenance-annotated :class:`DocumentChunk` instances suitable for embedding and vector
retrieval.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, cast

from omniscribe.core.block_tree import BlockNode, BlockType, DocumentTree, TableNode
from omniscribe.core.chunking.taxonomy import RAGElementCategory, map_element_type
from omniscribe.core.errors import ChunkingError
from omniscribe.core.writers.markdown import _clean_table_cell, _render_figure_node

_BBox = tuple[float, float, float, float]


@dataclass(slots=True)
class DocumentChunk:
    """A provenance-preserving text chunk with bounding boxes and metadata."""

    chunk_id: str
    element_type: str
    text: str
    section_path: list[str]
    page_span: tuple[int, int]
    bbox: list[_BBox]
    block_ids: list[str]
    trust_score: float | None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "element_type": self.element_type,
            "text": self.text,
            "section_path": list(self.section_path),
            "page_span": list(self.page_span),
            "bbox": [list(b) for b in self.bbox],
            "block_ids": list(self.block_ids),
            "trust_score": self.trust_score,
            "metadata": dict(self.metadata),
        }


def _new_chunk_id() -> str:
    return f"chk_{uuid.uuid4().hex[:12]}"


class SectionAwareChunker:
    """Chunks DocumentTree preserving section boundaries, atomic tables, and block provenance."""

    def __init__(
        self,
        max_chars: int = 1200,
        overlap_chars: int = 120,
        min_chars: int = 200,
    ) -> None:
        if max_chars <= 0:
            raise ChunkingError(
                f"max_chars must be a positive integer, got {max_chars}"
            )
        if overlap_chars < 0:
            raise ChunkingError(
                f"overlap_chars must be non-negative, got {overlap_chars}"
            )
        if min_chars <= 0:
            raise ChunkingError(
                f"min_chars must be a positive integer, got {min_chars}"
            )
        if overlap_chars >= max_chars:
            raise ChunkingError(
                f"overlap_chars ({overlap_chars}) must be strictly less than max_chars ({max_chars})"
            )
        if min_chars > max_chars:
            raise ChunkingError(
                f"min_chars ({min_chars}) must be less than or equal to max_chars ({max_chars})"
            )

        self.max_chars = max_chars
        self.overlap_chars = overlap_chars
        self.min_chars = min_chars

    def chunk(self, tree: DocumentTree) -> list[DocumentChunk]:
        """Produce a list of DocumentChunk instances from a DocumentTree."""
        elements = self._collect_elements(tree)
        chunks: list[DocumentChunk] = []

        current_section_path: list[str] = []
        accum_blocks: list[BlockNode] = []
        nl2 = chr(10) + chr(10)

        def flush_accum() -> None:
            nonlocal accum_blocks
            if not accum_blocks:
                return
            chunk = self._create_chunk_from_blocks(accum_blocks, current_section_path)
            chunks.append(chunk)
            accum_blocks = []

        for elem in elements:
            # 1. Section Header -> Section boundary
            is_sec_header = False
            if isinstance(elem, BlockNode):
                if elem.block_type == BlockType.SECTION_HEADER:
                    is_sec_header = True
                elif (
                    elem.section_hierarchy
                    and elem.section_hierarchy != current_section_path
                ):
                    flush_accum()
                    current_section_path = list(elem.section_hierarchy)

            if is_sec_header:
                header_node = cast(BlockNode, elem)
                flush_accum()
                level = header_node.level if header_node.level > 0 else 1
                if header_node.section_hierarchy:
                    current_section_path = list(header_node.section_hierarchy)
                else:
                    title_text = header_node.text.strip()
                    if level <= len(current_section_path):
                        current_section_path = [
                            *current_section_path[: level - 1],
                            title_text,
                        ]
                    else:
                        current_section_path = [*current_section_path, title_text]

                title_chunk = self._create_chunk_from_blocks(
                    [header_node], current_section_path, override_element_type="title"
                )
                chunks.append(title_chunk)
                continue

            # 2. Table handling (Atomic unless oversized)
            elem_type = (
                "table"
                if isinstance(elem, TableNode)
                else map_element_type(elem.block_type, elem.metadata)
            )

            if elem_type == "table":
                flush_accum()
                table_chunks = self._chunk_table(elem, current_section_path)
                chunks.extend(table_chunks)
                continue

            # 3. Figure handling (Atomic)
            if elem_type == "figure":
                flush_accum()
                fig_chunk = self._chunk_figure(
                    cast(BlockNode, elem), current_section_path
                )
                chunks.append(fig_chunk)
                continue

            # 4. Formula / Equation handling (Atomic)
            if elem_type == "formula":
                flush_accum()
                eq_chunk = self._chunk_equation(
                    cast(BlockNode, elem), current_section_path
                )
                chunks.append(eq_chunk)
                continue

            # 5. Narrative / List block handling
            block_node = cast(BlockNode, elem)
            block_text = block_node.text.strip()
            if not block_text:
                continue

            if not accum_blocks:
                if len(block_text) > self.max_chars:
                    chunks.append(
                        self._create_chunk_from_blocks(
                            [block_node], current_section_path
                        )
                    )
                else:
                    accum_blocks.append(block_node)
            else:
                current_text = nl2.join(b.text.strip() for b in accum_blocks)
                proposed_text = f"{current_text}{nl2}{block_text}"

                if len(proposed_text) <= self.max_chars:
                    accum_blocks.append(block_node)
                else:
                    prev_blocks = list(accum_blocks)
                    flush_accum()

                    overlap_seed: list[BlockNode] = []
                    if self.overlap_chars > 0 and len(prev_blocks) > 1:
                        for k in range(1, len(prev_blocks)):
                            suffix = prev_blocks[k:]
                            suffix_text = nl2.join(b.text.strip() for b in suffix)
                            if len(suffix_text) <= self.overlap_chars:
                                if (
                                    len(f"{suffix_text}{nl2}{block_text}")
                                    <= self.max_chars
                                ):
                                    overlap_seed = suffix
                                break

                    if overlap_seed:
                        accum_blocks = [*overlap_seed, block_node]
                    else:
                        if len(block_text) > self.max_chars:
                            chunks.append(
                                self._create_chunk_from_blocks(
                                    [block_node], current_section_path
                                )
                            )
                        else:
                            accum_blocks = [block_node]

        flush_accum()
        return chunks

    def _collect_elements(self, tree: DocumentTree) -> list[BlockNode | TableNode]:
        elements: list[BlockNode | TableNode] = []
        rendered_table_ids: set[str | int] = set()

        for page in tree.pages:
            for child in page.children:
                if isinstance(child, TableNode):
                    if child.block_id:
                        rendered_table_ids.add(child.block_id)
                    rendered_table_ids.add(id(child))
                    elements.append(child)
                else:
                    bt_val = (
                        child.block_type.value
                        if hasattr(child.block_type, "value")
                        else str(child.block_type or "")
                    )
                    if bt_val == "table":
                        if child.block_id:
                            rendered_table_ids.add(child.block_id)
                        rendered_table_ids.add(id(child))
                    elements.append(child)

        for table in tree.tables:
            t_id = getattr(table, "block_id", "")
            if (not t_id or t_id not in rendered_table_ids) and id(
                table
            ) not in rendered_table_ids:
                elements.append(table)
                if t_id:
                    rendered_table_ids.add(t_id)
                rendered_table_ids.add(id(table))

        return elements

    def _create_chunk_from_blocks(
        self,
        blocks: list[BlockNode],
        section_path: list[str],
        override_element_type: str | None = None,
    ) -> DocumentChunk:
        nl2 = chr(10) + chr(10)
        text = nl2.join(b.text.strip() for b in blocks if b.text.strip())
        if override_element_type:
            elem_type = override_element_type
        else:
            types = {map_element_type(b.block_type, b.metadata) for b in blocks}
            if len(types) == 1:
                elem_type = next(iter(types))
            elif types == {RAGElementCategory.LIST_ITEM.value}:
                elem_type = RAGElementCategory.LIST_ITEM.value
            else:
                elem_type = RAGElementCategory.NARRATIVE.value

        pages = [b.page_idx for b in blocks if getattr(b, "page_idx", None) is not None]
        page_span = (min(pages), max(pages)) if pages else (0, 0)
        bboxes = [b.bbox for b in blocks if hasattr(b, "bbox") and b.bbox]
        block_ids = [b.block_id for b in blocks if getattr(b, "block_id", None)]

        trust_scores: list[float] = [
            b.trust_score for b in blocks if b.trust_score is not None
        ]
        min_trust = min(trust_scores) if trust_scores else None

        return DocumentChunk(
            chunk_id=_new_chunk_id(),
            element_type=elem_type,
            text=text,
            section_path=list(section_path),
            page_span=page_span,
            bbox=bboxes,
            block_ids=block_ids,
            trust_score=min_trust,
            metadata={"block_count": len(blocks)},
        )

    def _chunk_table(
        self,
        table: TableNode | BlockNode | Any,
        section_path: list[str],
    ) -> list[DocumentChunk]:
        rendered_gfm = _render_table_gfm(table)
        page_idx = getattr(table, "page_idx", 0)
        bbox = [getattr(table, "bbox", (0.0, 0.0, 0.0, 0.0))]
        t_id = getattr(table, "block_id", _new_chunk_id())

        cells = getattr(table, "cells", None)
        all_cell_scores: list[float] = []
        if cells and isinstance(cells, (list, tuple)):
            for r in cells:
                if isinstance(r, (list, tuple)):
                    for c in r:
                        ts = getattr(c, "trust_score", None)
                        if ts is not None:
                            all_cell_scores.append(float(ts))
        else:
            t_score = getattr(table, "trust_score", None)
            if t_score is not None:
                all_cell_scores.append(float(t_score))

        min_trust = min(all_cell_scores) if all_cell_scores else None

        if len(rendered_gfm) <= self.max_chars or not cells:
            return [
                DocumentChunk(
                    chunk_id=_new_chunk_id(),
                    element_type="table",
                    text=rendered_gfm,
                    section_path=list(section_path),
                    page_span=(page_idx, page_idx),
                    bbox=bbox,
                    block_ids=[t_id],
                    trust_score=min_trust,
                    metadata={"table_id": t_id, "rows": len(cells) if cells else 0},
                )
            ]

        return self._split_table_by_rows(table, section_path, min_trust)

    def _split_table_by_rows(
        self,
        table: TableNode | BlockNode | Any,
        section_path: list[str],
        trust_score: float | None,
    ) -> list[DocumentChunk]:
        cells = getattr(table, "cells", [])
        page_idx = getattr(table, "page_idx", 0)
        bbox = [getattr(table, "bbox", (0.0, 0.0, 0.0, 0.0))]
        t_id = getattr(table, "block_id", _new_chunk_id())
        nl = chr(10)

        rows_data: list[list[str]] = []
        max_cols = 0
        for row in cells:
            if not isinstance(row, (list, tuple)):
                continue
            r_vals = [_clean_table_cell(getattr(c, "text", str(c))) for c in row]
            if len(r_vals) > max_cols:
                max_cols = len(r_vals)
            rows_data.append(r_vals)

        if not rows_data:
            return []

        for row in rows_data:
            while len(row) < max_cols:
                row.append("")

        header_row = rows_data[0]
        header_text = (
            "| "
            + " | ".join(header_row)
            + f" |{nl}| "
            + " | ".join(["---"] * max_cols)
            + " |"
        )
        data_rows = rows_data[1:]

        if not data_rows:
            return [
                DocumentChunk(
                    chunk_id=_new_chunk_id(),
                    element_type="table",
                    text=header_text,
                    section_path=list(section_path),
                    page_span=(page_idx, page_idx),
                    bbox=bbox,
                    block_ids=[t_id],
                    trust_score=trust_score,
                    metadata={"table_id": t_id, "split": True},
                )
            ]

        chunks: list[DocumentChunk] = []
        current_data_rows: list[str] = []

        def emit_subtable() -> None:
            if not current_data_rows:
                return
            subtable_text = header_text + nl + nl.join(current_data_rows)
            chunks.append(
                DocumentChunk(
                    chunk_id=_new_chunk_id(),
                    element_type="table",
                    text=subtable_text,
                    section_path=list(section_path),
                    page_span=(page_idx, page_idx),
                    bbox=bbox,
                    block_ids=[t_id],
                    trust_score=trust_score,
                    metadata={"table_id": t_id, "split": True},
                )
            )

        for row in data_rows:
            row_line = "| " + " | ".join(row) + " |"
            if not current_data_rows:
                current_data_rows.append(row_line)
            else:
                test_text = (
                    header_text + nl + nl.join(current_data_rows) + nl + row_line
                )
                if len(test_text) <= self.max_chars:
                    current_data_rows.append(row_line)
                else:
                    emit_subtable()
                    current_data_rows = [row_line]

        emit_subtable()
        return chunks

    def _chunk_figure(
        self,
        node: BlockNode,
        section_path: list[str],
    ) -> DocumentChunk:
        md_text = _render_figure_node(node)
        bbox = [node.bbox] if getattr(node, "bbox", None) else []
        page_idx = getattr(node, "page_idx", 0)
        return DocumentChunk(
            chunk_id=_new_chunk_id(),
            element_type="figure",
            text=md_text,
            section_path=list(section_path),
            page_span=(page_idx, page_idx),
            bbox=bbox,
            block_ids=[node.block_id],
            trust_score=getattr(node, "trust_score", None),
            metadata=dict(getattr(node, "metadata", {}) or {}),
        )

    def _chunk_equation(
        self,
        node: BlockNode,
        section_path: list[str],
    ) -> DocumentChunk:
        latex = getattr(node, "latex", None) or node.text
        md_text = f"$${latex.strip()}$$"
        bbox = [node.bbox] if getattr(node, "bbox", None) else []
        page_idx = getattr(node, "page_idx", 0)
        return DocumentChunk(
            chunk_id=_new_chunk_id(),
            element_type="formula",
            text=md_text,
            section_path=list(section_path),
            page_span=(page_idx, page_idx),
            bbox=bbox,
            block_ids=[node.block_id],
            trust_score=getattr(node, "trust_score", None),
            metadata=dict(getattr(node, "metadata", {}) or {}),
        )


def _render_table_gfm(table: TableNode | BlockNode | Any) -> str:
    from omniscribe.core.writers.markdown import _render_table

    return _render_table(table)


def chunk_tree(
    tree: DocumentTree,
    max_chars: int = 1200,
    overlap_chars: int = 120,
    min_chars: int = 200,
) -> list[DocumentChunk]:
    """Public functional entrypoint to chunk a DocumentTree with SectionAwareChunker."""
    chunker = SectionAwareChunker(
        max_chars=max_chars,
        overlap_chars=overlap_chars,
        min_chars=min_chars,
    )
    return chunker.chunk(tree)


__all__ = [
    "ChunkingError",
    "DocumentChunk",
    "SectionAwareChunker",
    "chunk_tree",
]
