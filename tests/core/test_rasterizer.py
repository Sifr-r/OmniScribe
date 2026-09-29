"""Tests for PDF rasterizer serial rendering and sequential order preservation."""

from __future__ import annotations

import concurrent.futures
import io
from unittest.mock import patch

import pymupdf as fitz

from omniscribe.core.pdf.rasterizer import (
    convert_generator,
)


def _make_test_pdf(num_pages: int = 4) -> bytes:
    doc = fitz.open()
    try:
        for i in range(num_pages):
            page = doc.new_page(width=300, height=400)
            page.insert_text((50, 50), f"Page {i}")
        buf = io.BytesIO()
        doc.save(buf)
        return buf.getvalue()
    finally:
        doc.close()


def test_rasterizer_renders_serially_without_thread_pool_and_preserves_order() -> None:
    """When parallelism > 1 and len(page_nums) > 1, no ThreadPoolExecutor is spawned and sequential order is preserved."""
    pdf_bytes = _make_test_pdf(num_pages=6)

    with patch.object(
        concurrent.futures,
        "ThreadPoolExecutor",
        wraps=concurrent.futures.ThreadPoolExecutor,
    ) as mock_executor:
        results = list(convert_generator(pdf_bytes, parallelism=3))

        mock_executor.assert_not_called()
        assert [r[0] for r in results] == [0, 1, 2, 3, 4, 5]
        for _idx, img, b64 in results:
            assert img.width > 0 and img.height > 0
            assert isinstance(b64, str) and len(b64) > 0


def test_rasterizer_serial_when_parallelism_one() -> None:
    """When parallelism <= 1, no ThreadPoolExecutor is spawned."""
    pdf_bytes = _make_test_pdf(num_pages=3)

    with patch.object(
        concurrent.futures,
        "ThreadPoolExecutor",
        wraps=concurrent.futures.ThreadPoolExecutor,
    ) as mock_executor:
        results = list(convert_generator(pdf_bytes, parallelism=1))

        mock_executor.assert_not_called()
        assert [r[0] for r in results] == [0, 1, 2]


def test_rasterizer_serial_when_single_page() -> None:
    """When len(page_nums) <= 1, no ThreadPoolExecutor is spawned even if parallelism > 1."""
    pdf_bytes = _make_test_pdf(num_pages=1)

    with patch.object(
        concurrent.futures,
        "ThreadPoolExecutor",
        wraps=concurrent.futures.ThreadPoolExecutor,
    ) as mock_executor:
        results = list(convert_generator(pdf_bytes, parallelism=4))

        mock_executor.assert_not_called()
        assert [r[0] for r in results] == [0]


def test_rasterizer_parallel_and_serial_produce_identical_images() -> None:
    """Verify convert_generator produces identical sequential output regardless of parallelism without thread pool concurrency."""
    pdf_bytes = _make_test_pdf(num_pages=4)
    with patch.object(
        concurrent.futures,
        "ThreadPoolExecutor",
        wraps=concurrent.futures.ThreadPoolExecutor,
    ) as mock_executor:
        serial = list(convert_generator(pdf_bytes, parallelism=1))
        parallel = list(convert_generator(pdf_bytes, parallelism=4))

        mock_executor.assert_not_called()
        assert len(serial) == len(parallel) == 4
        for s, p in zip(serial, parallel, strict=True):
            assert s[0] == p[0]
            assert s[1].size == p[1].size
            assert s[2] == p[2]
