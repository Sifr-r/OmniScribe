"""Shared pydantic base models for the plugin request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator


class TrimmedModel(BaseModel):
    """Shared config: reject unknown fields, trim string values."""

    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def _strip_strings(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


__all__ = ["TrimmedModel"]
