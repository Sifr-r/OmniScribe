# Python boundary refactor — 2026-09-29

This is the dated refactor record. Before/after snippets and test counts describe
the 2026-09-29 work; they are not fresh validation of subsequent edits.
Current follow-up verification is recorded separately in
[the 2026-09-30 document-state review](document-state-review-2026-09-30.md).

The backend already groups its business features under `plugins/<domain>/`.
Keep that working ownership. This change removes 19 redundant route
catch-and-reraise blocks, moves OCR business failures onto the existing
framework-independent `PluginError`, validates transcription responses, and
repairs manual request-validation JSON serialization. No new runtime file,
repository abstraction, dependency, route URL, token rule, queue behavior,
or OpenAPI snapshot change is introduced.

## Concrete layout and file responsibilities

```text
src/omniscribe/plugins/
  _http.py                         existing capability helpers and HTTP error mapping
  errors.py                        existing framework-independent PluginError
  documents/routes.py              extraction/export HTTP validation and responses
  glossary/routes.py               glossary HTTP input validation and response mapping
  ocr/
    pipeline_bridge.py             validated request-to-pipeline assembly
    service.py                     OCR jobs, artifact access and runtime orchestration
  transcribe/
    routes.py                      upload/config HTTP validation and transcript serialization
    schemas.py                     typed transcription request/config/segment/output contracts
  translate/routes.py              translation HTTP validation, submission and result responses
tests/
  plugins/test_ocr_plugin.py        isolated OCR harness and endpoint behavior proof
  plugins/test_pipeline_bridge.py  assembly, SSRF and credential isolation proof
  routers/test_transcribe_routes.py transcript payload and request/output validation proof
docs/backend-refactor-details-2026-09-29.md
                                   reviewable before/after evidence and verification record
```

Every changed runtime file retains its existing responsibility. The only added
runtime type is `TranscriptionSegmentResponse` in the existing feature schema
module. `_http.py` and `errors.py` are reused without edits.

## Behavior boundaries

- Routes let domain exceptions reach the existing application-level handler.
  The glossary URL fetch still explicitly propagates `GlossaryError` before
  translating other external-fetch exceptions; that catch has real behavior.
- OCR SSRF failures keep HTTP 400, `bad_request`, and their original detail.
  Result capability misses keep HTTP 404, `not_found`, and
  `result not available`; token comparison and artifact access are unchanged.
  OCR continues returning binary `Response` objects for established download
  paths; a broader binary-output rewrite would add churn without changing
  domain ownership.
- Transcription response fields use strict existing Pydantic contracts, typed
  segments, and finite numbers. `extra="allow"` retains provider extension
  fields and `exclude_unset=True` retains the distinction between absent
  fields and explicit nulls. Invalid internal outputs produce a sanitized
  HTTP 500 envelope without exposing validation inputs.
- Manual Pydantic validation responses are passed through FastAPI's existing
  `jsonable_encoder`. Engine/temperature validators include a `ValueError`
  in their context; raw `JSONResponse` previously failed to encode it and
  raised a server error. The response remains the existing HTTP 422
  `{"detail": [...]}` shape.
- The isolated OCR test application now registers the same shared domain-error
  handler as production. Existing direct bridge tests assert `PluginError`
  and its unchanged status/detail/tag.

## Verification

Baseline: **220 passed** across all router tests, OCR plugin/pipeline,
transcription service and harness error tests (102.60 s). Two new malformed
transcription-option tests failed before the fix with
`TypeError: Object of type ValueError is not JSON serializable`.

Response/request-focused proof after the fix: **14 passed**, including absent
versus null fields, extension fields, invalid segment IDs, NaN/infinite
numbers (including provider extension fields), and serializable 422 errors.
Modified-file Ruff and formatting checks passed for all ten changed Python
files. Full mypy passed for **221 source files**; the final serializer change
also passed an incremental mypy check for both transcription modules. The
expanded focused final repeat passed **237 tests** (74.89 s), including the
OpenAPI snapshot and the extension-NaN regression, after registering the
production domain-error handler in the isolated OCR test harness. The root
task owns the global fast gate. Only the installed Starlette/httpx deprecation
warning remained; no test failures or cache-permission warnings remained in
the final focused run.

Environment: `uv run` could not initialize its user cache, so checks use the
already-installed `.venv/Scripts` tools. The initial pytest run warned that
the existing `.pytest_cache` was locked; no operation retried that directory.
Subsequent checks use separate caches inside `.agent-tmp/`.

## Exact before-and-after differences

The following unified diff is the complete change for every modified Python
file: deleted lines are the exact before version; added lines are the exact
after version. No additional architectural recommendation requires a hidden
file move or a future abstraction.

```diff
diff --git a/src/omniscribe/plugins/documents/routes.py b/src/omniscribe/plugins/documents/routes.py
index 0ee751e..f1864d7 100644
--- a/src/omniscribe/plugins/documents/routes.py
+++ b/src/omniscribe/plugins/documents/routes.py
@@ -47,7 +47,6 @@ from omniscribe.plugins.documents.schemas import (
 )
 from omniscribe.plugins.documents.service import (
     EXPORT_MEDIA_TYPES,
-    DocumentsError,
     build_chunks_export,
     build_document_export,
     build_markdown_export,
@@ -116,16 +115,13 @@ async def handle_extract(
     """Run structured extraction against an OCR text blob.
 
     Validates that ``body.text`` is non-empty (returns 400 otherwise) and
-    delegates to :func:`run_extraction`. ``DocumentsError`` is re-raised
-    so the FastAPI exception handler can map it to the standard error
+    delegates to :func:`run_extraction`. Domain errors propagate
+    so the FastAPI exception handler can map them to the standard error
     envelope; any other exception is the service's responsibility.
     """
     if not body.text.strip():
         return envelope(400, "bad_request", "'text' is required")
-    try:
-        extracted = await run_extraction(body, settings)
-    except DocumentsError:
-        raise
+    extracted = await run_extraction(body, settings)
     return {"extracted_data": extracted}
 
 
diff --git a/src/omniscribe/plugins/glossary/routes.py b/src/omniscribe/plugins/glossary/routes.py
index 86cc129..6106e03 100644
--- a/src/omniscribe/plugins/glossary/routes.py
+++ b/src/omniscribe/plugins/glossary/routes.py
@@ -15,6 +15,7 @@ from typing import Any
 from urllib.parse import urlparse
 
 from fastapi import APIRouter, Body, Request
+from fastapi.encoders import jsonable_encoder
 from fastapi.responses import JSONResponse
 from pydantic import ValidationError
 
@@ -130,7 +131,7 @@ def build_glossary_router(service: GlossaryImportService) -> APIRouter:
             except ValidationError as exc:
                 return JSONResponse(
                     status_code=422,
-                    content={"detail": exc.errors(include_url=False)},
+                    content={"detail": jsonable_encoder(exc.errors(include_url=False))},
                 )
         else:
             try:
@@ -142,14 +143,10 @@ def build_glossary_router(service: GlossaryImportService) -> APIRouter:
             except ValidationError as exc:
                 return JSONResponse(
                     status_code=422,
-                    content={"detail": exc.errors(include_url=False)},
+                    content={"detail": jsonable_encoder(exc.errors(include_url=False))},
                 )
 
-        try:
-            body = await service.import_glossary(source.source)
-        except GlossaryError:
-            raise
-        return body
+        return await service.import_glossary(source.source)
 
     @router.post("/api/glossary/import/url", response_model=None)
     async def import_glossary_from_url(
@@ -179,7 +176,7 @@ def build_glossary_router(service: GlossaryImportService) -> APIRouter:
             except ValidationError as exc:
                 return JSONResponse(
                     status_code=422,
-                    content={"detail": exc.errors(include_url=False)},
+                    content={"detail": jsonable_encoder(exc.errors(include_url=False))},
                 )
             url = body_model.url
             name = body_model.name
@@ -216,13 +213,9 @@ def build_glossary_router(service: GlossaryImportService) -> APIRouter:
         except ValidationError as exc:
             return JSONResponse(
                 status_code=422,
-                content={"detail": exc.errors(include_url=False)},
+                content={"detail": jsonable_encoder(exc.errors(include_url=False))},
             )
-        try:
-            body = await service.import_glossary(source.source)
-        except GlossaryError:
-            raise
-        return body
+        return await service.import_glossary(source.source)
 
     @router.get("/api/glossary/sources", response_model=None)
     async def list_sources() -> list[dict[str, Any]] | JSONResponse:
@@ -232,11 +225,8 @@ def build_glossary_router(service: GlossaryImportService) -> APIRouter:
         older Flutter client contract; both endpoints return the same
         payload.
         """
-        try:
-            service.ensure_store_ready()
-            return service.list_library()
-        except GlossaryError:
-            raise
+        service.ensure_store_ready()
+        return service.list_library()
 
     @router.delete("/api/glossary/sources/{source_id}", response_model=None)
     async def delete_source(source_id: str) -> dict[str, Any] | JSONResponse:
@@ -246,11 +236,8 @@ def build_glossary_router(service: GlossaryImportService) -> APIRouter:
         ``/api/glossary/sources/{id}`` route is preserved for clients
         that have not migrated to ``/api/glossary/library/{id}``.
         """
-        try:
-            service.ensure_store_ready()
-            return service.delete(source_id)
-        except GlossaryError:
-            raise
+        service.ensure_store_ready()
+        return service.delete(source_id)
 
     @router.get("/api/glossary/library", response_model=None)
     async def list_library() -> list[dict[str, Any]] | JSONResponse:
@@ -260,11 +247,8 @@ def build_glossary_router(service: GlossaryImportService) -> APIRouter:
         user-configured reorder). The Flutter client uses this to
         populate the glossary management screen.
         """
-        try:
-            service.ensure_store_ready()
-            return service.list_library()
-        except GlossaryError:
-            raise
+        service.ensure_store_ready()
+        return service.list_library()
 
     @router.post("/api/glossary/library/{glossary_id}/enable", response_model=None)
     async def toggle_library_entry(
@@ -277,11 +261,8 @@ def build_glossary_router(service: GlossaryImportService) -> APIRouter:
         (``true`` or ``false``) — there is no implicit-flip variant on
         this endpoint.
         """
-        try:
-            service.ensure_store_ready()
-            return service.toggle(glossary_id, enabled=req.enabled)
-        except GlossaryError:
-            raise
+        service.ensure_store_ready()
+        return service.toggle(glossary_id, enabled=req.enabled)
 
     @router.post("/api/glossary/library/{source_id}/toggle", response_model=None)
     async def toggle_source(
@@ -294,12 +275,9 @@ def build_glossary_router(service: GlossaryImportService) -> APIRouter:
         (handy for the Flutter "tap to toggle" UI); when a body is
         provided the explicit ``enabled`` value wins.
         """
-        try:
-            service.ensure_store_ready()
-            enabled = body.enabled if body is not None else None
-            return service.toggle(source_id, enabled=enabled)
-        except GlossaryError:
-            raise
+        service.ensure_store_ready()
+        enabled = body.enabled if body is not None else None
+        return service.toggle(source_id, enabled=enabled)
 
     @router.post("/api/glossary/library/reorder", response_model=None)
     async def reorder_library(
@@ -311,11 +289,8 @@ def build_glossary_router(service: GlossaryImportService) -> APIRouter:
         validates that every id is known and that no entry is missing
         before persisting the new ordering.
         """
-        try:
-            service.ensure_store_ready()
-            return service.reorder(req.ordered_ids)
-        except GlossaryError:
-            raise
+        service.ensure_store_ready()
+        return service.reorder(req.ordered_ids)
 
     @router.delete("/api/glossary/library/{glossary_id}", response_model=None)
     async def delete_library_entry(
@@ -328,11 +303,8 @@ def build_glossary_router(service: GlossaryImportService) -> APIRouter:
         row level (a missing id returns the standard not-found
         envelope).
         """
-        try:
-            service.ensure_store_ready()
-            return service.delete(glossary_id)
-        except GlossaryError:
-            raise
+        service.ensure_store_ready()
+        return service.delete(glossary_id)
 
     @router.get("/api/glossary/library/preview", response_model=None)
     async def library_preview() -> dict[str, Any] | JSONResponse:
@@ -342,11 +314,8 @@ def build_glossary_router(service: GlossaryImportService) -> APIRouter:
         first few entries per language pair so the user can sanity-check
         an import without paging through the full library.
         """
-        try:
-            service.ensure_store_ready()
-            return service.library_preview()
-        except GlossaryError:
-            raise
+        service.ensure_store_ready()
+        return service.library_preview()
 
     @router.get("/api/glossary/library/entries", response_model=None)
     async def get_library_entries(
@@ -361,16 +330,13 @@ def build_glossary_router(service: GlossaryImportService) -> APIRouter:
         filter; ``limit`` and ``offset`` paginate. The merged view is
         used when ``source_id`` is omitted.
         """
-        try:
-            service.ensure_store_ready()
-            return service.entries(
-                glossary_id=source_id,
-                query=q,
-                limit=limit,
-                offset=offset,
-            )
-        except GlossaryError:
-            raise
+        service.ensure_store_ready()
+        return service.entries(
+            glossary_id=source_id,
+            query=q,
+            limit=limit,
+            offset=offset,
+        )
 
     @router.get("/api/glossary/library/{glossary_id}/entries", response_model=None)
     async def library_entries(
@@ -384,16 +350,13 @@ def build_glossary_router(service: GlossaryImportService) -> APIRouter:
         Same shape as :func:`get_library_entries` but always scoped to
         the path-parameter glossary; ``source_id`` is unnecessary here.
         """
-        try:
-            service.ensure_store_ready()
-            return service.entries(
-                glossary_id=glossary_id,
-                query=q,
-                limit=limit,
-                offset=offset,
-            )
-        except GlossaryError:
-            raise
+        service.ensure_store_ready()
+        return service.entries(
+            glossary_id=glossary_id,
+            query=q,
+            limit=limit,
+            offset=offset,
+        )
 
     @router.get("/api/glossary/library/merged", response_model=None)
     async def merged_entries() -> dict[str, Any] | JSONResponse:
@@ -403,10 +366,7 @@ def build_glossary_router(service: GlossaryImportService) -> APIRouter:
         so the translation pipeline can consult the full vocabulary in
         one round-trip instead of fetching per-source.
         """
-        try:
-            service.ensure_store_ready()
-            return service.merged()
-        except GlossaryError:
-            raise
+        service.ensure_store_ready()
+        return service.merged()
 
     return router
diff --git a/src/omniscribe/plugins/ocr/pipeline_bridge.py b/src/omniscribe/plugins/ocr/pipeline_bridge.py
index 1b3857e..c78acc3 100644
--- a/src/omniscribe/plugins/ocr/pipeline_bridge.py
+++ b/src/omniscribe/plugins/ocr/pipeline_bridge.py
@@ -18,8 +18,6 @@ from collections.abc import Awaitable, Callable
 from typing import Any
 from urllib.parse import urlsplit
 
-from fastapi import HTTPException
-
 from omniscribe.config import RuntimeSettings
 from omniscribe.core.callbacks import BlockCallbackSet
 from omniscribe.core.document import SpellcheckMode
@@ -29,6 +27,7 @@ from omniscribe.core.imaging.page_preprocess import (
 )
 from omniscribe.core.workflows.repair import RepairOptions
 from omniscribe.pipeline import OCRPipeline
+from omniscribe.plugins.errors import PluginError
 from omniscribe.plugins.ocr.schemas import OCRRequest
 from omniscribe.utils.security import (
     _rewrite_url_with_resolved_ip,
@@ -63,9 +62,10 @@ def build_pipeline(
     if clean_base:
         check = check_ssrf_target_sync(clean_base)
         if not check.allowed:
-            raise HTTPException(
-                status_code=400,
-                detail=f"Invalid api_base URL (SSRF blocked: {check.reason})",
+            raise PluginError(
+                400,
+                "bad_request",
+                f"Invalid api_base URL (SSRF blocked: {check.reason})",
             )
         resolved_ip = check.resolved_ip
         if not is_same_origin(clean_base, settings.llm_api_base):
diff --git a/src/omniscribe/plugins/ocr/service.py b/src/omniscribe/plugins/ocr/service.py
index b5e9092..acf5262 100644
--- a/src/omniscribe/plugins/ocr/service.py
+++ b/src/omniscribe/plugins/ocr/service.py
@@ -26,7 +26,6 @@ from datetime import UTC, datetime
 from pathlib import Path
 from typing import Any, cast
 
-from fastapi import HTTPException
 from fastapi.responses import Response
 
 from omniscribe.config import RuntimeSettings
@@ -36,6 +35,7 @@ from omniscribe.core.readers import get_reader_for_suffix, render_synthetic_pdf
 from omniscribe.core.workflows.base import OCRCancelled
 from omniscribe.harness.events import Event
 from omniscribe.plugins.artifacts import ArtifactStore
+from omniscribe.plugins.errors import PluginError
 from omniscribe.plugins.jobs import (
     JobCancelled,
     JobCompleted,
@@ -856,7 +856,7 @@ class OCRServiceImpl:
         to the caller; the only successful code is 200 with the
         PDF bytes.
         """
-        not_found = HTTPException(status_code=404, detail="result not available")
+        not_found = PluginError(404, "not_found", "result not available")
         record = await self._queue.status(job_id)
         # Token compare runs only when there is a token to compare
         # against (i.e. a record with a stored result_artifact_token).
@@ -1119,16 +1119,17 @@ class OCRServiceImpl:
             The updated effective configuration dictionary.
 
         Raises:
-            HTTPException: If an ``api_base`` update fails SSRF validation.
+            PluginError: If an ``api_base`` update fails SSRF validation.
         """
         if "api_base" in updates and updates["api_base"] is not None:
             new_base = str(updates["api_base"]).strip()
             if new_base and new_base != self._config.get("api_base"):
                 check = check_ssrf_target_sync(new_base)
                 if not check.allowed:
-                    raise HTTPException(
-                        status_code=400,
-                        detail=f"Invalid api_base URL (SSRF blocked: {check.reason})",
+                    raise PluginError(
+                        400,
+                        "bad_request",
+                        f"Invalid api_base URL (SSRF blocked: {check.reason})",
                     )
         numeric_keys = ("dpi", "concurrency", "dense_threshold", "max_image_dim")
         numeric_updates = {
diff --git a/src/omniscribe/plugins/transcribe/routes.py b/src/omniscribe/plugins/transcribe/routes.py
index 1de7fd5..4f45b47 100644
--- a/src/omniscribe/plugins/transcribe/routes.py
+++ b/src/omniscribe/plugins/transcribe/routes.py
@@ -10,6 +10,7 @@ from __future__ import annotations
 from typing import Any
 
 from fastapi import APIRouter, Request
+from fastapi.encoders import jsonable_encoder
 from fastapi.responses import JSONResponse
 from pydantic import ValidationError
 
@@ -18,11 +19,9 @@ from omniscribe.plugins.transcribe.schemas import (
     TranscribeRequest,
     TranscriptionConfigResponse,
     TranscriptionConfigUpdate,
+    TranscriptionJobResponse,
 )
-from omniscribe.plugins.transcribe.service import (
-    TranscribeError,
-    TranscriptionService,
-)
+from omniscribe.plugins.transcribe.service import TranscriptionService
 
 
 def build_transcribe_router(service: TranscriptionService) -> APIRouter:
@@ -41,7 +40,7 @@ def build_transcribe_router(service: TranscriptionService) -> APIRouter:
     router = APIRouter(tags=["transcribe"])
 
     @router.post("/api/transcribe", response_model=None)
-    async def transcribe_audio(request: Request) -> Any:
+    async def transcribe_audio(request: Request) -> JSONResponse:
         """Transcribe an uploaded audio file (multipart/form-data).
 
         Reads the ``file`` part and any string-typed form fields, then
@@ -65,20 +64,27 @@ def build_transcribe_router(service: TranscriptionService) -> APIRouter:
         except ValidationError as exc:
             return JSONResponse(
                 status_code=422,
-                content={"detail": exc.errors(include_url=False)},
+                content={"detail": jsonable_encoder(exc.errors(include_url=False))},
             )
         filename = str(getattr(upload, "filename", "") or "") or "audio.wav"
         content_type = getattr(upload, "content_type", "") or None
+        result = await service.transcribe(
+            options,
+            file_bytes=file_bytes,
+            filename=filename,
+            content_type=content_type,
+        )
         try:
-            result = await service.transcribe(
-                options,
-                file_bytes=file_bytes,
-                filename=filename,
-                content_type=content_type,
+            payload = TranscriptionJobResponse.model_validate(result)
+            return JSONResponse(
+                content=jsonable_encoder(payload.model_dump(exclude_unset=True))
+            )
+        except (ValidationError, ValueError, TypeError):
+            return envelope(
+                500,
+                "internal_server_error",
+                "Transcription service returned an invalid response.",
             )
-        except TranscribeError:
-            raise
-        return result
 
     @router.get("/api/config/transcription", response_model=None)
     async def get_transcription_config() -> TranscriptionConfigResponse:
@@ -94,7 +100,7 @@ def build_transcribe_router(service: TranscriptionService) -> APIRouter:
     @router.post("/api/config/transcription", response_model=None)
     async def update_transcription_config(
         body: TranscriptionConfigUpdate,
-    ) -> TranscriptionConfigResponse | JSONResponse:
+    ) -> TranscriptionConfigResponse:
         """Persist an updated transcription configuration.
 
         Validation failures (``TranscribeError``) propagate so the
@@ -102,10 +108,7 @@ def build_transcribe_router(service: TranscriptionService) -> APIRouter:
         envelope; successful updates echo the new config back to the
         caller.
         """
-        try:
-            return service.update_config(body)
-        except TranscribeError:
-            raise
+        return service.update_config(body)
 
     @router.get("/api/models/transcription", response_model=None)
     async def get_transcription_models() -> dict[str, Any]:
diff --git a/src/omniscribe/plugins/transcribe/schemas.py b/src/omniscribe/plugins/transcribe/schemas.py
index 3444ec4..3f5e67e 100644
--- a/src/omniscribe/plugins/transcribe/schemas.py
+++ b/src/omniscribe/plugins/transcribe/schemas.py
@@ -139,9 +139,23 @@ class TranscriptionConfigResponse(BaseModel):
     temperature: float = 0.0
 
 
+class TranscriptionSegmentResponse(BaseModel):
+    """Timed segment emitted to the client; retain provider extension fields."""
+
+    model_config = ConfigDict(strict=True, extra="allow", allow_inf_nan=False)
+
+    id: int
+    start: float
+    end: float
+    text: str
+    confidence: float | None = None
+
+
 class TranscriptionJobResponse(BaseModel):
     """Response returned upon transcription execution."""
 
+    model_config = ConfigDict(strict=True, extra="allow", allow_inf_nan=False)
+
     text: str
     language: str | None = None
     duration: float | None = None
@@ -150,7 +164,7 @@ class TranscriptionJobResponse(BaseModel):
     metadata_artifact_id: str | None = None
     metadata_artifact_token: str | None = None
     job_id: str | None = None
-    segments: list[dict[str, Any]] = []
+    segments: list[TranscriptionSegmentResponse] = Field(default_factory=list)
 
 
 UpdateTranscriptionConfigRequest = TranscriptionConfigUpdate
@@ -162,6 +176,7 @@ __all__ = [
     "TranscriptionConfigUpdate",
     "TranscriptionEngineType",
     "TranscriptionJobResponse",
+    "TranscriptionSegmentResponse",
     "UpdateTranscriptionConfigRequest",
     "unpack_transcribe_options",
 ]
diff --git a/src/omniscribe/plugins/translate/routes.py b/src/omniscribe/plugins/translate/routes.py
index 116cea1..aee1fb4 100644
--- a/src/omniscribe/plugins/translate/routes.py
+++ b/src/omniscribe/plugins/translate/routes.py
@@ -13,10 +13,7 @@ from omniscribe.plugins.translate.schemas import (
     NllbRequest,
     TranslationRequest,
 )
-from omniscribe.plugins.translate.service import (
-    TranslateError,
-    TranslationService,
-)
+from omniscribe.plugins.translate.service import TranslationService
 
 
 def build_translate_router(service: TranslationService) -> APIRouter:
@@ -55,10 +52,7 @@ def build_translate_router(service: TranslationService) -> APIRouter:
                 "bad_request",
                 "'text' or 'text_artifact_id'/'text_artifact_token' is required",
             )
-        try:
-            translated = await service.translate_sync(body)
-        except TranslateError:
-            raise
+        translated = await service.translate_sync(body)
         return {"translated_text": translated}
 
     @router.post("/api/translate/async", response_model=None)
@@ -82,10 +76,7 @@ def build_translate_router(service: TranslationService) -> APIRouter:
                 "bad_request",
                 "'text' or 'text_artifact_id'/'text_artifact_token' is required",
             )
-        try:
-            return await service.submit(body)
-        except TranslateError:
-            raise
+        return await service.submit(body)
 
     @router.get("/api/translate/status/{job_id}", response_model=None)
     async def translation_status(
@@ -130,9 +121,6 @@ def build_translate_router(service: TranslationService) -> APIRouter:
         specifically want raw NLLB behaviour without tree-aware
         postprocessing.
         """
-        try:
-            return await service.translate_nllb(body.text, body.target_language)
-        except TranslateError:
-            raise
+        return await service.translate_nllb(body.text, body.target_language)
 
     return router
diff --git a/tests/plugins/test_ocr_plugin.py b/tests/plugins/test_ocr_plugin.py
index 35da9da..939c81b 100644
--- a/tests/plugins/test_ocr_plugin.py
+++ b/tests/plugins/test_ocr_plugin.py
@@ -18,6 +18,8 @@ from omniscribe.harness.context import Context
 from omniscribe.plugins import artifacts as art
 from omniscribe.plugins import jobs, progress, runtime
 from omniscribe.plugins import state_backend as sb
+from omniscribe.plugins._http import plugin_error_exception_handler
+from omniscribe.plugins.errors import PluginError
 from omniscribe.plugins.ocr.plugin import OCRPlugin
 from omniscribe.plugins.runtime import RuntimeService
 
@@ -74,6 +76,7 @@ async def _boot(**ocr_config: Any) -> tuple[Context, FastAPI]:
     await ctx.plugin(progress.ProgressPlugin(), config={})
     await ctx.plugin(OCRPlugin(), config=ocr_config)
     app = FastAPI()
+    app.add_exception_handler(PluginError, plugin_error_exception_handler)
     for router in ctx.routes():
         app.include_router(router)
     return ctx, app
diff --git a/tests/plugins/test_pipeline_bridge.py b/tests/plugins/test_pipeline_bridge.py
index b6d43c3..1a9d752 100644
--- a/tests/plugins/test_pipeline_bridge.py
+++ b/tests/plugins/test_pipeline_bridge.py
@@ -15,6 +15,7 @@ from omniscribe.core.imaging.page_preprocess import (
     PagePreprocessingOptions,
 )
 from omniscribe.core.workflows.repair import RepairOptions
+from omniscribe.plugins.errors import PluginError
 from omniscribe.plugins.ocr import pipeline_bridge
 from omniscribe.plugins.ocr.schemas import OCRRequest
 
@@ -75,31 +76,28 @@ def test_build_pipeline_falls_back_to_settings_llm_coordinates() -> None:
 
 
 def test_build_pipeline_rejects_ssrf_blocked_api_base() -> None:
-    from fastapi import HTTPException
-
     settings = load_settings()
     request = OCRRequest(
         pipeline_mode="grounded",
         api_base="http://169.254.169.254/v1",
     )
-    with pytest.raises(HTTPException) as excinfo:
+    with pytest.raises(PluginError) as excinfo:
         pipeline_bridge.build_pipeline(settings, request)
     assert excinfo.value.status_code == 400
+    assert excinfo.value.error == "bad_request"
     assert "SSRF blocked" in excinfo.value.detail
 
 
 def test_build_pipeline_rejects_localhost_when_ssrf_local_disabled(
     monkeypatch: pytest.MonkeyPatch,
 ) -> None:
-    from fastapi import HTTPException
-
     monkeypatch.setenv("ALLOW_SSRF_LOCAL", "false")
     settings = load_settings()
     request = OCRRequest(
         pipeline_mode="grounded",
         api_base="http://127.0.0.1:1234/v1",
     )
-    with pytest.raises(HTTPException) as excinfo:
+    with pytest.raises(PluginError) as excinfo:
         pipeline_bridge.build_pipeline(settings, request)
     assert excinfo.value.status_code == 400
     assert "SSRF blocked" in excinfo.value.detail
diff --git a/tests/routers/test_transcribe_routes.py b/tests/routers/test_transcribe_routes.py
index 397499a..7222264 100644
--- a/tests/routers/test_transcribe_routes.py
+++ b/tests/routers/test_transcribe_routes.py
@@ -5,6 +5,7 @@ from __future__ import annotations
 import json
 from typing import Any
 
+import pytest
 from fastapi.testclient import TestClient
 
 from omniscribe.core.transcription.types import (
@@ -63,6 +64,75 @@ def test_transcribe_success_contract(api_client: TestClient, monkeypatch: Any) -
     assert data["text_artifact_id"] and data["text_artifact_token"]
     assert data["metadata_artifact_id"] and data["metadata_artifact_token"]
     assert data["segments"][0]["text"] == "Sample transcribed speech text"
+    assert set(data) == {
+        "text",
+        "language",
+        "duration",
+        "job_id",
+        "segments",
+        "text_artifact_id",
+        "text_artifact_token",
+        "metadata_artifact_id",
+        "metadata_artifact_token",
+    }
+    assert data["segments"] == [
+        {
+            "id": 0,
+            "start": 0.0,
+            "end": 4.0,
+            "text": "Sample transcribed speech text",
+            "confidence": None,
+        }
+    ]
+
+
+@pytest.mark.parametrize("malformed", [None, "id", "nan", "inf", "extension_nan"])
+def test_transcribe_response_boundary_preserves_fields_and_rejects_invalid_types(
+    api_client: TestClient, monkeypatch: Any, malformed: str | None
+) -> None:
+    from omniscribe.plugins.transcribe.service import TranscriptionServiceImpl
+
+    payload: dict[str, Any] = {
+        "text": "speech",
+        "language": None,
+        "vendor": "extension",
+        "segments": [
+            {
+                "id": 0,
+                "start": 0.0,
+                "end": 4.0,
+                "text": "speech",
+                "words": [{"word": "speech"}],
+            }
+        ],
+    }
+    if malformed == "id":
+        payload["segments"][0]["id"] = "provider-secret-invalid-id"
+    elif malformed == "nan":
+        payload["segments"][0]["start"] = float("nan")
+    elif malformed == "inf":
+        payload["duration"] = float("inf")
+    elif malformed == "extension_nan":
+        payload["vendor"] = float("nan")
+
+    async def stub_transcribe(self: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
+        return payload
+
+    monkeypatch.setattr(TranscriptionServiceImpl, "transcribe", stub_transcribe)
+    response = api_client.post(
+        "/api/transcribe",
+        files={"file": ("test.wav", WAV_HEADER, "audio/wav")},
+    )
+    if malformed:
+        assert response.status_code == 500
+        assert response.json() == {
+            "error": "internal_server_error",
+            "detail": "Transcription service returned an invalid response.",
+        }
+        assert "provider-secret" not in response.text
+    else:
+        assert response.status_code == 200
+        assert response.json() == payload
 
 
 def test_transcribe_unsupported_format_400(api_client: TestClient) -> None:
@@ -76,6 +146,22 @@ def test_transcribe_unsupported_format_400(api_client: TestClient) -> None:
     assert "Unsupported audio format" in body["detail"]
 
 
+@pytest.mark.parametrize("options", [{"engine": "unknown"}, {"temperature": "bad"}])
+def test_transcribe_invalid_options_return_serializable_422(
+    api_client: TestClient, options: dict[str, str]
+) -> None:
+    response = api_client.post(
+        "/api/transcribe",
+        files={"file": ("test.wav", WAV_HEADER, "audio/wav")},
+        data=options,
+    )
+    assert response.status_code == 422
+    detail = response.json()["detail"]
+    assert isinstance(detail, list)
+    assert detail[0]["loc"] == [next(iter(options))]
+    assert detail[0]["type"] == "value_error"
+
+
 def test_transcribe_ssrf_override_403(api_client: TestClient, monkeypatch: Any) -> None:
     _stub_engine(monkeypatch, _result())
     response = api_client.post(
```
