"""Unit tests for the transcribe plugin schemas."""

from __future__ import annotations

import pydantic
import pytest

from omniscribe.plugins.transcribe.schemas import (
    TranscribeRequest,
    TranscriptionConfigUpdate,
    TranscriptionEngineType,
)


def test_transcribe_request_defaults() -> None:
    req = TranscribeRequest()
    assert req.model is None
    assert req.engine is None
    assert req.api_base is None
    assert req.api_key is None
    assert req.language is None
    assert req.prompt is None
    assert req.temperature == 0.0
    assert req.channel_id is None


def test_transcribe_request_rejects_unknown_fields() -> None:
    with pytest.raises(pydantic.ValidationError):
        TranscribeRequest.model_validate({"bogus": "x"})


def test_transcribe_request_coerces_numeric_temperature() -> None:
    req = TranscribeRequest.model_validate({"temperature": "0.5"})
    assert req.temperature == 0.5


def test_transcribe_request_validates_engine() -> None:
    req = TranscribeRequest(engine="local")
    assert req.engine == "local"

    with pytest.raises(pydantic.ValidationError):
        TranscribeRequest(engine="locall")


def test_update_transcription_config_request_validates_engine() -> None:
    from omniscribe.plugins.transcribe.schemas import UpdateTranscriptionConfigRequest

    update = UpdateTranscriptionConfigRequest(engine=TranscriptionEngineType("local"))
    assert update.engine == "local"

    # Deliberately pass an invalid engine string to verify validation rejects it.
    with pytest.raises(pydantic.ValidationError):
        UpdateTranscriptionConfigRequest(engine="locall")  # type: ignore[arg-type]


def test_engine_enum_covers_factory_vocabulary() -> None:
    values = {member.value for member in TranscriptionEngineType}
    assert values == {
        "api",
        "whisper_api",
        "local",
        "whisper_local",
        "faster_whisper",
        "faster-whisper",
        "auto",
    }


def test_config_update_temperature_bounds() -> None:
    with pytest.raises(pydantic.ValidationError):
        TranscriptionConfigUpdate(temperature=2.5)
    update = TranscriptionConfigUpdate(temperature=1.5)
    assert update.temperature == 1.5


def test_config_update_strips_strings() -> None:
    update = TranscriptionConfigUpdate(model="  whisper-1  ", language=" en ")
    assert update.model == "whisper-1"
    assert update.language == "en"


def test_config_update_rejects_unknown_fields() -> None:
    with pytest.raises(pydantic.ValidationError):
        TranscriptionConfigUpdate.model_validate({"nope": 1})


def test_unpack_transcribe_options() -> None:
    from omniscribe.plugins.transcribe.schemas import unpack_transcribe_options

    req = TranscribeRequest(
        model="whisper-large-v3",
        engine="faster_whisper",
        api_base="https://api.example.com",
        api_key="secret",
        language="fr",
        prompt="Bonjour",
        temperature=0.7,
        channel_id="ch1",
    )
    unpacked = unpack_transcribe_options(req)
    assert unpacked == {
        "model": "whisper-large-v3",
        "engine": "faster_whisper",
        "api_base": "https://api.example.com",
        "api_key": "secret",
        "language": "fr",
        "prompt": "Bonjour",
        "temperature": 0.7,
        "channel_id": "ch1",
    }
