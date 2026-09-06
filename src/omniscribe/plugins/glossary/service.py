"""Glossary import/library service (verbatim re-home + harness dispatch).

Kwarg builders, the entry-count estimate, and the default-name helper are
verbatim from `44ef123^:api/routers/glossary_imports.py`; only the error
type (`GlossaryError` instead of the old envelope exception classes) and
the store/queue seams changed. Async imports dispatch on the harness
JobQueue via the `GlossaryJobRunner` marker (third producer).

`omniscribe.core.lexicon` is NEVER imported at module top (pyarrow lives
in the optional `lexicon` extra — see store.py): `LexiconStore` is
TYPE_CHECKING-only, and the runtime names are imported function-level
after the `_library()` 503 guard in the methods that use them.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Protocol
from urllib.parse import urlparse

from omniscribe.core.glossary_sources import (
    FormatNotAvailableError,
    GlossaryImportLimitError,
    parse,
)
from omniscribe.plugins.errors import PluginError
from omniscribe.plugins.glossary.schemas import (
    GlossaryFormat,
    GlossaryImportSource,
    GlossaryListItem,
    GlossaryPreviewResponse,
)
from omniscribe.plugins.jobs import GlossaryJobRunner, JobOutcome
from omniscribe.utils.security import is_ssrf_target

if TYPE_CHECKING:
    from omniscribe.core.lexicon import LexiconStore

_LOGGER = logging.getLogger("omniscribe.plugins.glossary")

SYNC_THRESHOLD = 5_000


class GlossaryError(PluginError):
    """User-facing glossary error (envelope wire fields on ``PluginError``)."""


def _decode_bytes_payload(value: str) -> bytes:
    if not value:
        raise GlossaryError(422, "validation_failed", "inline_bytes_b64 is required.")

    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise GlossaryError(
            422, "validation_failed", "inline_bytes_b64 is not valid base64."
        ) from exc


def _is_safe_sql_dsn(dsn: str) -> bool:
    """Reject DSNs with shell metacharacters or query-string injection."""
    if not dsn:
        return False
    try:
        parsed = urlparse(dsn)
    except ValueError:
        return False
    if not parsed.scheme or parsed.scheme not in {
        "sqlite",
        "postgresql",
        "mysql",
        "mssql",
        "oracle",
    }:
        return False
    return not any(ch in dsn for ch in (";", "\n", "\r", "\x00"))


def _build_csv_kwargs(source: GlossaryImportSource) -> dict[str, Any]:
    """Build parser kwargs for CSV/TSV/XLIFF/TBX/TMX/JSON_PAIRS formats."""
    if source.text is not None:
        return {"text": source.text, "encoding": source.encoding or "utf-8"}
    elif source.inline_bytes_b64 is not None:
        return {
            "data": _decode_bytes_payload(source.inline_bytes_b64),
            "encoding": source.encoding or "utf-8",
        }
    else:
        raise GlossaryError(
            422,
            "validation_failed",
            "Provide 'text' or 'inline_bytes_b64' for inline formats.",
        )


async def _build_git_glossary_kwargs(source: GlossaryImportSource) -> dict[str, Any]:
    """Build parser kwargs for Git Glossary format (SSRF-checked)."""
    if not source.git_url:
        raise GlossaryError(
            400, "bad_request", "git_url is required for git_glossary imports."
        )
    ssrf = await is_ssrf_target(source.git_url)
    if not ssrf.allowed:
        raise GlossaryError(
            403,
            "ssrf_blocked",
            f"URL targets a blocked address: {ssrf.reason or 'blocked'}",
        )
    return {
        "url": source.git_url,
        "ref": source.git_ref or "HEAD",
        "path": source.git_path or "GLOSSARY.md",
        "credentials": source.git_credentials,
    }


def _build_sql_table_kwargs(source: GlossaryImportSource) -> dict[str, Any]:
    """Build parser kwargs for SQL Table format (DSN-sanitized)."""
    if not (
        source.sql_dsn
        and source.sql_source_table
        and source.sql_source_col
        and source.sql_target_col
    ):
        raise GlossaryError(
            422,
            "validation_failed",
            (
                "sql_dsn, sql_source_table, sql_source_col and sql_target_col "
                "are required for sql_table imports."
            ),
        )
    if not _is_safe_sql_dsn(source.sql_dsn):
        raise GlossaryError(
            422, "validation_failed", "sql_dsn contains unsafe characters."
        )
    return {
        "dsn": source.sql_dsn,
        "source_table": source.sql_source_table,
        "source_col": source.sql_source_col,
        "target_table": source.sql_target_table,
        "target_col": source.sql_target_col,
        "where_clause": source.sql_where,
        "encoding": source.encoding or "utf-8",
    }


async def build_parser_kwargs(
    source: GlossaryImportSource,
) -> tuple[dict[str, Any], str]:
    """Dispatch to the per-format kwargs builder (verbatim structure)."""
    fmt = source.format
    if fmt == GlossaryFormat.GIT_GLOSSARY:
        kwargs = await _build_git_glossary_kwargs(source)
    elif fmt in {
        GlossaryFormat.CSV,
        GlossaryFormat.TSV,
        GlossaryFormat.XLIFF,
        GlossaryFormat.TBX,
        GlossaryFormat.TMX,
        GlossaryFormat.JSON_PAIRS,
    }:
        kwargs = _build_csv_kwargs(source)
    elif fmt == GlossaryFormat.SQL_TABLE:
        kwargs = _build_sql_table_kwargs(source)
    else:
        raise GlossaryError(422, "validation_failed", f"Unknown format: {fmt}")
    kwargs["max_entries"] = source.max_entries
    return kwargs, fmt.value


def entry_count_estimate(kwargs: dict[str, Any]) -> int:
    """Estimate entry count for sync/async threshold selection (verbatim)."""
    text = kwargs.get("text")
    data = kwargs.get("data")
    if isinstance(text, str) and text:
        return max(text.count("\n"), 1)
    if isinstance(data, (bytes, bytearray)) and data:
        return max(bytes(data).count(b"\n"), 1)
    if kwargs.get("dsn") and kwargs.get("source_table"):
        return SYNC_THRESHOLD + 1  # assume large; favor async for SQL.
    if kwargs.get("url"):
        return SYNC_THRESHOLD + 1  # git/remote fetch always async.
    return SYNC_THRESHOLD + 1


def default_name(format_name: str, kwargs: dict[str, Any]) -> str:
    """Display-name fallback (verbatim)."""
    raw_name = kwargs.get("name")
    if isinstance(raw_name, str) and raw_name.strip():
        return raw_name.strip()
    if kwargs.get("url"):
        return f"Git glossary {kwargs['url']}"
    if kwargs.get("dsn") and kwargs.get("source_table"):
        target = kwargs.get("target_table") or kwargs["source_table"]
        return f"SQL {kwargs['source_table']} \u2192 {target}"
    return f"{format_name.upper()} import"


def _coerce_format(value: str) -> GlossaryFormat:
    try:
        return GlossaryFormat(value)
    except ValueError:
        return GlossaryFormat.JSON_PAIRS


@dataclass(frozen=True)
class _GlossaryImportPayload:
    """One queued glossary import."""

    # ClassVar dispatch marker (not a field): the jobs queue resolves the
    # runner registered under this service key at claim time.
    runner_protocol: ClassVar[type] = GlossaryJobRunner

    submission_id: str
    format_name: str
    kwargs: dict[str, Any]
    display_name: str


class GlossaryImportService(Protocol):
    async def import_glossary(self, source: GlossaryImportSource) -> dict[str, Any]: ...
    async def run_import_job(self, payload: Any) -> JobOutcome: ...
    def ensure_store_ready(self) -> None: ...
    def list_library(self) -> list[dict[str, Any]]: ...
    def toggle(
        self, glossary_id: str, *, enabled: bool | None = None
    ) -> dict[str, Any]: ...
    def reorder(self, ordered_ids: list[str]) -> dict[str, Any]: ...
    def delete(self, glossary_id: str) -> dict[str, Any]: ...
    def library_preview(self) -> dict[str, Any]: ...
    def entries(
        self,
        glossary_id: str | None = None,
        *,
        query: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> dict[str, Any]: ...
    def merged(self) -> dict[str, Any]: ...


class GlossaryImportServiceImpl:
    """Harness glossary import/library service over LexiconProvider."""

    def __init__(
        self,
        store_provider: Callable[[], LexiconStore | None],
        queue: Any,
    ) -> None:
        self._store_provider = store_provider
        self._queue = queue

    # -- store seam ---------------------------------------------------------

    def ensure_store_ready(self) -> None:
        """Validate that the LexiconStore is available, raising 503 if missing."""
        self._library()

    def _library(self) -> LexiconStore:
        store = self._store_provider()
        if store is None:
            raise GlossaryError(
                503,
                "backend_unavailable",
                "Lexicon store is not available. Install with: uv sync --extra lexicon",
            )
        return store

    # -- import -------------------------------------------------------------

    async def import_glossary(self, source: GlossaryImportSource) -> dict[str, Any]:
        """Sync up to SYNC_THRESHOLD entries, otherwise queue (verbatim)."""
        kwargs, format_name = await build_parser_kwargs(source)
        try:
            estimate = entry_count_estimate(kwargs)
            if estimate <= SYNC_THRESHOLD:
                return await self._process_sync(source, kwargs, format_name)
            return await self._process_async(source, kwargs, format_name)
        except FormatNotAvailableError as exc:
            raise GlossaryError(503, "backend_unavailable", str(exc)) from exc
        except GlossaryImportLimitError as exc:
            raise GlossaryError(
                400, "bad_request", f"Too many entries (max {exc.limit})"
            ) from exc
        except ValueError as exc:
            raise GlossaryError(422, "validation_failed", str(exc)) from exc

    def _resolve_name(self, source: GlossaryImportSource) -> str | None:
        candidate = source.name
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
        return None

    async def _process_sync(
        self,
        source: GlossaryImportSource,
        kwargs: dict[str, Any],
        format_name: str,
    ) -> dict[str, Any]:
        store = self._library()
        summary = parse(format=format_name, **kwargs)
        display_name = self._resolve_name(source) or default_name(format_name, kwargs)
        meta = store.save_glossary(
            name=display_name,
            format=format_name,
            entries=summary.entries,
            source_uri=summary.source_uri,
            encoding=summary.encoding,
        )
        return {
            "glossary_id": meta.id,
            "job_id": None,
            "format": format_name,
            "name": meta.name,
            "entry_count": len(summary.entries),
            "warnings": list(summary.warnings),
            "queued": False,
        }

    async def _process_async(
        self,
        source: GlossaryImportSource,
        kwargs: dict[str, Any],
        format_name: str,
    ) -> dict[str, Any]:
        if self._queue is None:
            raise GlossaryError(503, "backend_unavailable", "Job queue unavailable.")
        display_name = self._resolve_name(source) or default_name(format_name, kwargs)
        submission_id = secrets.token_hex(16)
        handle = await self._queue.submit(
            _GlossaryImportPayload(
                submission_id=submission_id,
                format_name=format_name,
                kwargs=kwargs,
                display_name=display_name,
            ),
            request_meta={
                "submission_id": submission_id,
                "name": display_name,
                "format": format_name,
            },
        )
        return {
            "glossary_id": None,
            "job_id": handle.job_id,
            "format": format_name,
            "name": display_name,
            "entry_count": 0,
            "warnings": [],
            "queued": True,
        }

    # -- runner ---------------------------------------------------------------

    async def run_import_job(self, payload: Any) -> JobOutcome:
        """Claim-time runner body for queued imports."""
        if not isinstance(payload, _GlossaryImportPayload):
            raise ValueError("glossary job queue received a foreign payload")
        summary = parse(format=payload.format_name, **payload.kwargs)
        meta = self._library().save_glossary(
            name=payload.display_name,
            format=payload.format_name,
            entries=summary.entries,
            source_uri=summary.source_uri,
            encoding=summary.encoding,
        )
        outcome = {
            "glossary_id": meta.id,
            "format": payload.format_name,
            "name": meta.name,
            "entry_count": len(summary.entries),
            "warnings": list(summary.warnings),
        }
        return JobOutcome(
            blob=json.dumps(outcome).encode("utf-8"),
            content_type="application/json",
        )

    # -- library ------------------------------------------------------------

    @staticmethod
    def _serialize_item(item: Any) -> dict[str, Any]:
        return GlossaryListItem(
            id=item.id,
            name=item.name,
            format=_coerce_format(item.format),
            source_uri=item.source_uri,
            encoding=item.encoding,
            entry_count=item.entry_count,
            enabled=item.enabled,
            priority=item.priority,
            group=item.group,
        ).model_dump()

    def list_library(self) -> list[dict[str, Any]]:
        self.ensure_store_ready()
        return [self._serialize_item(i) for i in self._library().list_glossaries()]

    def toggle(
        self, glossary_id: str, *, enabled: bool | None = None
    ) -> dict[str, Any]:
        self.ensure_store_ready()
        store = self._library()
        if enabled is None:
            current = store.get_glossary(glossary_id)
            if current is None:
                raise GlossaryError(404, "not_found", "Glossary not found.")
            target_enabled = not current.enabled
        else:
            target_enabled = enabled
        # GlossaryNotFoundError subclasses KeyError; catching the base class
        # avoids importing the lexicon core package (pyarrow) on the
        # web-only fast tier.
        try:
            meta = store.toggle_glossary(glossary_id, enabled=target_enabled)
        except KeyError as exc:
            raise GlossaryError(404, "not_found", "Glossary not found.") from exc
        return self._serialize_item(meta)

    def reorder(self, ordered_ids: list[str]) -> dict[str, Any]:
        self.ensure_store_ready()
        store = self._library()
        # GlossaryNotFoundError subclasses KeyError; see toggle.
        try:
            store.reorder_glossaries(ordered_ids)
        except KeyError as exc:
            raise GlossaryError(404, "not_found", "Glossary not found.") from exc
        except ValueError as exc:
            raise GlossaryError(422, "validation_failed", str(exc)) from exc
        return {"ok": True}

    def delete(self, glossary_id: str) -> dict[str, Any]:
        self.ensure_store_ready()
        deleted = self._library().delete_glossary(glossary_id)
        if not deleted:
            raise GlossaryError(404, "not_found", "Glossary not found.")
        return {"ok": True, "id": glossary_id}

    def library_preview(self) -> dict[str, Any]:
        self.ensure_store_ready()
        store = self._library()
        from omniscribe.core.lexicon import preview

        payload = preview(store)
        conflicts_value = payload.get("conflicts", [])
        enabled_value = payload.get("enabled_glossaries", [])
        if not isinstance(conflicts_value, list):
            conflicts_value = []
        if not isinstance(enabled_value, list):
            enabled_value = []
        return GlossaryPreviewResponse(
            count=int(str(payload.get("count", 0) or 0)),
            conflicts=[
                dict(item) for item in conflicts_value if isinstance(item, dict)
            ],
            enabled_glossaries=[str(item) for item in enabled_value],
        ).model_dump()

    def entries(
        self,
        glossary_id: str | None = None,
        *,
        query: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> dict[str, Any]:
        self.ensure_store_ready()
        store = self._library()
        meta: Any = None
        if glossary_id is not None:
            meta = store.get_glossary(glossary_id)
            if meta is None:
                raise GlossaryError(404, "not_found", "Glossary not found.")
            raw_entries = store.list_entries(glossary_id)
        else:
            raw_entries = []
            for g in store.list_glossaries():
                raw_entries.extend(store.list_entries(g.id))

        if query:
            q_lower = query.lower()
            raw_entries = [
                e
                for e in raw_entries
                if q_lower in getattr(e, "source_text", "").lower()
                or q_lower in getattr(e, "target_text", "").lower()
            ]

        total = len(raw_entries)
        safe_offset = max(0, offset)
        if limit is not None:
            safe_limit = max(0, limit)
            paged = raw_entries[safe_offset : safe_offset + safe_limit]
        else:
            paged = raw_entries[safe_offset:]

        formatted_entries = [
            {
                "source": getattr(e, "source_text", ""),
                "target": getattr(e, "target_text", ""),
                "case_sensitive": getattr(e, "case_sensitive", False),
                "notes": getattr(e, "notes", ""),
            }
            for e in paged
        ]

        result: dict[str, Any] = {
            "entries": formatted_entries,
            "total": total,
        }
        if meta is not None:
            result["id"] = meta.id
            result["name"] = meta.name
            result["format"] = meta.format
        return result

    def merged(self) -> dict[str, Any]:
        self.ensure_store_ready()
        store = self._library()
        from omniscribe.core.lexicon import merged_enabled_glossary

        return merged_enabled_glossary(store).to_dict()
