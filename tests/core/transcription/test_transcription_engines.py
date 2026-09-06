"""Comprehensive unit and mock tests for transcription engines.

Covers:
- `WhisperLocalEngine` (`LocalWhisperEngine`):
  - Device and compute type resolution during initialization.
  - Thread-safe lazy model loading.
  - Missing dependency handling (`ImportError` -> `TranscriptionError` 503).
  - Whisper model loading failure handling (`TranscriptionError` 500).
  - Transcription parsing: segments, word timestamp alignment, confidence, duration.
  - Audio format handling and temporary file lifecycle cleanup.
- `GenericAudioAPIEngine` (`ApiWhisperEngine`):
  - OpenAI-compatible `/audio/transcriptions` verbose JSON response parsing.
  - Error status code handling (401, 403, 404, 429, 500).
  - Connection timeouts, transient network retries with backoff, and 502 exhaustion.
  - Empty or silent audio payload handling.
"""

from __future__ import annotations

import math
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from omniscribe.core.document import DocumentResult
from omniscribe.core.transcription.api_engine import GenericAudioAPIEngine
from omniscribe.core.transcription.local_engine import (
    _FASTER_WHISPER_MISSING_MSG,
    WhisperLocalEngine,
)
from omniscribe.core.transcription.types import (
    TranscriptionError,
    TranscriptionResult,
    TranscriptionSegment,
    logprob_to_confidence,
)
from omniscribe.core.transcription.validation import (
    AudioValidationError,
    validate_audio_input,
)

# Architectural aliases as specified in Domain 3 requirements
LocalWhisperEngine = WhisperLocalEngine
ApiWhisperEngine = GenericAudioAPIEngine


class TestLogprobToConfidence:
    """avg_logprob is a log-domain quantity; confidence must be exp()'d."""

    def test_converts_negative_logprob_into_unit_interval(self) -> None:
        assert logprob_to_confidence(-0.15) == pytest.approx(math.exp(-0.15))

    def test_zero_logprob_is_perfect_confidence(self) -> None:
        assert logprob_to_confidence(0.0) == 1.0

    def test_none_passthrough(self) -> None:
        assert logprob_to_confidence(None) is None

    def test_very_negative_logprob_underflows_to_zero(self) -> None:
        assert logprob_to_confidence(-100.0) == pytest.approx(0.0, abs=1e-6)

    def test_result_is_always_in_unit_interval(self) -> None:
        for lp in (-5.0, -2.0, -0.5, -0.01):
            conf = logprob_to_confidence(lp)
            assert conf is not None
            assert 0.0 < conf <= 1.0


# ---------------------------------------------------------------------------
# Test Helpers & Test Doubles
# ---------------------------------------------------------------------------


class _FakeWord:
    """Mock word object matching faster-whisper word structure."""

    def __init__(
        self,
        word: str,
        start: float,
        end: float,
        probability: float = 0.95,
    ) -> None:
        self.word: str = word
        self.start: float = start
        self.end: float = end
        self.probability: float = probability


class _FakeSegment:
    """Mock segment object matching faster-whisper segment structure."""

    def __init__(
        self,
        text: str,
        start: float,
        end: float,
        avg_logprob: float = -0.2,
        words: list[_FakeWord] | None = None,
    ) -> None:
        self.text: str = text
        self.start: float = start
        self.end: float = end
        self.avg_logprob: float = avg_logprob
        self.words: list[_FakeWord] | None = words


class _FakeTranscriptionInfo:
    """Mock info object matching faster-whisper transcribe info structure."""

    def __init__(
        self,
        language: str = "en",
        duration: float = 5.0,
    ) -> None:
        self.language: str = language
        self.duration: float = duration


# ---------------------------------------------------------------------------
# LocalWhisperEngine Tests
# ---------------------------------------------------------------------------


class TestLocalWhisperEngineInitialization:
    """Tests for WhisperLocalEngine initialization and model loading."""

    def test_initialization_defaults(self) -> None:
        engine = LocalWhisperEngine()
        assert engine.model_size_or_path == "base"
        assert engine.device == "auto"
        assert engine._model is None
        assert isinstance(engine._lock, type(threading.Lock()))

    def test_initialization_custom_parameters(self) -> None:
        engine = LocalWhisperEngine(model_size_or_path="medium.en", device="cuda")
        assert engine.model_size_or_path == "medium.en"
        assert engine.device == "cuda"
        assert engine._model is None

    def test_thread_safe_caching_returns_same_model_instance(self) -> None:
        engine = LocalWhisperEngine(model_size_or_path="small", device="cpu")
        mock_model = MagicMock()

        # Simulate pre-loaded model
        engine._model = mock_model
        assert engine._get_model() is mock_model

        # Verify repeated calls return cached reference
        assert engine._get_model() is mock_model

    def test_lazy_loading_instantiates_whisper_model_with_correct_args(self) -> None:
        engine = LocalWhisperEngine(model_size_or_path="tiny", device="cpu")
        mock_whisper_cls = MagicMock()
        mock_whisper_instance = MagicMock()
        mock_whisper_cls.return_value = mock_whisper_instance

        fake_faster_whisper = SimpleNamespace(WhisperModel=mock_whisper_cls)

        with patch.dict(sys.modules, {"faster_whisper": fake_faster_whisper}):
            loaded_model = engine._get_model()

        mock_whisper_cls.assert_called_once_with(
            "tiny",
            device="cpu",
            compute_type="default",
        )
        assert loaded_model is mock_whisper_instance
        assert engine._model is mock_whisper_instance

    def test_missing_whisper_dependency_raises_domain_error_503(self) -> None:
        engine = LocalWhisperEngine()

        with patch.dict(sys.modules, {"faster_whisper": None}):
            with pytest.raises(TranscriptionError) as exc_info:
                engine._get_model()

        err = exc_info.value
        assert err.status_code == 503
        assert err.message == _FASTER_WHISPER_MISSING_MSG
        assert "uv sync --extra transcription" in err.message
        assert isinstance(err.__cause__, ImportError)

    def test_whisper_model_instantiation_failure_raises_domain_error_500(self) -> None:
        engine = LocalWhisperEngine(model_size_or_path="large-v3", device="cuda")

        def _boom(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("CUDA out of memory while loading weights")

        fake_faster_whisper = SimpleNamespace(WhisperModel=_boom)

        with patch.dict(sys.modules, {"faster_whisper": fake_faster_whisper}):
            with pytest.raises(TranscriptionError) as exc_info:
                engine._get_model()

        err = exc_info.value
        assert err.status_code == 500
        assert "Failed to load local Whisper model 'large-v3'" in err.message
        assert "CUDA out of memory" in err.message
        assert isinstance(err.__cause__, RuntimeError)


class TestLocalWhisperEngineTranscribe:
    """Tests for WhisperLocalEngine.transcribe and result parsing."""

    async def test_transcribe_successful_parsing_with_word_timestamps(self) -> None:
        engine = LocalWhisperEngine(model_size_or_path="base", device="cpu")

        words = [
            _FakeWord(word="Hello", start=0.0, end=0.4, probability=0.98),
            _FakeWord(word="world", start=0.45, end=0.9, probability=0.99),
        ]
        segments = [
            _FakeSegment(
                text=" Hello world",
                start=0.0,
                end=1.0,
                avg_logprob=-0.15,
                words=words,
            ),
            _FakeSegment(
                text=" OmniScribe offline audio test.",
                start=1.2,
                end=3.4,
                avg_logprob=-0.22,
                words=[
                    _FakeWord(word="OmniScribe", start=1.2, end=2.0, probability=0.95),
                    _FakeWord(word="offline", start=2.05, end=2.5, probability=0.92),
                    _FakeWord(word="audio", start=2.55, end=2.9, probability=0.94),
                    _FakeWord(word="test.", start=2.95, end=3.4, probability=0.96),
                ],
            ),
        ]
        info = _FakeTranscriptionInfo(language="en", duration=3.5)

        mock_model = MagicMock()
        mock_model.transcribe.return_value = (iter(segments), info)
        engine._model = mock_model

        fake_audio_bytes = b"RIFFFAKEWAVDATA"
        res = await engine.transcribe(
            file_bytes=fake_audio_bytes,
            filename="sample_audio.wav",
            language="en",
            prompt="Initial context",
            temperature=0.2,
        )

        assert isinstance(res, TranscriptionResult)
        assert res.text == "Hello world OmniScribe offline audio test."
        assert res.language == "en"
        assert res.duration == 3.5
        assert res.metadata == {
            "model": "base",
            "engine": "whisper_local",
            "device": "cpu",
        }

        # Check segments
        assert len(res.segments) == 2
        seg0 = res.segments[0]
        assert seg0.id == 0
        assert seg0.start == 0.0
        assert seg0.end == 1.0
        assert seg0.text == "Hello world"
        assert seg0.confidence == pytest.approx(math.exp(-0.15))
        assert len(seg0.words) == 2
        assert seg0.words[0] == {
            "word": "Hello",
            "start": 0.0,
            "end": 0.4,
            "probability": 0.98,
        }

        seg1 = res.segments[1]
        assert seg1.id == 1
        assert seg1.start == 1.2
        assert seg1.end == 3.4
        assert seg1.text == "OmniScribe offline audio test."
        assert seg1.confidence == pytest.approx(math.exp(-0.22))
        assert len(seg1.words) == 4

    async def test_transcribe_passes_parameters_to_whisper_model(self) -> None:
        engine = LocalWhisperEngine()
        mock_model = MagicMock()
        mock_model.transcribe.return_value = (iter([]), _FakeTranscriptionInfo())
        engine._model = mock_model

        await engine.transcribe(
            file_bytes=b"dummy-bytes",
            filename="meeting.mp3",
            language="de",
            prompt="Sitzungsprotokoll",
            temperature=0.3,
        )

        # Verify arguments passed to model.transcribe
        assert mock_model.transcribe.call_count == 1
        call_args, call_kwargs = mock_model.transcribe.call_args
        temp_file_arg = call_args[0]
        assert temp_file_arg.endswith(".mp3")
        assert call_kwargs["language"] == "de"
        assert call_kwargs["initial_prompt"] == "Sitzungsprotokoll"
        # Temperature fallback tuple: requested value first, escalation after.
        assert call_kwargs["temperature"] == (0.3, 0.2, 0.4, 0.6, 0.8, 1.0)
        assert call_kwargs["beam_size"] == 5
        assert call_kwargs["condition_on_previous_text"] is False
        assert call_kwargs["vad_filter"] is True
        assert call_kwargs["word_timestamps"] is True

    def test_join_segment_texts_newline_after_sentence_end(self) -> None:
        from omniscribe.core.transcription.local_engine import _join_segment_texts

        assert (
            _join_segment_texts(["Hello world.", "Second part"])
            == "Hello world.\nSecond part"
        )
        # No terminal punctuation → space join (previous behaviour).
        assert _join_segment_texts(["Hello", "world"]) == "Hello world"

    def test_join_segment_texts_edge_cases(self) -> None:
        from omniscribe.core.transcription.local_engine import _join_segment_texts

        assert _join_segment_texts([]) == ""
        assert _join_segment_texts(["Only one."]) == "Only one."
        assert _join_segment_texts(["", "Skipped empty.", "Next"]) == (
            "Skipped empty.\nNext"
        )
        # CJK sentence-final punctuation also breaks lines.
        assert _join_segment_texts(["こんにちは。", "次の文"] ) == "こんにちは。\n次の文"

    async def test_transcribe_cleans_up_temporary_file_on_success(self) -> None:
        engine = LocalWhisperEngine()
        created_temp_path: list[str] = []

        def _side_effect(path: str, **_kwargs: Any) -> tuple[Any, Any]:
            created_temp_path.append(path)
            # The file should exist while transcribe runs
            assert Path(path).exists()
            return iter([]), _FakeTranscriptionInfo()

        mock_model = MagicMock()
        mock_model.transcribe.side_effect = _side_effect
        engine._model = mock_model

        await engine.transcribe(b"bytes-data", "record.wav")

        assert len(created_temp_path) == 1
        # The file MUST be cleaned up after transcribe exits
        assert not Path(created_temp_path[0]).exists()

    async def test_transcribe_cleans_up_temporary_file_on_exception(self) -> None:
        engine = LocalWhisperEngine()
        created_temp_path: list[str] = []

        def _exploding_transcribe(path: str, **_kwargs: Any) -> Any:
            created_temp_path.append(path)
            assert Path(path).exists()
            raise RuntimeError("Audio codec decoding error inside whisper")

        mock_model = MagicMock()
        mock_model.transcribe.side_effect = _exploding_transcribe
        engine._model = mock_model

        with pytest.raises(RuntimeError, match="Audio codec decoding error"):
            await engine.transcribe(b"bad-bytes", "broken.wav")

        assert len(created_temp_path) == 1
        # The file MUST be cleaned up even if transcription crashes
        assert not Path(created_temp_path[0]).exists()

    async def test_transcribe_segments_without_words_handled_cleanly(self) -> None:
        engine = LocalWhisperEngine()
        seg_no_words = _FakeSegment(
            text="Segment without word alignment",
            start=0.0,
            end=2.0,
            words=None,
        )
        mock_model = MagicMock()
        mock_model.transcribe.return_value = (
            iter([seg_no_words]),
            _FakeTranscriptionInfo(),
        )
        engine._model = mock_model

        res = await engine.transcribe(b"audio", "file.wav")
        assert len(res.segments) == 1
        assert res.segments[0].words == []
        assert res.segments[0].text == "Segment without word alignment"

    async def test_transcribe_falls_back_to_language_argument(self) -> None:
        engine = LocalWhisperEngine()
        mock_model = MagicMock()
        # info has no language attribute or language is None
        info = SimpleNamespace(duration=4.2)
        mock_model.transcribe.return_value = (iter([]), info)
        engine._model = mock_model

        res = await engine.transcribe(b"audio", "file.wav", language="ja")
        assert res.language == "ja"

    async def test_to_document_result_structure(self) -> None:
        result = TranscriptionResult(
            text="First sentence. Second sentence.",
            language="en",
            duration=4.0,
            segments=[
                TranscriptionSegment(
                    id=0,
                    start=0.0,
                    end=1.8,
                    text="First sentence.",
                    confidence=-0.1,
                    words=[
                        {"word": "First", "start": 0.0, "end": 0.8, "probability": 0.9}
                    ],
                ),
                TranscriptionSegment(
                    id=1,
                    start=2.0,
                    end=3.9,
                    text="Second sentence.",
                    confidence=-0.2,
                ),
            ],
            metadata={"model": "base", "engine": "whisper_local"},
        )

        doc = result.to_document_result()
        assert isinstance(doc, DocumentResult)
        assert len(doc.pages) == 1
        page = doc.pages[0]
        assert page.page_index == 0
        assert page.metadata["media_type"] == "audio"
        assert page.metadata["duration"] == 4.0

        assert len(page.blocks) == 2
        b0 = page.blocks[0]
        assert b0.reading_order == 0
        assert b0.text == "First sentence."
        assert b0.kind == "speech"
        assert b0.source_processor == "voice_transcription"
        assert b0.metadata["start_time"] == 0.0
        assert b0.metadata["end_time"] == 1.8
        assert b0.metadata["duration"] == 1.8

        b1 = page.blocks[1]
        assert b1.reading_order == 1
        assert b1.text == "Second sentence."
        assert b1.metadata["start_time"] == 2.0


class TestLocalWhisperEngineAudioFormatHandling:
    """Tests for audio format handling and validation."""

    async def test_extension_without_dot_falls_back_to_wav(self) -> None:
        engine = LocalWhisperEngine()
        created_temp_path: list[str] = []

        def _capture(path: str, **_kwargs: Any) -> tuple[Any, Any]:
            created_temp_path.append(path)
            return iter([]), _FakeTranscriptionInfo()

        mock_model = MagicMock()
        mock_model.transcribe.side_effect = _capture
        engine._model = mock_model

        await engine.transcribe(b"dummy", filename="no_extension_file")
        assert len(created_temp_path) == 1
        assert created_temp_path[0].endswith(".wav")

    def test_validation_rejects_unsupported_extensions(self) -> None:
        for bad_name in ("test.exe", "document.pdf", "script.py", "archive.zip"):
            with pytest.raises(AudioValidationError) as exc:
                validate_audio_input(bad_name, content_type="audio/wav", file_size=1024)
            assert exc.value.status_code == 415
            assert "Unsupported audio format" in exc.value.message


# ---------------------------------------------------------------------------
# ApiWhisperEngine Tests
# ---------------------------------------------------------------------------


class TestApiWhisperEngineTranscribe:
    """Tests for GenericAudioAPIEngine transcription and verbose JSON parsing."""

    def test_initialization_defaults_and_normalization(self) -> None:
        engine = ApiWhisperEngine()
        assert engine.model == "whisper-1"
        assert engine.api_base == "https://api.openai.com/v1"
        assert engine.api_key is None
        assert engine.timeout == 300.0

        # Custom initialization with trailing slash in api_base
        custom = ApiWhisperEngine(
            model="  whisper-large-v3  ",
            api_base="http://localhost:8080/v1///",
            api_key="sk-custom-12345",
            timeout=60.0,
        )
        assert custom.model == "whisper-large-v3"
        assert custom.api_base == "http://localhost:8080/v1"
        assert custom.api_key == "sk-custom-12345"
        assert custom.timeout == 60.0

    async def test_transcribe_success_verbose_json_parsing(self) -> None:
        engine = ApiWhisperEngine(
            model="whisper-1",
            api_base="https://api.openai.com/v1",
            api_key="valid-key",
        )

        fake_resp = MagicMock(spec=httpx.Response)
        fake_resp.status_code = 200
        fake_resp.json.return_value = {
            "text": "The quick brown fox jumps over the lazy dog.",
            "language": "english",
            "duration": 4.12,
            "segments": [
                {
                    "id": 0,
                    "start": 0.0,
                    "end": 2.1,
                    "text": "The quick brown fox",
                    "avg_logprob": -0.12,
                    "words": [
                        {"word": "The", "start": 0.0, "end": 0.2},
                        {"word": "quick", "start": 0.25, "end": 0.6},
                    ],
                },
                {
                    "id": 1,
                    "start": 2.2,
                    "end": 4.12,
                    "text": "jumps over the lazy dog.",
                    "avg_logprob": -0.18,
                    "words": [],
                },
            ],
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = fake_resp
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch("httpx.AsyncClient", return_value=mock_client):
            res = await engine.transcribe(
                file_bytes=b"dummy-audio-bytes",
                filename="fox.mp3",
                language="en",
                prompt="animal sentence",
                temperature=0.1,
            )

        assert isinstance(res, TranscriptionResult)
        assert res.text == "The quick brown fox jumps over the lazy dog."
        assert res.language == "english"
        assert res.duration == 4.12
        assert res.metadata == {
            "model": "whisper-1",
            "api_base": "https://api.openai.com/v1",
        }
        assert len(res.segments) == 2
        assert res.segments[0].id == 0
        assert res.segments[0].confidence == pytest.approx(math.exp(-0.12))
        assert res.segments[0].words[0]["word"] == "The"
        assert res.segments[1].confidence == pytest.approx(math.exp(-0.18))

        # Verify client.post request structure
        mock_client.post.assert_called_once()
        call_url, call_kwargs = mock_client.post.call_args
        assert call_url[0] == "https://api.openai.com/v1/audio/transcriptions"
        assert call_kwargs["headers"] == {"Authorization": "Bearer valid-key"}
        assert call_kwargs["data"] == {
            "model": "whisper-1",
            "response_format": "verbose_json",
            "temperature": "0.1",
            "language": "en",
            "prompt": "animal sentence",
        }
        assert "file" in call_kwargs["files"]
        assert call_kwargs["files"]["file"] == ("fox.mp3", b"dummy-audio-bytes")

    async def test_transcribe_omits_auth_header_when_api_key_not_set(self) -> None:
        engine = ApiWhisperEngine(
            model="local-whisper",
            api_base="http://localhost:5000/v1",
            api_key=None,
        )

        fake_resp = MagicMock(spec=httpx.Response)
        fake_resp.status_code = 200
        fake_resp.json.return_value = {"text": "Local model transcription"}

        mock_client = AsyncMock()
        mock_client.post.return_value = fake_resp
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch("httpx.AsyncClient", return_value=mock_client):
            await engine.transcribe(b"bytes", "audio.wav")

        call_kwargs = mock_client.post.call_args[1]
        assert call_kwargs["headers"] == {}


class TestApiWhisperEngineErrorHandling:
    """Tests for GenericAudioAPIEngine API error responses, retries, and timeouts."""

    @pytest.mark.parametrize(
        ("status_code", "expected_msg_fragment"),
        [
            (401, "Invalid API key or unauthorized access."),
            (403, "Invalid API key or unauthorized access."),
            (404, "Model or endpoint not found: test-model"),
        ],
    )
    async def test_api_status_code_error_mappings(
        self,
        status_code: int,
        expected_msg_fragment: str,
    ) -> None:
        engine = ApiWhisperEngine(model="test-model", api_key="sk-test")

        fake_resp = MagicMock(spec=httpx.Response)
        fake_resp.status_code = status_code
        fake_resp.headers = {}
        fake_resp.text = f"API error with code {status_code}"

        mock_client = AsyncMock()
        mock_client.post.return_value = fake_resp
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(TranscriptionError) as exc_info:
                await engine.transcribe(b"audio", "file.wav")

        assert exc_info.value.status_code == status_code
        assert expected_msg_fragment in exc_info.value.message

    @pytest.mark.parametrize("status_code", [429, 500, 503])
    async def test_retryable_status_exhausts_attempts_then_502(
        self, status_code: int
    ) -> None:
        engine = ApiWhisperEngine(model="test-model", api_key="sk-test")

        fake_resp = MagicMock(spec=httpx.Response)
        fake_resp.status_code = status_code
        fake_resp.headers = {}
        fake_resp.text = f"API error with code {status_code}"

        mock_client = AsyncMock()
        mock_client.post.return_value = fake_resp
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            with pytest.raises(TranscriptionError) as exc_info:
                await engine.transcribe(b"audio", "file.wav")

        assert mock_client.post.call_count == 3
        assert mock_sleep.call_count == 2
        assert exc_info.value.status_code == 502
        assert f"status {status_code}" in exc_info.value.message

    async def test_transient_connection_timeout_retries_and_succeeds(self) -> None:
        engine = ApiWhisperEngine(model="whisper-1")

        fake_resp_success = MagicMock(spec=httpx.Response)
        fake_resp_success.status_code = 200
        fake_resp_success.json.return_value = {"text": "Recovered on attempt 2"}

        mock_client = AsyncMock()
        # Attempt 1 raises transient timeout; Attempt 2 succeeds
        mock_client.post.side_effect = [
            httpx.ReadTimeout("Read timed out after 30 seconds"),
            fake_resp_success,
        ]
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            res = await engine.transcribe(b"bytes", "file.wav")

        assert res.text == "Recovered on attempt 2"
        assert mock_client.post.call_count == 2
        mock_sleep.assert_called_once_with(1.0)

    async def test_transient_connection_timeout_exhaustion_raises_502(self) -> None:
        engine = ApiWhisperEngine(model="whisper-1")

        mock_client = AsyncMock()
        # All 3 attempts fail with transient connection error
        mock_client.post.side_effect = httpx.ConnectError("Connection refused by host")
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            with pytest.raises(TranscriptionError) as exc_info:
                await engine.transcribe(b"bytes", "file.wav")

        err = exc_info.value
        assert err.status_code == 502
        assert "Audio transcription API request failed" in err.message
        assert "Connection refused" in err.message
        assert isinstance(err.__cause__, httpx.ConnectError)
        assert mock_client.post.call_count == 3
        # Backoff: attempt 1 slept 1.0, attempt 2 slept 2.0
        assert mock_sleep.call_count == 2
        assert mock_sleep.call_args_list[0][0][0] == 1.0
        assert mock_sleep.call_args_list[1][0][0] == 2.0

    async def test_non_transient_exception_terminates_without_retries(self) -> None:
        engine = ApiWhisperEngine(model="whisper-1")

        mock_client = AsyncMock()
        # ValueError is considered a non-transient caller-side bug
        mock_client.post.side_effect = ValueError("Invalid URL scheme")
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            with pytest.raises(TranscriptionError) as exc_info:
                await engine.transcribe(b"bytes", "file.wav")

        assert exc_info.value.status_code == 502
        # Only 1 attempt made
        assert mock_client.post.call_count == 1
        mock_sleep.assert_not_called()

    async def test_retry_hoists_client_and_honors_retry_after(self) -> None:
        """One client across attempts; Retry-After beats exponential backoff."""
        engine = ApiWhisperEngine(model="whisper-1")

        resp_500 = MagicMock(spec=httpx.Response)
        resp_500.status_code = 500
        resp_500.headers = {}
        resp_500.text = "server exploded"
        resp_429 = MagicMock(spec=httpx.Response)
        resp_429.status_code = 429
        resp_429.headers = {"Retry-After": "7"}
        resp_429.text = "slow down"
        resp_ok = MagicMock(spec=httpx.Response)
        resp_ok.status_code = 200
        resp_ok.json.return_value = {"text": "finally"}

        mock_client = AsyncMock()
        mock_client.post.side_effect = [resp_500, resp_429, resp_ok]
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        constructed = []

        def _fake_client(*args: Any, **kwargs: Any) -> AsyncMock:
            constructed.append(True)
            return mock_client

        with (
            patch("httpx.AsyncClient", side_effect=_fake_client),
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            res = await engine.transcribe(b"bytes", "file.wav")

        assert res.text == "finally"
        assert len(constructed) == 1, "client must be constructed once, not per attempt"
        assert mock_client.post.call_count == 3
        delays = [call.args[0] for call in mock_sleep.call_args_list]
        assert delays == [1.0, 7.0], "exponential then Retry-After honored"

    def test_retry_delay_caps_and_fallbacks(self) -> None:
        engine = ApiWhisperEngine(model="whisper-1")
        # Exponential 1/2/4 base with 16s cap.
        assert engine._retry_delay_s(1, None) == 1.0
        assert engine._retry_delay_s(2, None) == 2.0
        assert engine._retry_delay_s(3, None) == 4.0
        assert engine._retry_delay_s(9, None) == 16.0
        # Retry-After wins but is capped at 60.
        assert engine._retry_delay_s(1, "7") == 7.0
        assert engine._retry_delay_s(1, "500") == 60.0
        # Invalid Retry-After falls back to exponential.
        assert engine._retry_delay_s(1, "soon") == 1.0


class TestApiWhisperEngineEmptyAndSilentAudio:
    """Tests for empty, silent, or degenerate audio payloads."""

    async def test_empty_audio_response_payload(self) -> None:
        engine = ApiWhisperEngine()

        fake_resp = MagicMock(spec=httpx.Response)
        fake_resp.status_code = 200
        fake_resp.json.return_value = {
            "text": "",
            "language": "en",
            "duration": 0.0,
            "segments": [],
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = fake_resp
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch("httpx.AsyncClient", return_value=mock_client):
            res = await engine.transcribe(b"silent-audio", "silent.wav")

        assert res.text == ""
        assert res.duration == 0.0
        assert res.segments == []

        # Converting empty result produces empty page blocks
        doc = res.to_document_result()
        assert len(doc.pages) == 1
        assert doc.pages[0].blocks == []

    async def test_silent_audio_transcription_with_blank_tag(self) -> None:
        engine = ApiWhisperEngine()

        fake_resp = MagicMock(spec=httpx.Response)
        fake_resp.status_code = 200
        fake_resp.json.return_value = {
            "text": " [BLANK_AUDIO] ",
            "language": "en",
            "duration": 5.0,
            "segments": [
                {
                    "id": 0,
                    "start": 0.0,
                    "end": 5.0,
                    "text": "[BLANK_AUDIO]",
                    "avg_logprob": -0.99,
                }
            ],
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = fake_resp
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch("httpx.AsyncClient", return_value=mock_client):
            res = await engine.transcribe(b"silence", "silence.wav")

        assert res.text == " [BLANK_AUDIO] "
        assert len(res.segments) == 1
        assert res.segments[0].text == "[BLANK_AUDIO]"

        doc = res.to_document_result()
        assert len(doc.pages[0].blocks) == 1
        assert doc.pages[0].blocks[0].text == "[BLANK_AUDIO]"

    async def test_missing_fields_in_verbose_json_tolerated(self) -> None:
        engine = ApiWhisperEngine()

        fake_resp = MagicMock(spec=httpx.Response)
        fake_resp.status_code = 200
        # Minimal payload lacking optional keys
        fake_resp.json.return_value = {}

        mock_client = AsyncMock()
        mock_client.post.return_value = fake_resp
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch("httpx.AsyncClient", return_value=mock_client):
            res = await engine.transcribe(b"bytes", "audio.wav")

        assert res.text == ""
        assert res.language is None
        assert res.duration is None
        assert res.segments == []
