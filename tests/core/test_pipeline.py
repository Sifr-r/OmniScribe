"""
OCRPipeline orchestration tests with fully stubbed dependencies.

These validate wiring, concurrency, progress callbacks, and the refine
fallback without loading Surya or contacting the LLM.
"""

from __future__ import annotations

import base64
import io
from collections.abc import Awaitable, Callable

import pytest
from PIL import Image

from omniscribe.core.grounded import GroundedResponse
from omniscribe.core.workflows.hybrid import (
    _drop_refined_duplicates,
    _is_refinable,
    parse_page_range,
)
from omniscribe.core.workflows.repair import RepairOptions
from omniscribe.pipeline import OCRPipeline
from tests.conftest import _StubOCR


def _make_tiny_b64_image() -> str:
    # Paint dark stripes so any cropped sub-region has enough pixel variance
    # to pass the refine-stage blank-crop guard. A pure-white image trips
    # is_blank_crop and short-circuits the refine path under test.
    from PIL import ImageDraw

    img = Image.new("RGB", (300, 300), "white")
    draw = ImageDraw.Draw(img)
    for y in range(0, 300, 20):
        draw.rectangle([0, y, 300, y + 5], fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


class _StubAligner:
    def __init__(self, boxes_per_page=None, alignment=None):
        self.boxes = boxes_per_page or [
            [0.1, 0.1, 0.9, 0.15],
            [0.1, 0.2, 0.9, 0.25],
            [0.1, 0.3, 0.9, 0.35],
        ]
        self.alignment = alignment  # callable or None (identity)

    def get_detected_boxes_batch(self, images):
        return [list(self.boxes) for _ in images]

    def align_text(self, structured, lines):
        if self.alignment:
            return self.alignment(structured, lines)
        # Default: populate every box with the corresponding line (padding/truncating).
        out = []
        for i, (box, _) in enumerate(structured):
            out.append((box, lines[i] if i < len(lines) else ""))
        return out


class _StubPDF:
    def __init__(self, n_pages: int = 2):
        self.n_pages = n_pages
        self.last_pages = None

    def convert_to_images(self, path, dpi=150, max_image_dim=1024):
        return {i: _make_tiny_b64_image() for i in range(self.n_pages)}

    def embed_structured_text(self, inp, out, pages, dpi):
        self.last_pages = dict(pages)  # type: ignore[assignment]


class TestParsePageRange:
    def test_single_page(self):
        assert parse_page_range("3", 10) == [2]

    def test_range(self):
        assert parse_page_range("1-3", 10) == [0, 1, 2]

    def test_mixed(self):
        assert parse_page_range("1-3,5,7-9", 10) == [0, 1, 2, 4, 6, 7, 8]

    def test_out_of_range_clipped(self):
        assert parse_page_range("8-12", 5) == []  # none in range

    def test_duplicates_collapsed(self):
        assert parse_page_range("1,1,2-3,3", 5) == [0, 1, 2]


class TestDropRefinedDuplicates:
    """Post-refine dedup pass: refined text that already exists in a
    nearby matched box gets dropped, so the OCR text layer doesn't
    contain the same line twice."""

    def _box(self, idx: int) -> list[float]:
        # Generate a benign distinct bbox for each index so list ops
        # treat them as separate entries. Coordinates don't matter for
        # the dedup logic — it's index-based.
        return [0.1, 0.05 * idx, 0.9, 0.05 * idx + 0.04]

    def test_drops_exact_duplicate_in_adjacent_matched_box(self):
        # Refined text equals the matched neighbor — drop refined.
        boxes = [
            (self._box(0), "HEALTH INTAKE FORM"),  # matched
            (self._box(1), "HEALTH INTAKE FORM"),  # refined (dup)
        ]
        _drop_refined_duplicates(boxes, refined_indices={1})  # type: ignore[arg-type]
        assert boxes[0][1] == "HEALTH INTAKE FORM"  # matched kept
        assert boxes[1][1] == ""  # refined dropped

    def test_drops_substring_of_concatenated_neighbor(self):
        # The DP can attach a skip_line to a matched box, producing a
        # concatenated string. Refine then re-OCRs the lost line's box
        # and produces the substring — drop the refined copy.
        boxes = [
            (self._box(0), "HEALTH INTAKE FORM Please fill out the form."),
            (self._box(1), "HEALTH INTAKE FORM"),  # refined (substring)
        ]
        _drop_refined_duplicates(boxes, refined_indices={1})  # type: ignore[arg-type]
        assert boxes[1][1] == ""

    def test_keeps_distinct_text_in_adjacent_box(self):
        # Two real entries that just happen to look similar — keep both.
        boxes = [
            (self._box(0), "Vyvanse (25mg) daily for attention"),  # matched
            (
                self._box(1),
                "Vyranse (25mg) daily for attention",
            ),  # refined, real 2nd entry
        ]
        _drop_refined_duplicates(boxes, refined_indices={1})  # type: ignore[arg-type]
        assert boxes[1][1] == "Vyranse (25mg) daily for attention"

    def test_case_and_whitespace_normalized(self):
        boxes = [
            (self._box(0), "Date: 9/14/19"),  # matched
            (self._box(1), "  date:    9/14/19  "),  # refined, sloppy
        ]
        _drop_refined_duplicates(boxes, refined_indices={1})  # type: ignore[arg-type]
        assert boxes[1][1] == ""

    def test_token_boundary_word_matching_preserves_short_words(self):
        # Short words like "in", "on", "1" must not be blanked out when adjacent to "training", "station"
        boxes = [
            (self._box(0), "staff training course"),
            (self._box(1), "in"),
        ]
        _drop_refined_duplicates(boxes, refined_indices={1})  # type: ignore[arg-type]
        assert boxes[1][1] == "in"

        boxes2 = [
            (self._box(0), "metro station stop"),
            (self._box(1), "on"),
        ]
        _drop_refined_duplicates(boxes2, refined_indices={1})  # type: ignore[arg-type]
        assert boxes2[1][1] == "on"

    def test_does_not_compare_two_refined_against_each_other(self):
        # Both boxes are refined: each could be a real recovery, even if
        # they happen to OCR identically. Don't drop either — that's the
        # caller's job at a higher layer if it matters.
        boxes = [
            (self._box(0), "same content"),
            (self._box(1), "same content"),
        ]
        _drop_refined_duplicates(boxes, refined_indices={0, 1})  # type: ignore[arg-type]
        assert boxes[0][1] == "same content"
        assert boxes[1][1] == "same content"

    def test_respects_search_radius(self):
        # Refined box is far from the matching box — no dedup.
        boxes = [(self._box(i), "filler") for i in range(20)]
        boxes[0] = (self._box(0), "the line")
        boxes[15] = (self._box(15), "the line")
        _drop_refined_duplicates(boxes, refined_indices={15}, radius=4)  # type: ignore[arg-type]
        assert boxes[15][1] == "the line"  # too far, not deduped

    def test_empty_refined_text_skipped(self):
        # Refined returned empty (e.g. blank-crop short circuit) — leave it.
        boxes = [
            (self._box(0), "real content"),
            (self._box(1), ""),
        ]
        _drop_refined_duplicates(boxes, refined_indices={1})  # type: ignore[arg-type]
        assert boxes[1][1] == ""


class TestRefinableGate:
    def test_accepts_medium_boxes(self):
        assert _is_refinable([0.1, 0.1, 0.5, 0.2])

    def test_rejects_thin_rule_lines(self):
        assert not _is_refinable([0.1, 0.1, 0.9, 0.105])

    def test_rejects_tiny_decorations(self):
        assert not _is_refinable([0.1, 0.1, 0.11, 0.11])


class TestOCRPipeline:
    async def test_basic_e2e(self, stub_ocr):
        aligner = _StubAligner()
        pdf = _StubPDF(n_pages=2)
        pipe = OCRPipeline(aligner, stub_ocr, pdf)

        result = await pipe.run("in.pdf", "out.pdf", concurrency=2, refine=False)

        assert set(result.keys()) == {0, 1}
        assert pdf.last_pages is not None
        assert set(pdf.last_pages.keys()) == {0, 1}
        # Every page got written with 3 boxes (as defined by StubAligner).
        for p in pdf.last_pages.values():
            assert len(p) == 3

    async def test_refine_fills_empty_boxes(self, make_stub_ocr):
        ocr = make_stub_ocr(page_lines=["only one line"], crop_text="from crop")

        def alignment_with_gap(structured, lines):
            # Populate box 0, leave 1 and 2 empty.
            out = []
            for i, (b, _) in enumerate(structured):
                out.append((b, lines[0] if i == 0 else ""))
            return out

        aligner = _StubAligner(alignment=alignment_with_gap)
        pdf = _StubPDF(n_pages=1)
        pipe = OCRPipeline(aligner, ocr, pdf)

        await pipe.run("in.pdf", "out.pdf", concurrency=2, refine=True)

        # 2 empty boxes per page × 1 page = 2 crop calls.
        assert ocr.crop_calls == 2
        texts = [t for _, t in pdf.last_pages[0]]  # type: ignore[index]
        assert texts[0] == "only one line"
        assert texts[1] == "from crop"
        assert texts[2] == "from crop"

    async def test_refine_skips_when_disabled(self, make_stub_ocr):
        ocr = make_stub_ocr(page_lines=["single"])
        aligner = _StubAligner(
            alignment=lambda s, lines: (
                [(s[0][0], lines[0])] + [(b, "") for b, _ in s[1:]]
            )
        )
        pdf = _StubPDF(n_pages=1)
        pipe = OCRPipeline(aligner, ocr, pdf)

        await pipe.run("in.pdf", "out.pdf", refine=False)
        assert ocr.crop_calls == 0

    async def test_dense_mode_always_uses_per_box_ocr(self, make_stub_ocr):
        # dense_mode="always" should bypass full-page OCR entirely and OCR
        # every detected box individually via perform_ocr_on_crop.
        ocr = make_stub_ocr(page_lines=["fullpage line"], crop_text="per-box")
        aligner = _StubAligner()  # 3 boxes per page
        pdf = _StubPDF(n_pages=2)
        pipe = OCRPipeline(aligner, ocr, pdf)

        await pipe.run(
            "in.pdf",
            "out.pdf",
            concurrency=3,
            refine=False,
            dense_mode="always",  # type: ignore[arg-type]
        )

        # 3 boxes × 2 pages = 6 per-box OCR calls. No full-page calls.
        assert ocr.page_calls == 0
        assert ocr.crop_calls == 6
        # Every box got the per-box text, not the (unused) full-page line.
        for page_boxes in pdf.last_pages.values():  # type: ignore[attr-defined]
            assert all(t == "per-box" for _, t in page_boxes)

    async def test_dense_mode_never_keeps_full_page(self, make_stub_ocr):
        ocr = make_stub_ocr(page_lines=["fullpage line"], crop_text="per-box")
        aligner = _StubAligner()
        pdf = _StubPDF(n_pages=1)
        pipe = OCRPipeline(aligner, ocr, pdf)

        await pipe.run(
            "in.pdf",
            "out.pdf",
            refine=False,
            dense_mode="never",  # type: ignore[arg-type]
        )
        assert ocr.page_calls == 1
        assert ocr.crop_calls == 0

    async def test_dense_mode_auto_picks_per_box_for_dense_pages(self, make_stub_ocr):
        ocr = make_stub_ocr(page_lines=["fullpage line"], crop_text="per-box")
        # 7 rows × 10 cols = 70 boxes, each 0.09 wide × 0.13 tall — well
        # above _is_refinable's 0.03×0.008 floor so every box reaches the
        # LLM. The stub page image's horizontal stripes ensure each crop
        # has enough pixel variance to pass the blank check.
        many_boxes = [
            [c * 0.10, r * 0.13, c * 0.10 + 0.09, r * 0.13 + 0.13]
            for r in range(7)
            for c in range(10)
        ]
        aligner = _StubAligner(boxes_per_page=many_boxes)
        pdf = _StubPDF(n_pages=1)
        pipe = OCRPipeline(aligner, ocr, pdf)

        await pipe.run(
            "in.pdf",
            "out.pdf",
            concurrency=5,
            refine=False,
            dense_mode="auto",  # type: ignore[arg-type]
            dense_threshold=60,
        )
        # 70 boxes > 60 threshold → per-box; full-page OCR was NOT called.
        assert ocr.page_calls == 0
        assert ocr.crop_calls == 70

    async def test_dense_mode_auto_keeps_full_page_for_sparse_pages(
        self, make_stub_ocr
    ):
        ocr = make_stub_ocr(page_lines=["fullpage line"], crop_text="per-box")
        aligner = _StubAligner()  # 3 boxes per page (sparse)
        pdf = _StubPDF(n_pages=1)
        pipe = OCRPipeline(aligner, ocr, pdf)

        await pipe.run(
            "in.pdf",
            "out.pdf",
            refine=False,
            dense_mode="auto",  # type: ignore[arg-type]
            dense_threshold=60,
        )
        assert ocr.page_calls == 1
        assert ocr.crop_calls == 0

    async def test_dense_mode_invalid_raises(self, stub_ocr):
        pipe = OCRPipeline(_StubAligner(), stub_ocr, _StubPDF(n_pages=1))
        with pytest.raises(ValueError, match="dense_mode"):
            await pipe.run("in.pdf", "out.pdf", dense_mode="invalid")  # type: ignore[arg-type]

    async def test_progress_stages_all_fire(self, stub_ocr):
        aligner = _StubAligner(alignment=lambda s, lines: [(b, "") for b, _ in s])
        pipe = OCRPipeline(aligner, stub_ocr, _StubPDF(n_pages=1))

        stages_seen = []

        async def cb(stage, cur, tot, msg):
            stages_seen.append(stage)

        await pipe.run("in.pdf", "out.pdf", progress=cb, refine=True)
        # All five pipeline stages should appear when refinement actually runs.
        assert set(stages_seen) == {"convert", "detect", "ocr", "refine", "embed"}

    async def test_progress_skips_refine_when_no_targets(self, stub_ocr):
        # StubAligner default fills every box, so refine has nothing to do.
        aligner = _StubAligner()
        pipe = OCRPipeline(aligner, stub_ocr, _StubPDF(n_pages=1))

        stages_seen = []

        async def cb(stage, cur, tot, msg):
            stages_seen.append(stage)

        await pipe.run("in.pdf", "out.pdf", progress=cb, refine=True)
        # Nothing to refine — the stage just doesn't emit.
        assert "refine" not in stages_seen
        assert {"convert", "detect", "ocr", "embed"}.issubset(stages_seen)

    async def test_concurrency_parameter_is_respected(self, make_stub_ocr):
        ocr = make_stub_ocr()
        pipe = OCRPipeline(_StubAligner(), ocr, _StubPDF(n_pages=4))
        await pipe.run("in.pdf", "out.pdf", concurrency=2, refine=False)
        assert ocr.page_calls == 4  # one per page

    async def test_custom_output_writer(self, stub_ocr):
        captured = {}

        def custom_writer(inp, out, pages, dpi):
            captured["called"] = True
            captured["pages"] = dict(pages)  # type: ignore[assignment]
            captured["dpi"] = dpi

        pipe = OCRPipeline(
            _StubAligner(), stub_ocr, _StubPDF(n_pages=1), output_writer=custom_writer
        )
        await pipe.run("in.pdf", "out.pdf", dpi=250, refine=False)

        assert captured.get("called") is True
        assert captured["dpi"] == 250
        assert 0 in captured["pages"]  # type: ignore[operator]

    async def test_per_page_failure_records_last_failed_pages_and_invokes_warning(
        self, stub_ocr
    ):
        """A page whose OCR call raises must land in last_failed_pages and
        trigger the on_warning callback exactly once; the surviving pages
        still produce a result and the PDF is still written."""

        class _FailingPageOCR(_StubOCR):
            def __init__(self):
                super().__init__()
                self.calls: list[int] = []
                self._counter = 0

            async def perform_ocr(self, image_base64, **kwargs):
                idx = self._counter
                self._counter += 1
                self.calls.append(idx)
                # Page 1 (the second call) fails. Pages 0 and 2 succeed.
                if idx == 1:
                    raise RuntimeError("simulated OCR timeout")
                return list(self.page_lines)

        warnings: list[tuple[int, BaseException]] = []

        async def on_warning(page_index, exc):
            warnings.append((page_index, exc))

        ocr = _FailingPageOCR()
        pdf = _StubPDF(n_pages=3)
        pipe = OCRPipeline(_StubAligner(), ocr, pdf)

        result = await pipe.run(
            "in.pdf", "out.pdf", concurrency=1, refine=False, on_warning=on_warning
        )

        # All three pages should still appear in the result; page 1 has
        # empty text because its OCR call raised, but the page is present.
        assert set(result.keys()) == {0, 1, 2}
        assert result[1] == []
        # The pipeline recorded exactly the failed page.
        assert pipe.last_failed_pages == [1]
        # The on_warning callback fired exactly once for the failed page.
        assert len(warnings) == 1
        assert warnings[0][0] == 1
        assert isinstance(warnings[0][1], RuntimeError)
        assert "simulated OCR timeout" in str(warnings[0][1])
        # The PDF writer still ran with the surviving pages' data.
        assert pdf.last_pages is not None
        assert set(pdf.last_pages.keys()) == {0, 1, 2}

    async def test_per_page_failure_resets_last_failed_pages_per_run(self, stub_ocr):
        """A second run on the same pipeline instance must not see the
        first run's failures — last_failed_pages is reset at run() entry."""

        class _FailingPageOCR(_StubOCR):
            def __init__(self):
                super().__init__()
                self._counter = 0

            async def perform_ocr(self, image_base64, **kwargs):
                self._counter += 1
                if self._counter == 1:
                    raise RuntimeError("first run page 0 fails")
                return list(self.page_lines)

        ocr = _FailingPageOCR()
        pdf = _StubPDF(n_pages=1)
        pipe = OCRPipeline(_StubAligner(), ocr, pdf)

        # First run: page 0 fails.
        await pipe.run("in.pdf", "out-1.pdf", concurrency=1, refine=False)
        assert pipe.last_failed_pages == [0]

        # Second run: same pipeline, but the stub's counter has moved on,
        # so this run is all-success. last_failed_pages must reset.
        await pipe.run("in.pdf", "out-2.pdf", concurrency=1, refine=False)
        assert pipe.last_failed_pages == []


class TestPipelineAclose:
    """Audit M-domain 2: the per-request pipeline must release the long-lived
    AsyncOpenAI client it builds in OCRProcessor.__init__.
    """

    async def test_hybrid_pipeline_closes_ocr_processor_client(self) -> None:
        from unittest.mock import AsyncMock

        ocr = _StubOCR()
        # Inject an aclose-capable client so we can observe the close.
        mock_client = AsyncMock()
        mock_client.aclose = AsyncMock()
        ocr.client = mock_client
        pipe = OCRPipeline(_StubAligner(), ocr, _StubPDF(n_pages=1))

        await pipe.aclose()

        mock_client.aclose.assert_awaited_once()
        # The aclose method must clear the reference so a second call is a
        # no-op rather than re-closing a (possibly already-closed) client.
        assert ocr.client is None
        await pipe.aclose()  # idempotent

    async def test_grounded_pipeline_aclose_is_noop(self) -> None:
        from unittest.mock import AsyncMock, MagicMock

        from omniscribe.core.grounded import GroundedResponse

        backend = MagicMock()
        backend.ocr_document = AsyncMock(
            return_value=GroundedResponse(blocks=[], failed_pages=[])
        )
        # No ocr_processor attribute on the grounded engine; aclose must
        # handle that without raising.
        pipe = OCRPipeline(
            aligner=None,
            ocr_processor=None,
            pdf_handler=_StubPDF(n_pages=1),
            grounded_backend=backend,
        )
        await pipe.aclose()  # must not raise

    async def test_pipeline_async_context_manager(self) -> None:
        from unittest.mock import AsyncMock

        ocr = _StubOCR()
        mock_client = AsyncMock()
        mock_client.aclose = AsyncMock()
        ocr.client = mock_client
        async with OCRPipeline(_StubAligner(), ocr, _StubPDF(n_pages=1)) as pipe:
            assert isinstance(pipe, OCRPipeline)
        mock_client.aclose.assert_awaited_once()


class TestPipelineRepairPassthrough:
    async def test_hybrid_run_forwards_repair_options(self, stub_ocr):
        pipe = OCRPipeline(_StubAligner(), stub_ocr, _StubPDF(n_pages=1))
        captured: dict = {}

        async def fake_execute(**kwargs):
            captured.update(kwargs)
            return {}

        pipe._engine.execute = fake_execute  # type: ignore[attr-defined]
        opts = RepairOptions(target=0.9)

        await pipe.run("in.pdf", "out.pdf", repair_options=opts)

        assert captured["repair_options"] is opts

    async def test_hybrid_run_defaults_repair_options_to_none(self, stub_ocr):
        pipe = OCRPipeline(_StubAligner(), stub_ocr, _StubPDF(n_pages=1))
        captured: dict = {}

        async def fake_execute(**kwargs):
            captured.update(kwargs)
            return {}

        pipe._engine.execute = fake_execute  # type: ignore[attr-defined]

        await pipe.run("in.pdf", "out.pdf")

        assert captured["repair_options"] is None

    async def test_grounded_run_forwards_repair_options(self):
        class _TinyGroundedBackend:
            async def ocr_document(
                self,
                pdf_path: str,
                progress: Callable[[str, int, int, str], Awaitable[None]] | None = None,
                on_warning: Callable[[int, BaseException], Awaitable[None]]
                | None = None,
                *,
                pages: str | None = None,
            ) -> GroundedResponse:
                return GroundedResponse(blocks=[])

        pipe = OCRPipeline(
            grounded_backend=_TinyGroundedBackend(),
            pdf_handler=_StubPDF(n_pages=1),
        )
        captured: dict = {}

        async def fake_execute(**kwargs):
            captured.update(kwargs)
            return {}

        pipe._engine.execute = fake_execute  # type: ignore[attr-defined]
        opts = RepairOptions(enabled=True, target=0.95)

        await pipe.run("in.pdf", "out.pdf", repair_options=opts)

        assert captured["repair_options"] is opts


class TestWhitespaceRecallWiring:
    def test_pipeline_injects_enabled_booster_by_default(self, monkeypatch):
        monkeypatch.delenv("OMNISCRIBE_WHITESPACE_RECALL", raising=False)
        pipe = OCRPipeline(_StubAligner(), _StubOCR(), _StubPDF())
        booster = pipe._engine.recall_booster  # type: ignore[attr-defined]
        assert booster is not None
        assert booster.options.enabled is True

    def test_pipeline_env_kill_switch_disables_booster(self, monkeypatch):
        monkeypatch.setenv("OMNISCRIBE_WHITESPACE_RECALL", "off")
        pipe = OCRPipeline(_StubAligner(), _StubOCR(), _StubPDF())
        booster = pipe._engine.recall_booster  # type: ignore[attr-defined]
        assert booster is not None
        assert booster.options.enabled is False


class TestPackagePublicExports:
    def test_omniscribe_root_exports(self):
        import omniscribe

        for sym in omniscribe.__all__:
            assert hasattr(omniscribe, sym), f"Missing omniscribe.{sym}"

    def test_omniscribe_core_exports(self):
        import omniscribe.core

        for sym in omniscribe.core.__all__:
            assert hasattr(omniscribe.core, sym), f"Missing omniscribe.core.{sym}"

    def test_omniscribe_pipeline_exports(self):
        from omniscribe.pipeline import OCRPipeline, parse_page_range

        assert OCRPipeline is not None
        assert callable(parse_page_range)

    def test_omniscribe_ocr_quality_exports(self):
        import omniscribe.core.ocr_quality

        for sym in omniscribe.core.ocr_quality.__all__:
            assert hasattr(omniscribe.core.ocr_quality, sym), (
                f"Missing omniscribe.core.ocr_quality.{sym}"
            )
