"""Tests for typed env helpers in :mod:`omniscribe.utils.env`.

The dotted-string ``.env`` loader lives in the ``dotenv`` package itself
(``server.py`` and ``core/ocr/processor.py`` import it directly); the
homegrown reimplementation was removed in the fat-trim PR.
"""

from __future__ import annotations

import pytest


def test_canonical_boolean_sets() -> None:
    from omniscribe.utils.env import DISABLE_STRINGS, ENABLE_STRINGS

    assert isinstance(ENABLE_STRINGS, frozenset)
    assert isinstance(DISABLE_STRINGS, frozenset)
    assert {"1", "true", "yes", "on", "y", "enabled"} == ENABLE_STRINGS
    assert {"0", "false", "no", "off", "n", "disabled"} == DISABLE_STRINGS
    assert ENABLE_STRINGS.isdisjoint(DISABLE_STRINGS)


def test_parse_bool() -> None:
    from omniscribe.utils.env import parse_bool

    # Boolean passthrough
    assert parse_bool(True) is True
    assert parse_bool(False) is False

    # None falls back to default
    assert parse_bool(None) is False
    assert parse_bool(None, default=True) is True

    # Truthy strings
    for truthy in (
        "1",
        "true",
        "yes",
        "on",
        "y",
        "enabled",
        "TRUE",
        " Enabled ",
        "Yes",
    ):
        assert parse_bool(truthy) is True
        assert parse_bool(truthy, default=False) is True

    # Falsy strings
    for falsy in (
        "0",
        "false",
        "no",
        "off",
        "n",
        "disabled",
        "FALSE",
        " Disabled ",
        "No",
    ):
        assert parse_bool(falsy) is False
        assert parse_bool(falsy, default=True) is False

    # Unknown strings fall back to default
    assert parse_bool("banana", default=False) is False
    assert parse_bool("banana", default=True) is True
    assert parse_bool("", default=False) is False
    assert parse_bool("", default=True) is True


def test_env_bool(monkeypatch: pytest.MonkeyPatch) -> None:
    from omniscribe.utils.env import env_bool

    # Unset falls back to default
    monkeypatch.delenv("TEST_BOOL_FLAG", raising=False)
    assert env_bool("TEST_BOOL_FLAG", default=False) is False
    assert env_bool("TEST_BOOL_FLAG", default=True) is True

    # Truthy env vars
    for truthy in ("1", "true", "yes", "on", "y", "enabled", "TRUE"):
        monkeypatch.setenv("TEST_BOOL_FLAG", truthy)
        assert env_bool("TEST_BOOL_FLAG", default=False) is True

    # Falsy env vars
    for falsy in ("0", "false", "no", "off", "n", "disabled", "OFF"):
        monkeypatch.setenv("TEST_BOOL_FLAG", falsy)
        assert env_bool("TEST_BOOL_FLAG", default=True) is False

    # Unknown string falls back to default
    monkeypatch.setenv("TEST_BOOL_FLAG", "invalid_value")
    assert env_bool("TEST_BOOL_FLAG", default=False) is False
    assert env_bool("TEST_BOOL_FLAG", default=True) is True


def test_env_bool_logs_warning_on_invalid_value(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    from omniscribe.utils.env import env_bool

    monkeypatch.setenv("TEST_BOOL_FLAG", "invalid_value")
    with caplog.at_level(logging.WARNING, logger="omniscribe.utils.env"):
        result = env_bool("TEST_BOOL_FLAG", default=True)
    assert result is True
    assert "Ignoring invalid boolean environment value for TEST_BOOL_FLAG" in caplog.text


def test_env_str_vs_env_list_csv_empty_semantics(monkeypatch: pytest.MonkeyPatch) -> None:
    from omniscribe.utils.env import env_list_csv, env_str

    # Unset
    monkeypatch.delenv("EMPTY_VAR", raising=False)
    assert env_str("EMPTY_VAR") is None
    assert env_list_csv("EMPTY_VAR") == []

    # Empty string
    monkeypatch.setenv("EMPTY_VAR", "")
    assert env_str("EMPTY_VAR") is None
    assert env_list_csv("EMPTY_VAR") == []

    # Whitespace-only string
    monkeypatch.setenv("EMPTY_VAR", "   ")
    assert env_str("EMPTY_VAR") is None
    assert env_list_csv("EMPTY_VAR") == []
