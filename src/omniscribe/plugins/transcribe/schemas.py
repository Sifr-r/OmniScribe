"""Schemas for the transcribe plugin (client-frozen contract).

`TranscribeRequest` is parsed from multipart form fields by the route
(manual `request.form()` parse + `model_validate`, mirroring the OCR
plugin's `OCRRequest` pattern — form values are strings coerced by
before-validators).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _validate_optional_string(value: Any) -> Any:
    if value is None:
        return value
    if not isinstance(value, str):
        raise ValueError("must be a string")
    return value.strip()


def _coerce_float(value: Any) -> Any:
    if isinstance(value, str):
        return float(value)
    return value


class TranscriptionEngineType(StrEnum):
    API = "api"
    WHISPER_API = "whisper_api"
    LOCAL = "local"
    WHISPER_LOCAL = "whisper_local"
    FASTER_WHISPER = "faster_whisper"
    FASTER_WHISPER_DASH = "faster-whisper"
    AUTO = "auto"


ALLOWED_TRANSCRIBE_ENGINES = frozenset(
    member.value for member in TranscriptionEngineType
)


class TranscribeRequest(BaseModel):
    """One transcription upload's options, parsed from form fields."""

    model_config = ConfigDict(extra="forbid")

    model: str | None = None
    engine: str | None = None
    api_base: str | None = None
    api_key: str | None = None
    language: str | None = None
    prompt: str | None = None
    temperature: float = 0.0
    channel_id: str | None = None

    @field_validator(
        "model",
        "engine",
        "api_base",
        "api_key",
        "language",
        "prompt",
        "channel_id",
        mode="before",
    )
    @classmethod
    def _strip(cls, value: Any) -> Any:
        return _validate_optional_string(value)

    @field_validator("engine")
    @classmethod
    def _validate_engine(cls, value: str | None) -> str | None:
        if value is not None and value not in ALLOWED_TRANSCRIBE_ENGINES:
            raise ValueError(
                f"engine must be one of {sorted(ALLOWED_TRANSCRIBE_ENGINES)}, got {value!r}"
            )
        return value

    @field_validator("temperature", mode="before")
    @classmethod
    def _temperature(cls, value: Any) -> Any:
        return _coerce_float(value)


def unpack_transcribe_options(request: TranscribeRequest) -> dict[str, Any]:
    """Unpack a TranscribeRequest into keyword arguments for engine/service calls."""
    return {
        "model": request.model,
        "engine": request.engine,
        "api_base": request.api_base,
        "api_key": request.api_key,
        "language": request.language,
        "prompt": request.prompt,
        "temperature": request.temperature,
        "channel_id": request.channel_id,
    }


class TranscriptionConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_base: str | None = None
    api_key: str | None = None
    transcription_api_key: str | None = None
    model: str | None = None
    engine: TranscriptionEngineType | None = None
    language: str | None = None
    prompt: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)

    @field_validator(
        "api_base",
        "api_key",
        "transcription_api_key",
        "model",
        "language",
        "prompt",
        mode="before",
    )
    @classmethod
    def _validate_optional_strings(cls, value: Any) -> Any:
        return _validate_optional_string(value)


class TranscriptionConfigResponse(BaseModel):
    """Transcription-namespace runtime configuration."""

    transcription_api_base: str
    transcription_api_key: str
    transcription_model: str
    transcription_engine: str
    transcription_auth_token: str | None = None
    language: str | None = None
    prompt: str | None = None
    temperature: float = 0.0


class TranscriptionJobResponse(BaseModel):
    """Response returned upon transcription execution."""

    text: str
    language: str | None = None
    duration: float | None = None
    text_artifact_id: str | None = None
    text_artifact_token: str | None = None
    metadata_artifact_id: str | None = None
    metadata_artifact_token: str | None = None
    job_id: str | None = None
    segments: list[dict[str, Any]] = []


UpdateTranscriptionConfigRequest = TranscriptionConfigUpdate

__all__ = [
    "ALLOWED_TRANSCRIBE_ENGINES",
    "TranscribeRequest",
    "TranscriptionConfigResponse",
    "TranscriptionConfigUpdate",
    "TranscriptionEngineType",
    "TranscriptionJobResponse",
    "UpdateTranscriptionConfigRequest",
    "unpack_transcribe_options",
]
