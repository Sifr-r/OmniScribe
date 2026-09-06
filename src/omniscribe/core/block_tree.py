"""Block-tree document IR (Docling/Marker style).

This is a richer alternative to :mod:`omniscribe.core.document` that carries
the structural information (headings, tables, figures, sections, spans) needed
for structured export (DOCX, HTML, block-tree JSON) and structure-preserving
translation.

The legacy :class:`~omniscribe.core.document.DocumentResult` is kept for
backward compatibility; new exporters and translation paths consume
:class:`DocumentTree`.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from omniscribe.core.document import DocumentResult


class BlockType(StrEnum):
    TEXT = "text"
    PARAGRAPH = "paragraph"
    SECTION_HEADER = "section_header"
    LIST_ITEM = "list_item"
    TABLE = "table"
    FIGURE = "figure"
    CAPTION = "caption"
    EQUATION = "equation"
    PAGE_HEADER = "page_header"
    PAGE_FOOTER = "page_footer"
    PAGE_NUMBER = "page_number"
    FOOTNOTE = "footnote"
    CODE = "code"
    KEY_VALUE = "key_value"


_BBox = tuple[float, float, float, float]


def _as_bbox(values: Sequence[float]) -> _BBox:
    """Convert a 4-element sequence to a fixed-length ``_BBox`` tuple.

    ``tuple(generator_expr)`` infers as ``tuple[float, ...]`` (variable-length),
    which mypy treats as incompatible with the fixed-length ``_BBox`` alias.
    This helper does an explicit unpack + float coercion so the call site gets
    the right tuple type and we fail fast on malformed input.
    """
    if len(values) != 4:
        raise ValueError(f"Expected bbox with 4 values, got {len(values)}")
    b = list(values)
    return (float(b[0]), float(b[1]), float(b[2]), float(b[3]))


def _new_block_id() -> str:
    """Generate a stable, sortable block id."""
    return uuid.uuid4().hex[:16]


@dataclass(slots=True)
class Span:
    """An inline run inside a block (bold, italic, font)."""

    text: str
    bold: bool = False
    italic: bool = False
    code: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "bold": self.bold,
            "italic": self.italic,
            "code": self.code,
        }

    # Phase D (review M4) — `from_dict` mirrors `to_dict`. Keep the
    # two in sync when adding fields. Used by the JSON-based tree
    # artifact (see `api/services/tree_artifact.py`) — replaces the
    # previous pickle round-trip.
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Span:
        return cls(
            text=data["text"],
            bold=bool(data.get("bold", False)),
            italic=bool(data.get("italic", False)),
            code=bool(data.get("code", False)),
        )


@dataclass(slots=True)
class BlockNode:
    """A single block in the document tree.

    The ``block_id`` is stable across the pipeline and is what the UI binds to
    when rendering the bbox overlay. ``children`` are populated for tables
    (cell nodes), lists (item nodes), and nested sections.
    """

    block_type: BlockType
    bbox: _BBox
    text: str
    page_idx: int
    block_id: str = field(default_factory=_new_block_id)
    confidence: float | None = None
    # OCR quality trust-layer outputs (see DocumentBlock). ``None`` when the
    # trust layer is disabled; serialized so clients can flag blocks for review.
    trust_score: float | None = None
    trust_flags: tuple[str, ...] | None = None
    children: list[BlockNode] = field(default_factory=list)
    parent_id: str | None = None
    level: int = 0  # heading level (1..6) for SECTION_HEADER; list depth for LIST_ITEM
    section_hierarchy: list[str] = field(default_factory=list)
    spans: list[Span] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    # In-memory cross-reference to the parent FigureNode. Lives here
    # only so `core/writers/html.py` (which iterates `PageTree.children`,
    # not `DocumentTree.figures`) can inline the image without a second
    # pass. Not serialized in `to_dict`; the canonical bytes are
    # carried by `FigureNode.image_bytes` and round-trip through its
    # base64-encoded `image_bytes_b64` key.
    image_bytes: bytes | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "block_id": self.block_id,
            "block_type": self.block_type.value,
            "bbox": list(self.bbox),
            "text": self.text,
            "page_idx": self.page_idx,
            "confidence": self.confidence,
            "trust_score": self.trust_score,
            "trust_flags": list(self.trust_flags) if self.trust_flags else None,
            "level": self.level,
            "section_hierarchy": list(self.section_hierarchy),
            "spans": [s.to_dict() for s in self.spans],
            "metadata": dict(self.metadata),
        }
        if self.children:
            d["children"] = [c.to_dict() for c in self.children]
        return d

    # Phase D (review M4) — see Span.from_dict for the contract.
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BlockNode:
        return cls(
            block_type=BlockType(data["block_type"]),
            bbox=_as_bbox(data["bbox"]),
            text=data["text"],
            page_idx=int(data["page_idx"]),
            block_id=data.get("block_id") or _new_block_id(),
            confidence=(
                float(data["confidence"])
                if data.get("confidence") is not None
                else None
            ),
            trust_score=(
                float(data["trust_score"])
                if data.get("trust_score") is not None
                else None
            ),
            trust_flags=(
                tuple(str(f) for f in data["trust_flags"])
                if data.get("trust_flags")
                else None
            ),
            children=[BlockNode.from_dict(c) for c in data.get("children", [])],
            parent_id=data.get("parent_id"),
            level=int(data.get("level", 0)),
            section_hierarchy=list(data.get("section_hierarchy", [])),
            spans=[Span.from_dict(s) for s in data.get("spans", [])],
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(slots=True)
class PageTree:
    page_idx: int
    width: int | None = None
    height: int | None = None
    children: list[BlockNode | TableNode] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_idx": self.page_idx,
            "width": self.width,
            "height": self.height,
            "children": [c.to_dict() for c in self.children],
            "metadata": dict(self.metadata),
        }

    # Phase D (review M4) — see Span.from_dict for the contract.
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PageTree:
        children: list[BlockNode | TableNode] = []
        for c in data.get("children", []):
            if c.get("block_type") == "table" and "rows" in c:
                children.append(TableNode.from_dict(c))
            else:
                children.append(BlockNode.from_dict(c))
        return cls(
            page_idx=int(data["page_idx"]),
            width=(int(data["width"]) if data.get("width") is not None else None),
            height=(int(data["height"]) if data.get("height") is not None else None),
            children=children,
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(slots=True)
class Section:
    """A cross-page section entry in the document outline."""

    title: str
    level: int
    start_page: int
    children: list[Section] = field(default_factory=list)
    block_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "level": self.level,
            "start_page": self.start_page,
            "block_id": self.block_id,
            "children": [c.to_dict() for c in self.children],
        }

    # Phase D (review M4) — see Span.from_dict for the contract.
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Section:
        return cls(
            title=data["title"],
            level=int(data["level"]),
            start_page=int(data["start_page"]),
            children=[Section.from_dict(c) for c in data.get("children", [])],
            block_id=data.get("block_id"),
        )


@dataclass(slots=True)
class TableNode:
    """A detected table on a page.

    A TableNode is the structural parent of its cell BlockNodes; per-cell
    text and per-cell block_type live on the child BlockNode, not on the
    TableNode. ``rows`` and ``cols`` describe the grid shape; ``cells``
    is ``rows`` lists of length ``cols``. ``bbox`` is the union of all
    cell bboxes (used by the PDF embed step when the table has no
    drawn border).
    """

    rows: int
    cols: int
    page_idx: int
    bbox: _BBox
    cells: list[list[BlockNode]] = field(default_factory=list)
    block_id: str = field(default_factory=_new_block_id)
    block_type: BlockType = BlockType.TABLE

    def to_dict(self) -> dict[str, Any]:
        return {
            "block_id": self.block_id,
            "block_type": "table",
            "rows": self.rows,
            "cols": self.cols,
            "page_idx": self.page_idx,
            "bbox": list(self.bbox),
            "cells": [[c.to_dict() for c in row] for row in self.cells],
        }

    # Phase D (review M4) — see Span.from_dict for the contract.
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TableNode:
        return cls(
            rows=int(data["rows"]),
            cols=int(data["cols"]),
            page_idx=int(data["page_idx"]),
            bbox=_as_bbox(data["bbox"]),
            cells=[
                [BlockNode.from_dict(c) for c in row] for row in data.get("cells", [])
            ],
            block_id=data.get("block_id") or _new_block_id(),
        )


@dataclass(slots=True)
class FigureNode:
    page_idx: int
    bbox: _BBox
    image_bytes: bytes | None = None
    caption: str = ""
    block_id: str = field(default_factory=_new_block_id)

    def to_dict(self) -> dict[str, Any]:
        # Phase D (review M4) — `image_bytes` is base64-encoded for the
        # JSON artifact so the payload stays pure UTF-8. `from_dict`
        # decodes back to bytes. The round-trip preserves the bytes
        # exactly (modulo base64 size expansion, which only matters
        # for very large images).
        import base64

        d: dict[str, Any] = {
            "block_id": self.block_id,
            "block_type": "figure",
            "page_idx": self.page_idx,
            "bbox": list(self.bbox),
            "has_image": self.image_bytes is not None,
            "caption": self.caption,
        }
        if self.image_bytes is not None:
            d["image_bytes_b64"] = base64.b64encode(self.image_bytes).decode("ascii")
        return d

    # Phase D (review M4) — see Span.from_dict for the contract.
    # `image_bytes` is binary; the JSON artifact encodes it as a
    # base64 string under the "image_bytes_b64" key (rather than
    # trying to embed raw bytes). The reader decodes back to bytes
    # so the in-memory FigureNode is indistinguishable from one
    # constructed in-process.
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FigureNode:
        import base64

        image_bytes: bytes | None = None
        if data.get("image_bytes_b64"):
            image_bytes = base64.b64decode(data["image_bytes_b64"])
        elif data.get("image_bytes") is not None:
            # Backward-compat: if the JSON still has raw bytes (older
            # artifact or hand-edited), accept them as-is.
            image_bytes = bytes(data["image_bytes"])
        return cls(
            page_idx=int(data["page_idx"]),
            bbox=_as_bbox(data["bbox"]),
            image_bytes=image_bytes,
            caption=str(data.get("caption", "")),
            block_id=data.get("block_id") or _new_block_id(),
        )


@dataclass(slots=True)
class EquationNode:
    page_idx: int
    bbox: _BBox
    latex: str
    block_id: str = field(default_factory=_new_block_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "block_id": self.block_id,
            "block_type": "equation",
            "page_idx": self.page_idx,
            "bbox": list(self.bbox),
            "latex": self.latex,
        }

    # Phase D (review M4) — see Span.from_dict for the contract.
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EquationNode:
        return cls(
            page_idx=int(data["page_idx"]),
            bbox=_as_bbox(data["bbox"]),
            latex=str(data.get("latex", "")),
            block_id=data.get("block_id") or _new_block_id(),
        )


@dataclass(slots=True)
class DocumentTree:
    """The canonical rich IR for structured export and structure-preserving translation."""

    pages: list[PageTree] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    tables: list[TableNode] = field(default_factory=list)
    figures: list[FigureNode] = field(default_factory=list)
    equations: list[EquationNode] = field(default_factory=list)
    source_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pages": [p.to_dict() for p in self.pages],
            "sections": [s.to_dict() for s in self.sections],
            "tables": [t.to_dict() for t in self.tables],
            "figures": [f.to_dict() for f in self.figures],
            "equations": [e.to_dict() for e in self.equations],
            "source_path": self.source_path,
            "metadata": dict(self.metadata),
        }

    # Phase D (review M4) — see Span.from_dict for the contract. The
    # JSON artifact uses `DocumentTree.from_dict(tree.to_dict())` as
    # the round-trip; pickle is no longer in this path.
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DocumentTree:
        return cls(
            pages=[PageTree.from_dict(p) for p in data.get("pages", [])],
            sections=[Section.from_dict(s) for s in data.get("sections", [])],
            tables=[TableNode.from_dict(t) for t in data.get("tables", [])],
            figures=[FigureNode.from_dict(f) for f in data.get("figures", [])],
            equations=[EquationNode.from_dict(e) for e in data.get("equations", [])],
            source_path=data.get("source_path"),
            metadata=dict(data.get("metadata", {})),
        )

    def iter_text_blocks(self) -> list[BlockNode]:
        """Yield every leaf text block in reading order."""
        out: list[BlockNode] = []
        for page in self.pages:
            for child in page.children:
                out.extend(_walk_text(child))
        return out


def _walk_text(node: BlockNode | TableNode) -> list[BlockNode]:
    if isinstance(node, TableNode) or node.block_type == BlockType.TABLE:
        # table cells are walked separately
        return []
    if node.children and node.block_type not in (BlockType.LIST_ITEM,):
        # nested blocks (e.g. a section header followed by paragraphs) — walk children
        out: list[BlockNode] = []
        for c in node.children:
            out.extend(_walk_text(c))
        if out:
            return out
    return [node]


def from_pages_data(
    pages_data: dict[int, Sequence[tuple[Sequence[float], str]]],
    *,
    source_path: str | None = None,
) -> DocumentTree:
    """Best-effort conversion from the legacy {page: [(bbox, text)]} shape.

    Each line becomes a :class:`BlockNode` of type :attr:`BlockType.PARAGRAPH`
    (or :attr:`BlockType.SECTION_HEADER` for short, uppercase-heavy lines).
    """
    tree = DocumentTree(source_path=source_path)
    for page_idx in sorted(pages_data):
        page = PageTree(page_idx=page_idx)
        for bbox, text in pages_data[page_idx]:
            text = (text or "").strip()
            if not text:
                continue
            kind = _classify_simple(text)
            page.children.append(
                BlockNode(
                    block_type=kind,
                    bbox=_as_bbox(bbox),
                    text=text,
                    page_idx=page_idx,
                    level=1 if kind == BlockType.SECTION_HEADER else 0,
                )
            )
        tree.pages.append(page)
    return tree


def from_document_result(document: DocumentResult) -> DocumentTree:
    """Initialize a DocumentTree from a DocumentResult."""
    tree = DocumentTree(
        source_path=document.source_path,
        metadata=dict(document.metadata) if hasattr(document, "metadata") else {},
    )
    for page in document.pages:
        tree_page = PageTree(
            page_idx=page.page_index,
            width=page.width,
            height=page.height,
            metadata=dict(page.metadata),
        )
        for block in page.blocks:
            # Prefer 'label' from metadata (Grounded path), then 'kind'
            # Phase E (review E.6) — `DocumentBlock.metadata` is typed
            # `dict[str, object]`, so the `.get(...)` returns `object`
            # unless we narrow here. The cast is safe because every
            # producer of these keys (the grounded backend, the
            # structure / layout / quality processors) puts a string
            # under "label" and bytes-or-None under "image_bytes".
            kind_str = str(
                block.metadata.get("label")
                or (block.kind if isinstance(block.kind, str) else "paragraph")
            )

            if kind_str in ("image", "figure"):
                # `bytes | None` cast mirrors the FigureNode dataclass
                # shape; the producer is the GroundedEngine which
                # either populates this from a real crop or leaves it
                # as None.
                fig_node = FigureNode(
                    page_idx=page.page_index,
                    bbox=block.bbox,
                    image_bytes=cast("bytes | None", block.metadata.get("image_bytes")),
                    caption=block.text,
                )
                tree.figures.append(fig_node)
                # Keep a BlockNode in the page children too, just mark
                # it as figure. `image_bytes` is a real field on
                # BlockNode now (added so core/writers/html.py can
                # inline the figure from a page-children walk without a
                # second pass over DocumentTree.figures); we mirror the
                # FigureNode's bytes here.
                node = BlockNode(
                    block_type=BlockType.FIGURE,
                    bbox=block.bbox,
                    text=block.text,
                    page_idx=page.page_index,
                    confidence=block.confidence,
                    trust_score=block.trust_score,
                    trust_flags=block.trust_flags,
                    metadata=dict(block.metadata),
                    image_bytes=fig_node.image_bytes,
                )
                tree_page.children.append(node)
                continue

            try:
                block_type = BlockType(kind_str)
            except ValueError:
                block_type = BlockType.PARAGRAPH

            node = BlockNode(
                block_type=block_type,
                bbox=block.bbox,
                text=block.text,
                page_idx=page.page_index,
                confidence=block.confidence,
                trust_score=block.trust_score,
                trust_flags=block.trust_flags,
                metadata=dict(block.metadata),
            )
            tree_page.children.append(node)
        tree.pages.append(tree_page)
    return tree


def _classify_simple(text: str) -> BlockType:
    """Cheap classifier used when richer processor data is unavailable."""
    if len(text) <= 80:
        upper = sum(1 for c in text if c.isalpha() and c.isupper())
        alpha = sum(1 for c in text if c.isalpha())
        if alpha > 0 and upper / alpha >= 0.65:
            return BlockType.SECTION_HEADER
    return BlockType.PARAGRAPH
