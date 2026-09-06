"""
Tests for the grounded-OCR pipeline path (no Surya / no DP / no refine).

Validates:
    - Z.AI hosted response parser against a real captured fixture.
    - layout_details parser (GLM-OCR via vLLM / self-hosted).
    - OCRPipeline routes to the grounded path when a backend is supplied.
    - Grounded pipeline produces a searchable PDF whose extracted text is
      recoverable at the expected positions.
"""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pymupdf as fitz
import pytest
from PIL import Image

from omniscribe.core.grounded import (
    GroundedBlock,
    GroundedResponse,
    PromptedGroundedOCR,
    _parse_grounded_json,
    _rasterize_to_jpeg_pages,
    log_grounded_parse_failure,
    parse_glm_layout_details,
    parsers,
)
from omniscribe.core.grounded.models import RepairableGroundedBackend
from omniscribe.core.ocr import LLMCallError, ModelNotLoadedError
from omniscribe.core.pdf import PDFHandler
from omniscribe.pipeline import OCRPipeline

FIXTURES = Path(__file__).parent.parent.parent / "fixtures"


def test_grounded_pdf_rasterization_skips_intermediate_jpeg_decode(
    example_pdfs: dict[str, Path],
    monkeypatch,
):
    """PDF pixmaps should convert directly before the final thumbnail JPEG."""
    original_open = Image.open

    def reject_bytesio(source, *args, **kwargs):
        if isinstance(source, io.BytesIO):
            raise AssertionError("unexpected intermediate JPEG decode")
        return original_open(source, *args, **kwargs)

    monkeypatch.setattr(Image, "open", reject_bytesio)
    pages = _rasterize_to_jpeg_pages(str(example_pdfs["digital.pdf"]), 1024, 150)

    assert len(pages) == 1
    encoded, width, height = pages[0]
    assert max(width, height) <= 1024
    assert original_open(io.BytesIO(base64.b64decode(encoded))).format == "JPEG"


# --- parsers ----------------------------------------------------------------


class TestParseGLMLayoutDetails:
    def test_parses_flat_list(self):
        payload = {
            "data_info": {"pages": [{"width": 1000, "height": 2000}]},
            "layout_details": [
                {
                    "label": "text",
                    "content": "Hello world",
                    "bbox_2d": [100, 200, 500, 260],
                },
                {"label": "image", "content": "...", "bbox_2d": [0, 0, 100, 100]},
            ],
        }
        response = parse_glm_layout_details(payload)
        assert len(response.blocks) == 1
        assert response.blocks[0].text == "Hello world"
        assert response.blocks[0].bbox == [0.1, 0.1, 0.5, 0.13]

    def test_parses_nested_per_page_list(self):
        payload = {
            "data_info": {"pages": [{"width": 1000, "height": 2000}]},
            "layout_details": [
                [
                    {
                        "label": "text",
                        "content": "On page 0",
                        "bbox_2d": [0, 0, 500, 100],
                    }
                ],
            ],
        }
        response = parse_glm_layout_details(payload, page_index=0)
        assert response.blocks[0].text == "On page 0"

    def test_accepts_json_string(self):
        payload = {
            "data_info": {"pages": [{"width": 100, "height": 100}]},
            "layout_details": [
                {"label": "text", "content": "x", "bbox_2d": [0, 0, 50, 50]},
            ],
        }
        response = parse_glm_layout_details(json.dumps(payload))
        assert len(response.blocks) == 1


# --- pipeline routing -------------------------------------------------------


class _StubGroundedBackend:
    """Canned backend — returns fixed blocks, records invocation + progress."""

    def __init__(self, blocks: list[GroundedBlock], page_sizes):
        self.response = GroundedResponse(blocks=blocks, page_sizes=page_sizes)
        self.called_with: list[str] = []
        self.progress_calls: list[tuple] = []
        self.warning_calls: list[tuple[int, BaseException]] = []

    async def ocr_document(
        self, pdf_path: str, progress=None, on_warning=None
    ) -> GroundedResponse:
        self.called_with.append(pdf_path)
        if progress is not None:
            # Mimic a real backend's per-page emission so pipeline-level
            # progress adapters get something meaningful.
            n = len({b.page_index for b in self.response.blocks}) or 1
            await progress("ocr", 0, n, f"Stub grounded OCR (0/{n})...")
            await progress("ocr", n, n, f"Stub grounded OCR ({n}/{n})")
        return self.response


async def test_pipeline_routes_to_grounded_when_backend_provided(
    example_pdfs: dict[str, Path], tmp_path: Path
):
    """Grounded path skips Surya entirely — no aligner or ocr_processor needed."""
    marker_blocks = [
        GroundedBlock(
            bbox=[0.10, 0.10, 0.60, 0.14], text="GROUNDED_ALPHA", page_index=0
        ),
        GroundedBlock(
            bbox=[0.10, 0.30, 0.60, 0.34], text="GROUNDED_BETA", page_index=0
        ),
        GroundedBlock(
            bbox=[0.10, 0.60, 0.60, 0.64], text="GROUNDED_GAMMA", page_index=0
        ),
    ]
    backend = _StubGroundedBackend(marker_blocks, page_sizes=[(1000, 1300)])

    input_pdf = str(example_pdfs["digital.pdf"])
    output_pdf = str(tmp_path / "grounded_out.pdf")

    pipe = OCRPipeline(pdf_handler=PDFHandler(), grounded_backend=backend)
    pages_text = await pipe.run(input_pdf, output_pdf)

    # Backend was called with the input path.
    assert backend.called_with == [input_pdf]
    # All three markers ended up in the output's searchable layer.
    assert pages_text[0] == ["GROUNDED_ALPHA", "GROUNDED_BETA", "GROUNDED_GAMMA"]
    with fitz.open(output_pdf) as doc:
        text = doc[0].get_text("text")
    assert "GROUNDED_ALPHA" in text
    assert "GROUNDED_BETA" in text
    assert "GROUNDED_GAMMA" in text


async def test_grounded_path_preserves_bbox_position(
    example_pdfs: dict[str, Path], tmp_path: Path
):
    """Text emitted by the grounded backend must land *inside* its bbox —
    this is the same positional-correspondence guarantee as the hybrid path."""
    block = GroundedBlock(
        bbox=[0.20, 0.30, 0.60, 0.34],
        text="POSMARKER_ZETA",
        page_index=0,
    )
    backend = _StubGroundedBackend([block], page_sizes=[(1000, 1300)])

    input_pdf = str(example_pdfs["digital.pdf"])
    output_pdf = str(tmp_path / "grounded_pos.pdf")

    pipe = OCRPipeline(pdf_handler=PDFHandler(), grounded_backend=backend)
    await pipe.run(input_pdf, output_pdf)

    with fitz.open(output_pdf) as doc:
        page = doc[0]
        pw, ph = page.rect.width, page.rect.height
        words = page.get_text("words")

    expected_rect = fitz.Rect(0.20 * pw, 0.30 * ph, 0.60 * pw, 0.34 * ph)
    hits = [w for w in words if "POSMARKER_ZETA" in w[4]]
    assert hits, "marker missing from grounded output"
    for w in hits:
        wr = fitz.Rect(w[0], w[1], w[2], w[3])
        inter = wr & expected_rect
        assert not inter.is_empty, (
            f"grounded marker at {list(wr)} outside {list(expected_rect)}"  # type: ignore[call-overload]
        )
        overlap = inter.get_area() / max(1e-6, wr.get_area())
        assert overlap >= 0.5, f"overlap too low: {overlap:.2f}"


async def test_grounded_path_propagates_failed_pages_to_pipeline(
    example_pdfs: dict[str, Path], tmp_path: Path
):
    """When a grounded backend reports per-page failures, the pipeline
    surfaces them on ``last_failed_pages`` and forwards them to the
    caller's ``on_warning`` callback."""

    class _FailingGroundedBackend(_StubGroundedBackend):
        def __init__(self, fail_pages: set[int]):
            super().__init__(blocks=[], page_sizes=[(1000, 1300)] * 3)
            self.fail_pages = fail_pages
            self._counter = 0

        async def ocr_document(self, pdf_path, progress=None, on_warning=None):
            self.called_with.append(pdf_path)
            for page_idx in range(3):
                if page_idx in self.fail_pages:
                    err = RuntimeError(f"grounded failure on page {page_idx}")
                    if on_warning is not None:
                        await on_warning(page_idx, err)
            return GroundedResponse(
                blocks=[],
                page_sizes=[(1000, 1300)] * 3,
                failed_pages=sorted(self.fail_pages),
            )

    warnings: list[tuple[int, BaseException]] = []

    async def on_warning(page_index, exc):
        warnings.append((page_index, exc))

    backend = _FailingGroundedBackend(fail_pages={0, 2})
    pipe = OCRPipeline(pdf_handler=PDFHandler(), grounded_backend=backend)

    input_pdf = str(example_pdfs["digital.pdf"])
    output_pdf = str(tmp_path / "grounded_partial.pdf")
    await pipe.run(input_pdf, output_pdf, on_warning=on_warning)

    assert pipe.last_failed_pages == [0, 2]
    assert [w[0] for w in warnings] == [0, 2]
    assert all(isinstance(w[1], RuntimeError) for w in warnings)
    # PDF still written.
    assert Path(output_pdf).exists()


def test_pipeline_rejects_construction_without_pdf_handler():
    with pytest.raises(ValueError, match="pdf_handler is required"):
        OCRPipeline(grounded_backend=_StubGroundedBackend([], [(100, 100)]))


def test_pipeline_rejects_hybrid_run_without_aligner_or_ocr(
    example_pdfs: dict[str, Path], tmp_path: Path
):
    """Hybrid path needs both `aligner` and `ocr_processor`. If a user forgets
    to pass either (and doesn't supply a grounded backend), `OCRPipeline`
    initialization should raise an explicit ValueError."""
    with pytest.raises(ValueError, match="Hybrid pipeline requires"):
        OCRPipeline(pdf_handler=PDFHandler())  # no aligner, no ocr


async def test_grounded_path_forwards_progress_callback(
    example_pdfs: dict[str, Path], tmp_path: Path
):
    """The pipeline should forward its progress callback into the grounded
    backend so users see per-page ticks instead of a 0→100 jump."""
    block = GroundedBlock(bbox=[0.1, 0.1, 0.5, 0.15], text="X", page_index=0)
    backend = _StubGroundedBackend([block], page_sizes=[(1000, 1300)])

    stages: list[str] = []

    async def cb(stage, cur, tot, msg):
        stages.append(stage)

    pipe = OCRPipeline(pdf_handler=PDFHandler(), grounded_backend=backend)
    await pipe.run(
        str(example_pdfs["digital.pdf"]),
        str(tmp_path / "out.pdf"),
        progress=cb,
    )

    # Backend should have emitted "ocr" stage ticks via the forwarded callback,
    # and the pipeline should still emit "embed" for the output-writing phase.
    assert "ocr" in stages
    assert "embed" in stages


class TestPromptedGroundedResilience:
    """R2 + R3: per-page failures must not tank the entire run, and progress
    must tick per page."""

    async def test_one_failing_page_does_not_lose_others(self, monkeypatch):
        # Build a fake PromptedGroundedOCR that renders 3 fake pages and makes
        # page 1 fail while pages 0 and 2 succeed.
        from omniscribe.core.grounded import PromptedGroundedOCR

        class _FakeClient:
            def __init__(self, *a, **kw):
                self.chat = self
                self.completions = self
                self.calls = 0

            async def create(self, **kwargs):
                idx = self.calls
                self.calls += 1
                if idx == 1:
                    raise RuntimeError("boom on page 1")

                class _Choice:
                    message = type(
                        "M",
                        (),
                        {"content": f'[{{"bbox_2d":[0,0,100,50],"content":"p{idx}"}}]'},
                    )

                class _Resp:
                    choices = [_Choice]

                return _Resp

        # Monkey-patch AsyncOpenAI inside grounded.py to return our fake.

        class _FakeAsyncOpenAI:
            def __init__(self, *a, **kw):
                pass

            def __new__(cls, *a, **kw):
                return _FakeClient()

        monkeypatch.setattr("openai.AsyncOpenAI", _FakeAsyncOpenAI)

        PromptedGroundedOCR(max_image_dim=64, concurrency=1)

        # Fake the page-rasterization step by pre-seeding page_imgs via monkey-
        # patching: replace fitz.open so we drive 3 synthetic pages.
        import base64
        import io

        from PIL import Image

        def _tiny_b64():
            buf = io.BytesIO()
            Image.new("RGB", (64, 64), "white").save(buf, "JPEG")
            return base64.b64encode(buf.getvalue()).decode()

        # Reach into the backend's own loop by providing a PDF whose page count
        # matches. Easier: subclass and override the rasterization step.
        class _Fixed(PromptedGroundedOCR):
            async def ocr_document(self, pdf_path, progress=None, on_warning=None):
                # Copy the live method but seed page_imgs directly.
                self_ = self
                import asyncio as _a

                from openai import AsyncOpenAI

                import omniscribe.core.grounded as _g

                page_imgs = [(_tiny_b64(), 100, 100)] * 3
                client = AsyncOpenAI(
                    base_url=self_.api_base,
                    api_key=self_.api_key,
                    timeout=self_.timeout_s,
                )
                sem = _a.Semaphore(max(1, self_.concurrency))
                total_pages = len(page_imgs)

                async def run_one(page_idx: int):
                    async with sem:
                        try:
                            resp = await client.chat.completions.create(
                                model=self_.model,
                                temperature=0.0,
                                max_tokens=self_.max_tokens,
                                messages=[{"role": "user", "content": []}],
                            )
                            text = (resp.choices[0].message.content or "").strip()
                            return page_idx, _g._parse_grounded_json(
                                text,
                                page_idx,
                                100,
                                100,
                            )
                        except Exception:
                            return page_idx, []

                tasks = [_a.create_task(run_one(i)) for i in range(total_pages)]
                blocks_by_page: dict[int, list] = {}
                for fut in _a.as_completed(tasks):
                    page_idx, blocks = await fut
                    blocks_by_page[page_idx] = blocks

                flat = []
                for i in range(total_pages):
                    flat.extend(blocks_by_page.get(i, []))
                return _g.GroundedResponse(blocks=flat, page_sizes=[(100, 100)] * 3)

        response = await _Fixed().ocr_document("ignored.pdf")
        texts = [b.text for b in response.blocks]
        # Pages 0 and 2 should survive; page 1 raised and returned empty.
        assert "p0" in texts
        assert "p2" in texts
        assert "p1" not in texts  # page 1 failed silently

    async def test_prompted_grounded_cancels_pending_tasks_on_circuit_break(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import asyncio

        from omniscribe.core.ocr.resilience import CircuitOpenError

        inst = PromptedGroundedOCR(model="fake-model")
        monkeypatch.setattr(
            "omniscribe.core.grounded.prompted._rasterize_to_jpeg_pages",
            lambda *a, **kw: [("img0", 100, 100), ("img1", 100, 100)],
        )

        cancelled_pages: list[str] = []

        async def fake_call(b64: str, prompt: str | None = None) -> str:
            if b64 == "img0":
                raise CircuitOpenError(3, 5.0)
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                cancelled_pages.append(b64)
                raise
            return "[]"

        monkeypatch.setattr(inst, "_call_with_retry", fake_call)

        with pytest.raises(CircuitOpenError):
            await inst.ocr_document("fake.pdf")

        assert "img1" in cancelled_pages


# --- prompted grounded JSON parser (Qwen2.5-VL / Qwen3-VL response shapes) --


class TestPromptedGroundedParser:
    def test_bare_json_array_qwen3_vl_style(self):
        # Qwen3-VL returns a bare JSON array with no fence wrapper.
        raw = (
            '[{"bbox_2d": [40, 38, 175, 96], "content": "Algorithms"}, '
            '{"bbox_2d": [50, 107, 487, 153], "content": "- computational procedure"}]'
        )
        blocks = _parse_grounded_json(raw, page_idx=0, img_w=800, img_h=1000)
        assert len(blocks) == 2
        assert blocks[0].text == "Algorithms"
        # Coordinates should be normalized.
        assert blocks[0].bbox == [40 / 800, 38 / 1000, 175 / 800, 96 / 1000]

    def test_fenced_json_qwen25_vl_style(self):
        # Qwen2.5-VL wraps in ```json ... ```.
        raw = (
            "```json\n"
            "[\n"
            '    {"bbox_2d": [40, 38, 175, 96], "content": "Algorithms,"},\n'
            '    {"bbox_2d": [68, 117, 226, 150], "content": "- computational"}\n'
            "]\n"
            "```"
        )
        blocks = _parse_grounded_json(raw, page_idx=0, img_w=800, img_h=1000)
        assert len(blocks) == 2
        assert blocks[0].text == "Algorithms,"

    def test_object_wrapping_array(self):
        # Some models wrap the array in {"results": [...]}.
        raw = '{"results": [{"bbox_2d":[0,0,100,50],"content":"hi"}]}'
        blocks = _parse_grounded_json(raw, page_idx=2, img_w=100, img_h=100)
        assert len(blocks) == 1
        assert blocks[0].page_index == 2
        assert blocks[0].text == "hi"

    def test_invalid_bbox_filtered(self):
        raw = (
            '[{"bbox_2d":[0,0,100,50],"content":"keep"},'
            '{"bbox_2d":[100,0,0,50],"content":"drop-x-reversed"},'
            '{"bbox_2d":[0,100,100,50],"content":"drop-y-reversed"},'
            '{"content":"drop-missing-bbox"},'
            '{"bbox_2d":[0,0,10],"content":"drop-wrong-length"}]'
        )
        blocks = _parse_grounded_json(raw, page_idx=0, img_w=100, img_h=100)
        assert [b.text for b in blocks] == ["keep"]

    def test_empty_content_filtered(self):
        raw = '[{"bbox_2d":[0,0,10,10],"content":"  "},{"bbox_2d":[0,10,10,20],"content":"real"}]'
        blocks = _parse_grounded_json(raw, page_idx=0, img_w=10, img_h=20)
        assert [b.text for b in blocks] == ["real"]

    def test_empty_input_returns_empty(self):
        assert _parse_grounded_json("", 0, 100, 100) == []

    def test_garbage_input_returns_empty(self):
        assert _parse_grounded_json("not json at all", 0, 100, 100) == []

    def test_alternate_field_names(self):
        # Accept `bbox` + `text` as aliases.
        raw = '[{"bbox":[0,0,50,50],"text":"alt-named"}]'
        blocks = _parse_grounded_json(raw, page_idx=0, img_w=100, img_h=100)
        assert blocks[0].text == "alt-named"

    def test_preamble_prose_tolerated(self):
        raw = 'Here is the result:\n[{"bbox_2d":[0,0,10,10],"content":"x"}]'
        blocks = _parse_grounded_json(raw, page_idx=0, img_w=10, img_h=10)
        assert len(blocks) == 1

    def test_invalid_json_types_return_empty(self):
        # JSON parsed successfully but not list/dict (e.g. null, true, 123)
        assert _parse_grounded_json("null", 0, 100, 100) == []
        assert _parse_grounded_json("123", 0, 100, 100) == []
        assert _parse_grounded_json("true", 0, 100, 100) == []
        assert _parse_grounded_json('"just a string"', 0, 100, 100) == []

    def test_log_grounded_parse_failure_emits_warning(self, caplog):
        with caplog.at_level("WARNING"):
            log_grounded_parse_failure("invalid raw json", 1, ValueError("test err"))
        assert "Grounded bbox JSON parsing failed on page 1: test err" in caplog.text

    # --- tolerance added for VLM response-shape variance (Phase 1 fix) ---

    def test_alternate_bbox_keys_accepted(self):
        # VLMs vary: some use `box`, `box_2d`, `bounding_box`, or
        # `coordinates` instead of `bbox_2d`. First matching alias wins.
        for key in ("box", "box_2d", "bounding_box", "coordinates"):
            raw = f'[{{"{key}": [10, 20, 110, 70], "content": "via {key}"}}]'
            blocks = _parse_grounded_json(raw, page_idx=0, img_w=200, img_h=200)
            assert len(blocks) == 1, f"failed for key {key}"
            assert blocks[0].text == f"via {key}"

    def test_alternate_content_keys_accepted(self):
        for key in ("text", "label_text", "ocr_text"):
            raw = f'[{{"bbox_2d":[10,20,110,70],"{key}":"via {key}"}}]'
            blocks = _parse_grounded_json(raw, page_idx=0, img_w=200, img_h=200)
            assert len(blocks) == 1
            assert blocks[0].text == f"via {key}"

    def test_already_normalized_coords_passed_through(self):
        # If the VLM already emitted 0..1 coords (e.g. an upstream
        # wrapper normalizes them), don't divide again — that would
        # collapse every box to a 0×0 dot.
        raw = (
            '[{"bbox_2d":[0.05, 0.10, 0.55, 0.35], "content":"line one"},'
            '{"bbox_2d":[0.05, 0.40, 0.55, 0.65], "content":"line two"}]'
        )
        blocks = _parse_grounded_json(raw, page_idx=0, img_w=1024, img_h=1024)
        assert len(blocks) == 2
        assert blocks[0].bbox == [0.05, 0.10, 0.55, 0.35]
        assert blocks[1].bbox == [0.05, 0.40, 0.55, 0.65]

    def test_xywh_auto_detected_when_xyxy_out_of_range(self):
        # Literal XYXY would put x1 past the image (1500 > 1000), so the
        # parser falls back to interpreting (x1, y1) as (w, h).
        raw = '[{"bbox_2d":[100, 200, 300, 150], "content":"xywh block"}]'
        blocks = _parse_grounded_json(raw, page_idx=0, img_w=1000, img_h=1000)
        assert len(blocks) == 1
        # x0=100, y0=200, w=300, h=150 → (100+300)/1000=0.4, (200+150)/1000=0.35
        assert blocks[0].bbox == pytest.approx([0.1, 0.2, 0.4, 0.35])

    def test_xyxy_in_range_not_treated_as_xywh(self):
        # x1=400 and y1=300 are both inside the 1000×1000 image, so the
        # XYWH branch should NOT fire — it should be treated as XYXY.
        raw = '[{"bbox_2d":[100, 200, 400, 300], "content":"xyxy block"}]'
        blocks = _parse_grounded_json(raw, page_idx=0, img_w=1000, img_h=1000)
        assert len(blocks) == 1
        assert blocks[0].bbox == pytest.approx([0.1, 0.2, 0.4, 0.3])

    def test_drop_summary_logged_at_info_when_items_dropped(self, caplog):
        # The summary log makes the "all blocks dropped" failure mode
        # easy to spot in server logs.
        raw = (
            '[{"bbox_2d":[0,0,10,10],"content":"keep"},'
            '{"content":"no-bbox"},'
            '{"bbox_2d":[0,0,10,10],"content":"  "}]'
        )
        with caplog.at_level("INFO", logger="omniscribe.core.grounded.parsers"):
            _parse_grounded_json(raw, page_idx=7, img_w=10, img_h=10)
        assert "page 7" in caplog.text
        assert "missing_or_bad_bbox=1" in caplog.text
        assert "empty_content=1" in caplog.text

    def test_truncated_json_array_recovery(self):
        # When VLM stream is cut off mid-array by token limit, the parser
        # should recover all complete bounding boxes before the cutoff.
        raw = (
            '[{"bbox_2d":[0,0,10,10],"content":"first complete block"},'
            '{"bbox_2d":[0,10,10,20],"content":"second complete block"},'
            '{"bbox_2d":[0,20,10,30],'
        )
        blocks = _parse_grounded_json(raw, page_idx=0, img_w=100, img_h=100)
        assert len(blocks) == 2
        assert blocks[0].text == "first complete block"
        assert blocks[1].text == "second complete block"


class TestPromptedGroundedEnsureModelLoaded:
    """Pre-flight model verification for the grounded path. Issue #7 was
    actually filed against the grounded path specifically — user had
    OlmOCR loaded but requested Qwen3-VL, and got OlmOCR's bad grounded
    output instead of Qwen3-VL's good output."""

    def _patch_openai(self, monkeypatch, model_ids=None, raise_exc=None):
        async def _list():
            if raise_exc is not None:
                raise raise_exc
            return SimpleNamespace(
                data=[SimpleNamespace(id=m) for m in (model_ids or [])]
            )

        fake_client = SimpleNamespace(models=SimpleNamespace(list=_list))

        def _fake_async_openai(*args, **kwargs):
            return fake_client

        # ensure_model_loaded imports AsyncOpenAI at the top of
        # `omniscribe.core.grounded.prompted`. Patching the source
        # `openai.AsyncOpenAI` is no longer sufficient — we need to
        # patch the module-level binding that PromptedGroundedOCR
        # actually references.
        monkeypatch.setattr(
            "omniscribe.core.grounded.prompted.AsyncOpenAI",
            _fake_async_openai,
        )
        return fake_client

    async def test_passes_when_model_loaded(self, monkeypatch):
        self._patch_openai(monkeypatch, model_ids=["qwen/qwen3-vl-8b"])
        backend = PromptedGroundedOCR(
            api_base="http://localhost:1234/v1",
            model="qwen/qwen3-vl-8b",
        )
        await backend.ensure_model_loaded()  # no raise

    async def test_raises_on_mismatch_with_helpful_message(self, monkeypatch):
        # The exact issue #7 scenario: requested grounded-capable Qwen3-VL,
        # but LM Studio has the OlmOCR text-only model loaded.
        self._patch_openai(monkeypatch, model_ids=["allenai_olmocr-2-7b-1025"])
        backend = PromptedGroundedOCR(
            api_base="http://localhost:1234/v1",
            model="qwen/qwen3-vl-8b",
        )
        with pytest.raises(ModelNotLoadedError) as exc_info:
            await backend.ensure_model_loaded()
        msg = str(exc_info.value)
        assert "qwen/qwen3-vl-8b" in msg
        assert "allenai_olmocr-2-7b-1025" in msg
        assert "--no-verify-model" in msg

    def test_subclass_of_llm_call_error(self):
        # Catchable via the same except-clause as other LLM failures.
        assert issubclass(ModelNotLoadedError, LLMCallError)


# ---------------------------------------------------------------------------
# F1.14 — RepairableGroundedBackend Protocol
# (re-homed from test_audit_medium_d1.py)
# ---------------------------------------------------------------------------


class TestRepairableGroundedBackendProtocol:
    """F1.14 audit fix: a typed ``RepairableGroundedBackend`` Protocol
    with a runtime ``isinstance`` check replaces the prior
    ``hasattr(..., "ocr_crop")`` duck-type + ``# type: ignore``.
    """

    def test_protocol_is_runtime_checkable(self) -> None:
        """The Protocol carries ``@runtime_checkable`` so
        ``isinstance(obj, RepairableGroundedBackend)`` works at runtime.
        """

        # Construct two minimal duck-typed objects and assert the
        # isinstance check reflects the presence/absence of the
        # ``ocr_crop`` method.
        class WithOcrCrop:
            async def ocr_crop(self, image_base64, bbox):
                return "text"

        class WithoutOcrCrop:
            async def ocr_document(self, pdf_path):
                return GroundedResponse(blocks=[])

        with_ = WithOcrCrop()
        without_ = WithoutOcrCrop()
        assert isinstance(with_, RepairableGroundedBackend)
        assert not isinstance(without_, RepairableGroundedBackend)

    def test_workflow_uses_isinstance_not_hasattr(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pin the contract that the grounded engine uses
        ``isinstance(..., RepairableGroundedBackend)`` as the gate, so
        a future refactor that reverts to ``hasattr`` lands a test
        failure here.
        """
        from omniscribe.core.workflows import grounded as grounded_mod

        # Read the source, normalise whitespace so we can match
        # multi-line calls cleanly.
        with open(grounded_mod.__file__, encoding="utf-8") as f:
            source = f.read()
        normalised = " ".join(source.split())
        assert (
            "isinstance(self.grounded_backend, RepairableGroundedBackend)" in normalised
        )
        assert 'hasattr(self.grounded_backend, "ocr_crop")' not in normalised
        # The legacy ``# type: ignore[attr-defined]`` for ``ocr_crop``
        # is gone too.
        assert (
            "self.grounded_backend.ocr_crop  # type: ignore[attr-defined]" not in source
        )


# ---------------------------------------------------------------------------
# F1.16 — GLM parser deny-list (re-homed from test_audit_medium_d1.py)
# ---------------------------------------------------------------------------


class TestGLMParserDenyList:
    """F1.16 audit fix: ``parse_glm_layout_details`` uses a deny-list
    of structural labels (image, figure, table, equation, ...) instead
    of the prior strict-allow-list ``!= "text"``. Future GLM label
    additions flow through; only the structural ones are dropped.
    """

    def test_strict_allow_list_dropped(self) -> None:
        """Pre-fix behaviour: ``label == "image"`` blocks were dropped.
        Post-fix behaviour: the same blocks are still dropped because
        ``"image"`` is in the structural deny-list.
        """
        payload = {
            "data_info": {"pages": [{"width": 1000, "height": 2000}]},
            "layout_details": [
                {"label": "text", "content": "Hello", "bbox_2d": [100, 200, 500, 260]},
                {"label": "image", "content": "...", "bbox_2d": [0, 0, 100, 100]},
            ],
        }
        resp = parsers.parse_glm_layout_details(payload)
        assert len(resp.blocks) == 1
        assert resp.blocks[0].text == "Hello"

    def test_new_content_label_passes_through(self) -> None:
        """A previously-unknown content label (e.g. ``"list_item"``)
        must NOT be dropped by the post-fix parser, because it is not
        in the structural deny-list.
        """
        payload = {
            "data_info": {"pages": [{"width": 1000, "height": 2000}]},
            "layout_details": [
                {"label": "text", "content": "Hello", "bbox_2d": [100, 200, 500, 260]},
                {
                    "label": "list_item",
                    "content": "First bullet",
                    "bbox_2d": [100, 300, 500, 360],
                },
            ],
        }
        resp = parsers.parse_glm_layout_details(payload)
        # Both blocks should be kept (one for "text", one for the
        # newly-allowed "list_item" content label).
        texts = sorted(b.text for b in resp.blocks)
        assert texts == ["First bullet", "Hello"]

    def test_label_omission_keeps_block(self) -> None:
        """Older fixtures omit the ``label`` field entirely; the
        pre-fix parser allowed those blocks (strict equality with
        ``"text"`` was False, so ``continue`` was not taken). The
        post-fix parser keeps the same behaviour.
        """
        payload = {
            "data_info": {"pages": [{"width": 1000, "height": 2000}]},
            "layout_details": [
                {"content": "No label", "bbox_2d": [100, 200, 500, 260]},
            ],
        }
        resp = parsers.parse_glm_layout_details(payload)
        assert len(resp.blocks) == 1
        assert resp.blocks[0].text == "No label"


class TestGLMParserHardening:
    """Malformed bbox payloads must not crash the parser; alias keys parse."""

    def _payload(self, *blocks: dict) -> dict:
        return {
            "data_info": {"pages": [{"width": 1000, "height": 2000}]},
            "layout_details": [list(blocks)],
        }

    def test_bbox_alias_keys_parse(self) -> None:
        payload = self._payload(
            {"content": "via bbox", "bbox": [100, 200, 500, 260]},
            {"content": "via box", "box": [110, 210, 510, 270]},
        )
        resp = parsers.parse_glm_layout_details(payload)
        assert [b.text for b in resp.blocks] == ["via bbox", "via box"]

    def test_missing_or_malformed_bbox_is_skipped(self) -> None:
        payload = self._payload(
            {"content": "no bbox at all"},
            {"content": "wrong length", "bbox_2d": [100, 200, 500]},
            {"content": "non numeric", "bbox_2d": [100, 200, "x", 500]},
            {"content": "fine", "bbox_2d": [100, 200, 500, 260]},
        )
        resp = parsers.parse_glm_layout_details(payload)
        assert [b.text for b in resp.blocks] == ["fine"]

    def test_non_finite_bbox_is_skipped(self) -> None:
        payload = self._payload(
            {"content": "nan box", "bbox_2d": [0, 0, float("nan"), 100]},
            {"content": "inf box", "bbox_2d": [0, 0, float("inf"), 100]},
            {"content": "fine", "bbox_2d": [100, 200, 500, 260]},
        )
        resp = parsers.parse_glm_layout_details(payload)
        assert [b.text for b in resp.blocks] == ["fine"]

    def test_parser_emits_drop_events(self, caplog) -> None:
        import logging

        caplog.set_level(logging.DEBUG, logger="omniscribe.core.ocr_quality.events")
        payload = self._payload(
            {"content": "no bbox at all"},
            {"content": "non numeric", "bbox_2d": [100, 200, "x", 500]},
        )
        parsers.parse_glm_layout_details(payload)
        messages = [
            r.getMessage()
            for r in caplog.records
            if "sub_module=parsers" in r.getMessage()
        ]
        assert any("drop:missing_bbox" in m for m in messages)
        assert any("drop:bad_bbox" in m for m in messages)
