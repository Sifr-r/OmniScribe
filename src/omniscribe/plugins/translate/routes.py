"""HTTP routes for the translate plugin (client-frozen contract)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from omniscribe.plugins._http import envelope
from omniscribe.plugins.translate.schemas import (
    AsyncTranslationRequest,
    NllbRequest,
    TranslationRequest,
)
from omniscribe.plugins.translate.service import (
    TranslateError,
    TranslationService,
)


def build_translate_router(service: TranslationService) -> APIRouter:
    """Build and return the FastAPI router for the translate plugin.

    Exposes the client-frozen contract documented in this module's
    docstring. The five endpoints cover:

    * ``POST /api/translate`` — synchronous translation of inline text
      or a referenced text artifact.
    * ``POST /api/translate/async`` — enqueue an async translation job
      on the in-process harness JobQueue.
    * ``GET /api/translate/status/{job_id}`` — poll an async job's
      status (queued / running / completed / failed).
    * ``GET /api/translate/result/{job_id}`` — fetch the token-bound
      result artifact for a completed job.
    * ``POST /api/translate/nllb`` — direct NLLB-200 inference for
      clients that have not opted into the async tree-aware path.
    """
    router = APIRouter(tags=["translate"])

    @router.post("/api/translate", response_model=None)
    async def translate(body: TranslationRequest) -> dict[str, Any] | JSONResponse:
        """Synchronously translate inline text or a referenced artifact.

        The route accepts EITHER an inline ``text`` payload OR a
        ``text_artifact_id`` + ``text_artifact_token`` pair. Both empty
        returns the standard 400 envelope so the client sees a uniform
        error rather than a 500 from a downstream empty-input crash.
        """
        if not body.text.strip() and not (
            body.text_artifact_id and body.text_artifact_token
        ):
            return envelope(
                400,
                "bad_request",
                "'text' or 'text_artifact_id'/'text_artifact_token' is required",
            )
        try:
            translated = await service.translate_sync(body)
        except TranslateError:
            raise
        return {"translated_text": translated}

    @router.post("/api/translate/async", response_model=None)
    async def translate_async(
        body: AsyncTranslationRequest,
    ) -> dict[str, Any] | JSONResponse:
        """Enqueue an async translation job.

        The artifact pair is optional-with-bounds on the schema, so a
        missing pair never 422s; the route owns the 400 contract. The
        response is the same shape returned by the in-process
        JobQueue submit path (``job_id`` + ``status``).
        """
        # The artifact pair is optional-with-bounds on the schema, so a
        # missing pair never 422s; the route owns the 400 contract.
        if not (body.text_artifact_id and body.text_artifact_token):
            return envelope(
                400,
                "bad_request",
                "'text_artifact_id'/'text_artifact_token' is required",
            )
        try:
            return await service.submit(body)
        except TranslateError:
            raise

    @router.get("/api/translate/status/{job_id}", response_model=None)
    async def translation_status(
        job_id: str,
    ) -> dict[str, Any] | JSONResponse:
        """Return the current status of an async translation job.

        An unknown ``job_id`` is mapped to 404 rather than 200-with-null
        so the client can treat it as a terminal failure to display.
        """
        body = await service.job_status(job_id)
        if body is None:
            return envelope(404, "not_found", "unknown job")
        return body

    @router.get("/api/translate/result/{job_id}", response_model=None)
    async def translate_result(
        job_id: str,
        token: str = "",
    ) -> dict[str, Any] | JSONResponse:
        """Fetch the token-bound translation result for a completed job.

        Missing/wrong token, unknown job, or still-running job all map
        to the same 404 (no existence leak — see C-3/H-3 semantics in
        ``docs/SECURITY.md``).
        """
        body = await service.result(job_id, token)
        if body is None:
            # Missing/wrong token, unknown job, or incomplete job all map to
            # the same 404 (no existence leak; C-3/H-3 semantics).
            return envelope(404, "not_found", "result not found")
        return body

    @router.post("/api/translate/nllb", response_model=None)
    async def translate_nllb(body: NllbRequest) -> dict[str, Any] | JSONResponse:
        """Run a one-shot NLLB-200 translation on the local model.

        Kept as a separate route from :func:`translate` because the
        NLLB backend has a different prompt surface (no glossary
        injection, no async queue) and is exercised by clients that
        specifically want raw NLLB behaviour without tree-aware
        postprocessing.
        """
        try:
            return await service.translate_nllb(body.text, body.target_language)
        except TranslateError:
            raise

    return router
