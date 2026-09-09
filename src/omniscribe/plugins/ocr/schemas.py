"""OCR request/response schemas.

``OCRRequest`` parses the exact FormData field set the frontend's
``buildOcrFormData`` sends. Response models mirror the frontend types:
``AsyncSubmitResponse`` ↔ ``processOcrAsync`` return shape and
``JobStatusResponse`` ↔ ``OcrJobStatusResponse``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel, Field, field_validator, model_validator

from omniscribe.core.document import DenseMode
from omniscribe.utils.env import parse_bool

PipelineMode = Literal["hybrid", "grounded"]

_HttpJobStatus = Literal["pending", "processing", "complete", "error", "cancelled"]


def _is_bool_annotation(annotation: Any) -> bool:
    if annotation is bool:
        return True
    origin = get_origin(annotation)
    if origin is not None:
        return any(_is_bool_annotation(arg) for arg in get_args(annotation))
    return False


#: Frontend dense toggles ("on"/"off") are still accepted at the HTTP
#: edge for backwards compatibility, but the aliasing now lives in
#: the ``_parse_dense_mode`` validator below (one place, audit D14).
_DENSE_MODE_ALIASES: dict[str, DenseMode] = {
    "on": DenseMode.ALWAYS,
    "off": DenseMode.NEVER,
}

_VALID_PROCESSORS = {
    "reading_order",
    "quality_analysis",
    "structure_analysis",
    "section_analysis",
    "layout_enrichment",
    "table_extraction",
}


def _parse_bool(value: Any, default: bool = False) -> Any:
    return parse_bool(value, default=default)


def _parse_dense_mode(value: object) -> DenseMode:
    """Coerce an HTTP form-field string into a :class:`DenseMode`.

    Accepts the canonical ``"auto" | "always" | "never"`` spellings plus
    the legacy ``"on" / "off"`` aliases (front-end convention). Unknown
    values fall back to :attr:`DenseMode.AUTO` rather than failing the
    upload — the same fall-back the previous ``dense_mode_normalized``
    property implemented.
    """
    if isinstance(value, DenseMode):
        return value
    text = str(value).strip().lower()
    if text in _DENSE_MODE_ALIASES:
        return _DENSE_MODE_ALIASES[text]
    try:
        return DenseMode(text)
    except ValueError:
        return DenseMode.AUTO


class OCRRequest(BaseModel):
    """One OCR upload's options, parsed from multipart form fields."""

    model: str | None = None
    api_base: str | None = None
    api_key: str | None = None
    pipeline_mode: PipelineMode = "hybrid"
    dense_mode: DenseMode = DenseMode.AUTO
    spellcheck: str | None = None
    document_processors: list[str] = Field(default_factory=list)
    pages: str | None = None
    preprocess_pages: bool | None = None
    orientation_detection: bool = False
    deskew: bool = False
    denoise: bool = False
    normalize_contrast: bool = False
    crop_cleanup: bool = False
    progress_channel: str | None = None
    progress_token: str | None = None
    quality_loop_enabled: bool | None = None
    quality_target: float = Field(default=0.85, ge=0.5, le=1.0)
    quality_max_retries: int = Field(default=2, ge=0, le=5)

    @field_validator("document_processors", mode="before")
    @classmethod
    def _split_processors(cls, value: object) -> object:
        if isinstance(value, str):
            raw_items: list[object] = [value]
        elif isinstance(value, (list, tuple, set)):
            raw_items = list(value)
        else:
            return value

        names: list[str] = []
        for item in raw_items:
            if isinstance(item, str):
                for part in item.split(","):
                    cleaned = part.strip()
                    if cleaned:
                        names.append(cleaned)
            elif item is not None:
                cleaned = str(item).strip()
                if cleaned:
                    names.append(cleaned)

        unknown = sorted(set(names) - _VALID_PROCESSORS)
        if unknown:
            raise ValueError(f"unknown document processor(s): {', '.join(unknown)}")
        return names

    @model_validator(mode="before")
    @classmethod
    def _coerce_bool(cls, data: Any) -> Any:
        if isinstance(data, (dict, Mapping)):
            coerced = dict(data)
            for field_name, field_info in cls.model_fields.items():
                if _is_bool_annotation(field_info.annotation):
                    val = coerced.get(field_name)
                    if isinstance(val, str):
                        coerced[field_name] = _parse_bool(val)
            return coerced
        if isinstance(data, str):
            return _parse_bool(data)
        return data

    @field_validator("dense_mode", mode="before")
    @classmethod
    def _validate_dense_mode(cls, value: object) -> DenseMode:
        return _parse_dense_mode(value)

    @property
    def preprocessing_enabled(self) -> bool:
        """Master flag wins; otherwise any per-page toggle implies enabled."""
        if self.preprocess_pages is not None:
            return self.preprocess_pages
        return any(
            (
                self.orientation_detection,
                self.deskew,
                self.denoise,
                self.normalize_contrast,
                self.crop_cleanup,
            )
        )


class AsyncSubmitResponse(BaseModel):
    """Shape the frontend's ``processOcrAsync`` expects."""

    job_id: str
    status: Literal["pending", "processing", "complete", "error", "cancelled"] = (
        "pending"
    )
    status_url: str


class JobStatusResponse(BaseModel):
    """Mirrors the frontend ``OcrJobStatusResponse`` contract.

    Security note (2026-08-29 audit C-3 / H-3): the result ``token`` is
    intentionally **not** in this response. The unauthenticated
    ``GET /api/process/status/{job_id}`` + ``GET /api/jobs`` chain would
    otherwise let any caller fetch another user's OCR'd PDF without the
    constant-time gate at ``fetch_result``. The async client obtains the
    token from the ``job_completed`` SSE event payload (the out-of-band
    channel, parallel to the sync path's ``X-Text-Artifact-Token``
    response header). Only ``text_artifact_id`` is safe to expose — it's
    the opaque handle, not the secret.
    """

    job_id: str
    filename: str = ""
    status: Literal["pending", "processing", "complete", "error", "cancelled"]
    created_at: float
    started_at: float | None = None
    completed_at: float | None = None
    duration_s: float | None = None
    error: str | None = None
    text_artifact_id: str | None = None
    failed_pages: list[int] = Field(default_factory=list)


#: Mapping from internal JobRecord status strings onto the HTTP API status vocabulary.
_QUEUE_STATUS_TO_HTTP: dict[str, _HttpJobStatus] = {
    "queued": "pending",
    "running": "processing",
    "complete": "complete",
    "error": "error",
    "cancelled": "cancelled",
}


class JobListItemResponse(BaseModel):
    """Mirrors the frontend ``JobRecordResponse`` contract."""

    id: str
    filename: str = ""
    model: str = ""
    pipeline_mode: str = ""
    pages: str | None = None
    duration_s: float = 0.0
    timestamp: str = ""
    status: str
    failed_pages: list[int] = Field(default_factory=list)


class PreflightRequest(BaseModel):
    """Audit 6.3: optional overrides for the model pre-flight route.

    All fields default to the current ``/api/config`` value, so a bare
    ``GET /api/process/preflight`` preflights the active LLM endpoint.
    Pass overrides (e.g. for a multi-tenant install where the operator
    wants to probe a candidate model before swapping the live one).
    """

    api_base: str | None = None
    api_key: str | None = None
    model: str | None = None


class PreflightResponse(BaseModel):
    """Result of :meth:`OCRService.preflight_check`.

    ``loaded`` is the single source of truth for the UI badge; the
    ``requested_model`` / ``loaded_models`` pair lets the operator see
    exactly which models are present on the server.
    """

    loaded: bool
    requested_model: str
    api_base: str
    loaded_models: list[str] = Field(default_factory=list)
    detail: str = ""

    def __iter__(self) -> Any:
        return iter(
            (
                self.loaded,
                self.requested_model,
                self.api_base,
                self.loaded_models,
                self.detail,
            )
        )


__all__ = [
    "_QUEUE_STATUS_TO_HTTP",
    "AsyncSubmitResponse",
    "JobListItemResponse",
    "JobStatusResponse",
    "OCRRequest",
    "PipelineMode",
    "PreflightRequest",
    "PreflightResponse",
]
