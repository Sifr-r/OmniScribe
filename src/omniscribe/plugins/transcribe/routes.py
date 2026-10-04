"""HTTP routes for the transcribe plugin (client-frozen contract).

Routes whose handler may answer with the error envelope declare a union
return type; FastAPI cannot build a response model from such unions, so
those decorators pass ``response_model=None``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from omniscribe.plugins._http import envelope
from omniscribe.plugins.transcribe.schemas import (
    TranscribeRequest,
    TranscriptionConfigResponse,
    TranscriptionConfigUpdate,
    TranscriptionJobResponse,
)
from omniscribe.plugins.transcribe.service import TranscriptionService


def build_transcribe_router(service: TranscriptionService) -> APIRouter:
    """Build and return the FastAPI router for the transcribe plugin.

    Exposes the client-frozen contract documented in this module's
    docstring. The four endpoints cover:

    * ``POST /api/transcribe`` — multipart audio upload → transcription.
    * ``GET /api/config/transcription`` — current transcription config.
    * ``POST /api/config/transcription`` — update transcription config.
    * ``GET /api/models/transcription`` — list available transcription
      models discovered from the configured backend (local Whisper,
      hosted API, etc.).
    """
    router = APIRouter(tags=["transcribe"])

    @router.post("/api/transcribe", response_model=None)
    async def transcribe_audio(request: Request) -> JSONResponse:
        """Transcribe an uploaded audio file (multipart/form-data).

        Reads the ``file`` part and any string-typed form fields, then
        validates the field-set against :class:`TranscribeRequest` so
        the client gets the standard 422 envelope for unknown option
        keys. Filename and content-type are forwarded to the service
        so the local backend can pick the right decoder.
        """
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            return envelope(400, "bad_request", "missing 'file' field")
        file_bytes: bytes = await upload.read()
        fields: dict[str, Any] = {
            key: value
            for key, value in form.items()
            if key != "file" and isinstance(value, str)
        }
        try:
            options = TranscribeRequest.model_validate(fields)
        except ValidationError as exc:
            return JSONResponse(
                status_code=422,
                content={"detail": jsonable_encoder(exc.errors(include_url=False))},
            )
        filename = str(getattr(upload, "filename", "") or "") or "audio.wav"
        content_type = getattr(upload, "content_type", "") or None
        result = await service.transcribe(
            options,
            file_bytes=file_bytes,
            filename=filename,
            content_type=content_type,
        )
        try:
            payload = TranscriptionJobResponse.model_validate(result)
            return JSONResponse(
                content=jsonable_encoder(payload.model_dump(exclude_unset=True))
            )
        except (ValidationError, ValueError, TypeError):
            return envelope(
                500,
                "internal_server_error",
                "Transcription service returned an invalid response.",
            )

    @router.get("/api/config/transcription", response_model=None)
    async def get_transcription_config() -> TranscriptionConfigResponse:
        """Return the current transcription configuration.

        The config controls the active model, language hint, and any
        backend-specific options (e.g. Whisper ``beam_size``). The
        Flutter client reads this on screen mount so the UI matches
        the persisted state.
        """
        return service.get_config()

    @router.post("/api/config/transcription", response_model=None)
    async def update_transcription_config(
        body: TranscriptionConfigUpdate,
    ) -> TranscriptionConfigResponse:
        """Persist an updated transcription configuration.

        Validation failures (``TranscribeError``) propagate so the
        framework exception handler maps them to the standard error
        envelope; successful updates echo the new config back to the
        caller.
        """
        return service.update_config(body)

    @router.get("/api/models/transcription", response_model=None)
    async def get_transcription_models() -> dict[str, Any]:
        """List transcription models available on the configured backend.

        Used by the Flutter client's model picker. Discovery is
        delegated to the service so the route stays backend-agnostic
        (works for both local Whisper.cpp and remote API providers).
        """
        return {"models": await service.discover_models()}

    return router
