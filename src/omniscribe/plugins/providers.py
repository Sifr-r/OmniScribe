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
from omniscribe.plugins._http import bearer_token
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


def build_providers_router(manager: ProviderManagerImpl) -> APIRouter:
    """Catalog, details, and live model discovery routes."""
    router = APIRouter(prefix="/api/providers", tags=["providers"])

    @router.get("")
    async def list_providers() -> dict[str, list[dict[str, Any]]]:
        """List every provider preset known to the manager.

        Powers the Flutter client's provider picker. Each entry is a
        dict with the metadata the UI needs (name, default api-base
        pattern, supported model kinds).
        """
        return {"providers": manager.list_providers()}

    @router.get("/active", status_code=200)
    async def get_active() -> dict[str, str]:
        """Return the currently-active provider id, api-base, and model.

        ``{}`` is returned when no provider is active yet (fresh
        install or after the operator cleared it).
        """
        return manager.get_active()

    @router.get("/{provider_id}")
    async def provider_details(provider_id: str) -> dict[str, Any]:
        """Return the full preset dict for ``provider_id``.

        404 when the id is unknown so the UI can distinguish a typo
        from a transient backend error.
        """
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
        """Live model-list discovery against the provider's ``/v1/models``.

        The api-key resolution order is: explicit ``X-Provider-Api-Key``
        header → standard ``Authorization: Bearer`` header → query
        ``api_key`` parameter. The first non-empty wins so the Flutter
        client can keep its secrets in the header without leaking them
        to the URL (which lands in proxy access logs).
        """
        if manager.get_provider(provider_id) is None:
            raise HTTPException(status_code=404, detail="unknown provider")
        resolved_api_key = x_provider_api_key or bearer_token(authorization) or api_key
        return await manager.discover_models(
            provider_id, api_base=api_base, api_key=resolved_api_key
        )

    @router.post("/active", status_code=200)
    async def set_active(
        payload: SetActiveProviderRequest,
    ) -> SetActiveProviderResponse:
        """Persist the new active provider + api-base + model + api-key.

        The api-key is only persisted if non-empty (the request can be
        used to change provider/model without rotating the key). The
        response echoes the resolved values back so the caller can
        confirm what the server actually stored.
        """
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
        """Probe the provider's ``/v1/models`` endpoint without storing anything.

        Used by the setup wizard to confirm the operator's api-base +
        api-key actually reach a live OpenAI-compatible server before
        they commit the values to the active provider slot.
        """
        return await manager.validate(
            payload.provider_id,
            api_base=payload.api_base,
            api_key=payload.api_key,
        )

    return router


class ProvidersPlugin(Plugin):
    """Registers the settings-backed ProviderManager and its routes.

    On ``apply``: construct the :class:`ProviderManagerImpl` with the
    current runtime settings (or fall back to a fresh
    :func:`load_settings` call when the runtime plugin is absent),
    bind it under the :class:`ProviderManager` Protocol so other
    plugins can look it up, and mount the router at
    ``/api/providers``.
    """

    Schema = ProvidersSchema

    async def apply(self, ctx: Context) -> None:
        """Wire the providers plugin into the harness context."""
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
