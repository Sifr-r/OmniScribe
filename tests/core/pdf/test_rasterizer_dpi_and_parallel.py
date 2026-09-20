"""Tests for the Phase 3 rasterizer/embedder changes:

- :func:`_effective_dpi` picks a DPI that targets ``max_image_dim``.
- :func:`_get_embed_font` caches the ``fitz.Font("helv")`` instance.
- :func:`convert_generator` / :func:`convert_pdf_to_images` accept a
  ``parallelism`` parameter and produce the same output whether serial
  or parallel.
"""

from __future__ import annotations

import io

import pymupdf as fitz
import pytest

from omniscribe.core.pdf.embedder_helpers import (
    _get_embed_font,
)
from omniscribe.core.pdf.rasterizer import (
    _DEFAULT_RASTERIZER_WORKERS,
    _calculate_safe_dpi,
    _check_page_cap,
    _effective_dpi,
    _max_page_cap,
    _rasterize_one_page,
    convert_batches,
    convert_generator,
    convert_pdf_to_images,
)

# --- _effective_dpi -----------------------------------------------------


class TestEffectiveDpi:
    """The Phase 3 fix: don't rasterize at 200 DPI just to throw the
    result away. The helper picks the smallest DPI that lands inside
    ``max_image_dim`` on the longest edge."""

    def test_us_letter_targets_1024_longest_edge(self):
        # US Letter: 612 x 792 pt (8.5 x 11 in). Longest = 792 pt.
        # Target dpi = 72 * 1024 / 792 = 93.
        dpi = _effective_dpi(
            width=612, height=792, requested_dpi=200, max_image_dim=1024
        )
        assert dpi == pytest.approx(93, abs=1)

    def test_a4_targets_1024_longest_edge(self):
        # A4: 595 x 842 pt. Longest = 842.
        dpi = _effective_dpi(
            width=595, height=842, requested_dpi=200, max_image_dim=1024
        )
        assert dpi == pytest.approx(87, abs=1)

    def test_caps_to_requested_dpi_when_already_small(self):
        # A tiny page that would naturally render at 200 DPI well under
        # the 1024 cap. The helper should not inflate past the user's
        # request — it only shrinks when the result would exceed
        # ``max_image_dim``.
        # 200 pt x 200 pt @ 200 DPI = 555 px longest (< 1024).
        dpi = _effective_dpi(
            width=200, height=200, requested_dpi=200, max_image_dim=1024
        )
        assert dpi == 200

    def test_floor_at_72_dpi(self):
        # max_image_dim=10 forces a tiny DPI; the helper floors at 72.
        dpi = _effective_dpi(
            width=1000, height=1000, requested_dpi=200, max_image_dim=10
        )
        assert dpi == 72

    def test_zero_or_negative_dim_falls_back_to_safe(self):
        # Defensive: a degenerate page size should not crash; it should
        # land on the memory-safe DPI from _calculate_safe_dpi.
        assert _effective_dpi(0, 0, 200, 1024) == _calculate_safe_dpi(0, 0, 200)
        assert _effective_dpi(100, 100, 200, 0) == _calculate_safe_dpi(100, 100, 200)


# --- Font cache ---------------------------------------------------------


class TestEmbedFontCache:
    """The embedder loads ``fitz.Font("helv")`` once at module level;
    subsequent calls return the same instance."""

    def test_get_embed_font_returns_singleton(self):
        f1 = _get_embed_font()
        f2 = _get_embed_font()
        assert f1 is f2

    def test_module_level_constant_references_active_font(self):
        # _EMBED_FONT starts as None and is set on first call. After
        # the previous test ran, the cache is hot.
        assert _get_embed_font() is not None


# --- Parallel rasterization --------------------------------------------


def _make_test_pdf(num_pages: int = 6) -> bytes:
    """Build a small in-memory PDF for the parallel-rasterization tests."""
    doc = fitz.open()
    try:
        for _ in range(num_pages):
            doc.new_page(width=612, height=792)  # US Letter
        buf = io.BytesIO()
        doc.save(buf)
        return buf.getvalue()
    finally:
        doc.close()


class TestParallelRasterization:
    """Both ``convert_generator`` and ``convert_pdf_to_images`` must
    produce identical output regardless of ``parallelism``."""

    def test_generator_pages_in_order_serial_vs_parallel(self):
        pdf_bytes = _make_test_pdf(num_pages=8)
        serial = list(
            convert_generator(pdf_bytes, dpi=200, max_image_dim=1024, parallelism=1)
        )
        parallel = list(
            convert_generator(pdf_bytes, dpi=200, max_image_dim=1024, parallelism=4)
        )
        # Order is preserved by the thread-pool fan-out.
        assert [p[0] for p in serial] == [p[0] for p in parallel] == list(range(8))
        # Each page's base64 payload should be byte-identical regardless
        # of parallelism (rasterization is deterministic per page).
        for s, p in zip(serial, parallel, strict=True):
            assert s[2] == p[2]
            assert s[1].size == p[1].size

    def test_eager_dict_identical_serial_vs_parallel(self, tmp_path):
        pdf_path = tmp_path / "fixture.pdf"
        pdf_path.write_bytes(_make_test_pdf(num_pages=5))
        serial = convert_pdf_to_images(
            pdf_path, dpi=200, max_image_dim=1024, parallelism=1
        )
        parallel = convert_pdf_to_images(
            pdf_path, dpi=200, max_image_dim=1024, parallelism=4
        )
        assert serial.keys() == parallel.keys()
        for k in serial:
            assert serial[k] == parallel[k]

    def test_parallelism_one_uses_serial_path(self):
        # Sanity: parallelism=1 should yield through the same path as
        # the old behaviour (no thread pool creation at all).
        pdf_bytes = _make_test_pdf(num_pages=3)
        out = list(convert_generator(pdf_bytes, parallelism=1))
        assert [p[0] for p in out] == [0, 1, 2]

    def test_convert_batches_respects_parallelism(self):
        pdf_bytes = _make_test_pdf(num_pages=6)
        # batch_size=2, parallelism=2 → first batch holds pages 0+1.
        batches = list(
            convert_batches(
                pdf_bytes, batch_size=2, dpi=200, max_image_dim=1024, parallelism=2
            )
        )
        assert [len(b) for b in batches] == [2, 2, 2]
        assert [p[0] for b in batches for p in b] == [0, 1, 2, 3, 4, 5]


# --- Per-page helper ----------------------------------------------------


class TestRasterizeOnePage:
    """``_rasterize_one_page`` is the leaf worker that runs inside the
    thread pool. It must produce a (page_num, PIL.Image, b64_str) triple
    that matches the previous in-line behaviour."""

    def test_produces_three_tuple(self):
        doc = fitz.open()
        try:
            doc.new_page(width=300, height=400)
            page_num, img, b64 = _rasterize_one_page(doc, 0, dpi=150, max_image_dim=512)
        finally:
            doc.close()
        assert page_num == 0
        assert img.size[0] <= 512 and img.size[1] <= 512
        assert isinstance(b64, str) and len(b64) > 0


# --- Default workers from env ------------------------------------------


def test_default_rasterizer_workers_is_positive():
    # Sanity on the env-derived default so a bad env var never breaks
    # the import path or yields a pool of size 0.
    assert _DEFAULT_RASTERIZER_WORKERS >= 1


# --- Audit P2-9: hard page-count cap ------------------------------------


class TestPageCap:
    """``OMNISCRIBE_MAX_PAGES`` bounds the pages a run rasterizes."""

    def test_default_cap_is_500(self, monkeypatch):
        monkeypatch.delenv("OMNISCRIBE_MAX_PAGES", raising=False)
        assert _max_page_cap() == 500

    def test_zero_and_garbage_disable_cap(self, monkeypatch):
        monkeypatch.setenv("OMNISCRIBE_MAX_PAGES", "0")
        assert _max_page_cap() == 0
        monkeypatch.setenv("OMNISCRIBE_MAX_PAGES", "not-a-number")
        assert _max_page_cap() == 0

    def test_check_raises_over_cap(self, monkeypatch):
        monkeypatch.setenv("OMNISCRIBE_MAX_PAGES", "10")
        with pytest.raises(ValueError, match="exceeds the configured maximum"):
            _check_page_cap(11)
        _check_page_cap(10)  # at the cap is fine

    def test_convert_pdf_to_images_enforces_cap(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OMNISCRIBE_MAX_PAGES", "2")
        pdf_bytes = _make_test_pdf(num_pages=3)
        path = tmp_path / "big.pdf"
        path.write_bytes(pdf_bytes)
        with pytest.raises(ValueError, match="exceeds the configured maximum"):
            convert_pdf_to_images(str(path), dpi=72)

    def test_page_range_selection_under_cap_still_allowed(self, monkeypatch, tmp_path):
        # Cap applies after page-range selection: 2 pages of a 3-page
        # document stays under a cap of 2.
        monkeypatch.setenv("OMNISCRIBE_MAX_PAGES", "2")
        pdf_bytes = _make_test_pdf(num_pages=3)
        path = tmp_path / "subset.pdf"
        path.write_bytes(pdf_bytes)
        out = list(convert_generator(str(path), dpi=72, pages="1-2"))
        assert [p[0] for p in out] == [0, 1]

    def test_cap_disabled_allows_large_documents(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OMNISCRIBE_MAX_PAGES", "0")
        pdf_bytes = _make_test_pdf(num_pages=3)
        path = tmp_path / "uncapped.pdf"
        path.write_bytes(pdf_bytes)
        images = convert_pdf_to_images(str(path), dpi=72)
        assert len(images) == 3

    def test_convert_pdf_to_images_respects_pages_param(self, tmp_path):
        pdf_bytes = _make_test_pdf(num_pages=5)
        path = tmp_path / "five_pages.pdf"
        path.write_bytes(pdf_bytes)

        images = convert_pdf_to_images(str(path), dpi=72, pages="2,4")
        assert list(images.keys()) == [1, 3]

    def test_pdf_handler_convert_forwards_pages_param(self, tmp_path):
        from omniscribe.core.pdf import PDFHandler

        pdf_bytes = _make_test_pdf(num_pages=5)
        path = tmp_path / "five_pages.pdf"
        path.write_bytes(pdf_bytes)

        handler = PDFHandler()
        images = handler.convert(str(path), dpi=72, pages="1,3,5")
        assert list(images.keys()) == [0, 2, 4]

        images_to = handler.convert_to_images(str(path), dpi=72, pages="2")
        assert list(images_to.keys()) == [1]
