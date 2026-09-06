"""Local faster-whisper engine for offline voice transcription."""

from __future__ import annotations

import asyncio
import logging
import tempfile
import threading
from pathlib import Path
from typing import Any

from omniscribe.core.transcription.types import (
    TranscriptionError,
    TranscriptionResult,
    TranscriptionSegment,
    logprob_to_confidence,
)

logger = logging.getLogger(__name__)

_FASTER_WHISPER_MISSING_MSG = (
    "Local Whisper transcription requires the optional 'transcription' extra. "
    "Install it with `uv sync --extra transcription` or `pip install 'omniscribe[transcription]'`."
)

_SENTENCE_END = tuple("。！？!?.…")


def _join_segment_texts(parts: list[str]) -> str:
    """Join segments; newline after sentence-final punctuation, else space."""
    out = ""
    for part in parts:
        if not part:
            continue
        if not out:
            out = part
        elif out.endswith(_SENTENCE_END):
            out += "\n" + part
        else:
            out += " " + part
    return out


class WhisperLocalEngine:
    """Local offline transcription engine using faster-whisper."""

    def __init__(self, model_size_or_path: str = "base", device: str = "auto") -> None:
        self.model_size_or_path = model_size_or_path
        self.device = device
        self._model: Any = None
        self._lock = threading.Lock()

    def _get_model(self) -> Any:
        if self._model is not None:
            return self._model

        with self._lock:
            if self._model is not None:
                return self._model

            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise TranscriptionError(
                    _FASTER_WHISPER_MISSING_MSG, status_code=503
                ) from exc

            try:
                self._model = WhisperModel(
                    self.model_size_or_path,
                    device=self.device,
                    compute_type="default",
                )
                return self._model
            except Exception as exc:
                raise TranscriptionError(
                    f"Failed to load local Whisper model '{self.model_size_or_path}': {exc}",
                    status_code=500,
                ) from exc

    async def transcribe(
        self,
        file_bytes: bytes,
        filename: str = "audio.wav",
        language: str | None = None,
        prompt: str | None = None,
        temperature: float = 0.0,
    ) -> TranscriptionResult:
        """Transcribe audio bytes using local faster-whisper model."""
        model = await asyncio.to_thread(self._get_model)

        # Write temp file for faster-whisper ingestion
        ext = Path(filename).suffix or ".wav"
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        try:

            def _sync_transcribe() -> tuple[list[Any], Any]:
                # condition_on_previous_text=False: hallucination loops on
                # long audio; vad_filter=True: skips silence/music; the
                # temperature tuple lets whisper escalate instead of
                # silently returning garbage at temperature=0.
                segments_iter, info = model.transcribe(
                    tmp_path,
                    language=language,
                    initial_prompt=prompt,
                    temperature=(temperature, 0.2, 0.4, 0.6, 0.8, 1.0),
                    beam_size=5,
                    condition_on_previous_text=False,
                    vad_filter=True,
                    word_timestamps=True,
                )
                return list(segments_iter), info

            segments_list, info = await asyncio.to_thread(_sync_transcribe)

            segments: list[TranscriptionSegment] = []
            full_text_parts: list[str] = []

            for idx, seg in enumerate(segments_list):
                text_clean = seg.text.strip()
                full_text_parts.append(text_clean)
                words = [
                    {
                        "word": w.word,
                        "start": w.start,
                        "end": w.end,
                        "probability": w.probability,
                    }
                    for w in (seg.words or [])
                ]
                segments.append(
                    TranscriptionSegment(
                        id=idx,
                        start=seg.start,
                        end=seg.end,
                        text=text_clean,
                        confidence=logprob_to_confidence(
                            getattr(seg, "avg_logprob", None)
                        ),
                        words=words,
                    )
                )

            full_text = _join_segment_texts(full_text_parts)
            return TranscriptionResult(
                text=full_text,
                language=getattr(info, "language", language),
                duration=getattr(info, "duration", None),
                segments=segments,
                metadata={
                    "model": self.model_size_or_path,
                    "engine": "whisper_local",
                    "device": self.device,
                },
            )
        finally:
            Path(tmp_path).unlink(missing_ok=True)
