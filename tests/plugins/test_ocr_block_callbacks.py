"""Tests for OCR block callbacks, adapter schema compliance, and pipeline wiring.

Validates RFC 004 R2 block callback adapter:
1. `_block_callbacks_adapter` return values (None on missing channel or progress).
2. Wire schema compliance for emitted frames (`block_complete`, `page_complete`,
   `block_retry`, `block_revised`, `quality_summary`).
3. Safe exception handling in progress emission (fail-open / non-bubbling).
4. `_execute` pipeline integration (passes `BlockCallbackSet` when channel is present,
   `None` when channel is absent).
5. Digital document fast path emission (emits `block_complete` and `page_complete`
   frames for ingested pages and blocks).
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from omniscribe.config import RuntimeSettings
from omniscribe.core.callbacks import BlockCallbackSet
from omniscribe.core.document import DocumentBlock, DocumentPage, DocumentResult
from omniscribe.plugins.ocr.schemas import OCRRequest
from omniscribe.plugins.ocr.service import OCRServiceImpl

ocr_service_mod = importlib.import_module("omniscribe.plugins.ocr.service")


class _MockProgressService:
    """In-memory mock capturing progress emissions."""

    def __init__(self, *, raise_on_emit: bool = False) -> None:
        self.emitted_frames: list[tuple[str, str | None, dict[str, Any]]] = []
        self.raise_on_emit = raise_on_emit

    async def emit_progress(
        self, job_id: str, channel_id: str | None, frame: Mapping[str, Any]
    ) -> int:
        if self.raise_on_emit:
            raise RuntimeError("Transport connection severed")
        self.emitted_frames.append((job_id, channel_id, dict(frame)))
        return 1

    def is_cancelled(self, channel_id: str) -> bool:
        return False


def _create_service(
    progress: Any | None = None,
    settings: RuntimeSettings | None = None,
) -> OCRServiceImpl:
    """Create an OCRServiceImpl instance with minimal mock dependencies."""

    class _DummyQueue:
        def is_cancelled(self, job_id: str) -> bool:
            return False

        async def status(self, job_id: str) -> Any:
            return None

    class _DummyArtifacts:
        async def put(self, *args: Any, **kwargs: Any) -> Any:
            class _Handle:
                id = "artifact-id-123"
                token = "artifact-token-456"

            return _Handle()

    return OCRServiceImpl(
        settings=settings or RuntimeSettings(),
        queue=_DummyQueue(),  # type: ignore[arg-type]
        artifacts=_DummyArtifacts(),  # type: ignore[arg-type]
        progress=progress,
        max_upload_mb=10,
    )


# ============================================================================
# 1. Tests for `_block_callbacks_adapter`
# ============================================================================


def test_adapter_returns_none_when_channel_is_none() -> None:
    """_block_callbacks_adapter returns None if channel is None."""
    mock_progress = _MockProgressService()
    service = _create_service(progress=mock_progress)
    assert service._block_callbacks_adapter("job-1", None) is None


def test_adapter_returns_none_when_channel_is_empty_string() -> None:
    """_block_callbacks_adapter returns None if channel is empty string."""
    mock_progress = _MockProgressService()
    service = _create_service(progress=mock_progress)
    assert service._block_callbacks_adapter("job-1", "") is None


def test_adapter_returns_none_when_progress_service_is_none() -> None:
    """_block_callbacks_adapter returns None if _progress is None even with a channel."""
    service = _create_service(progress=None)
    assert service._block_callbacks_adapter("job-1", "channel-xyz") is None


def test_adapter_returns_complete_block_callback_set_when_active() -> None:
    """When active, returns a BlockCallbackSet with all callback attributes defined."""
    mock_progress = _MockProgressService()
    service = _create_service(progress=mock_progress)
    callbacks = service._block_callbacks_adapter("job-1", "channel-xyz")

    assert callbacks is not None
    assert isinstance(callbacks, BlockCallbackSet)
    assert callable(callbacks.on_block)
    assert callable(callbacks.on_page_complete)
    assert callable(callbacks.on_block_retry)
    assert callable(callbacks.on_block_revised)
    assert callable(callbacks.on_quality_summary)


async def test_callback_on_block_emits_block_complete_schema() -> None:
    """on_block invokes emit_progress with valid block_complete frame schema."""
    mock_progress = _MockProgressService()
    service = _create_service(progress=mock_progress)
    callbacks = service._block_callbacks_adapter("job-100", "chan-100")
    assert callbacks is not None
    assert callbacks.on_block is not None

    # Test with confidence provided
    await callbacks.on_block(
        0,
        2,
        [0.1, 0.2, 0.8, 0.9],
        "Recognized sentence",
        "paragraph",
        0.95,
    )

    assert len(mock_progress.emitted_frames) == 1
    job_id, channel, frame = mock_progress.emitted_frames[0]
    assert job_id == "job-100"
    assert channel == "chan-100"
    assert frame == {
        "type": "block_complete",
        "page_idx": 0,
        "block_idx": 2,
        "bbox": [0.1, 0.2, 0.8, 0.9],
        "text": "Recognized sentence",
        "kind": "paragraph",
        "confidence": 0.95,
    }

    # Test with confidence=None (should omit confidence or not fail)
    await callbacks.on_block(
        1,
        0,
        [0.0, 0.0, 1.0, 0.5],
        "Header text",
        "heading",
        None,
    )
    assert len(mock_progress.emitted_frames) == 2
    _, _, frame2 = mock_progress.emitted_frames[1]
    assert frame2["type"] == "block_complete"
    assert frame2["page_idx"] == 1
    assert frame2["block_idx"] == 0
    assert frame2["bbox"] == [0.0, 0.0, 1.0, 0.5]
    assert frame2["text"] == "Header text"
    assert frame2["kind"] == "heading"
    assert "confidence" not in frame2


async def test_callback_on_page_complete_emits_page_complete_schema() -> None:
    """on_page_complete invokes emit_progress with valid page_complete frame schema."""
    mock_progress = _MockProgressService()
    service = _create_service(progress=mock_progress)
    callbacks = service._block_callbacks_adapter("job-101", "chan-101")
    assert callbacks is not None
    assert callbacks.on_page_complete is not None

    await callbacks.on_page_complete(4)

    assert len(mock_progress.emitted_frames) == 1
    job_id, channel, frame = mock_progress.emitted_frames[0]
    assert job_id == "job-101"
    assert channel == "chan-101"
    assert frame == {
        "type": "page_complete",
        "page_idx": 4,
    }


async def test_callback_on_block_retry_emits_block_retry_schema() -> None:
    """on_block_retry invokes emit_progress with valid block_retry frame schema."""
    mock_progress = _MockProgressService()
    service = _create_service(progress=mock_progress)
    callbacks = service._block_callbacks_adapter("job-102", "chan-102")
    assert callbacks is not None
    assert callbacks.on_block_retry is not None

    await callbacks.on_block_retry(1, 3, 2, 0.62, 0.85)

    assert len(mock_progress.emitted_frames) == 1
    job_id, channel, frame = mock_progress.emitted_frames[0]
    assert job_id == "job-102"
    assert channel == "chan-102"
    assert frame == {
        "type": "block_retry",
        "page_idx": 1,
        "block_idx": 3,
        "attempt": 2,
        "confidence": 0.62,
        "target": 0.85,
    }


async def test_callback_on_block_revised_emits_block_revised_schema() -> None:
    """on_block_revised invokes emit_progress with valid block_revised frame schema."""
    mock_progress = _MockProgressService()
    service = _create_service(progress=mock_progress)
    callbacks = service._block_callbacks_adapter("job-103", "chan-103")
    assert callbacks is not None
    assert callbacks.on_block_revised is not None

    # Test with confidence provided
    await callbacks.on_block_revised(
        0,
        3,
        2,
        [0.15, 0.25, 0.75, 0.85],
        "Repaired high-confidence text",
        "text",
        0.94,
    )

    assert len(mock_progress.emitted_frames) == 1
    job_id, channel, frame = mock_progress.emitted_frames[0]
    assert job_id == "job-103"
    assert channel == "chan-103"
    assert frame == {
        "type": "block_revised",
        "page_idx": 0,
        "block_idx": 3,
        "attempt": 2,
        "bbox": [0.15, 0.25, 0.75, 0.85],
        "text": "Repaired high-confidence text",
        "kind": "text",
        "confidence": 0.94,
    }

    # Test with confidence None
    await callbacks.on_block_revised(
        0,
        3,
        2,
        [0.15, 0.25, 0.75, 0.85],
        "Repaired text",
        "text",
        None,
    )
    assert len(mock_progress.emitted_frames) == 2
    _, _, frame2 = mock_progress.emitted_frames[1]
    assert frame2["type"] == "block_revised"
    assert "confidence" not in frame2


async def test_callback_on_quality_summary_emits_quality_summary_schema() -> None:
    """on_quality_summary invokes emit_progress with valid quality_summary frame schema."""
    mock_progress = _MockProgressService()
    service = _create_service(progress=mock_progress)
    callbacks = service._block_callbacks_adapter("job-104", "chan-104")
    assert callbacks is not None
    assert callbacks.on_quality_summary is not None

    # Page scope: page_idx is int
    await callbacks.on_quality_summary("page", 2, 0.85, 0.91, 3, 1)

    assert len(mock_progress.emitted_frames) == 1
    job_id, channel, frame = mock_progress.emitted_frames[0]
    assert job_id == "job-104"
    assert channel == "chan-104"
    assert frame == {
        "type": "quality_summary",
        "scope": "page",
        "target": 0.85,
        "avg_confidence": 0.91,
        "repaired_count": 3,
        "below_target_count": 1,
        "page_idx": 2,
    }

    # Document scope: page_idx is None
    await callbacks.on_quality_summary("document", None, 0.85, 0.89, 7, 2)
    assert len(mock_progress.emitted_frames) == 2
    _, _, frame2 = mock_progress.emitted_frames[1]
    assert frame2 == {
        "type": "quality_summary",
        "scope": "document",
        "target": 0.85,
        "avg_confidence": 0.89,
        "repaired_count": 7,
        "below_target_count": 2,
    }
    assert "page_idx" not in frame2


async def test_adapter_callback_exceptions_do_not_bubble_up() -> None:
    """Verify exceptions inside emit_progress are safely caught and logged."""
    broken_progress = _MockProgressService(raise_on_emit=True)
    service = _create_service(progress=broken_progress)
    callbacks = service._block_callbacks_adapter("job-105", "chan-105")
    assert callbacks is not None
    assert callbacks.on_block is not None
    assert callbacks.on_page_complete is not None
    assert callbacks.on_block_retry is not None
    assert callbacks.on_block_revised is not None
    assert callbacks.on_quality_summary is not None

    # None of these calls should raise an exception even when emit_progress raises
    await callbacks.on_block(0, 0, [0.0, 0.0, 1.0, 1.0], "text", "kind", 0.9)
    await callbacks.on_page_complete(0)
    await callbacks.on_block_retry(0, 0, 1, 0.5, 0.85)
    await callbacks.on_block_revised(0, 0, 1, [0.0, 0.0, 1.0, 1.0], "text", "kind", 0.9)
    await callbacks.on_quality_summary("page", 0, 0.85, 0.9, 1, 0)


# ============================================================================
# 2. Tests for OCR Pipeline Execution in `_execute`
# ============================================================================


async def test_execute_with_progress_channel_passes_block_callbacks_to_pipeline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When _execute runs with a progress channel, build_pipeline receives a non-None BlockCallbackSet."""
    captured: dict[str, Any] = {}

    def fake_build(
        settings: Any,
        options: OCRRequest,
        *,
        block_callbacks: BlockCallbackSet | None = None,
    ) -> Any:
        captured["block_callbacks"] = block_callbacks

        class _FakePipeline:
            last_document_result = None

        return _FakePipeline()

    async def fake_run(pipeline: Any, **kwargs: Any) -> dict[int, list[str]]:
        out_path = Path(kwargs["output_path"])
        out_path.write_bytes(b"%PDF-1.4 fake output")
        return {0: ["mock text"]}

    monkeypatch.setattr(ocr_service_mod, "build_pipeline", fake_build)
    monkeypatch.setattr(ocr_service_mod, "run_pipeline", fake_run)

    mock_progress = _MockProgressService()
    service = _create_service(progress=mock_progress)

    work_dir = tmp_path / "work_with_channel"
    work_dir.mkdir(parents=True, exist_ok=True)
    input_file = work_dir / "test.pdf"
    input_file.write_bytes(b"%PDF-1.4 test input")

    req = OCRRequest(progress_channel="my-test-channel")
    pdf_bytes, pages_data, _ = await service._execute(
        req, input_file, "test.pdf", job_id="job-pipeline-1"
    )

    assert pdf_bytes == b"%PDF-1.4 fake output"
    assert pages_data == {0: ["mock text"]}
    assert captured["block_callbacks"] is not None
    assert isinstance(captured["block_callbacks"], BlockCallbackSet)


async def test_execute_without_progress_channel_passes_none_block_callbacks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When _execute runs without a progress channel, build_pipeline receives block_callbacks=None."""
    captured: dict[str, Any] = {}

    def fake_build(
        settings: Any,
        options: OCRRequest,
        *,
        block_callbacks: BlockCallbackSet | None = None,
    ) -> Any:
        captured["block_callbacks"] = block_callbacks

        class _FakePipeline:
            last_document_result = None

        return _FakePipeline()

    async def fake_run(pipeline: Any, **kwargs: Any) -> dict[int, list[str]]:
        out_path = Path(kwargs["output_path"])
        out_path.write_bytes(b"%PDF-1.4 fake output")
        return {0: ["mock text"]}

    monkeypatch.setattr(ocr_service_mod, "build_pipeline", fake_build)
    monkeypatch.setattr(ocr_service_mod, "run_pipeline", fake_run)

    mock_progress = _MockProgressService()
    service = _create_service(progress=mock_progress)

    work_dir = tmp_path / "work_no_channel"
    work_dir.mkdir(parents=True, exist_ok=True)
    input_file = work_dir / "test.pdf"
    input_file.write_bytes(b"%PDF-1.4 test input")

    req = OCRRequest(progress_channel=None)
    pdf_bytes, pages_data, _ = await service._execute(
        req, input_file, "test.pdf", job_id="job-pipeline-2"
    )

    assert pdf_bytes == b"%PDF-1.4 fake output"
    assert pages_data == {0: ["mock text"]}
    assert captured["block_callbacks"] is None


# ============================================================================
# 3. Tests for Digital Document Fast Path
# ============================================================================


async def test_digital_document_fastpath_emits_block_and_page_complete_frames(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Digital document fast path emits block_complete and page_complete frames when progress channel is active."""
    doc_result = DocumentResult(
        pages=[
            DocumentPage(
                page_index=0,
                blocks=[
                    DocumentBlock(
                        bbox=(0.1, 0.1, 0.5, 0.3),
                        text="Document Title",
                        kind="heading",
                        confidence=1.0,
                        trust_score=1.0,
                        trust_flags=("source:digital",),
                    ),
                    DocumentBlock(
                        bbox=(0.1, 0.35, 0.9, 0.6),
                        text="First paragraph body.",
                        kind="paragraph",
                        confidence=1.0,
                        trust_score=1.0,
                        trust_flags=("source:digital",),
                    ),
                    # Whitespace block should NOT trigger on_block
                    DocumentBlock(
                        bbox=(0.1, 0.65, 0.9, 0.7),
                        text="   \n\t  ",
                        kind="text",
                        confidence=1.0,
                        trust_score=1.0,
                    ),
                ],
                width=612,
                height=792,
            ),
            DocumentPage(
                page_index=1,
                blocks=[
                    DocumentBlock(
                        bbox=(0.1, 0.1, 0.8, 0.4),
                        text="Second page content.",
                        kind="paragraph",
                        confidence=1.0,
                        trust_score=1.0,
                        trust_flags=("source:digital",),
                    ),
                ],
                width=612,
                height=792,
            ),
        ],
    )

    class _MockReader:
        def read(self, path: Path, filename: str) -> DocumentResult:
            return doc_result

    monkeypatch.setattr(
        ocr_service_mod, "get_reader_for_suffix", lambda suffix: _MockReader()
    )
    monkeypatch.setattr(
        ocr_service_mod, "render_synthetic_pdf", lambda doc: b"%PDF-1.4 synthetic docx"
    )

    mock_progress = _MockProgressService()
    service = _create_service(progress=mock_progress)

    work_dir = tmp_path / "work_digital"
    work_dir.mkdir(parents=True, exist_ok=True)
    input_file = work_dir / "sample.docx"
    input_file.write_bytes(b"dummy docx bytes")

    req = OCRRequest(progress_channel="chan-digital-1")
    pdf_bytes, pages_data, trust_summary = await service._execute(
        req, input_file, "sample.docx", job_id="job-digital-1"
    )

    assert pdf_bytes == b"%PDF-1.4 synthetic docx"
    assert pages_data == {
        0: ["Document Title", "First paragraph body."],
        1: ["Second page content."],
    }
    assert trust_summary is not None

    # Filter emitted frames for block_complete and page_complete
    block_and_page_frames = [
        frame
        for _, _, frame in mock_progress.emitted_frames
        if frame.get("type") in ("block_complete", "page_complete")
    ]

    expected_frames = [
        {
            "type": "block_complete",
            "page_idx": 0,
            "block_idx": 0,
            "bbox": [0.1, 0.1, 0.5, 0.3],
            "text": "Document Title",
            "kind": "heading",
            "confidence": 1.0,
        },
        {
            "type": "block_complete",
            "page_idx": 0,
            "block_idx": 1,
            "bbox": [0.1, 0.35, 0.9, 0.6],
            "text": "First paragraph body.",
            "kind": "paragraph",
            "confidence": 1.0,
        },
        {
            "type": "page_complete",
            "page_idx": 0,
        },
        {
            "type": "block_complete",
            "page_idx": 1,
            "block_idx": 0,
            "bbox": [0.1, 0.1, 0.8, 0.4],
            "text": "Second page content.",
            "kind": "paragraph",
            "confidence": 1.0,
        },
        {
            "type": "page_complete",
            "page_idx": 1,
        },
    ]

    assert block_and_page_frames == expected_frames


async def test_digital_document_fastpath_without_progress_channel_runs_cleanly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Digital document fast path completes normally without emitting frames when no progress channel is provided."""
    doc_result = DocumentResult(
        pages=[
            DocumentPage(
                page_index=0,
                blocks=[
                    DocumentBlock(
                        bbox=(0.0, 0.0, 1.0, 1.0),
                        text="Page 0 text",
                        kind="text",
                        confidence=1.0,
                    ),
                ],
                width=612,
                height=792,
            )
        ],
    )

    class _MockReader:
        def read(self, path: Path, filename: str) -> DocumentResult:
            return doc_result

    monkeypatch.setattr(
        ocr_service_mod, "get_reader_for_suffix", lambda suffix: _MockReader()
    )
    monkeypatch.setattr(
        ocr_service_mod, "render_synthetic_pdf", lambda doc: b"%PDF-1.4 synthetic"
    )

    mock_progress = _MockProgressService()
    service = _create_service(progress=mock_progress)

    work_dir = tmp_path / "work_digital_no_chan"
    work_dir.mkdir(parents=True, exist_ok=True)
    input_file = work_dir / "sample.docx"
    input_file.write_bytes(b"dummy docx bytes")

    req = OCRRequest(progress_channel=None)
    pdf_bytes, pages_data, _ = await service._execute(
        req, input_file, "sample.docx", job_id="job-digital-2"
    )

    assert pdf_bytes == b"%PDF-1.4 synthetic"
    assert pages_data == {0: ["Page 0 text"]}
    assert len(mock_progress.emitted_frames) == 0
