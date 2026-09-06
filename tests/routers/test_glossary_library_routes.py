"""Router contract tests for glossary library and sources endpoints.

Tests the HTTP surface for:
- GET /api/glossary/sources (listing enabled and disabled sources)
- DELETE /api/glossary/sources/{source_id} (success 200, 404 on unknown ID)
- POST /api/glossary/library/{source_id}/toggle (explicit body or flipping state)
- POST /api/glossary/library/reorder (reordering sources)
- GET /api/glossary/library/entries (query filtering, source_id, limit, offset)
- GET /api/glossary/library/preview and GET /api/glossary/library/merged
- 503 error handling when the LexiconStore is unavailable
"""

from __future__ import annotations

import json
from importlib.util import find_spec
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from omniscribe.plugins.glossary.service import (
    GlossaryError,
    GlossaryImportService,
)
from tests.plugins.test_glossary_service import FakeLexiconStore

_pyarrow_available: bool = find_spec("pyarrow") is not None
_NEEDS_LEXICON_EXTRA = pytest.mark.skipif(
    not _pyarrow_available,
    reason="core.lexicon preview/merged helpers require the lexicon extra (pyarrow)",
)


def _get_service(api_client: TestClient) -> GlossaryImportService:
    """Extract the injected GlossaryImportService instance from the app context."""
    return api_client.app.state.context.inject(GlossaryImportService)  # type: ignore[attr-defined, no-any-return]


def _inject_store(api_client: TestClient) -> FakeLexiconStore:
    """Inject a clean in-memory FakeLexiconStore into the service."""
    store = FakeLexiconStore()
    service: Any = _get_service(api_client)
    service._store_provider = lambda: store
    return store


def _import_json_pairs(
    api_client: TestClient, text: str, name: str | None = None
) -> dict[str, Any]:
    """Helper to import a glossary from an inline JSON-pairs string."""
    payload: dict[str, Any] = {"source": {"format": "json_pairs", "text": text}}
    if name is not None:
        payload["source"]["name"] = name
    response = api_client.post("/api/glossary/import", json=payload)
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def test_library_routes_mounted(api_client: TestClient) -> None:
    """Verify that all new and existing library route paths are mounted."""
    paths = set(json.loads(api_client.get("/openapi.json").text)["paths"])
    for path in (
        "/api/glossary/sources",
        "/api/glossary/sources/{source_id}",
        "/api/glossary/library/{source_id}/toggle",
        "/api/glossary/library/entries",
        "/api/glossary/library",
        "/api/glossary/library/preview",
        "/api/glossary/library/merged",
        "/api/glossary/library/{glossary_id}",
        "/api/glossary/library/{glossary_id}/enable",
        "/api/glossary/library/{glossary_id}/entries",
        "/api/glossary/library/reorder",
    ):
        assert path in paths, f"Path {path} not registered in openapi"


def test_sources_listing_enabled_and_disabled(api_client: TestClient) -> None:
    """GET /api/glossary/sources returns all sources regardless of enabled state."""
    _inject_store(api_client)

    # Empty list initially
    empty_resp = api_client.get("/api/glossary/sources")
    assert empty_resp.status_code == 200
    assert empty_resp.json() == []

    # Import first source (starts enabled=True)
    res_a = _import_json_pairs(
        api_client,
        '{"entries": [{"source": "hello", "target": "hola"}]}',
        name="Source A",
    )
    # Import second source
    res_b = _import_json_pairs(
        api_client,
        '{"entries": [{"source": "goodbye", "target": "adiós"}]}',
        name="Source B",
    )

    # Toggle second source to disabled
    toggle_resp = api_client.post(
        f"/api/glossary/library/{res_b['glossary_id']}/toggle",
        json={"enabled": False},
    )
    assert toggle_resp.status_code == 200
    assert toggle_resp.json()["enabled"] is False

    # List sources: both should be present
    sources_resp = api_client.get("/api/glossary/sources")
    assert sources_resp.status_code == 200
    sources = sources_resp.json()
    assert len(sources) == 2

    source_a = next(s for s in sources if s["id"] == res_a["glossary_id"])
    source_b = next(s for s in sources if s["id"] == res_b["glossary_id"])

    assert source_a["name"] == "Source A"
    assert source_a["enabled"] is True
    assert source_a["entry_count"] == 1

    assert source_b["name"] == "Source B"
    assert source_b["enabled"] is False
    assert source_b["entry_count"] == 1


def test_delete_source_success_and_404(api_client: TestClient) -> None:
    """DELETE /api/glossary/sources/{source_id} deletes a source or returns 404."""
    _inject_store(api_client)
    res = _import_json_pairs(
        api_client,
        '{"entries": [{"source": "cat", "target": "gato"}]}',
        name="Cats",
    )
    source_id: str = res["glossary_id"]

    # Successfully delete existing source
    del_resp = api_client.delete(f"/api/glossary/sources/{source_id}")
    assert del_resp.status_code == 200
    assert del_resp.json() == {"ok": True, "id": source_id}

    # Verify gone from sources
    sources = api_client.get("/api/glossary/sources").json()
    assert not any(s["id"] == source_id for s in sources)

    # Delete unknown ID returns 404
    del_unknown = api_client.delete(f"/api/glossary/sources/{source_id}")
    assert del_unknown.status_code == 404
    assert del_unknown.json() == {
        "error": "not_found",
        "detail": "Glossary not found.",
    }


def test_toggle_source_flip_without_body(api_client: TestClient) -> None:
    """POST /api/glossary/library/{source_id}/toggle flips state when body is omitted or empty."""
    _inject_store(api_client)
    res = _import_json_pairs(
        api_client,
        '{"entries": [{"source": "dog", "target": "perro"}]}',
        name="Dogs",
    )
    source_id: str = res["glossary_id"]

    # Initially enabled
    sources = api_client.get("/api/glossary/sources").json()
    assert sources[0]["enabled"] is True

    # Toggle with no body -> flips to False
    resp1 = api_client.post(f"/api/glossary/library/{source_id}/toggle")
    assert resp1.status_code == 200
    assert resp1.json()["enabled"] is False

    # Toggle with empty JSON -> flips back to True
    resp2 = api_client.post(f"/api/glossary/library/{source_id}/toggle", json={})
    assert resp2.status_code == 200
    assert resp2.json()["enabled"] is True


def test_toggle_source_explicit_body(api_client: TestClient) -> None:
    """POST /api/glossary/library/{source_id}/toggle respects explicit {"enabled": bool}."""
    _inject_store(api_client)
    res = _import_json_pairs(
        api_client,
        '{"entries": [{"source": "sun", "target": "sol"}]}',
        name="Sun",
    )
    source_id: str = res["glossary_id"]

    # Explicit disable
    resp1 = api_client.post(
        f"/api/glossary/library/{source_id}/toggle", json={"enabled": False}
    )
    assert resp1.status_code == 200
    assert resp1.json()["enabled"] is False

    # Explicit enable
    resp2 = api_client.post(
        f"/api/glossary/library/{source_id}/toggle", json={"enabled": True}
    )
    assert resp2.status_code == 200
    assert resp2.json()["enabled"] is True


def test_toggle_source_not_found(api_client: TestClient) -> None:
    """POST /api/glossary/library/{source_id}/toggle returns 404 for unknown source."""
    _inject_store(api_client)
    resp = api_client.post(
        "/api/glossary/library/missing-source-id/toggle", json={"enabled": True}
    )
    assert resp.status_code == 404
    assert resp.json() == {
        "error": "not_found",
        "detail": "Glossary not found.",
    }


def test_reorder_library_sources(api_client: TestClient) -> None:
    """POST /api/glossary/library/reorder reorders sources in the library."""
    _inject_store(api_client)
    res_1 = _import_json_pairs(
        api_client, '{"entries": [{"source": "1", "target": "uno"}]}', name="One"
    )
    res_2 = _import_json_pairs(
        api_client, '{"entries": [{"source": "2", "target": "dos"}]}', name="Two"
    )
    id1: str = res_1["glossary_id"]
    id2: str = res_2["glossary_id"]

    # Reorder to [id2, id1]
    reorder_resp = api_client.post(
        "/api/glossary/library/reorder", json={"ordered_ids": [id2, id1]}
    )
    assert reorder_resp.status_code == 200
    assert reorder_resp.json() == {"ok": True}

    sources = api_client.get("/api/glossary/sources").json()
    assert [s["id"] for s in sources] == [id2, id1]

    # Reorder with unknown id returns 404
    err_resp = api_client.post(
        "/api/glossary/library/reorder", json={"ordered_ids": ["unknown-id"]}
    )
    assert err_resp.status_code == 404
    assert err_resp.json()["error"] == "not_found"


def test_get_library_entries_all_and_filtering(api_client: TestClient) -> None:
    """GET /api/glossary/library/entries aggregates all entries and filters by query."""
    _inject_store(api_client)
    res_1 = _import_json_pairs(
        api_client,
        json.dumps(
            {
                "entries": [
                    {"source": "Apple", "target": "Manzana"},
                    {"source": "Banana", "target": "Plátano"},
                ]
            }
        ),
        name="Fruits",
    )
    _import_json_pairs(
        api_client,
        json.dumps(
            {
                "entries": [
                    {"source": "Pineapple", "target": "Piña"},
                    {"source": "Cherry", "target": "Cereza"},
                ]
            }
        ),
        name="Berries",
    )
    g1_id: str = res_1["glossary_id"]

    # All entries across all glossaries
    all_resp = api_client.get("/api/glossary/library/entries")
    assert all_resp.status_code == 200
    all_body = all_resp.json()
    assert all_body["total"] == 4
    assert len(all_body["entries"]) == 4

    # Query matching source text case-insensitively ("apple" matches "Apple" and "Pineapple")
    q_src = api_client.get("/api/glossary/library/entries?q=apple")
    assert q_src.status_code == 200
    q_body = q_src.json()
    assert q_body["total"] == 2
    sources = [e["source"] for e in q_body["entries"]]
    assert "Apple" in sources
    assert "Pineapple" in sources

    # Query matching target text case-insensitively ("cereza")
    q_tgt = api_client.get("/api/glossary/library/entries?q=cereza")
    assert q_tgt.status_code == 200
    tgt_body = q_tgt.json()
    assert tgt_body["total"] == 1
    assert tgt_body["entries"][0]["source"] == "Cherry"
    assert tgt_body["entries"][0]["target"] == "Cereza"

    # Query with no match
    q_none = api_client.get("/api/glossary/library/entries?q=nonexistent")
    assert q_none.status_code == 200
    assert q_none.json()["total"] == 0
    assert q_none.json()["entries"] == []

    # Filter by source_id
    src_resp = api_client.get(f"/api/glossary/library/entries?source_id={g1_id}")
    assert src_resp.status_code == 200
    src_body = src_resp.json()
    assert src_body["total"] == 2
    assert src_body["id"] == g1_id
    assert [e["source"] for e in src_body["entries"]] == ["Apple", "Banana"]

    # Filter by unknown source_id returns 404
    src_404 = api_client.get("/api/glossary/library/entries?source_id=unknown-id")
    assert src_404.status_code == 404
    assert src_404.json() == {
        "error": "not_found",
        "detail": "Glossary not found.",
    }


def test_get_library_entries_pagination(api_client: TestClient) -> None:
    """GET /api/glossary/library/entries applies limit and offset pagination."""
    _inject_store(api_client)
    _import_json_pairs(
        api_client,
        json.dumps(
            {
                "entries": [
                    {"source": f"term_{i}", "target": f"def_{i}"} for i in range(10)
                ]
            }
        ),
        name="Paginated",
    )

    # First page: limit 3, offset 0
    p1 = api_client.get("/api/glossary/library/entries?limit=3&offset=0").json()
    assert p1["total"] == 10
    assert len(p1["entries"]) == 3
    assert p1["entries"][0]["source"] == "term_0"
    assert p1["entries"][2]["source"] == "term_2"

    # Second page: limit 3, offset 3
    p2 = api_client.get("/api/glossary/library/entries?limit=3&offset=3").json()
    assert p2["total"] == 10
    assert len(p2["entries"]) == 3
    assert p2["entries"][0]["source"] == "term_3"
    assert p2["entries"][2]["source"] == "term_5"

    # Offset beyond total
    p_empty = api_client.get("/api/glossary/library/entries?offset=20").json()
    assert p_empty["total"] == 10
    assert p_empty["entries"] == []


@_NEEDS_LEXICON_EXTRA
def test_library_preview_and_merged(api_client: TestClient) -> None:
    """Preview and merged endpoints report conflicts and merged dictionary."""
    _inject_store(api_client)
    _import_json_pairs(
        api_client,
        '{"entries": [{"source": "Start", "target": "Comenzar"}]}',
        name="LexA",
    )
    _import_json_pairs(
        api_client,
        '{"entries": [{"source": "Start", "target": "Iniciar"}]}',
        name="LexB",
    )

    preview_resp = api_client.get("/api/glossary/library/preview")
    assert preview_resp.status_code == 200
    preview_body = preview_resp.json()
    assert preview_body["count"] == 1
    assert "LexA" in preview_body["enabled_glossaries"]
    assert "LexB" in preview_body["enabled_glossaries"]
    assert len(preview_body["conflicts"]) > 0

    merged_resp = api_client.get("/api/glossary/library/merged")
    assert merged_resp.status_code == 200
    merged_body = merged_resp.json()
    assert "entries" in merged_body
    assert len(merged_body["entries"]) >= 1


def test_503_when_store_provider_is_none(api_client: TestClient) -> None:
    """Library routes return 503 backend_unavailable when LanceDB store is missing."""
    service: Any = _get_service(api_client)
    service._store_provider = lambda: None

    for endpoint, method, payload in (
        ("/api/glossary/sources", "get", None),
        ("/api/glossary/sources/any_id", "delete", None),
        ("/api/glossary/library/any_id/toggle", "post", {"enabled": True}),
        ("/api/glossary/library/entries", "get", None),
        ("/api/glossary/library", "get", None),
        ("/api/glossary/library/reorder", "post", {"ordered_ids": []}),
        ("/api/glossary/library/preview", "get", None),
        ("/api/glossary/library/merged", "get", None),
    ):
        if method == "get":
            resp = api_client.get(endpoint)
        elif method == "delete":
            resp = api_client.delete(endpoint)
        else:
            resp = api_client.post(endpoint, json=payload)

        assert resp.status_code == 503, f"{endpoint} did not return 503"
        body = resp.json()
        assert body["error"] == "backend_unavailable"
        assert "uv sync --extra lexicon" in body["detail"]


def test_503_when_ensure_store_ready_mocked(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mocking service.ensure_store_ready() raises 503 backend_unavailable."""
    service: Any = _get_service(api_client)
    mock_ready = MagicMock(
        side_effect=GlossaryError(
            503,
            "backend_unavailable",
            "Lexicon store is not ready (custom mocked).",
        )
    )
    monkeypatch.setattr(service, "ensure_store_ready", mock_ready)

    resp_sources = api_client.get("/api/glossary/sources")
    assert resp_sources.status_code == 503
    assert resp_sources.json() == {
        "error": "backend_unavailable",
        "detail": "Lexicon store is not ready (custom mocked).",
    }

    resp_entries = api_client.get("/api/glossary/library/entries")
    assert resp_entries.status_code == 503
    assert resp_entries.json() == {
        "error": "backend_unavailable",
        "detail": "Lexicon store is not ready (custom mocked).",
    }
