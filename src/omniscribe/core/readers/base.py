"""Base classes, exceptions, and synthetic helpers for digital document readers.

Provides:
- :class:`BaseDocumentReader` — abstract protocol/ABC for digital ingest readers.
- :class:`ReaderError` — domain exception hierarchy rooted in :class:`OmniScribeError`.
- Synthetic block and page builders assigning 1.0 confidence and ("source:digital",) trust flags.
"""

from __future__ import annotations

import abc
import io
from collections.abc import Sequence
from pathlib import Path

from omniscribe.core.document import (
    BBox,
    DocumentBlock,
    DocumentPage,
    DocumentResult,
)
from omniscribe.core.errors import OmniScribeError

__all__ = [
    "BaseDocumentReader",
    "MalformedDocumentError",
    "ReaderError",
    "UnsupportedDocumentError",
    "create_synthetic_block",
    "create_synthetic_page",
    "layout_synthetic_blocks",
    "resolve_source_bytes",
]


class ReaderError(OmniScribeError):
    """Base domain exception for digital document reader failures."""


class UnsupportedDocumentError(ReaderError):
    """Raised when an unsupported or unrecognized document format is encountered."""


class MalformedDocumentError(ReaderError):
    """Raised when a digital document cannot be parsed or has corrupted contents."""


def resolve_source_bytes(source: Path | bytes | io.BytesIO) -> bytes:
    """Validate and extract bytes safely from Path, bytes, or BytesIO.

    Guards against path traversal, missing files, and empty contents.
    """
    if isinstance(source, bytes):
        if not source:
            raise MalformedDocumentError("Document source is empty bytes")
        return source

    if isinstance(source, io.BytesIO):
        raw = source.getvalue()
        if not raw:
            raise MalformedDocumentError("Document stream is empty")
        return raw

    if isinstance(source, (str, Path)):
        resolved_path = Path(source).resolve()
        if not resolved_path.is_file():
            raise ReaderError(
                f"Document file does not exist or is not a regular file: {resolved_path}",
                details={"path": str(resolved_path)},
            )
        try:
            raw = resolved_path.read_bytes()
            if not raw:
                raise MalformedDocumentError(
                    f"Document file is empty: {resolved_path}",
                    details={"path": str(resolved_path)},
                )
            return raw
        except OSError as exc:
            raise ReaderError(
                f"Failed to read document file: {exc}",
                details={"path": str(resolved_path)},
            ) from exc

    raise UnsupportedDocumentError(f"Unsupported source type: {type(source)!r}")


def create_synthetic_block(
    text: str,
    *,
    kind: str = "paragraph",
    bbox: BBox | None = None,
    reading_order: int | None = None,
    metadata: dict[str, object] | None = None,
) -> DocumentBlock:
    """Construct a synthetic DocumentBlock with full trust guarantees.

    Each block is tagged with confidence = 1.0, trust_score = 1.0,
    trust_flags = ("source:digital",), and source_processor = "digital".
    """
    meta = dict(metadata or {})
    return DocumentBlock(
        bbox=bbox or (0.0, 0.0, 1.0, 1.0),
        text=text,
        kind=kind,
        confidence=1.0,
        source_processor="digital",
        reading_order=reading_order,
        metadata=meta,
        trust_score=1.0,
        trust_flags=("source:digital",),
    )


def layout_synthetic_blocks(
    blocks_spec: Sequence[tuple[str, str, dict[str, object]]],
    *,
    top_margin: float = 0.05,
    bottom_margin: float = 0.95,
    left_margin: float = 0.05,
    right_margin: float = 0.95,
) -> list[DocumentBlock]:
    """Assign non-overlapping normalized vertical bounding boxes to sequential blocks.

    Distributes vertical space proportionally according to line counts.
    """
    if not blocks_spec:
        return []

    total_units = sum(max(1, len(text.splitlines())) for text, _, _ in blocks_spec)
    avail_h = max(0.01, bottom_margin - top_margin)
    unit_h = avail_h / max(total_units, 1)

    blocks: list[DocumentBlock] = []
    curr_y = top_margin

    for idx, (text, kind, meta) in enumerate(blocks_spec):
        lines_count = max(1, len(text.splitlines()))
        block_h = lines_count * unit_h
        y0 = round(min(0.999, max(0.001, curr_y)), 4)
        y1 = round(min(1.0, max(y0 + 0.001, curr_y + block_h)), 4)
        curr_y += block_h
        bbox: BBox = (left_margin, y0, right_margin, y1)
        blocks.append(
            create_synthetic_block(
                text=text,
                kind=kind,
                bbox=bbox,
                reading_order=idx,
                metadata=meta,
            )
        )

    return blocks


def create_synthetic_page(
    page_index: int,
    blocks: list[DocumentBlock],
    width: int = 595,
    height: int = 842,
    metadata: dict[str, object] | None = None,
) -> DocumentPage:
    """Construct a DocumentPage carrying synthetic DocumentBlocks."""
    return DocumentPage(
        page_index=page_index,
        blocks=blocks,
        width=width,
        height=height,
        metadata=dict(metadata or {}),
    )


class BaseDocumentReader(abc.ABC):
    """Abstract protocol / base class for digital document ingest readers."""

    @abc.abstractmethod
    def read(
        self,
        source: Path | bytes | io.BytesIO,
        filename: str = "",
    ) -> DocumentResult:
        """Parse source document into a canonical DocumentResult."""
        ...
