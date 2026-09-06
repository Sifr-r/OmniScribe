"""Unit tests for the glossary plugin service (fake store, no HTTP)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

from omniscribe.plugins.glossary import service as glossary_service
from omniscribe.plugins.glossary.schemas import (
    GlossaryFormat,
    GlossaryImportSource,
)

# ---------------------------------------------------------------------------
# Fake in-memory LexiconStore (Protocol subset; mirrors GlossaryMeta shapes)
# ---------------------------------------------------------------------------


@dataclass
class _FakeMeta:
    id: str
    name: str
    format: str
    source_uri: str | None = None
    encoding: str | None = None
    entry_count: int = 0
    enabled: bool = True
    priority: int = 0
    group: str = "default"


@dataclass
class _FakeEntry:
    source_text: str
    target_text: str
    case_sensitive: bool = False
    notes: str = ""


class FakeLexiconStore:
    """In-memory LexiconStore double (extra-independent route tests)."""

    def __init__(self) -> None:
        self._glossaries: dict[str, _FakeMeta] = {}
        self._entries: dict[str, list[_FakeEntry]] = {}
        self._counter = 0

    def list_glossaries(self) -> list[Any]:
        return list(self._glossaries.values())

    def get_glossary(self, glossary_id: str) -> Any:
        return self._glossaries.get(glossary_id)

    def save_glossary(
        self,
        *,
        name: str,
        format: str,
        entries: Any,
        source_uri: str | None = None,
        encoding: str | None = None,
        group: str = "default",
        priority: int = 0,
    ) -> Any:
        self._counter += 1
        gid = f"g{self._counter}"
        self._glossaries[gid] = _FakeMeta(
            id=gid,
            name=name,
            format=format,
            source_uri=source_uri,
            encoding=encoding,
            entry_count=len(list(entries)),
            enabled=True,
            priority=priority,
            group=group,
        )
        self._entries[gid] = [
            _FakeEntry(
                source_text=str(e.get("source", "")),
                target_text=str(e.get("target", "")),
                case_sensitive=bool(e.get("case_sensitive", False)),
                notes=str(e.get("notes", "")),
            )
            for e in entries
        ]
        return self._glossaries[gid]

    def toggle_glossary(self, glossary_id: str, *, enabled: bool) -> Any:
        if glossary_id not in self._glossaries:
            raise KeyError(glossary_id)
        meta = self._glossaries[glossary_id]
        meta.enabled = enabled
        return meta

    def reorder_glossaries(self, ordered_ids: Any) -> None:
        missing = [gid for gid in ordered_ids if gid not in self._glossaries]
        if missing:
            raise KeyError(missing[0])
        reordered: dict[str, _FakeMeta] = {}
        for gid in ordered_ids:
            reordered[gid] = self._glossaries[gid]
        for gid, meta in self._glossaries.items():
            reordered.setdefault(gid, meta)
        self._glossaries = reordered

    def delete_glossary(self, glossary_id: str) -> bool:
        return self._glossaries.pop(glossary_id, None) is not None

    def list_entries(self, glossary_id: str) -> list[Any]:
        return list(self._entries.get(glossary_id, []))


def _service(
    store: FakeLexiconStore | None = None,
) -> tuple[glossary_service.GlossaryImportServiceImpl, FakeLexiconStore]:
    store = store or FakeLexiconStore()
    impl = glossary_service.GlossaryImportServiceImpl(
        store_provider=lambda: store,  # type: ignore[arg-type, return-value]
        queue=None,
    )
    return impl, store


def _json_pairs_source(text: str, name: str | None = None) -> GlossaryImportSource:
    return GlossaryImportSource(format=GlossaryFormat.JSON_PAIRS, text=text, name=name)


async def test_sync_import_json_pairs() -> None:
    impl, _store = _service()
    body = await impl.import_glossary(
        _json_pairs_source('{"entries": [{"source": "Hi", "target": "Salut"}]}')
    )
    assert body["entry_count"] == 1
    assert body["queued"] is False
    assert body["format"] == "json_pairs"
    assert body["glossary_id"]


async def test_import_requires_text_or_bytes_422() -> None:
    impl, _store = _service()
    with pytest.raises(glossary_service.GlossaryError) as excinfo:
        await impl.import_glossary(GlossaryImportSource(format=GlossaryFormat.CSV))
    assert excinfo.value.status_code == 422
    assert excinfo.value.error == "validation_failed"
    assert "text" in excinfo.value.detail and "inline_bytes_b64" in excinfo.value.detail


async def test_import_invalid_base64_422() -> None:
    impl, _store = _service()
    with pytest.raises(glossary_service.GlossaryError) as excinfo:
        await impl.import_glossary(
            GlossaryImportSource(
                format=GlossaryFormat.CSV, inline_bytes_b64="!!! not base64 !!!"
            )
        )
    assert excinfo.value.status_code == 422
    assert "base64" in excinfo.value.detail


async def test_import_max_entries_400() -> None:
    impl, _store = _service()
    # 3 entries in source, capped at 2 → the parser limit path (schema's
    # ge=1 bound passes max_entries=2 through to GlossaryImportLimitError).
    raw = json.dumps(
        {
            "entries": [
                {"source": "A", "target": "1"},
                {"source": "B", "target": "2"},
                {"source": "C", "target": "3"},
            ]
        }
    )
    with pytest.raises(glossary_service.GlossaryError) as excinfo:
        await impl.import_glossary(
            GlossaryImportSource(
                format=GlossaryFormat.JSON_PAIRS, text=raw, max_entries=2
            )
        )
    assert excinfo.value.status_code == 400
    assert "max 2" in excinfo.value.detail


async def test_git_import_ssrf_blocked_403(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from omniscribe.utils.security import SSRFCheckResult

    async def denied(url: str | None) -> SSRFCheckResult:
        return SSRFCheckResult(allowed=False, resolved_ip=None, reason="loopback")

    monkeypatch.setattr(glossary_service, "is_ssrf_target", denied)
    impl, _store = _service()
    with pytest.raises(glossary_service.GlossaryError) as excinfo:
        await impl.import_glossary(
            GlossaryImportSource(
                format=GlossaryFormat.GIT_GLOSSARY,
                git_url="http://127.0.0.1:1",
            )
        )
    assert excinfo.value.status_code == 403
    assert excinfo.value.error == "ssrf_blocked"


async def test_sql_unsafe_dsn_422() -> None:
    impl, _store = _service()
    with pytest.raises(glossary_service.GlossaryError) as excinfo:
        await impl.import_glossary(
            GlossaryImportSource(
                format=GlossaryFormat.SQL_TABLE,
                sql_dsn="sqlite:///tmp/example.db; DROP TABLE users;",
                sql_source_table="glossary",
                sql_source_col="source",
                sql_target_col="target",
            )
        )
    assert excinfo.value.status_code == 422
    assert "unsafe" in excinfo.value.detail


async def test_async_dispatch_above_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    submitted: list[Any] = []

    class _Queue:
        async def submit(self, payload: Any, *, request_meta: Any = None) -> Any:
            submitted.append(payload)

            class _Handle:
                job_id = "job-abc"
                status_url = "/api/process/status/job-abc"

            return _Handle()

    impl = glossary_service.GlossaryImportServiceImpl(
        store_provider=lambda: FakeLexiconStore(),  # type: ignore[arg-type, return-value]
        queue=_Queue(),
    )
    # One-line JSON text has 0 newlines → estimate 1; threshold 0 forces
    # every import onto the async path deterministically.
    monkeypatch.setattr(glossary_service, "SYNC_THRESHOLD", 0)
    body = await impl.import_glossary(
        _json_pairs_source(
            '{"entries": [{"source": "A", "target": "1"}, {"source": "B", "target": "2"}, {"source": "C", "target": "3"}]}'
        )
    )
    assert body["queued"] is True
    assert body["job_id"] == "job-abc"
    assert body["entry_count"] == 0
    assert len(submitted) == 1
    assert submitted[0].runner_protocol is glossary_service.GlossaryJobRunner


async def test_store_missing_503() -> None:
    impl = glossary_service.GlossaryImportServiceImpl(
        store_provider=lambda: None, queue=None
    )
    with pytest.raises(glossary_service.GlossaryError) as excinfo:
        await impl.import_glossary(
            _json_pairs_source('{"entries": [{"source": "Hi", "target": "Salut"}]}')
        )
    assert excinfo.value.status_code == 503
    assert "uv sync --extra lexicon" in excinfo.value.detail


async def test_library_ops_and_404() -> None:
    pytest.importorskip("pyarrow")
    pytest.importorskip("lancedb")
    impl, _store = _service()
    body = await impl.import_glossary(
        _json_pairs_source('{"entries": [{"source": "A", "target": "1"}]}', name="T")
    )
    gid = body["glossary_id"]
    assert impl.list_library()[0]["name"] == "T"

    toggled = impl.toggle(gid, enabled=False)
    assert toggled["enabled"] is False
    assert impl.list_library()[0]["enabled"] is False

    assert impl.reorder([gid]) == {"ok": True}
    assert impl.delete(gid) == {"ok": True, "id": gid}

    with pytest.raises(glossary_service.GlossaryError) as excinfo:
        impl.toggle("missing-id", enabled=False)
    assert excinfo.value.status_code == 404
    assert excinfo.value.detail == "Glossary not found."


async def test_run_import_job_happy_path() -> None:
    impl, store = _service()
    kwargs, format_name = await glossary_service.build_parser_kwargs(
        _json_pairs_source('{"entries": [{"source": "A", "target": "1"}]}')
    )
    payload = glossary_service._GlossaryImportPayload(
        submission_id="s1",
        format_name=format_name,
        kwargs=kwargs,
        display_name="Pinned",
    )
    outcome = await impl.run_import_job(payload)
    assert outcome.content_type == "application/json"
    data = json.loads(outcome.blob.decode("utf-8"))
    assert data["glossary_id"] == store.list_glossaries()[0].id
    assert data["name"] == "Pinned"
    assert data["entry_count"] == 1
    assert data["warnings"] == []


async def test_run_import_job_rejects_foreign_payload() -> None:
    impl, _store = _service()
    with pytest.raises(ValueError):
        await impl.run_import_job(object())


def test_ensure_store_ready() -> None:
    # 503 when store is missing
    missing_impl = glossary_service.GlossaryImportServiceImpl(
        store_provider=lambda: None, queue=None
    )
    with pytest.raises(glossary_service.GlossaryError) as excinfo:
        missing_impl.ensure_store_ready()
    assert excinfo.value.status_code == 503
    assert excinfo.value.error == "backend_unavailable"

    # No error when store is present
    impl, _store = _service()
    impl.ensure_store_ready()


async def test_service_toggle_flips_state() -> None:
    impl, _store = _service()
    body = await impl.import_glossary(
        _json_pairs_source('{"entries": [{"source": "A", "target": "1"}]}', name="T")
    )
    gid: str = body["glossary_id"]
    assert impl.list_library()[0]["enabled"] is True

    # Toggle without enabled parameter -> flips to False
    toggled = impl.toggle(gid)
    assert toggled["enabled"] is False

    # Toggle again -> flips back to True
    toggled_again = impl.toggle(gid)
    assert toggled_again["enabled"] is True


async def test_service_enhanced_entries() -> None:
    impl, _store = _service()
    res_1 = await impl.import_glossary(
        _json_pairs_source(
            json.dumps(
                {
                    "entries": [
                        {"source": "Hello World", "target": "Hola Mundo"},
                        {"source": "Apple", "target": "Manzana"},
                    ]
                }
            ),
            name="G1",
        )
    )
    await impl.import_glossary(
        _json_pairs_source(
            json.dumps(
                {
                    "entries": [
                        {"source": "World Cup", "target": "Copa Mundial"},
                        {"source": "Pineapple", "target": "Piña"},
                    ]
                }
            ),
            name="G2",
        )
    )
    g1_id: str = res_1["glossary_id"]

    # All entries across all glossaries
    all_entries = impl.entries()
    assert all_entries["total"] == 4
    assert len(all_entries["entries"]) == 4

    # Single glossary
    g1_entries = impl.entries(g1_id)
    assert g1_entries["total"] == 2
    assert g1_entries["id"] == g1_id
    assert [e["source"] for e in g1_entries["entries"]] == ["Hello World", "Apple"]

    # 404 for unknown glossary_id
    with pytest.raises(glossary_service.GlossaryError) as excinfo:
        impl.entries("unknown-id")
    assert excinfo.value.status_code == 404

    # Query filtering on source (case-insensitive)
    world_query = impl.entries(query="world")
    assert world_query["total"] == 2
    sources = [e["source"] for e in world_query["entries"]]
    assert "Hello World" in sources
    assert "World Cup" in sources

    # Query filtering on target (case-insensitive)
    target_query = impl.entries(query="manzana")
    assert target_query["total"] == 1
    assert target_query["entries"][0]["source"] == "Apple"

    # Pagination: limit and offset
    page_1 = impl.entries(limit=2, offset=0)
    assert page_1["total"] == 4
    assert len(page_1["entries"]) == 2

    page_2 = impl.entries(limit=2, offset=2)
    assert page_2["total"] == 4
    assert len(page_2["entries"]) == 2
    assert page_1["entries"] != page_2["entries"]
