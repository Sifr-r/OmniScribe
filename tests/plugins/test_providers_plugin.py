"""Providers plugin: catalog shape, discovery, active provider, routes."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pathlib import Path
import pytest

from omniscribe.config import load_settings
from omniscribe.harness.context import Context
from omniscribe.plugins import providers as prov
from omniscribe.plugins.providers import (
    PROVIDER_TEMPLATES,
    ProviderManager,
    ProviderManagerImpl,
    build_providers_router,
)


@pytest.fixture(autouse=True)
def _isolate_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate .env writes to a clean temporary directory so repo .env is untouched."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "LLM_API_BASE=http://localhost:1234/v1\nLLM_MODEL=allenai/olmocr-2-7b\nLLM_API_KEY=lm-studio\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeHttpClient:
    """Records discovery calls and answers with a canned payload."""

    def __init__(
        self, payload: dict[str, Any] | None = None, *, fail: Exception | None = None
    ) -> None:
        self.payload = payload or {}
        self.fail = fail
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def get(
        self, url: str, headers: dict[str, str] | None = None
    ) -> _FakeResponse:
        self.calls.append((url, dict(headers or {})))
        if self.fail is not None:
            raise self.fail
        return _FakeResponse(self.payload)

    async def aclose(self) -> None:
        return None


def _manager(
    client: FakeHttpClient | None = None,
) -> tuple[ProviderManagerImpl, FakeHttpClient]:
    http = client or FakeHttpClient()
    return (
        ProviderManagerImpl(
            load_settings(),
            discovery_timeout_seconds=1.0,
            http_client=http,  # type: ignore[arg-type]
        ),
        http,
    )


# -- catalog ------------------------------------------------------------------


def test_list_providers_maps_every_template_onto_preset_shape() -> None:
    manager, _ = _manager()
    presets = manager.list_providers()
    assert len(presets) == len(PROVIDER_TEMPLATES)
    ids = {preset["id"] for preset in presets}
    assert ids == set(PROVIDER_TEMPLATES)
    for preset in presets:
        assert set(preset) == {
            "id",
            "name",
            "category",
            "description",
            "recommended_base_url",
            "api_base",
            "default_model",
            "requires_key",
            "notes",
            "env_keys",
        }
    lmstudio = next(preset for preset in presets if preset["id"] == "lmstudio")
    assert lmstudio["category"] == "local"
    assert lmstudio["requires_key"] is False
    assert lmstudio["env_keys"] == []
    openai = next(preset for preset in presets if preset["id"] == "openai")
    assert openai["category"] == "cloud"
    assert openai["requires_key"] is True
    assert openai["env_keys"] == ["OPENAI_API_KEY"]
    anthropic = next(preset for preset in presets if preset["id"] == "anthropic")
    assert anthropic["env_keys"] == ["ANTHROPIC_API_KEY"]
    databricks = next(preset for preset in presets if preset["id"] == "databricks")
    assert databricks["env_keys"] == ["DATABRICKS_TOKEN", "DATABRICKS_API_TOKEN"]


def test_get_provider_returns_none_for_unknown_id() -> None:
    manager, _ = _manager()
    assert manager.get_provider("lmstudio") is not None
    assert manager.get_provider("nope") is None


# -- active provider ----------------------------------------------------------


def test_get_active_reflects_runtime_settings() -> None:
    manager, _ = _manager()
    active = manager.get_active()
    settings = load_settings()
    assert active == {
        "provider_id": "lmstudio",
        "api_base": settings.llm_api_base,
        "model": settings.llm_model,
    }


def test_set_active_writes_back_into_settings() -> None:
    manager, _ = _manager()
    active = manager.set_active(
        provider_id="openai", api_base="https://api.openai.com/v1", model="gpt-4o"
    )
    assert active == {
        "provider_id": "openai",
        "api_base": "https://api.openai.com/v1",
        "model": "gpt-4o",
    }
    assert manager.get_active() == active
    # the shared settings object observed the write-through
    assert manager._settings.llm_model == "gpt-4o"


def test_set_active_invokes_persist_env_key() -> None:
    from unittest.mock import call, patch

    manager, _ = _manager()
    with patch("omniscribe.plugins.providers_service.persist_env_key") as mock_persist:
        manager.set_active(
            provider_id="openai",
            api_base="https://api.openai.com/v1",
            model="gpt-4o",
            api_key="sk-test-secret",
        )
        mock_persist.assert_has_calls(
            [
                call("LLM_API_BASE", "https://api.openai.com/v1"),
                call("LLM_MODEL", "gpt-4o"),
                call("LLM_API_KEY", "sk-test-secret"),
            ],
            any_order=False,
        )

    # When settings.llm_api_key is empty and no api_key is provided
    manager2, _ = _manager()
    manager2._settings.llm_api_key = ""
    with patch("omniscribe.plugins.providers_service.persist_env_key") as mock_persist2:
        manager2.set_active(
            provider_id="openai",
            api_base="https://api.openai.com/v1",
            model="gpt-4o",
            api_key=None,
        )
        mock_persist2.assert_has_calls(
            [
                call("LLM_API_BASE", "https://api.openai.com/v1"),
                call("LLM_MODEL", "gpt-4o"),
            ],
            any_order=False,
        )
        assert all(c.args[0] != "LLM_API_KEY" for c in mock_persist2.mock_calls)


def test_set_active_falls_back_to_template_when_api_base_and_model_omitted() -> None:
    manager, _ = _manager()
    active = manager.set_active(provider_id="openai")
    assert active["provider_id"] == "openai"
    assert active["api_base"] == "https://api.openai.com/v1"
    assert active["model"] == "gpt-4o"
    assert manager._settings.llm_api_base == "https://api.openai.com/v1"
    assert manager._settings.llm_model == "gpt-4o"


def test_set_active_falls_back_to_settings_when_template_has_no_url_or_models() -> None:
    manager, _ = _manager()
    manager._settings.llm_api_base = "https://api.openai.com/v1"
    manager._settings.llm_model = "custom-model-x"
    active = manager.set_active(provider_id="databricks")
    assert active["provider_id"] == "databricks"
    assert active["api_base"] == "https://api.openai.com/v1"
    assert active["model"] == "custom-model-x"


def test_set_active_resolves_active_provider_from_host_when_provider_id_omitted() -> None:
    manager, _ = _manager()
    active = manager.set_active(api_base="https://api.anthropic.com")
    assert active["provider_id"] == "anthropic"


def test_set_active_rejects_ssrf_blocked_host() -> None:
    manager, _ = _manager()
    with pytest.raises(ValueError, match="blocked by the SSRF guard"):
        manager.set_active(api_base="http://169.254.169.254/latest/meta-data")


# -- discovery ------------------------------------------------------------------


async def test_discover_models_openai_compatible_parses_data_ids() -> None:
    manager, http = _manager(
        FakeHttpClient({"data": [{"id": "model-a"}, {"id": "model-b"}]})
    )
    result = await manager.discover_models("openai", api_key="sk-test")
    assert result == {"models": ["model-a", "model-b"], "error": None}
    url, headers = http.calls[0]
    # For HTTPS, URL hostname is preserved for TLS SNI and cert verification;
    # IP pinning occurs at the transport layer via _PinnedIPTransport.
    from urllib.parse import urlsplit

    host = urlsplit(url).hostname or ""
    assert host == "api.openai.com"
    assert urlsplit(url).path == "/v1/models"
    assert "Bearer sk-test" in headers["Authorization"]


async def test_discover_models_ollama_uses_api_tags() -> None:
    manager, http = _manager(
        FakeHttpClient({"models": [{"name": "llama3"}, {"name": "qwen2.5vl"}]})
    )
    result = await manager.discover_models("ollama")
    assert result == {"models": ["llama3", "qwen2.5vl"], "error": None}
    url, headers = http.calls[0]
    # H-1 audit fix: URL host rewritten to the SSRF-resolved IP.
    # On this test host, ``localhost`` resolves to ``::1`` (IPv6) but
    # could resolve to ``127.0.0.1`` on others; we accept either, and
    # assert the path + Host header are preserved.
    from urllib.parse import urlsplit

    host = urlsplit(url).hostname or ""
    assert host in {"127.0.0.1", "::1"}, f"expected loopback IP, got {host!r}"
    assert urlsplit(url).port == 11434
    assert urlsplit(url).path == "/api/tags"
    # The Host header preserves the original hostname so virtual
    # hosting / HTTPS SNI still match.
    assert headers.get("Host") == "localhost"


async def test_discover_models_failure_falls_back_to_presets() -> None:
    manager, _ = _manager(FakeHttpClient(fail=httpx.ConnectError("connection refused")))
    result = await manager.discover_models("lmstudio")
    assert result["models"] == list(PROVIDER_TEMPLATES["lmstudio"].models)
    assert result["error"] is not None


async def test_discover_models_without_base_url_reports_error() -> None:
    manager, http = _manager()
    result = await manager.discover_models("azure")
    assert result["models"] == []
    assert result["error"] == "no base URL for provider"
    assert http.calls == []


# -- validate (direct unit tests, no route) ----------------------------------


async def test_validate_ollama_probes_api_tags_endpoint() -> None:
    """The ollama provider must hit /api/tags, not the OpenAI-style /models."""
    manager, http = _manager(
        FakeHttpClient({"models": [{"name": "llama3"}, {"name": "qwen2.5vl"}]})
    )
    result = await manager.validate("ollama", api_base="")
    assert result.valid is True
    assert result.model_count == 2
    assert result.error is None
    assert len(http.calls) == 1
    url, headers = http.calls[0]
    # H-1 audit fix: URL host rewritten to SSRF-resolved IP.
    from urllib.parse import urlsplit

    host = urlsplit(url).hostname or ""
    assert host in {"127.0.0.1", "::1"}, f"expected loopback IP, got {host!r}"
    assert urlsplit(url).port == 11434
    assert urlsplit(url).path == "/api/tags"
    assert headers.get("Host") == "localhost"


async def test_validate_openai_compatible_probes_models_endpoint() -> None:
    manager, http = _manager(
        FakeHttpClient({"data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}]})
    )
    result = await manager.validate(
        "openai", api_base="https://api.openai.com/v1", api_key="sk-test"
    )
    assert result.valid is True
    assert result.model_count == 2
    assert result.models == ["gpt-4o", "gpt-4o-mini"]
    assert result.error is None
    url, headers = http.calls[0]
    # For HTTPS, URL hostname is preserved for TLS SNI and cert verification
    from urllib.parse import urlsplit

    host = urlsplit(url).hostname or ""
    assert host == "api.openai.com"
    assert urlsplit(url).path == "/v1/models"
    assert headers["Authorization"] == "Bearer sk-test"


async def test_validate_unknown_provider_short_circuits() -> None:
    manager, http = _manager()
    result = await manager.validate("bogus", api_base="http://does.not.matter")
    assert result.valid is False
    assert result.model_count == 0
    assert result.error == "unknown provider"
    assert http.calls == []


async def test_validate_connection_error_returns_classified_failure() -> None:
    manager, _ = _manager(FakeHttpClient(fail=httpx.ConnectError("connection refused")))
    result = await manager.validate("lmstudio", api_base="http://127.0.0.1:1/v1")
    assert result.valid is False
    assert result.model_count == 0
    assert result.error is not None
    assert "connection" in result.error.lower()


# -- routes ------------------------------------------------------------------


def test_router_catalog_details_and_models() -> None:
    manager, _ = _manager(
        FakeHttpClient({"data": [{"id": "model-a"}, {"id": "model-b"}]})
    )
    app = FastAPI()
    app.include_router(build_providers_router(manager))
    with TestClient(app) as client:
        listing = client.get("/api/providers")
        assert listing.status_code == 200
        assert len(listing.json()["providers"]) == len(PROVIDER_TEMPLATES)

        details = client.get("/api/providers/lmstudio")
        assert details.status_code == 200
        assert details.json()["id"] == "lmstudio"

        assert client.get("/api/providers/nope").status_code == 404
        assert client.get("/api/providers/nope/models").status_code == 404

        models = client.get("/api/providers/openai/models")
        assert models.status_code == 200
        assert models.json() == {"models": ["model-a", "model-b"], "error": None}


def test_provider_models_accepts_x_provider_api_key_header() -> None:
    manager, http = _manager(FakeHttpClient({"data": [{"id": "model-a"}]}))
    app = FastAPI()
    app.include_router(build_providers_router(manager))
    with TestClient(app) as client:
        response = client.get(
            "/api/providers/openai/models",
            headers={"X-Provider-Api-Key": "sk-header-key"},
        )
        assert response.status_code == 200
        assert response.json() == {"models": ["model-a"], "error": None}
        assert len(http.calls) == 1
        _, headers = http.calls[0]
        assert headers["Authorization"] == "Bearer sk-header-key"


def test_provider_models_accepts_authorization_bearer_header() -> None:
    manager, http = _manager(FakeHttpClient({"data": [{"id": "model-a"}]}))
    app = FastAPI()
    app.include_router(build_providers_router(manager))
    with TestClient(app) as client:
        response = client.get(
            "/api/providers/openai/models",
            headers={"Authorization": "Bearer sk-bearer-key"},
        )
        assert response.status_code == 200
        assert response.json() == {"models": ["model-a"], "error": None}
        assert len(http.calls) == 1
        _, headers = http.calls[0]
        assert headers["Authorization"] == "Bearer sk-bearer-key"


def test_provider_models_passes_resolved_key_to_discover_models() -> None:
    manager, _ = _manager()
    mock_discover = AsyncMock(return_value={"models": ["mock-m"], "error": None})
    manager.discover_models = mock_discover  # type: ignore[method-assign]
    app = FastAPI()
    app.include_router(build_providers_router(manager))
    with TestClient(app) as client:
        # 1. X-Provider-Api-Key header takes highest precedence
        resp1 = client.get(
            "/api/providers/openai/models?api_key=query-key",
            headers={
                "X-Provider-Api-Key": "header-x-key",
                "Authorization": "Bearer header-bearer-key",
            },
        )
        assert resp1.status_code == 200
        mock_discover.assert_awaited_with(
            "openai", api_base=None, api_key="header-x-key"
        )

        # 2. Authorization: Bearer takes precedence over query param
        resp2 = client.get(
            "/api/providers/openai/models?api_key=query-key",
            headers={"Authorization": "Bearer header-bearer-key"},
        )
        assert resp2.status_code == 200
        mock_discover.assert_awaited_with(
            "openai", api_base=None, api_key="header-bearer-key"
        )

        # 3. Non-Bearer Authorization falls back to query param
        resp3 = client.get(
            "/api/providers/openai/models?api_key=query-key",
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
        )
        assert resp3.status_code == 200
        mock_discover.assert_awaited_with(
            "openai", api_base=None, api_key="query-key"
        )

        # 4. Query param used when no headers provided
        resp4 = client.get("/api/providers/openai/models?api_key=query-key")
        assert resp4.status_code == 200
        mock_discover.assert_awaited_with(
            "openai", api_base=None, api_key="query-key"
        )


def test_bearer_token_helper() -> None:
    assert prov._bearer_token("Bearer sk-test-token") == "sk-test-token"
    assert prov._bearer_token("Bearer   sk-test-token  ") == "sk-test-token"
    assert prov._bearer_token("Bearer ") == ""
    assert prov._bearer_token("Basic dXNlcjpwYXNz") is None
    assert prov._bearer_token(None) is None
    assert prov._bearer_token("") is None


async def test_plugin_registers_provider_manager_service() -> None:
    ctx = Context()
    await ctx.plugin(prov.ProvidersPlugin(), config={})
    manager = ctx.inject(ProviderManager)
    assert len(manager.list_providers()) == len(PROVIDER_TEMPLATES)
    assert ctx.routes()
    await ctx.dispose()


# -- GET /api/providers/active & POST /api/providers/active -----------------


def test_get_active_route(api_client: TestClient) -> None:
    response = api_client.get("/api/providers/active")
    assert response.status_code == 200
    body = response.json()
    assert "provider_id" in body
    assert "api_base" in body
    assert "model" in body

    # After setting active provider, GET reflects the change
    post_resp = api_client.post(
        "/api/providers/active",
        json={
            "providerId": "lmstudio",
            "apiBase": "http://localhost:1234/v1",
            "apiKey": "sk-test-1234",
            "model": "allenai/olmocr-2-7b",
        },
    )
    assert post_resp.status_code == 200

    get_resp = api_client.get("/api/providers/active")
    assert get_resp.status_code == 200
    assert get_resp.json() == {
        "provider_id": "lmstudio",
        "api_base": "http://localhost:1234/v1",
        "model": "allenai/olmocr-2-7b",
    }


def test_set_active_route_writes_through_settings(api_client: TestClient) -> None:
    response = api_client.post(
        "/api/providers/active",
        json={
            "providerId": "lmstudio",
            "apiBase": "http://localhost:1234/v1",
            "apiKey": "sk-test-1234",
            "model": "allenai/olmocr-2-7b",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["provider_id"] == "lmstudio"
    # Boot settings are seeded by the api_client fixture; assert write-through
    # by reaching the harness-owned manager the same way the unit test does.
    manager = api_client.app.state.context.inject(ProviderManager)  # type: ignore[attr-defined]
    assert manager._settings.llm_api_base == "http://localhost:1234/v1"
    assert manager._settings.llm_api_key == "sk-test-1234"
    assert manager._settings.llm_model == "allenai/olmocr-2-7b"


def test_set_active_route_with_omitted_api_key(api_client: TestClient) -> None:
    # First write a sentinel api_key.
    api_client.post(
        "/api/providers/active",
        json={
            "providerId": "lmstudio",
            "apiBase": "http://localhost:1234/v1",
            "apiKey": "sk-sentinel",
            "model": "allenai/olmocr-2-7b",
        },
    )
    # Now post without api_key; sentinel must be unchanged while base + model flip.
    response = api_client.post(
        "/api/providers/active",
        json={
            "providerId": "lmstudio",
            "apiBase": "http://localhost:9999/v1",
            "model": "different-model",
        },
    )
    assert response.status_code == 200
    # Reach the harness-owned manager (same pattern as
    # ``test_set_active_writes_back_into_settings``) and confirm partial writes.
    manager = api_client.app.state.context.inject(ProviderManager)  # type: ignore[attr-defined]
    assert manager._settings.llm_api_key == "sk-sentinel"
    assert manager._settings.llm_api_base == "http://localhost:9999/v1"
    assert manager._settings.llm_model == "different-model"


def test_set_active_route_with_omitted_api_base_and_model(api_client: TestClient) -> None:
    response = api_client.post(
        "/api/providers/active",
        json={"providerId": "anthropic"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["provider_id"] == "anthropic"
    assert body["api_base"] == "https://api.anthropic.com"
    assert body["model"] == "claude-sonnet-4-5"


# -- POST /api/providers/validate --------------------------------------------


def test_validate_route_returns_model_count(api_client, monkeypatch) -> None:
    """Stub httpx so the live probe never hits the network."""
    import httpx

    class _FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def get(self, url, headers=None):
            return _FakeResponse({"data": [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}]})

        async def aclose(self):
            return None

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    response = api_client.post(
        "/api/providers/validate",
        json={
            "providerId": "lmstudio",
            "apiBase": "http://localhost:1234/v1",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["model_count"] == 3
    assert body["models"] == ["m1", "m2", "m3"]


def test_validate_route_handles_offline_provider(api_client, monkeypatch) -> None:
    import httpx

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def get(self, url, headers=None):
            raise httpx.ConnectError("connection refused")

        async def aclose(self):
            return None

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    response = api_client.post(
        "/api/providers/validate",
        json={
            "providerId": "openai",
            "apiBase": "http://127.0.0.1:1/v1",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert body.get("error")


def test_validate_route_unknown_provider(api_client) -> None:
    response = api_client.post(
        "/api/providers/validate",
        json={
            "providerId": "bogus",
            "apiBase": "http://localhost:1234/v1",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert body["error"] == "unknown provider"


# -- Anthropic endpoint & headers ----------------------------------------------


async def test_discover_models_anthropic_endpoint_and_headers() -> None:
    manager, http = _manager(
        FakeHttpClient(
            {"data": [{"id": "claude-sonnet-4-5"}, {"id": "claude-haiku-3-5"}]}
        )
    )
    result = await manager.discover_models("anthropic", api_key="sk-ant-test")
    assert result == {
        "models": ["claude-sonnet-4-5", "claude-haiku-3-5"],
        "error": None,
    }
    url, headers = http.calls[0]
    assert url == "https://api.anthropic.com/v1/models"
    assert headers.get("x-api-key") == "sk-ant-test"
    assert headers.get("anthropic-version") == "2023-06-01"
    assert "Authorization" not in headers


async def test_discover_models_anthropic_base_with_v1_does_not_duplicate() -> None:
    manager, http = _manager(FakeHttpClient({"data": [{"id": "claude-sonnet-4-5"}]}))
    result = await manager.discover_models(
        "anthropic",
        api_base="https://api.anthropic.com/v1",
        api_key="sk-ant-test",
    )
    assert result["models"] == ["claude-sonnet-4-5"]
    url, _ = http.calls[0]
    assert url == "https://api.anthropic.com/v1/models"


async def test_validate_anthropic_uses_custom_headers_and_endpoint() -> None:
    manager, http = _manager(
        FakeHttpClient({"data": [{"id": "claude-sonnet-4-5"}]})
    )
    result = await manager.validate(
        "anthropic",
        api_base="https://api.anthropic.com",
        api_key="sk-ant-test",
    )
    assert result.valid is True
    assert result.model_count == 1
    assert result.models == ["claude-sonnet-4-5"]
    url, headers = http.calls[0]
    assert url == "https://api.anthropic.com/v1/models"
    assert headers.get("x-api-key") == "sk-ant-test"
    assert headers.get("anthropic-version") == "2023-06-01"
    assert "Authorization" not in headers


# -- Auto-discovery of Provider API Keys ---------------------------------------


async def test_discover_models_auto_discovers_from_env(
    monkeypatch,
) -> None:
    from unittest.mock import AsyncMock, MagicMock, patch

    cases = [
        ("openai", "OPENAI_API_KEY"),
        ("groq", "GROQ_API_KEY"),
        ("openrouter", "OPENROUTER_API_KEY"),
        ("deepseek", "DEEPSEEK_API_KEY"),
        ("minimax", "MINIMAX_API_KEY"),
        ("azure", "AZURE_OPENAI_API_KEY"),
    ]
    with patch(
        "omniscribe.plugins.providers_service.is_ssrf_target",
        new=AsyncMock(
            return_value=MagicMock(
                allowed=True, resolved_ip="104.18.3.161", reason=None
            )
        ),
    ):
        for provider_id, env_var in cases:
            monkeypatch.setenv(env_var, "sk-test-val")
            manager, http = _manager(FakeHttpClient({"data": [{"id": "m1"}]}))
            result = await manager.discover_models(provider_id, api_base="https://api.example.com/v1")
            assert result["models"] == ["m1"]
            assert len(http.calls) == 1
            _, headers = http.calls[0]
            assert headers.get("Authorization") == "Bearer sk-test-val"
            monkeypatch.delenv(env_var, raising=False)


async def test_discover_models_auto_discovers_anthropic_from_env(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")
    manager, http = _manager(FakeHttpClient({"data": [{"id": "claude-sonnet-4-5"}]}))
    result = await manager.discover_models("anthropic")
    assert result["models"] == ["claude-sonnet-4-5"]
    _, headers = http.calls[0]
    assert headers.get("x-api-key") == "sk-ant-env"
    assert headers.get("anthropic-version") == "2023-06-01"
    assert "Authorization" not in headers


async def test_discover_models_auto_discovers_databricks_tokens(
    monkeypatch,
) -> None:
    from unittest.mock import AsyncMock, MagicMock, patch

    with patch(
        "omniscribe.plugins.providers_service.is_ssrf_target",
        new=AsyncMock(
            return_value=MagicMock(
                allowed=True, resolved_ip="104.18.3.161", reason=None
            )
        ),
    ):
        # 1. DATABRICKS_TOKEN preferred
        monkeypatch.setenv("DATABRICKS_TOKEN", "dapi-tok-1")
        manager, http = _manager(FakeHttpClient({"data": [{"id": "db-1"}]}))
        await manager.discover_models("databricks", api_base="https://dbc.cloud.databricks.com")
        assert len(http.calls) == 1
        _, headers = http.calls[0]
        assert headers.get("Authorization") == "Bearer dapi-tok-1"

        # 2. DATABRICKS_API_TOKEN fallback
        monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)
        monkeypatch.setenv("DATABRICKS_API_TOKEN", "dapi-tok-2")
        manager2, http2 = _manager(FakeHttpClient({"data": [{"id": "db-2"}]}))
        await manager2.discover_models("databricks", api_base="https://dbc.cloud.databricks.com")
        assert len(http2.calls) == 1
        _, headers2 = http2.calls[0]
        assert headers2.get("Authorization") == "Bearer dapi-tok-2"


async def test_explicit_api_key_overrides_env_var(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    manager, http = _manager(FakeHttpClient({"data": [{"id": "m1"}]}))
    await manager.discover_models("openai", api_key="caller-key")
    _, headers = http.calls[0]
    assert headers.get("Authorization") == "Bearer caller-key"


async def test_auto_discover_falls_back_to_settings_when_active_matches(
    monkeypatch,
) -> None:
    # Ensure no env var
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    manager, http = _manager(FakeHttpClient({"data": [{"id": "m1"}]}))
    manager.set_active(
        provider_id="openai",
        api_base="https://api.openai.com/v1",
        model="gpt-4o",
        api_key="sk-settings-custom-key",
    )
    result = await manager.discover_models("openai")
    assert result["models"] == ["m1"]
    _, headers = http.calls[0]
    assert headers.get("Authorization") == "Bearer sk-settings-custom-key"


async def test_cloud_provider_ignores_default_lm_studio_sentinel_in_settings(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    manager, http = _manager(FakeHttpClient({"data": [{"id": "m1"}]}))
    # Settings has default sentinel "lm-studio"
    manager._settings.llm_api_key = "lm-studio"
    manager.set_active(
        provider_id="openai",
        api_base="https://api.openai.com/v1",
        model="gpt-4o",
    )
    result = await manager.discover_models("openai")
    assert result["models"] == ["m1"]
    _, headers = http.calls[0]
    assert "Authorization" not in headers


async def test_auto_discover_does_not_use_settings_for_inactive_provider(
    monkeypatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    manager, http = _manager(FakeHttpClient({"data": [{"id": "m1"}]}))
    manager.set_active(
        provider_id="openai",
        api_base="https://api.openai.com/v1",
        model="gpt-4o",
        api_key="sk-openai-custom-key",
    )
    result = await manager.discover_models("anthropic")
    assert result["models"] == ["m1"]
    _, headers = http.calls[0]
    assert "x-api-key" not in headers
    assert "Authorization" not in headers

