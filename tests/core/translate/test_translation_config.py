"""Tests for the translation_config boundary."""

from __future__ import annotations

import logging

import pytest

from omniscribe.core.translate.config import (
    DEFAULT_TRANSLATION_ACCEPTANCE_SCORE,
    DEFAULT_TRANSLATION_API_BASE,
    DEFAULT_TRANSLATION_API_KEY,
    DEFAULT_TRANSLATION_MAX_ATTEMPTS,
    DEFAULT_TRANSLATION_MAX_LENGTH_RATIO,
    DEFAULT_TRANSLATION_MAX_TOKENS,
    DEFAULT_TRANSLATION_MIN_LENGTH_RATIO,
    DEFAULT_TRANSLATION_MODEL,
    DEFAULT_TRANSLATION_ENTITY_MEMORY_CAP,
    DEFAULT_TRANSLATION_EVALUATE,
    DEFAULT_TRANSLATION_LEXICON_MIN_SCORE,
    DEFAULT_TRANSLATION_LEXICON_RESULT_COUNT,
    TranslationSettings,
)


def test_translation_settings_defaults() -> None:
    settings = TranslationSettings()
    assert settings.api_base == DEFAULT_TRANSLATION_API_BASE
    assert settings.api_key == DEFAULT_TRANSLATION_API_KEY
    assert settings.model == DEFAULT_TRANSLATION_MODEL
    assert settings.max_attempts == DEFAULT_TRANSLATION_MAX_ATTEMPTS
    assert settings.min_length_ratio == DEFAULT_TRANSLATION_MIN_LENGTH_RATIO
    assert settings.max_length_ratio == DEFAULT_TRANSLATION_MAX_LENGTH_RATIO
    assert settings.acceptance_score == DEFAULT_TRANSLATION_ACCEPTANCE_SCORE


def test_translation_settings_from_env_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LLM_API_BASE", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("OMNISCRIBE_TRANSLATION_MAX_ATTEMPTS", raising=False)
    monkeypatch.delenv("OMNISCRIBE_TRANSLATION_MIN_LENGTH_RATIO", raising=False)
    monkeypatch.delenv("OMNISCRIBE_TRANSLATION_MAX_LENGTH_RATIO", raising=False)
    monkeypatch.delenv("OMNISCRIBE_TRANSLATION_ACCEPTANCE_SCORE", raising=False)

    settings = TranslationSettings.from_env()
    assert settings.api_base == DEFAULT_TRANSLATION_API_BASE
    assert settings.api_key == DEFAULT_TRANSLATION_API_KEY
    assert settings.model == DEFAULT_TRANSLATION_MODEL
    assert settings.max_attempts == DEFAULT_TRANSLATION_MAX_ATTEMPTS
    assert settings.min_length_ratio == DEFAULT_TRANSLATION_MIN_LENGTH_RATIO
    assert settings.max_length_ratio == DEFAULT_TRANSLATION_MAX_LENGTH_RATIO
    assert settings.acceptance_score == DEFAULT_TRANSLATION_ACCEPTANCE_SCORE


def test_translation_settings_from_env_custom(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_BASE", "http://custom/v1")
    monkeypatch.setenv("LLM_API_KEY", "custom-key")
    monkeypatch.setenv("LLM_MODEL", "custom-model")
    monkeypatch.setenv("OMNISCRIBE_TRANSLATION_MAX_ATTEMPTS", "5")
    monkeypatch.setenv("OMNISCRIBE_TRANSLATION_MIN_LENGTH_RATIO", "0.25")
    monkeypatch.setenv("OMNISCRIBE_TRANSLATION_MAX_LENGTH_RATIO", "3.0")
    monkeypatch.setenv("OMNISCRIBE_TRANSLATION_ACCEPTANCE_SCORE", "0.9")

    settings = TranslationSettings.from_env()
    assert settings.api_base == "http://custom/v1"
    assert settings.api_key == "custom-key"
    assert settings.model == "custom-model"
    assert settings.max_attempts == 5
    assert settings.min_length_ratio == 0.25
    assert settings.max_length_ratio == 3.0
    assert settings.acceptance_score == 0.9


def test_translation_settings_from_env_invalid_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Invalid env values must not crash at import time; we fall back to defaults.
    monkeypatch.setenv("OMNISCRIBE_TRANSLATION_MAX_ATTEMPTS", "not-a-number")
    monkeypatch.setenv("OMNISCRIBE_TRANSLATION_MIN_LENGTH_RATIO", "abc")
    monkeypatch.setenv("OMNISCRIBE_TRANSLATION_MAX_LENGTH_RATIO", "0.9")  # below 1.0
    monkeypatch.setenv("OMNISCRIBE_TRANSLATION_ACCEPTANCE_SCORE", "2.5")  # out of range
    monkeypatch.setenv("OMNISCRIBE_TRANSLATION_MAX_ATTEMPTS", "0")  # below minimum

    settings = TranslationSettings.from_env()
    assert settings.max_attempts == DEFAULT_TRANSLATION_MAX_ATTEMPTS
    assert settings.min_length_ratio == DEFAULT_TRANSLATION_MIN_LENGTH_RATIO
    assert settings.max_length_ratio == DEFAULT_TRANSLATION_MAX_LENGTH_RATIO
    assert settings.acceptance_score == DEFAULT_TRANSLATION_ACCEPTANCE_SCORE


def test_translation_settings_from_mapping_defaults() -> None:
    settings = TranslationSettings.from_mapping({})
    assert settings.api_base == DEFAULT_TRANSLATION_API_BASE
    assert settings.api_key == DEFAULT_TRANSLATION_API_KEY
    assert settings.model == DEFAULT_TRANSLATION_MODEL
    assert settings.max_attempts == DEFAULT_TRANSLATION_MAX_ATTEMPTS
    assert settings.min_length_ratio == DEFAULT_TRANSLATION_MIN_LENGTH_RATIO
    assert settings.max_length_ratio == DEFAULT_TRANSLATION_MAX_LENGTH_RATIO
    assert settings.acceptance_score == DEFAULT_TRANSLATION_ACCEPTANCE_SCORE


def test_translation_settings_from_mapping_custom() -> None:
    settings = TranslationSettings.from_mapping(
        {
            "api_base": "http://mapping/v1",
            "api_key": "mapping-key",
            "model": "mapping-model",
            "max_attempts": 4,
            "min_length_ratio": 0.2,
            "max_length_ratio": 2.0,
            "acceptance_score": 0.85,
        }
    )
    assert settings.api_base == "http://mapping/v1"
    assert settings.api_key == "mapping-key"
    assert settings.model == "mapping-model"
    assert settings.max_attempts == 4
    assert settings.min_length_ratio == 0.2
    assert settings.max_length_ratio == 2.0
    assert settings.acceptance_score == 0.85


def test_translation_settings_new_fields_defaults() -> None:
    settings = TranslationSettings()
    assert settings.lexicon_result_count == DEFAULT_TRANSLATION_LEXICON_RESULT_COUNT
    assert settings.lexicon_min_score == DEFAULT_TRANSLATION_LEXICON_MIN_SCORE
    assert settings.evaluate_enabled is DEFAULT_TRANSLATION_EVALUATE
    assert settings.max_tokens == DEFAULT_TRANSLATION_MAX_TOKENS
    assert settings.entity_memory_cap == DEFAULT_TRANSLATION_ENTITY_MEMORY_CAP


def test_translation_settings_new_fields_env_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OMNISCRIBE_TRANSLATION_EVALUATE", "false")
    monkeypatch.setenv("OMNISCRIBE_TRANSLATION_LEXICON_RESULT_COUNT", "5")
    monkeypatch.setenv("OMNISCRIBE_TRANSLATION_LEXICON_MIN_SCORE", "0.5")
    monkeypatch.setenv("OMNISCRIBE_TRANSLATION_MAX_TOKENS", "4096")
    monkeypatch.setenv("OMNISCRIBE_TRANSLATION_ENTITY_MEMORY_CAP", "10")
    settings = TranslationSettings.from_env()
    assert settings.evaluate_enabled is False
    assert settings.lexicon_result_count == 5
    assert settings.lexicon_min_score == 0.5
    assert settings.max_tokens == 4096
    assert settings.entity_memory_cap == 10


def test_translation_settings_bool_env_variants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for raw, expected in [("1", True), ("yes", True), ("0", False), ("off", False)]:
        monkeypatch.setenv("OMNISCRIBE_TRANSLATION_EVALUATE", raw)
        assert TranslationSettings.from_env().evaluate_enabled is expected


def test_translation_settings_invalid_bool_env_warns_and_falls_back(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("OMNISCRIBE_TRANSLATION_EVALUATE", "maybe")
    with caplog.at_level(logging.WARNING, logger="omniscribe.core.translate.config"):
        settings = TranslationSettings.from_env()
    assert settings.evaluate_enabled is True
    assert "OMNISCRIBE_TRANSLATION_EVALUATE" in caplog.text


def test_translation_settings_invalid_int_env_warns(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("OMNISCRIBE_TRANSLATION_MAX_ATTEMPTS", "notanint")
    with caplog.at_level(logging.WARNING, logger="omniscribe.core.translate.config"):
        settings = TranslationSettings.from_env()
    assert settings.max_attempts == DEFAULT_TRANSLATION_MAX_ATTEMPTS
    assert "OMNISCRIBE_TRANSLATION_MAX_ATTEMPTS" in caplog.text


def test_translation_settings_new_fields_validation() -> None:
    with pytest.raises(ValueError, match="lexicon_result_count must be >= 1"):
        TranslationSettings(lexicon_result_count=0)
    with pytest.raises(ValueError, match="lexicon_result_count must be an integer"):
        TranslationSettings(lexicon_result_count=True)
    with pytest.raises(ValueError, match="max_tokens must be >= 1"):
        TranslationSettings(max_tokens=0)
    with pytest.raises(ValueError, match="entity_memory_cap must be an integer"):
        TranslationSettings(entity_memory_cap=False)
    with pytest.raises(
        ValueError, match="lexicon_min_score must be between 0.0 and 1.0"
    ):
        TranslationSettings(lexicon_min_score=1.5)


def test_translation_settings_from_mapping_new_fields() -> None:
    settings = TranslationSettings.from_mapping(
        {
            "lexicon_result_count": 7,
            "lexicon_min_score": 0.4,
            "evaluate_enabled": False,
            "max_tokens": 1024,
            "entity_memory_cap": 5,
        }
    )
    assert settings.lexicon_result_count == 7
    assert settings.lexicon_min_score == 0.4
    assert settings.evaluate_enabled is False
    assert settings.max_tokens == 1024
    assert settings.entity_memory_cap == 5


def test_translation_settings_post_init_validation() -> None:
    # Test empty strings
    with pytest.raises(ValueError, match="api_base must be a non-empty string"):
        TranslationSettings(api_base="   ")

    with pytest.raises(ValueError, match="api_key must be a non-empty string"):
        TranslationSettings(api_key="")

    with pytest.raises(ValueError, match="model must be a non-empty string"):
        TranslationSettings(model="")

    # Test non-strings (though type hints suggest str, people might pass other types)
    with pytest.raises(ValueError, match="api_base must be a non-empty string"):
        TranslationSettings(api_base=123)  # type: ignore

    # Tunable validation: types and ranges
    with pytest.raises(ValueError, match="max_attempts must be an integer"):
        TranslationSettings(max_attempts="3")  # type: ignore

    with pytest.raises(ValueError, match="max_attempts must be >= 1"):
        TranslationSettings(max_attempts=0)

    with pytest.raises(ValueError, match=r"min_length_ratio must be a number"):
        TranslationSettings(min_length_ratio="0.5")  # type: ignore

    with pytest.raises(
        ValueError, match=r"min_length_ratio must be between 0\.0 and 1\.0"
    ):
        TranslationSettings(min_length_ratio=1.5)

    with pytest.raises(ValueError, match=r"max_length_ratio must be a number"):
        TranslationSettings(max_length_ratio="2.5")  # type: ignore

    with pytest.raises(ValueError, match=r"max_length_ratio must be >= 1\.0"):
        TranslationSettings(max_length_ratio=0.5)

    with pytest.raises(ValueError, match="max_length_ratio must be a number"):
        TranslationSettings(max_length_ratio=False)

    with pytest.raises(ValueError, match=r"acceptance_score must be a number"):
        TranslationSettings(acceptance_score=None)  # type: ignore

    with pytest.raises(
        ValueError, match=r"acceptance_score must be between 0\.0 and 1\.0"
    ):
        TranslationSettings(acceptance_score=-0.1)

    # bool is a subclass of int in Python — guard against it passing as 1.0/0 silently.
    with pytest.raises(ValueError, match="max_attempts must be an integer"):
        TranslationSettings(max_attempts=True)

    with pytest.raises(ValueError, match="min_length_ratio must be a number"):
        TranslationSettings(min_length_ratio=False)


def test_translation_settings_from_mapping_validation() -> None:
    with pytest.raises(ValueError, match="api_base must be a string"):
        TranslationSettings.from_mapping({"api_base": 123})

    with pytest.raises(ValueError, match="api_key must be a string"):
        TranslationSettings.from_mapping({"api_key": None})

    with pytest.raises(ValueError, match="model must be a string"):
        TranslationSettings.from_mapping({"model": []})

    with pytest.raises(ValueError, match="max_attempts must be an integer"):
        TranslationSettings.from_mapping({"max_attempts": "3"})

    with pytest.raises(ValueError, match="min_length_ratio must be a number"):
        TranslationSettings.from_mapping({"min_length_ratio": "0.5"})

    with pytest.raises(ValueError, match="max_length_ratio must be a number"):
        TranslationSettings.from_mapping({"max_length_ratio": []})

    with pytest.raises(ValueError, match="acceptance_score must be a number"):
        TranslationSettings.from_mapping({"acceptance_score": []})
