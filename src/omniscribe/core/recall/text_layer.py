"""Embedded-text-layer recall source for the hybrid pipeline.

Second box source alongside the pixel-statistics whitespace booster
(``core/recall/whitespace.py``): digital PDFs already carry exact glyph
positions, so text lines Surya missed can be recovered from
``page.get_text("words")`` with no image analysis at all. Scanned pages
have no text layer and contribute nothing, making the pass a strict no-op
there. ``HybridEngine._detect_layout`` merges the recovered line boxes
into the detected boxes before dense selection, OCR, and DP alignment —
boxes only, so recovered lines flow through the same OCR / alignment /
trust stack as detected ones.

Known limitation: candidate granularity is the PDF's own extraction
lines, so PDFs with poor positional fidelity in their text layer
(hand-built overlays, some converters) inherit that inaccuracy; the
per-page fail-open guard limits the blast radius.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import pymupdf as fitz  # PyMuPDF

from omniscribe.core.document import BBox
from omniscribe.core.recall import (
    MAX_RECALL_BOXES_PER_PAGE,
    STRADDLE_MIN_OVERLAP,
    geometry,
)
from omniscribe.utils.env import DISABLE_STRINGS, env_str

logger = logging.getLogger(__name__)

_ENV_TEXT_LAYER_RECALL = "OMNISCRIBE_TEXT_LAYER_RECALL"

# Pages with fewer extracted words than this are treated as having no
# usable text layer (scans, image-only pages, corrupt overlays) and are
# skipped wholesale rather than contributing a stray word or two.
_MIN_WORDS_ON_PAGE = 3

# Dedup against the already-merged boxes (Surya + any whitespace-recall
# extras applied earlier in the same chunk). Text-layer boxes hug the
# glyphs tightly while Surya boxes carry padding, so the containment bar
# is slightly looser than the booster's: a candidate mostly inside an
# existing box is the same line, not a miss.
_MAX_CONTAINMENT = 0.6
_MAX_IOU = 0.3
# Straddle guard (mirrors the whitespace booster): a candidate overlapping >= 2
# existing boxes this much (as a fraction of its own area) spans a gutter
# or stacked lines and is rejected rather than merged — a wide box would
# feed garbled multi-region text to per-box OCR.
_STRADDLE_MIN_OVERLAP = STRADDLE_MIN_OVERLAP

# Junk-blast-radius bound, mirroring the booster: at most this many
# text-layer boxes per page, kept in extraction (reading) order. Bounds
# n_boxes inflation toward dense_threshold on pathological text layers.
_MAX_TEXT_LAYER_BOXES_PER_PAGE = MAX_RECALL_BOXES_PER_PAGE

#: Blocks whose OCR tokens agree with the PDF text layer below this share
#: are treated as likely fluent hallucinations by the quality repair loop.
TEXT_LAYER_AGREEMENT_TARGET = 0.2


@dataclass(frozen=True, slots=True)
class TextLayerRecallOptions:
    enabled: bool = True

    @classmethod
    def from_env(cls) -> TextLayerRecallOptions:
        """Seed from ``OMNISCRIBE_TEXT_LAYER_RECALL`` (default on).

        Only explicit disable values (``0``/``false``/``no``/``off``/
        ``n``/``disabled``, case-insensitive) turn the pass off; unset or
        unrecognized values keep it enabled.

        The env read goes through :func:`omniscribe.utils.env.env_str`
        (audit H3) so this module no longer imports ``os``.
        """
        raw = (env_str(_ENV_TEXT_LAYER_RECALL) or "").strip().lower()
        return cls(enabled=raw not in DISABLE_STRINGS)


class PdfTextLayerRecall:
    """Recovers text-line boxes Surya missed via the PDF's embedded text layer.

    Lifecycle: ``open`` is called once per run (it is a strict no-op for
    non-PDF inputs), ``supplement`` per page during detection, ``close``
    at the end of the run. Every failure path degrades to "no extra
    boxes" and never fails the job.
    """

    def __init__(self, options: TextLayerRecallOptions | None = None) -> None:
        self.options = options or TextLayerRecallOptions()
        self._doc: fitz.Document | None = None
        # Run-level observability counter (mirrors the booster's T2
        # counter): extraction lines seen minus boxes finally returned,
        # so ``HybridEngine`` can report dedup/cap activity at INFO.
        # Cumulative across ``supplement`` calls; the engine reads deltas.
        self.candidates_dropped = 0

    @property
    def enabled(self) -> bool:
        """Kill-switch state — ``HybridEngine`` skips the pass when off."""
        return self.options.enabled

    def open(self, input_path: str) -> bool:
        """Open the source PDF for text extraction.

        Returns ``True`` when the document is usable. Non-PDF inputs
        (image jobs, multi-page TIFFs) and open failures return ``False``
        and leave the pass a no-op for this run.
        """
        self._doc = None
        if not self.options.enabled:
            return False
        if not input_path.lower().endswith(".pdf"):
            logger.debug("Text-layer recall skipping non-PDF input: %s", input_path)
            return False
        try:
            self._doc = fitz.open(input_path)
            if not getattr(self._doc, "is_pdf", True):
                logger.debug("Text-layer recall skipping non-PDF input: %s", input_path)
                self.close()
                return False
        except Exception as e:
            logger.warning(
                "Text-layer recall could not open %s: %s: %s",
                input_path,
                type(e).__name__,
                e,
            )
            logger.debug("Text-layer recall skipping non-PDF input: %s", input_path)
            self._doc = None
            return False
        return True

    def close(self) -> None:
        """Release the underlying document (safe to call more than once)."""
        doc = self._doc
        self._doc = None
        if doc is not None:
            doc.close()

    def page_text(self, page_num: int) -> str:
        """Raw text of one page from the open document; ``""`` when unavailable.

        Used by the quality repair loop's text-layer agreement trigger.
        Fail-open: closed doc, out-of-range page, or extraction errors all
        return ``""`` (which makes the agreement check neutral).
        """
        if self._doc is None:
            return ""
        try:
            if page_num < 0 or page_num >= len(self._doc):
                return ""
            page_text_str: str = self._doc[page_num].get_text()
            return page_text_str
        except Exception as exc:
            logger.warning(
                "Text-layer page_text failed on page %d: %s: %s",
                page_num,
                type(exc).__name__,
                exc,
            )
            return ""

    def supplement(self, page_num: int, existing_boxes: list[BBox]) -> list[BBox]:
        """Return text-line boxes from the text layer not covered by ``existing_boxes``.

        Returns only the *additional* boxes; the caller appends them.
        Empty when disabled, when no document is open, or when nothing
        survives dedup. Coordinates are normalized to ``0..1`` on the
        (rotation-aware) page rect, matching the Surya box contract.
        """
        if not self.options.enabled or self._doc is None:
            return []
        # H3 audit fix: wrap per-page extraction in try/except so a single
        # corrupted PDF page degrades to "no extra boxes" instead of
        # aborting the per-page supplement loop. Mirrors the
        # whitespace.py booster's fail-open contract.
        try:
            return self._supplement_inner(page_num, existing_boxes)
        except Exception as exc:
            logger.warning(
                "Text-layer recall failed on page %d: %s: %s; degrading to empty.",
                page_num,
                type(exc).__name__,
                exc,
            )
            self.candidates_dropped += 1
            return []

    def _supplement_inner(
        self, page_num: int, existing_boxes: list[BBox]
    ) -> list[BBox]:
        """Inner body of :meth:`supplement`; isolated so the H3 audit
        fail-open wrapper can catch all exceptions without swallowing
        legitimate ``return []`` for normal conditions."""
        if self._doc is None:
            # Fail-open: an unopened/closed source contributes no boxes
            # (asserts vanish under `python -O` — pedantic review 1.9).
            return []
        if page_num < 0 or page_num >= len(self._doc):
            return []
        page = self._doc[page_num]
        width, height = page.rect.width, page.rect.height
        if width <= 0 or height <= 0:
            return []
        words = page.get_text("words")
        if len(words) < _MIN_WORDS_ON_PAGE:
            return []

        # Group words into extraction lines and take each line's union box.
        lines: dict[tuple[int, int], list[tuple[float, float, float, float]]] = {}
        for x0, y0, x1, y1, _word, block_no, line_no, _word_no in words:
            lines.setdefault((block_no, line_no), []).append((x0, y0, x1, y1))

        candidates: list[BBox] = []
        for word_boxes in lines.values():
            cx0 = min(b[0] for b in word_boxes)
            cy0 = min(b[1] for b in word_boxes)
            cx1 = max(b[2] for b in word_boxes)
            cy1 = max(b[3] for b in word_boxes)
            # Normalize and clamp onto the page so oversized overlay
            # coordinates never escape the 0..1 bbox contract.
            nx0 = max(0.0, min(1.0, cx0 / width))
            ny0 = max(0.0, min(1.0, cy0 / height))
            nx1 = max(0.0, min(1.0, cx1 / width))
            ny1 = max(0.0, min(1.0, cy1 / height))
            if nx1 <= nx0 or ny1 <= ny0:
                continue
            candidates.append((nx0, ny0, nx1, ny1))

        kept = [
            box
            for box in candidates
            if not geometry.is_duplicate(
                box,
                existing_boxes,
                max_containment=_MAX_CONTAINMENT,
                max_iou=_MAX_IOU,
                straddle_min_overlap=_STRADDLE_MIN_OVERLAP,
            )
        ]
        if len(kept) > _MAX_TEXT_LAYER_BOXES_PER_PAGE:
            kept = kept[:_MAX_TEXT_LAYER_BOXES_PER_PAGE]
        # Every extraction line that did not become a returned box was
        # dropped by dedup or the cap (run-summary counter).
        self.candidates_dropped += len(candidates) - len(kept)
        return kept


def token_agreement(ocr_text: str, layer_text: str) -> float:
    """Share of OCR tokens present in the PDF text layer (0..1).

    The repair trigger's text-shape heuristic can't see fluent
    hallucinations (they score 0.99 like real text); a low token overlap
    against the PDF's own text layer is the missing signal. Tokens shorter
    than 3 chars are ignored on the OCR side (function words carry no
    signal); no evidence on either side returns 1.0 so missing layers
    never flag.
    """
    if not ocr_text.strip() or not layer_text.strip():
        return 1.0
    layer_tokens = {t.casefold() for t in re.findall(r"\w+", layer_text)}
    ocr_tokens = [t.casefold() for t in re.findall(r"\w+", ocr_text) if len(t) >= 3]
    if not ocr_tokens:
        return 1.0
    hits = sum(1 for t in ocr_tokens if t in layer_tokens)
    return hits / len(ocr_tokens)


def _overlaps_existing(candidate: BBox, existing_boxes: list[BBox]) -> bool:
    """True when the candidate is already explained by a merged box."""
    return geometry.overlaps(
        candidate,
        existing_boxes,
        max_containment=_MAX_CONTAINMENT,
        max_iou=_MAX_IOU,
    )


def _straddles_existing(candidate: BBox, existing_boxes: list[BBox]) -> bool:
    """True when the candidate spans >= 2 merged boxes (gutter/stacked lines)."""
    return geometry.straddles(
        candidate,
        existing_boxes,
        straddle_min_overlap=_STRADDLE_MIN_OVERLAP,
    )
