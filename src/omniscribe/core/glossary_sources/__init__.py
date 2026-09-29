"""Glossary parsers for common exchange and data-source formats."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from . import csv_tsv, git_repo, json_pairs, lanes_lexicon, sql_table, tbx, tmx, xliff
from .csv_tsv import parse_csv, parse_csv_tsv, parse_tsv
from .encoding import decode_bytes, detect_encoding, read_text_auto_detect
from .git_repo import parse_git_glossary
from .json_pairs import parse_json_pairs
from .lanes_lexicon import parse_lanes_lexicon_sqlite, parse_lanes_lexicon_xml
from .sql_table import parse_sql_table
from .summary import FormatNotAvailableError, GlossaryImportSummary, redact_dsn
from .tbx import parse_tbx
from .tmx import parse_tmx
from .xliff import parse_xliff

__all__ = [
    "FormatNotAvailableError",
    "GlossaryImportSummary",
    "decode_bytes",
    "detect_encoding",
    "parse",
    "parse_csv",
    "parse_csv_tsv",
    "parse_git_glossary",
    "parse_json_pairs",
    "parse_lanes_lexicon_sqlite",
    "parse_lanes_lexicon_xml",
    "parse_sql_table",
    "parse_tbx",
    "parse_tmx",
    "parse_tsv",
    "parse_xliff",
    "read_text_auto_detect",
    "redact_dsn",
]

PARSERS: dict[str, str] = {
    "csv": "csv_tsv.parse_csv",
    "tsv": "csv_tsv.parse_tsv",
    "xliff": "xliff.parse_xliff",
    "tbx": "tbx.parse_tbx",
    "tmx": "tmx.parse_tmx",
    "git_glossary": "git_repo.parse_git_glossary",
    "sql_table": "sql_table.parse_sql_table",
    "json_pairs": "json_pairs.parse_json_pairs",
    "lanes_sqlite": "lanes_lexicon.parse_lanes_lexicon_sqlite",
    "lanes_xml": "lanes_lexicon.parse_lanes_lexicon_xml",
}

_FORMAT_MODULES = {
    "csv_tsv": csv_tsv,
    "xliff": xliff,
    "tbx": tbx,
    "tmx": tmx,
    "git_repo": git_repo,
    "sql_table": sql_table,
    "json_pairs": json_pairs,
    "lanes_lexicon": lanes_lexicon,
}


class GlossaryImportLimitError(ValueError):
    """Raised when a source exceeds the caller's entry limit."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__(f"Too many entries (maximum is {limit:,}).")


Parser = Callable[..., GlossaryImportSummary]


def parse(format: str, **kwargs: Any) -> GlossaryImportSummary:
    """Dispatch a normalized source format to its parser."""
    format_name = str(format).strip().lower()
    if format_name not in PARSERS:
        raise ValueError(f"Unsupported glossary format: {format_name or '<empty>'}.")

    source_uri = kwargs.pop("source_uri", None)
    max_entries = kwargs.pop("max_entries", None)
    parser = _parser_for(format_name)
    parser_kwargs = dict(kwargs)
    if format_name in {"csv", "tsv", "xliff", "tbx", "tmx", "json_pairs"}:
        data = parser_kwargs.pop("data", None)
        if data is None:
            data = parser_kwargs.pop("text", None)
        if data is None:
            raise ValueError("Glossary source data is required.")
        summary = parser(data, **parser_kwargs)
    elif format_name in {"lanes_sqlite", "lanes_xml"}:
        # ``max_entries`` is mapped to the parser's ``limit`` kwarg here so
        # callers get a uniform ``max_entries`` knob. Other locals-only
        # formats (sql_table, git_glossary) keep their own semantics.
        if "limit" not in parser_kwargs and max_entries is not None:
            parser_kwargs["limit"] = max_entries
        summary = parser(**parser_kwargs)
    else:
        summary = parser(**parser_kwargs)

    if source_uri is not None or summary.source_uri is not None:
        summary = replace(summary, source_uri=source_uri or summary.source_uri)
    summary = replace(summary, format=format_name)
    if max_entries is not None:
        try:
            limit = int(max_entries)
        except (TypeError, ValueError) as exc:
            raise ValueError("max_entries must be an integer.") from exc
        if limit < 1 or limit > 1_000_000:
            raise ValueError("max_entries must be between 1 and 1,000,000.")
        if len(summary.entries) > limit:
            raise GlossaryImportLimitError(limit)
    return summary


def _parser_for(format_name: str) -> Parser:
    module_name, function_name = PARSERS[format_name].split(".", 1)
    module = _FORMAT_MODULES[module_name]
    return getattr(module, function_name)  # type: ignore[no-any-return]
