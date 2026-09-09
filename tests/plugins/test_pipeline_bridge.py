"""Pipeline bridge: request → OCRPipeline assembly and callback adaptation."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from omniscribe.config import load_settings
from omniscribe.core.document import DenseMode, SpellcheckMode
from omniscribe.core.imaging.page_preprocess import (
    LocalPagePreprocessor,
    PagePreprocessingOptions,
)
from omniscribe.core.workflows.repair import RepairOptions
from omniscribe.plugins.ocr import pipeline_bridge
from omniscribe.plugins.ocr.schemas import OCRRequest


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


# -- build_pipeline --------------------------------------------------------------


def test_build_pipeline_grounded_assembles_grounded_engine() -> None:
    from omniscribe.core.grounded import PromptedGroundedOCR

    settings = load_settings()
    request = OCRRequest(
        pipeline_mode="grounded",
        model="qwen/qwen3-vl-8b",
        api_base="http://localhost:1234/v1",
        document_processors="reading_order",  # type: ignore[arg-type]
    )
    pipeline = pipeline_bridge.build_pipeline(settings, request)
    assert isinstance(pipeline.grounded_backend, PromptedGroundedOCR)
    assert pipeline.grounded_backend.model == "qwen/qwen3-vl-8b"


def test_build_pipeline_hybrid_uses_shared_aligner_and_preprocessor(
    fake_aligner_module: object,
) -> None:
    settings = load_settings()
    request = OCRRequest(pipeline_mode="hybrid", denoise="true")  # type: ignore[arg-type]
    pipeline = pipeline_bridge.build_pipeline(settings, request)
    assert pipeline.grounded_backend is None
    engine = pipeline._engine
    assert engine.aligner is fake_aligner_module  # type: ignore[attr-defined]
    # a per-page toggle implies preprocessing is on
    assert isinstance(engine.page_preprocessor, LocalPagePreprocessor)  # type: ignore[attr-defined]

    off = pipeline_bridge.build_pipeline(settings, OCRRequest(pipeline_mode="hybrid"))
    assert off._engine.page_preprocessor is None  # type: ignore[attr-defined]


def test_build_pipeline_falls_back_to_settings_llm_coordinates() -> None:
    settings = load_settings()
    request = OCRRequest(pipeline_mode="grounded")
    pipeline = pipeline_bridge.build_pipeline(settings, request)
    assert pipeline.grounded_backend.model == settings.llm_model  # type: ignore[union-attr]


def test_build_pipeline_rejects_ssrf_blocked_api_base() -> None:
    from fastapi import HTTPException

    settings = load_settings()
    request = OCRRequest(
        pipeline_mode="grounded",
        api_base="http://169.254.169.254/v1",
    )
    with pytest.raises(HTTPException) as excinfo:
        pipeline_bridge.build_pipeline(settings, request)
    assert excinfo.value.status_code == 400
    assert "SSRF blocked" in excinfo.value.detail


def test_build_pipeline_rejects_localhost_when_ssrf_local_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    monkeypatch.setenv("ALLOW_SSRF_LOCAL", "false")
    settings = load_settings()
    request = OCRRequest(
        pipeline_mode="grounded",
        api_base="http://127.0.0.1:1234/v1",
    )
    with pytest.raises(HTTPException) as excinfo:
        pipeline_bridge.build_pipeline(settings, request)
    assert excinfo.value.status_code == 400
    assert "SSRF blocked" in excinfo.value.detail


# -- resolve_run_kwargs -----------------------------------------------------------


def test_resolve_run_kwargs_maps_request_fields() -> None:
    settings = load_settings()
    request = OCRRequest.model_validate(
        {
            "dense_mode": "on",
            "spellcheck": "en-US",
            "pages": "1-3",
            "quality_target": "0.9",
            "quality_max_retries": "4",
            "deskew": "true",
        }
    )
    kwargs = pipeline_bridge.resolve_run_kwargs(settings, request)
    assert kwargs["dense_mode"] is DenseMode.ALWAYS
    assert kwargs["spellcheck"] == SpellcheckMode("en-US")
    assert kwargs["pages"] == "1-3"
    repair = kwargs["repair_options"]
    assert isinstance(repair, RepairOptions)
    assert repair.enabled is True
    assert repair.target == pytest.approx(0.9)
    assert repair.max_retries == 4
    options = kwargs["preprocessing_options"]
    assert isinstance(options, PagePreprocessingOptions)
    assert options.enabled is True
    assert options.deskew is True


def test_resolve_run_kwargs_disables_repair_loop_on_explicit_false() -> None:
    settings = load_settings()
    kwargs = pipeline_bridge.resolve_run_kwargs(
        settings,
        OCRRequest(quality_loop_enabled="false"),  # type: ignore[arg-type]
    )
    assert kwargs["repair_options"] is None


def test_resolve_run_kwargs_invalid_spellcheck_falls_back_to_none() -> None:
    settings = load_settings()
    kwargs = pipeline_bridge.resolve_run_kwargs(settings, OCRRequest(spellcheck="xx"))
    assert kwargs["spellcheck"] is SpellcheckMode.NONE


def test_resolve_run_kwargs_grounded_skips_preprocessing_options() -> None:
    settings = load_settings()
    kwargs = pipeline_bridge.resolve_run_kwargs(
        settings,
        OCRRequest(pipeline_mode="grounded", denoise="true"),  # type: ignore[arg-type]
    )
    assert "preprocessing_options" not in kwargs


# -- run_pipeline -----------------------------------------------------------------


class FakePipeline:
    """Captures ``run`` keyword args and fires the core callbacks."""

    def __init__(self) -> None:
        self.calls: dict[str, Any] = {}
        self.closed = False

    async def aclose(self) -> None:
        # Mirror the OCRPipeline.aclose contract — the bridge always calls it
        # in a finally block to release per-request resources.
        self.closed = True

    async def run(
        self,
        input_path: str,
        output_path: str,
        *,
        progress=None,
        on_warning=None,
        cancel_check=None,
        trust_model_id=None,
        **kwargs: Any,
    ) -> dict[int, list[str]]:
        self.calls = {
            "input_path": input_path,
            "output_path": output_path,
            "trust_model_id": trust_model_id,
            "cancel_check": cancel_check,
            **kwargs,
        }
        if progress is not None:
            await progress("ocr", 1, 2, "Processing page 1")
        if on_warning is not None:
            await on_warning(3, RuntimeError("boom"))
        return {0: ["hello"]}


async def test_run_pipeline_adapts_callbacks_into_simple_frames() -> None:
    settings = load_settings()
    request = OCRRequest(model="some-model")
    pipeline = FakePipeline()
    progress_frames: list[tuple[int, str, str]] = []
    warnings: list[str] = []

    async def on_progress(percent: int, stage: str, message: str) -> None:
        progress_frames.append((percent, stage, message))

    async def on_warning(text: str) -> None:
        warnings.append(text)

    pages = await pipeline_bridge.run_pipeline(
        pipeline,  # type: ignore[arg-type]
        settings=settings,
        request=request,
        input_path="in.pdf",
        output_path="out.pdf",
        on_progress=on_progress,
        on_warning=on_warning,
    )
    assert pages == {0: ["hello"]}
    # core (stage, current, total, message) → (percent, stage, message)
    assert progress_frames == [(50, "ocr", "Processing page 1")]
    # core (page_idx, exc) → human warning text (page numbers are 1-based)
    assert warnings == ["Warning on page 4: boom"]
    assert pipeline.calls["trust_model_id"] == "some-model"
    assert pipeline.calls["dense_mode"] is DenseMode.AUTO


async def test_run_pipeline_without_adapters_passes_none_callbacks() -> None:
    settings = load_settings()
    pipeline = FakePipeline()
    pages = await pipeline_bridge.run_pipeline(
        pipeline,  # type: ignore[arg-type]
        settings=settings,
        request=OCRRequest(),
        input_path="in.pdf",
        output_path="out.pdf",
    )
    assert pages == {0: ["hello"]}
    # trust_model_id falls back to the settings model
    assert pipeline.calls["trust_model_id"] == settings.llm_model
