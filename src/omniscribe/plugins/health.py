"""Health plugin — liveness and readiness probes.

``/api/health`` and ``/api/healthz`` always answer ``200 {"status":"ok"}``
(process is alive). ``/ready`` and ``/readyz`` answer ``503
{"status":"starting"}`` until the runtime plugin marks the harness ready,
then ``200 {"status":"ready"}`` — orchestrators use the split to gate
traffic on boot completion.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from omniscribe.harness.context import Context
from omniscribe.harness.plugin import Plugin
from omniscribe.plugins.runtime import RuntimeService


def build_health_router(runtime: RuntimeService) -> APIRouter:
    router = APIRouter(tags=["health"])

    @router.get("/api/health")
    @router.get("/api/healthz")
    async def liveness() -> dict[str, str]:
        """Liveness probe — answers 200 as soon as the process is up."""
        return {"status": "ok"}

    @router.get("/ready")
    @router.get("/readyz")
    async def readiness() -> JSONResponse:
        """Readiness probe — answers 200 only after the runtime reports ready."""
        if runtime.ready:
            return JSONResponse({"status": "ready"}, status_code=200)
        return JSONResponse({"status": "starting"}, status_code=503)

    return router


class HealthPlugin(Plugin):
    """Mounts the probe routes against the injected RuntimeService."""

    async def apply(self, ctx: Context) -> None:
        runtime = ctx.inject(RuntimeService)
        ctx.mount_router(build_health_router(runtime))


plugin = HealthPlugin()
