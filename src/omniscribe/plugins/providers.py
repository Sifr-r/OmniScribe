"""Providers plugin — FastAPI routes + plugin boot.

The service layer (catalog, Pydantic models, SSRF helpers,
``ProviderManager`` Protocol + ``ProviderManagerImpl``) lives in
``omniscribe.plugins.providers_service``. This module owns the
transport layer (FastAPI router) and the plugin boot glue that
registers the manager as a Context service and mounts the router
on the harness.

Public surface re-exported here for backward compatibility:
``ProviderManager``, ``ProviderManagerImpl``, ``PROVIDER_TEMPLATES``,
``build_providers_router``, ``SetActiveProviderRequest`` /
``Response``, ``ValidateProviderRequest`` / ``Response``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException

from omniscribe.config import load_settings
from omniscribe.harness.context import Context
from omniscribe.harness.plugin import Plugin
from omniscribe.plugins.providers_service import (
    PROVIDER_TEMPLATES,
    ProviderManager,
    ProviderManagerImpl,
    ProvidersSchema,
    SetActiveProviderRequest,
    SetActiveProviderResponse,
    ValidateProviderRequest,
    ValidateProviderResponse,
)

__all__ = [
    "PROVIDER_TEMPLATES",
    "ProviderManager",
    "ProviderManagerImpl",
    "ProvidersPlugin",
    "SetActiveProviderRequest",
    "SetActiveProviderResponse",
    "ValidateProviderRequest",
    "ValidateProviderResponse",
    "build_providers_router",
    "plugin",
]


def _bearer_token(authorization: str | None) -> str | None:
    if authorization and authorization.startswith("Bearer "):
        return authorization.removeprefix("Bearer ").strip()
    return None


def build_providers_router(manager: ProviderManagerImpl) -> APIRouter:
    """Catalog, details, and live model discovery routes."""
    router = APIRouter(prefix="/api/providers", tags=["providers"])

    @router.get("")
    async def list_providers() -> dict[str, list[dict[str, Any]]]:
        return {"providers": manager.list_providers()}

    @router.get("/active", status_code=200)
    async def get_active() -> dict[str, str]:
        return manager.get_active()

    @router.get("/{provider_id}")
    async def provider_details(provider_id: str) -> dict[str, Any]:
        preset = manager.get_provider(provider_id)
        if preset is None:
            raise HTTPException(status_code=404, detail="unknown provider")
        return preset

    @router.get("/{provider_id}/models")
    async def provider_models(
        provider_id: str,
        api_base: str | None = None,
        api_key: str | None = None,
        x_provider_api_key: str | None = Header(None, alias="X-Provider-Api-Key"),
        authorization: str | None = Header(None),
    ) -> dict[str, Any]:
        if manager.get_provider(provider_id) is None:
            raise HTTPException(status_code=404, detail="unknown provider")
        resolved_api_key = x_provider_api_key or _bearer_token(authorization) or api_key
        return await manager.discover_models(
            provider_id, api_base=api_base, api_key=resolved_api_key
        )

    @router.post("/active", status_code=200)
    async def set_active(
        payload: SetActiveProviderRequest,
    ) -> SetActiveProviderResponse:
        active = manager.set_active(
            provider_id=payload.provider_id,
            api_base=payload.api_base,
            model=payload.model,
            api_key=payload.api_key,
        )
        return SetActiveProviderResponse(
            status="ok",
            provider_id=active.get("provider_id", payload.provider_id),
            api_base=active.get("api_base", payload.api_base or ""),
            model=active.get("model", payload.model or ""),
        )

    @router.post("/validate", status_code=200)
    async def validate_provider(
        payload: ValidateProviderRequest,
    ) -> ValidateProviderResponse:
        return await manager.validate(
            payload.provider_id,
            api_base=payload.api_base,
            api_key=payload.api_key,
        )

    return router


class ProvidersPlugin(Plugin):
    """Registers the settings-backed ProviderManager and its routes."""

    Schema = ProvidersSchema

    async def apply(self, ctx: Context) -> None:
        from omniscribe.plugins.runtime import RuntimeService

        settings = (
            ctx.inject(RuntimeService).settings
            if ctx.has(RuntimeService)
            else load_settings()
        )
        manager = ProviderManagerImpl(
            settings,
            discovery_timeout_seconds=float(
                self.config.get("discovery_timeout_seconds", 5.0)
            ),
        )
        ctx.service(ProviderManager, manager)
        ctx.mount_router(build_providers_router(manager))


plugin = ProvidersPlugin()
