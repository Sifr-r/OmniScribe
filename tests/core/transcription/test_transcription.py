"""Unit tests for the transcription subpackage (core/transcription/)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from omniscribe.core.transcription.api_engine import GenericAudioAPIEngine
from omniscribe.core.transcription.factory import (
    create_transcription_engine,
    get_transcription_engine,
)
from omniscribe.core.transcription.types import (
    TranscriptionError,
    TranscriptionResult,
    logprob_to_confidence,
)
from omniscribe.core.transcription.validation import (
    AudioValidationError,
    validate_audio_input,
)


class TestAudioValidation:
    def test_valid_extensions(self) -> None:
        for ext in (
            ".mp3",
            ".wav",
            ".m4a",
            ".flac",
            ".ogg",
            ".webm",
            ".aac",
            ".opus",
            ".mp4",
        ):
            res = validate_audio_input(f"recording{ext}", "audio/mpeg", 1024)
            assert res == ext

    def test_empty_filename_raises(self) -> None:
        with pytest.raises(AudioValidationError) as exc:
            validate_audio_input("", "audio/mp3", 100)
        assert exc.value.status_code == 400

    def test_unsupported_extension_raises(self) -> None:
        with pytest.raises(AudioValidationError) as exc:
            validate_audio_input("test.pdf", "audio/mp3", 100)
        assert exc.value.status_code == 415

    def test_invalid_mime_type_raises(self) -> None:
        with pytest.raises(AudioValidationError) as exc:
            validate_audio_input("test.mp3", "image/png", 100)
        assert exc.value.status_code == 415

    def test_zero_byte_file_raises(self) -> None:
        with pytest.raises(AudioValidationError) as exc:
            validate_audio_input("test.mp3", "audio/mpeg", 0)
        assert exc.value.status_code == 400

    def test_oversized_file_raises(self) -> None:
        with pytest.raises(AudioValidationError) as exc:
            validate_audio_input("test.mp3", "audio/mpeg", 200, max_bytes=100)
        assert exc.value.status_code == 413


class TestGenericAudioAPIEngine:
    async def test_transcribe_success(self) -> None:
        engine = GenericAudioAPIEngine(
            model="whisper-1",
            api_base="https://api.example.com/v1",
            api_key="secret-key",
        )
        fake_response = MagicMock(spec=httpx.Response)
        fake_response.status_code = 200
        fake_response.json.return_value = {
            "text": "Hello world transcription",
            "language": "en",
            "duration": 5.4,
            "segments": [
                {
                    "id": 0,
                    "start": 0.0,
                    "end": 2.5,
                    "text": "Hello world",
                    "avg_logprob": -0.2,
                },
                {
                    "id": 1,
                    "start": 2.5,
                    "end": 5.4,
                    "text": "transcription",
                    "avg_logprob": -0.1,
                },
            ],
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = fake_response
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch("httpx.AsyncClient", return_value=mock_client):
            res = await engine.transcribe(
                b"fake_audio_bytes", "sample.wav", language="en"
            )

        assert isinstance(res, TranscriptionResult)
        assert res.text == "Hello world transcription"
        assert res.language == "en"
        assert len(res.segments) == 2
        assert res.segments[0].text == "Hello world"
        assert res.segments[1].end == 5.4

    async def test_transcribe_unauthorized(self) -> None:
        engine = GenericAudioAPIEngine(
            model="whisper-1",
            api_base="https://api.example.com/v1",
            api_key="bad-key",
        )
        fake_response = MagicMock(spec=httpx.Response)
        fake_response.status_code = 401
        fake_response.text = "Unauthorized"

        mock_client = AsyncMock()
        mock_client.post.return_value = fake_response
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(TranscriptionError) as exc:
                await engine.transcribe(b"fake_audio", "audio.mp3")
            assert exc.value.status_code == 401


class TestTranscriptionFactory:
    def test_factory_creates_api_engine(self) -> None:
        engine = get_transcription_engine(
            engine_type="api",
            model="custom-whisper",
            api_base="http://localhost:8080/v1",
        )
        assert isinstance(engine, GenericAudioAPIEngine)
        assert engine.model == "custom-whisper"
        assert engine.api_base == "http://localhost:8080/v1"

    def test_factory_auto_falls_back_to_api_without_faster_whisper(self) -> None:
        with patch.dict("sys.modules", {"faster_whisper": None}):
            engine = get_transcription_engine(engine_type="auto")
            assert isinstance(engine, GenericAudioAPIEngine)

    def test_factory_rejects_unknown_engine_type(self) -> None:
        with pytest.raises(
            ValueError, match="Unknown transcription engine: 'invalid_engine'"
        ):
            create_transcription_engine(engine_type="invalid_engine")

    def test_create_transcription_engine_alias(self) -> None:
        engine = create_transcription_engine(engine_type="api")
        assert isinstance(engine, GenericAudioAPIEngine)


class TestConfidenceCalculation:
    def test_logprob_to_confidence_none(self) -> None:
        assert logprob_to_confidence(None) is None

    def test_logprob_to_confidence_nan(self) -> None:
        assert logprob_to_confidence(float("nan")) == 0.0

    def test_logprob_to_confidence_overflow(self) -> None:
        assert logprob_to_confidence(1000.0) == 1.0
        assert logprob_to_confidence(float("inf")) == 1.0

    def test_logprob_to_confidence_clamping(self) -> None:
        assert logprob_to_confidence(0.5) == 1.0

    def test_logprob_to_confidence_normal(self) -> None:
        import math

        conf = logprob_to_confidence(-0.5)
        assert conf is not None
        assert 0.0 < conf < 1.0
        assert math.isclose(conf, math.exp(-0.5))


class TestWhisperLocalEngine:
    def test_whisper_local_engine_lock_and_caching(self) -> None:
        import threading

        from omniscribe.core.transcription.local_engine import WhisperLocalEngine

        engine = WhisperLocalEngine()
        assert isinstance(engine._lock, type(threading.Lock()))
        assert engine._model is None

        # Simulate model already loaded
        fake_model = object()
        engine._model = fake_model
        assert engine._get_model() is fake_model
