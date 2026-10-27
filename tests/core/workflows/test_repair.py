"""Unit tests for the QualityRepairLoop in core/workflows/repair.py."""

from __future__ import annotations

import asyncio
from typing import cast
from unittest.mock import AsyncMock

import pytest

from omniscribe.core.callbacks import BlockCallbackSet
from omniscribe.core.ocr.processor import OCRProcessor
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


class TestTextLayerAgreementRepair:
    def test_token_agreement_flags_foreign_text(self) -> None:
        from omniscribe.core.recall.text_layer import token_agreement

        assert (
            token_agreement(
                "The committee unanimously approved the budget",
                "Der Haushalt wurde einstimmig genehmigt",
            )
            < 0.2
        )

    def test_token_agreement_passes_real_text(self) -> None:
        from omniscribe.core.recall.text_layer import token_agreement

        layer = "The committee unanimously approved the budget after long debate."
        assert token_agreement(layer, layer) > 0.8

    def test_token_agreement_no_evidence_is_neutral(self) -> None:
        from omniscribe.core.recall.text_layer import token_agreement

        assert token_agreement("", "layer text") == 1.0
        assert token_agreement("ocr text", "") == 1.0

    async def test_repair_page_flags_fluent_hallucination_with_layer(self) -> None:
        """A well-formed block (est 0.99, normally exempt) whose text does
        not appear in the PDF text layer becomes a repair target."""
        loop = QualityRepairLoop(
            options=RepairOptions(enabled=True, target=0.9, max_retries=1)
        )
        page_blocks = [
            (
                (0.0, 0.0, 1.0, 1.0),
                "The committee unanimously approved the budget",
            )
        ]
        re_ocr = AsyncMock(return_value="Der Haushalt wurde einstimmig genehmigt")

        await loop.repair_page(
            page_idx=0,
            page_blocks=page_blocks,
            re_ocr=re_ocr,
            layer_text="Der Haushalt wurde einstimmig genehmigt",
        )

        re_ocr.assert_called_once()

    async def test_repair_page_skips_when_layer_agrees(self) -> None:
        loop = QualityRepairLoop(
            options=RepairOptions(enabled=True, target=0.9, max_retries=1)
        )
        text = "The committee unanimously approved the budget"
        page_blocks = [((0.0, 0.0, 1.0, 1.0), text)]
        re_ocr = AsyncMock()

        await loop.repair_page(
            page_idx=0,
            page_blocks=page_blocks,
            re_ocr=re_ocr,
            layer_text=text,
        )

        re_ocr.assert_not_called()

    async def test_run_repair_phase_flows_text_layers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from PIL import Image

        from omniscribe.core.workflows.hybrid_repair import run_repair_phase

        monkeypatch.setattr(
            "omniscribe.core.workflows.hybrid_repair.decode_base64_image",
            lambda image_b64, mode=None: Image.effect_noise((64, 64), 64).convert(
                "RGB"
            ),
        )

        class _StubOCR:
            def __init__(self) -> None:
                self.calls = 0

            async def perform_ocr_on_crop(self, crop_b64: str, **kwargs: object) -> str:
                self.calls += 1
                return "repaired text"

        stub_ocr = _StubOCR()

        class _StubEngine:
            ocr_processor: OCRProcessor = cast(OCRProcessor, stub_ocr)
            block_callbacks = BlockCallbackSet()

        summaries = await run_repair_phase(
            engine=_StubEngine(),
            pages_structured={
                0: [
                    (
                        (0.0, 0.0, 1.0, 1.0),
                        "The committee unanimously approved the budget",
                    )
                ],
            },
            images_dict={0: "img-0"},
            page_nums=[0],
            repair_options=RepairOptions(enabled=True, target=0.9, max_retries=1),
            concurrency=1,
            progress=None,
            on_warning=None,
            decoded_get=lambda p_num: None,
            text_layers={0: "Der Haushalt wurde einstimmig genehmigt"},
        )

        assert summaries[0].block_count == 1
        # The well-formed block was re-OCRed because it disagreed with the
        # text layer (its own estimate can't rise above 0.99, so the stall
        # guard keeps the original text — the trigger fired, which is what
        # this task tests).
        assert stub_ocr.calls == 1


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

        def fake_decode(image_b64: object, mode: str | None = None) -> Image.Image:
            decode_calls.append(image_b64)
            # Noisy texture: crop_for_ocr_from_image returns None for
            # mostly-uniform regions, which would skip the re-OCR call.
            return Image.effect_noise((64, 64), 64).convert("RGB")

        monkeypatch.setattr(
            "omniscribe.core.workflows.hybrid_repair.decode_base64_image",
            fake_decode,
        )

        class _StubOCR:
            async def perform_ocr_on_crop(self, crop_b64: str, **kwargs: object) -> str:
                return "abc"

        class _StubEngine:
            ocr_processor: OCRProcessor = cast(OCRProcessor, _StubOCR())
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


#: Deadlock guard for the concurrency tests below. It bounds how long a
#: test waits for a latch it expects to be released; it never establishes
#: the behaviour under test. Generous on purpose so a loaded CI box does
#: not turn a scheduling delay into a failure.
_GUARD_TIMEOUT = 30.0


async def _drain_ready_tasks() -> None:
    """Let every ready task take one step, without using a timer.

    ``sleep(0)`` is a bare event-loop yield: it has no duration and so is
    unaffected by machine load. Used to give a wrongly-admitted task the
    chance to register before a test asserts on a peak in-flight count.
    """
    for _ in range(5):
        await asyncio.sleep(0)


class TestRepairPhaseConcurrency:
    """Phase 4b fans out across pages, bounded by ``concurrency``.

    The driver used to walk ``page_nums`` one page at a time, so repair
    wall-clock was the SUM of every page's VLM latency even though the
    phase is dominated by network-bound re-OCR calls that sit idle
    waiting on a response. These tests pin the bounded-concurrency
    contract that replaced it: real overlap above 1, strict
    serialisation at 1, page-ordered summaries no matter which page
    finishes first, and a progress counter that cannot interleave.

    Every precondition here is established with ``asyncio.Event`` latches
    rather than with sleeps. An earlier version of these tests inferred
    overlap and completion order from ``asyncio.sleep`` durations, which
    made them pass in isolation and fail under parallel suite load.
    ``_GUARD_TIMEOUT`` is a deadlock guard only — it never establishes the
    behaviour under test — and ``sleep(0)`` is a duration-free yield.
    """

    async def test_pages_overlap_when_concurrency_above_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``concurrency=3`` admits three pages at once, and never a fourth.

        Overlap is forced by a latch: every admitted page parks on
        ``release`` and the test withholds it until three pages are
        provably inside at the same time. Because the parked pages hold
        their permits, a fourth would have registered its arrival by the
        time the loop is drained — so the peak asserted here fails both
        for "never overlapped" and for "bound removed".
        """
        from omniscribe.core.workflows import hybrid_repair as hr
        from omniscribe.core.workflows.hybrid_repair import run_repair_phase

        bound = 3
        inflight = 0
        peak = 0
        saturated = asyncio.Event()
        release = asyncio.Event()

        async def _latched_repair(*, p_num: int, **kwargs: object) -> PageRepairSummary:
            nonlocal inflight, peak
            inflight += 1
            peak = max(peak, inflight)
            if inflight >= bound:
                saturated.set()
            try:
                await release.wait()
            finally:
                inflight -= 1
            return PageRepairSummary(
                page_idx=p_num,
                target=0.9,
                block_count=1,
                avg_confidence=0.4,
                repaired_count=0,
                below_target_count=1,
            )

        monkeypatch.setattr(hr, "repair_single_page", _latched_repair)

        class _StubEngine:
            ocr_processor: OCRProcessor = cast(OCRProcessor, object())
            block_callbacks = BlockCallbackSet()

        pages = list(range(6))
        task = asyncio.create_task(
            run_repair_phase(
                engine=_StubEngine(),
                # "ab" estimates ~0.4, below the 0.5 target, so every page
                # is a repair target and the driver does not early-return.
                pages_structured={p: [((0.0, 0.0, 1.0, 1.0), "ab")] for p in pages},
                images_dict={p: f"img-{p}" for p in pages},
                page_nums=pages,
                repair_options=RepairOptions(enabled=True, target=0.5, max_retries=1),
                concurrency=bound,
                progress=None,
                on_warning=None,
                decoded_get=lambda p_num: None,
            )
        )

        await asyncio.wait_for(saturated.wait(), timeout=_GUARD_TIMEOUT)
        await _drain_ready_tasks()
        held = peak
        release.set()
        summaries = await asyncio.wait_for(task, timeout=_GUARD_TIMEOUT)

        assert held == bound, (
            f"expected exactly {bound} pages in flight at once, got {held}"
        )
        assert len(summaries) == 6

    async def test_concurrency_one_stays_serial(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``concurrency=1`` keeps the old one-page-at-a-time behaviour.

        The first page parks on ``release`` while the loop is drained, so
        ``peak == 1`` is observed with every later page already created
        and waiting — no timing assumption is involved. ``admitted``
        follows ``page_nums`` because ``asyncio.Semaphore`` releases its
        waiters FIFO, a property of the primitive rather than of
        scheduling luck.
        """
        from omniscribe.core.workflows import hybrid_repair as hr
        from omniscribe.core.workflows.hybrid_repair import run_repair_phase

        inflight = 0
        peak = 0
        admitted: list[int] = []
        entered = asyncio.Event()
        release = asyncio.Event()

        async def _latched_repair(*, p_num: int, **kwargs: object) -> PageRepairSummary:
            nonlocal inflight, peak
            admitted.append(p_num)
            inflight += 1
            peak = max(peak, inflight)
            entered.set()
            try:
                await release.wait()
            finally:
                inflight -= 1
            return PageRepairSummary(
                page_idx=p_num,
                target=0.9,
                block_count=1,
                avg_confidence=0.4,
                repaired_count=0,
                below_target_count=1,
            )

        monkeypatch.setattr(hr, "repair_single_page", _latched_repair)

        class _StubEngine:
            ocr_processor: OCRProcessor = cast(OCRProcessor, object())
            block_callbacks = BlockCallbackSet()

        pages = list(range(4))
        task = asyncio.create_task(
            run_repair_phase(
                engine=_StubEngine(),
                pages_structured={p: [((0.0, 0.0, 1.0, 1.0), "ab")] for p in pages},
                images_dict={p: f"img-{p}" for p in pages},
                page_nums=pages,
                repair_options=RepairOptions(enabled=True, target=0.5, max_retries=1),
                concurrency=1,
                progress=None,
                on_warning=None,
                decoded_get=lambda p_num: None,
            )
        )

        await asyncio.wait_for(entered.wait(), timeout=_GUARD_TIMEOUT)
        await _drain_ready_tasks()
        held = peak
        release.set()
        summaries = await asyncio.wait_for(task, timeout=_GUARD_TIMEOUT)

        assert held == 1, f"expected exactly one page in flight, got {held}"
        assert admitted == pages, "expected pages to be admitted in page_nums order"
        assert [s.page_idx for s in summaries] == pages

    async def test_summaries_keep_page_order_when_pages_finish_out_of_order(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Summaries stay positional to ``page_nums`` under concurrency.

        Completion order is forced, not inferred: page 1 parks on
        ``others_finished`` and is released only once pages 0, 2 and 3
        have all returned, so it provably completes last. No wall-clock
        sleep is involved and the reordering therefore holds on a loaded
        machine. The observed completion order is asserted *before* the
        summary order, so the test fails loudly rather than passing
        vacuously if the reordering ever stops happening.
        """
        from omniscribe.core.workflows import hybrid_repair as hr
        from omniscribe.core.workflows.hybrid_repair import run_repair_phase

        pages = [0, 1, 2, 3]
        others_finished = asyncio.Event()
        completed: list[int] = []

        async def _gated_repair(*, p_num: int, **kwargs: object) -> PageRepairSummary:
            if p_num == 1:
                await others_finished.wait()
            completed.append(p_num)
            if p_num != 1 and len(completed) == len(pages) - 1:
                others_finished.set()
            return PageRepairSummary(
                page_idx=p_num,
                target=0.9,
                block_count=1,
                avg_confidence=0.4,
                repaired_count=0,
                below_target_count=1,
            )

        monkeypatch.setattr(hr, "repair_single_page", _gated_repair)

        class _StubEngine:
            ocr_processor: OCRProcessor = cast(OCRProcessor, object())
            block_callbacks = BlockCallbackSet()

        summaries = await run_repair_phase(
            engine=_StubEngine(),
            pages_structured={p: [((0.0, 0.0, 1.0, 1.0), "ab")] for p in pages},
            images_dict={p: f"img-{p}" for p in pages},
            page_nums=pages,
            repair_options=RepairOptions(enabled=True, target=0.5, max_retries=1),
            concurrency=len(pages),
            progress=None,
            on_warning=None,
            decoded_get=lambda p_num: None,
        )

        # Precondition: page 1 really did complete last.
        assert sorted(completed) == pages, f"pages ran {completed}"
        assert completed[-1] == 1, (
            f"expected page 1 to complete last, got completion order {completed}"
        )
        assert [s.page_idx for s in summaries] == pages

    async def test_summaries_track_pages_through_real_repair_under_concurrency(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """End-to-end companion: real repair, one summary per page, in order.

        Runs the unstubbed ``repair_single_page`` with every page
        repairable and all of them admitted at once. It deliberately
        makes no claim about completion order — that guarantee is
        covered above with a forced gate — its job is that no page is
        dropped, duplicated or misfiled once the driver is concurrent.
        """
        from PIL import Image

        from omniscribe.core.workflows.hybrid_repair import run_repair_phase

        monkeypatch.setattr(
            "omniscribe.core.workflows.hybrid_repair.decode_base64_image",
            lambda image_b64, mode=None: Image.effect_noise((64, 64), 64).convert(
                "RGB"
            ),
        )

        class _StubOCR:
            async def perform_ocr_on_crop(self, crop_b64: str, **kwargs: object) -> str:
                return "clean repaired words here"

        class _StubEngine:
            ocr_processor: OCRProcessor = cast(OCRProcessor, _StubOCR())
            block_callbacks = BlockCallbackSet()

        pages = [0, 1, 2, 3]
        summaries = await run_repair_phase(
            engine=_StubEngine(),
            pages_structured={p: [((0.0, 0.0, 1.0, 1.0), "ab")] for p in pages},
            images_dict={p: f"img-{p}" for p in pages},
            page_nums=pages,
            repair_options=RepairOptions(enabled=True, target=0.9, max_retries=1),
            concurrency=len(pages),
            progress=None,
            on_warning=None,
            decoded_get=lambda p_num: None,
        )

        assert [s.page_idx for s in summaries] == pages
        assert all(
            s.block_count == 1 and s.repaired_count == 1 and s.below_target_count == 0
            for s in summaries
        ), f"unexpected per-page stats: {summaries}"

    async def test_progress_emits_never_interleave_across_pages(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The shared counter serialises its progress emit.

        The counter is carried via a single-element list, which the old
        serial loop made safe by construction. With concurrent pages the
        increment and its emit have to be one critical section,
        otherwise two pages sit inside the callback simultaneously and a
        stale count can be published after a newer one.
        """
        from PIL import Image

        from omniscribe.core.workflows.hybrid_repair import run_repair_phase

        monkeypatch.setattr(
            "omniscribe.core.workflows.hybrid_repair.decode_base64_image",
            lambda image_b64, mode=None: Image.effect_noise((64, 64), 64).convert(
                "RGB"
            ),
        )

        class _StubOCR:
            async def perform_ocr_on_crop(self, crop_b64: str, **kwargs: object) -> str:
                # A bare yield, not a duration: the ordering this test
                # cares about comes from the counter lock, not timing.
                await asyncio.sleep(0)
                return "clean repaired words here"

        class _StubEngine:
            ocr_processor: OCRProcessor = cast(OCRProcessor, _StubOCR())
            block_callbacks = BlockCallbackSet()

        events: list[str] = []

        async def _progress(stage: str, current: int, total: int, message: str) -> None:
            events.append(f"+{current}")
            # Yield: an unguarded counter lets the next page's emit land
            # inside this one.
            await asyncio.sleep(0)
            events.append(f"-{current}")

        pages = [0, 1, 2]
        await run_repair_phase(
            engine=_StubEngine(),
            pages_structured={p: [((0.0, 0.0, 1.0, 1.0), "ab")] for p in pages},
            images_dict={p: f"img-{p}" for p in pages},
            page_nums=pages,
            repair_options=RepairOptions(enabled=True, target=0.9, max_retries=1),
            concurrency=3,
            progress=_progress,
            on_warning=None,
            decoded_get=lambda p_num: None,
        )

        # The opening "0/targets" emit plus one per repaired block, each a
        # contiguous pair, in non-decreasing order.
        assert events == [
            x for n in range(len(events) // 2) for x in (f"+{n}", f"-{n}")
        ]

    async def test_cancelled_run_terminates_without_dangling_tasks(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cancelling the phase aborts it promptly and drains its pages.

        The phase has no ``cancel_check``; a cancelled run arrives as
        ``asyncio`` cancellation of the awaiting task, and the TaskGroup
        must cancel and await every in-flight page before re-raising so
        no page task is left running against the VLM endpoint.
        """
        from omniscribe.core.workflows import hybrid_repair as hr
        from omniscribe.core.workflows.hybrid_repair import run_repair_phase

        started = asyncio.Event()
        release = asyncio.Event()

        async def _hanging_repair(*, p_num: int, **kwargs: object) -> PageRepairSummary:
            started.set()
            # Never completes on its own; only cancellation ends it.
            await release.wait()
            raise AssertionError("unreachable")

        monkeypatch.setattr(hr, "repair_single_page", _hanging_repair)

        class _StubEngine:
            ocr_processor: OCRProcessor = cast(OCRProcessor, object())
            block_callbacks = BlockCallbackSet()

        pages = [0, 1, 2, 3]
        task = asyncio.create_task(
            run_repair_phase(
                engine=_StubEngine(),
                pages_structured={p: [((0.0, 0.0, 1.0, 1.0), "ab")] for p in pages},
                images_dict={p: f"img-{p}" for p in pages},
                page_nums=pages,
                repair_options=RepairOptions(enabled=True, target=0.5, max_retries=1),
                concurrency=3,
                progress=None,
                on_warning=None,
                decoded_get=lambda p_num: None,
            )
        )

        await asyncio.wait_for(started.wait(), timeout=_GUARD_TIMEOUT)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=_GUARD_TIMEOUT)

        leftover = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        assert leftover == [], f"cancelled run left dangling tasks: {leftover}"


class TestRepairPhaseFailureSurface:
    """A failed page must reach the caller as its own exception type.

    ``asyncio.TaskGroup`` wraps child failures in an exception group, so
    the fan-out has to unwrap it. Audit P0-1 already recorded a group
    leaking out of the OCR stage as a generic 500 for exactly this reason,
    and the repair fan-out was the one place where the unwrap was
    incomplete: a page error outside the three control-flow types escaped
    as an ``ExceptionGroup``, and a MIXED failure escaped as a
    ``BaseExceptionGroup`` that no type-specific handler matched — so
    ``except OCRCancelled`` (the 503 cancel handshake in
    ``plugins/ocr/routes.py``) stopped firing.
    """

    async def test_page_error_surfaces_as_its_own_type(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A plain page error must reach the caller as that same error."""
        from omniscribe.core.workflows import hybrid_repair as hr
        from omniscribe.core.workflows.hybrid_repair import run_repair_phase

        async def _failing_repair(*, p_num: int, **kwargs: object) -> PageRepairSummary:
            raise ValueError("page repair exploded")

        monkeypatch.setattr(hr, "repair_single_page", _failing_repair)

        class _StubEngine:
            ocr_processor: OCRProcessor = cast(OCRProcessor, object())
            block_callbacks = BlockCallbackSet()

        pages = [0, 1]
        with pytest.raises(ValueError, match="page repair exploded"):
            await asyncio.wait_for(
                run_repair_phase(
                    engine=_StubEngine(),
                    pages_structured={p: [((0.0, 0.0, 1.0, 1.0), "ab")] for p in pages},
                    images_dict={p: f"img-{p}" for p in pages},
                    page_nums=pages,
                    repair_options=RepairOptions(
                        enabled=True, target=0.5, max_retries=1
                    ),
                    concurrency=2,
                    progress=None,
                    on_warning=None,
                    decoded_get=lambda p_num: None,
                ),
                timeout=_GUARD_TIMEOUT,
            )

    async def test_mixed_failure_surfaces_cancellation_bare(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A cancellation must stay catchable by ``except OCRCancelled``.

        Both pages are released together so both failures land in the
        group; the unwrap then has to pick the cancellation and raise it
        bare, because the cancel handshake upstream is keyed on that exact
        type rather than on the group.
        """
        from omniscribe.core.workflows import hybrid_repair as hr
        from omniscribe.core.workflows.base import OCRCancelled
        from omniscribe.core.workflows.hybrid_repair import run_repair_phase

        release = asyncio.Event()

        async def _failing_repair(*, p_num: int, **kwargs: object) -> PageRepairSummary:
            await release.wait()
            if p_num == 0:
                raise OCRCancelled("cancelled mid-repair")
            raise ValueError("page repair exploded")

        monkeypatch.setattr(hr, "repair_single_page", _failing_repair)

        class _StubEngine:
            ocr_processor: OCRProcessor = cast(OCRProcessor, object())
            block_callbacks = BlockCallbackSet()

        pages = [0, 1]
        task = asyncio.create_task(
            run_repair_phase(
                engine=_StubEngine(),
                pages_structured={p: [((0.0, 0.0, 1.0, 1.0), "ab")] for p in pages},
                images_dict={p: f"img-{p}" for p in pages},
                page_nums=pages,
                repair_options=RepairOptions(enabled=True, target=0.5, max_retries=1),
                concurrency=2,
                progress=None,
                on_warning=None,
                decoded_get=lambda p_num: None,
            )
        )
        await _drain_ready_tasks()
        release.set()

        with pytest.raises(OCRCancelled):
            await asyncio.wait_for(task, timeout=_GUARD_TIMEOUT)

    def test_repair_failure_prefers_control_flow_exception(self) -> None:
        """The helper prefers the cancellation over any other leaf."""
        from omniscribe.core.workflows.base import OCRCancelled
        from omniscribe.core.workflows.hybrid_repair import _repair_failure

        group = BaseExceptionGroup(
            "repair", [OCRCancelled("cancelled"), ValueError("boom")]
        )
        assert isinstance(_repair_failure(group), OCRCancelled)

    def test_repair_failure_returns_other_leaf_bare(self) -> None:
        """Without a control-flow leaf, the original error type survives."""
        from omniscribe.core.workflows.hybrid_repair import _repair_failure

        group = BaseExceptionGroup("repair", [ValueError("boom")])
        picked = _repair_failure(group)
        assert isinstance(picked, ValueError)
        assert not isinstance(picked, BaseExceptionGroup)
