"""GET/POST /api/config and the /api/config/ocr aliases."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_config_seed_shape(api_client: TestClient) -> None:
    response = api_client.get("/api/config")
    assert response.status_code == 200
    seeded = response.json()
    assert seeded["pipeline_mode"] == "hybrid"
    assert seeded["dense_mode"] == "auto"
    assert seeded["document_processors"] == []
    assert seeded["api_base"]


def test_config_round_trip_ignores_unknown_keys(api_client: TestClient) -> None:
    updated = api_client.post(
        "/api/config", json={"model": "new-model", "dpi": 300, "unknown_key": 1}
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["model"] == "new-model"
    assert body["dpi"] == 300
    assert "unknown_key" not in body

    # The next GET sees the same store.
    assert api_client.get("/api/config").json()["model"] == "new-model"


def test_config_ocr_aliases_share_the_store(api_client: TestClient) -> None:
    api_client.post("/api/config", json={"model": "aliased-model"})
    assert api_client.get("/api/config/ocr").json()["model"] == "aliased-model"

    put = api_client.put("/api/config/ocr", json={"dense_mode": "always"})
    assert put.status_code == 200
    assert put.json()["dense_mode"] == "always"
    assert api_client.get("/api/config").json()["dense_mode"] == "always"


def test_config_persistence_and_sync(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    persisted: dict[str, str] = {}

    def _fake_persist(key: str, val: str) -> None:
        persisted[key] = val

    monkeypatch.setattr("omniscribe.plugins.ocr.service.persist_env_key", _fake_persist)

    # 1. Update api_base, model, api_key via endpoint
    resp = api_client.post(
        "/api/config",
        json={
            "api_base": "http://127.0.0.1:11434/v1",
            "model": "qwen2.5:latest",
            "api_key": "my-secret-key",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["api_base"] == "http://127.0.0.1:11434/v1"
    assert body["model"] == "qwen2.5:latest"
    assert body["api_key"] == "******"

    # Verify persist_env_key was called for all 3 keys
    assert persisted.get("LLM_API_BASE") == "http://127.0.0.1:11434/v1"
    assert persisted.get("LLM_MODEL") == "qwen2.5:latest"
    assert persisted.get("LLM_API_KEY") == "my-secret-key"

    # 2. Masking check for lm-studio
    resp2 = api_client.post(
        "/api/config",
        json={"api_key": "lm-studio"},
    )
    assert resp2.status_code == 200
    assert resp2.json()["api_key"] == "lm-studio"
    assert persisted.get("LLM_API_KEY") == "lm-studio"

    # 3. Masking placeholder "******" does not overwrite key or re-persist
    persisted.clear()
    resp3 = api_client.post(
        "/api/config",
        json={"api_key": "******", "dpi": 200},
    )
    assert resp3.status_code == 200
    assert "LLM_API_KEY" not in persisted
    assert persisted.get("OCR_DPI") == "200"

    # 4. Persistence of OCR parameters
    persisted.clear()
    resp4 = api_client.post(
        "/api/config",
        json={
            "concurrency": 5,
            "dpi": 300,
            "dense_threshold": 120,
            "max_image_dim": 2048,
        },
    )
    assert resp4.status_code == 200
    assert persisted.get("OCR_CONCURRENCY") == "5"
    assert persisted.get("OCR_DPI") == "300"
    assert persisted.get("OCR_DENSE_THRESHOLD") == "120"
    assert persisted.get("OCR_MAX_IMAGE_DIM") == "2048"


def test_get_config_synchronizes_with_runtime_settings() -> None:
    from unittest.mock import MagicMock

    from omniscribe.config import RuntimeSettings
    from omniscribe.plugins.artifacts import ArtifactStore
    from omniscribe.plugins.jobs import JobQueue
    from omniscribe.plugins.ocr.service import OCRServiceImpl

    settings = RuntimeSettings(
        llm_api_base="http://initial-base/v1",
        llm_model="initial-model",
        llm_api_key="initial-secret",
    )
    queue = MagicMock(spec=JobQueue)
    artifacts = MagicMock(spec=ArtifactStore)
    service = OCRServiceImpl(
        settings,
        queue,
        artifacts,
        progress=None,
        max_upload_mb=100,
    )

    # Initial get_config reflects initial settings
    cfg = service.get_config()
    assert cfg["api_base"] == "http://initial-base/v1"
    assert cfg["model"] == "initial-model"
    assert cfg["api_key"] == "******"
    assert service._config["api_base"] == "http://initial-base/v1"
    assert service._config["model"] == "initial-model"
    assert service._config["api_key"] == "initial-secret"

    # Mutate settings directly at runtime
    settings.llm_api_base = "http://mutated-base/v1"
    settings.llm_model = "mutated-model"
    settings.llm_api_key = "mutated-secret"

    # get_config() synchronizes _config with runtime settings
    cfg2 = service.get_config()
    assert cfg2["api_base"] == "http://mutated-base/v1"
    assert cfg2["model"] == "mutated-model"
    assert cfg2["api_key"] == "******"
    assert service._config["api_base"] == "http://mutated-base/v1"
    assert service._config["model"] == "mutated-model"
    assert service._config["api_key"] == "mutated-secret"

    # If api_key is lm-studio, it is not masked
    settings.llm_api_key = "lm-studio"
    cfg3 = service.get_config()
    assert cfg3["api_key"] == "lm-studio"
