"""Providers service layer — catalog, SSRF helpers, manager impl.

Audit catalog (Sprint 6 long-file split): ``plugins/providers.py``
mixed the catalog, Pydantic models, SSRF helpers, the manager
Protocol, the manager impl, the FastAPI router, and the plugin
boot glue in one 535-LOC file. This module is the service half:
the static catalog, request/response shapes, DNS-pinning
helpers, the Protocol, and the ``ProviderManagerImpl`` that
talks to the provider ``/models`` and ``/api/tags`` endpoints.

``plugins/providers.py`` is now the transport half (FastAPI
router + plugin boot) and re-exports the public surface
(``ProviderManager``, ``ProviderManagerImpl``,
``PROVIDER_TEMPLATES``, ``build_providers_router``) so
existing imports keep working.
"""

from __future__ import annotations

import ipaddress
import logging
import os
from typing import Any, Literal, Protocol, runtime_checkable
from urllib.parse import urlsplit, urlunsplit

import httpcore
import httpx
from httpcore._backends.auto import AutoBackend
from pydantic import BaseModel, ConfigDict, Field

from omniscribe.config import RuntimeSettings
from omniscribe.core.llm.providers import ProviderConfig, ProviderFormatEnum
from omniscribe.utils.env import persist_env_key
from omniscribe.utils.security import is_blocked_host, is_ssrf_target

_LOGGER = logging.getLogger("omniscribe.plugins.providers")


class _PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    """Network backend that redirects TCP connections for a specific host to a pinned IP."""

    def __init__(self, target_host: str, resolved_ip: str) -> None:
        self._target_host = target_host.lower()
        self._resolved_ip = resolved_ip
        self._backend: httpcore.AsyncNetworkBackend = AutoBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        target = self._resolved_ip if host.lower() == self._target_host else host
        return await self._backend.connect_tcp(
            target,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )


class _PinnedIPTransport(httpx.AsyncHTTPTransport):
    """httpx transport pinning connections to the SSRF-resolved IP without global socket mutation."""

    def __init__(self, target_host: str, resolved_ip: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        backend = _PinnedNetworkBackend(target_host, resolved_ip)
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=self._pool._ssl_context,
            network_backend=backend,
            http2=self._pool._http2,
            retries=self._pool._retries,
        )


def _rewrite_url_with_resolved_ip(url: str, resolved_ip: str) -> str:
    """Rewrite ``url`` so the connection goes to ``resolved_ip``.

    Used for plain HTTP connections to prevent DNS rebinding TOCTOU.
    For HTTPS connections, _PinnedIPTransport is used instead to avoid
    breaking TLS SNI / certificate validation.
    """
    parts = urlsplit(url)
    port = parts.port
    try:
        ip = ipaddress.ip_address(resolved_ip)
        host_literal = (
            f"[{resolved_ip}]" if isinstance(ip, ipaddress.IPv6Address) else resolved_ip
        )
    except ValueError:
        host_literal = resolved_ip
    netloc = host_literal if port is None else f"{host_literal}:{port}"
    return urlunsplit(parts._replace(netloc=netloc))


def _base_hostname(base: str) -> str:
    """Return the hostname component of a base URL string, or empty."""
    try:
        return (urlsplit(base).hostname or "").strip().lower()
    except ValueError:
        return ""


class SetActiveProviderRequest(BaseModel):
    """Payload for ``POST /api/providers/active``.

    ``populate_by_name=True`` accepts both the snake_case field names
    (the Flutter client's actual payload shape — see
    ``client/lib/data/models/provider_preset.dart`` ``SetActiveProviderRequest.toJson``)
    and the camelCase aliases (used by the curl smoke test and any
    non-Flutter client). The response is always snake_case because the
    response model has no aliases.
    """

    model_config = ConfigDict(populate_by_name=True)
    provider_id: str = Field(alias="providerId")
    api_base: str | None = Field(default=None, alias="apiBase")
    api_key: str | None = Field(default=None, alias="apiKey")
    model: str | None = None


class SetActiveProviderResponse(BaseModel):
    """Ack for ``POST /api/providers/active`` — echoes the persisted state.

    snake_case output (no aliases); the Flutter client parses
    ``provider_id`` / ``api_base`` / ``model`` directly.
    """

    status: Literal["ok"]
    provider_id: str
    api_base: str
    model: str


class ValidateProviderRequest(BaseModel):
    """Payload for ``POST /api/providers/validate``.

    ``populate_by_name=True`` accepts both the snake_case field names
    (the Flutter client's actual payload shape — see
    ``client/lib/data/repositories/provider_repository.dart`` ``validateProvider``)
    and the camelCase aliases (used by the curl smoke test and any
    non-Flutter client). The response is always snake_case because the
    response model has no aliases.
    """

    model_config = ConfigDict(populate_by_name=True)
    provider_id: str = Field(alias="providerId")
    api_base: str = Field(alias="apiBase")
    api_key: str | None = Field(default=None, alias="apiKey")
    model: str | None = None


class ValidateProviderResponse(BaseModel):
    """Result of ``POST /api/providers/validate`` — wire probe of provider reachability.

    snake_case output (no aliases); the Flutter client parses
    ``valid`` / ``model_count`` / ``error`` directly.
    """

    valid: bool
    model_count: int
    models: list[str] = Field(default_factory=list)
    error: str | None = None


_O = ProviderFormatEnum.OPENAI_COMPATIBLE
_A = ProviderFormatEnum.ANTHROPIC_COMPATIBLE
_OL = ProviderFormatEnum.OLLAMA_COMPATIBLE

#: Static catalog of known providers. Shapes mirror the frontend's
#: ``ProviderPreset`` contract (id, name, category, description,
#: recommended_base_url, default_model, requires_key, notes).
PROVIDER_TEMPLATES: dict[str, ProviderConfig] = {
    "lmstudio": ProviderConfig(
        id="lmstudio",
        display_name="LM Studio",
        format=_O,
        api_url="http://localhost:1234/v1",
        models=["allenai/olmocr-2-7b"],
        requires_auth=False,
    ),
    "openai": ProviderConfig(
        id="openai",
        display_name="OpenAI",
        format=_O,
        api_url="https://api.openai.com/v1",
        models=["gpt-4o", "gpt-4o-mini"],
    ),
    "anthropic": ProviderConfig(
        id="anthropic",
        display_name="Anthropic",
        format=_A,
        api_url="https://api.anthropic.com",
        models=["claude-sonnet-4-5"],
    ),
    "openrouter": ProviderConfig(
        id="openrouter",
        display_name="OpenRouter",
        format=_O,
        api_url="https://openrouter.ai/api/v1",
        models=[],
    ),
    "ollama": ProviderConfig(
        id="ollama",
        display_name="Ollama",
        format=_OL,
        api_url="http://localhost:11434",
        models=[],
        requires_auth=False,
    ),
    "databricks": ProviderConfig(
        id="databricks",
        display_name="Databricks",
        format=_O,
        api_url="",
        models=[],
    ),
    "azure": ProviderConfig(
        id="azure",
        display_name="Azure OpenAI",
        format=_O,
        api_url="",
        models=[],
    ),
    "groq": ProviderConfig(
        id="groq",
        display_name="Groq",
        format=_O,
        api_url="https://api.groq.com/openai/v1",
        models=[],
    ),
    "deepseek": ProviderConfig(
        id="deepseek",
        display_name="DeepSeek",
        format=_O,
        api_url="https://api.deepseek.com/v1",
        models=["deepseek-chat"],
    ),
    "minimax": ProviderConfig(
        id="minimax",
        display_name="MiniMax",
        format=_O,
        api_url="https://api.minimaxi.com/v1",
        models=[],
    ),
    "litellm": ProviderConfig(
        id="litellm",
        display_name="LiteLLM Proxy",
        format=_O,
        api_url="http://localhost:4000",
        models=[],
        requires_auth=False,
    ),
}

_CATEGORIES = {
    "lmstudio": "local",
    "ollama": "local",
    "litellm": "local",
}
_DESCRIPTIONS = {
    "lmstudio": "Local OpenAI-compatible server (the OmniScribe default).",
    "openai": "OpenAI hosted models.",
    "anthropic": "Anthropic hosted Claude models.",
    "openrouter": "Router across many hosted model vendors.",
    "ollama": "Local models via the Ollama runtime.",
    "databricks": "Databricks model-serving endpoints.",
    "azure": "Azure OpenAI service deployments.",
    "groq": "Groq inference endpoints.",
    "deepseek": "DeepSeek hosted models.",
    "minimax": "MiniMax hosted models.",
    "litellm": "Self-hosted LiteLLM proxy.",
}

_ENV_KEYS: dict[str, list[str]] = {
    "openai": ["OPENAI_API_KEY"],
    "anthropic": ["ANTHROPIC_API_KEY"],
    "groq": ["GROQ_API_KEY"],
    "openrouter": ["OPENROUTER_API_KEY"],
    "deepseek": ["DEEPSEEK_API_KEY"],
    "minimax": ["MINIMAX_API_KEY"],
    "databricks": ["DATABRICKS_TOKEN", "DATABRICKS_API_TOKEN"],
    "azure": ["AZURE_OPENAI_API_KEY"],
}


def _to_preset(config: ProviderConfig) -> dict[str, Any]:
    """Map a catalog entry onto the frontend ``ProviderPreset`` shape."""
    return {
        "id": config.id,
        "name": config.display_name,
        "category": _CATEGORIES.get(config.id, "cloud"),
        "description": _DESCRIPTIONS.get(config.id, ""),
        "recommended_base_url": config.api_url,
        "api_base": config.api_url or None,
        "default_model": config.models[0] if config.models else "",
        "requires_key": config.requires_auth,
        "notes": "" if config.requires_auth else "No API key required.",
        "env_keys": _ENV_KEYS.get(config.id, []),
    }


def _build_endpoint_url(provider_id: str, base: str) -> str:
    cleaned_base = base.rstrip("/")
    if provider_id == "ollama":
        return f"{cleaned_base}/api/tags"
    if provider_id == "anthropic":
        if cleaned_base.endswith("/v1/models"):
            return cleaned_base
        if cleaned_base.endswith("/v1"):
            return f"{cleaned_base}/models"
        return f"{cleaned_base}/v1/models"
    return f"{cleaned_base}/models"


def _build_headers(provider_id: str, api_key: str | None) -> dict[str, str]:
    if provider_id == "anthropic":
        headers = {"anthropic-version": "2023-06-01"}
        if api_key:
            headers["x-api-key"] = api_key
        return headers
    if api_key:
        return {"Authorization": f"Bearer {api_key}"}
    return {}


@runtime_checkable
class ProviderManager(Protocol):
    """Provider catalog + discovery + active-provider seam."""

    def list_providers(self) -> list[dict[str, Any]]: ...

    def get_provider(self, provider_id: str) -> dict[str, Any] | None: ...

    async def discover_models(
        self,
        provider_id: str,
        *,
        api_base: str | None = None,
        api_key: str | None = None,
    ) -> dict[str, Any]: ...

    def get_active(self) -> dict[str, str]: ...

    def set_active(
        self,
        *,
        provider_id: str | None = None,
        api_base: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
    ) -> dict[str, str]: ...

    async def validate(
        self,
        provider_id: str,
        *,
        api_base: str,
        api_key: str | None = None,
    ) -> ValidateProviderResponse: ...


class ProviderManagerImpl:
    """Settings-backed manager; discovery goes through ``httpx.AsyncClient``."""

    def __init__(
        self,
        settings: RuntimeSettings,
        *,
        discovery_timeout_seconds: float,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._timeout = discovery_timeout_seconds
        self._client = http_client
        self._active_provider_id: str | None = None
        active_base = getattr(self._settings, "llm_api_base", "") or ""
        active_host = _base_hostname(active_base)
        if active_host:
            for pid, cfg in PROVIDER_TEMPLATES.items():
                if cfg.api_url and _base_hostname(cfg.api_url) == active_host:
                    self._active_provider_id = pid
                    break

    def list_providers(self) -> list[dict[str, Any]]:
        return [_to_preset(config) for config in PROVIDER_TEMPLATES.values()]

    def get_provider(self, provider_id: str) -> dict[str, Any] | None:
        config = PROVIDER_TEMPLATES.get(provider_id)
        return _to_preset(config) if config is not None else None

    def _is_active_provider(self, provider_id: str, base: str) -> bool:
        if self._active_provider_id == provider_id:
            return True
        active_base = (getattr(self._settings, "llm_api_base", "") or "").rstrip("/")
        if not active_base:
            return False
        active_host = _base_hostname(active_base)
        base_host = _base_hostname(base)
        if active_host and base_host and active_host == base_host:
            return True
        config = PROVIDER_TEMPLATES.get(provider_id)
        return bool(
            config and config.api_url and _base_hostname(config.api_url) == active_host
        )

    def _resolve_api_key(
        self, provider_id: str, api_key: str | None, base: str
    ) -> str | None:
        if api_key is not None and api_key.strip():
            return api_key.strip()

        for env_var in _ENV_KEYS.get(provider_id, []):
            val = os.environ.get(env_var, "").strip()
            if val:
                return val

        if self._is_active_provider(provider_id, base):
            settings_key = (getattr(self._settings, "llm_api_key", "") or "").strip()
            if settings_key:
                category = _CATEGORIES.get(provider_id, "cloud")
                if category == "cloud" and settings_key == "lm-studio":
                    return None
                return settings_key

        return None

    async def _probe_models(
        self,
        provider_id: str,
        base: str,
        api_key: str | None,
        fallback: list[str],
    ) -> tuple[list[str], str | None]:
        ssrf_check = await is_ssrf_target(base)
        if not ssrf_check.allowed:
            return (
                fallback,
                f"Invalid provider URL (SSRF blocked: {ssrf_check.reason})",
            )

        resolved_key = self._resolve_api_key(provider_id, api_key, base)
        url = _build_endpoint_url(provider_id, base)
        headers = _build_headers(provider_id, resolved_key)

        original_host = _base_hostname(base)
        is_https = urlsplit(url).scheme.lower() == "https"

        if (
            not is_https
            and ssrf_check.resolved_ip
            and original_host != ssrf_check.resolved_ip
        ):
            url = _rewrite_url_with_resolved_ip(url, ssrf_check.resolved_ip)
            headers = {**headers, "Host": original_host}

        if self._client is not None:
            client = self._client
            should_close = False
        else:
            should_close = True
            if is_https and ssrf_check.resolved_ip:
                transport = _PinnedIPTransport(original_host, ssrf_check.resolved_ip)
                client = httpx.AsyncClient(transport=transport, timeout=self._timeout)
            else:
                client = httpx.AsyncClient(timeout=self._timeout)

        try:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            payload = response.json()
        finally:
            if should_close:
                await client.aclose()

        if provider_id == "ollama":
            models = [str(entry["name"]) for entry in payload.get("models", [])]
        else:
            models = [str(entry["id"]) for entry in payload.get("data", [])]

        return (models or fallback), None

    async def discover_models(
        self,
        provider_id: str,
        *,
        api_base: str | None = None,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        config = PROVIDER_TEMPLATES.get(provider_id)
        fallback = list(config.models) if config is not None else []
        base = (api_base or (config.api_url if config else "")).rstrip("/")
        if not base:
            return {"models": fallback, "error": "no base URL for provider"}
        try:
            models, error = await self._probe_models(
                provider_id, base, api_key, fallback
            )
            return {"models": models, "error": error}
        except Exception as exc:
            _LOGGER.warning("model discovery failed for %s: %s", provider_id, exc)
            return {"models": fallback, "error": str(exc)}

    def get_active(self) -> dict[str, str]:
        return {
            "provider_id": self._active_provider_id or "",
            "api_base": self._settings.llm_api_base,
            "model": self._settings.llm_model,
        }

    def set_active(
        self,
        *,
        provider_id: str | None = None,
        api_base: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
    ) -> dict[str, str]:
        config = PROVIDER_TEMPLATES.get(provider_id) if provider_id else None

        resolved_api_base = api_base
        if not resolved_api_base:
            resolved_api_base = (
                config.api_url
                if config and config.api_url
                else self._settings.llm_api_base
            )

        resolved_model = model
        if not resolved_model:
            resolved_model = (
                getattr(config, "default_model", None)
                or (config.models[0] if config and config.models else None)
                or self._settings.llm_model
            )

        # C-4 / H-4 audit fix: refuse to write an api_base that fails
        # the SSRF guard. Combined with the (deferred) auth middleware,
        # an unauthenticated caller could otherwise point the OCR pipeline
        # at an attacker-controlled VLM. The deferred-capability story in
        # AGENTS.md documents this; until the per-route provider catalog
        # gate lands, every api_base write is SSRF-validated.
        # set_active is sync; run the SSRF check synchronously via
        # is_blocked_host (no DNS, just URL/host parsing). For full DNS
        # pinning, callers should use the validate() round-trip.
        if resolved_api_base and is_blocked_host(
            resolved_api_base.split("//", 1)[-1].split("/")[0].split(":")[0]
        ):
            raise ValueError(
                f"api_base {resolved_api_base!r} is blocked by the SSRF guard "
                "(private / loopback / metadata range). Use a public URL."
            )

        self._settings.llm_api_base = resolved_api_base
        self._settings.llm_model = resolved_model
        if api_key:
            self._settings.llm_api_key = api_key
        if provider_id:
            self._active_provider_id = provider_id
        else:
            active_host = _base_hostname(resolved_api_base)
            for pid, cfg in PROVIDER_TEMPLATES.items():
                if cfg.api_url and _base_hostname(cfg.api_url) == active_host:
                    self._active_provider_id = pid
                    break

        persist_env_key("LLM_API_BASE", self._settings.llm_api_base)
        persist_env_key("LLM_MODEL", self._settings.llm_model)
        if (
            getattr(self._settings, "llm_api_key", None)
            and self._settings.llm_api_key.strip()
        ):
            persist_env_key("LLM_API_KEY", self._settings.llm_api_key)

        return self.get_active()

    async def validate(
        self,
        provider_id: str,
        *,
        api_base: str,
        api_key: str | None = None,
    ) -> ValidateProviderResponse:
        config = PROVIDER_TEMPLATES.get(provider_id)
        if config is None:
            return ValidateProviderResponse(
                valid=False, model_count=0, models=[], error="unknown provider"
            )
        fallback = list(config.models)
        base = (api_base or config.api_url or "").rstrip("/")
        if not base:
            return ValidateProviderResponse(
                valid=False, model_count=0, models=[], error="no base URL for provider"
            )
        try:
            models, error = await self._probe_models(
                provider_id, base, api_key, fallback
            )
            if error is not None:
                return ValidateProviderResponse(
                    valid=False, model_count=0, models=[], error=error
                )
            return ValidateProviderResponse(
                valid=True,
                model_count=len(models),
                models=models,
                error=None,
            )
        except Exception as exc:
            _LOGGER.warning("validate failed for %s: %s", provider_id, exc)
            return ValidateProviderResponse(
                valid=False, model_count=0, models=[], error=str(exc)
            )


class ProvidersSchema(BaseModel):
    discovery_timeout_seconds: float = 5.0
