"""Glossary plugin schemas (verbatim re-homes from the pre-harness API).

`GlossaryUrlImportBody` is the Flutter client's JSON-body shape for
`POST /api/glossary/import/url` (the old surface used query params; the
rebuilt route accepts both).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _validate_optional_string(value: Any) -> Any:
    if value is None:
        return value
    if not isinstance(value, str):
        raise ValueError("must be a string")
    return value.strip()


class GlossaryFormat(StrEnum):
    CSV = "csv"
    TSV = "tsv"
    XLIFF = "xliff"
    TBX = "tbx"
    TMX = "tmx"
    GIT_GLOSSARY = "git_glossary"
    SQL_TABLE = "sql_table"
    JSON_PAIRS = "json_pairs"
    LANES_SQLITE = "lanes_sqlite"
    LANES_XML = "lanes_xml"


class GlossaryImportSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: GlossaryFormat
    text: str | None = None
    inline_bytes_b64: str | None = None
    url: str | None = None
    git_url: str | None = None
    git_ref: str | None = "HEAD"
    git_path: str | None = "GLOSSARY.md"
    git_credentials: str | None = None
    sql_dsn: str | None = None
    sql_source_table: str | None = None
    sql_target_table: str | None = None
    sql_source_col: str | None = "source"
    sql_target_col: str | None = "target"
    sql_where: str | None = None
    # Lane's Lexicon (Perseus/alpheios TEI XML and the SQLite snapshot of
    # the same corpus). Both ``lanes_*`` paths are local filesystem
    # paths only — the parser layer rejects URLs, env-vars, and remote
    # schemes (see ``omniscribe.core.glossary_sources.lanes_lexicon``).
    lanes_sqlite_path: str | None = Field(default=None, max_length=4096)
    lanes_xml_path: str | None = Field(default=None, max_length=4096)
    lanes_domain: str | None = Field(default=None, max_length=200)
    encoding: str | None = None
    max_entries: int | None = Field(default=None, ge=1, le=1_000_000)
    name: str | None = Field(default=None, max_length=200)

    @field_validator(
        "name",
        "encoding",
        "git_ref",
        "git_path",
        "lanes_sqlite_path",
        "lanes_xml_path",
        "lanes_domain",
        mode="before",
    )
    @classmethod
    def _strip_optional(cls, value: Any) -> Any:
        return _validate_optional_string(value)


class GlossaryImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: GlossaryImportSource
    channel_id: str | None = None
    session_token: str | None = None

    @field_validator("channel_id", "session_token", mode="before")
    @classmethod
    def _strip_optional(cls, value: Any) -> Any:
        return _validate_optional_string(value)


class GlossaryUrlImportBody(BaseModel):
    """Client JSON-body shape for the URL import route."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1)
    format: GlossaryFormat | None = None
    name: str | None = Field(default=None, max_length=200)
    encoding: str | None = None
    channel_id: str | None = None

    @field_validator("url", "name", "encoding", "channel_id", mode="before")
    @classmethod
    def _strip_optional(cls, value: Any) -> Any:
        return _validate_optional_string(value)


class GlossaryListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    format: GlossaryFormat
    source_uri: str | None = None
    encoding: str | None = None
    entry_count: int = Field(ge=0)
    enabled: bool = True
    priority: int = 0
    group: str = "default"


class GlossaryToggleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class GlossaryToggleBody(BaseModel):
    """Optional toggle body: if enabled is not provided, the route flips state."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None


class GlossaryReorderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ordered_ids: list[str] = Field(min_length=0, max_length=200)


class GlossaryPreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int = Field(ge=0)
    conflicts: list[dict[str, Any]] = Field(default_factory=list, max_length=1000)
    enabled_glossaries: list[str] = Field(default_factory=list, max_length=100)


class GlossaryImportJobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    glossary_id: str | None = None
    job_id: str | None = None
    format: GlossaryFormat
    name: str
    entry_count: int = Field(ge=0)
    warnings: list[str] = Field(default_factory=list, max_length=100)
    queued: bool = False
