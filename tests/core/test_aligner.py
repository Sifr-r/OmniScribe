"""
Unit tests for the DP line-to-box aligner.

Covers the three properties that matter for correctness:
    1. Monotonicity — reading order is preserved.
    2. Conservation — every LLM line lands somewhere (nothing silently dropped).
    3. Size-awareness — large paragraphs bind to large boxes, short lines to short boxes.
"""

from __future__ import annotations

import ast
import io
import pathlib
import threading
import types

import pytest
from PIL import Image

from omniscribe.core.aligner import (
    _SKIP_BOX_COST,
    HybridAligner,
    _dp_align,
    _match_cost,
    _normalize_lines,
    _reading_order_sort,
)


def _aligner() -> HybridAligner:
    """Construct without firing Surya __init__ — we only exercise align_text."""
    return HybridAligner.__new__(HybridAligner)


class TestNormalizeLines:
    def test_string_with_newlines(self):
        assert _normalize_lines("hello\n\nworld") == ["hello", "world"]

    def test_list_of_strings(self):
        assert _normalize_lines(["  hello  ", "world"]) == ["hello", "world"]

    def test_list_items_with_embedded_newlines(self):
        assert _normalize_lines(["line1\nline2", "line3"]) == [
            "line1",
            "line2",
            "line3",
        ]

    def test_empty_inputs(self):
        assert _normalize_lines("") == []
        assert _normalize_lines(None) == []  # type: ignore[arg-type]
        assert _normalize_lines([]) == []
        assert _normalize_lines(["   ", ""]) == []


class TestMatchCost:
    """The cost is asymmetric: over-fill (long line in small box) costs
    more than under-fill (short line in big box). This prevents the DP
    from packing long lines into too-narrow boxes when the symmetric
    cost would have allowed it, while still keeping mild overfills
    cheap enough to match (rather than skip)."""

    def test_perfect_fit_is_zero(self):
        assert _match_cost(50, 50) == pytest.approx(0.0)

    def test_underfill_is_cheap_capped_at_half(self):
        # Maximally under-filled (line clamped to min 1 char vs 100):
        # cost is just under 0.5 by the half-the-deficit formula.
        assert _match_cost(0, 100) < 0.5
        assert _match_cost(0, 100) == pytest.approx(0.495, abs=0.01)
        # 50% under-fill: halved relative deficit.
        assert _match_cost(50, 100) == pytest.approx(0.25)

    def test_overfill_grows_toward_one_at_extreme_ratios(self):
        # 50% over-fill: cost = 50/150 ≈ 0.333
        assert _match_cost(150, 100) == pytest.approx(1 / 3)
        # 100% over-fill: cost = 100/200 = 0.5
        assert _match_cost(200, 100) == pytest.approx(0.5)
        # 5x over-fill: cost = 400/500 = 0.8
        assert _match_cost(500, 100) == pytest.approx(0.8)
        # All overfill costs are < 1 by construction.
        assert _match_cost(10**9, 100) < 1.0

    def test_overfill_costs_more_than_equivalent_underfill(self):
        # The asymmetry: 2x line vs box (overfill) must hurt more than
        # 2x box vs line (underfill). Without this, the DP packs long
        # lines into narrow boxes and shifts subsequent matches.
        assert _match_cost(40, 20) > _match_cost(20, 40)

    def test_mild_overfill_stays_below_double_skip(self):
        # Mild overfill (line ~2x box capacity) must be cheaper than
        # ``skip_box + skip_line``, otherwise the DP loses lines instead
        # of placing them. ``2 * SKIP_BOX_COST`` is the rough budget.
        assert _match_cost(40, 20) < 2 * _SKIP_BOX_COST + 1.0


class TestDPAlign:
    def test_one_to_one_equal_sized(self):
        lines = ["alpha line", "beta line", "gamma line"]
        boxes = [[0.0, 0.0, 1.0, 0.3], [0.0, 0.3, 1.0, 0.6], [0.0, 0.6, 1.0, 0.9]]
        _, mapping, _ = _dp_align(lines, boxes)  # type: ignore[arg-type]
        assert mapping == {0: ["alpha line"], 1: ["beta line"], 2: ["gamma line"]}

    def test_respects_box_sizes(self):
        # Short heading + very long paragraph + short footer should map by size.
        lines = ["Title", "x" * 200, "Fin"]
        boxes = [
            [0.1, 0.05, 0.3, 0.08],  # tiny
            [0.0, 0.15, 1.0, 0.85],  # huge
            [0.4, 0.9, 0.55, 0.93],  # tiny
        ]
        _, mapping, _ = _dp_align(lines, boxes)  # type: ignore[arg-type]
        assert mapping[0] == ["Title"]
        assert mapping[1] == ["x" * 200]
        assert mapping[2] == ["Fin"]

    def test_more_lines_than_boxes_conserves_text(self):
        lines = ["A", "B", "C", "D", "E"]
        boxes = [[0.0, 0.0, 1.0, 0.5], [0.0, 0.5, 1.0, 1.0]]
        _, mapping, _ = _dp_align(lines, boxes)  # type: ignore[arg-type]
        all_placed = [t for vs in mapping.values() for t in vs]
        assert set(all_placed) == set(lines), "every line must be placed"

    def test_more_boxes_than_lines_leaves_empties(self):
        lines = ["one", "two"]
        boxes = [[0.0, 0.0, 0.3, 0.1]] * 5
        _, mapping, _ = _dp_align(lines, boxes)  # type: ignore[arg-type]
        assert sum(len(v) for v in mapping.values()) == 2

    def test_long_line_does_not_displace_short_lines_into_narrow_trap(self):
        # Regression for the duplication seen on examples/hybrid.pdf.
        # Reading-order lines: short, long, short. Box 1 is a narrow
        # trap that the symmetric cost used to allow the long line to
        # slide into. The wide box (index 2) is the visually-correct
        # home for the long line; if the DP leaves it empty, refine
        # crops the same content and produces a duplicate.
        lines = [
            "x" * 12,  # short
            "x" * 40,  # long
            "x" * 12,  # short
        ]
        boxes = [
            [0.05, 0.05, 0.30, 0.10],  # medium
            [0.05, 0.15, 0.15, 0.18],  # narrow trap
            [0.05, 0.25, 0.95, 0.32],  # wide (long-line home)
            [0.05, 0.40, 0.30, 0.45],  # medium
        ]
        out = _aligner().align_text([(b, "") for b in boxes], lines)  # type: ignore[misc]
        texts = [t for _, t in out]

        # The long line lands in the wide box (index 2), not displaced.
        assert texts[2] == "x" * 40, (
            f"long line should land in the wide box, got texts={texts}"
        )
        # The narrow trap (index 1) must not absorb the long line.
        assert "x" * 40 not in texts[1], (
            f"long line incorrectly packed into narrow trap: {texts[1]!r}"
        )

    def test_two_column_layout(self):
        # When boxes are passed in the same order the LLM emits text
        # (column-major here), the DP achieves a 1:1 monotonic match.
        lines = [
            "Left column first paragraph text here",
            "Left column second paragraph also has words",
            "Right column title",
            "Right column body text continues on",
        ]
        boxes = [
            [0.05, 0.10, 0.45, 0.15],  # L row 1
            [0.05, 0.18, 0.45, 0.30],  # L row 2
            [0.55, 0.10, 0.95, 0.12],  # R short title
            [0.55, 0.15, 0.95, 0.35],  # R body
        ]
        _, mapping, _ = _dp_align(lines, boxes)  # type: ignore[arg-type]
        assert len(mapping) == 4
        # Each box got text in increasing box-index order (monotonic).
        for i in range(4):
            assert mapping.get(i)

    def test_cost_increases_with_order_mismatch(self):
        # Heterogeneous box sizes + line lengths so that the DP cost
        # depends on the box ordering. align_text relies on this.
        lines = ["tiny", "x" * 200, "x" * 200, "tiny"]
        boxes_aligned = [
            [0.0, 0.05, 0.10, 0.10],  # tiny
            [0.0, 0.15, 1.0, 0.45],  # huge
            [0.0, 0.50, 1.0, 0.80],  # huge
            [0.4, 0.85, 0.55, 0.90],  # tiny
        ]
        boxes_shuffled = [boxes_aligned[i] for i in (1, 0, 3, 2)]
        cost_aligned, _, _ = _dp_align(lines, boxes_aligned)  # type: ignore[arg-type]
        cost_shuffled, _, _ = _dp_align(lines, boxes_shuffled)  # type: ignore[arg-type]
        assert cost_shuffled > cost_aligned, (
            f"shuffled cost {cost_shuffled} must exceed aligned {cost_aligned}"
        )

    def test_empty_boxes_yields_empty_mapping(self):
        assert _dp_align(["some text"], []) == (0.0, {}, 0)

    def test_empty_lines_yields_empty_mapping(self):
        assert _dp_align([], [[0.0, 0.0, 1.0, 1.0]]) == (0.0, {}, 0)  # type: ignore[list-item]


class TestAlignTextPublicAPI:
    def test_full_page_fallback_when_no_boxes(self):
        out = _aligner().align_text([], ["first line", "second line"])
        assert out == [((0.0, 0.0, 1.0, 1.0), "first line\nsecond line")]

    def test_all_empty_when_no_lines(self):
        structured = [((0.0, 0.0, 0.5, 0.5), ""), ((0.5, 0.5, 1.0, 1.0), "")]
        out = _aligner().align_text(structured, [])
        assert [t for _, t in out] == ["", ""]

    def test_result_length_matches_input_boxes(self):
        structured = [([i / 10, 0, i / 10 + 0.1, 0.1], "") for i in range(5)]
        lines = ["a", "b", "c"]
        out = _aligner().align_text(structured, lines)  # type: ignore[arg-type]
        assert len(out) == 5  # one tuple per input box, in order

    def test_preserves_box_order(self):
        boxes = [
            [0.0, 0.0, 1.0, 0.1],
            [0.0, 0.1, 1.0, 0.2],
            [0.0, 0.2, 1.0, 0.3],
        ]
        structured = [(b, "") for b in boxes]
        out = _aligner().align_text(structured, ["x", "y", "z"])  # type: ignore[arg-type]
        assert [b for b, _ in out] == boxes  # type: ignore[comparison-overlap]

    def test_accepts_both_string_and_list_input(self):
        structured = [((0.0, 0.0, 0.5, 0.5), "")]
        out_str = _aligner().align_text(structured, "one\ntwo")
        out_lst = _aligner().align_text(structured, ["one", "two"])
        assert out_str == out_lst

    def test_degenerate_single_line_many_boxes_falls_back_to_full_page(self):
        # Symptom users report as "all text packed in the top-left
        # corner": an LLM variant that doesn't break visual lines emits
        # ONE giant line for the whole page. The DP matches that line to
        # one box and every other box stays empty. align_text now
        # detects this and embeds the text in a single full-page bbox so
        # search works across the whole page instead of one corner.
        boxes = [[0.05, i * 0.05, 0.45, i * 0.05 + 0.04] for i in range(20)]
        structured = [(b, "") for b in boxes]
        single_line = "all the page text emitted as one big string with no line breaks"
        out = _aligner().align_text(structured, [single_line])  # type: ignore[arg-type]
        assert len(out) == 1, f"expected full-page fallback, got {len(out)} boxes"
        bbox, text = out[0]
        assert bbox == (0.0, 0.0, 1.0, 1.0)
        assert text == single_line

    def test_single_line_single_box_does_not_trigger_fallback(self):
        # A 1-line / 1-box page is the normal trivial case — the line
        # should land in its real box, not the full-page fallback.
        boxes = [[0.1, 0.1, 0.9, 0.2]]
        structured = [(b, "") for b in boxes]
        out = _aligner().align_text(structured, ["the one line"])  # type: ignore[arg-type]
        assert len(out) == 1
        bbox, text = out[0]
        assert bbox == boxes[0]  # type: ignore[comparison-overlap]
        assert text == "the one line"

    @pytest.mark.parametrize("n_lines,n_boxes", [(1, 1), (5, 3), (3, 5), (10, 10)])
    def test_conserves_all_lines_across_shapes(self, n_lines, n_boxes):
        lines = [f"line-{i}" for i in range(n_lines)]
        boxes = [[i / n_boxes, 0, (i + 1) / n_boxes, 0.1] for i in range(n_boxes)]
        structured = [(b, "") for b in boxes]
        out = _aligner().align_text(structured, lines)  # type: ignore[arg-type]
        placed_text = " ".join(t for _, t in out if t)
        for line in lines:
            assert line in placed_text, f"line {line!r} was dropped"


# Two-column fixture for auto-detect tests. Heterogeneous box sizes so that
# different orderings produce different DP costs (uniform boxes cost-tie
# and can't discriminate between orderings). Left column tall, right short.
_AUTODETECT_L_BOXES = [
    [0.05, 0.05, 0.40, 0.20],
    [0.05, 0.25, 0.40, 0.40],
    [0.05, 0.45, 0.40, 0.60],
    [0.05, 0.65, 0.40, 0.80],
]
_AUTODETECT_R_BOXES = [
    [0.60, 0.05, 0.95, 0.08],
    [0.60, 0.25, 0.95, 0.28],
    [0.60, 0.45, 0.95, 0.48],
    [0.60, 0.65, 0.95, 0.68],
]
# Surya-style row-major input: L1, R1, L2, R2, L3, R3, L4, R4.
_AUTODETECT_BOXES_ROW_MAJOR = [
    box
    for pair in zip(_AUTODETECT_L_BOXES, _AUTODETECT_R_BOXES, strict=False)
    for box in pair
]

_AUTODETECT_LEFT_TEXT = [
    f"L{i + 1} long body line with many words to fill a tall capacity box"
    for i in range(4)
]
_AUTODETECT_RIGHT_TEXT = [f"R{i + 1} short" for i in range(4)]


class TestAutoDetectReadingOrder:
    """align_text must place text in the right boxes regardless of which
    order the LLM emitted lines in. The DP cost itself is the signal —
    no per-model branching needed."""

    def test_column_major_emission_aligns_correctly(self):
        # OlmOCR-style: entire left column emitted first, then right column.
        lines = _AUTODETECT_LEFT_TEXT + _AUTODETECT_RIGHT_TEXT
        structured = [(b, "") for b in _AUTODETECT_BOXES_ROW_MAJOR]
        out = _aligner().align_text(structured, lines)  # type: ignore[arg-type]
        actual = [t for _, t in out]
        for i in range(4):
            assert actual[i * 2] == _AUTODETECT_LEFT_TEXT[i]
            assert actual[i * 2 + 1] == _AUTODETECT_RIGHT_TEXT[i]

    def test_row_major_emission_aligns_correctly(self):
        # Row-major emission: L1 R1 L2 R2 L3 R3 L4 R4.
        lines = []
        for i in range(4):
            lines.append(_AUTODETECT_LEFT_TEXT[i])
            lines.append(_AUTODETECT_RIGHT_TEXT[i])
        structured = [(b, "") for b in _AUTODETECT_BOXES_ROW_MAJOR]
        out = _aligner().align_text(structured, lines)  # type: ignore[arg-type]
        actual = [t for _, t in out]
        for i in range(4):
            assert actual[i * 2] == _AUTODETECT_LEFT_TEXT[i]
            assert actual[i * 2 + 1] == _AUTODETECT_RIGHT_TEXT[i]

    def test_single_column_unaffected(self):
        # Single-column page: row-major and column-major collapse to the
        # same order, so auto-detect is a no-op.
        boxes = [
            [0.10, 0.10, 0.90, 0.18],
            [0.10, 0.25, 0.90, 0.33],
            [0.10, 0.40, 0.90, 0.48],
            [0.10, 0.55, 0.90, 0.63],
        ]
        lines = ["first", "second", "third", "fourth"]
        structured = [(b, "") for b in boxes]
        out = _aligner().align_text(structured, lines)  # type: ignore[arg-type]
        assert [t for _, t in out] == lines


class TestReadingOrderSort:
    """Column-major reading-order sorter for multi-column pages."""

    def test_single_column_uses_row_major(self):
        # All boxes share roughly the same x range — no column gap.
        boxes = [
            [0.1, 0.20, 0.9, 0.25],
            [0.1, 0.10, 0.9, 0.15],
            [0.1, 0.30, 0.9, 0.35],
            [0.1, 0.40, 0.9, 0.45],
        ]
        out = _reading_order_sort(list(boxes))  # type: ignore[arg-type]
        ys = [b[1] for b in out]
        assert ys == sorted(ys)

    def test_two_column_emits_column_major(self):
        # Left column (x≈0.2) and right column (x≈0.8), interleaved input.
        # Expected output: all of left column first (top-to-bottom), then all
        # of right column (top-to-bottom).
        L = [
            [0.05, 0.10, 0.40, 0.15],
            [0.05, 0.30, 0.40, 0.35],
            [0.05, 0.50, 0.40, 0.55],
            [0.05, 0.70, 0.40, 0.75],
        ]
        R = [
            [0.60, 0.10, 0.95, 0.15],
            [0.60, 0.30, 0.95, 0.35],
            [0.60, 0.50, 0.95, 0.55],
            [0.60, 0.70, 0.95, 0.75],
        ]
        # Interleave so input is row-major.
        interleaved: list[list[float]] = []
        for left, right in zip(L, R, strict=False):
            interleaved.append(right)
            interleaved.append(left)

        out = _reading_order_sort(interleaved)  # type: ignore[arg-type]
        # Left column first (4 boxes), then right column (4 boxes).
        assert out[:4] == L  # type: ignore[comparison-overlap]
        assert out[4:] == R  # type: ignore[comparison-overlap]

    def test_lone_marginal_box_does_not_create_fake_column(self):
        # 5 body boxes centered around x≈0.5 plus a single page-number-like
        # box at x≈0.95. Without the ≥2-on-each-side guard, the single
        # marginal box would be sorted as its own "column" and re-ordered
        # ahead of body content.
        body = [
            [0.10, 0.10, 0.90, 0.15],
            [0.10, 0.30, 0.90, 0.35],
            [0.10, 0.50, 0.90, 0.55],
            [0.10, 0.70, 0.90, 0.75],
            [0.10, 0.90, 0.90, 0.95],
        ]
        page_num = [[0.92, 0.95, 0.99, 0.98]]
        out = _reading_order_sort(body + page_num)  # type: ignore[arg-type]
        # Output must still respect top-to-bottom flow — i.e. body comes
        # before the page number, which sits at the bottom-right.
        ys = [b[1] for b in out]
        assert ys == sorted(ys)

    def test_three_columns_recurse(self):
        col_x = [(0.05, 0.30), (0.40, 0.60), (0.70, 0.95)]
        boxes: list[list[float]] = []
        # Build 3 boxes per column at distinct y values, interleaved.
        for y in (0.10, 0.40, 0.70):
            for x0, x1 in col_x:
                boxes.append([x0, y, x1, y + 0.05])

        out = _reading_order_sort(boxes)  # type: ignore[arg-type]
        # Column 1 (x≈0.18) → 3 boxes, then column 2 (x≈0.50) → 3,
        # then column 3 (x≈0.83) → 3.
        x_centers = [(b[0] + b[2]) / 2 for b in out]
        assert x_centers[:3] == sorted(x_centers[:3])
        # Each contiguous run of 3 should share the same column band.
        for start in (0, 3, 6):
            xs = x_centers[start : start + 3]
            assert max(xs) - min(xs) < 0.1, f"column band leaked at {start}"
        # Across runs, x must be increasing (left → right).
        assert x_centers[0] < x_centers[3] < x_centers[6]


# --- Audit P2-9: shared predictor singleton + batch resilience -------------


def _png_bytes(size: tuple[int, int] = (100, 80)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, format="PNG")
    return buf.getvalue()


def _pred_with_boxes() -> object:
    return types.SimpleNamespace(
        bboxes=[types.SimpleNamespace(bbox=(10.0, 10.0, 50.0, 40.0))]
    )


class _StubPredictor:
    """Callable stand-in for Surya's DetectionPredictor."""

    def __init__(self, results_per_call: list[object]) -> None:
        self.results_per_call = list(results_per_call)
        self.calls = 0

    def __call__(self, images):
        idx = min(self.calls, len(self.results_per_call) - 1)
        self.calls += 1
        return self.results_per_call[idx]


class TestSharedPredictorSingleton:
    def test_shared_predictor_and_aligner_are_reused(self, monkeypatch):
        from omniscribe.core import aligner as aligner_mod

        aligner_mod.reset_shared_detection_predictor()
        aligner_mod.reset_shared_hybrid_aligner()
        monkeypatch.setattr(aligner_mod, "DetectionPredictor", lambda: object())
        try:
            p1 = aligner_mod.get_shared_detection_predictor()
            p2 = aligner_mod.get_shared_detection_predictor()
            assert p1 is p2

            a1 = aligner_mod.get_shared_hybrid_aligner()
            a2 = aligner_mod.get_shared_hybrid_aligner()
            assert a1 is a2
            assert a1.detection_predictor is p1

            # A default-constructed aligner joins the same singleton.
            assert HybridAligner().detection_predictor is p1
        finally:
            aligner_mod.reset_shared_detection_predictor()
            aligner_mod.reset_shared_hybrid_aligner()


class TestDetectionBatchResilience:
    def test_mismatched_batch_degrades_to_empty_pages(self):
        # One prediction for two pages: the strict zip must fire and the
        # chunk must degrade to no detected boxes instead of raising.
        pred = _StubPredictor([[object()]])
        aligner = HybridAligner(detection_predictor=pred)
        result = aligner.get_detected_boxes_batch([_png_bytes(), _png_bytes((90, 120))])
        assert result == [[], []]
        assert pred.calls == 1

    def test_all_empty_batch_retries_without_rebuilding_predictor(self):
        # First call: every page empty. The retry loop must re-run
        # detection on the SAME predictor (no model rebuild) and pick up
        # the boxes the second pass finds.
        pred = _StubPredictor(
            [
                [types.SimpleNamespace(bboxes=[])],
                [_pred_with_boxes()],
            ]
        )
        aligner = HybridAligner(detection_predictor=pred)
        result = aligner.get_detected_boxes_batch([_png_bytes()])
        assert aligner.detection_predictor is pred
        assert pred.calls == 2
        assert len(result) == 1 and len(result[0]) == 1
        assert all(0.0 <= v <= 1.0 for v in result[0][0])


class TestPreDecodedImageEntryPoint:
    """``get_detected_boxes_from_images`` is the additive, decode-free
    sibling of ``get_detected_boxes_batch``.

    The layout stage uses it to hand over the images ``decode_chunk_bytes``
    already produced instead of paying for a second decode of the same
    bytes. Because it is a pure de-duplication, both entry points must
    produce identical boxes for identical pixels.
    """

    def test_image_and_byte_paths_return_identical_boxes(self) -> None:
        pred = _StubPredictor([[_pred_with_boxes()], [_pred_with_boxes()]])
        aligner = HybridAligner(detection_predictor=pred)
        # Page is 100x80, bbox (10, 10, 50, 40) normalizes to
        # (0.1, 0.125, 0.5, 0.5).
        expected = [[(0.1, 0.125, 0.5, 0.5)]]
        assert aligner.get_detected_boxes_batch([_png_bytes((100, 80))]) == expected
        from_images = aligner.get_detected_boxes_from_images(
            [Image.new("RGB", (100, 80), "white")]
        )
        assert from_images == expected
        # Both entry points reached the predictor exactly once.
        assert pred.calls == 2

    def test_image_path_converts_non_rgb_like_the_byte_path(self) -> None:
        # A grayscale page is converted to RGB before detection so the
        # normalized coordinates are computed from identical pixels.
        pred = _StubPredictor([[_pred_with_boxes()]])
        aligner = HybridAligner(detection_predictor=pred)
        result = aligner.get_detected_boxes_from_images(
            [Image.new("L", (100, 80), "white")]
        )
        assert result == [[(0.1, 0.125, 0.5, 0.5)]]

    def test_empty_inputs_short_circuit(self) -> None:
        aligner = HybridAligner(detection_predictor=_StubPredictor([]))
        assert aligner.get_detected_boxes_from_images([]) == []
        assert aligner.get_detected_boxes_batch([]) == []


class TestNaNBboxHandling:
    """F1.6 audit fix (HIGH): Surya occasionally returns NaN/inf
    bbox coordinates. The aligner must drop those boxes (not silently
    drop them via the downstream ``cx1 > cx0`` filter) and log a
    per-page count so operators see the regression in the server log.
    """

    def test_nan_bboxes_are_dropped_and_counted(self, caplog) -> None:
        import logging

        # A prediction with one valid box, one NaN box, and one inf box.
        # The page image is 100x100, so the valid box normalizes cleanly
        # to (0.1, 0.1, 0.5, 0.4). The NaN and inf boxes are dropped.
        pred = _StubPredictor(
            [
                [
                    types.SimpleNamespace(
                        bboxes=[
                            types.SimpleNamespace(bbox=(10.0, 10.0, 50.0, 40.0)),
                            types.SimpleNamespace(
                                bbox=[float("nan"), 10.0, 50.0, 40.0]
                            ),
                            types.SimpleNamespace(
                                bbox=[10.0, 10.0, float("inf"), 40.0]
                            ),
                        ]
                    )
                ]
            ]
        )
        aligner = HybridAligner(detection_predictor=pred)

        with caplog.at_level(logging.WARNING, logger="omniscribe.core.aligner"):
            result = aligner.get_detected_boxes_batch([_png_bytes()])

        # Only the valid box survives.
        assert len(result) == 1
        assert len(result[0]) == 1
        assert all(0.0 <= v <= 1.0 for v in result[0][0])

        # The warning was logged with the NaN/inf count.
        warnings = [r.message for r in caplog.records if "NaN" in r.message]
        assert len(warnings) == 1
        assert "1 NaN" in warnings[0]
        assert "1 inf" in warnings[0]

    def test_pure_nan_page_returns_empty_with_warning(self, caplog) -> None:
        import logging

        # A page whose every box is NaN — common if Surya's batch
        # output is corrupted for a single image. The page degrades
        # to no detected boxes; the warning still fires so the
        # regression is visible. (The aligner also retries the
        # detection on fully-empty pages, so the warning may fire
        # more than once; we only assert that it fires at least
        # once.)
        pred = _StubPredictor(
            [
                [
                    types.SimpleNamespace(
                        bboxes=[
                            types.SimpleNamespace(bbox=[float("nan")] * 4),
                        ]
                    )
                ]
            ]
        )
        aligner = HybridAligner(detection_predictor=pred)

        with caplog.at_level(logging.WARNING, logger="omniscribe.core.aligner"):
            result = aligner.get_detected_boxes_batch([_png_bytes()])

        assert result == [[]]
        warnings = [r.message for r in caplog.records if "NaN" in r.message]
        assert len(warnings) >= 1, "expected at least one NaN warning"


# --- Audit L5: tqdm_patch.apply() must run before the surya import --------


class TestTqdmPatchImportOrder:
    """Regression tests for the import-time ordering constraint documented
    in AGENTS.md and the audit L5 finding.

    Surya 0.17.x captures ``tqdm`` at import time. If the monkey-patch
    runs *after* the surya import, the server log gets spammed with
    progress bars — see the comment block above ``tqdm_patch.apply()`` in
    ``core/aligner.py``. The constraint is also spelled out in AGENTS.md
    ("Keep ``tqdm_patch.apply()`` before ``from surya.detection import
    DetectionPredictor`` in ``core/aligner.py``").
    """

    def test_tqdm_silenced_after_aligner_import(self) -> None:
        """Importing ``omniscribe.core.aligner`` must apply the tqdm patch.

        If the test_aligner.py module is loaded (it is, otherwise we
        couldn't be running), then ``omniscribe.core.aligner`` is too —
        so ``tqdm.tqdm`` should now be the SilentTqdm class. If a future
        refactor moves the call into a lazy initializer that doesn't run
        at import, this assertion will catch it.
        """
        import tqdm as _tqdm

        from omniscribe.utils.tqdm_patch import SilentTqdm

        # Reload-tolerance: even if some other test re-ran apply() with a
        # newer SilentTqdm class object, the identity contract still
        # requires it to come from the same module.
        assert _tqdm.tqdm is SilentTqdm, (
            "tqdm_patch.apply() did not run during aligner import; "
            "Surya will spam progress bars. See the comment block above "
            "tqdm_patch.apply() in core/aligner.py."
        )

    def test_apply_precedes_surya_import_in_source(self) -> None:
        """AST-level guard: ``tqdm_patch.apply()`` must appear in the
        aligner source *before* ``from surya.detection import ...``.

        Catches accidental reordering during a refactor — the AGENTS.md
        constraint is "byte-for-byte" so the literal line numbers are
        what the contract checks. Source-relative path keeps the test
        portable across repo layouts.
        """
        src_path = (
            pathlib.Path(__file__).resolve().parents[2]
            / "src"
            / "omniscribe"
            / "core"
            / "aligner.py"
        )
        tree = ast.parse(src_path.read_text(encoding="utf-8"))

        apply_line: int | None = None
        surya_line: int | None = None
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "surya.detection":
                surya_line = node.lineno
            if (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
                and node.value.func.attr == "apply"
                and isinstance(node.value.func.value, ast.Name)
                and node.value.func.value.id == "tqdm_patch"
            ):
                apply_line = node.lineno

        assert apply_line is not None, "tqdm_patch.apply() call not found in aligner.py"
        assert surya_line is not None, (
            "from surya.detection import not found in aligner.py"
        )
        assert apply_line < surya_line, (
            f"AGENTS.md constraint violated: tqdm_patch.apply() "
            f"(line {apply_line}) must precede surya import "
            f"(line {surya_line}) in core/aligner.py"
        )


# ---------------------------------------------------------------------------
# F1.12 — detection predictor lock documented
# (re-homed from test_audit_medium_d1.py)
# ---------------------------------------------------------------------------


class TestDetectionPredictorLock:
    """F1.12 audit fix: the detection predictor lock is **intentional**
    (Surya is not documented as thread-safe for concurrent forward
    passes; a single GPU gains nothing from concurrent passes). We
    pin the behaviour with a regression test so a future refactor
    that silently removes the lock lands a failure here.
    """

    def test_shared_predictor_lock_exists(self) -> None:
        """The shared lock is a ``threading.Lock`` instance."""
        from omniscribe.core import aligner

        assert isinstance(aligner._shared_predictor_lock, type(threading.Lock()))

    def test_two_concurrent_detection_calls_serialize(self) -> None:
        """Two threads calling ``_shared_predictor_lock`` acquire it
        serially (the second one blocks until the first releases).

        This is the contract the F1.12 comment block depends on;
        a future refactor that switches to a no-op or per-batch lock
        would change this behaviour and should update the test.
        """
        from omniscribe.core import aligner

        order: list[str] = []
        order_lock = threading.Lock()

        def worker(name: str) -> None:
            with aligner._shared_predictor_lock:
                with order_lock:
                    order.append(f"{name}-acquired")
                # Hold the lock long enough for the other thread to
                # try to acquire it. If the lock were a no-op the
                # other thread would interleave here.
                import time

                time.sleep(0.05)
                with order_lock:
                    order.append(f"{name}-released")

        t1 = threading.Thread(target=worker, args=("a",))
        t2 = threading.Thread(target=worker, args=("b",))
        t1.start()
        t2.start()
        t1.join(timeout=2.0)
        t2.join(timeout=2.0)

        # Exactly one thread should be holding the lock at a time:
        # either a-acquired, a-released, b-acquired, b-released
        # OR b-acquired, b-released, a-acquired, a-released.
        # If the lock were a no-op we'd see interleaving (e.g.
        # a-acquired, b-acquired, a-released, b-released).
        assert len(order) == 4
        # The two acquire events must not be adjacent.
        acquire_indices = [i for i, e in enumerate(order) if "acquired" in e]
        assert acquire_indices[1] - acquire_indices[0] == 2, (
            f"detection lock did not serialise — order was {order}"
        )
