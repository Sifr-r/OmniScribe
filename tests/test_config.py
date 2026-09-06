from __future__ import annotations

import pytest

from omniscribe.config import (
    DEFAULT_GROUNDED_MODEL,
    RuntimeSettings,
    load_settings,
)


def test_default_grounded_model_constant(monkeypatch: pytest.MonkeyPatch) -> None:
    assert DEFAULT_GROUNDED_MODEL == "qwen/qwen3-vl-8b"
    assert (
        RuntimeSettings.model_fields["grounded_model"].default == DEFAULT_GROUNDED_MODEL
    )
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("OMNISCRIBE_GROUNDED_MODEL", raising=False)
    settings = RuntimeSettings()
    assert settings.grounded_model == DEFAULT_GROUNDED_MODEL


def test_inherit_llm_model_for_grounded_inherits_when_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_MODEL", "custom/vision-model")
    monkeypatch.delenv("OMNISCRIBE_GROUNDED_MODEL", raising=False)

    settings = RuntimeSettings()
    assert settings.grounded_model == "custom/vision-model"


def test_inherit_llm_model_for_grounded_explicit_override_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_MODEL", "custom/vision-model")
    monkeypatch.setenv("OMNISCRIBE_GROUNDED_MODEL", "explicit/grounded-model")

    settings = RuntimeSettings()
    assert settings.grounded_model == "explicit/grounded-model"

    # Explicit kwarg also wins
    kwarg_settings = RuntimeSettings(grounded_model="kwarg/grounded-model")
    assert kwarg_settings.grounded_model == "kwarg/grounded-model"


def test_inherit_llm_model_for_grounded_no_env_retains_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("OMNISCRIBE_GROUNDED_MODEL", raising=False)

    settings = RuntimeSettings()
    assert settings.grounded_model == DEFAULT_GROUNDED_MODEL


def test_normalize_non_positive_rate_limit() -> None:
    # Positive rate limit
    s_pos = RuntimeSettings(rate_limit_per_min=60)
    assert s_pos.rate_limit_per_min == 60

    # 0 disables rate limiting -> None
    s_zero = RuntimeSettings(rate_limit_per_min=0)
    assert s_zero.rate_limit_per_min is None

    # Negative rate limit disables rate limiting -> None
    s_neg = RuntimeSettings(rate_limit_per_min=-10)
    assert s_neg.rate_limit_per_min is None

    # String representations
    s_str_zero = RuntimeSettings(rate_limit_per_min="0")  # type: ignore[arg-type]
    assert s_str_zero.rate_limit_per_min is None

    s_str_neg = RuntimeSettings(rate_limit_per_min="-5")  # type: ignore[arg-type]
    assert s_str_neg.rate_limit_per_min is None

    s_str_pos = RuntimeSettings(rate_limit_per_min="100")  # type: ignore[arg-type]
    assert s_str_pos.rate_limit_per_min == 100


def test_cors_origins_defaults_to_empty_list() -> None:
    settings = RuntimeSettings()
    assert settings.cors_origins == []
    assert settings.cors_origins_raw is None


def test_cors_origins_from_csv_string() -> None:
    settings = RuntimeSettings(cors_origins="http://a.com, http://b.com")  # type: ignore[arg-type]
    assert settings.cors_origins == ["http://a.com", "http://b.com"]
    assert settings.cors_origins_raw == "http://a.com,http://b.com"


def test_cors_origins_from_list() -> None:
    settings = RuntimeSettings(cors_origins=["http://a.com", " http://b.com ", ""])
    assert settings.cors_origins == ["http://a.com", "http://b.com"]


def test_cors_origins_from_cors_origins_raw_alias() -> None:
    # ``cors_origins_raw`` is now a read-only property (audit 4.1: prefer
    # the typed list field). The CSV-to-list parsing path is covered by
    # ``test_cors_origins_from_csv_string``; this test now exercises the
    # equivalent list-input path so the typed surface stays in sync.
    settings = RuntimeSettings(
        cors_origins=["http://allowed.example.com", "http://other.com"]
    )
    assert settings.cors_origins == ["http://allowed.example.com", "http://other.com"]
    assert settings.cors_origins_raw == "http://allowed.example.com,http://other.com"


def test_cors_origins_from_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNISCRIBE_CORS_ORIGINS", "http://env-a.com, http://env-b.com")
    settings = RuntimeSettings()
    assert settings.cors_origins == ["http://env-a.com", "http://env-b.com"]


def test_load_settings_overrides() -> None:
    settings = load_settings(
        rate_limit_per_min=15, cors_origins=["http://localhost:3000"]
    )
    assert settings.rate_limit_per_min == 15
    assert settings.cors_origins == ["http://localhost:3000"]


def test_max_upload_mb_invalid_value_falls_back_to_default() -> None:
    settings = RuntimeSettings(max_upload_mb="not-a-number")
    assert settings.max_upload_mb == 1_024


def test_max_upload_mb_valid_string_parses() -> None:
    settings = RuntimeSettings(max_upload_mb="2048")
    assert settings.max_upload_mb == 2048
