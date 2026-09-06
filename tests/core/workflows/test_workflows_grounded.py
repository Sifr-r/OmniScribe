"""
Direct unit tests for ``GroundedEngine``'s staged methods.

Grounded has only one engine-specific stage (``_accumulate_pages``) plus the
``execute()`` orchestrator. These tests pin down the contract: blocks are
grouped by ``page_index`` preserving backend order, the orchestrator
propagates ``failed_pages`` from the backend into ``last_failed_pages``,
document processors run on the assembled ``DocumentResult``, and the
output writer is invoked exactly once at the end.

The ``test_pipeline.py`` suite exercises the hybrid path through
``OCRPipeline``; this file is the grounded equivalent.
"""

from __future__ import annotations

import pytest

from omniscribe.core.callbacks import BlockCallbackSet
from omniscribe.core.document import DocumentResult
from omniscribe.core.grounded import GroundedBlock, GroundedResponse
from omniscribe.core.ocr.resilience import CircuitOpenError
from omniscribe.core.processors import DocumentProcessor
from omniscribe.core.workflows.grounded import GroundedEngine
from omniscribe.core.workflows.repair import RepairOptions

# ---------------------------------------------------------------------------
# Stub backend
# ---------------------------------------------------------------------------


class _StubGroundedBackend:
    """Drop-in replacement for ``GroundedOCRBackend``.

    Returns a configurable ``GroundedResponse`` and records every call so
    tests can assert on it.
    """

    def __init__(self, response: GroundedResponse | None = None) -> None:
        self.response = response or GroundedResponse(blocks=[])
        self.calls: list[str] = []

    async def ocr_document(self, pdf_path, progress=None, on_warning=None):
        self.calls.append(pdf_path)
        return self.response


def _noop_writer(_in: str, _out: str, _pages: dict, _dpi: int) -> None:
    """Output writer that discards its arguments. Tests don't inspect PDF output."""


def _engine(
    backend: _StubGroundedBackend | None = None,
    document_processors: list[DocumentProcessor] | None = None,
) -> GroundedEngine:
    return GroundedEngine(
        grounded_backend=backend or _StubGroundedBackend(),
        output_writer=_noop_writer,
        document_processors=document_processors,
    )


class _TaggingProcessor(DocumentProcessor):
    """Tags each page so tests can verify the processor pipeline ran."""

    async def process(self, document: DocumentResult) -> DocumentResult:
        for page in document.pages:
            page.metadata["tagging_processor"] = True
        return document


# ---------------------------------------------------------------------------
# _accumulate_pages
# ---------------------------------------------------------------------------


class TestGroundedAccumulatePages:
    def test_groups_blocks_by_page_index(self) -> None:
        blocks = [
            GroundedBlock(bbox=[0.1, 0.1, 0.9, 0.2], text="p0 first", page_index=0),
            GroundedBlock(bbox=[0.1, 0.3, 0.9, 0.4], text="p1 first", page_index=1),
            GroundedBlock(bbox=[0.1, 0.5, 0.9, 0.6], text="p0 second", page_index=0),
        ]
        pages = GroundedEngine._accumulate_pages(blocks)
        assert pages == {
            0: [
                ((0.1, 0.1, 0.9, 0.2), "p0 first"),
                ((0.1, 0.5, 0.9, 0.6), "p0 second"),
            ],
            1: [((0.1, 0.3, 0.9, 0.4), "p1 first")],
        }

    def test_preserves_backend_ordering(self) -> None:
        # Backend emits page 1's block interleaved with page 0's. Within
        # each page, blocks must keep their backend order.
        blocks = [
            GroundedBlock(bbox=[0.1, 0.1, 0.9, 0.2], text="p0-a", page_index=0),
            GroundedBlock(bbox=[0.1, 0.2, 0.9, 0.3], text="p1-a", page_index=1),
            GroundedBlock(bbox=[0.1, 0.3, 0.9, 0.4], text="p0-b", page_index=0),
            GroundedBlock(bbox=[0.1, 0.4, 0.9, 0.5], text="p1-b", page_index=1),
        ]
        pages = GroundedEngine._accumulate_pages(blocks)
        assert [t for _, t in pages[0]] == ["p0-a", "p0-b"]
        assert [t for _, t in pages[1]] == ["p1-a", "p1-b"]

    def test_empty_blocks_returns_empty_dict(self) -> None:
        assert GroundedEngine._accumulate_pages([]) == {}

    def test_drops_blocks_with_default_text_label(self) -> None:
        # Non-text labels (image, signature_line, etc.) are filtered upstream
        # by the backend; _accumulate_pages preserves whatever the backend
        # sends. This test pins down that contract.
        blocks = [
            GroundedBlock(
                bbox=[0.1, 0.1, 0.9, 0.2], text="real", page_index=0, label="text"
            ),
            GroundedBlock(
                bbox=[0.1, 0.3, 0.9, 0.4], text="ignored", page_index=0, label="image"
            ),
        ]
        pages = GroundedEngine._accumulate_pages(blocks)
        # Both blocks land in pages[0] — filtering is the backend's job.
        assert [t for _, t in pages[0]] == ["real", "ignored"]


# ---------------------------------------------------------------------------
# execute()
# ---------------------------------------------------------------------------


class TestGroundedExecute:
    async def test_basic_flow_passes_blocks_to_writer(self) -> None:
        captured: dict = {}

        def writer(inp, out, pages, dpi):
            captured["input"] = inp
            captured["output"] = out
            captured["pages"] = dict(pages)
            captured["dpi"] = dpi

        backend = _StubGroundedBackend(
            GroundedResponse(
                blocks=[
                    GroundedBlock(
                        bbox=[0.1, 0.1, 0.9, 0.2], text="hello", page_index=0
                    ),
                    GroundedBlock(
                        bbox=[0.1, 0.3, 0.9, 0.4], text="world", page_index=0
                    ),
                ]
            )
        )
        engine = GroundedEngine(grounded_backend=backend, output_writer=writer)

        result = await engine.execute("in.pdf", "out.pdf", dpi=150)

        assert backend.calls == ["in.pdf"]
        assert captured["input"] == "in.pdf"
        assert captured["output"] == "out.pdf"
        assert captured["dpi"] == 150
        # Writer received the accumulated blocks.
        assert captured["pages"][0] == [
            ((0.1, 0.1, 0.9, 0.2), "hello"),
            ((0.1, 0.3, 0.9, 0.4), "world"),
        ]
        # Returned view filters out blank-text boxes (none here).
        assert result == {0: ["hello", "world"]}
        assert engine.last_failed_pages == []
        assert engine.last_document_result is not None

    async def test_propagates_failed_pages_from_backend(self) -> None:
        backend = _StubGroundedBackend(
            GroundedResponse(
                blocks=[
                    GroundedBlock(bbox=[0.1, 0.1, 0.9, 0.2], text="ok", page_index=0)
                ],
                failed_pages=[1, 2],
            )
        )
        engine = _engine(backend=backend)
        await engine.execute("in.pdf", "out.pdf", dpi=150)
        assert engine.last_failed_pages == [1, 2]

    async def test_resets_run_state_at_entry(self) -> None:
        # A second run on the same engine must clear stale state from the
        # first — same contract as HybridEngine via EngineBase._reset_run_state.
        backend = _StubGroundedBackend(
            GroundedResponse(
                blocks=[
                    GroundedBlock(bbox=[0.1, 0.1, 0.9, 0.2], text="first", page_index=0)
                ],
                failed_pages=[3],
            )
        )
        engine = _engine(backend=backend)

        await engine.execute("in.pdf", "out-1.pdf", dpi=150)
        assert engine.last_failed_pages == [3]
        assert engine.last_document_result is not None

        # Reset the backend to a clean response (no failures).
        backend.response = GroundedResponse(
            blocks=[
                GroundedBlock(bbox=[0.1, 0.1, 0.9, 0.2], text="second", page_index=0)
            ]
        )
        await engine.execute("in.pdf", "out-2.pdf", dpi=150)
        assert engine.last_failed_pages == []
        # The document_result has been replaced.
        assert engine.last_document_result is not None
        assert engine.last_document_result.pages[0].blocks[0].text == "second"

    async def test_runs_document_processors(self) -> None:
        backend = _StubGroundedBackend(
            GroundedResponse(
                blocks=[
                    GroundedBlock(bbox=[0.1, 0.1, 0.9, 0.2], text="hello", page_index=0)
                ]
            )
        )
        engine = _engine(backend=backend, document_processors=[_TaggingProcessor()])  # type: ignore[abstract]

        await engine.execute("in.pdf", "out.pdf", dpi=150)

        assert engine.last_document_result is not None
        assert (
            engine.last_document_result.pages[0].metadata.get("tagging_processor")
            is True
        )

    async def test_empty_response_emits_blank_document(self) -> None:
        backend = _StubGroundedBackend(GroundedResponse(blocks=[]))
        engine = _engine(backend=backend)

        result = await engine.execute("in.pdf", "out.pdf", dpi=150)

        assert result == {}
        assert engine.last_failed_pages == []
        assert engine.last_document_result is not None
        assert engine.last_document_result.pages == []


# ---------------------------------------------------------------------------
# Quality repair (spec §3.2)
# ---------------------------------------------------------------------------


class _CropCapableStubBackend(_StubGroundedBackend):
    """Adds the ``ocr_crop`` primitive so the engine's feature-detection fires."""

    def __init__(
        self,
        response: GroundedResponse | None = None,
        crop_text: str = "The quick brown fox jumps over the lazy dog",
    ) -> None:
        super().__init__(response)
        self.crop_text = crop_text
        self.crop_calls = 0
        # Records (input_path, page_index, bbox) per call so tests can
        # pin the exact arguments the engine forwards to ``ocr_crop``.
        self.crop_args: list[tuple[str, int, tuple[float, float, float, float]]] = []

    async def ocr_crop(self, input_path, page_index, bbox, **kwargs):
        self.crop_calls += 1
        self.crop_args.append((input_path, page_index, tuple(bbox)))
        return self.crop_text


class TestGroundedRepair:
    def _below_target_response(self) -> GroundedResponse:
        return GroundedResponse(
            blocks=[
                GroundedBlock(bbox=[0.1, 0.1, 0.9, 0.2], text="x", page_index=0),
            ]
        )

    async def test_below_target_block_is_repaired_and_persisted(self) -> None:
        backend = _CropCapableStubBackend(self._below_target_response())
        engine = _engine(backend=backend)

        await engine.execute(
            "in.pdf",
            "out.pdf",
            dpi=150,
            repair_options=RepairOptions(target=0.98),
        )

        assert backend.crop_calls == 1
        # I-1: the engine must forward input_path / page_index / bbox into
        # ``ocr_crop`` untouched (bbox repacked from the GroundedBlock list
        # into a float tuple by ``_repair_blocks``).
        assert backend.crop_args == [("in.pdf", 0, (0.1, 0.1, 0.9, 0.2))]
        assert backend.response.blocks[0].text == (
            "The quick brown fox jumps over the lazy dog"
        )
        assert engine.last_document_result is not None
        assert engine.last_document_result.pages[0].blocks[0].text == (
            "The quick brown fox jumps over the lazy dog"
        )

    async def test_healthy_block_makes_zero_crop_calls(self) -> None:
        backend = _CropCapableStubBackend(
            GroundedResponse(
                blocks=[
                    GroundedBlock(
                        bbox=[0.1, 0.1, 0.9, 0.2],
                        text="The quick brown fox jumps over the lazy dog",
                        page_index=0,
                    )
                ]
            )
        )
        engine = _engine(backend=backend)

        await engine.execute(
            "in.pdf",
            "out.pdf",
            dpi=150,
            repair_options=RepairOptions(target=0.98),
        )

        assert backend.crop_calls == 0

    async def test_empty_blocks_are_skipped(self) -> None:
        # Empty and whitespace-only blocks are never repair candidates —
        # no crop call, text left untouched (parity with hybrid's
        # ``test_empty_blocks_are_left_to_refine``).
        backend = _CropCapableStubBackend(
            GroundedResponse(
                blocks=[
                    GroundedBlock(bbox=[0.1, 0.1, 0.9, 0.2], text="", page_index=0),
                    GroundedBlock(bbox=[0.1, 0.3, 0.9, 0.4], text="   ", page_index=0),
                ]
            )
        )
        engine = _engine(backend=backend)

        await engine.execute(
            "in.pdf",
            "out.pdf",
            dpi=150,
            repair_options=RepairOptions(target=0.98),
        )

        assert backend.crop_calls == 0
        assert backend.response.blocks[0].text == ""
        assert backend.response.blocks[1].text == "   "

    async def test_backend_without_ocr_crop_is_skipped(self) -> None:
        # The plain stub has no ``ocr_crop`` — repair must be a no-op and
        # leave the original text untouched.
        backend = _StubGroundedBackend(self._below_target_response())
        engine = _engine(backend=backend)

        await engine.execute(
            "in.pdf",
            "out.pdf",
            dpi=150,
            repair_options=RepairOptions(target=0.98),
        )

        assert backend.response.blocks[0].text == "x"
        assert engine.last_document_result is not None
        assert engine.last_document_result.pages[0].blocks[0].text == "x"

    async def test_circuit_open_error_propagates(self) -> None:
        class _BreakerBackend(_CropCapableStubBackend):
            async def ocr_crop(self, input_path, page_index, bbox, **kwargs):
                raise CircuitOpenError(failures=5, retry_after=30.0)

        backend = _BreakerBackend(self._below_target_response())
        engine = _engine(backend=backend)

        with pytest.raises(CircuitOpenError):
            await engine.execute(
                "in.pdf",
                "out.pdf",
                dpi=150,
                repair_options=RepairOptions(target=0.98),
            )

    async def test_crop_failure_emits_warning_and_keeps_best_text(self) -> None:
        class _ExplodingBackend(_CropCapableStubBackend):
            async def ocr_crop(self, input_path, page_index, bbox, **kwargs):
                raise RuntimeError("VLM exploded")

        backend = _ExplodingBackend(self._below_target_response())
        engine = _engine(backend=backend)
        warnings: list[tuple[int, Exception]] = []

        async def on_warn(page_idx, exc):
            warnings.append((page_idx, exc))

        await engine.execute(
            "in.pdf",
            "out.pdf",
            dpi=150,
            repair_options=RepairOptions(target=0.98),
            on_warning=on_warn,
        )

        # Spec §3.2: warning frame out, best-so-far text kept, job goes on.
        assert len(warnings) == 1
        assert warnings[0][0] == 0
        assert isinstance(warnings[0][1], RuntimeError)
        assert backend.response.blocks[0].text == "x"
        assert engine.last_document_result is not None
        assert engine.last_document_result.pages[0].blocks[0].text == "x"

    async def test_default_off_without_repair_options(self) -> None:
        backend = _CropCapableStubBackend(self._below_target_response())
        engine = _engine(backend=backend)

        await engine.execute("in.pdf", "out.pdf", dpi=150)

        assert backend.crop_calls == 0
        assert backend.response.blocks[0].text == "x"

    async def test_progress_reuses_refine_stage(self) -> None:
        # Parity with hybrid's ``test_progress_reuses_refine_stage``: repair
        # progress rides the ``refine`` stage label, never a new one.
        backend = _CropCapableStubBackend(self._below_target_response())
        engine = _engine(backend=backend)
        events: list[tuple[str, int, int]] = []

        async def cb(stage: str, cur: int, tot: int, msg: str) -> None:
            events.append((stage, cur, tot))

        await engine.execute(
            "in.pdf",
            "out.pdf",
            dpi=150,
            repair_options=RepairOptions(target=0.98),
            progress=cb,
        )

        assert events[0] == ("refine", 0, 1)
        # ``EngineBase._emit`` appends the trailing ``embed`` frames, so
        # pin the refine bracket on the refine-scoped subsequence.
        refine_events = [e for e in events if e[0] == "refine"]
        assert refine_events[0] == ("refine", 0, 1)
        assert refine_events[-1] == ("refine", 1, 1)

    async def test_page_and_job_summary_callbacks_fire(self) -> None:
        # Parity with hybrid's ``test_page_and_job_summary_callbacks_fire``,
        # except grounded's ``execute`` emits the job summary itself (hybrid's
        # test calls ``_repair_pages`` + ``emit_job_repair_summary`` by hand).
        seen: list[tuple[str, int | None]] = []

        async def on_summary(scope, page_idx, target, avg, repaired, below):
            seen.append((scope, page_idx))

        backend = _CropCapableStubBackend(self._below_target_response())
        # ``_engine()`` doesn't take ``block_callbacks`` — construct the
        # engine directly, binding the NamedTuple field by keyword.
        engine = GroundedEngine(
            grounded_backend=backend,
            output_writer=_noop_writer,
            block_callbacks=BlockCallbackSet(on_quality_summary=on_summary),
        )

        await engine.execute(
            "in.pdf",
            "out.pdf",
            dpi=150,
            repair_options=RepairOptions(target=0.98),
        )

        # Page summary from ``_repair_blocks``, job summary from
        # ``emit_job_repair_summary`` — both fired inside ``execute``.
        assert seen == [("page", 0), ("job", None)]

    async def test_multi_page_blocks_repaired_in_ascending_page_order(self) -> None:
        # Insert page 1's block FIRST so ``sorted(by_page)`` ordering is
        # actually exercised (dict insertion order alone would hide a bug).
        blocks = [
            GroundedBlock(bbox=[0.1, 0.3, 0.9, 0.4], text="y", page_index=1),
            GroundedBlock(bbox=[0.1, 0.1, 0.9, 0.2], text="x", page_index=0),
        ]
        backend = _CropCapableStubBackend(GroundedResponse(blocks=blocks))
        engine = _engine(backend=backend)

        await engine.execute(
            "in.pdf",
            "out.pdf",
            dpi=150,
            repair_options=RepairOptions(target=0.98),
        )

        assert backend.crop_calls == 2
        assert [page_idx for _, page_idx, _ in backend.crop_args] == [0, 1]
        assert blocks[0].text == "The quick brown fox jumps over the lazy dog"
        assert blocks[1].text == "The quick brown fox jumps over the lazy dog"
