"""OCR request/response schemas.

``OCRRequest`` parses the exact FormData field set the frontend's
``buildOcrFormData`` sends. Response models mirror the frontend types:
``AsyncSubmitResponse`` ↔ ``processOcrAsync`` return shape and
``JobStatusResponse`` ↔ ``OcrJobStatusResponse``.

The optional ``quality_options`` field carries the advertised OCR
trust / quality-routing controls as one typed, ``extra="forbid"`` model.
The route parses multipart form values as plain strings, so the field
accepts either a JSON object string (FormData) or an already-decoded
mapping (the Redis queue envelope round-trip).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from omniscribe.core.document import DenseMode
from omniscribe.core.pdf.page_range import parse_page_range
from omniscribe.utils.env import DISABLE_STRINGS, ENABLE_STRINGS, parse_bool

PipelineMode = Literal["hybrid", "grounded"]

_HttpJobStatus = Literal["pending", "processing", "complete", "error", "cancelled"]


def _is_bool_annotation(annotation: Any) -> bool:
    if annotation is bool:
        return True
    origin = get_origin(annotation)
    if origin is not None:
        return any(_is_bool_annotation(arg) for arg in get_args(annotation))
    return False


def _coerce_bool_form_fields(
    model: type[BaseModel],
    data: Any,
    parse: Callable[[str, str], bool],
) -> Any:
    """Coerce the string-valued boolean fields of ``model`` in ``data``.

    Shared by :class:`OCRRequest` (lenient ``parse_bool``, unchanged HTTP
    behaviour) and the nested quality models (strict ``parse``, which
    rejects unknown spellings instead of falling back to a default).
    """
    if not isinstance(data, (dict, Mapping)):
        return data
    coerced = dict(data)
    for field_name, field_info in model.model_fields.items():
        if _is_bool_annotation(field_info.annotation):
            val = coerced.get(field_name)
            if isinstance(val, str):
                coerced[field_name] = parse(field_name, val)
    return coerced


def _parse_strict_bool(field_name: str, value: str) -> bool:
    """Form-field bool parser that fails loudly on an unknown spelling.

    :func:`omniscribe.utils.env.parse_bool` falls back to ``False`` for
    anything it does not recognise, which is correct for environment
    variables but wrong for a request: a typo in a quality control must
    produce a validation error, never a silently disabled sub-module.
    """
    text = value.strip().lower()
    if text in ENABLE_STRINGS:
        return True
    if text in DISABLE_STRINGS:
        return False
    raise ValueError(
        f"{field_name} must be a boolean "
        f"({', '.join(sorted(ENABLE_STRINGS))} or "
        f"{', '.join(sorted(DISABLE_STRINGS))}); got {value!r}"
    )


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
    # Registered in the local processor registry (see
    # ``omniscribe.core.processors.base.build_document_processors``); the
    # request allowlist was the only place it stayed unreachable.
    "table_fallback",
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


class OCRTrustOptions(BaseModel):
    """Per-request trust-layer sub-module controls.

    Field-for-field mirror of the implemented knobs on
    :class:`omniscribe.core.ocr_quality.OCrQualitySettings`; the bridge
    forwards this model straight into
    :func:`omniscribe.core.ocr_quality.build_trust_orchestrator`. Every
    switch defaults to ``False`` — the orchestrator factory returns
    ``None`` (trust layer off) until at least one sub-module is enabled,
    so an upload without ``quality_options`` keeps byte-identical output.

    ``extra="forbid"`` mirrors the core settings model: a mistyped
    control name is a validation error, not a silently dropped switch.
    """

    model_config = ConfigDict(extra="forbid")

    watermark_enabled: bool = False
    watermark_aggressiveness: float = Field(default=0.5, ge=0.0, le=1.0)

    script_detect_enabled: bool = False

    hallucination_enabled: bool = False
    hallucination_cross_check: bool = False  # second VLM call, off by default
    hallucination_cross_check_threshold: float = Field(default=0.4, ge=0.0, le=1.0)
    hallucination_repetition_window: int = Field(default=6, ge=2, le=64)
    hallucination_length_plausibility_min: float = Field(default=0.0001, ge=0.0, le=1.0)

    calibration_enabled: bool = False

    # Auto-flag a block in the UI when trust_score < this threshold.
    trust_flag_threshold: float = Field(default=0.5, ge=0.0, le=1.0)

    @model_validator(mode="before")
    @classmethod
    def _coerce_nested_form_bools(cls, data: Any) -> Any:
        return _coerce_bool_form_fields(cls, data, _parse_strict_bool)


class OCRQualityRoutingOptions(BaseModel):
    """Per-request quality-routing switch.

    Mirrors :class:`omniscribe.core.ocr_quality.routing.QualityRoutingOptions`
    (the only implemented field). When enabled, the hybrid engine records
    the per-page routing decisions produced by ``QualityRoutingPolicy``.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False

    @model_validator(mode="before")
    @classmethod
    def _coerce_nested_form_bools(cls, data: Any) -> Any:
        return _coerce_bool_form_fields(cls, data, _parse_strict_bool)


class OCRQualityOptions(BaseModel):
    """The ``quality_options`` request payload.

    ``trust`` gates the OCR quality trust layer (watermark / script /
    hallucination / calibration) and ``routing`` gates quality-based
    routing decisions. Both default to ``None``, which the bridge reads
    as "layer off" — identical to omitting ``quality_options`` entirely.
    """

    model_config = ConfigDict(extra="forbid")

    trust: OCRTrustOptions | None = None
    routing: OCRQualityRoutingOptions | None = None


def _decode_quality_options(value: object) -> object:
    """Normalize the FormData-shaped ``quality_options`` value.

    Multipart form values arrive as strings, so a client sends one JSON
    object string (``'{"trust": {"calibration_enabled": true}}'``); the
    Redis queue envelope round-trip passes an already-decoded mapping.
    Anything else raises a ``ValueError`` naming ``quality_options`` so
    the route's 422 path reports the offending field instead of dropping
    the control.
    """
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return None
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "quality_options must be a JSON object, for example "
            '\'{"trust": {"calibration_enabled": true}}\'; '
            f"got invalid JSON ({exc})"
        ) from exc
    if not isinstance(decoded, dict):
        raise ValueError(
            f"quality_options must be a JSON object; got {type(decoded).__name__}"
        )
    return decoded


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
    dpi: int | None = Field(default=None, ge=36, le=600)
    concurrency: int | None = Field(default=None, ge=1, le=32)
    dense_threshold: int | None = Field(default=None, ge=1, le=10000)
    max_image_dim: int | None = Field(default=None, ge=128, le=8192)
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
    #: Trust-layer / quality-routing controls. ``None`` (the default,
    #: including when the field is absent from the form) leaves both off.
    quality_options: OCRQualityOptions | None = None

    @field_validator("pages")
    @classmethod
    def _validate_pages(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        if parse_page_range(value) is None:
            raise ValueError("pages must be a positive page range, for example 1-3,5")
        return value.strip()

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
        coerced = _coerce_bool_form_fields(
            cls, data, lambda _field_name, value: _parse_bool(value)
        )
        if isinstance(data, str):
            return _parse_bool(data)
        return coerced

    @field_validator("dense_mode", mode="before")
    @classmethod
    def _validate_dense_mode(cls, value: object) -> DenseMode:
        return _parse_dense_mode(value)

    @field_validator("quality_options", mode="before")
    @classmethod
    def _validate_quality_options(cls, value: object) -> object:
        return _decode_quality_options(value)

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
    result_token: str | None = None


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
    # Opaque handle to the rich document artifact, exposed for the same
    # reason as ``text_artifact_id``: clients need it to request a
    # structure-preserving export. The token stays out of this response
    # for the reason given in the class docstring.
    document_artifact_id: str | None = None
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
    "OCRQualityOptions",
    "OCRQualityRoutingOptions",
    "OCRRequest",
    "OCRTrustOptions",
    "PipelineMode",
    "PreflightRequest",
    "PreflightResponse",
]
