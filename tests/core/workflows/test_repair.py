"""Unit tests for the QualityRepairLoop in core/workflows/repair.py."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from omniscribe.core.callbacks import BlockCallbackSet
from omniscribe.core.ocr.resilience import CircuitOpenError
from omniscribe.core.workflows.repair import (
    PageRepairSummary,
    QualityRepairLoop,
    RepairOptions,
    emit_job_repair_summary,
)


class TestQualityRepairLoop:
    async def test_disabled_loop_returns_early(self) -> None:
        loop = QualityRepairLoop(options=RepairOptions(enabled=False, target=0.9))
        page_blocks = [((0.0, 0.0, 1.0, 1.0), "Low conf text")]
        re_ocr = AsyncMock()

        summary = await loop.repair_page(
            page_idx=0,
            page_blocks=page_blocks,
            re_ocr=re_ocr,
        )

        assert isinstance(summary, PageRepairSummary)
        assert summary.repaired_count == 0
        re_ocr.assert_not_called()

    async def test_high_confidence_blocks_skip_re_ocr(self) -> None:
        loop = QualityRepairLoop(
            options=RepairOptions(enabled=True, target=0.8),
            confidence_estimator=lambda text: 0.95,
        )
        page_blocks = [((0.0, 0.0, 1.0, 1.0), "High confidence clean text")]
        re_ocr = AsyncMock()

        summary = await loop.repair_page(
            page_idx=0,
            page_blocks=page_blocks,
            re_ocr=re_ocr,
        )

        assert summary.block_count == 1
        assert summary.repaired_count == 0
        assert summary.below_target_count == 0
        re_ocr.assert_not_called()

    async def test_low_confidence_repaired_successfully(self) -> None:
        def fake_estimator(text: str) -> float:
            if "Clean" in text:
                return 0.95
            return 0.4

        loop = QualityRepairLoop(
            options=RepairOptions(enabled=True, target=0.85, max_retries=2),
            confidence_estimator=fake_estimator,
        )
        page_blocks = [((0.0, 0.0, 1.0, 1.0), "Mangled OCR text")]
        re_ocr = AsyncMock(return_value="Clean and repaired text")
        on_retry = AsyncMock()
        on_revised = AsyncMock()

        summary = await loop.repair_page(
            page_idx=0,
            page_blocks=page_blocks,
            re_ocr=re_ocr,
            on_block_retry=on_retry,
            on_block_revised=on_revised,
        )

        assert summary.repaired_count == 1
        assert summary.below_target_count == 0
        assert page_blocks[0][1] == "Clean and repaired text"
        on_retry.assert_called_once()
        on_revised.assert_called_once()

    async def test_stall_guard_stops_retry_on_non_improving_result(self) -> None:
        def fake_estimator(text: str) -> float:
            return 0.5  # constant low score

        loop = QualityRepairLoop(
            options=RepairOptions(enabled=True, target=0.9, max_retries=3),
            confidence_estimator=fake_estimator,
        )
        page_blocks = [((0.0, 0.0, 1.0, 1.0), "Initial text")]
        re_ocr = AsyncMock(return_value="Still same quality text")

        summary = await loop.repair_page(
            page_idx=0,
            page_blocks=page_blocks,
            re_ocr=re_ocr,
        )

        assert summary.repaired_count == 0
        assert summary.below_target_count == 1
        # Stall guard triggers on attempt 1 because new_conf (0.5) <= conf (0.5)
        assert re_ocr.call_count == 1

    async def test_circuit_open_error_propagates(self) -> None:
        loop = QualityRepairLoop(
            options=RepairOptions(enabled=True, target=0.9),
            confidence_estimator=lambda text: 0.3,
        )
        page_blocks = [((0.0, 0.0, 1.0, 1.0), "Text needing repair")]
        re_ocr = AsyncMock(
            side_effect=CircuitOpenError("Circuit open for endpoint", retry_after=30.0)  # type: ignore[arg-type]
        )

        with pytest.raises(CircuitOpenError):
            await loop.repair_page(
                page_idx=0,
                page_blocks=page_blocks,
                re_ocr=re_ocr,
            )

    async def test_estimator_none_coerced_to_zero(self) -> None:
        loop = QualityRepairLoop(
            options=RepairOptions(enabled=True, target=0.8),
            confidence_estimator=lambda text: None,  # returns None
        )
        page_blocks = [((0.0, 0.0, 1.0, 1.0), "Unknown score text")]
        re_ocr = AsyncMock(return_value="Still unknown")

        summary = await loop.repair_page(
            page_idx=0,
            page_blocks=page_blocks,
            re_ocr=re_ocr,
        )

        assert summary.block_count == 1
        assert summary.below_target_count == 1

    async def test_repair_passes_previous_text_and_attempt(self) -> None:
        """re_ocr receives the block's current text and the attempt number."""
        conf_by_text = {"Mangled OCR text": 0.4, "Better but imperfect text": 0.5}

        def fake_estimator(text: str) -> float:
            return conf_by_text.get(text, 0.95)

        loop = QualityRepairLoop(
            options=RepairOptions(enabled=True, target=0.9, max_retries=2),
            confidence_estimator=fake_estimator,
        )
        page_blocks = [((0.0, 0.0, 1.0, 1.0), "Mangled OCR text")]
        re_ocr = AsyncMock(
            side_effect=["Better but imperfect text", "Perfectly clean final text"]
        )

        await loop.repair_page(page_idx=0, page_blocks=page_blocks, re_ocr=re_ocr)

        assert re_ocr.call_count == 2
        first, second = re_ocr.call_args_list
        assert first.args == (0, (0.0, 0.0, 1.0, 1.0))
        assert first.kwargs["previous_text"] == "Mangled OCR text"
        assert first.kwargs["attempt"] == 1
        # The accepted attempt-1 revision becomes the previous text of attempt 2.
        assert second.kwargs["previous_text"] == "Better but imperfect text"
        assert second.kwargs["attempt"] == 2


class TestJobRepairSummary:
    async def test_emit_job_repair_summary_aggregates_properly(self) -> None:
        on_summary = AsyncMock()
        callbacks = BlockCallbackSet(on_quality_summary=on_summary)

        summaries = [
            PageRepairSummary(
                page_idx=0,
                target=0.85,
                block_count=2,
                avg_confidence=0.8,
                repaired_count=1,
                below_target_count=0,
            ),
            PageRepairSummary(
                page_idx=1,
                target=0.85,
                block_count=4,
                avg_confidence=0.9,
                repaired_count=2,
                below_target_count=1,
            ),
        ]

        await emit_job_repair_summary(callbacks, summaries)

        on_summary.assert_called_once()
        args = on_summary.call_args[0]
        assert args[0] == "job"
        assert args[1] is None
        assert args[2] == 0.85
        # Weighted avg: (0.8*2 + 0.9*4) / 6 = (1.6 + 3.6)/6 = 5.2/6 ≈ 0.8666
        assert pytest.approx(args[3], 0.001) == 0.8666
        assert args[4] == 3  # repaired total: 1 + 2
        assert args[5] == 1  # below target total: 0 + 1


class TestRepairPhasePageDecode:
    async def test_run_repair_phase_skips_decoding_pages_without_targets(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Perf: pages with no below-target blocks must not have their
        image decoded. The repair phase used to decode every page before
        checking whether the page had any repair targets, costing a full
        decode per clean page (~295 wasted decodes on a 300-page doc with
        5 bad blocks)."""
        from PIL import Image

        from omniscribe.core.workflows.hybrid_repair import run_repair_phase

        decode_calls: list[object] = []

        def fake_decode(image_b64: object) -> Image.Image:
            decode_calls.append(image_b64)
            # Noisy texture: crop_for_ocr_from_image returns None for
            # mostly-uniform regions, which would skip the re-OCR call.
            return Image.effect_noise((64, 64), 64).convert("RGB")

        monkeypatch.setattr(
            "omniscribe.core.workflows.hybrid_repair._decode_page_image",
            fake_decode,
        )

        class _StubOCR:
            async def perform_ocr_on_crop(self, crop_b64: str, **kwargs: object) -> str:
                return "abc"

        class _StubEngine:
            ocr_processor = _StubOCR()
            block_callbacks = BlockCallbackSet()

        summaries = await run_repair_phase(
            engine=_StubEngine(),
            pages_structured={
                0: [((0.0, 0.0, 1.0, 1.0), "ab")],  # est. 0.4 -> below target 0.5
                1: [((0.0, 0.0, 1.0, 1.0), "abc")],  # est. 0.7 -> above target
            },
            images_dict={0: "img-0", 1: "img-1"},
            page_nums=[0, 1],
            repair_options=RepairOptions(enabled=True, target=0.5, max_retries=2),
            concurrency=1,
            progress=None,
            on_warning=None,
            decoded_get=lambda p_num: None,
        )

        assert decode_calls == ["img-0"]
        assert len(summaries) == 2
        assert summaries[0].repaired_count == 1
        assert summaries[1].repaired_count == 0
        assert summaries[1].below_target_count == 0
