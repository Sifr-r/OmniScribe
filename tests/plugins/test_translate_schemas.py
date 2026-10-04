"""Unit tests for translate plugin request schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from omniscribe.plugins.translate.schemas import (
    AsyncTranslationRequest,
    NllbRequest,
    TranslationRequest,
)


def test_translation_request_defaults() -> None:
    body = TranslationRequest()
    assert body.text == ""
    assert body.text_artifact_id is None
    assert body.text_artifact_token is None
    assert body.target_language == "Spanish"
    assert body.glossary is None
    assert body.glossary_text is None
    assert body.sliding_window_words == 80
    assert body.dual_translate is False
    assert body.second_api_base is None
    assert body.second_api_key is None
    assert body.second_model is None
    assert body.api_base is None
    assert body.api_key is None
    assert body.model is None


def test_translation_request_rejects_extra_fields() -> None:
    # The old contract was extra="forbid"; the client never sends
    # prompt_template/channel_id (notifier leaves them None).
    with pytest.raises(ValidationError):
        TranslationRequest(text="x", prompt_template="y")  # type: ignore[call-arg]


def test_target_language_bounds() -> None:
    assert TranslationRequest(target_language="French").target_language == "French"
    with pytest.raises(ValidationError):
        TranslationRequest(target_language="")


def test_target_language_max_length() -> None:
    with pytest.raises(ValidationError):
        TranslationRequest(target_language="x" * 81)


def test_sliding_window_words_bounds() -> None:
    assert TranslationRequest(sliding_window_words=0).sliding_window_words == 0
    assert TranslationRequest(sliding_window_words=2000).sliding_window_words == 2000
    with pytest.raises(ValidationError):
        TranslationRequest(sliding_window_words=2001)
    with pytest.raises(ValidationError):
        TranslationRequest(sliding_window_words=-1)


def test_glossary_max_entries() -> None:
    entries = [{"source": "a", "target": "b"}] * 1000
    assert TranslationRequest(glossary=entries).glossary == entries
    with pytest.raises(ValidationError):
        TranslationRequest(glossary=entries + entries)


def test_strings_are_trimmed() -> None:
    body = TranslationRequest(text="  hello  ", api_base="  http://x  ")
    assert body.text == "hello"
    assert body.api_base == "http://x"


def test_async_request_bounds_artifact_pair_when_provided() -> None:
    body = AsyncTranslationRequest()
    assert body.text_artifact_id is None
    body = AsyncTranslationRequest(
        text_artifact_id="a" * 32, text_artifact_token="t" * 43
    )
    assert body.target_language == "English"
    assert body.channel_id is None
    with pytest.raises(ValidationError):
        AsyncTranslationRequest(text_artifact_id="short", text_artifact_token="t" * 43)
    with pytest.raises(ValidationError):
        AsyncTranslationRequest(text_artifact_id="a" * 32, text_artifact_token="t" * 31)
    with pytest.raises(ValidationError):
        AsyncTranslationRequest(
            text_artifact_id="a" * 32, text_artifact_token="t" * 257
        )


def test_async_request_accepts_text_and_channel_id_for_tolerance() -> None:
    # The client posts the same toJson for async as for sync; text and
    # channel_id are accepted and ignored (spec: tolerant superset).
    body = AsyncTranslationRequest(
        text="ignored",
        text_artifact_id="a" * 32,
        text_artifact_token="t" * 43,
        channel_id="ch-1",
    )
    assert body.text == "ignored"
    assert body.channel_id == "ch-1"


def test_nllb_request_defaults() -> None:
    body = NllbRequest()
    assert body.text == ""
    assert body.target_language == "English"


def test_nllb_request_accepts_client_offered_targets() -> None:
    """The two labels that used to resolve to English now validate."""
    assert NllbRequest(text="hi", target_language="Korean").target_language == "Korean"
    assert (
        NllbRequest(text="hi", target_language="Chinese (Simplified)").target_language
        == "Chinese (Simplified)"
    )
    # Raw NLLB codes stay acceptable.
    assert (
        NllbRequest(text="hi", target_language="kor_Hang").target_language == "kor_Hang"
    )


def test_nllb_request_rejects_unknown_target() -> None:
    """Unknown targets produce an explicit validation error, not a fallback."""
    with pytest.raises(ValidationError) as excinfo:
        NllbRequest(text="hi", target_language="Klingon")
    assert "Klingon" in str(excinfo.value)


def test_llm_translation_request_is_not_language_constrained() -> None:
    """Only the NLLB route is constrained; the LLM path takes any name."""
    assert TranslationRequest(text="hi", target_language="Klingon").target_language == (
        "Klingon"
    )


def test_async_request_rejects_extra_fields() -> None:
    # extra="forbid" is inherited from _TrimmedModel.
    with pytest.raises(ValidationError):
        AsyncTranslationRequest(  # type: ignore[call-arg]
            text_artifact_id="a" * 32, text_artifact_token="t" * 43, bogus="1"
        )


def test_async_request_accepts_superset_fields() -> None:
    # Deliberate extension vs the old TreeTranslationRequest: the client
    # posts the same toJson to both routes, so the async schema accepts
    # everything the sync schema does.
    body = AsyncTranslationRequest(
        text_artifact_id="a" * 32,
        text_artifact_token="t" * 43,
        glossary_text="en foo fr bar",
        sliding_window_words=120,
        dual_translate=True,
        second_model="m2",
    )
    assert body.glossary_text == "en foo fr bar"
    assert body.sliding_window_words == 120
    assert body.dual_translate is True
    assert body.second_model == "m2"


def test_target_language_whitespace_only_rejected() -> None:
    # Trim validator runs before min_length — whitespace-only collapses
    # to "" and fails min_length=1.
    with pytest.raises(ValidationError):
        TranslationRequest(target_language="   ")
