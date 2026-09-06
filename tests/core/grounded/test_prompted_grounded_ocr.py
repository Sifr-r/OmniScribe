"""Comprehensive unit and mock test suite for PromptedGroundedOCR.

Covers:
- Prompt builder: system and user prompt construction across model architectures
  (standard models vs. models sensitive to system role like OlmOCR).
- Multi-page chunking logic and page index preservation:
  deterministic page-ordered flattening, concurrency limits, per-page isolation,
  and progress/warning callbacks.
- Coordinate parsing and normalization:
  - Canonical pixel XYXY coordinate normalization.
  - Bounding box key and content key aliases.
  - Clamping out-of-bounds coordinates to [0.0, 1.0].
  - Sorting boxes into natural reading order via ReadingOrderProcessor.
- Robust JSON repair loop:
  - Markdown code block fence stripping (```json ... ```, bare arrays, unclosed fences).
  - Truncated JSON array recovery.
  - Preamble/postamble prose extraction.
  - Nested dictionary wrapper recovery ({"results": [...]}, etc.).
- Cancellation signal handling:
  - External `asyncio.CancelledError` clean child task wind-down.
  - `OCRCancelled` and `CircuitOpenError` unhandled re-raise and sibling task cancellation.
- Fallback and error handling on empty or corrupted VLM responses:
  - Empty text, conversational prose, non-array JSON primitives.
  - Malformed or degenerate bounding boxes.
  - Degenerate crops in `ocr_crop` returning empty text without LLM calls.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from omniscribe.core.document import DocumentBlock, DocumentPage, DocumentResult
from omniscribe.core.grounded.models import GroundedResponse
from omniscribe.core.grounded.parsers import (
    _clamp,
    _extract_bbox,
    _normalize_bbox,
    _parse_grounded_json,
    _recover_truncated_json_array,
)
from omniscribe.core.grounded.prompted import (
    CROP_OCR_PROMPT,
    DEFAULT_GROUNDING_PROMPT,
    GROUNDED_OCR_SYSTEM_MESSAGE,
    REPAIR_CROP_PROMPT,
    PromptedGroundedOCR,
)
from omniscribe.core.llm.temperatures import TEMPERATURE_GROUNDED
from omniscribe.core.ocr.resilience import CircuitOpenError
from omniscribe.core.processors.reading_order import ReadingOrderProcessor
from omniscribe.core.workflows.base import OCRCancelled

# ---------------------------------------------------------------------------
# Test Helpers
# ---------------------------------------------------------------------------


def _create_synthetic_page_images(
    count: int = 3,
    width: int = 1000,
    height: int = 1000,
) -> list[tuple[str, int, int]]:
    """Generate fake base64 page images for testing multi-page pipelines."""
    # Tiny 1x1 valid base64 gif/jpeg placeholder
    fake_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    return [(f"{fake_b64}_{idx}", width, height) for idx in range(count)]


# ---------------------------------------------------------------------------
# 1. Prompt Builder Tests
# ---------------------------------------------------------------------------


class TestPromptBuilder:
    """Tests for prompt builder and message construction across model architectures."""

    async def test_standard_model_includes_system_message(self) -> None:
        """Models supporting system role (e.g. Qwen, GPT, Mistral) receive GROUNDED_OCR_SYSTEM_MESSAGE."""
        backend = PromptedGroundedOCR(
            model="qwen/qwen3-vl-8b",
            api_base="http://localhost:1234/v1",
        )

        mock_call_llm = AsyncMock(return_value="[]")
        with patch("omniscribe.core.grounded.prompted.call_llm", mock_call_llm):
            await backend._call_with_retry(image_b64="fake_image_b64")

        mock_call_llm.assert_called_once()
        call_kwargs = mock_call_llm.call_args.kwargs
        assert call_kwargs["system_prompt"] == GROUNDED_OCR_SYSTEM_MESSAGE
        assert call_kwargs["model"] == "qwen/qwen3-vl-8b"
        assert call_kwargs["temperature"] == 0.0

        messages = call_kwargs["messages"]
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        content = messages[0]["content"]
        assert content[0] == {"type": "text", "text": DEFAULT_GROUNDING_PROMPT}
        assert content[1]["type"] == "image_url"
        assert content[1]["image_url"]["url"] == "data:image/jpeg;base64,fake_image_b64"

    @pytest.mark.parametrize(
        "model_name",
        [
            "allenai/olmocr-2-7b",
            "allenai/olmocr-7b-0225-preview",
            "olmocr",
            "OLMOCR-2",
        ],
    )
    async def test_olmocr_models_omit_system_message(self, model_name: str) -> None:
        """OlmOCR family models expect a single user turn; system prompt must be None."""
        backend = PromptedGroundedOCR(
            model=model_name,
            api_base="http://localhost:1234/v1",
        )

        mock_call_llm = AsyncMock(return_value="[]")
        with patch("omniscribe.core.grounded.prompted.call_llm", mock_call_llm):
            await backend._call_with_retry(image_b64="fake_image_b64")

        mock_call_llm.assert_called_once()
        call_kwargs = mock_call_llm.call_args.kwargs
        assert call_kwargs["system_prompt"] is None
        assert call_kwargs["model"] == model_name

    async def test_custom_user_prompt_override_at_init(self) -> None:
        """Custom user prompt passed to constructor is used in user message."""
        custom_prompt = "You are a legal document parser. Output bounding boxes only."
        backend = PromptedGroundedOCR(
            model="qwen/qwen3-vl-8b",
            prompt=custom_prompt,
        )

        mock_call_llm = AsyncMock(return_value="[]")
        with patch("omniscribe.core.grounded.prompted.call_llm", mock_call_llm):
            await backend._call_with_retry(image_b64="img")

        messages = mock_call_llm.call_args.kwargs["messages"]
        assert messages[0]["content"][0]["text"] == custom_prompt

    async def test_call_with_retry_prompt_override_parameter(self) -> None:
        """Dynamic prompt passed directly to _call_with_retry takes precedence over instance prompt."""
        backend = PromptedGroundedOCR(prompt="Default prompt")

        mock_call_llm = AsyncMock(return_value="[]")
        with patch("omniscribe.core.grounded.prompted.call_llm", mock_call_llm):
            await backend._call_with_retry(
                image_b64="img", prompt="Ad-hoc override prompt"
            )

        messages = mock_call_llm.call_args.kwargs["messages"]
        assert messages[0]["content"][0]["text"] == "Ad-hoc override prompt"

    async def test_crop_ocr_uses_crop_prompt(self) -> None:
        """ocr_crop forwards CROP_OCR_PROMPT to _call_with_retry."""
        backend = PromptedGroundedOCR()

        # Mock raster cache with 1 page
        backend._raster_cache[("dummy.pdf", -1, -1, 1024, 150)] = [
            ("page0_b64", 100, 100)
        ]

        with (
            patch.object(
                backend, "_get_page_images", new_callable=AsyncMock
            ) as mock_get_pages,
            patch(
                "omniscribe.core.grounded.prompted._crop_normalized",
                return_value="crop_b64",
            ),
            patch.object(
                backend, "_call_with_retry", new_callable=AsyncMock
            ) as mock_call,
        ):
            mock_get_pages.return_value = [("page0_b64", 100, 100)]
            mock_call.return_value = "Extracted crop text"

            text = await backend.ocr_crop(
                "dummy.pdf", page_index=0, bbox=[0.1, 0.1, 0.5, 0.5]
            )

        assert text == "Extracted crop text"
        mock_call.assert_called_once_with(
            "crop_b64", prompt=CROP_OCR_PROMPT, temperature=TEMPERATURE_GROUNDED
        )

    def test_repair_crop_prompt_includes_previous_text_and_reason(self) -> None:
        assert "previous attempt" in REPAIR_CROP_PROMPT.lower()
        assert "{previous_text}" in REPAIR_CROP_PROMPT
        assert "{rejection_reason}" in REPAIR_CROP_PROMPT

    async def test_ocr_crop_repair_pass_uses_repair_prompt_and_warmer_temp(
        self,
    ) -> None:
        """ocr_crop with previous_text builds REPAIR_CROP_PROMPT and bumps temperature."""
        backend = PromptedGroundedOCR()

        with (
            patch.object(
                backend, "_get_page_images", new_callable=AsyncMock
            ) as mock_get_pages,
            patch(
                "omniscribe.core.grounded.prompted._crop_normalized",
                return_value="crop_b64",
            ),
            patch.object(
                backend, "_call_with_retry", new_callable=AsyncMock
            ) as mock_call,
        ):
            mock_get_pages.return_value = [("page0_b64", 100, 100)]
            mock_call.return_value = "corrected crop text"

            text = await backend.ocr_crop(
                "dummy.pdf",
                page_index=0,
                bbox=[0.1, 0.1, 0.5, 0.5],
                previous_text="garbled attempt",
                attempt=2,
            )

            assert text == "corrected crop text"
            mock_call.assert_called_once()
            kwargs = mock_call.call_args.kwargs
            assert "garbled attempt" in kwargs["prompt"]
            assert "confidence below target or garbled" in kwargs["prompt"]
            # TEMPERATURE_GROUNDED + 0.1 * (attempt - 1), capped at 0.3.
            assert kwargs["temperature"] == pytest.approx(
                min(TEMPERATURE_GROUNDED + 0.1 * (2 - 1), 0.3)
            )

            # First attempt with no previous text keeps the plain crop prompt.
            mock_call.reset_mock()
            await backend.ocr_crop("dummy.pdf", page_index=0, bbox=[0.1, 0.1, 0.5, 0.5])
            assert mock_call.call_args.kwargs["prompt"] == CROP_OCR_PROMPT
            assert mock_call.call_args.kwargs["temperature"] == TEMPERATURE_GROUNDED


# ---------------------------------------------------------------------------
# 2. Multi-Page Chunking & Index Preservation Tests
# ---------------------------------------------------------------------------


class TestMultiPageChunking:
    """Tests for multi-page processing, out-of-order resolution, and page index integrity."""

    async def test_page_index_preserved_with_out_of_order_completions(self) -> None:
        """Results must be deterministically sorted by page index regardless of completion order."""
        backend = PromptedGroundedOCR(concurrency=3)
        pages = _create_synthetic_page_images(count=3, width=800, height=1000)

        # Responses for page 0, 1, 2
        page_responses: dict[str, str] = {
            pages[0][
                0
            ]: '[{"bbox_2d": [0, 0, 100, 50], "content": "Page Zero Header"}]',
            pages[1][0]: '[{"bbox_2d": [0, 50, 100, 100], "content": "Page One Body"}]',
            pages[2][
                0
            ]: '[{"bbox_2d": [0, 100, 100, 150], "content": "Page Two Footer"}]',
        }

        # Page 2 completes first (delay 0.01), then page 0 (delay 0.02), then page 1 (delay 0.04)
        delays: dict[str, float] = {
            pages[0][0]: 0.02,
            pages[1][0]: 0.04,
            pages[2][0]: 0.01,
        }

        async def _mock_call(b64: str, prompt: str | None = None) -> str:
            await asyncio.sleep(delays[b64])
            return page_responses[b64]

        with (
            patch.object(
                backend, "_get_page_images", new_callable=AsyncMock
            ) as mock_imgs,
            patch.object(backend, "_call_with_retry", side_effect=_mock_call),
        ):
            mock_imgs.return_value = pages
            response = await backend.ocr_document("doc.pdf")

        assert isinstance(response, GroundedResponse)
        assert len(response.blocks) == 3
        # Strict page index ordering
        assert response.blocks[0].page_index == 0
        assert response.blocks[0].text == "Page Zero Header"
        assert response.blocks[1].page_index == 1
        assert response.blocks[1].text == "Page One Body"
        assert response.blocks[2].page_index == 2
        assert response.blocks[2].text == "Page Two Footer"
        assert response.page_sizes == [(800, 1000)] * 3
        assert response.failed_pages == []

    async def test_concurrency_semaphore_limits_in_flight_tasks(self) -> None:
        """Concurrency setting must strictly limit concurrent VLM executions."""
        concurrency_limit = 2
        backend = PromptedGroundedOCR(concurrency=concurrency_limit)
        pages = _create_synthetic_page_images(count=4)

        in_flight = 0
        max_in_flight = 0
        lock = asyncio.Lock()

        async def _tracking_call(_b64: str, _prompt: str | None = None) -> str:
            nonlocal in_flight, max_in_flight
            async with lock:
                in_flight += 1
                if in_flight > max_in_flight:
                    max_in_flight = in_flight
            await asyncio.sleep(0.02)
            async with lock:
                in_flight -= 1
            return "[]"

        with (
            patch.object(
                backend, "_get_page_images", new_callable=AsyncMock
            ) as mock_imgs,
            patch.object(backend, "_call_with_retry", side_effect=_tracking_call),
        ):
            mock_imgs.return_value = pages
            await backend.ocr_document("doc.pdf")

        assert max_in_flight <= concurrency_limit

    async def test_per_page_isolation_and_warning_callback(self) -> None:
        """When page 1 fails, surviving pages (0 and 2) must complete, recording page 1 in failed_pages."""
        backend = PromptedGroundedOCR(concurrency=1)
        pages = _create_synthetic_page_images(count=3)

        async def _call_with_failure(b64: str, _prompt: str | None = None) -> str:
            if b64 == pages[1][0]:
                raise RuntimeError("Corrupted page 1 rendering buffer")
            return '[{"bbox_2d": [0, 0, 50, 50], "content": "valid"}]'

        warnings_received: list[tuple[int, BaseException]] = []

        async def _on_warning(page_idx: int, exc: BaseException) -> None:
            warnings_received.append((page_idx, exc))

        with (
            patch.object(
                backend, "_get_page_images", new_callable=AsyncMock
            ) as mock_imgs,
            patch.object(backend, "_call_with_retry", side_effect=_call_with_failure),
        ):
            mock_imgs.return_value = pages
            resp = await backend.ocr_document("test.pdf", on_warning=_on_warning)

        assert resp.failed_pages == [1]
        assert len(resp.blocks) == 2
        assert resp.blocks[0].page_index == 0
        assert resp.blocks[1].page_index == 2
        assert len(warnings_received) == 1
        assert warnings_received[0][0] == 1
        assert "Corrupted page 1 rendering buffer" in str(warnings_received[0][1])

    async def test_progress_callback_ticks_properly(self) -> None:
        """Progress callback should be emitted initially at 0 and after every completed page."""
        backend = PromptedGroundedOCR(concurrency=2)
        pages = _create_synthetic_page_images(count=3)

        ticks: list[tuple[str, int, int]] = []

        async def _progress(stage: str, current: int, total: int, _msg: str) -> None:
            ticks.append((stage, current, total))

        with (
            patch.object(
                backend, "_get_page_images", new_callable=AsyncMock
            ) as mock_imgs,
            patch.object(
                backend, "_call_with_retry", new_callable=AsyncMock, return_value="[]"
            ),
        ):
            mock_imgs.return_value = pages
            await backend.ocr_document("doc.pdf", progress=_progress)

        assert len(ticks) == 4
        assert ticks[0] == ("ocr", 0, 3)
        assert ticks[1] == ("ocr", 1, 3)
        assert ticks[2] == ("ocr", 2, 3)
        assert ticks[3] == ("ocr", 3, 3)


# ---------------------------------------------------------------------------
# 3. Coordinate Parsing & Normalization Tests
# ---------------------------------------------------------------------------


class TestCoordinateParsingAndNormalization:
    """Tests for bbox extraction, clamping, aliases, and natural reading order."""

    def test_clamp_helper(self) -> None:
        """Coordinate clamping ensures values land strictly within [0.0, 1.0]."""
        assert _clamp(-0.5) == 0.0
        assert _clamp(0.0) == 0.0
        assert _clamp(0.55) == 0.55
        assert _clamp(1.0) == 1.0
        assert _clamp(1.5) == 1.0

    def test_extract_bbox_supported_aliases(self) -> None:
        """First matching bbox key alias from _BBOX_KEYS is extracted."""
        for key in ("bbox_2d", "bbox", "box_2d", "box", "bounding_box", "coordinates"):
            item = {key: [10, 20, 30, 40], "other": 123}
            extracted = _extract_bbox(item)
            assert extracted == [10, 20, 30, 40]

    def test_extract_bbox_rejects_non_4_tuples(self) -> None:
        assert _extract_bbox({"bbox_2d": [10, 20, 30]}) is None
        assert _extract_bbox({"bbox_2d": [10, 20, 30, 40, 50]}) is None
        assert _extract_bbox({"bbox_2d": "not a list"}) is None
        assert _extract_bbox({"unrelated": [10, 20, 30, 40]}) is None

    def test_normalize_bbox_pixel_xyxy(self) -> None:
        """Pixel coordinates are converted to normalized 0..1 floating point bounds."""
        norm = _normalize_bbox([100, 200, 500, 600], img_w=1000, img_h=1000)
        assert norm == (0.1, 0.2, 0.5, 0.6)

    def test_normalize_bbox_clamps_boundary_pixels(self) -> None:
        """Pixel coordinates extending slightly beyond bounds (e.g. w+1) clamp to 1.0."""
        norm = _normalize_bbox([0, 0, 1001, 1001], img_w=1000, img_h=1000)
        assert norm == (0.0, 0.0, 1.0, 1.0)

    def test_normalize_bbox_already_normalized_passthrough(self) -> None:
        """If bounding box is already in [0, 1], it is preserved without re-dividing."""
        norm = _normalize_bbox([0.15, 0.25, 0.75, 0.85], img_w=2048, img_h=2048)
        assert norm == (0.15, 0.25, 0.75, 0.85)

    def test_normalize_bbox_xywh_fallback(self) -> None:
        """When XYXY would exceed boundaries, XYWH interpretation is applied."""
        # [x0=200, y0=300, w=400, h=250] -> x1=600, y1=550
        norm = _normalize_bbox([200, 300, 400, 250], img_w=1000, img_h=1000)
        assert norm == pytest.approx((0.2, 0.3, 0.6, 0.55))

    def test_normalize_bbox_rejects_negative_or_inverted_boxes(self) -> None:
        # Negative coordinate
        assert _normalize_bbox([-10, 0, 50, 50], 100, 100) is None
        # Inverted normalized coordinate (x1 <= x0)
        assert _normalize_bbox([0.6, 0.1, 0.2, 0.5], 100, 100) is None
        # Inverted normalized coordinate (y1 <= y0)
        assert _normalize_bbox([0.1, 0.7, 0.5, 0.3], 100, 100) is None
        # Zero-width pixel box (x1 <= 0)
        assert _normalize_bbox([10, 10, 0, 50], 100, 100) is None
        # Zero-height pixel box (y1 <= 0)
        assert _normalize_bbox([10, 10, 50, 0], 100, 100) is None
        # Inverted pixel box that cannot be rescued as XYWH because it exceeds image boundary
        assert _normalize_bbox([70, 0, 50, 50], 100, 100) is None
        # Non-numeric
        assert _normalize_bbox(["bad", 0, 50, 50], 100, 100) is None  # type: ignore[list-item]

    async def test_sorting_boxes_into_natural_reading_order(self) -> None:
        """Verify that parsed grounded blocks can be organized into natural row-major reading order."""
        # Multi-element scrambled layout on a single page:
        # 1. Header (y=0.05, x=0.1)
        # 2. Left column paragraph 1 (y=0.20, x=0.1)
        # 3. Right column paragraph 1 (y=0.20, x=0.6)
        # 4. Left column paragraph 2 (y=0.30, x=0.1)
        # 5. Footer (y=0.90, x=0.4)
        blocks = [
            DocumentBlock(
                bbox=(0.1, 0.30, 0.45, 0.38), text="Left Line 2", kind="text"
            ),
            DocumentBlock(bbox=(0.4, 0.90, 0.60, 0.95), text="Footer", kind="text"),
            DocumentBlock(
                bbox=(0.6, 0.20, 0.95, 0.28), text="Right Line 1", kind="text"
            ),
            DocumentBlock(
                bbox=(0.1, 0.05, 0.90, 0.12), text="Document Title", kind="text"
            ),
            DocumentBlock(
                bbox=(0.1, 0.20, 0.45, 0.28), text="Left Line 1", kind="text"
            ),
        ]

        doc = DocumentResult(pages=[DocumentPage(page_index=0, blocks=blocks)])
        processor = ReadingOrderProcessor(row_tolerance=0.04)
        ordered_doc = await processor.process(doc)

        ordered_texts = [b.text for b in ordered_doc.pages[0].blocks]
        expected_order = [
            "Document Title",
            "Left Line 1",
            "Right Line 1",
            "Left Line 2",
            "Footer",
        ]
        assert ordered_texts == expected_order
        assert [b.reading_order for b in ordered_doc.pages[0].blocks] == [0, 1, 2, 3, 4]


# ---------------------------------------------------------------------------
# 4. Robust JSON Repair Loop Tests
# ---------------------------------------------------------------------------


class TestRobustJSONRepairLoop:
    """Tests for extracting, cleaning, and repairing malformed or truncated JSON from VLMs."""

    @pytest.mark.parametrize(
        "fence_wrapper",
        [
            "```json\n{payload}\n```",
            "```JSON\n{payload}\n```",
            "```\n{payload}\n```",
            "   ```json   \n{payload}\n   ```   ",
        ],
    )
    def test_strip_markdown_code_block_fences(self, fence_wrapper: str) -> None:
        """Markdown code blocks enclosing JSON arrays must be stripped cleanly."""
        inner = '[{"bbox_2d": [0, 0, 100, 100], "content": "fenced block"}]'
        raw = fence_wrapper.format(payload=inner)
        blocks = _parse_grounded_json(raw, page_idx=0, img_w=100, img_h=100)
        assert len(blocks) == 1
        assert blocks[0].text == "fenced block"

    def test_unclosed_fence_due_to_truncation_is_handled(self) -> None:
        """If response starts with ```json but the closing ``` was truncated, it is stripped defensively."""
        raw = '```json\n[{"bbox_2d": [0, 0, 10, 10], "content": "unclosed"}]'
        blocks = _parse_grounded_json(raw, page_idx=0, img_w=100, img_h=100)
        assert len(blocks) == 1
        assert blocks[0].text == "unclosed"

    def test_recover_truncated_json_array_mid_element(self) -> None:
        """Truncation mid-element extracts all previously completed elements."""
        raw = (
            "[\n"
            '  {"bbox_2d": [10, 10, 50, 30], "content": "Line one"},\n'
            '  {"bbox_2d": [10, 40, 50, 60], "content": "Line two"},\n'
            '  {"bbox_2d": [10, 70, 50, 90], "content": "Incomplete line'
        )
        blocks = _parse_grounded_json(raw, page_idx=0, img_w=100, img_h=100)
        assert len(blocks) == 2
        assert blocks[0].text == "Line one"
        assert blocks[1].text == "Line two"

    def test_recover_truncated_json_array_direct_helper(self) -> None:
        raw_cut = '[{"a": 1}, {"b": 2}, {"c":'
        recovered = _recover_truncated_json_array(raw_cut)
        assert recovered == [{"a": 1}, {"b": 2}]

        # No complete object
        assert _recover_truncated_json_array("no json here") is None

    def test_preamble_and_postamble_prose_tolerated(self) -> None:
        """Chatty VLMs producing conversational text around the JSON array are handled."""
        raw = (
            "Here is the requested line segmentation for the provided document image:\n\n"
            '[{"bbox_2d": [20, 20, 80, 40], "content": "Actual Document Text"}]\n\n'
            "I hope this helps your extraction pipeline!"
        )
        blocks = _parse_grounded_json(raw, page_idx=0, img_w=100, img_h=100)
        assert len(blocks) == 1
        assert blocks[0].text == "Actual Document Text"

    @pytest.mark.parametrize(
        "wrapper_key",
        ["results", "blocks", "layout", "layout_details", "items"],
    )
    def test_nested_dictionary_wrapper_recovery(self, wrapper_key: str) -> None:
        """Models wrapping the array inside an outer object (e.g. {'results': [...]}) are unpacked."""
        raw = (
            f'{{"{wrapper_key}": [{{"bbox_2d": [0, 0, 50, 50], "content": "nested"}}]}}'
        )
        blocks = _parse_grounded_json(raw, page_idx=1, img_w=100, img_h=100)
        assert len(blocks) == 1
        assert blocks[0].page_index == 1
        assert blocks[0].text == "nested"

    def test_single_dictionary_object_treated_as_one_element_list(self) -> None:
        """A single JSON object output instead of a list is treated as a 1-element list."""
        raw = '{"bbox_2d": [0, 0, 50, 50], "content": "single item"}'
        blocks = _parse_grounded_json(raw, page_idx=0, img_w=100, img_h=100)
        assert len(blocks) == 1
        assert blocks[0].text == "single item"


# ---------------------------------------------------------------------------
# 5. Cancellation Signal Handling Tests
# ---------------------------------------------------------------------------


class TestCancellationHandling:
    """Tests for cancellation propagation and clean task wind-down."""

    async def test_asyncio_cancelled_error_aborts_and_cleans_tasks(self) -> None:
        """Cancelling the ocr_document task cancels all in-flight page workers."""
        backend = PromptedGroundedOCR(concurrency=3)
        pages = _create_synthetic_page_images(count=3)

        cancelled_pages: list[str] = []

        async def _hanging_call(b64: str, _prompt: str | None = None) -> str:
            try:
                await asyncio.sleep(10.0)
            except asyncio.CancelledError:
                cancelled_pages.append(b64)
                raise
            return "[]"

        with (
            patch.object(
                backend, "_get_page_images", new_callable=AsyncMock
            ) as mock_imgs,
            patch.object(backend, "_call_with_retry", side_effect=_hanging_call),
        ):
            mock_imgs.return_value = pages
            doc_task = asyncio.create_task(backend.ocr_document("doc.pdf"))
            # Let workers start
            await asyncio.sleep(0.01)
            # Cancel the parent task
            doc_task.cancel()

            with pytest.raises(asyncio.CancelledError):
                await doc_task

        assert len(cancelled_pages) == 3

    async def test_ocr_cancelled_exception_bubbles_immediately(self) -> None:
        """Domain OCRCancelled exception bypasses page-level catch and aborts the document."""
        backend = PromptedGroundedOCR(concurrency=2)
        pages = _create_synthetic_page_images(count=2)

        async def _raise_cancelled(b64: str, _prompt: str | None = None) -> str:
            if b64 == pages[0][0]:
                raise OCRCancelled("Pipeline cancelled by operator")
            await asyncio.sleep(5.0)
            return "[]"

        with (
            patch.object(
                backend, "_get_page_images", new_callable=AsyncMock
            ) as mock_imgs,
            patch.object(backend, "_call_with_retry", side_effect=_raise_cancelled),
        ):
            mock_imgs.return_value = pages
            with pytest.raises(OCRCancelled, match="Pipeline cancelled by operator"):
                await backend.ocr_document("doc.pdf")

    async def test_circuit_open_error_bubbles_and_cancels_siblings(self) -> None:
        """CircuitOpenError fails fast without waiting for remaining pages."""
        backend = PromptedGroundedOCR(concurrency=2)
        pages = _create_synthetic_page_images(count=2)

        sibling_cancelled = False

        async def _failing_call(b64: str, _prompt: str | None = None) -> str:
            nonlocal sibling_cancelled
            if b64 == pages[0][0]:
                raise CircuitOpenError(3, 10.0)
            try:
                await asyncio.sleep(10.0)
            except asyncio.CancelledError:
                sibling_cancelled = True
                raise
            return "[]"

        with (
            patch.object(
                backend, "_get_page_images", new_callable=AsyncMock
            ) as mock_imgs,
            patch.object(backend, "_call_with_retry", side_effect=_failing_call),
        ):
            mock_imgs.return_value = pages
            with pytest.raises(CircuitOpenError):
                await backend.ocr_document("doc.pdf")

        assert sibling_cancelled is True


# ---------------------------------------------------------------------------
# 6. Corrupted & Empty Response Handling Tests
# ---------------------------------------------------------------------------


class TestCorruptedAndEmptyResponses:
    """Tests for edge cases with empty, non-JSON, and malformed VLM outputs."""

    def test_empty_string_returns_empty_list(self) -> None:
        assert _parse_grounded_json("", page_idx=0, img_w=100, img_h=100) == []
        assert (
            _parse_grounded_json("    \n\t  ", page_idx=0, img_w=100, img_h=100) == []
        )

    def test_conversational_refusal_returns_empty_list_and_logs(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        raw = "I apologize, but there is no legible text visible in this document."
        with caplog.at_level("WARNING"):
            blocks = _parse_grounded_json(raw, page_idx=3, img_w=100, img_h=100)

        assert blocks == []
        assert "Grounded bbox JSON parsing failed on page 3" in caplog.text

    @pytest.mark.parametrize(
        "non_collection_json", ["null", "12345", "true", '"plain string"']
    )
    def test_non_collection_json_primitives_return_empty_list(
        self, non_collection_json: str
    ) -> None:
        blocks = _parse_grounded_json(
            non_collection_json, page_idx=0, img_w=100, img_h=100
        )
        assert blocks == []

    def test_malformed_and_degenerate_bounding_boxes_dropped(self) -> None:
        """Individual invalid bounding boxes are dropped while valid items on the same page survive."""
        raw = (
            "[\n"
            '  {"bbox_2d": [0, 0, 50, 50], "content": "Valid line 1"},\n'
            '  {"bbox_2d": [70, 0, 50, 50], "content": "Invalid x-inverted"},\n'
            '  {"bbox_2d": [0, 70, 50, 50], "content": "Invalid y-inverted"},\n'
            '  {"bbox_2d": [10, 10, 0, 50], "content": "Invalid zero-width"},\n'
            '  {"bbox_2d": [-5, 0, 50, 50], "content": "Invalid negative"},\n'
            '  {"bbox_2d": [0, 0, 50, 50], "content": "   "},\n'  # empty content
            '  {"content": "Missing bbox entirely"},\n'
            '  {"bbox_2d": "not a list", "content": "Wrong bbox type"},\n'
            '  {"bbox_2d": [50, 50, 100, 100], "content": "Valid line 2"}\n'
            "]"
        )
        blocks = _parse_grounded_json(raw, page_idx=0, img_w=100, img_h=100)
        assert len(blocks) == 2
        assert blocks[0].text == "Valid line 1"
        assert blocks[1].text == "Valid line 2"

    async def test_ocr_crop_degenerate_box_returns_empty_immediately(self) -> None:
        """Degenerate crop coordinates (x0 >= x1 or y0 >= y1) return empty without invoking VLM."""
        backend = PromptedGroundedOCR()
        backend._raster_cache[("sample.pdf", -1, -1, 1024, 150)] = [
            ("fake_b64", 100, 100)
        ]

        with (
            patch.object(
                backend, "_get_page_images", new_callable=AsyncMock
            ) as mock_get_imgs,
            patch.object(
                backend, "_call_with_retry", new_callable=AsyncMock
            ) as mock_call,
        ):
            mock_get_imgs.return_value = [("fake_b64", 100, 100)]
            # Bbox with zero area: x0=0.5, y0=0.5, x1=0.5, y1=0.5
            res = await backend.ocr_crop(
                "sample.pdf", page_index=0, bbox=[0.5, 0.5, 0.5, 0.5]
            )

        assert res == ""
        mock_call.assert_not_called()

    async def test_ocr_crop_invalid_page_index_raises_value_error(self) -> None:
        """Requesting ocr_crop on out-of-range page index raises ValueError."""
        backend = PromptedGroundedOCR()
        with patch.object(
            backend, "_get_page_images", new_callable=AsyncMock
        ) as mock_get_imgs:
            mock_get_imgs.return_value = [("img0", 100, 100)]
            with pytest.raises(ValueError, match="page_index 5 out of range"):
                await backend.ocr_crop(
                    "sample.pdf", page_index=5, bbox=[0.1, 0.1, 0.5, 0.5]
                )
