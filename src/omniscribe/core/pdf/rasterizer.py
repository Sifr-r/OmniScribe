"""
Rasterizer module for PDF and image inputs.

Provides PyMuPDF AGPL licensing warning emission, safe DPI calculations,
image extension validation, and rasterization of PDF pages and images
(JPEG, PNG, BMP, WebP, TIFF, AVIF) into base64 JPEGs.

The module provides safe serial rasterization of PDF pages and images
into base64 JPEGs. PyMuPDF documents are not thread-safe, so per-page
rendering is performed serially.
"""

from __future__ import annotations

import base64
import io
import logging
import os
import threading
from collections.abc import Iterator
from pathlib import Path

import pymupdf as fitz  # PyMuPDF
from PIL import Image, ImageSequence

from omniscribe.core.pdf.page_range import (
    parse_page_range_with_total as parse_page_range,
)

_LOGGER = logging.getLogger(__name__)

# Retained for backward-compatibility in API signatures. PyMuPDF Document
# instances are not thread-safe across threads (MuPDF native state can
# corrupt or race), so page rasterization is performed serially over `doc`.
# Operators may still configure OMNISCRIBE_RASTERIZER_WORKERS without error.
_DEFAULT_RASTERIZER_WORKERS = max(
    1, min(8, int(os.getenv("OMNISCRIBE_RASTERIZER_WORKERS", "4")))
)

# E5: this flag guards the one-shot PyMuPDF AGPL notice. The read-modify-
# write sequence in ``_emit_pymupdf_agpl_notice`` is protected by a
# ``threading.Lock`` so concurrent first-use across workers emits the
# notice at most once per process. The log call itself is OUTSIDE the
# lock so the lock isn't held during I/O.
_PYMUPDF_AGPL_NOTICE_EMITTED = False
_AGPL_NOTICE_LOCK = threading.Lock()


def _emit_pymupdf_agpl_notice() -> None:
    global _PYMUPDF_AGPL_NOTICE_EMITTED
    with _AGPL_NOTICE_LOCK:
        if _PYMUPDF_AGPL_NOTICE_EMITTED:
            return
        _PYMUPDF_AGPL_NOTICE_EMITTED = True
    _LOGGER.warning(
        "OmniScribe is built on PyMuPDF (Artifex Software). PyMuPDF is "
        "AGPL-3.0; if you distribute this binary or a non-AGPL derivative "
        "to anyone outside your organization you may owe Artifex a "
        "commercial license. See README > Third-Party Software Notices."
    )


IMAGE_EXTENSIONS = frozenset(
    {
        ".jpg",
        ".jpeg",
        ".png",
        ".tif",
        ".tiff",
        ".bmp",
        ".webp",
        ".avif",
    }
)

VLM_JPEG_QUALITY_PDF_PATH: int
VLM_JPEG_QUALITY_GROUNDED: int
EMBED_JPEG_QUALITY_PDF: int
EMBED_JPEG_QUALITY_IMAGE: int

MAX_SAFE_PIXELS: int

# Resolve the tunables from env once at import time. The previous
# hardcoded values are kept as the defaults in
# ``RasterizationSettings`` so behaviour is unchanged when no env vars
# are set; operators can override via ``OMNISCRIBE_RASTERIZER_*``.
# See deep-refactor audit §4.7 (preserved in Git history).
from omniscribe.core.pdf.rasterization_settings import (  # noqa: E402
    RasterizationSettings as _RasterizationSettings,
)

_rasterization_settings = _RasterizationSettings.from_env()
VLM_JPEG_QUALITY_PDF_PATH = _rasterization_settings.vlm_jpeg_quality_pdf_path
VLM_JPEG_QUALITY_GROUNDED = _rasterization_settings.vlm_jpeg_quality_grounded
EMBED_JPEG_QUALITY_PDF = _rasterization_settings.embed_jpeg_quality_pdf
EMBED_JPEG_QUALITY_IMAGE = _rasterization_settings.embed_jpeg_quality_image

MAX_SAFE_PIXELS = _rasterization_settings.max_safe_pixels


def _is_image_path(path: str | Path) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXTENSIONS


def _max_page_cap() -> int:
    """Hard total page-count cap per run (audit P2-9).

    Chunk size was already bounded (500) but document size was not — an
    unbounded document can hold the worker for hours and exhaust memory
    in the eager paths. Reads ``OMNISCRIBE_MAX_PAGES`` at call time
    (default 500); a non-positive or unparseable value disables the cap.
    """
    raw = os.getenv("OMNISCRIBE_MAX_PAGES", "500")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 0
    return max(0, value)


def _check_page_cap(page_count: int) -> None:
    """Raise ``ValueError`` when ``page_count`` exceeds the configured cap.

    Applied to the number of pages a run will actually rasterize (after
    any page-range selection), so a 3-page selection of a huge document
    stays allowed while full-document runs of huge documents fail fast
    with a stable, user-actionable message.
    """
    cap = _max_page_cap()
    if cap and page_count > cap:
        raise ValueError(
            f"Document requires rasterizing {page_count} page(s), which "
            f"exceeds the configured maximum of {cap}. Narrow the page "
            f"range or raise OMNISCRIBE_MAX_PAGES."
        )


def _calculate_safe_dpi(width: float, height: float, requested_dpi: int) -> int:
    """Cap DPI to prevent PyMuPDF OOM on massive pages (e.g. blueprints)."""
    page_area = width * height
    page_pixels = page_area * (requested_dpi / 72) ** 2

    if page_pixels <= MAX_SAFE_PIXELS:
        return requested_dpi

    if page_area <= 0:
        return 72

    safe_dpi = int(72 * (MAX_SAFE_PIXELS / page_area) ** 0.5)
    return max(72, min(requested_dpi, safe_dpi))


def _effective_dpi(
    width: float, height: float, requested_dpi: int, max_image_dim: int
) -> int:
    """Choose a DPI that targets ``max_image_dim`` on the longest edge.

    The previous pipeline asked PyMuPDF for ``dpi=200`` and then ran
    ``Image.thumbnail((1024, 1024))`` to squash the result — wasting
    ~3-4x the rasterization CPU and peak memory. This helper picks the
    smallest DPI that produces an image whose longest edge is already
    ``<= max_image_dim``, so the downstream ``thumbnail`` is a no-op.

    Falls back to :func:`_calculate_safe_dpi` for the memory cap.
    """
    if width <= 0 or height <= 0 or max_image_dim <= 0:
        return _calculate_safe_dpi(width, height, requested_dpi)

    longest_pt = max(width, height)
    target_dpi = int(72 * max_image_dim / longest_pt)
    # Don't go below 72 — below that text becomes unreadable in the
    # embedded image even after the VLM sees it fine.
    target_dpi = max(72, min(requested_dpi, target_dpi))
    return _calculate_safe_dpi(width, height, target_dpi)


def _images_from_image_file(path: str | Path, max_image_dim: int) -> dict[int, str]:
    """Load a JPEG/PNG/TIFF/BMP; multi-frame TIFFs become multiple pages."""
    images: dict[int, str] = {}
    with Image.open(path) as src:
        for page_num, frame in enumerate(ImageSequence.Iterator(src)):
            img = frame.convert("RGB")
            img.thumbnail((max_image_dim, max_image_dim))
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=VLM_JPEG_QUALITY_PDF_PATH)
            images[page_num] = base64.b64encode(buffer.getvalue()).decode("utf-8")
    return images


def _generator_from_image_source(
    source: str | bytes | Path,
    pages: str | None,
    max_image_dim: int,
) -> Iterator[tuple[int, Image.Image, str]]:
    if isinstance(source, (str, Path)):
        src_context = Image.open(source)
    else:
        src_context = Image.open(io.BytesIO(source))

    with src_context as src:
        total_pages = getattr(src, "n_frames", 1)
        selected_pages: set[int] | None = None
        if pages:
            selected_pages = set(parse_page_range(pages, total_pages))
        _check_page_cap(
            len(selected_pages) if selected_pages is not None else total_pages
        )

        for page_num in range(total_pages):
            if selected_pages is not None and page_num not in selected_pages:
                continue
            src.seek(page_num)
            img = src.convert("RGB")
            img.thumbnail((max_image_dim, max_image_dim))
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=VLM_JPEG_QUALITY_PDF_PATH)
            b64_str = base64.b64encode(buffer.getvalue()).decode("utf-8")
            yield page_num, img, b64_str


def _rasterize_one_page(
    doc: fitz.Document,
    page_num: int,
    dpi: int,
    max_image_dim: int,
) -> tuple[int, Image.Image, str]:
    """Rasterize a single page of an open :class:`fitz.Document`."""
    page = doc[page_num]
    effective = _effective_dpi(page.rect.width, page.rect.height, dpi, max_image_dim)
    pix = page.get_pixmap(dpi=effective)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    # Defensive: the tuned DPI should already land <= max_image_dim, but
    # an exotic page ratio can still push one edge a pixel over. A cheap
    # thumbnail is cheaper than re-rasterizing.
    img.thumbnail((max_image_dim, max_image_dim))

    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=VLM_JPEG_QUALITY_PDF_PATH)
    b64_str = base64.b64encode(buffer.getvalue()).decode("utf-8")
    return page_num, img, b64_str


def _generator_from_pdf_source(
    source: str | bytes | Path,
    dpi: int,
    pages: str | None,
    max_image_dim: int,
    parallelism: int = _DEFAULT_RASTERIZER_WORKERS,
) -> Iterator[tuple[int, Image.Image, str]]:
    """Stream rasterized pages serially from a PDF source.

    Note: PyMuPDF documents are not thread-safe across threads (see
    https://pymupdf.readthedocs.io/en/latest/recipes-multiprocessing.html).
    Sharing a single `fitz.Document` instance across threads causes memory
    corruption and race conditions in MuPDF native state. Therefore, per-page
    rendering is strictly performed serially over `doc`. The `parallelism`
    parameter is retained for backward-compatibility.
    """
    _emit_pymupdf_agpl_notice()

    if isinstance(source, bytes):
        doc = fitz.open(stream=source, filetype="pdf")
    else:
        doc = fitz.open(source)

    try:
        total_pages = len(doc)
        selected_pages: set[int] | None = None
        if pages:
            selected_pages = set(parse_page_range(pages, total_pages))

        if selected_pages is not None:
            page_nums = sorted(selected_pages)
        else:
            page_nums = list(range(total_pages))

        # Audit P2-9: hard page-count cap, checked before any raster work.
        _check_page_cap(len(page_nums))

        if not page_nums:
            return

        # PyMuPDF documents are not thread-safe across threads; render serially
        # over `doc` to prevent native memory corruption.
        for page_num in page_nums:
            yield _rasterize_one_page(doc, page_num, dpi, max_image_dim)
    finally:
        doc.close()


def convert_generator(
    source: str | bytes | Path,
    dpi: int = 200,
    pages: str | None = None,
    max_image_dim: int = 1024,
    parallelism: int = _DEFAULT_RASTERIZER_WORKERS,
) -> Iterator[tuple[int, Image.Image, str]]:
    """Stream page images and base64-encoded JPEGs lazily one page at a time.

    Accepts either a PDF or raw image file/bytes (JPEG, PNG, TIFF, BMP,
    WebP, AVIF). Yields ``(page_num, image_pil, base64_jpeg_str)``
    tuples. The source PDF/document is kept open until the generator is
    exhausted; the caller MUST consume the iterator (or call ``.close()``
    on it) so PyMuPDF releases the file handle promptly.

    PyMuPDF documents are not thread-safe across threads, so per-page
    rendering is performed serially. The ``parallelism`` parameter is
    retained for backward-compatibility.

    Raises ``ValueError`` for empty paths / empty bytes / non-positive DPI.
    """
    if dpi <= 0:
        raise ValueError("dpi must be greater than 0")

    if isinstance(source, (str, Path)) and not str(source).strip():
        raise ValueError("Source file path cannot be empty")
    if isinstance(source, bytes) and len(source) == 0:
        raise ValueError("Source bytes cannot be empty")

    is_image = False
    if isinstance(source, (str, Path)):
        is_image = _is_image_path(source)
    elif isinstance(source, bytes) and not source.startswith(b"%PDF"):
        try:
            with Image.open(io.BytesIO(source)) as test_img:
                test_img.verify()
            is_image = True
        except Exception:
            is_image = False

    if is_image:
        yield from _generator_from_image_source(
            source, pages=pages, max_image_dim=max_image_dim
        )
    else:
        yield from _generator_from_pdf_source(
            source,
            dpi=dpi,
            pages=pages,
            max_image_dim=max_image_dim,
            parallelism=parallelism,
        )


def convert_batches(
    source: str | bytes | Path,
    *,
    batch_size: int = 8,
    dpi: int = 200,
    pages: str | None = None,
    max_image_dim: int = 1024,
    parallelism: int = _DEFAULT_RASTERIZER_WORKERS,
) -> Iterator[list[tuple[int, Image.Image, str]]]:
    """Stream pages from ``source`` in bounded batches.

    Audit H1 fix: this is the bounded-memory counterpart to
    :func:`convert_pdf_to_images`. Where the eager API materializes every
    page's PIL image and base64 string into a single ``dict`` up-front
    (peak memory grows linearly with page count), ``convert_batches``
    only holds at most ``batch_size`` pages worth of decoded images and
    encoded JPEGs at any one time. Each yielded batch is an independent
    ``list`` so the caller can release the previous batch's PIL objects
    as soon as downstream processing finishes.

    The last batch may be shorter than ``batch_size`` when the source
    page count is not an exact multiple. Page selection via ``pages`` is
    delegated to :func:`convert_generator`, so the same ``"1,3-5,7"``
    syntax is supported.

    Parameters
    ----------
    source
        Path, ``bytes``, or :class:`~pathlib.Path` to a PDF, JPEG, PNG,
        TIFF, BMP, WebP, or AVIF file. Multi-frame TIFFs are expanded
        one frame per page.
    batch_size
        Maximum number of pages per yielded batch. Must be ``>= 1``.
        ``batch_size=1`` is equivalent to consuming
        :func:`convert_generator` one item at a time; ``batch_size>=2``
        amortizes per-batch overhead (generator setup, base64 encode)
        while still bounding peak memory.
    dpi
        PyMuPDF rasterization DPI. Ignored for raw image inputs.
    pages
        Optional page selector passed through to
        :func:`convert_generator`.
    max_image_dim
        Longest-edge cap applied to each rasterized page.

    Yields
    ------
    list[tuple[int, Image.Image, str]]
        A batch of at most ``batch_size`` ``(page_num, image, b64_str)``
        triples. Each batch is a fresh list; the caller may mutate or
        discard it freely.

    Raises
    ------
    ValueError
        If ``batch_size`` is not a positive integer, or if
        :func:`convert_generator` rejects the source (empty path,
        empty bytes, non-positive DPI).
    """
    if not isinstance(batch_size, int) or batch_size < 1:
        raise ValueError("batch_size must be a positive integer")

    batch: list[tuple[int, Image.Image, str]] = []
    for item in convert_generator(
        source,
        dpi=dpi,
        pages=pages,
        max_image_dim=max_image_dim,
        parallelism=parallelism,
    ):
        batch.append(item)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def convert(
    pdf_path: str | Path,
    dpi: int = 150,
    max_image_dim: int = 1024,
    parallelism: int = _DEFAULT_RASTERIZER_WORKERS,
    pages: str | None = None,
) -> dict[int, str]:
    """Backward-compatible alias for :func:`convert_pdf_to_images`.

    Historical name kept for callers (and pre-existing tests) that
    expect ``from omniscribe.core.pdf.rasterizer import convert``.
    For large PDFs prefer :func:`convert_batches` (bounded peak memory)
    or :func:`convert_generator` (single-page streaming).
    """
    return convert_pdf_to_images(
        pdf_path,
        dpi=dpi,
        max_image_dim=max_image_dim,
        parallelism=parallelism,
        pages=pages,
    )


def convert_pdf_to_images(
    pdf_path: str | Path,
    dpi: int = 150,
    max_image_dim: int = 1024,
    parallelism: int = _DEFAULT_RASTERIZER_WORKERS,
    pages: str | None = None,
) -> dict[int, str]:
    """
    Render every page to a base64-encoded JPEG, capped at `max_image_dim`
    pixels on the longest edge so the image fits the VLM's context window.

    Backward-compatible eager entry point. For large PDFs prefer
    :func:`convert_batches` (bounded peak memory) or
    :func:`convert_generator` (single-page streaming).
    """
    return {
        page_num: b64
        for page_num, _img, b64 in convert_generator(
            pdf_path,
            dpi=dpi,
            pages=pages,
            max_image_dim=max_image_dim,
            parallelism=parallelism,
        )
    }
