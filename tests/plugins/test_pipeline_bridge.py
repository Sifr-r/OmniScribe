"""Pipeline bridge: request → OCRPipeline assembly and callback adaptation."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from omniscribe.config import RuntimeSettings, load_settings
from omniscribe.core.document import DenseMode, SpellcheckMode
from omniscribe.core.grounded.prompted import PromptedGroundedOCR
from omniscribe.core.imaging.page_preprocess import (
    LocalPagePreprocessor,
    PagePreprocessingOptions,
)
from omniscribe.core.ocr.processor import OCRProcessor
from omniscribe.core.workflows.repair import RepairOptions
from omniscribe.plugins.errors import PluginError
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
    settings = load_settings()
    request = OCRRequest(
        pipeline_mode="grounded",
        api_base="http://169.254.169.254/v1",
    )
    with pytest.raises(PluginError) as excinfo:
        pipeline_bridge.build_pipeline(settings, request)
    assert excinfo.value.status_code == 400
    assert excinfo.value.error == "bad_request"
    assert "SSRF blocked" in excinfo.value.detail


def test_build_pipeline_rejects_localhost_when_ssrf_local_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALLOW_SSRF_LOCAL", "false")
    settings = load_settings()
    request = OCRRequest(
        pipeline_mode="grounded",
        api_base="http://127.0.0.1:1234/v1",
    )
    with pytest.raises(PluginError) as excinfo:
        pipeline_bridge.build_pipeline(settings, request)
    assert excinfo.value.status_code == 400
    assert "SSRF blocked" in excinfo.value.detail


def test_build_pipeline_foreign_origin_does_not_attach_settings_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    settings = load_settings()
    settings.llm_api_base = "http://localhost:1234/v1"
    settings.llm_api_key = "server-super-secret"

    # Mock SSRF check to allow the custom URL
    monkeypatch.setattr(
        pipeline_bridge,
        "check_ssrf_target_sync",
        lambda url: SimpleNamespace(allowed=True, resolved_ip="192.168.1.50"),
    )

    # 1. Foreign origin without request.api_key receives empty api_key
    request = OCRRequest(
        pipeline_mode="grounded",
        api_base="http://custom-host:8000/v1",
    )
    pipeline = pipeline_bridge.build_pipeline(settings, request)
    assert pipeline.grounded_backend.api_key == ""  # type: ignore[union-attr]
    # Plain HTTP URL is rewritten with resolved IP
    assert pipeline.grounded_backend.api_base == "http://192.168.1.50:8000/v1"  # type: ignore[union-attr]

    # 2. Foreign origin with user key receives user key
    request_with_key = OCRRequest(
        pipeline_mode="grounded",
        api_base="http://custom-host:8000/v1",
        api_key="user-provided-key",
    )
    pipeline2 = pipeline_bridge.build_pipeline(settings, request_with_key)
    assert pipeline2.grounded_backend.api_key == "user-provided-key"  # type: ignore[union-attr]

    # 3. Same origin receives settings key
    request_same = OCRRequest(
        pipeline_mode="grounded",
        api_base="http://localhost:1234/v1/custom",
    )
    pipeline3 = pipeline_bridge.build_pipeline(settings, request_same)
    assert pipeline3.grounded_backend.api_key == "server-super-secret"  # type: ignore[union-attr]


@pytest.mark.parametrize("pipeline_mode", ["grounded", "hybrid"])
def test_build_pipeline_pins_transport_to_resolved_ip_for_https(
    monkeypatch: pytest.MonkeyPatch, pipeline_mode: str
) -> None:
    """An HTTPS ``api_base`` must keep its hostname *and* pin its transport.

    Regression for a DNS-rebinding TOCTOU window that only existed on the
    https scheme. ``_rewrite_url_with_resolved_ip`` cannot be used for https —
    rewriting the host to a literal IP breaks SNI and certificate validation —
    so ``pipeline_bridge`` previously skipped pinning entirely and handed the
    bare hostname to ``AsyncOpenAI`` / ``call_llm``, both of which re-resolve
    on connect. A host answering public during validation and link-local on the
    second lookup therefore walked past the SSRF guard.

    The contract now is: ``api_base`` keeps the hostname (so TLS still works)
    *and* the resolved address is threaded into the engine, which builds an
    IP-pinned transport from it.
    """
    from types import SimpleNamespace

    settings = load_settings()
    resolved_ip = "203.0.113.10"

    pinned_args: list[tuple[str, str]] = []

    class _FakePinnedClient:
        def __init__(self) -> None:
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True

    def _fake_create_pinned_client(url: str, ip: str, timeout: float = 60.0) -> object:
        pinned_args.append((url, ip))
        return _FakePinnedClient()

    monkeypatch.setattr(
        pipeline_bridge,
        "check_ssrf_target_sync",
        lambda url: SimpleNamespace(allowed=True, resolved_ip=resolved_ip),
    )
    # ``raising=False`` so that against the pre-fix source — which never
    # imported these names — the test fails on the behavioural assertions
    # below rather than on the patch mechanism itself.
    monkeypatch.setattr(
        "omniscribe.core.ocr.processor.create_pinned_client",
        _fake_create_pinned_client,
        raising=False,
    )
    monkeypatch.setattr(
        "omniscribe.core.grounded.prompted.create_pinned_client",
        _fake_create_pinned_client,
        raising=False,
    )

    request = OCRRequest(
        pipeline_mode=pipeline_mode,  # type: ignore[arg-type]
        api_base="https://vlm.example.internal/v1",
    )
    pipeline = pipeline_bridge.build_pipeline(settings, request)

    engine_backend = (
        pipeline.grounded_backend
        if pipeline.grounded_backend is not None
        else getattr(pipeline._engine, "ocr_processor", None)
    )
    assert isinstance(engine_backend, (PromptedGroundedOCR, OCRProcessor))

    # The hostname survives, so TLS SNI / certificate validation still work.
    assert engine_backend.api_base == "https://vlm.example.internal/v1"
    # ...and the validated address is pinned, closing the rebind window.
    # Pre-fix, ``pinned_args`` is empty: https never reached this code.
    assert pinned_args == [("https://vlm.example.internal/v1", resolved_ip)]
    assert getattr(engine_backend, "_pinned_http_client", None) is not None


@pytest.mark.parametrize("pipeline_mode", ["grounded", "hybrid"])
async def test_ocr_pipeline_aclose_releases_pinned_transport(
    monkeypatch: pytest.MonkeyPatch, pipeline_mode: str
) -> None:
    """The pipeline owns the pinned client and must release it exactly once.

    ``call_llm`` never closes an injected client, so leaking it would keep a
    connection pool alive per OCR run. Idempotency matters because
    ``OCRPipeline.__aexit__`` also routes through ``aclose``.
    """
    from types import SimpleNamespace

    settings = load_settings()
    closed: list[bool] = []

    class _FakePinnedClient:
        async def aclose(self) -> None:
            closed.append(True)

    monkeypatch.setattr(
        pipeline_bridge,
        "check_ssrf_target_sync",
        lambda url: SimpleNamespace(allowed=True, resolved_ip="203.0.113.10"),
    )
    monkeypatch.setattr(
        "omniscribe.core.ocr.processor.create_pinned_client",
        lambda url, ip, timeout=60.0: _FakePinnedClient(),
        raising=False,
    )
    monkeypatch.setattr(
        "omniscribe.core.grounded.prompted.create_pinned_client",
        lambda url, ip, timeout=60.0: _FakePinnedClient(),
        raising=False,
    )

    pipeline = pipeline_bridge.build_pipeline(
        settings,
        OCRRequest(
            pipeline_mode=pipeline_mode,  # type: ignore[arg-type]
            api_base="https://vlm.example.internal/v1",
        ),
    )

    await pipeline.aclose()
    assert closed == [True], "pinned client must be closed exactly once"
    # Safe to call again.
    await pipeline.aclose()
    assert closed == [True], "aclose must stay idempotent"


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


def test_processing_defaults_and_request_overrides_reach_both_engines() -> None:
    settings = RuntimeSettings(
        ocr_dpi=300, ocr_concurrency=4, ocr_max_image_dim=2048, ocr_dense_threshold=200
    )
    expected = {
        "dpi": 300,
        "concurrency": 4,
        "max_image_dim": 2048,
        "dense_threshold": 200,
    }
    kwargs = pipeline_bridge.resolve_run_kwargs(settings, OCRRequest())
    assert {key: kwargs[key] for key in expected} == expected
    request = OCRRequest(
        pipeline_mode="grounded",
        dpi=400,
        concurrency=2,
        max_image_dim=1536,
        dense_threshold=100,
    )
    kwargs = pipeline_bridge.resolve_run_kwargs(settings, request)
    assert kwargs["dense_threshold"] == 100
    pipeline = pipeline_bridge.build_pipeline(settings, request)
    backend = pipeline.grounded_backend
    assert backend is not None
    assert backend.dpi == kwargs["dpi"] == 400  # type: ignore[attr-defined]
    assert backend.concurrency == kwargs["concurrency"] == 2  # type: ignore[attr-defined]
    assert backend.max_image_dim == kwargs["max_image_dim"] == 1536  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "field,value",
    [("dpi", 0), ("concurrency", 33), ("max_image_dim", -1), ("dense_threshold", 0)],
)
def test_processing_options_reject_invalid_limits(field: str, value: int) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        OCRRequest.model_validate({field: value})


def test_page_selection_rejects_invalid_syntax() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="positive page range"):
        OCRRequest(pages="3-1")


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
