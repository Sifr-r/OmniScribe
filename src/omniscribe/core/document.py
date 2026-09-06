"""Local document intelligence intermediate representation.

The public pipeline still writes legacy ``{page: [(bbox, text)]}`` structures,
but document processors need a richer handoff object for ordering, quality
metadata, and future extraction/export features. Keep bboxes normalized in
``0..1`` here; PDF coordinate conversion belongs at the output writer boundary.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from omniscribe.core.block_tree import DocumentTree


class PipelineMode(StrEnum):
    HYBRID = "hybrid"
    GROUNDED = "grounded"


class DenseMode(StrEnum):
    AUTO = "auto"
    ALWAYS = "always"
    NEVER = "never"


class SpellcheckMode(StrEnum):
    NONE = "none"
    AR = "ar"
    EN_US = "en-US"
    DE = "de"
    ES = "es"
    FR = "fr"


BBox = tuple[float, float, float, float]


@dataclass(slots=True)
class DocumentBlock:
    bbox: BBox
    text: str
    kind: str = "text"
    confidence: float | None = None
    source_processor: str = "ocr"
    reading_order: int | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    # OCR quality trust-layer outputs. ``None`` when the trust layer is
    # disabled (Phase 1 default). When populated, ``trust_score`` is in
    # ``[0, 1]`` and ``trust_flags`` is a sorted tuple of string flags.
    trust_score: float | None = None
    trust_flags: tuple[str, ...] | None = None


@dataclass(slots=True)
class DocumentPage:
    page_index: int
    blocks: list[DocumentBlock] = field(default_factory=list)
    width: int | None = None
    height: int | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return "\n".join(block.text for block in self.blocks if block.text.strip())


@dataclass(slots=True)
class DocumentResult:
    """Canonical in-memory handoff for optional document processors.

    Pages are zero-indexed to match the existing OCR pipeline dictionaries.
    Blocks are intentionally mutable: processors can reorder blocks, annotate
    metadata, or rewrite text before ``to_pages_data`` feeds the PDF writer.
    """

    pages: list[DocumentPage]
    source_path: str | None = None
    tree: DocumentTree | None = None

    @classmethod
    def from_pages_data(
        cls,
        pages_data: Mapping[int, Sequence[tuple[Sequence[float], str]]],
        *,
        source_path: str | None = None,
        source_processor: str = "ocr",
        confidence_fn: Callable[[str], float] | None = None,
    ) -> DocumentResult:
        """Build a result from legacy ``{page: [(bbox, text)]}`` payloads.

        The conversion validates every bbox up front so downstream processors can
        assume normalized ``[x0, y0, x1, y1]`` geometry. Invalid or pixel-space
        boxes raise ``ValueError`` instead of being embedded silently.

        ``confidence_fn`` (e.g. the OCR workflow's ``_estimate_confidence``)
        populates ``DocumentBlock.confidence`` at build time so the trust
        layer and repair triggers consume real signal instead of a default 0.0.
        """

        pages: list[DocumentPage] = []
        for page_index in sorted(pages_data):
            blocks = [
                DocumentBlock(
                    bbox=_normalize_bbox(bbox),
                    text=text,
                    source_processor=source_processor,
                    reading_order=reading_order,
                    confidence=(
                        confidence_fn(text)
                        if confidence_fn is not None and text.strip()
                        else None
                    ),
                )
                for reading_order, (bbox, text) in enumerate(pages_data[page_index])
            ]
            pages.append(DocumentPage(page_index=page_index, blocks=blocks))
        return cls(pages=pages, source_path=source_path)

    def text(self) -> str:
        return "\n\n".join(page.text for page in self.pages if page.text.strip())

    def to_pages_data(self) -> dict[int, list[tuple[BBox, str]]]:
        """Convert back to legacy ``{page: [(bbox, text)]}`` for output writers.

        When every block on a page carries a ``reading_order`` annotation
        (e.g. set by :class:`ReadingOrderProcessor`), blocks are emitted in
        that order so the PDF text layer respects processor-assigned reading
        order even if the block list was not physically sorted.
        """
        result: dict[int, list[tuple[BBox, str]]] = {}
        for page in self.pages:
            blocks = page.blocks
            if blocks and all(b.reading_order is not None for b in blocks):
                blocks = sorted(blocks, key=lambda b: b.reading_order or 0)
            result[page.page_index] = [(block.bbox, block.text) for block in blocks]
        return result


def _normalize_bbox(bbox: Sequence[float]) -> BBox:
    if len(bbox) != 4:
        raise ValueError(f"Expected bbox with 4 values, got {len(bbox)}")
    x0, y0, x1, y1 = (float(value) for value in bbox)
    if not (
        0.0 <= x0 <= 1.0 and 0.0 <= y0 <= 1.0 and 0.0 <= x1 <= 1.0 and 0.0 <= y1 <= 1.0
    ):
        raise ValueError(f"Expected normalized bbox in 0..1, got {(x0, y0, x1, y1)!r}")
    if x1 <= x0:
        x1 = min(1.0, max(x0 + 1e-4, 1e-4))
        if x1 <= x0:
            x0 = max(0.0, x1 - 1e-4)
    if y1 <= y0:
        y1 = min(1.0, max(y0 + 1e-4, 1e-4))
        if y1 <= y0:
            y0 = max(0.0, y1 - 1e-4)
    return (x0, y0, x1, y1)
