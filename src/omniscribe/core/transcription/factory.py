"""Factory function to instantiate transcription engines based on configuration."""

from __future__ import annotations

from typing import Any, Protocol

import httpx

from omniscribe.core.transcription.api_engine import GenericAudioAPIEngine
from omniscribe.core.transcription.local_engine import WhisperLocalEngine
from omniscribe.core.transcription.types import TranscriptionResult


class TranscriptionEngineProtocol(Protocol):
    """Protocol for transcription engines."""

    async def transcribe(
        self,
        file_bytes: bytes,
        filename: str = "audio.wav",
        language: str | None = None,
        prompt: str | None = None,
        temperature: float = 0.0,
    ) -> TranscriptionResult: ...


def get_transcription_engine(
    engine_type: str = "api",
    model: str = "whisper-1",
    api_base: str = "https://api.openai.com/v1",
    api_key: str | None = None,
    resolved_ip: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    **kwargs: Any,
) -> TranscriptionEngineProtocol:
    """Instantiate a transcription engine instance.

    Supported engine_types:
      - "api", "whisper_api": Remote OpenAI-compatible audio API endpoint.
      - "local", "whisper_local": Offline local faster-whisper model.
      - "auto": Prefers local engine if faster-whisper is installed, falls back to API.
    """
    normalized_type = (engine_type or "api").lower().strip()

    if normalized_type in ("faster_whisper", "faster-whisper"):
        normalized_type = "whisper_local"

    if normalized_type not in ("local", "whisper_local", "api", "whisper_api", "auto"):
        raise ValueError(f"Unknown transcription engine: {engine_type!r}")

    if normalized_type == "auto":
        try:
            import faster_whisper  # noqa: F401

            normalized_type = "local"
        except ImportError:
            normalized_type = "api"

    if normalized_type in ("local", "whisper_local"):
        return WhisperLocalEngine(model_size_or_path=model or "base", **kwargs)

    return GenericAudioAPIEngine(
        model=model or "whisper-1",
        api_base=api_base,
        api_key=api_key,
        resolved_ip=resolved_ip,
        transport=transport,
        **kwargs,
    )


create_transcription_engine = get_transcription_engine

__all__ = [
    "TranscriptionEngineProtocol",
    "create_transcription_engine",
    "get_transcription_engine",
]
