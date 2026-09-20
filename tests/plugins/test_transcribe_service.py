"""Unit tests for the transcribe plugin service (no HTTP layer)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from omniscribe.plugins.transcribe import config_store
from omniscribe.plugins.transcribe import service as transcribe_service
from omniscribe.plugins.transcribe.schemas import TranscribeRequest


class _FakeStore:
    def __init__(self) -> None:
        self.blobs: dict[str, tuple[str, bytes, str]] = {}

    async def put(
        self,
        blob: bytes,
        *,
        content_type: str,
        owner_job_id: str,
        ttl_seconds: int | None = None,
    ) -> Any:
        artifact_id = f"a{len(self.blobs):031d}"
        token = f"t{len(self.blobs):041d}"
        self.blobs[artifact_id] = (token, blob, content_type)

        class _Handle:
            pass

        handle = _Handle()
        handle.id = artifact_id  # type: ignore[attr-defined]
        handle.token = token  # type: ignore[attr-defined]
        return handle

    async def get(self, artifact_id: str, token: str) -> Any:
        entry = self.blobs.get(artifact_id)
        if entry is None or entry[0] != token:
            return None

        class _Blob:
            blob = entry[1]  # type: ignore[index]
            content_type = entry[2]  # type: ignore[index]
            record = None

        return _Blob()


def _config() -> dict[str, str]:
    return {
        "transcription_api_base": "https://api.openai.com/v1",
        "transcription_model": "whisper-1",
        "transcription_engine": "api",
    }


def _result(text: str = "hello world", language: str | None = "en") -> Any:
    from omniscribe.core.transcription.types import (
        TranscriptionResult,
        TranscriptionSegment,
    )

    return TranscriptionResult(
        text=text,
        language=language,
        duration=2.0,
        segments=[TranscriptionSegment(id=0, start=0.0, end=2.0, text=text)],
    )


def _stub_engine(
    monkeypatch: pytest.MonkeyPatch,
    result: Any,
    calls: list[dict[str, Any]],
    factory_calls: list[dict[str, Any]] | None = None,
) -> None:
    class _Engine:
        async def transcribe(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            return result

    def _factory(**kw: Any) -> _Engine:
        if factory_calls is not None:
            factory_calls.append(kw)
        return _Engine()

    monkeypatch.setattr(transcribe_service, "get_transcription_engine", _factory)


async def test_transcribe_happy_path_stores_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    factory_calls: list[dict[str, Any]] = []
    _stub_engine(monkeypatch, _result(), calls, factory_calls)
    store = _FakeStore()
    result = await transcribe_service.transcribe(
        TranscribeRequest(model="whisper-1"),
        file_bytes=b"fake-audio",
        filename="clip.wav",
        content_type="audio/wav",
        store=store,  # type: ignore[arg-type]
        config=_config(),
    )
    assert result["text"] == "hello world"
    assert result["language"] == "en"
    assert result["duration"] == 2.0
    assert result["job_id"].startswith("job-")
    assert len(result["segments"]) == 1
    assert result["segments"][0]["text"] == "hello world"
    # Both artifacts stored and referenced with tokens.
    assert result["text_artifact_id"] in store.blobs
    assert result["metadata_artifact_id"] in store.blobs
    text_blob = json.loads(store.blobs[result["text_artifact_id"]][1].decode("utf-8"))
    assert text_blob == {"0": "hello world"}
    meta_blob = json.loads(
        store.blobs[result["metadata_artifact_id"]][1].decode("utf-8")
    )
    assert set(meta_blob) == {"0"}
    # Factory received the resolved chain values.
    assert factory_calls[0]["model"] == "whisper-1"
    assert factory_calls[0]["api_base"] == "https://api.openai.com/v1"
    # Engine received exactly the Protocol call surface.
    assert set(calls[0]) == {
        "file_bytes",
        "filename",
        "language",
        "prompt",
        "temperature",
    }


async def test_transcribe_resolves_form_over_config_over_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    factory_calls: list[dict[str, Any]] = []
    _stub_engine(monkeypatch, _result(), calls, factory_calls)
    await transcribe_service.transcribe(
        TranscribeRequest(model="custom-model", api_key="sk-x"),
        file_bytes=b"x",
        filename="a.mp3",
        content_type="audio/mpeg",
        store=_FakeStore(),  # type: ignore[arg-type]
        config={"transcription_model": "config-model"},
    )
    assert factory_calls[0]["model"] == "custom-model"


async def test_transcribe_ssrf_checks_override_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_call_llm(*args: Any, **kwargs: Any) -> str:
        raise AssertionError("must not be called")

    monkeypatch.setattr(transcribe_service, "get_transcription_engine", fail_call_llm)
    with pytest.raises(transcribe_service.TranscribeError) as excinfo:
        await transcribe_service.transcribe(
            TranscribeRequest(api_base="http://169.254.169.254/latest"),
            file_bytes=b"x",
            filename="a.wav",
            content_type="audio/wav",
            store=_FakeStore(),  # type: ignore[arg-type]
            config=_config(),
        )
    assert excinfo.value.status_code == 403
    assert excinfo.value.error == "ssrf_blocked"


async def test_transcribe_bad_extension_maps_to_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(transcribe_service.TranscribeError) as excinfo:
        await transcribe_service.transcribe(
            TranscribeRequest(),
            file_bytes=b"%PDF-1.4",
            filename="doc.pdf",
            content_type="application/pdf",
            store=_FakeStore(),  # type: ignore[arg-type]
            config=_config(),
        )
    assert excinfo.value.status_code == 400
    assert excinfo.value.error == "bad_request"
    assert "Unsupported audio format" in excinfo.value.detail


async def test_transcribe_engine_error_maps_to_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from omniscribe.core.transcription.types import TranscriptionError

    class _Broken:
        async def transcribe(self, **kwargs: Any) -> Any:
            raise TranscriptionError(
                "Local transcription requires the 'transcription' extra", 503
            )

    monkeypatch.setattr(
        transcribe_service, "get_transcription_engine", lambda **kw: _Broken()
    )
    with pytest.raises(transcribe_service.TranscribeError) as excinfo:
        await transcribe_service.transcribe(
            TranscribeRequest(engine="local"),
            file_bytes=b"x",
            filename="a.wav",
            content_type="audio/wav",
            store=_FakeStore(),  # type: ignore[arg-type]
            config=_config(),
        )
    assert excinfo.value.status_code == 503
    assert excinfo.value.error == "backend_unavailable"
    assert "transcription" in excinfo.value.detail


async def test_transcribe_unexpected_error_maps_to_502(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Broken:
        async def transcribe(self, **kwargs: Any) -> Any:
            raise RuntimeError("connection reset")

    monkeypatch.setattr(
        transcribe_service, "get_transcription_engine", lambda **kw: _Broken()
    )
    with pytest.raises(transcribe_service.TranscribeError) as excinfo:
        await transcribe_service.transcribe(
            TranscribeRequest(),
            file_bytes=b"x",
            filename="a.wav",
            content_type="audio/wav",
            store=_FakeStore(),  # type: ignore[arg-type]
            config=_config(),
        )
    assert excinfo.value.status_code == 502
    assert excinfo.value.error == "ai_error"
    assert excinfo.value.detail == "The AI service request failed."


# ---------------------------------------------------------------------------
# Config store + model discovery
# ---------------------------------------------------------------------------


def test_mask_api_key() -> None:
    assert config_store.mask_api_key(None) is None
    assert config_store.mask_api_key("") == ""
    assert config_store.mask_api_key("lm-studio") == "lm-studio"
    assert config_store.mask_api_key("short") == "********"
    assert config_store.mask_api_key("abcd1234wxyz") == "********"
    assert config_store.mask_api_key("abcd1234wxyzextra") == "abcd...xtra"


def test_config_store_defaults_and_write_through() -> None:
    store = transcribe_service.TranscriptionConfigStore(auth_token="tok")
    read = store.read()
    assert read.transcription_api_base == "https://api.openai.com/v1"
    assert read.transcription_model == "whisper-1"
    assert read.transcription_engine == "api"
    assert read.temperature == 0.0
    assert read.transcription_auth_token == "********"  # "tok" masked: len <= 8

    store.update(
        {"transcription_model": "gpt-4o-audio-preview", "transcription_api_key": "k"}
    )
    read = store.read()
    assert read.transcription_model == "gpt-4o-audio-preview"
    # mask_api_key masks any short key as "********" (verbatim old rule),
    # so the write-through shows up as "" → "********".
    assert read.transcription_api_key == "********"


async def test_discover_models_falls_back_on_bad_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _ssrf_allowed(url: str | None) -> Any:
        return SimpleNamespace(allowed=True)

    class _FailingClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> _FailingClient:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def get(self, url: str, headers: dict | None = None) -> Any:
            raise RuntimeError("no network")

    monkeypatch.setattr(config_store, "is_ssrf_target", _ssrf_allowed)
    monkeypatch.setattr(config_store.httpx, "AsyncClient", _FailingClient)
    models = await transcribe_service.discover_transcription_models(
        "http://localhost:1234/v1", None
    )
    assert models == config_store.TRANSCRIPTION_FALLBACK_MODELS


def test_extract_model_ids_handles_openai_and_ollama() -> None:
    openai = {"data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}]}
    ollama = {"models": [{"name": "llama3"}]}
    assert config_store.extract_model_ids_from_response(openai) == [
        "gpt-4o",
        "gpt-4o-mini",
    ]
    assert config_store.extract_model_ids_from_response(ollama) == ["llama3"]
    assert config_store.extract_model_ids_from_response(None) == []


def test_resolve_engine_settings_guards_foreign_origin() -> None:
    config = {
        "transcription_api_base": "https://api.openai.com/v1",
        "transcription_api_key": "sk-server-secret-key",
    }
    # 1. Matching origin attaches stored key
    req_same = TranscribeRequest(
        api_base="https://api.openai.com/v1/audio/transcriptions"
    )
    res_same = transcribe_service.resolve_engine_settings(req_same, config)
    assert res_same["api_key"] == "sk-server-secret-key"

    # 2. Foreign origin does NOT attach stored key
    req_foreign = TranscribeRequest(api_base="https://attacker.com/v1")
    res_foreign = transcribe_service.resolve_engine_settings(req_foreign, config)
    assert res_foreign["api_key"] is None

    # 3. Explicit request key is preserved on foreign origin
    req_user_key = TranscribeRequest(
        api_base="https://attacker.com/v1", api_key="sk-user-key"
    )
    res_user_key = transcribe_service.resolve_engine_settings(req_user_key, config)
    assert res_user_key["api_key"] == "sk-user-key"


async def test_discover_models_pins_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def _ssrf_check(url: str | None) -> Any:
        return SimpleNamespace(allowed=True, resolved_ip="192.168.1.100")

    class _CaptureClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured["transport"] = kwargs.get("transport")

        async def __aenter__(self) -> _CaptureClient:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def get(self, url: str, headers: dict | None = None) -> Any:
            captured.setdefault("urls", []).append(url)
            captured.setdefault("headers", []).append(headers)

            class _Resp:
                status_code = 200

                def json(self) -> dict[str, Any]:
                    return {"data": [{"id": "discovered-model"}]}

            return _Resp()

    monkeypatch.setattr(config_store, "is_ssrf_target", _ssrf_check)
    monkeypatch.setattr(config_store.httpx, "AsyncClient", _CaptureClient)

    # 1. HTTPS uses pinned transport
    models = await transcribe_service.discover_transcription_models(
        "https://api.openai.com/v1", None
    )
    assert models == ["discovered-model"]
    assert captured["transport"] is not None

    # 2. Plain HTTP rewrites URL with resolved IP
    captured.clear()
    models = await transcribe_service.discover_transcription_models(
        "http://my-host.internal:8000/v1", None
    )
    assert models == ["discovered-model"]
    assert any("192.168.1.100:8000" in u for u in captured["urls"])
    assert captured["headers"][0].get("Host") == "my-host.internal"
