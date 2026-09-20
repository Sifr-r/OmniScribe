"""Tests for §3 (GroundedEngine) — per-block + per-page callbacks."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from omniscribe.core.callbacks import BlockCallbackSet
from omniscribe.core.grounded import GroundedBlock, GroundedResponse
from omniscribe.core.workflows.grounded import GroundedEngine


class _StubGroundedBackend:
    """Stub that returns a canned GroundedResponse for testing."""

    def __init__(self, blocks: list[GroundedBlock]) -> None:
        self._blocks = blocks
        self.ocr_calls: int = 0

    async def ocr_document(
        self,
        pdf_path: str,
        progress: Callable[[str, int, int, str], Awaitable[None]] | None = None,
        on_warning: Callable[[int, BaseException], Awaitable[None]] | None = None,
        *,
        pages: str | None = None,
    ) -> GroundedResponse:
        self.ocr_calls += 1
        return GroundedResponse(blocks=self._blocks, page_sizes=[(100, 100)])


def _noop_writer(_in: str, _out: str, _pages: Any, _dpi: int) -> None:
    """Output writer that discards its arguments."""
    return None


async def test_grounded_engine_emits_per_block_callbacks_in_order():
    blocks = [
        GroundedBlock(bbox=[0.0, 0.0, 0.5, 0.1], text="Hello", page_index=0),
        GroundedBlock(bbox=[0.0, 0.2, 0.5, 0.3], text="World", page_index=0),
        GroundedBlock(bbox=[0.0, 0.4, 0.5, 0.5], text="Page two", page_index=1),
    ]
    backend = _StubGroundedBackend(blocks)

    block_events: list[tuple[int, int, str]] = []
    page_events: list[int] = []

    async def on_block(
        page_idx: int,
        block_idx: int,
        _bbox: list[float],
        text: str,
        _kind: str,
        _conf: float | None,
    ) -> None:
        block_events.append((page_idx, block_idx, text))

    async def on_page_complete(page_idx: int) -> None:
        page_events.append(page_idx)

    engine = GroundedEngine(
        grounded_backend=backend,
        output_writer=_noop_writer,
        block_callbacks=BlockCallbackSet(
            on_block=on_block,
            on_page_complete=on_page_complete,
        ),
    )

    await engine.execute("in.pdf", "out.pdf", dpi=100)

    assert block_events == [
        (0, 0, "Hello"),
        (0, 1, "World"),
        (1, 0, "Page two"),
    ]
    assert page_events == [0, 1]


async def test_grounded_engine_skips_empty_blocks():
    blocks = [
        GroundedBlock(bbox=[0.0, 0.0, 0.5, 0.1], text="Real text", page_index=0),
        GroundedBlock(bbox=[0.0, 0.2, 0.5, 0.3], text="", page_index=0),
        GroundedBlock(bbox=[0.0, 0.4, 0.5, 0.5], text="   ", page_index=0),
    ]
    backend = _StubGroundedBackend(blocks)

    block_events: list[str] = []

    async def on_block(
        page_idx: int,
        block_idx: int,
        _bbox: list[float],
        text: str,
        _kind: str,
        _conf: float | None,
    ) -> None:
        block_events.append(text)

    engine = GroundedEngine(
        grounded_backend=backend,
        output_writer=_noop_writer,
        block_callbacks=BlockCallbackSet(on_block=on_block),
    )

    await engine.execute("in.pdf", "out.pdf", dpi=100)

    assert block_events == ["Real text"]


async def test_grounded_engine_no_callbacks_when_callbacks_none():
    """Default BlockCallbackSet() (both fields None) → engine runs cleanly."""
    blocks = [GroundedBlock(bbox=[0, 0, 0.5, 0.1], text="x", page_index=0)]
    backend = _StubGroundedBackend(blocks)
    engine = GroundedEngine(
        grounded_backend=backend,
        output_writer=_noop_writer,
        # block_callbacks left at default (None)
    )
    await engine.execute("in.pdf", "out.pdf", dpi=100)
    assert backend.ocr_calls == 1
