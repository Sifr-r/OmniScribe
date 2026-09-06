"""Generic OpenAI-compatible API engine for voice transcription."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from omniscribe.core.ocr.resilience import is_transient_error
from omniscribe.core.transcription.types import (
    TranscriptionError,
    TranscriptionResult,
    TranscriptionSegment,
    logprob_to_confidence,
)

logger = logging.getLogger(__name__)


class GenericAudioAPIEngine:
    """Transcription engine that calls an OpenAI-compatible `/v1/audio/transcriptions` API.

    Works with OpenAI API, LM Studio audio models, vLLM, Whisper-WebUI, or any remote model.
    Accepts arbitrary model strings, custom API keys, and custom endpoints.
    """

    def __init__(
        self,
        model: str = "whisper-1",
        api_base: str = "https://api.openai.com/v1",
        api_key: str | None = None,
        timeout: float = 300.0,
    ) -> None:
        self.model = model.strip() if model else "whisper-1"
        self.api_base = (api_base or "https://api.openai.com/v1").rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    async def transcribe(
        self,
        file_bytes: bytes,
        filename: str = "audio.wav",
        language: str | None = None,
        prompt: str | None = None,
        temperature: float = 0.0,
    ) -> TranscriptionResult:
        """Send audio bytes to `/v1/audio/transcriptions` with retries on transient errors."""
        url = f"{self.api_base}/audio/transcriptions"
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        data: dict[str, Any] = {
            "model": self.model,
            "response_format": "verbose_json",
            "temperature": str(temperature),
        }
        if language:
            data["language"] = language
        if prompt:
            data["prompt"] = prompt

        files = {"file": (filename, file_bytes)}

        max_attempts = 3
        last_exception: Exception | None = None

        # One client across all attempts — connection pooling and no
        # per-attempt socket churn.
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(1, max_attempts + 1):
                try:
                    response = await client.post(
                        url, headers=headers, data=data, files=files
                    )
                except TranscriptionError:
                    raise
                except Exception as exc:
                    last_exception = exc
                    if is_transient_error(exc) and attempt < max_attempts:
                        logger.warning(
                            "Transient error in audio transcription (attempt %d/%d): %s",
                            attempt,
                            max_attempts,
                            exc,
                        )
                        await asyncio.sleep(self._retry_delay_s(attempt, None))
                        continue
                    break

                if response.status_code == 200:
                    return self._parse_verbose_json(response.json())
                if response.status_code in (401, 403):
                    raise TranscriptionError(
                        "Invalid API key or unauthorized access.",
                        status_code=response.status_code,
                    )
                if response.status_code == 404:
                    raise TranscriptionError(
                        f"Model or endpoint not found: {self.model}", status_code=404
                    )

                retry_after = response.headers.get("Retry-After")
                last_exception = TranscriptionError(
                    f"Audio API transcription failed with status {response.status_code}: {response.text}",
                    status_code=response.status_code,
                )
                retryable_status = response.status_code in (429, 500, 502, 503, 504)
                if attempt < max_attempts and retryable_status:
                    await asyncio.sleep(
                        self._retry_delay_s(attempt, retry_after)
                    )
                    continue
                break

        raise TranscriptionError(
            f"Audio transcription API request failed: {last_exception}", status_code=502
        ) from last_exception

    @staticmethod
    def _retry_delay_s(attempt: int, retry_after: str | None) -> float:
        """Exponential backoff (1/2/4s base, 16s cap); Retry-After wins."""
        if retry_after:
            try:
                return min(float(retry_after), 60.0)
            except ValueError:
                pass
        return min(1.0 * (2 ** (attempt - 1)), 16.0)

    def _parse_verbose_json(self, payload: dict[str, Any]) -> TranscriptionResult:
        full_text = payload.get("text", "")
        language = payload.get("language")
        duration = payload.get("duration")

        raw_segments = payload.get("segments", [])
        segments: list[TranscriptionSegment] = []

        for idx, seg in enumerate(raw_segments):
            segments.append(
                TranscriptionSegment(
                    id=seg.get("id", idx),
                    start=float(seg.get("start", 0.0)),
                    end=float(seg.get("end", 0.0)),
                    text=seg.get("text", "").strip(),
                    confidence=logprob_to_confidence(
                        float(seg["avg_logprob"]) if "avg_logprob" in seg else None
                    ),
                    words=seg.get("words", []),
                )
            )

        return TranscriptionResult(
            text=full_text,
            language=language,
            duration=duration,
            segments=segments,
            metadata={"model": self.model, "api_base": self.api_base},
        )
