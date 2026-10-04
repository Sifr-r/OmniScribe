"""Advertised OCR quality controls are wired end to end (finding 3).

Every test builds :class:`OCRRequest` from the FormData-shaped input the
frontend sends (plain string values, or one ``quality_options`` JSON
string) and asserts the *effect* on the core components — a real
``TrustOrchestrator`` on the constructed pipeline, the exact settings it
received, quality-routing forwarded to ``OCRPipeline.run`` — never just
that the field round-trips.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
import time
import types
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from omniscribe.config import load_settings
from omniscribe.core.document import DocumentBlock, DocumentPage, DocumentResult
from omniscribe.core.ocr_quality import OCrQualitySettings, build_trust_orchestrator
from omniscribe.core.ocr_quality.summary import document_trust_summary
from omniscribe.core.processors import build_document_processors
from omniscribe.harness.context import Context
from omniscribe.plugins import artifacts as art
from omniscribe.plugins import jobs, progress, runtime
from omniscribe.plugins import state_backend as sb
from omniscribe.plugins._http import plugin_error_exception_handler
from omniscribe.plugins.errors import PluginError
from omniscribe.plugins.ocr import pipeline_bridge
from omniscribe.plugins.ocr.plugin import OCRPlugin
from omniscribe.plugins.ocr.schemas import (
    OCRQualityOptions,
    OCRRequest,
    OCRTrustOptions,
)

# The package __init__ re-exports the ``plugin`` instance, which shadows
# the submodule attribute — import the module itself for monkeypatching.
ocr_service_mod = importlib.import_module("omniscribe.plugins.ocr.service")

PDF_BYTES = b"%PDF-1.4 fake"
PDF_UPLOAD = {"files": {"file": ("a.pdf", b"%PDF-1.4 input", "application/pdf")}}


# -- helpers --------------------------------------------------------------------


def form_request(**fields: str) -> OCRRequest:
    """Build a request from FormData-shaped input (every value a string)."""
    return OCRRequest.model_validate(fields)


def quality_json(**payload: Any) -> str:
    """The single form field a client sends for the quality controls."""
    return json.dumps(payload)


@pytest.fixture()
def fake_aligner_module(monkeypatch: pytest.MonkeyPatch) -> object:
    """Stub ``omniscribe.core.aligner`` so hybrid builds never load Surya."""
    sentinel = object()
    module = types.ModuleType("omniscribe.core.aligner")

    def get_shared_hybrid_aligner() -> object:
        return sentinel

    module.get_shared_hybrid_aligner = get_shared_hybrid_aligner  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "omniscribe.core.aligner", module)
    return sentinel


async def _boot() -> tuple[Context, FastAPI]:
    ctx = Context()
    await ctx.plugin(runtime.RuntimePlugin(), config={})
    await ctx.plugin(sb.StateBackendPlugin(), config={"backend": "memory"})
    await ctx.plugin(art.ArtifactsPlugin(), config={})
    await ctx.plugin(jobs.JobsPlugin(), config={})
    await ctx.plugin(progress.ProgressPlugin(), config={})
    await ctx.plugin(OCRPlugin(), config={})
    app = FastAPI()

    async def handle_plugin_error(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, PluginError)
        return await plugin_error_exception_handler(request, exc)

    app.add_exception_handler(PluginError, handle_plugin_error)
    for router in ctx.routes():
        app.include_router(router)
    return ctx, app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


async def _wait_status(
    client: httpx.AsyncClient, job_id: str, status: str, *, timeout: float = 5.0
) -> dict[str, Any]:
    deadline = time.time() + timeout
    body: dict[str, Any] = {}
    while time.time() < deadline:
        body = (await client.get(f"/api/process/status/{job_id}")).json()
        if body.get("status") == status:
            return body
        await asyncio.sleep(0.01)
    raise AssertionError(f"job {job_id} never reached {status!r}; last={body}")


@pytest.fixture()
def captured_pipelines(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Keep the real ``build_pipeline``; stub only the OCR execution.

    ``_execute`` (``run_sync`` and ``run_job``) is the single call site of
    both, so every pipeline it builds is captured here — the trust layer
    under test is the real one.
    """
    built: list[Any] = []
    real_build = ocr_service_mod.build_pipeline

    def capturing_build(settings: Any, request: Any, **kwargs: Any) -> Any:
        pipeline = real_build(settings, request, **kwargs)
        built.append(pipeline)
        return pipeline

    async def fake_run(pipeline: Any, *, output_path: str, **kwargs: Any) -> dict:
        Path(output_path).write_bytes(PDF_BYTES)
        return {0: ["hello world"]}

    monkeypatch.setattr(ocr_service_mod, "build_pipeline", capturing_build)
    monkeypatch.setattr(ocr_service_mod, "run_pipeline", fake_run)
    return built


# -- schema: FormData → typed options -------------------------------------------


def test_form_data_quality_options_become_typed_options() -> None:
    request = form_request(
        quality_options=quality_json(
            trust={"calibration_enabled": True, "trust_flag_threshold": "0.7"},
            routing={"enabled": "true"},
        )
    )
    options = request.quality_options
    assert isinstance(options, OCRQualityOptions)
    assert isinstance(options.trust, OCRTrustOptions)
    assert options.trust is not None
    assert options.trust.calibration_enabled is True
    assert options.trust.trust_flag_threshold == pytest.approx(0.7)
    assert options.routing is not None
    assert options.routing.enabled is True


def test_quality_options_round_trip_through_queue_envelope() -> None:
    """``jobs_redis`` serialises with ``model_dump()`` and rebuilds the request."""
    request = form_request(
        quality_options=quality_json(trust={"script_detect_enabled": True})
    )
    restored = OCRRequest(**request.model_dump())
    assert restored.quality_options == request.quality_options
    assert restored.quality_options is not None
    assert restored.quality_options.trust is not None
    assert restored.quality_options.trust.script_detect_enabled is True


def test_absent_quality_options_stay_none() -> None:
    assert OCRRequest().quality_options is None
    assert form_request(quality_options="").quality_options is None
    assert form_request(quality_options=quality_json()).quality_options is not None


# -- schema: invalid input is an explicit error, never a silent drop -----------


@pytest.mark.parametrize(
    "raw,expected_field",
    [
        ("{not json", "quality_options"),
        ('"a string"', "quality_options"),
        (quality_json(trust={"calibration_enabled": "maybe"}), "calibration_enabled"),
        (
            quality_json(trust={"watermark_aggressiveness": "9"}),
            "watermark_aggressiveness",
        ),
        (
            quality_json(trust={"hallucination_repetition_window": "1"}),
            "hallucination_repetition_window",
        ),
        (quality_json(trust={"trust_flag_threshold": "-0.2"}), "trust_flag_threshold"),
        (quality_json(trust={"typo_switch": True}), "typo_switch"),
        (quality_json(routing={"enabled": "sometimes"}), "enabled"),
        (quality_json(routing={"typo": True}), "typo"),
        (quality_json(typo_group=True), "typo_group"),
    ],
)
def test_invalid_quality_options_raise_naming_the_field(
    raw: str, expected_field: str
) -> None:
    with pytest.raises(ValidationError) as excinfo:
        form_request(quality_options=raw)
    message = str(excinfo.value)
    assert "quality_options" in message
    assert expected_field in message
    # Not a silent drop: no request is produced at all.
    assert excinfo.value.errors()[0]["loc"][0] == "quality_options"


# -- bridge: the trust orchestrator is really attached -------------------------


@pytest.mark.parametrize("pipeline_mode", ["grounded", "hybrid"])
def test_build_pipeline_attaches_trust_orchestrator(
    fake_aligner_module: object, pipeline_mode: str
) -> None:
    request = form_request(
        pipeline_mode=pipeline_mode,
        model="some-model",
        quality_options=quality_json(
            trust={
                "calibration_enabled": True,
                "script_detect_enabled": True,
                "trust_flag_threshold": 0.35,
            }
        ),
    )
    pipeline = pipeline_bridge.build_pipeline(load_settings(), request)
    orchestrator = pipeline._engine.trust_orchestrator
    assert orchestrator is not None
    settings = orchestrator.settings  # type: ignore[attr-defined]
    assert isinstance(settings, OCrQualitySettings)
    assert settings.calibration_enabled is True
    assert settings.script_detect_enabled is True
    assert settings.trust_flag_threshold == pytest.approx(0.35)
    assert settings.any_submodule_enabled() is True


@pytest.mark.parametrize("pipeline_mode", ["grounded", "hybrid"])
def test_build_pipeline_without_quality_options_keeps_layer_off(
    fake_aligner_module: object, pipeline_mode: str
) -> None:
    """Existing behaviour: no opt-in means no orchestrator and no routing kwarg."""
    request = form_request(
        pipeline_mode=pipeline_mode, model="some-model", quality_loop_enabled="true"
    )
    pipeline = pipeline_bridge.build_pipeline(load_settings(), request)
    assert pipeline._engine.trust_orchestrator is None
    kwargs = pipeline_bridge.resolve_run_kwargs(load_settings(), request)
    assert "quality_routing_options" not in kwargs
    # ``trust_model_id`` alone still does not turn the layer on.
    assert kwargs["repair_options"].enabled is True


def test_trust_block_with_every_module_off_is_not_attached() -> None:
    """``build_trust_orchestrator`` short-circuits to None (core contract)."""
    request = form_request(
        pipeline_mode="grounded", quality_options=quality_json(trust={})
    )
    assert pipeline_bridge.build_trust_orchestrator_for_request(request) is None
    assert build_trust_orchestrator(OCrQualitySettings()) is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("watermark_enabled", True),
        ("watermark_aggressiveness", 0.9),
        ("script_detect_enabled", True),
        ("hallucination_enabled", True),
        ("hallucination_cross_check", True),
        ("hallucination_cross_check_threshold", 0.8),
        ("hallucination_repetition_window", 12),
        ("hallucination_length_plausibility_min", 0.5),
        ("calibration_enabled", True),
        ("trust_flag_threshold", 0.25),
    ],
)
def test_every_trust_option_reaches_the_core_settings(field: str, value: Any) -> None:
    """Guards request/core field-name drift (``OCrQualitySettings`` forbids extras)."""
    assert field in OCrQualitySettings.model_fields
    trust = OCRTrustOptions(**{field: value})
    settings = OCrQualitySettings.model_validate(trust.model_dump())
    assert getattr(settings, field) == value


def test_quality_routing_is_forwarded_only_when_enabled() -> None:
    settings = load_settings()
    enabled = form_request(quality_options=quality_json(routing={"enabled": True}))
    routing = pipeline_bridge.resolve_run_kwargs(settings, enabled)
    assert routing["quality_routing_options"].enabled is True

    disabled = form_request(quality_options=quality_json(routing={"enabled": False}))
    assert "quality_routing_options" not in pipeline_bridge.resolve_run_kwargs(
        settings, disabled
    )
    # Grounded runs ignore routing entirely (same rule as preprocessing_options).
    grounded = form_request(
        pipeline_mode="grounded",
        quality_options=quality_json(routing={"enabled": True}),
    )
    assert "quality_routing_options" not in pipeline_bridge.resolve_run_kwargs(
        settings, grounded
    )


# -- scored block metadata survives --------------------------------------------


def test_trust_layer_scores_blocks_with_the_requested_options() -> None:
    orchestrator = pipeline_bridge.build_trust_orchestrator_for_request(
        form_request(
            model="unknown-model",
            quality_options=quality_json(
                trust={"calibration_enabled": True, "trust_flag_threshold": 0.5}
            ),
        )
    )
    assert orchestrator is not None
    blocks = [
        DocumentBlock(
            bbox=(0.0, 0.0, 0.4, 0.05), text="clear heading", confidence=0.95
        ),
        DocumentBlock(bbox=(0.0, 0.1, 0.4, 0.15), text="muddy", confidence=0.2),
    ]
    scored = orchestrator(blocks, None, model_id="unknown-model")
    assert [b.trust_score for b in scored] == [pytest.approx(0.95), pytest.approx(0.2)]
    assert scored[0].trust_flags is None
    assert scored[1].trust_flags == ("low_calibrated_conf",)
    # Inputs are never mutated (orchestrator contract).
    assert blocks[1].trust_score is None

    summary = document_trust_summary(
        DocumentResult(pages=[DocumentPage(page_index=0, blocks=scored)])
    )
    assert summary is not None
    assert summary["block_count"] == 2
    assert summary["flagged_count"] == 1


# -- table_fallback ------------------------------------------------------------


async def test_table_fallback_is_accepted_and_actually_runs() -> None:
    """Implemented + registered (``build_document_processors``), so allowlisted."""
    request = form_request(document_processors="table_fallback,reading_order")
    assert request.document_processors == ["table_fallback", "reading_order"]
    processors = build_document_processors(request.document_processors)
    assert [p.name for p in processors] == ["table_fallback", "reading_order"]

    document = DocumentResult(
        pages=[
            DocumentPage(
                page_index=0,
                blocks=[
                    DocumentBlock(
                        bbox=(0.0, 0.0, 0.4, 0.1),
                        text="Name | Qty\n--- | ---\nBolt | 4",
                        kind="table",
                        confidence=0.1,
                    )
                ],
            )
        ]
    )
    processed = await processors[0].process(document)
    tables = processed.pages[0].metadata["tables"]
    # The separator row is dropped by the default parser: header + data row.
    assert tables == [
        {
            "table_index": 0,
            "row_count": 2,
            "column_count": 2,
            "fallback_applied": True,
        }
    ]
    assert processed.tree is not None
    assert [node.rows for node in processed.tree.tables] == [2]


# -- HTTP: both the sync and the async path attach the real layer --------------


@pytest.mark.parametrize("path", ["/api/process", "/api/process/async"])
@pytest.mark.parametrize("pipeline_mode", ["grounded", "hybrid"])
async def test_http_quality_options_reach_the_built_pipeline(
    fake_aligner_module: object,
    captured_pipelines: list[Any],
    path: str,
    pipeline_mode: str,
) -> None:
    ctx, app = await _boot()
    try:
        async with _client(app) as client:
            response = await client.post(
                path,
                data={
                    "pipeline_mode": pipeline_mode,
                    "quality_options": quality_json(
                        trust={
                            "calibration_enabled": True,
                            "trust_flag_threshold": 0.4,
                        },
                        routing={"enabled": True},
                    ),
                },
                files=PDF_UPLOAD["files"],
            )
            if path == "/api/process/async":
                assert response.status_code == 202
                await _wait_status(client, response.json()["job_id"], "complete")
            else:
                assert response.status_code == 200
    finally:
        await ctx.dispose()

    assert len(captured_pipelines) == 1
    orchestrator = captured_pipelines[0]._engine.trust_orchestrator
    assert orchestrator is not None, "quality_options did not attach a trust layer"
    assert orchestrator.settings.calibration_enabled is True
    assert orchestrator.settings.trust_flag_threshold == pytest.approx(0.4)


async def test_http_invalid_quality_options_return_422_naming_the_field() -> None:
    ctx, app = await _boot()
    try:
        async with _client(app) as client:
            response = await client.post(
                "/api/process",
                data={"quality_options": quality_json(trust={"typo_switch": True})},
                files=PDF_UPLOAD["files"],
            )
    finally:
        await ctx.dispose()
    assert response.status_code == 422
    body = json.dumps(response.json())
    assert "quality_options" in body
    assert "typo_switch" in body


async def test_http_without_quality_options_attaches_nothing(
    fake_aligner_module: object, captured_pipelines: list[Any]
) -> None:
    ctx, app = await _boot()
    try:
        async with _client(app) as client:
            response = await client.post(
                "/api/process",
                data={"pipeline_mode": "hybrid"},
                files=PDF_UPLOAD["files"],
            )
        assert response.status_code == 200
        assert "x-document-trust" not in response.headers
    finally:
        await ctx.dispose()
    assert len(captured_pipelines) == 1
    assert captured_pipelines[0]._engine.trust_orchestrator is None
