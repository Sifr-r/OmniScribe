"""Underscored helpers for the sandwich-PDF embedder.

Contains helper functions, font caching/probing, and rasterization
support used by ``omniscribe.core.pdf.embedder``.
"""

from __future__ import annotations

import io
import logging
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pymupdf as fitz  # PyMuPDF
from PIL import Image, ImageSequence

from omniscribe.core.document import BBox
from omniscribe.core.pdf.rasterizer import (
    EMBED_JPEG_QUALITY_IMAGE,
    EMBED_JPEG_QUALITY_PDF,
    _calculate_safe_dpi,
)

logger = logging.getLogger(__name__)

# Module-level font cache. ``fitz.Font("helv")`` loads the built-in
# Helvetica metrics once per call; doing that once per text box on a
# 200-page PDF is the kind of micro-cost that adds up to a second.
_EMBED_FONT: fitz.Font | None = None
_EMBED_FONT_ASCENDER: float = 1.075
_EMBED_FONT_DESCENDER: float = -0.299

# Audit P0-3 — ``helv`` (WinAnsi) cannot encode Arabic/CJK, so the
# searchable layer was empty for exactly the scripts this product
# targets. Each text run is drawn with the first font in a chain that
# covers all of its non-space characters:
#
#   1. ``OMNISCRIBE_EMBED_FONT_PATH`` — explicit TTF/OTF override.
#   2. Bundled fonts in ``omniscribe/resources/fonts/`` — drop a font
#      such as Noto Naskh Arabic there for full Arabic coverage.
#   3. OS font candidates (Tahoma/Segoe UI on Windows, Noto/DejaVu on
#      Linux) — broad coverage including Arabic/Hebrew.
#   4. PyMuPDF's bundled Droid Sans Fallback ("cjk") — always available;
#      covers CJK/Cyrillic/Greek/Latin but not Arabic/Hebrew.
#
# The chain is tried in order per run, so on a Windows host Tahoma
# serves Arabic/Cyrillic runs and the built-in ``cjk`` serves CJK runs
# in the same document. Characters no chain font covers are dropped
# (logged once) rather than written as .notdef, which extracts back as
# U+0000 and pollutes copy/paste.
_UNICODE_CHAIN: tuple[fitz.Font, ...] | None = None
_LOGGED_ONCE_KEYS: set[str] = set()


def _log_once(
    msg: str,
    *args: Any,
    level: int = logging.WARNING,
    key: str | None = None,
) -> None:
    """Log a message at most once per distinct key (defaults to msg)."""
    cache_key = key if key is not None else msg
    if cache_key in _LOGGED_ONCE_KEYS:
        return
    _LOGGED_ONCE_KEYS.add(cache_key)
    logger.log(level, msg, *args)


_BUNDLED_FONT_DIR = (
    Path(__file__).resolve().parent.parent.parent / "resources" / "fonts"
)
_SYSTEM_FONT_CANDIDATES: tuple[str, ...] = (
    # Windows — Tahoma and Segoe UI both ship Arabic + Hebrew + Latin.
    r"C:\Windows\Fonts\tahoma.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
    # Common Linux distro locations for broad-coverage Noto / DejaVu.
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def _get_embed_font() -> fitz.Font:
    global _EMBED_FONT
    if _EMBED_FONT is None:
        _EMBED_FONT = fitz.Font("helv")
    return _EMBED_FONT


def _load_font_file(path: Path) -> fitz.Font | None:
    try:
        return fitz.Font(fontfile=str(path))
    except Exception:
        logger.warning("Embed font not loadable, skipping: %s", path)
        return None


# Codepoints used to probe whether a font's ToUnicode round-trips:
# Arabic meem, Persian peh (\u067e), and Hebrew alef — the scripts OS fonts
# most often remap to presentation-form codepoints (U+FE70+) that break
# copy/paste.
_PROBE_CODEPOINTS: tuple[int, ...] = (0x0645, 0x067E, 0x05D0)


def _font_preserves_codepoints(font: fitz.Font) -> bool:
    """Detect fonts whose cmap remaps logical RTL codepoints.

    Some OS fonts (notably Tahoma) carry the Arabic presentation-form
    block, and PyMuPDF's ToUnicode mapping then extracts U+FExx instead
    of the logical characters — the text layer is present but searches
    and copy/paste get the wrong codepoints. One tiny in-memory PDF
    round-trip per font candidate tells them apart.
    """
    buffer = getattr(font, "buffer", None)
    if buffer is None:
        return True
    probe = "".join(chr(cp) for cp in _PROBE_CODEPOINTS if font.has_glyph(cp))
    if not probe:
        return True  # font has no probe glyphs — nothing to remap
    try:
        doc = fitz.open()
        try:
            page = doc.new_page(width=100, height=20)
            page.insert_font(fontname="probe", fontbuffer=buffer)
            page.insert_text((2, 15), probe, fontname="probe", render_mode=3)
            got: str = page.get_text().strip().replace("\xa0", "")
            return got == probe
        finally:
            doc.close()
    except (RuntimeError, ValueError, OSError) as exc:
        # PyMuPDF raises ``RuntimeError`` for font-insert / text-extract
        # failures (e.g. malformed font buffer, unsupported glyphs);
        # ``ValueError`` for invalid arguments; ``OSError`` for
        # underlying file-system errors during the in-memory PDF
        # round-trip. Returning ``True`` preserves the previous fail-open
        # behaviour, so an embedded font never blocks output; the warning
        # is the operator-visible signal that the probe is unreliable.
        logger.warning(
            "Embedder font probe failed: %s",
            exc,
        )
        return True


def _resolve_unicode_chain() -> tuple[fitz.Font, ...]:
    """Collect every loadable Unicode-capable font, best first.

    Codepoint-preserving fonts rank ahead of remapping ones so a host
    whose only Arabic font remaps (e.g. Tahoma) doesn't silently turn
    the text layer into presentation forms; the remappers stay at the
    end as partial-coverage fallbacks.
    """
    candidates: list[fitz.Font] = []

    env_path = os.getenv("OMNISCRIBE_EMBED_FONT_PATH", "").strip()
    if env_path:
        font = _load_font_file(Path(env_path).expanduser())
        if font is not None:
            logger.info("Embed font from OMNISCRIBE_EMBED_FONT_PATH: %s", env_path)
            candidates.append(font)

    if _BUNDLED_FONT_DIR.is_dir():
        for candidate in sorted(_BUNDLED_FONT_DIR.iterdir()):
            if candidate.suffix.lower() in {".ttf", ".otf"}:
                font = _load_font_file(candidate)
                if font is not None:
                    logger.info("Embed font from bundled resources: %s", candidate.name)
                    candidates.append(font)

    for sys_path in _SYSTEM_FONT_CANDIDATES:
        path = Path(sys_path)
        if path.is_file():
            font = _load_font_file(path)
            if font is not None:
                logger.info("Embed font from system path: %s", sys_path)
                candidates.append(font)
                break  # one OS font is enough; keep the chain lean

    try:
        candidates.append(fitz.Font("cjk"))  # bundled Droid Sans Fallback
    except Exception:
        logger.warning(
            "PyMuPDF built-in 'cjk' font unavailable; embed falls back to helv"
        )

    preserving = [f for f in candidates if _font_preserves_codepoints(f)]
    remapping = [f for f in candidates if f not in preserving]
    if remapping:
        logger.info(
            "Embed font chain: %d codepoint-preserving font(s), %d remapping "
            "font(s) demoted to fallback",
            len(preserving),
            len(remapping),
        )
    return tuple(preserving + remapping)


def _get_unicode_chain() -> tuple[fitz.Font, ...]:
    global _UNICODE_CHAIN
    if _UNICODE_CHAIN is None:
        _UNICODE_CHAIN = _resolve_unicode_chain()
    return _UNICODE_CHAIN


def _unicode_font_alias(font: fitz.Font) -> str:
    return f"omni-embed-uni-{id(font)}"


def _font_covers(font: fitz.Font, text: str) -> bool:
    return all(font.has_glyph(ord(c)) for c in text if not c.isspace())


def _pick_embed_font(text: str) -> tuple[str, fitz.Font]:
    """Choose the font alias + metrics font for one text run.

    Pure-Latin text stays on the Base-14 ``helv`` (no font embedded, no
    output-size cost). Anything helv cannot encode moves to the first
    chain font that covers it, which is registered on the page under a
    per-font alias before drawing.
    """
    helv = _get_embed_font()
    if _font_covers(helv, text):
        return "helv", helv
    for font in _get_unicode_chain():
        if _font_covers(font, text):
            return _unicode_font_alias(font), font
    # No chain font covers the whole run — fall back to the first
    # Unicode font (partial coverage beats none) or helv if the chain
    # is empty; uncovered characters are filtered out below.
    chain = _get_unicode_chain()
    if chain:
        return _unicode_font_alias(chain[0]), chain[0]
    return "helv", helv


def _ensure_font_registered(page: fitz.Page, alias: str, font: fitz.Font) -> None:
    """Register the Unicode font on the page once (idempotent)."""
    if alias == "helv":
        return
    registered: set[str] = getattr(page, "_omni_registered_fonts", None) or set()
    if alias in registered:
        return
    page.insert_font(fontname=alias, fontbuffer=font.buffer)
    registered.add(alias)
    page._omni_registered_fonts = registered  # type: ignore[attr-defined]


def _filter_uncovered_chars(text: str, font: fitz.Font) -> str:
    """Drop characters the chosen font cannot encode.

    Writing them anyway would extract as U+0000 (``.notdef`` glyph) and
    corrupt copy/paste. Spaces are always kept so word boundaries
    survive. Logs one warning per process with the offending codepoints.
    """
    kept: list[str] = []
    missed: list[int] = []
    for c in text:
        if c.isspace() or font.has_glyph(ord(c)):
            kept.append(c)
        else:
            missed.append(ord(c))
    if missed:
        sample = ", ".join(f"U+{cp:04X}" for cp in missed[:8])
        _log_once(
            "Embed font '%s' lacks glyphs for %d character(s) (e.g. %s); "
            "they are omitted from the searchable layer. Set "
            "OMNISCRIBE_EMBED_FONT_PATH or drop a covering font into "
            "resources/fonts/ to include them.",
            font.name,
            len(missed),
            sample,
            key=f"glyph_miss:{font.name}",
        )
    return "".join(kept)


# Thread-pool worker count for parallel embed-side page rasterization.
# Defaults to the same env knob the VLM-side rasterizer uses.
_EMBED_RASTER_WORKERS = max(
    1, min(8, int(os.getenv("OMNISCRIBE_RASTERIZER_WORKERS", "4")))
)


# M6/M7 audit fix: extract magic numbers to module-level constants so
# they are documented and tunable in one place. The full-page fallback
# heuristic triggers when the bbox is within EPSILON of every page
# edge AND the text contains a newline.
_FULL_PAGE_FALLBACK_EPSILON = 0.001
_MIN_FONT_SIZE = 3.0
_MAX_FONT_SIZE = 72.0
_FALLBACK_BOX_INSET = 10  # px margin from page edge


def _handle_fullpage_fallback(
    page: fitz.Page,
    rect_coords: Sequence[float],
    text: str,
    page_width: float,
    page_height: float,
) -> bool:
    nx0, ny0, nx1, ny1 = rect_coords
    is_full_page_fallback = (
        nx0 <= _FULL_PAGE_FALLBACK_EPSILON
        and ny0 <= _FULL_PAGE_FALLBACK_EPSILON
        and nx1 >= 1.0 - _FULL_PAGE_FALLBACK_EPSILON
        and ny1 >= 1.0 - _FULL_PAGE_FALLBACK_EPSILON
        and "\n" in text
    )
    if is_full_page_fallback:
        fontname, font = _pick_embed_font(text)
        text = _filter_uncovered_chars(text, font)
        if not text.strip():
            return True
        _ensure_font_registered(page, fontname, font)
        fallback_rect = fitz.Rect(
            _FALLBACK_BOX_INSET,
            _FALLBACK_BOX_INSET,
            page_width - _FALLBACK_BOX_INSET,
            page_height - _FALLBACK_BOX_INSET,
        )
        page.insert_textbox(
            fallback_rect,
            text,
            fontsize=6,
            fontname=fontname,
            render_mode=3,
            color=(0, 0, 0),
            align=0,
        )
        return True
    return False


def _split_and_draw_lines(
    page: fitz.Page,
    rect_coords: Sequence[float],
    text: str,
    page_width: float,
    page_height: float,
) -> bool:
    nx0, ny0, nx1, ny1 = rect_coords
    if "\n" in text:
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        if len(lines) > 1:
            slice_h = (ny1 - ny0) / len(lines)
            for i, line in enumerate(lines):
                _draw_invisible_text(
                    page,
                    (nx0, ny0 + i * slice_h, nx1, ny0 + (i + 1) * slice_h),
                    line,
                    page_width,
                    page_height,
                )
            return True
        text = lines[0] if lines else text

    pdf_rect = fitz.Rect(
        nx0 * page_width,
        ny0 * page_height,
        nx1 * page_width,
        ny1 * page_height,
    )
    box_width = pdf_rect.width
    box_height = pdf_rect.height
    if box_width <= 0 or box_height <= 0:
        return True

    words = text.split()
    norm_height = ny1 - ny0
    aspect = box_height / max(0.01, box_width)
    if norm_height > 0.07 and aspect > 0.20 and len(words) >= 2:
        n_lines = 3 if norm_height > 0.13 else 2
        n_lines = min(n_lines, len(words))
        slice_h = (ny1 - ny0) / n_lines
        for i in range(n_lines):
            start = round(i * len(words) / n_lines)
            end = round((i + 1) * len(words) / n_lines)
            line_text = " ".join(words[start:end])
            if not line_text:
                continue
            _draw_invisible_text(
                page,
                (nx0, ny0 + i * slice_h, nx1, ny0 + (i + 1) * slice_h),
                line_text,
                page_width,
                page_height,
            )
        return True
    return False


def _draw_single_line_text(
    page: fitz.Page,
    rect_coords: Sequence[float],
    text: str,
    page_width: float,
    page_height: float,
) -> None:
    nx0, ny0, nx1, ny1 = rect_coords
    pdf_rect = fitz.Rect(
        nx0 * page_width,
        ny0 * page_height,
        nx1 * page_width,
        ny1 * page_height,
    )
    box_width = pdf_rect.width
    box_height = pdf_rect.height

    fontname, font = _pick_embed_font(text)
    text = _filter_uncovered_chars(text, font)
    if not text.strip():
        return
    _ensure_font_registered(page, fontname, font)

    ascender = getattr(font, "ascender", _EMBED_FONT_ASCENDER)
    descender = getattr(font, "descender", _EMBED_FONT_DESCENDER)
    extent_em = max(0.01, ascender - descender)
    fontsize = max(3.0, min(72.0, box_height / extent_em))

    natural_width = font.text_length(text, fontsize=fontsize)
    if natural_width <= 0:
        return

    target_width = max(1.0, box_width * 0.98)
    scale_x = min(50.0, target_width / natural_width)
    baseline = fitz.Point(pdf_rect.x0, pdf_rect.y1 + descender * fontsize)
    morph = (baseline, fitz.Matrix(scale_x, 1.0))
    page.insert_text(
        baseline,
        text,
        fontsize=fontsize,
        fontname=fontname,
        render_mode=3,
        color=(0, 0, 0),
        morph=morph,
    )


def _draw_invisible_text(
    page: fitz.Page,
    rect_coords: Sequence[float],
    text: str,
    page_width: float,
    page_height: float,
) -> None:
    """
    Embed invisible `text` so its glyph bboxes span the *full width* of
    the source bbox — selecting anywhere inside the bbox in a PDF viewer
    returns the text.
    """
    text = (text or "").strip()
    if not text:
        return

    # Phase 1: Handle full-page fallback detection
    if _handle_fullpage_fallback(page, rect_coords, text, page_width, page_height):
        return

    # Phase 2: Handle multi-line block detection and splitting
    if _split_and_draw_lines(page, rect_coords, text, page_width, page_height):
        return

    # Phase 3: Single-line drawing
    _draw_single_line_text(page, rect_coords, text, page_width, page_height)


def _embed_from_image_input(
    image_path: str | Path,
    output_pdf_path: str | Path,
    pages_data: dict[int, list[tuple[BBox, str]]] | dict[Any, Any],
    page_nums: Sequence[int] | None = None,
) -> None:
    """Build a sandwich PDF directly from an image (single- or multi-frame).

    ``page_nums`` restricts the output to the listed frame indices
    (audit P2-9); ``None`` keeps every frame.
    """
    selected = set(page_nums) if page_nums is not None else None
    new_doc = fitz.open()
    try:
        with Image.open(image_path) as src:
            for page_num, frame in enumerate(ImageSequence.Iterator(src)):
                if selected is not None and page_num not in selected:
                    continue
                img = frame.convert("RGB")
                width, height = float(img.width), float(img.height)

                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=EMBED_JPEG_QUALITY_IMAGE)
                img_data = buf.getvalue()

                new_page = new_doc.new_page(width=width, height=height)
                new_page.insert_image(new_page.rect, stream=img_data)

                for rect_coords, text in pages_data.get(page_num, []):
                    _draw_invisible_text(new_page, rect_coords, text, width, height)
        new_doc.save(output_pdf_path, garbage=3, deflate=True)
    finally:
        new_doc.close()


def _rasterize_embed_page(page: fitz.Page, dpi: int) -> tuple[float, float, bytes]:
    """Rasterize one page for sandwich embed; thread-safe across pages."""
    width = page.rect.width
    height = page.rect.height
    safe_dpi = _calculate_safe_dpi(width, height, dpi)
    pix = page.get_pixmap(dpi=safe_dpi)
    img_data = pix.tobytes("jpg", jpg_quality=EMBED_JPEG_QUALITY_PDF)
    return width, height, img_data
