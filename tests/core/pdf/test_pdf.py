"""
PDFHandler tests using the on-disk example PDFs.

These validate the conversion → embed round trip without hitting Surya or
the LLM. No model loads, so they run in well under a second.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from unittest.mock import patch

import pymupdf as fitz
import pytest

from omniscribe.core.pdf import PDFHandler


@pytest.fixture
def pdf_handler() -> PDFHandler:
    return PDFHandler()


@pytest.mark.parametrize("name", ["digital.pdf", "hybrid.pdf", "handwritten.pdf"])
def test_convert_to_images_produces_base64_per_page(
    pdf_handler: PDFHandler, example_pdfs: dict[str, Path], name: str
):
    images = pdf_handler.convert_to_images(str(example_pdfs[name]))

    # At least one page, indexed from 0, contiguous.
    assert images, f"{name}: no images returned"
    assert min(images) == 0
    assert list(images.keys()) == sorted(images.keys())

    # Each value should be decodable base64 of a non-empty image.
    for page_num, b64 in images.items():
        raw = base64.b64decode(b64)
        assert len(raw) > 256, (
            f"{name} page {page_num}: suspiciously small ({len(raw)} bytes)"
        )


def test_embed_structured_text_produces_searchable_pdf(
    pdf_handler: PDFHandler, example_pdfs: dict[str, Path], tmp_path: Path
):
    """Embed a known text marker and verify it's recoverable via get_text."""
    input_pdf = str(example_pdfs["digital.pdf"])
    output_pdf = str(tmp_path / "out.pdf")

    marker = "ZZUNIQUEMARKERZZ"
    pages_data = {0: [((0.1, 0.1, 0.6, 0.15), marker)]}

    pdf_handler.embed_structured_text(input_pdf, output_pdf, pages_data, dpi=150)

    doc = fitz.open(output_pdf)
    try:
        text = doc[0].get_text("text")
    finally:
        doc.close()
    assert marker in text, (
        f"expected marker {marker!r} in embedded text; got {text[:200]!r}"
    )


def test_embed_handles_empty_text_gracefully(
    pdf_handler: PDFHandler, example_pdfs: dict[str, Path], tmp_path: Path
):
    input_pdf = str(example_pdfs["digital.pdf"])
    output_pdf = str(tmp_path / "out_empty.pdf")
    # A real pipeline output: one box with text, one empty (DP-skipped).
    pages_data = {
        0: [
            ((0.1, 0.1, 0.6, 0.15), "hello world"),
            ((0.1, 0.2, 0.6, 0.25), ""),
        ]
    }
    pdf_handler.embed_structured_text(input_pdf, output_pdf, pages_data, dpi=150)

    doc = fitz.open(output_pdf)
    try:
        text = doc[0].get_text("text")
    finally:
        doc.close()
    assert "hello world" in text


@pytest.mark.parametrize("name", ["digital.pdf", "hybrid.pdf", "handwritten.pdf"])
def test_output_preserves_page_count(
    pdf_handler: PDFHandler, example_pdfs: dict[str, Path], tmp_path: Path, name: str
):
    input_pdf = str(example_pdfs[name])
    output_pdf = str(tmp_path / f"out_{name}")

    with fitz.open(input_pdf) as src:
        src_pages = len(src)

    # No text to embed — still a valid sandwich PDF of the same length.
    pdf_handler.embed_structured_text(input_pdf, output_pdf, {}, dpi=100)

    with fitz.open(output_pdf) as out:
        assert len(out) == src_pages


def _make_multipage_pdf(path: Path, n_pages: int = 3) -> Path:
    doc = fitz.open()
    for _ in range(n_pages):
        doc.new_page(width=200, height=300)
    doc.save(str(path))
    doc.close()
    return path


def test_embed_page_nums_renders_subset_only(pdf_handler: PDFHandler, tmp_path: Path):
    """Audit P2-9: subset runs must not rasterize the whole document."""
    input_pdf = str(_make_multipage_pdf(tmp_path / "three.pdf", 3))
    output_pdf = str(tmp_path / "subset.pdf")

    pages_data = {
        0: [((0.1, 0.1, 0.9, 0.2), "MARK_FIRST")],
        2: [((0.1, 0.1, 0.9, 0.2), "MARK_THIRD")],
    }
    pdf_handler.embed_structured_text(
        input_pdf, output_pdf, pages_data, dpi=72, page_nums=[0, 2]
    )

    with fitz.open(output_pdf) as out:
        assert len(out) == 2
        assert "MARK_FIRST" in out[0].get_text("text")
        assert "MARK_THIRD" in out[1].get_text("text")


def test_embed_page_nums_filters_multiframe_image(
    pdf_handler: PDFHandler, tmp_path: Path
):
    input_tiff = _make_multiframe_tiff(tmp_path / "scan.tif", n_frames=3)
    output_pdf = str(tmp_path / "subset_tiff.pdf")

    pages_data = {1: [((0.1, 0.1, 0.9, 0.2), "MARK_MIDDLE")]}
    pdf_handler.embed_structured_text(
        str(input_tiff), output_pdf, pages_data, dpi=72, page_nums=[1]
    )

    with fitz.open(output_pdf) as out:
        assert len(out) == 1
        assert "MARK_MIDDLE" in out[0].get_text("text")


# --- raw image inputs (JPEG / PNG / multi-page TIFF) -----------------------


def _make_image_file(path: Path, size=(800, 1000), mode="RGB") -> Path:
    """Write a simple JPEG/PNG/TIFF with some content to `path`."""
    from PIL import Image, ImageDraw

    img = Image.new(mode, size, "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle((100, 200, 700, 300), fill="lightgray")
    fmt = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "tif": "TIFF", "tiff": "TIFF"}[
        path.suffix.lower().lstrip(".")
    ]
    img.save(path, format=fmt)
    return path


def _make_multiframe_tiff(path: Path, n_frames: int = 3, size=(600, 800)) -> Path:
    from PIL import Image, ImageDraw

    frames = []
    for i in range(n_frames):
        img = Image.new("RGB", size, "white")
        ImageDraw.Draw(img).text((50, 50), f"Page {i + 1}", fill="black")
        frames.append(img)
    frames[0].save(path, format="TIFF", save_all=True, append_images=frames[1:])
    return path


@pytest.mark.parametrize("ext", ["jpg", "png", "tif"])
def test_convert_image_file_to_single_page(
    pdf_handler: PDFHandler, tmp_path: Path, ext: str
):
    src = _make_image_file(tmp_path / f"scan.{ext}")
    images = pdf_handler.convert_to_images(str(src))
    assert list(images.keys()) == [0]

    import base64
    import io

    from PIL import Image as PILImage

    raw = base64.b64decode(images[0])
    img = PILImage.open(io.BytesIO(raw))
    assert img.size[0] > 0 and img.size[1] > 0


def test_convert_multiframe_tiff(pdf_handler: PDFHandler, tmp_path: Path):
    src = _make_multiframe_tiff(tmp_path / "pages.tif", n_frames=4)
    images = pdf_handler.convert_to_images(str(src))
    assert sorted(images.keys()) == [0, 1, 2, 3]


def test_embed_into_image_input_produces_searchable_pdf(
    pdf_handler: PDFHandler, tmp_path: Path
):
    """Image → sandwich PDF round-trip: marker must be extractable."""
    src = _make_image_file(tmp_path / "scan.png")
    output_pdf = str(tmp_path / "out.pdf")

    marker = "IMAGEMARKERXYZ"
    pages_data = {0: [((0.15, 0.35, 0.55, 0.40), marker)]}
    pdf_handler.embed_structured_text(str(src), output_pdf, pages_data)

    with fitz.open(output_pdf) as doc:
        assert len(doc) == 1
        assert marker in doc[0].get_text("text")


def test_embed_multiframe_tiff_produces_multipage_pdf(
    pdf_handler: PDFHandler, tmp_path: Path
):
    src = _make_multiframe_tiff(tmp_path / "multi.tiff", n_frames=3)
    output_pdf = str(tmp_path / "multi_out.pdf")

    pages_data = {
        0: [((0.1, 0.1, 0.4, 0.15), "MARKERPAGEONE")],
        2: [((0.1, 0.1, 0.4, 0.15), "MARKERPAGETHREE")],
    }
    pdf_handler.embed_structured_text(str(src), output_pdf, pages_data)

    with fitz.open(output_pdf) as doc:
        assert len(doc) == 3
        assert "MARKERPAGEONE" in doc[0].get_text("text")
        assert doc[1].get_text("text").strip() == ""  # no text embedded on page 2
        assert "MARKERPAGETHREE" in doc[2].get_text("text")


def test_image_extension_detection():
    from omniscribe.core.pdf import _is_image_path

    assert _is_image_path("scan.jpg")
    assert _is_image_path("SCAN.JPEG")
    assert _is_image_path("pages.tiff")
    assert _is_image_path("page.png")
    assert _is_image_path("photo.webp")
    assert _is_image_path("photo.avif")
    assert _is_image_path("PHOTO.AVIF")
    assert not _is_image_path("doc.pdf")
    assert not _is_image_path("doc.PDF")
    assert not _is_image_path("notes.txt")


def test_avif_input_round_trip(pdf_handler: PDFHandler, tmp_path: Path):
    """AVIF input must decode and embed end-to-end (pillow-avif-plugin)."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (800, 1000), "white")
    ImageDraw.Draw(img).rectangle((100, 200, 700, 300), fill="lightgray")
    src = tmp_path / "scan.avif"
    img.save(src, format="AVIF", quality=80)

    images = pdf_handler.convert_to_images(str(src))
    assert list(images.keys()) == [0]

    output_pdf = str(tmp_path / "out.pdf")
    marker = "AVIFMARKERZZ"
    pdf_handler.embed_structured_text(
        str(src), output_pdf, {0: [((0.15, 0.35, 0.55, 0.40), marker)]}
    )
    with fitz.open(output_pdf) as doc:
        assert len(doc) == 1
        assert marker in doc[0].get_text("text")


# --- Audit P0-3: Unicode-capable embed font chain --------------------------


class TestEmbedUnicodeFontChain:
    """The searchable layer must survive non-Latin scripts.

    ``helv`` (WinAnsi) silently dropped Arabic/CJK glyphs. These tests
    pin the font-chain behavior: Latin stays on helv (zero size cost),
    non-Latin moves to a Unicode chain font, and characters no chain
    font covers are dropped rather than written as U+0000.
    """

    def _embed_and_extract(
        self, pdf_handler: PDFHandler, tmp_path: Path, text: str
    ) -> tuple[str, fitz.Document]:
        from PIL import Image

        src = tmp_path / "in.png"
        Image.new("RGB", (800, 600), "white").save(src)
        out = tmp_path / "out.pdf"
        pdf_handler.embed_structured_text(
            str(src), str(out), {0: [((0.05, 0.2, 0.95, 0.4), text)]}
        )
        doc = fitz.open(str(out))
        return doc[0].get_text("text"), doc

    def test_latin_only_embeds_no_unicode_font(
        self, pdf_handler: PDFHandler, tmp_path: Path
    ):
        text, doc = self._embed_and_extract(pdf_handler, tmp_path, "Hello Latin only")
        try:
            assert "Hello Latin only" in text
            # Pure-Latin runs stay on Base-14 helv; no chain font alias
            # should be registered on the page.
            font_names = [f[3] for f in doc[0].get_fonts()]
            assert not any("omni-embed-uni" in n for n in font_names)
        finally:
            doc.close()

    def test_cjk_text_survives_embed_round_trip(
        self, pdf_handler: PDFHandler, tmp_path: Path, monkeypatch
    ):
        from omniscribe.core.pdf import embedder_helpers as embedder

        monkeypatch.setattr(embedder, "_UNICODE_CHAIN", (fitz.Font("cjk"),))
        text, doc = self._embed_and_extract(pdf_handler, tmp_path, "中文测试")
        try:
            assert "中文测试" in text
            assert "\x00" not in text
        finally:
            doc.close()

    def test_arabic_actual_text_survives_without_covering_font(
        self, pdf_handler: PDFHandler, tmp_path: Path, monkeypatch
    ):
        # The scanned image supplies glyphs. ActualText must retain logical
        # Arabic even when the host's font chain cannot render Arabic.
        from omniscribe.core.pdf import embedder_helpers as embedder

        monkeypatch.setattr(embedder, "_UNICODE_CHAIN", (fitz.Font("cjk"),))
        text, doc = self._embed_and_extract(pdf_handler, tmp_path, "مرحبا")
        try:
            assert "\x00" not in text
            assert "مرحبا" in text
            assert doc[0].search_for("مرحبا")
        finally:
            doc.close()

    def test_pick_embed_font_prefers_helv_for_latin(self, monkeypatch):
        from omniscribe.core.pdf import embedder_helpers as embedder

        cjk = fitz.Font("cjk")
        monkeypatch.setattr(embedder, "_UNICODE_CHAIN", (cjk,))
        alias, font = embedder._pick_embed_font("plain latin")
        assert alias == "helv"
        alias, font = embedder._pick_embed_font("中文测试")
        assert alias.startswith("omni-embed-uni")
        assert font is cjk


def test_probe_codepoints_includes_persian_peh():
    from omniscribe.core.pdf.embedder_helpers import _PROBE_CODEPOINTS

    assert ord("\u067e") in _PROBE_CODEPOINTS
    assert 0x067E in _PROBE_CODEPOINTS


def test_log_once_helper(caplog: pytest.LogCaptureFixture):
    from omniscribe.core.pdf.embedder_helpers import _LOGGED_ONCE_KEYS, _log_once

    _LOGGED_ONCE_KEYS.clear()
    with caplog.at_level(logging.WARNING):
        _log_once("test message %d", 1, key="test_key")
        _log_once("test message %d", 2, key="test_key")
        _log_once("distinct message %d", 3, key="other_key")
    messages = [rec.message for rec in caplog.records if "test message" in rec.message]
    assert len(messages) == 1
    assert messages[0] == "test message 1"
    other_messages = [
        rec.message for rec in caplog.records if "distinct message" in rec.message
    ]
    assert len(other_messages) == 1
    assert other_messages[0] == "distinct message 3"


def test_font_probe_failure_warning_avoids_exc_info(caplog: pytest.LogCaptureFixture):
    from omniscribe.core.pdf import embedder_helpers

    class _StubFont:
        buffer = b"fake-font-buffer"

        def has_glyph(self, cp: int) -> bool:
            return True

    with caplog.at_level(logging.WARNING):
        with patch.object(
            embedder_helpers.fitz, "open", side_effect=RuntimeError("probe error")
        ):
            result = embedder_helpers._font_preserves_codepoints(_StubFont())  # type: ignore[arg-type]
    assert result is True
    probe_records = [
        r for r in caplog.records if "Embedder font probe failed" in r.message
    ]
    assert len(probe_records) == 1
    assert probe_records[0].exc_info is None
