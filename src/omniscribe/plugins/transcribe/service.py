"""Transcribe service: validation → engine → artifacts → response dict.

Verbatim re-home of the pre-harness `api/services/transcription.py`
(`44ef123^`) semantics onto the harness ArtifactStore. The old service
stored page-dict artifacts (`{0: [lines]}`) through a typed artifact
service; the harness store takes opaque bytes, so the same page-dict is
serialized as JSON using the text-artifact convention
`{"<page_index>": "<lines joined by \n>"}`.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Mapping
from typing import Any, Protocol

from omniscribe.config import RuntimeSettings
from omniscribe.core.transcription import (
    AudioValidationError,
    TranscriptionError,
    get_transcription_engine,
    validate_audio_input,
)
from omniscribe.plugins.artifacts import ArtifactStore
from omniscribe.plugins.errors import PluginError

# Audit 9.13: only the actually-used names are imported; the previous
# wholesale ``noqa: F401`` was hiding dead names. ``TRANSCRIPTION_FALLBACK_MODELS``,
# ``extract_model_ids_from_response``, and ``mask_api_key`` live in ``config_store``
# and are imported directly by the test suite / external callers as needed.
from omniscribe.plugins.transcribe.config_store import (
    TranscriptionConfigStore,
    discover_transcription_models,
)
from omniscribe.plugins.transcribe.schemas import (
    TranscribeRequest,
    TranscriptionConfigResponse,
    TranscriptionConfigUpdate,
    TranscriptionJobResponse,
)
from omniscribe.plugins.transcribe.schemas import (
    unpack_transcribe_options as unpack_transcribe_options,
)
from omniscribe.utils.security import check_ssrf_target_sync

_LOGGER = logging.getLogger("omniscribe.plugins.transcribe")

DEFAULT_TRANSCRIPTION_API_BASE = "https://api.openai.com/v1"
DEFAULT_TRANSCRIPTION_MODEL = "whisper-1"
DEFAULT_TRANSCRIPTION_ENGINE = "api"


class TranscribeError(PluginError):
    """User-facing transcribe error (envelope wire fields on ``PluginError``)."""


def _resolve_optional_str(
    request_value: Any,
    config: Mapping[str, Any],
    config_key: str,
    *,
    default: str = "",
) -> str | None:
    """Audit 9.11: flatten the form-or-config-or-default-or-None funnel.

    Picks the first non-empty value among (request, config, default),
    coerces to str, and returns None for the empty string so downstream
    engines can treat the unset case uniformly.
    """
    raw = request_value or config.get(config_key, default)
    return str(raw) if raw else None


def resolve_engine_settings(
    request: TranscribeRequest, config: Mapping[str, Any]
) -> dict[str, Any]:
    """Form → config store → default, per field (old fallback chain)."""
    return {
        "model": str(request.model or config.get("transcription_model", "whisper-1")),
        "engine": str(request.engine or config.get("transcription_engine", "api")),
        "api_base": str(
            request.api_base
            or config.get("transcription_api_base", DEFAULT_TRANSCRIPTION_API_BASE)
        ),
        "api_key": _resolve_optional_str(
            request.api_key, config, "transcription_api_key"
        ),
        "language": _resolve_optional_str(
            request.language, config, "transcription_language"
        ),
        "prompt": _resolve_optional_str(request.prompt, config, "transcription_prompt"),
        "temperature": request.temperature,
    }


async def transcribe(
    request: TranscribeRequest,
    *,
    file_bytes: bytes,
    filename: str,
    content_type: str | None,
    store: ArtifactStore,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    """Sync transcription; verbatim old response shape."""
    # SSRF-check the caller-supplied override only (translate precedent):
    # config-store/default values are trusted operator config.
    if request.api_base and request.api_base.strip():
        check = check_ssrf_target_sync(request.api_base.strip())
        if not check.allowed:
            raise TranscribeError(
                403,
                "ssrf_blocked",
                f"URL targets a blocked address: {check.reason}",
            )

    try:
        validate_audio_input(
            filename=filename,
            content_type=content_type,
            file_size=len(file_bytes),
        )
    except AudioValidationError as exc:
        raise TranscribeError(400, "bad_request", exc.message) from exc

    resolved = resolve_engine_settings(request, config)
    engine = get_transcription_engine(
        engine_type=resolved["engine"],
        model=resolved["model"],
        api_base=resolved["api_base"],
        api_key=resolved["api_key"],
    )
    try:
        result = await engine.transcribe(
            file_bytes=file_bytes,
            filename=filename,
            language=resolved["language"],
            prompt=resolved["prompt"],
            temperature=resolved["temperature"],
        )
    except TranscriptionError as exc:
        raise TranscribeError(503, "backend_unavailable", exc.message) from exc
    except Exception as exc:
        _LOGGER.exception("Voice transcription request failed")
        raise TranscribeError(
            502, "ai_error", "The AI service request failed."
        ) from exc

    job_id = f"job-{uuid.uuid4().hex[:12]}"
    lines = [s.text for s in result.segments] if result.segments else [result.text]
    text_handle = await store.put(
        json.dumps({"0": "\n".join(lines)}).encode("utf-8"),
        content_type="application/json",
        owner_job_id=job_id,
    )
    doc_result = result.to_document_result()
    page_metadata = doc_result.pages[0].metadata if doc_result.pages else {}
    meta_handle = await store.put(
        json.dumps({"0": json.dumps(page_metadata)}).encode("utf-8"),
        content_type="application/json",
        owner_job_id=job_id,
    )
    return {
        "text": result.text,
        "language": result.language,
        "duration": result.duration,
        "text_artifact_id": text_handle.id,
        "text_artifact_token": text_handle.token,
        "metadata_artifact_id": meta_handle.id,
        "metadata_artifact_token": meta_handle.token,
        "job_id": job_id,
        "segments": [
            {
                "id": s.id,
                "start": s.start,
                "end": s.end,
                "text": s.text,
                "confidence": s.confidence,
            }
            for s in result.segments
        ],
    }


class TranscriptionService(Protocol):
    async def transcribe(
        self,
        request: TranscribeRequest,
        *,
        file_bytes: bytes,
        filename: str,
        content_type: str | None,
    ) -> dict[str, Any] | TranscriptionJobResponse: ...

    def get_config(self) -> TranscriptionConfigResponse: ...

    def update_config(
        self, body: TranscriptionConfigUpdate
    ) -> TranscriptionConfigResponse: ...

    async def discover_models(self) -> list[str]: ...


class TranscriptionServiceImpl:
    """Harness transcription service over the ArtifactStore."""

    def __init__(
        self,
        settings: RuntimeSettings,
        store: ArtifactStore,
    ) -> None:
        self._settings = settings
        self._store = store
        self._config = TranscriptionConfigStore(
            auth_token=settings.transcription_auth_token
        )

    async def transcribe(
        self,
        request: TranscribeRequest,
        *,
        file_bytes: bytes,
        filename: str,
        content_type: str | None,
    ) -> dict[str, Any]:
        return await transcribe(
            request,
            file_bytes=file_bytes,
            filename=filename,
            content_type=content_type,
            store=self._store,
            config=self._config.get(),
        )

    def get_config(self) -> TranscriptionConfigResponse:
        return self._config.read()

    def update_config(
        self, body: TranscriptionConfigUpdate
    ) -> TranscriptionConfigResponse:
        updates: dict[str, Any] = {}
        if body.api_base is not None:
            check = check_ssrf_target_sync(body.api_base)
            if not check.allowed:
                raise TranscribeError(
                    403,
                    "ssrf_blocked",
                    f"URL targets a blocked address: {check.reason}",
                )
            updates["transcription_api_base"] = body.api_base
        if body.transcription_api_key is not None:
            updates["transcription_api_key"] = body.transcription_api_key
        elif body.api_key is not None:
            updates["transcription_api_key"] = body.api_key
        if body.model is not None:
            updates["transcription_model"] = body.model
        if body.engine is not None:
            updates["transcription_engine"] = body.engine.value
        if body.language is not None:
            updates["transcription_language"] = body.language
        if body.prompt is not None:
            updates["transcription_prompt"] = body.prompt
        if body.temperature is not None:
            updates["transcription_temperature"] = body.temperature
        if updates:
            self._config.update(updates)
        return self._config.read()

    async def discover_models(self) -> list[str]:
        config = self._config.get()
        api_base = str(
            config.get("transcription_api_base", DEFAULT_TRANSCRIPTION_API_BASE)
        )
        api_key = str(config.get("transcription_api_key", "")) or None
        return await discover_transcription_models(api_base, api_key)
