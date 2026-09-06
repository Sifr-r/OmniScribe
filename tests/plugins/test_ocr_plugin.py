"""OCR plugin: full route surface over a faked pipeline bridge."""

from __future__ import annotations

import asyncio
import importlib
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from omniscribe.core.workflows.base import OCRCancelled
from omniscribe.harness.context import Context
from omniscribe.plugins import artifacts as art
from omniscribe.plugins import jobs, progress, runtime
from omniscribe.plugins import state_backend as sb
from omniscribe.plugins.ocr.plugin import OCRPlugin
from omniscribe.plugins.runtime import RuntimeService

# The package __init__ re-exports the ``plugin`` instance, which shadows the
# submodule attribute — import the module itself for monkeypatching.
ocr_plugin_mod = importlib.import_module("omniscribe.plugins.ocr.plugin")
# Sprint 6 split (audit catalog): the pipeline bridge call sites moved
# to ``service.py``. The fake has to be patched on the new module too,
# otherwise the test would hit the real VLM/Surya code path.
ocr_service_mod = importlib.import_module("omniscribe.plugins.ocr.service")

PDF_BYTES = b"%PDF-1.4 fake"


@pytest.fixture()
def fake_pipeline(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replaces the bridge so no VLM / Surya is touched."""
    state: dict[str, Any] = {"fail": False, "wait": False, "gate": asyncio.Event()}

    def fake_build(settings: Any, request: Any, *, block_callbacks: Any = None):
        return object()

    async def fake_run(
        pipeline: Any,
        *,
        settings: Any,
        request: Any,
        input_path: str,
        output_path: str,
        on_progress: Any = None,
        on_warning: Any = None,
        cancel_check: Any = None,
    ) -> dict[int, list[str]]:
        if state["wait"]:
            await state["gate"].wait()
        if on_progress is not None:
            await on_progress(50, "ocr", "Processing page 1")
        if state["fail"]:
            raise RuntimeError("vlm exploded")
        Path(output_path).write_bytes(PDF_BYTES)
        return {0: ["hello world"]}

    monkeypatch.setattr(ocr_service_mod, "build_pipeline", fake_build)
    monkeypatch.setattr(ocr_service_mod, "run_pipeline", fake_run)
    return state


async def _boot(**ocr_config: Any) -> tuple[Context, FastAPI]:
    ctx = Context()
    await ctx.plugin(runtime.RuntimePlugin(), config={})
    await ctx.plugin(sb.StateBackendPlugin(), config={"backend": "memory"})
    await ctx.plugin(art.ArtifactsPlugin(), config={})
    await ctx.plugin(jobs.JobsPlugin(), config={})
    await ctx.plugin(progress.ProgressPlugin(), config={})
    await ctx.plugin(OCRPlugin(), config=ocr_config)
    app = FastAPI()
    for router in ctx.routes():
        app.include_router(router)
    return ctx, app


def _client(app: FastAPI) -> httpx.AsyncClient:
    """ASGI transport keeps the app on the test loop (same as the worker)."""
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def _upload() -> dict[str, Any]:
    return {"files": {"file": ("a.pdf", b"%PDF-1.4 input", "application/pdf")}}


async def _wait_status(
    client: httpx.AsyncClient, job_id: str, status: str, *, timeout: float = 5.0
) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = await client.get(f"/api/process/status/{job_id}")
        body = response.json()
        if body.get("status") == status:
            return body  # type: ignore[no-any-return]
        await asyncio.sleep(0.01)
    raise AssertionError(f"job {job_id} never reached {status!r}; last={body}")


async def _artifact_token_from_events(
    client: httpx.AsyncClient, job_id: str, *, timeout: float = 5.0
) -> str:
    """Read the ``job_completed`` SSE event and return its ``artifact_token``.

    2026-08-29 audit C-3 / H-3: the async result token is delivered
    out-of-band via the ``job_completed`` SSE event (not the status
    response). This helper replays the event stream for tests that
    need the token to download the result.
    """
    import json

    deadline = time.time() + timeout
    async with client.stream("GET", f"/api/process/{job_id}/events") as stream:
        assert stream.status_code == 200
        # Single aiter_lines() pass — httpx raises ``StreamConsumed`` on
        # the second iteration. Track the current SSE event name as we
        # walk the stream.
        current_event: str | None = None
        async for raw in stream.aiter_lines():
            if time.time() > deadline:
                raise AssertionError(
                    f"job {job_id} never emitted job_completed within {timeout}s"
                )
            if raw is None or raw == "" or raw.startswith(":"):
                current_event = None
                continue
            if raw.startswith("event:"):
                current_event = raw.removeprefix("event:").strip()
            elif raw.startswith("data:") and current_event == "job_completed":
                body = json.loads(raw.removeprefix("data:").strip())
                token = body.get("artifact_token")
                if token:
                    return str(token)
                raise AssertionError(
                    f"job_completed for {job_id} had no artifact_token"
                )
    raise AssertionError(f"job {job_id} never emitted job_completed")


# -- sync /api/process -----------------------------------------------------------


async def test_process_sync_returns_pdf_with_artifact_headers(fake_pipeline) -> None:
    ctx, app = await _boot()
    try:
        async with _client(app) as client:
            response = await client.post("/api/process", **_upload())
        assert response.status_code == 200
        assert response.content == PDF_BYTES
        assert response.headers["content-type"].startswith("application/pdf")
        assert response.headers["x-text-artifact-id"]
        assert response.headers["x-text-artifact-token"]
    finally:
        await ctx.dispose()


async def test_process_sync_sets_document_trust_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sync response carries X-Document-Trust when the pipeline scored blocks."""
    import json as json_mod

    from omniscribe.core.document import DocumentBlock, DocumentPage, DocumentResult

    doc_result = DocumentResult(
        pages=[
            DocumentPage(
                page_index=0,
                blocks=[
                    DocumentBlock(
                        bbox=(0.0, 0.0, 0.5, 0.1),
                        text="flagged",
                        trust_score=0.3,
                        trust_flags=("LOW_CALIBRATED_CONF",),
                    ),
                    DocumentBlock(
                        bbox=(0.0, 0.2, 0.5, 0.3), text="clean", trust_score=0.9
                    ),
                ],
            )
        ]
    )
    scored_pipeline = SimpleNamespace(last_document_result=doc_result)

    def scored_build(settings: Any, request: Any, *, block_callbacks: Any = None):
        return scored_pipeline

    async def scored_run(*args: Any, **kwargs: Any) -> dict[int, list[str]]:
        Path(kwargs["output_path"]).write_bytes(PDF_BYTES)
        return {0: ["flagged", "clean"]}

    monkeypatch.setattr(ocr_service_mod, "build_pipeline", scored_build)
    monkeypatch.setattr(ocr_service_mod, "run_pipeline", scored_run)

    ctx, app = await _boot()
    try:
        async with _client(app) as client:
            response = await client.post("/api/process", **_upload())
        assert response.status_code == 200
        trust = json_mod.loads(response.headers["x-document-trust"])
        assert trust["block_count"] == 2
        assert trust["flagged_count"] == 1
    finally:
        await ctx.dispose()


async def test_process_sync_rejects_missing_file(fake_pipeline) -> None:
    ctx, app = await _boot()
    try:
        async with _client(app) as client:
            response = await client.post("/api/process", data={"model": "x"})
        assert response.status_code == 400
    finally:
        await ctx.dispose()


async def test_process_sync_oversized_upload_is_413(fake_pipeline) -> None:
    ctx, app = await _boot(max_upload_mb=1)
    try:
        async with _client(app) as client:
            big = {
                "files": {
                    "file": ("big.pdf", b"x" * (1024 * 1024 + 1), "application/pdf")
                }
            }
            response = await client.post("/api/process", **big)  # type: ignore[arg-type]
        assert response.status_code == 413
    finally:
        await ctx.dispose()


async def test_process_sync_returns_503_when_cancelled(
    fake_pipeline, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def cancel_run(*args: Any, **kwargs: Any) -> dict[int, list[str]]:
        raise OCRCancelled("OCR cancelled after refine box 2/73.")

    monkeypatch.setattr(ocr_service_mod, "run_pipeline", cancel_run)
    ctx, app = await _boot()
    try:
        async with _client(app) as client:
            response = await client.post("/api/process", **_upload())
        assert response.status_code == 503
        data = response.json()
        assert data.get("cancelled") is True
        assert data.get("error") == "cancelled"
        assert "OCR cancelled after refine box 2/73." in data.get("detail", "")
    finally:
        await ctx.dispose()


# -- async lifecycle -----------------------------------------------------------


async def test_async_submit_status_result_and_job_list(fake_pipeline) -> None:
    ctx, app = await _boot()
    try:
        async with _client(app) as client:
            submit = await client.post(
                "/api/process/async", data={"pipeline_mode": "hybrid"}, **_upload()
            )
            assert submit.status_code == 202
            body = submit.json()
            assert body["status"] == "pending"
            job_id = body["job_id"]
            assert body["status_url"] == f"/api/process/status/{job_id}"

            done = await _wait_status(client, job_id, "complete")
            assert done["filename"] == "a.pdf"
            assert done["text_artifact_id"]
            # 2026-08-29 audit C-3 / H-3: status no longer leaks the
            # artifact token; the async client receives it from the
            # ``job_completed`` SSE event payload.
            assert "text_artifact_token" not in done
            assert "text_artifact_url" not in done

            token = await _artifact_token_from_events(client, job_id)
            assert token

            result = await client.get(
                f"/api/jobs/{job_id}/result",
                params={"token": token},
            )
            assert result.status_code == 200
            assert result.content == PDF_BYTES

            wrong = await client.get(
                f"/api/jobs/{job_id}/result", params={"token": "nope"}
            )
            # Pedantic 2.7: wrong-token on a complete job now returns
            # 404 (not 403) so the response is indistinguishable from
            # an unknown id or a not-yet-complete job.
            assert wrong.status_code == 404

            listing = await client.get("/api/jobs")
            assert listing.status_code == 200
            items = listing.json()
            assert len(items) == 1
            assert items[0]["id"] == job_id
            assert items[0]["filename"] == "a.pdf"
            assert items[0]["status"] == "complete"
            assert items[0]["timestamp"]

            # DELETE without confirm=true returns 400
            unconfirmed = await client.delete("/api/jobs")
            assert unconfirmed.status_code == 400
            assert unconfirmed.json() == {
                "error": "confirmation_required",
                "detail": (
                    "DELETE /api/jobs requires confirm=true query parameter "
                    "to prevent accidental wipe"
                ),
            }
            assert len((await client.get("/api/jobs")).json()) == 1

            # DELETE with confirm=true succeeds
            cleared = await client.delete("/api/jobs", params={"confirm": "true"})
            assert cleared.status_code == 200
            assert cleared.json()["cleared"] == 1
            assert (await client.get("/api/jobs")).json() == []
    finally:
        await ctx.dispose()


async def test_async_failure_maps_to_error_status_and_409_result(
    fake_pipeline,
) -> None:
    fake_pipeline["fail"] = True
    ctx, app = await _boot()
    try:
        async with _client(app) as client:
            submit = await client.post("/api/process/async", **_upload())
            job_id = submit.json()["job_id"]
            failed = await _wait_status(client, job_id, "error")
            assert failed["error"] == "vlm exploded"

            result = await client.get(
                f"/api/jobs/{job_id}/result", params={"token": "anything"}
            )
            # Pedantic 2.7: an errored job's result fetch is also 404
            # so a caller cannot distinguish "errored" from "unknown"
            # from "wrong token" by status alone.
            assert result.status_code == 404
    finally:
        await ctx.dispose()


async def test_status_unknown_job_is_404(fake_pipeline) -> None:
    ctx, app = await _boot()
    try:
        async with _client(app) as client:
            assert (await client.get("/api/process/status/nope")).status_code == 404
            assert (await client.get("/api/jobs/nope/result")).status_code == 404
            assert (await client.post("/api/jobs/nope/cancel")).status_code == 404
    finally:
        await ctx.dispose()


async def test_cancel_queued_job(fake_pipeline) -> None:
    fake_pipeline["wait"] = True
    ctx, app = await _boot()
    try:
        async with _client(app) as client:
            first = await client.post("/api/process/async", **_upload())
            first_id = first.json()["job_id"]
            # wait for the single worker to claim job one
            await _wait_status(client, first_id, "processing")

            second = await client.post("/api/process/async", **_upload())
            second_id = second.json()["job_id"]
            assert (await _wait_status(client, second_id, "pending"))[
                "status"
            ] == "pending"

            cancel = await client.post(f"/api/jobs/{second_id}/cancel")
            assert cancel.status_code == 200
            assert cancel.json() == {"cancelled": True}
            cancelled = await _wait_status(client, second_id, "cancelled")
            assert cancelled["status"] == "cancelled"
            assert cancelled["error"] == "Job cancelled."

            # terminal cancel returns {"cancelled": True, "status": record.status}
            again = await client.post(f"/api/jobs/{second_id}/cancel")
            assert again.status_code == 200
            assert again.json() == {"cancelled": True, "status": "cancelled"}

            fake_pipeline["gate"].set()
            await _wait_status(client, first_id, "complete")

            # cancel on completed job also returns {"cancelled": True, "status": "complete"}
            cancel_complete = await client.post(f"/api/jobs/{first_id}/cancel")
            assert cancel_complete.status_code == 200
            assert cancel_complete.json() == {"cancelled": True, "status": "complete"}
    finally:
        await ctx.dispose()


# -- SSE -----------------------------------------------------------------------


async def test_events_stream_replays_job_lifecycle(fake_pipeline) -> None:
    ctx, app = await _boot()
    try:
        async with _client(app) as client:
            submit = await client.post("/api/process/async", **_upload())
            job_id = submit.json()["job_id"]
            await _wait_status(client, job_id, "complete")

            async with client.stream("GET", f"/api/process/{job_id}/events") as stream:
                assert stream.status_code == 200
                text = "".join([chunk async for chunk in stream.aiter_text()])
            assert "event: job_queued" in text
            assert "event: job_started" in text
            assert "event: job_completed" in text

            unknown = await client.get("/api/process/nope/events")
            assert unknown.status_code == 404
    finally:
        await ctx.dispose()


# -- config store ----------------------------------------------------------------


async def test_config_round_trip_with_settings_write_through(fake_pipeline) -> None:
    ctx, app = await _boot()
    try:
        async with _client(app) as client:
            got = await client.get("/api/config")
            assert got.status_code == 200
            seeded = got.json()
            assert seeded["pipeline_mode"] == "hybrid"
            assert seeded["dense_mode"] == "auto"
            assert seeded["document_processors"] == []

            updated = await client.post(
                "/api/config",
                json={"model": "new-model", "dpi": 300, "unknown_key": 1},
            )
            assert updated.status_code == 200
            body = updated.json()
            assert body["model"] == "new-model"
            assert body["dpi"] == 300
            assert "unknown_key" not in body

            runtime_service = ctx.inject(RuntimeService)
            assert runtime_service.settings.llm_model == "new-model"

            alias = await client.get("/api/config/ocr")
            assert alias.json()["model"] == "new-model"
            put = await client.put("/api/config/ocr", json={"dense_mode": "always"})
            assert put.status_code == 200
            assert put.json()["dense_mode"] == "always"
    finally:
        await ctx.dispose()


async def test_update_config_rejects_ssrf_blocked_api_base(fake_pipeline) -> None:
    ctx, app = await _boot()
    try:
        async with _client(app) as client:
            res = await client.post(
                "/api/config",
                json={"api_base": "http://169.254.169.254/v1"},
            )
            assert res.status_code == 400
            assert "SSRF blocked" in res.json().get("detail", "")
    finally:
        await ctx.dispose()


async def test_event_buffers_and_done_jobs_are_bounded_and_pruned(
    fake_pipeline,
) -> None:
    from omniscribe.plugins.jobs import JobCompleted, JobQueued
    from omniscribe.plugins.ocr.service import OCRServiceImpl

    ctx, _app = await _boot()
    try:
        service = ctx.inject(ocr_plugin_mod.OCRService)
        assert isinstance(service, OCRServiceImpl)
        # Audit D7: read+write the public surface, not the private
        # ``_max_buffered_jobs`` attribute.
        service.max_buffered_jobs = 10

        for i in range(25):
            job_id = f"job_{i}"
            await service.record_event(JobQueued(job_id=job_id))
            await service.record_event(
                JobCompleted(job_id=job_id, artifact_id="a", artifact_token="t")
            )

        assert len(service._event_buffers) <= 10
        assert len(service._done_jobs) <= 10
        # Oldest jobs were pruned
        assert "job_0" not in service._event_buffers
        assert service.is_done("job_0") is False
        # Newest jobs are present
        assert "job_24" in service._event_buffers
        assert service.is_done("job_24") is True

        # Explicit prune
        pruned_count = service.prune(max_buffered_jobs=5)
        assert pruned_count == 5
        assert len(service._event_buffers) == 5
    finally:
        await ctx.dispose()


# -- octet-stream format sniff -------------------------------------------------
#
# Pedantic review 1.8: the previous implementation let every
# ``application/octet-stream`` upload through to the tempdir and only
# relied on the downstream parser to reject non-document bytes. The
# new route branches on declared type: typed uploads keep the existing
# per-format magic-byte chain, and octet-stream uploads run the new
# ``_sniff_format`` detector on the first 12 bytes. See the design
# note in ``docs/outstanding-work.md`` (§1, "Design note for the 1.8
# fix").


@pytest.mark.parametrize(
    "head, expected",
    [
        # Positive cases — one per supported format, plus the alternative
        # AVIF/HEIF brands the detector recognises.
        (b"%PDF-1.4\n", "pdf"),
        (b"\x89PNG\r\n\x1a\n", "png"),
        (b"\xff\xd8\xff\xe0\x00\x10JFIF", "jpeg"),
        (b"RIFF\x00\x00\x00\x00WEBPVP", "webp"),
        (b"\x00\x00\x00\x20ftypavif", "avif"),
        (b"\x00\x00\x00\x20ftypavis", "avif"),
        (b"\x00\x00\x00\x20ftypmif1", "avif"),
        # Negative cases — no signature match.
        (b"random text data that is not a document", None),
        (b"", None),
        # Truncated header that happens to start with the PNG signature
        # prefix. The full 8-byte signature is required.
        (b"\x89PNG", None),
        # Looks like a 28-byte ftyp box but the brand at offset 8 is not
        # one of the recognised AVIF/HEIF brands. The old
        # ``b"\\x00\\x00\\x00\\x1c"`` literal-size check would have
        # accepted this; the new detector correctly rejects it.
        (b"\x00\x00\x00\x1cftypxxxx", None),
    ],
)
def test_sniff_format(head: bytes, expected: str | None) -> None:
    from omniscribe.plugins.ocr.plugin import _sniff_format

    assert _sniff_format(head) == expected


async def test_octet_stream_pdf_upload_is_accepted(fake_pipeline) -> None:
    """``application/octet-stream`` with a real PDF body reaches the pipeline.

    The Flutter file picker surfaces ``application/octet-stream`` when the
    OS cannot classify a file; the route must still pass a real PDF
    through to the bridge.
    """
    ctx, app = await _boot()
    try:
        async with _client(app) as client:
            response = await client.post(
                "/api/process",
                files={
                    "file": ("doc.bin", b"%PDF-1.4 input", "application/octet-stream")
                },
            )
        assert response.status_code == 200
        assert response.content == PDF_BYTES
    finally:
        await ctx.dispose()


async def test_octet_stream_garbage_upload_is_rejected_with_415(fake_pipeline) -> None:
    """Regression for pedantic review 1.8: garbage labelled ``octet-stream``
    must 415 *before* the tempdir is written.

    The previous implementation skipped the magic-byte check on the
    octet-stream branch and only relied on the downstream parser to
    reject non-document bytes — which meant the upload was written to
    disk first. The new route sniffs the first 12 bytes and 415s on
    the null case before any tempdir I/O.
    """
    ctx, app = await _boot()
    try:
        async with _client(app) as client:
            response = await client.post(
                "/api/process",
                files={
                    "file": (
                        "doc.bin",
                        b"this is not a document at all",
                        "application/octet-stream",
                    )
                },
            )
        assert response.status_code == 415
        assert "octet-stream uploads must be" in response.json().get("detail", "")
    finally:
        await ctx.dispose()


async def test_octet_stream_size_cap_runs_before_format_sniff(fake_pipeline) -> None:
    """A 2 MB octet-stream garbage blob against a 1 MB cap returns 413, not 415.

    Pins the size-check → format-sniff ordering: an oversized upload
    must fail on size before the format detector runs. The 1 MB cap is
    large enough that the size check triggers first; the upload is
    labelled ``octet-stream`` so the format check would 415 if it ran
    first.
    """
    ctx, app = await _boot(max_upload_mb=1)
    try:
        async with _client(app) as client:
            response = await client.post(
                "/api/process",
                files={
                    "file": (
                        "doc.bin",
                        b"not a document" + b"\x00" * (2 * 1024 * 1024),
                        "application/octet-stream",
                    )
                },
            )
        assert response.status_code == 413
    finally:
        await ctx.dispose()


async def test_octet_stream_typed_upload_path_is_unchanged(fake_pipeline) -> None:
    """Typed uploads (declared MIME) keep the existing magic-byte chain.

    The new code path is gated on ``content_type == "application/octet-stream"``;
    typed uploads continue to use the legacy per-format chain. A real
    PDF declared ``application/pdf`` must still pass the existing check
    and reach the bridge (no-regression for the typed branch).
    """
    ctx, app = await _boot()
    try:
        async with _client(app) as client:
            response = await client.post(
                "/api/process",
                files={"file": ("a.pdf", b"%PDF-1.4 input", "application/pdf")},
            )
        assert response.status_code == 200
        assert response.content == PDF_BYTES
    finally:
        await ctx.dispose()


async def test_typed_avif_upload_with_variable_ftyp_is_accepted(fake_pipeline) -> None:
    """Regression test for pedantic review 1.7: typed AVIF with variable
    ftyp box size (e.g. 32 bytes) must pass format validation.
    """
    ctx, app = await _boot()
    try:
        # 32-byte ftyp box (0x00000020), brand "avif"
        avif_head = b"\x00\x00\x00\x20ftypavif" + b"\x00" * 50
        async with _client(app) as client:
            response = await client.post(
                "/api/process",
                files={"file": ("photo.avif", avif_head, "image/avif")},
            )
        assert response.status_code == 200
        assert response.content == PDF_BYTES
    finally:
        await ctx.dispose()


async def test_delete_jobs_confirmation_required(fake_pipeline) -> None:
    """DELETE /api/jobs requires confirm=true query parameter."""
    ctx, app = await _boot()
    try:
        async with _client(app) as client:
            # 1. No confirm param -> 400
            res = await client.delete("/api/jobs")
            assert res.status_code == 400
            assert res.json() == {
                "error": "confirmation_required",
                "detail": (
                    "DELETE /api/jobs requires confirm=true query parameter "
                    "to prevent accidental wipe"
                ),
            }

            # 2. confirm=false -> 400
            res_false = await client.delete("/api/jobs", params={"confirm": "false"})
            assert res_false.status_code == 400
            assert res_false.json()["error"] == "confirmation_required"

            # 3. confirm=true -> 200
            res_true = await client.delete("/api/jobs", params={"confirm": "true"})
            assert res_true.status_code == 200
            assert res_true.json() == {"status": "ok", "cleared": 0}
    finally:
        await ctx.dispose()


async def test_cancel_job_terminal_status_behavior(fake_pipeline) -> None:
    """POST /api/jobs/{job_id}/cancel returns status on terminal jobs."""
    ctx, app = await _boot()
    try:
        async with _client(app) as client:
            # 404 for unknown job
            res_404 = await client.post("/api/jobs/unknown-id/cancel")
            assert res_404.status_code == 404

            # Submit and wait for completion
            submit = await client.post("/api/process/async", **_upload())
            job_id = submit.json()["job_id"]
            await _wait_status(client, job_id, "complete")

            # Cancelling complete job returns cancelled=True and status=complete
            res_cancel = await client.post(f"/api/jobs/{job_id}/cancel")
            assert res_cancel.status_code == 200
            assert res_cancel.json() == {"cancelled": True, "status": "complete"}
    finally:
        await ctx.dispose()


def test_guess_suffix_helper() -> None:
    """Phase 3.8 (4.8, 2026-09-05): ``_guess_suffix`` was extracted to
    :mod:`omniscribe.plugins.ocr.services.content_sniff` and renamed
    ``guess_suffix``. The test is otherwise unchanged.
    """
    from omniscribe.plugins.ocr.services import guess_suffix

    # Preserves filename extension when present
    assert guess_suffix("doc.pdf") == ".pdf"
    assert guess_suffix("doc.PDF") == ".PDF"
    assert guess_suffix("photo.png", "image/jpeg") == ".png"
    assert guess_suffix("scan.tiff") == ".tiff"

    # Sniffs MIME when filename has no extension
    assert guess_suffix("extensionless", "image/png") == ".png"
    assert guess_suffix("extensionless", "image/jpeg") == ".jpeg"
    assert guess_suffix("extensionless", "image/jpg") == ".jpg"
    assert guess_suffix("extensionless", "image/webp") == ".webp"
    assert guess_suffix("extensionless", "image/avif") == ".avif"
    assert guess_suffix("extensionless", "image/png; charset=utf-8") == ".png"

    # Fallback to .pdf when unknown or octet-stream
    assert guess_suffix("extensionless", None) == ".pdf"
    assert guess_suffix("extensionless", "application/octet-stream") == ".pdf"
    assert guess_suffix("extensionless", "unknown/type") == ".pdf"


async def test_update_config_change_detection() -> None:
    """update_config avoids mutating settings when values are unchanged."""
    from omniscribe.config import load_settings
    from omniscribe.plugins.ocr.service import OCRServiceImpl

    settings = load_settings()
    settings.llm_api_base = "http://initial.base"
    settings.llm_api_key = "initial-key"
    settings.llm_model = "initial-model"

    class _DummyQueue:
        pass

    class _DummyArtifacts:
        pass

    service = OCRServiceImpl(
        settings=settings,
        queue=_DummyQueue(),  # type: ignore[arg-type]
        artifacts=_DummyArtifacts(),  # type: ignore[arg-type]
        progress=None,
        max_upload_mb=10,
    )

    # Calling update_config with identical coordinates doesn't mutate settings
    res = service.update_config(
        {
            "api_base": "http://initial.base",
            "api_key": "******",
            "model": "initial-model",
        }
    )
    assert res["model"] == "initial-model"

    # Updating with changed model mutates settings
    service.update_config({"model": "new-model"})
    assert settings.llm_model == "new-model"


async def test_document_page_preview() -> None:
    _ctx, app = await _boot()
    import pymupdf as fitz

    doc = fitz.open()
    p = doc.new_page()
    p.insert_text((50, 50), "Test Page Preview")
    pdf_bytes = doc.tobytes()

    async with _client(app) as client:
        resp = await client.post(
            "/api/documents/preview",
            files={"file": ("test.pdf", pdf_bytes, "application/pdf")},
            data={"page": "0", "dpi": "150"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"
        assert resp.headers["x-total-pages"] == "1"
        assert float(resp.headers["x-page-width"]) > 0
        assert float(resp.headers["x-page-height"]) > 0
        assert len(resp.content) > 0


async def test_document_page_preview_caching_and_boundaries() -> None:
    _ctx, app = await _boot()
    import pymupdf as fitz

    # Create 2-page doc
    doc = fitz.open()
    p1 = doc.new_page()
    p1.insert_text((50, 50), "Page 1 Content")
    p2 = doc.new_page()
    p2.insert_text((50, 50), "Page 2 Content")
    pdf_bytes = doc.tobytes()

    async with _client(app) as client:
        # 1. Initial upload (page 0)
        resp = await client.post(
            "/api/documents/preview",
            files={"file": ("multi.pdf", pdf_bytes, "application/pdf")},
            data={"page": "0", "dpi": "150"},
        )
        assert resp.status_code == 200
        assert resp.headers["x-total-pages"] == "2"
        doc_id = resp.headers.get("x-document-id")
        assert doc_id is not None and len(doc_id) == 16

        # 2. Subsequent request using doc_id WITHOUT file upload (page 1)
        resp2 = await client.post(
            "/api/documents/preview",
            data={"doc_id": doc_id, "page": "1", "dpi": "200"},
        )
        assert resp2.status_code == 200
        assert resp2.headers["x-document-id"] == doc_id
        assert resp2.headers["x-total-pages"] == "2"
        assert len(resp2.content) > 0

        # 3. Subsequent request using X-Document-Id header without file
        resp3 = await client.post(
            "/api/documents/preview",
            headers={"X-Document-Id": doc_id},
            data={"page": "0"},
        )
        assert resp3.status_code == 200
        assert resp3.headers["x-document-id"] == doc_id

        # 4. Unknown doc_id without file returns 404
        resp_404 = await client.post(
            "/api/documents/preview",
            data={"doc_id": "nonexistent_doc", "page": "0"},
        )
        assert resp_404.status_code == 404

        # 5. Boundary check: negative page returns 400
        resp_neg = await client.post(
            "/api/documents/preview",
            data={"doc_id": doc_id, "page": "-1"},
        )
        assert resp_neg.status_code == 400

        # 6. Boundary check: page out of bounds returns 404
        resp_oob = await client.post(
            "/api/documents/preview",
            data={"doc_id": doc_id, "page": "99"},
        )
        assert resp_oob.status_code == 404

        # 7. Boundary check: empty file returns 400
        resp_empty = await client.post(
            "/api/documents/preview",
            files={"file": ("empty.pdf", b"", "application/pdf")},
            data={"page": "0"},
        )
        assert resp_empty.status_code == 400


async def test_document_page_preview_lru_eviction() -> None:
    _ctx, app = await _boot()
    import pymupdf as fitz

    from omniscribe.plugins.ocr.plugin import (
        _PREVIEW_DOC_CACHE_CAPACITY,
        _preview_doc_cache,
    )

    doc_ids = []
    async with _client(app) as client:
        # Create and upload 11 distinct documents
        for i in range(11):
            doc = fitz.open()
            p = doc.new_page()
            p.insert_text((50, 50), f"Doc {i}")
            b = doc.tobytes()
            resp = await client.post(
                "/api/documents/preview",
                files={"file": (f"doc_{i}.pdf", b, "application/pdf")},
                data={"page": "0"},
            )
            assert resp.status_code == 200
            d_id = resp.headers.get("x-document-id")
            assert d_id is not None
            doc_ids.append(d_id)

        # Cache should be capped at 10
        assert len(_preview_doc_cache) <= _PREVIEW_DOC_CACHE_CAPACITY
        # The oldest document (doc_ids[0]) should have been evicted
        assert doc_ids[0] not in _preview_doc_cache
        # The newest document should be in cache
        assert doc_ids[-1] in _preview_doc_cache
