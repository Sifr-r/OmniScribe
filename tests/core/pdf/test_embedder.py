"""Tests for PDF embedder compression parameters and page_nums handling (Section 6.30, Section 6.31)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pymupdf as fitz
import pytest

from omniscribe.core.pdf import embedder_helpers
from omniscribe.core.pdf.embedder import embed_structured_text


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    """Create a 3-page test PDF."""
    pdf_path = tmp_path / "test_doc.pdf"
    doc = fitz.open()
    for i in range(3):
        page = doc.new_page(width=300, height=400)
        page.insert_text((50, 50), f"Page {i + 1} test text")
    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


def test_embed_structured_text_passes_garbage_and_deflate(
    sample_pdf: Path, tmp_path: Path
) -> None:
    """Section 6.30: new_doc.save must be called with garbage=3, deflate=True."""
    output_pdf = tmp_path / "output.pdf"
    pages_data = {0: [((0.1, 0.1, 0.9, 0.2), "Hello World")]}

    original_save = fitz.Document.save
    save_calls: list[dict[str, object]] = []

    def tracking_save(self: fitz.Document, filename: str, **kwargs: object) -> None:
        save_calls.append(kwargs)
        original_save(self, filename, **kwargs)

    with patch.object(fitz.Document, "save", side_effect=tracking_save, autospec=True):
        embed_structured_text(
            sample_pdf,
            output_pdf,
            pages_data,
            dpi=72,
            page_nums=[0],
        )

    assert len(save_calls) == 1
    assert save_calls[0].get("garbage") == 3
    assert save_calls[0].get("deflate") is True
    assert output_pdf.exists()


def test_embed_structured_text_empty_page_nums_passes_garbage_and_deflate(
    sample_pdf: Path, tmp_path: Path
) -> None:
    """Section 6.30 and Section 6.31: empty or all-out-of-bounds page_nums triggers early save with garbage/deflate."""
    output_pdf = tmp_path / "output_empty.pdf"
    save_calls: list[dict[str, object]] = []

    def tracking_save(*args: object, **kwargs: object) -> None:
        save_calls.append(kwargs)

    with patch.object(fitz.Document, "save", side_effect=tracking_save):
        embed_structured_text(
            sample_pdf,
            output_pdf,
            {},
            page_nums=[999, -5],
        )

    assert len(save_calls) == 1
    assert save_calls[0].get("garbage") == 3
    assert save_calls[0].get("deflate") is True


def test_embed_from_image_input_passes_garbage_and_deflate(tmp_path: Path) -> None:
    """Section 6.30: the image-input sandwich path must compact like the
    PDF branch (garbage=3, deflate=True)."""
    from PIL import Image

    image_path = tmp_path / "input.png"
    Image.new("RGB", (64, 32), color=(255, 255, 255)).save(image_path)
    output_pdf = tmp_path / "output_image.pdf"
    pages_data = {0: [((0.1, 0.1, 0.9, 0.2), "Hello World")]}

    original_save = fitz.Document.save
    save_calls: list[dict[str, object]] = []

    def tracking_save(self: fitz.Document, filename: str, **kwargs: object) -> None:
        save_calls.append(kwargs)
        original_save(self, filename, **kwargs)

    with patch.object(fitz.Document, "save", side_effect=tracking_save, autospec=True):
        embedder_helpers._embed_from_image_input(image_path, output_pdf, pages_data)

    assert len(save_calls) == 1
    assert save_calls[0].get("garbage") == 3
    assert save_calls[0].get("deflate") is True
    assert output_pdf.exists()


def test_embed_structured_text_unified_page_nums_filter(
    sample_pdf: Path, tmp_path: Path
) -> None:
    """Section 6.31: page_nums filter properly strips invalid indices while keeping valid ones."""
    output_pdf = tmp_path / "output_filtered.pdf"
    pages_data = {
        0: [((0.1, 0.1, 0.5, 0.2), "Page 0 text")],
        2: [((0.1, 0.1, 0.5, 0.2), "Page 2 text")],
    }

    embed_structured_text(
        sample_pdf,
        output_pdf,
        pages_data,
        dpi=72,
        page_nums=[-1, 0, 2, 100],
    )

    assert output_pdf.exists()
    out_doc = fitz.open(str(output_pdf))
    assert len(out_doc) == 2
    out_doc.close()


def test_embedder_helpers_docstring_trimmed() -> None:
    """Section 4.26: Outdated 470-LOC split notes are trimmed from embedder_helpers docstring."""
    doc = embedder_helpers.__doc__ or ""
    assert "470 LOC" not in doc
    assert "Sprint 6 long-file split" not in doc
