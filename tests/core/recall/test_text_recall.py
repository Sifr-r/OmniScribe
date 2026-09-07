"""Unit tests for the whitespace recall booster (core/recall/whitespace.py)."""

from __future__ import annotations

import sys

import numpy as np
import pytest
from PIL import Image, ImageDraw

from omniscribe.core.recall.whitespace import (
    WhitespaceRecallBooster,
    WhitespaceRecallOptions,
    _overlaps_surya,
    _straddles_surya,
)
from omniscribe.core.workflows.hybrid import HybridEngine
from tests.conftest import _StubOCR
from tests.core.test_pipeline import _make_tiny_b64_image, _StubAligner, _StubPDF
from tests.core.workflows.test_workflows_hybrid import _engine, _noop_writer


def _line_page() -> Image.Image:
    """White 800x1000 page with three dashed black text-like lines.

    Dashes (24px on, 8px off; 16px tall) mimic glyph ink density so the
    candidate passes the density filter while staying clearly line-like.
    Line tops are at y = 100, 200, 300.
    """
    img = Image.new("RGB", (800, 1000), "white")
    draw = ImageDraw.Draw(img)
    for y in (100, 200, 300):
        for x in range(80, 720, 32):
            draw.rectangle([x, y, x + 24, y + 16], fill="black")
    return img


def test_recovers_line_missed_by_surya() -> None:
    img = _line_page()
    # Surya covers lines 1 and 2 but misses line 3 (ink at y=300..316).
    surya = [(0.08, 0.095, 0.92, 0.128), (0.08, 0.195, 0.92, 0.228)]
    extra = WhitespaceRecallBooster().supplement(img, surya)
    assert len(extra) == 1
    x0, y0, x1, y1 = extra[0]
    assert y0 < 0.31 < y1
    assert x0 < 0.15 and x1 > 0.85


def test_rules_only_page_yields_no_boxes() -> None:
    img = Image.new("RGB", (800, 1000), "white")
    draw = ImageDraw.Draw(img)
    for y in (150, 400, 650):
        draw.rectangle([40, y, 760, y + 2], fill="black")
    assert WhitespaceRecallBooster().supplement(img, []) == []


def test_blank_page_yields_no_boxes() -> None:
    img = Image.new("RGB", (800, 1000), "white")
    assert WhitespaceRecallBooster().supplement(img, []) == []


def test_zero_by_zero_image_yields_no_boxes() -> None:
    # eng-new: a degenerate raster must not raise; ``gray.size == 0``
    # short-circuits before any cv2 stats run.
    img = Image.new("RGB", (0, 0))
    assert WhitespaceRecallBooster().supplement(img, []) == []


# Pedantic 1.15: when *every* surya box has zero height, the median
# collapses to 0 and the min-height floor multiplies the
# ``_FALLBACK_MIN_HEIGHT`` (0.6% of page) by ``_MIN_HEIGHT_FRACTION`` (0.45)
# to ~0.27% of page height — about 3 px at 1024 px. That floor accepts
# basically any horizontal stripe and breaches the booster's documented
# precision stance. The fix routes the all-zero case to the absolute
# fallback band [0.006, 0.06] (the same one used for ``[]``).


def test_all_zero_height_surya_keeps_text_line_above_fallback_band() -> None:
    """A 16-px text line on a 1000-px page sits above the
    ``_FALLBACK_MIN_HEIGHT`` (0.006) and below ``_FALLBACK_MAX_HEIGHT``
    (0.06), so it must survive the filter even when every surya box has
    zero height (the path the 1.15 fix routes through the fallback).
    """
    img = _line_page()
    surya = [(0.1, 0.2, 0.9, 0.2), (0.1, 0.5, 0.9, 0.5)]  # both zero-height
    extra = WhitespaceRecallBooster().supplement(img, surya)
    # The page has three text lines; the one at y=300..316 is not in
    # surya_boxes so the booster should pick it up.
    assert any(0.27 < y0 < 0.35 for (x0, y0, x1, y1) in extra)


def test_all_zero_height_surya_rejects_hairline_below_fallback_band() -> None:
    """Sanity check: a 3-px hairline must still be rejected when all
    surya boxes are zero-height.

    The rejection path is the absolute ``_MIN_COMPONENT_HEIGHT_PX = 10``
    gate (which sits above the 1.15 min-height fix's 6-px fallback),
    not the 1.15 fix itself. Kept as a guardrail so a future relaxation
    of either floor cannot start admitting hairline rules.
    """
    img = Image.new("RGB", (800, 1000), "white")
    draw = ImageDraw.Draw(img)
    # 3-px hairline spanning the page width at y=500.
    draw.rectangle([40, 500, 760, 503], fill="black")
    surya = [(0.1, 0.2, 0.9, 0.2)]  # zero-height
    extra = WhitespaceRecallBooster().supplement(img, surya)
    assert extra == []


# Isolated filter-gate fixtures (eng-new). Page geometry is 800x1000 so the
# dilation kernel lands at kw=16, kh=6 (components inflate by kw-1 / kh-1);
# surya_boxes=[] selects the fallback height band [6px, 60px]. Each fixture
# is sized so exactly one gate fires.


def test_aspect_gate_rejects_tall_narrow_component() -> None:
    # Vertical dash column: 6 six-by-five dashes on a 7px pitch merge into
    # one ~21x49px component â€” wide enough for the height/area/density
    # gates, too narrow for the 2:1 aspect floor.
    img = Image.new("RGB", (800, 1000), "white")
    draw = ImageDraw.Draw(img)
    for y in range(400, 440, 7):
        draw.rectangle([400, y, 405, y + 4], fill="black")
    assert WhitespaceRecallBooster().supplement(img, []) == []


def test_area_gate_rejects_wide_tall_component() -> None:
    # A 15-row dashed block (~735x315px post-dilation) passes aspect, the
    # 2.5x height cap (median 0.3 -> cap 0.75), and density, but its ~0.29
    # normalized area crosses the 0.25 ceiling.
    img = Image.new("RGB", (800, 1000), "white")
    draw = ImageDraw.Draw(img)
    for y in range(200, 500, 21):
        for x in range(40, 760, 32):
            draw.rectangle([x, y, x + 24, y + 16], fill="black")
    surya = [(0.05, 0.6, 0.95, 0.9)]  # below the block: no dedup overlap
    assert WhitespaceRecallBooster().supplement(img, surya) == []


def test_max_density_gate_rejects_solid_blob() -> None:
    # A solid 600x40 bar: the pre-dilation mask fills ~87% of the
    # post-dilation bbox, above the 0.75 text-ink ceiling; aspect, height,
    # and area all pass.
    img = Image.new("RGB", (800, 1000), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle([100, 400, 700, 440], fill="black")
    assert WhitespaceRecallBooster().supplement(img, []) == []


def test_candidate_inside_surya_box_is_dropped() -> None:
    img = _line_page()
    surya = [(0.05, 0.28, 0.95, 0.36)]  # fully covers line 3
    assert WhitespaceRecallBooster().supplement(img, surya) == []


def test_overlaps_surya_iou_branch() -> None:
    candidate = (0.1, 0.1, 0.9, 0.14)
    # Same-size box shifted down: containment ~0.48 stays below the 0.5
    # threshold while IoU ~0.31 crosses the 0.3 threshold.
    assert _overlaps_surya(candidate, [(0.1, 0.121, 0.9, 0.161)]) is True
    # No intersection at all: neither branch fires.
    assert _overlaps_surya(candidate, [(0.1, 0.2, 0.9, 0.24)]) is False


def test_straddle_guard_rejects_gutter_crossing_candidate() -> None:
    # Wide candidate spanning two column boxes, overlapping each ~25%.
    candidate = (0.1, 0.1, 0.9, 0.15)
    columns = [(0.1, 0.08, 0.45, 0.5), (0.55, 0.08, 0.9, 0.5)]
    assert _straddles_surya(candidate, columns) is True
    # Same candidate beside a single column: no straddle.
    assert _straddles_surya(candidate, [(0.1, 0.08, 0.45, 0.5)]) is False
    # Touches two boxes but each overlap is negligible: not a straddle.
    assert (
        _straddles_surya(candidate, [(0.09, 0.1, 0.11, 0.15), (0.89, 0.1, 0.91, 0.15)])
        is False
    )


def test_gutter_straddle_candidate_dropped_end_to_end() -> None:
    # Two text columns with a tight gutter; dilation can bridge the gap
    # into one wide component spanning both columns. Surya sees both
    # columns, so the bridging candidate must be rejected, not emitted.
    img = Image.new("RGB", (800, 1000), "white")
    draw = ImageDraw.Draw(img)
    for y in (200, 260):
        for col_x0 in (60, 420):
            for x in range(col_x0, col_x0 + 300, 32):
                draw.rectangle([x, y, x + 26, y + 16], fill="black")
    surya = [(0.07, 0.19, 0.46, 0.29), (0.52, 0.19, 0.91, 0.29)]
    extra = WhitespaceRecallBooster().supplement(img, surya)
    assert all(not (x0 < 0.5 < x1) for x0, _y0, x1, _y1 in extra)


def test_photo_region_does_not_become_box() -> None:
    # Solid dark rectangle standing in for a photographic region: Otsu
    # binarizes it into a dense component that must not pass the filters
    # (density ~1.0 far exceeds the 0.75 ceiling).
    img = Image.new("RGB", (800, 1000), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle([100, 400, 700, 600], fill=(30, 30, 30))
    extra = WhitespaceRecallBooster().supplement(img, [])
    assert all(not (y0 < 0.5 < y1 and x0 < 0.5 < x1) for x0, y0, x1, y1 in extra)


def test_dark_inverted_page_yields_no_boxes() -> None:
    # Black background with light "text": Otsu-invert's whitespace model
    # breaks (foreground fraction > 0.5), so the page is skipped wholesale.
    img = Image.new("RGB", (800, 1000), "black")
    draw = ImageDraw.Draw(img)
    for y in (100, 200, 300):
        for x in range(80, 720, 32):
            draw.rectangle([x, y, x + 24, y + 16], fill="white")
    assert WhitespaceRecallBooster().supplement(img, []) == []


def test_per_page_cap_bounds_output() -> None:
    # 14 well-separated dashed lines: every one passes the filters, so the
    # per-page cap (10) must trim the output.
    img = Image.new("RGB", (800, 1000), "white")
    draw = ImageDraw.Draw(img)
    for y in range(40, 950, 64):
        for x in range(80, 720, 32):
            draw.rectangle([x, y, x + 24, y + 16], fill="black")
    extra = WhitespaceRecallBooster().supplement(img, [])
    assert 0 < len(extra) <= 10


def test_zero_height_surya_boxes_do_not_disable_height_floor() -> None:
    # Page with the usual three 16px-tall dashed lines plus a 6px-tall
    # noise line at y=500 (dilation grows it to ~12px, below the ~15px
    # median height floor but above the 10px pixel floor). If degenerate
    # zero-height boxes dragged the median to zero, the noise line would
    # slip through.
    img = _line_page()
    draw = ImageDraw.Draw(img)
    for x in range(80, 720, 32):
        draw.rectangle([x, 500, x + 24, 506], fill="black")
    surya = [
        (0.1, 0.05, 0.9, 0.05),  # degenerate zero-height boxes (majority)
        (0.1, 0.4, 0.9, 0.4),
        (0.1, 0.7, 0.9, 0.7),
        (0.08, 0.095, 0.92, 0.128),  # covers line 1
        (0.08, 0.195, 0.92, 0.228),  # covers line 2
    ]
    extra = WhitespaceRecallBooster().supplement(img, surya)
    assert len(extra) == 1  # only line 3; noise line stays filtered
    _x0, y0, _x1, y1 = extra[0]
    assert y0 < 0.31 < y1


def test_all_zero_height_surya_boxes_fall_back() -> None:
    img = _line_page()
    surya = [(0.1, 0.5, 0.9, 0.5), (0.1, 0.6, 0.9, 0.6)]
    # Falls back to the absolute min height; must not raise.
    extra = WhitespaceRecallBooster().supplement(img, surya)
    assert all(y1 - y0 > 0 for _x0, y0, _x1, y1 in extra)


def test_disabled_options_return_no_boxes() -> None:
    img = _line_page()
    booster = WhitespaceRecallBooster(WhitespaceRecallOptions(enabled=False))
    assert booster.supplement(img, []) == []


def test_candidates_dropped_counter_tracks_filtered_components() -> None:
    # T2: the engine's INFO run summary reads this counter as a delta.
    booster = WhitespaceRecallBooster()
    assert booster.candidates_dropped == 0

    img = _line_page()
    # Three line components: line 3 is recovered, lines 1-2 are dedup-
    # dropped against the covering Surya boxes.
    surya = [(0.08, 0.095, 0.92, 0.128), (0.08, 0.195, 0.92, 0.228)]
    extra = booster.supplement(img, surya)
    assert len(extra) == 1
    assert booster.candidates_dropped == 2

    # Accumulates across calls: three hairline-rule components, all
    # rejected by the filters.
    rules = Image.new("RGB", (800, 1000), "white")
    draw = ImageDraw.Draw(rules)
    for y in (150, 400, 650):
        draw.rectangle([40, y, 760, y + 2], fill="black")
    assert booster.supplement(rules, []) == []
    assert booster.candidates_dropped == 5


def test_disabled_booster_leaves_counter_at_zero() -> None:
    booster = WhitespaceRecallBooster(WhitespaceRecallOptions(enabled=False))
    assert booster.supplement(_line_page(), []) == []
    assert booster.candidates_dropped == 0


def test_missing_cv2_is_graceful(monkeypatch: pytest.MonkeyPatch) -> None:
    # sys.modules["cv2"] = None makes `import cv2` raise ImportError.
    monkeypatch.setitem(sys.modules, "cv2", None)
    img = _line_page()
    assert WhitespaceRecallBooster().supplement(img, []) == []


class TestWhitespaceRecallOptionsFromEnv:
    def test_default_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OMNISCRIBE_WHITESPACE_RECALL", raising=False)
        assert WhitespaceRecallOptions.from_env().enabled is True

    @pytest.mark.parametrize(
        "value",
        [
            "0",
            "false",
            "no",
            "off",
            "n",
            "disabled",
            "FALSE",
            " Off ",
            "N",
            " Disabled ",
        ],
    )
    def test_disable_values(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv("OMNISCRIBE_WHITESPACE_RECALL", value)
        assert WhitespaceRecallOptions.from_env().enabled is False

    def test_unrecognized_value_stays_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OMNISCRIBE_WHITESPACE_RECALL", "banana")
        assert WhitespaceRecallOptions.from_env().enabled is True


def test_whitespace_recall_constants_match_standard() -> None:
    from omniscribe.core.recall import (
        MAX_RECALL_BOXES_PER_PAGE,
        STRADDLE_MIN_OVERLAP,
    )
    from omniscribe.core.recall.whitespace import (
        _MAX_RECALL_BOXES_PER_PAGE,
        _MAX_WHITESPACE_BOXES_PER_PAGE,
        _STRADDLE_MIN_OVERLAP,
    )
    from omniscribe.utils.env import DISABLE_STRINGS

    assert _STRADDLE_MIN_OVERLAP == STRADDLE_MIN_OVERLAP == 0.15
    assert _MAX_RECALL_BOXES_PER_PAGE == MAX_RECALL_BOXES_PER_PAGE == 10
    assert _MAX_WHITESPACE_BOXES_PER_PAGE == 10
    assert "0" in DISABLE_STRINGS
    assert "disabled" in DISABLE_STRINGS


def test_photo_edge_page_returns_image_with_expected_shape() -> None:
    """Pins the synthetic helper's output shape and line position.

    The helper is consumed by ``test_photo_edge_passes_filters_as_known_limitation``
    in this file. This test pins the helper's contract independently so a
    regression in the helper is identifiable separately from a regression in
    the booster.
    """
    img = _photo_edge_page(
        line_height_px=14,
        line_width_px=800,
        density=0.40,
    )
    assert img.size == (1000, 1400)
    # White background check: top-left pixel must be near-white.
    assert img.getpixel((10, 10))[0] > 240  # type: ignore[index]
    # The line sits at y = 0.5 * 1400 = 700. Inside the line region the
    # pixel must be darker than the background. The pre-dilation density
    # is 0.40, so on average 60% of pixels in the line are white and 40%
    # are dark — sampling the center of the line should find a dark pixel
    # at least once across 5 samples.
    samples = [img.getpixel((200 + i * 100, 700)) for i in range(5)]
    assert any(px[0] < 100 for px in samples), (  # type: ignore[index]
        f"no dark pixel found in line region (samples={samples})"
    )


def _photo_edge_page(
    *,
    line_y_frac: float = 0.5,
    line_height_px: int,
    line_width_px: int,
    density: float,
    page_w: int = 1000,
    page_h: int = 1400,
) -> Image.Image:
    """Return a white ``Image`` with one horizontal line at ``y = line_y_frac * page_h``.

    The line has the given pixel height, width, and ink ``density``. The
    density is the *pre-dilation* ink fraction inside the line rect, which
    maps directly to the booster's ``_MIN_INK_DENSITY`` / ``_MAX_INK_DENSITY``
    checks at ``src/omniscribe/core/recall/whitespace.py:51-52``. Dilation in the
    booster will inflate connectivity but the density check uses the
    pre-dilation mask, so the asserted density must be the pre-dilation value.

    The RNG is seeded deterministically from ``(line_height_px, density,
    line_width_px)`` so the same parameters always produce the same image —
    the test is reproducible and a regression in this helper is traceable.

    Consumed by ``test_photo_edge_passes_filters_as_known_limitation`` to
    produce a line-shape that exercises the booster's height + density
    filters without needing a real photo or scanned page.
    """
    seed = int(line_height_px * 1_000_000 + int(density * 1000) * 1000 + line_width_px)
    rng = np.random.default_rng(seed)

    img = Image.new("RGB", (page_w, page_h), (255, 255, 255))
    arr = np.array(img)

    y_start = int(line_y_frac * page_h) - line_height_px // 2
    y_end = y_start + line_height_px
    x_start = (page_w - line_width_px) // 2
    x_end = x_start + line_width_px

    # Build the line as a random mask with the requested ink density.
    line_pixels = arr[y_start:y_end, x_start:x_end]
    mask_shape = line_pixels.shape[:2]
    keep = rng.random(mask_shape) < density
    # Ink pixels are dark; background pixels stay white.
    line_pixels[keep] = (0, 0, 0)
    line_pixels[~keep] = (255, 255, 255)
    arr[y_start:y_end, x_start:x_end] = line_pixels

    return Image.fromarray(arr)


@pytest.mark.parametrize(
    ("post_dilate_height_px", "density", "line_width_px"),
    [
        (11, 0.30, 600),  # boundary — just over the 10px height floor
        (14, 0.40, 800),  # mid-range — typical photo edge
        (18, 0.55, 1000),  # worst case — thick + dense
    ],
    ids=["boundary_height", "typical_photo_edge", "worst_case_thick_dense"],
)
def test_photo_edge_passes_filters_as_known_limitation(
    post_dilate_height_px: int,
    density: float,
    line_width_px: int,
) -> None:
    """Document the line-shape photo-edge limitation.

    A horizontal line that satisfies the booster's height, density,
    aspect, and area filters (see ``src/omniscribe/core/recall/whitespace.py:65-71``)
    is emitted as a recall box. The shape described here is a line-shaped
    photo edge or figure border that survives every filter. This test pins
    the current behavior: exactly 1 box is emitted for each parameter case.

    Do not change the asserted box count without re-measuring the
    junk-box impact on ``examples/*.pdf`` via the T7 harness at
    ``scripts/measure_recall_delta.py`` (T7 was the T9 photo-edge
    limitation pin; the T9 spec was archived in 2026-08 — the test
    still pins current behavior, so the box count is the contract).
    """
    img = _photo_edge_page(
        line_height_px=post_dilate_height_px,
        line_width_px=line_width_px,
        density=density,
    )
    extras = WhitespaceRecallBooster().supplement(img, [])
    assert len(extras) == 1
    _x0, y0, _x1, y1 = extras[0]
    assert 0.45 < y0 < 0.55 and 0.45 < y1 < 0.55


# ---------------------------------------------------------------------------
# HybridEngine wiring (moved from test_workflows_hybrid.py — Phase 4.2)
# ---------------------------------------------------------------------------


class TestHybridWhitespaceRecall:
    async def test_detect_layout_merges_recall_boxes_and_resorts(self) -> None:
        class _FixedBooster:
            def supplement(self, image, surya_boxes):
                # Sits ABOVE the Surya box → must sort first (row-major).
                return [(0.1, 0.02, 0.9, 0.05)]

        aligner = _StubAligner(boxes_per_page=[[0.1, 0.1, 0.9, 0.2]])
        engine = HybridEngine(
            aligner=aligner,  # type: ignore[arg-type]
            ocr_processor=_StubOCR(),  # type: ignore[arg-type]
            pdf_handler=_StubPDF(),  # type: ignore[arg-type]
            output_writer=_noop_writer,
            recall_booster=_FixedBooster(),  # type: ignore[arg-type]
        )
        pages = await engine._detect_layout(
            images_dict={0: _make_tiny_b64_image()}, page_nums=[0], progress=None, input_path=""
        )
        boxes = [box for box, _ in pages[0]]
        assert boxes == [(0.1, 0.02, 0.9, 0.05), [0.1, 0.1, 0.9, 0.2]]

    async def test_detect_layout_unchanged_without_booster(self) -> None:
        aligner = _StubAligner(boxes_per_page=[[0.1, 0.1, 0.9, 0.2]])
        engine = _engine(aligner=aligner)
        assert engine.recall_booster is None
        pages = await engine._detect_layout(
            images_dict={0: _make_tiny_b64_image()}, page_nums=[0], progress=None, input_path=""
        )
        assert pages == {0: [([0.1, 0.1, 0.9, 0.2], "")]}  # type: ignore[comparison-overlap]

    async def test_booster_exception_keeps_surya_boxes(self) -> None:
        class _ExplodingBooster:
            def supplement(self, image, surya_boxes):
                raise RuntimeError("simulated cv2 failure")

        aligner = _StubAligner(boxes_per_page=[[0.1, 0.1, 0.9, 0.2]])
        engine = HybridEngine(
            aligner=aligner,  # type: ignore[arg-type]
            ocr_processor=_StubOCR(),  # type: ignore[arg-type]
            pdf_handler=_StubPDF(),  # type: ignore[arg-type]
            output_writer=_noop_writer,
            recall_booster=_ExplodingBooster(),  # type: ignore[arg-type]
        )
        pages = await engine._detect_layout(
            images_dict={0: _make_tiny_b64_image()}, page_nums=[0], progress=None, input_path=""
        )
        assert pages == {0: [([0.1, 0.1, 0.9, 0.2], "")]}  # type: ignore[comparison-overlap]


class TestHybridWhitespaceRecallRunSummary:
    """One INFO line per detect pass so ops can see recall activity."""

    async def test_summary_reports_added_boxes_and_dropped_candidates(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        class _CountingBooster:
            def __init__(self) -> None:
                self.candidates_dropped = 7

            def supplement(self, image, surya_boxes):
                self.candidates_dropped += 3
                return [(0.1, 0.02, 0.9, 0.05)]

        aligner = _StubAligner(boxes_per_page=[[0.1, 0.1, 0.9, 0.2]])
        engine = HybridEngine(
            aligner=aligner,  # type: ignore[arg-type]
            ocr_processor=_StubOCR(),  # type: ignore[arg-type]
            pdf_handler=_StubPDF(),  # type: ignore[arg-type]
            output_writer=_noop_writer,
            recall_booster=_CountingBooster(),  # type: ignore[arg-type]
        )
        with caplog.at_level("INFO", logger="omniscribe.core.workflows.hybrid"):
            await engine._detect_layout(
                images_dict={0: _make_tiny_b64_image()},
                page_nums=[0],
                progress=None,
                input_path="",
            )
        summaries = [
            r.getMessage()
            for r in caplog.records
            if "Whitespace recall summary" in r.getMessage()
        ]
        assert summaries == [
            "Whitespace recall summary: 1 box(es) added on 1 of 1 page(s); "
            "3 candidate(s) dropped by filters"
        ]

    async def test_env_disabled_booster_logs_zero_count_summary(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The kill-switch path still emits the summary — a zero-count line
        # tells ops the pass is wired but disabled.
        monkeypatch.setenv("OMNISCRIBE_WHITESPACE_RECALL", "false")
        booster = WhitespaceRecallBooster(WhitespaceRecallOptions.from_env())
        aligner = _StubAligner(boxes_per_page=[[0.1, 0.1, 0.9, 0.2]])
        engine = HybridEngine(
            aligner=aligner,  # type: ignore[arg-type]
            ocr_processor=_StubOCR(),  # type: ignore[arg-type]
            pdf_handler=_StubPDF(),  # type: ignore[arg-type]
            output_writer=_noop_writer,
            recall_booster=booster,
        )
        with caplog.at_level("INFO", logger="omniscribe.core.workflows.hybrid"):
            await engine._detect_layout(
                images_dict={0: _make_tiny_b64_image()},
                page_nums=[0],
                progress=None,
                input_path="",
            )
        summaries = [
            r.getMessage()
            for r in caplog.records
            if "Whitespace recall summary" in r.getMessage()
        ]
        assert summaries == [
            "Whitespace recall summary: 0 box(es) added on 0 of 1 page(s); "
            "0 candidate(s) dropped by filters"
        ]


class TestHybridWhitespaceRecallEndToEnd:
    async def test_recall_box_receives_ocr_text_in_document_result(self) -> None:
        class _FixedBooster:
            def supplement(self, image, surya_boxes):
                # Sits above the Surya box, so row-major sort puts it first.
                return [(0.1, 0.02, 0.9, 0.05)]

        aligner = _StubAligner(boxes_per_page=[[0.1, 0.1, 0.9, 0.2]])
        pdf = _StubPDF(n_pages=1)
        engine = HybridEngine(
            aligner=aligner,  # type: ignore[arg-type]
            ocr_processor=_StubOCR(),  # type: ignore[arg-type]
            pdf_handler=pdf,  # type: ignore[arg-type]
            # Wire the stub's embed method as the output writer so the
            # finalized pages land in ``pdf.last_pages`` for inspection.
            output_writer=pdf.embed_structured_text,
            recall_booster=_FixedBooster(),  # type: ignore[arg-type]
        )

        result = await engine.execute("in.pdf", "out.pdf", refine=False, concurrency=1)

        # Sparse alignment hands line i to box i; the recall box sorts first
        # and receives real OCR text, not an empty placeholder.
        assert result[0] == [
            "Section heading",
            "First paragraph of body text with several words.",
        ]
        doc = engine.last_document_result
        assert doc is not None
        recall_blocks = [
            b for b in doc.pages[0].blocks if tuple(b.bbox) == (0.1, 0.02, 0.9, 0.05)
        ]
        assert len(recall_blocks) == 1
        assert recall_blocks[0].text == "Section heading"
        # The embed payload carries the recall box with its text too.
        assert tuple(pdf.last_pages[0][0][0]) == (0.1, 0.02, 0.9, 0.05)  # type: ignore[index]
        assert pdf.last_pages[0][0][1] == "Section heading"  # type: ignore[index]

    async def test_env_off_run_is_byte_identical_to_no_booster(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OMNISCRIBE_WHITESPACE_RECALL", "false")

        async def _run(booster):
            aligner = _StubAligner(boxes_per_page=[[0.1, 0.1, 0.9, 0.2]])
            pdf = _StubPDF(n_pages=1)
            engine = HybridEngine(
                aligner=aligner,  # type: ignore[arg-type]
                ocr_processor=_StubOCR(),  # type: ignore[arg-type]
                pdf_handler=pdf,  # type: ignore[arg-type]
                output_writer=pdf.embed_structured_text,
                recall_booster=booster,
            )
            res = await engine.execute("in.pdf", "out.pdf", refine=False, concurrency=1)
            return res, engine.last_document_result, pdf.last_pages

        off = await _run(WhitespaceRecallBooster(WhitespaceRecallOptions.from_env()))
        baseline = await _run(None)
        # Legacy view, embed payload, and every result field match...
        assert off[0] == baseline[0]
        assert off[2] == baseline[2]
        assert off[1] is not None and baseline[1] is not None
        assert off[1].pages == baseline[1].pages
        assert off[1].source_path == baseline[1].source_path
        # ...except ``tree``, whose BlockNode ids are random per build by
        # design — compare its shape instead.
        assert off[1].tree is not None and baseline[1].tree is not None
        assert len(off[1].tree.pages) == len(baseline[1].tree.pages)
        off_nodes = [
            (n.block_type, n.bbox, n.text)
            for p in off[1].tree.pages
            for n in p.children
        ]
        base_nodes = [
            (n.block_type, n.bbox, n.text)
            for p in baseline[1].tree.pages
            for n in p.children
        ]
        assert off_nodes == base_nodes


class TestHybridWhitespaceRecallGuardRails:
    async def test_multi_page_partial_failure_keeps_both_pages(self) -> None:
        # G3: a booster that explodes on one page degrades that page to its
        # Surya boxes and must not poison the rest of the run.
        class _PartialFailBooster:
            def __init__(self) -> None:
                self.calls = 0

            def supplement(self, image, surya_boxes):
                self.calls += 1
                if self.calls == 2:
                    raise RuntimeError("page two explodes")
                return [(0.1, 0.02, 0.9, 0.05)]

        aligner = _StubAligner(boxes_per_page=[[0.1, 0.1, 0.9, 0.2]])
        engine = HybridEngine(
            aligner=aligner,  # type: ignore[arg-type]
            ocr_processor=_StubOCR(),  # type: ignore[arg-type]
            pdf_handler=_StubPDF(),  # type: ignore[arg-type]
            output_writer=_noop_writer,
            recall_booster=_PartialFailBooster(),  # type: ignore[arg-type]
        )
        pages = await engine._detect_layout(
            images_dict={0: _make_tiny_b64_image(), 1: _make_tiny_b64_image()},
            page_nums=[0, 1],
            progress=None,
            input_path="",
        )
        # Page 0 merged (recall box sorts first); page 1 keeps Surya only.
        assert [box for box, _ in pages[0]] == [
            (0.1, 0.02, 0.9, 0.05),
            [0.1, 0.1, 0.9, 0.2],
        ]
        assert pages[1] == [([0.1, 0.1, 0.9, 0.2], "")]  # type: ignore[comparison-overlap]

    async def test_apply_recall_fallback_decodes_on_cache_miss(self) -> None:
        # G4: the LRU can evict a page between chunk decode and recall; the
        # fallback re-decodes from images_dict and re-caches the page.
        class _FixedBooster:
            def supplement(self, image, surya_boxes):
                return [(0.1, 0.02, 0.9, 0.05)]

        engine = HybridEngine(
            aligner=_StubAligner(),  # type: ignore[arg-type]
            ocr_processor=_StubOCR(),  # type: ignore[arg-type]
            pdf_handler=_StubPDF(),  # type: ignore[arg-type]
            output_writer=_noop_writer,
            recall_booster=_FixedBooster(),  # type: ignore[arg-type]
        )
        assert engine._decoded_cache == {}
        merged, touched, added = await engine._apply_recall(
            chunk_pages=[0],
            images_dict={0: _make_tiny_b64_image()},
            chunk_boxes=[[(0.1, 0.1, 0.9, 0.2)]],
        )
        assert (touched, added) == (1, 1)
        assert len(merged[0]) == 2
        # The fallback decode repopulated the cache for later stages.
        assert (engine._current_run_id, 0) in engine._decoded_cache

    async def test_recall_boxes_normalized_to_bbox_tuples(self) -> None:
        # CQ-3: whatever container a duck-typed booster returns, the merge
        # boundary emits BBox tuples (HybridAligner's contract).
        class _ListBooster:
            def supplement(self, image, surya_boxes):
                return [[0.1, 0.02, 0.9, 0.05]]

        aligner = _StubAligner(boxes_per_page=[[0.1, 0.1, 0.9, 0.2]])
        engine = HybridEngine(
            aligner=aligner,  # type: ignore[arg-type]
            ocr_processor=_StubOCR(),  # type: ignore[arg-type]
            pdf_handler=_StubPDF(),  # type: ignore[arg-type]
            output_writer=_noop_writer,
            recall_booster=_ListBooster(),  # type: ignore[arg-type]
        )
        pages = await engine._detect_layout(
            images_dict={0: _make_tiny_b64_image()}, page_nums=[0], progress=None, input_path=""
        )
        box = pages[0][0][0]
        assert isinstance(box, tuple)
        assert box == (0.1, 0.02, 0.9, 0.05)

    async def test_disabled_booster_never_reaches_supplement(self) -> None:
        # T6: a disabled booster is skipped before any decode/thread work.
        class _CountingBooster:
            enabled = False

            def __init__(self) -> None:
                self.calls = 0

            def supplement(self, image, surya_boxes):
                self.calls += 1
                return [(0.1, 0.02, 0.9, 0.05)]

        booster = _CountingBooster()
        aligner = _StubAligner(boxes_per_page=[[0.1, 0.1, 0.9, 0.2]])
        engine = HybridEngine(
            aligner=aligner,  # type: ignore[arg-type]
            ocr_processor=_StubOCR(),  # type: ignore[arg-type]
            pdf_handler=_StubPDF(),  # type: ignore[arg-type]
            output_writer=_noop_writer,
            recall_booster=booster,  # type: ignore[arg-type]
        )
        pages = await engine._detect_layout(
            images_dict={0: _make_tiny_b64_image()}, page_nums=[0], progress=None, input_path=""
        )
        assert booster.calls == 0
        assert pages == {0: [([0.1, 0.1, 0.9, 0.2], "")]}  # type: ignore[comparison-overlap]

    async def test_apply_recall_without_booster_returns_surya_boxes(self) -> None:
        # T6: the if-guard degrades to the Surya boxes instead of asserting.
        engine = _engine()
        chunk_boxes = [[(0.1, 0.1, 0.9, 0.2)]]
        merged, touched, added = await engine._apply_recall(
            chunk_pages=[0],
            images_dict={0: _make_tiny_b64_image()},
            chunk_boxes=chunk_boxes,
        )
        assert merged is chunk_boxes
        assert (touched, added) == (0, 0)
