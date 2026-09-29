"""Unit tests for the glossary plugin schemas (verbatim old-contract pins)."""

from __future__ import annotations

import pydantic
import pytest

from omniscribe.plugins.glossary.schemas import (
    GlossaryFormat,
    GlossaryImportJobResponse,
    GlossaryImportRequest,
    GlossaryImportSource,
    GlossaryReorderRequest,
    GlossaryUrlImportBody,
)


def test_format_enum_vocabulary() -> None:
    assert {member.value for member in GlossaryFormat} == {
        "csv",
        "tsv",
        "xliff",
        "tbx",
        "tmx",
        "git_glossary",
        "sql_table",
        "json_pairs",
        "lanes_sqlite",
        "lanes_xml",
    }


def test_import_source_defaults_match_old_contract() -> None:
    source = GlossaryImportSource(format=GlossaryFormat.CSV)
    assert source.git_ref == "HEAD"
    assert source.git_path == "GLOSSARY.md"
    assert source.sql_source_col == "source"
    assert source.sql_target_col == "target"
    assert source.max_entries is None
    assert source.name is None


def test_import_source_strips_optional_strings() -> None:
    source = GlossaryImportSource(
        format=GlossaryFormat.CSV, name="  Pairs  ", encoding=" utf-8 "
    )
    assert source.name == "Pairs"
    assert source.encoding == "utf-8"


def test_import_source_rejects_unknown_fields() -> None:
    with pytest.raises(pydantic.ValidationError):
        GlossaryImportSource.model_validate({"format": "csv", "mystery": 1})


def test_max_entries_bounds() -> None:
    with pytest.raises(pydantic.ValidationError):
        GlossaryImportSource(format=GlossaryFormat.CSV, max_entries=0)
    with pytest.raises(pydantic.ValidationError):
        GlossaryImportSource(format=GlossaryFormat.CSV, max_entries=1_000_001)
    assert (
        GlossaryImportSource(format=GlossaryFormat.CSV, max_entries=1).max_entries == 1
    )
    assert (
        GlossaryImportSource(
            format=GlossaryFormat.CSV, max_entries=1_000_000
        ).max_entries
        == 1_000_000
    )


def test_import_job_response_shape() -> None:
    sync = GlossaryImportJobResponse(
        glossary_id="g1",
        format=GlossaryFormat.JSON_PAIRS,
        name="N",
        entry_count=1,
        warnings=[],
        queued=False,
    )
    assert sync.job_id is None
    queued = GlossaryImportJobResponse(
        job_id="j-1",
        format=GlossaryFormat.CSV,
        name="N",
        entry_count=0,
        warnings=[],
        queued=True,
    )
    assert queued.glossary_id is None


def test_reorder_request_bounds() -> None:
    with pytest.raises(pydantic.ValidationError):
        GlossaryReorderRequest(ordered_ids=[str(i) for i in range(201)])
    assert (
        len(
            GlossaryReorderRequest(ordered_ids=[str(i) for i in range(200)]).ordered_ids
        )
        == 200
    )


def test_url_import_body_accepts_and_coerces() -> None:
    body = GlossaryUrlImportBody(url="  http://example.com/g.csv  ")
    assert body.url == "http://example.com/g.csv"
    assert body.format is None
    assert body.name is None
    coerced = GlossaryUrlImportBody.model_validate(
        {"url": "http://x/tbx", "format": "tbx"}
    )
    assert coerced.format is GlossaryFormat.TBX


def test_url_import_body_rejects_blank_and_unknown() -> None:
    with pytest.raises(pydantic.ValidationError):
        GlossaryUrlImportBody(url="   ")
    with pytest.raises(pydantic.ValidationError):
        GlossaryUrlImportBody.model_validate({"url": "u", "mystery": 1})


def test_import_request_strips_channel_fields() -> None:
    req = GlossaryImportRequest.model_validate(
        {
            "source": {"format": "csv"},
            "channel_id": " ch-1 ",
            "session_token": " tok ",
        }
    )
    assert req.channel_id == "ch-1"
    assert req.session_token == "tok"
