"""Tests for script-aware length-ratio bands."""

from __future__ import annotations

import pytest

from omniscribe.core.translate.length_bands import (
    DEFAULT_MAX_RATIO,
    DEFAULT_MIN_RATIO,
    effective_band,
)


def test_same_script_uses_defaults() -> None:
    lo, hi = effective_band("Hello world, this is text.", "Hola mundo, esto es texto.")
    assert (lo, hi) == (DEFAULT_MIN_RATIO, DEFAULT_MAX_RATIO)


def test_cjk_target_shrinks_upper_bound() -> None:
    src = "Hello world, this is a longer English source paragraph."
    tgt = "こんにちは世界、これは日本語の段落です。"
    lo, hi = effective_band(src, tgt)
    assert hi < DEFAULT_MAX_RATIO
    assert lo <= DEFAULT_MIN_RATIO


def test_cjk_source_expands_upper_bound() -> None:
    src = "こんにちは世界、これは日本語の段落です。"
    tgt = "Hello world, this is a longer English translation paragraph."
    _lo, hi = effective_band(src, tgt)
    assert hi > DEFAULT_MAX_RATIO


@pytest.mark.parametrize("empty", ["", "   "])
def test_empty_input_falls_back_to_defaults(empty: str) -> None:
    assert effective_band(empty, "x") == (DEFAULT_MIN_RATIO, DEFAULT_MAX_RATIO)
    assert effective_band("x", empty) == (DEFAULT_MIN_RATIO, DEFAULT_MAX_RATIO)


def test_cjk_to_cjk_uses_defaults() -> None:
    src = "東京タワーは高い。"
    tgt = "東京スカイツリーも高い。"
    assert effective_band(src, tgt) == (DEFAULT_MIN_RATIO, DEFAULT_MAX_RATIO)
