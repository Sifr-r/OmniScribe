"""Transcription config store + model discovery (verbatim re-homes).

`mask_api_key` is verbatim from `44ef123^:api/services/helpers.py`
(`mask_api_key`). `extract_model_ids_from_response` is verbatim from
`44ef123^:api/services/provider_manager.py`. The discovery flow is
verbatim from `44ef123^:api/routers/models.py::get_transcription_models`
with the config store swapped for the plugin-owned one.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit

import httpx

from omniscribe.plugins.transcribe.schemas import (
    TranscriptionConfigResponse,
)
from omniscribe.utils.security import (
    _PinnedIPTransport,
    _rewrite_url_with_resolved_ip,
    is_ssrf_target,
)

_LOGGER = logging.getLogger("omniscribe.plugins.transcribe")

TRANSCRIPTION_FALLBACK_MODELS: list[str] = [
    "whisper-1",
    "whisper-large-v3",
    "whisper-medium",
    "whisper-base",
    "whisper-small",
    "whisper-tiny",
]


def mask_api_key(value: str | None) -> str | None:
    """Mask sensitive token or API key to prevent leaking credentials.

    For tokens <= 12 characters, a fixed mask is returned to prevent leaking
    excessive characters. Longer tokens preview the first and last 4 characters.
    """
    if not value or value == "lm-studio":
        return value
    if len(value) <= 12:
        return "********"
    return f"{value[:4]}...{value[-4:]}"


class TranscriptionConfigStore:
    """Plugin-owned in-memory transcription config (always writable)."""

    def __init__(self, auth_token: str | None = None) -> None:
        self._auth_token = auth_token
        self._data: dict[str, Any] = {
            "transcription_api_base": "https://api.openai.com/v1",
            "transcription_model": "whisper-1",
            "transcription_engine": "api",
            "transcription_temperature": 0.0,
        }

    def get(self) -> dict[str, Any]:
        return dict(self._data)

    def update(self, updates: dict[str, Any]) -> None:
        self._data.update(updates)

    def read(self) -> TranscriptionConfigResponse:
        data = self._data
        auth_tok = data.get("transcription_auth_token", self._auth_token)
        return TranscriptionConfigResponse(
            transcription_api_base=str(
                data.get("transcription_api_base", "https://api.openai.com/v1")
            ),
            transcription_api_key=mask_api_key(
                str(data.get("transcription_api_key", ""))
            )
            or "",
            transcription_model=str(data.get("transcription_model", "whisper-1")),
            transcription_engine=str(data.get("transcription_engine", "api")),
            transcription_auth_token=mask_api_key(auth_tok),
            language=str(data.get("transcription_language", "")) or None,
            prompt=str(data.get("transcription_prompt", "")) or None,
            temperature=float(data.get("transcription_temperature", 0.0)),
        )


def extract_model_ids_from_response(data: Any) -> list[str]:
    """Extract model identifiers from arbitrary JSON responses (verbatim)."""
    if not data:
        return []

    raw_items: list[Any] = []
    if isinstance(data, list):
        raw_items = data
    elif isinstance(data, dict):
        if "data" in data and isinstance(data["data"], list):
            raw_items = data["data"]
        elif "models" in data and isinstance(data["models"], list):
            raw_items = data["models"]
        elif "result" in data and isinstance(data["result"], list):
            raw_items = data["result"]
        elif "data" in data and isinstance(data["data"], dict):
            raw_items = list(data["data"].values())
        else:
            for v in data.values():
                if isinstance(v, dict) and any(k in v for k in ("id", "name", "model")):
                    raw_items.append(v)

    model_ids: list[str] = []
    seen: set[str] = set()

    for item in raw_items:
        mid: str | None = None
        if isinstance(item, str) and item.strip():
            mid = item.strip()
        elif isinstance(item, dict):
            for key in ("id", "name", "model", "model_id", "display_name"):
                val = item.get(key)
                if isinstance(val, str) and val.strip():
                    mid = val.strip()
                    break
        if mid and mid not in seen:
            seen.add(mid)
            model_ids.append(mid)

    return model_ids


async def discover_transcription_models(
    api_base: str, api_key: str | None
) -> list[str]:
    """Probe the configured endpoint for models; fall back on any failure.

    Verbatim flow from `44ef123^:api/routers/models.py:271-320`: SSRF-blocked
    → fallback list (no error); `lm-studio` key skipped for the Bearer
    header; probe `{base}/models` (base ends `/v1`) or `{base}/v1/models`
    then `{base}/models`, always then `{base}/api/tags` (Ollama); 5.0s
    timeout; per-URL failures swallowed; empty discovery → fallback.
    """
    fallback = list(TRANSCRIPTION_FALLBACK_MODELS)

    ssrf_check = await is_ssrf_target(api_base)
    if not ssrf_check.allowed:
        return fallback

    resolved_ip = getattr(ssrf_check, "resolved_ip", None)

    headers: dict[str, str] = {}
    if api_key and api_key != "lm-studio":
        headers["Authorization"] = f"Bearer {api_key}"

    base = api_base.rstrip("/")
    candidate_urls: list[str] = []
    if base.endswith("/v1"):
        candidate_urls.append(f"{base}/models")
    else:
        candidate_urls.append(f"{base}/v1/models")
        candidate_urls.append(f"{base}/models")
    candidate_urls.append(f"{base}/api/tags")

    target_host = urlsplit(base).hostname or ""
    is_https = urlsplit(base).scheme.lower() == "https"

    try:
        if is_https and resolved_ip:
            transport = _PinnedIPTransport(target_host, resolved_ip)
            client = httpx.AsyncClient(transport=transport, timeout=5.0)
        else:
            client = httpx.AsyncClient(timeout=5.0)
            if not is_https and resolved_ip and target_host != resolved_ip:
                candidate_urls = [
                    _rewrite_url_with_resolved_ip(u, resolved_ip)
                    for u in candidate_urls
                ]
                if target_host:
                    headers = {**headers, "Host": target_host}

        async with client:
            for url in candidate_urls:
                try:
                    resp = await client.get(url, headers=headers)
                    if resp.status_code == 200:
                        models = extract_model_ids_from_response(resp.json())
                        if models:
                            return models
                except Exception:
                    continue
    except Exception as exc:
        _LOGGER.warning(
            "Failed to fetch models from transcription api_base %s: %s",
            api_base,
            exc,
        )

    return fallback
