"""Request schemas for the documents plugin (extraction + export routes).

Field constraints reproduce the pre-harness contract (commit `44ef123^`,
``api/schemas/requests.py``) so the existing Flutter client keeps working
without changes.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field

from omniscribe.plugins._schemas import TrimmedModel


class ExtractionTemplate(StrEnum):
    INVOICE = "invoice"
    RESUME = "resume"
    ACADEMIC = "academic"
    TABLE = "table"
    TABLE_EXTRACTION = "table_extraction"
    CUSTOM = "custom"


class DocumentExportFormat(StrEnum):
    JSON = "json"
    MARKDOWN = "markdown"
    TEXT = "text"
    DOCLING = "docling"
    MINERU = "mineru"


class _TrimmedModel(TrimmedModel):
    """Documents-plugin alias of the shared plugin schema base."""


class ExtractionRequest(_TrimmedModel):
    text: str = ""
    template: ExtractionTemplate = ExtractionTemplate.INVOICE
    custom_prompt: str = Field(default="", max_length=4000)
    api_base: str | None = None
    api_key: str | None = None
    model: str | None = None


class ExportHtmlRequest(_TrimmedModel):
    text_artifact_id: str = Field(min_length=32, max_length=32)
    text_artifact_token: str = Field(min_length=32, max_length=256)


class ExportBlockTreeRequest(ExportHtmlRequest):
    metadata_artifact_id: str | None = Field(default=None, min_length=32, max_length=32)
    metadata_artifact_token: str | None = Field(
        default=None, min_length=32, max_length=256
    )


class DocumentExportRequest(ExportBlockTreeRequest):
    export_format: DocumentExportFormat = DocumentExportFormat.JSON


class ExportDocxRequest(_TrimmedModel):
    text: str = ""


class ExportMarkdownRequest(ExportBlockTreeRequest):
    """Request for markdown export from text/metadata artifacts."""


class ExportChunksRequest(ExportBlockTreeRequest):
    """Request for section-aware chunks export with tuning knobs."""

    max_chars: int = Field(default=1200, gt=0, le=100000)
    overlap_chars: int = Field(default=120, ge=0, le=10000)
    min_chars: int = Field(default=200, gt=0, le=100000)


class DocumentChunkPayload(_TrimmedModel):
    """Wire representation of a single DocumentChunk."""

    chunk_id: str
    element_type: str
    text: str
    section_path: list[str] = Field(default_factory=list)
    page_span: tuple[int, int]
    bbox: list[tuple[float, float, float, float]] = Field(default_factory=list)
    block_ids: list[str] = Field(default_factory=list)
    trust_score: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExportChunksResponse(_TrimmedModel):
    """Response payload containing extracted chunks and summary metadata."""

    chunks: list[DocumentChunkPayload]
    total_chunks: int
