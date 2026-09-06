"""Request schemas for the translate plugin.

Field constraints reproduce the pre-harness contract (commit ``44ef123^``,
``api/schemas/requests.py``) so the existing Flutter client keeps working
without changes.
"""

from __future__ import annotations

from pydantic import Field

from omniscribe.plugins._schemas import TrimmedModel


class TranslationRequest(TrimmedModel):
    text: str = ""
    text_artifact_id: str | None = None
    text_artifact_token: str | None = None
    target_language: str = Field(default="Spanish", min_length=1, max_length=80)
    api_base: str | None = None
    api_key: str | None = None
    model: str | None = None
    glossary: list[dict] | None = Field(default=None, max_length=1000)
    glossary_text: str | None = None
    sliding_window_words: int = Field(default=80, ge=0, le=2000)
    dual_translate: bool = False
    second_api_base: str | None = None
    second_api_key: str | None = None
    second_model: str | None = None


class AsyncTranslationRequest(TranslationRequest):
    """Async (tree-aware) submission: artifact pair required at the route
    level (400 envelope), legacy defaults, ``text``/``channel_id``
    accepted and ignored."""

    text_artifact_id: str | None = Field(default=None, min_length=32, max_length=32)
    text_artifact_token: str | None = Field(default=None, min_length=32, max_length=256)
    target_language: str = Field(default="English", min_length=1, max_length=80)
    channel_id: str | None = None


class NllbRequest(TrimmedModel):
    text: str = ""
    target_language: str = "English"
