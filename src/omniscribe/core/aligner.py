"""
HybridAligner - Detection-only OCR aligner.

Uses Surya's `DetectionPredictor` (fast) for bounding boxes and binds
LLM-produced text to those boxes using a monotonic Needleman-Wunsch DP
over (LLM lines, detected boxes). Skipping Surya's recognition step is
~10-21x faster; the LLM supplies the text quality.
"""

import io
import logging
import math
import threading

from omniscribe.utils import tqdm_patch

# --- tqdm progress-bar suppression ----------------------------------------
# Surya 0.17.x binds to `tqdm` at import time, so this monkey-patch MUST
# run before `from surya.detection import DetectionPredictor` below — once
# Surya has captured its `tqdm` reference the patch is too late and the
# server log gets spammed with progress bars.
#
# This is the same constraint that bans moving the call into a lazy
# initializer: the surya import on the next line is unconditional, and
# `tqdm_patch.apply()` is the only thing that stops Surya's noisy bars.
#
# AGENTS.md: "Keep `tqdm_patch.apply()` before `from surya.detection
# import DetectionPredictor` in `core/aligner.py`." Do not move.
tqdm_patch.apply()

from PIL import Image  # noqa: E402
from surya.detection import DetectionPredictor  # noqa: E402

from omniscribe.core.document import BBox  # noqa: E402

logger = logging.getLogger(__name__)


# Audit P2-9: ``DetectionPredictor`` loads Surya's model weights (seconds of
# work plus VRAM). The pipeline factory used to construct a fresh
# ``HybridAligner`` — and therefore a fresh predictor — on every request.
# The predictor is now a process-wide singleton, mirroring the
# circuit-breaker registry pattern in ``core.ocr.resilience``.
_shared_predictor: DetectionPredictor | None = None
# F1.12 audit investigation: this lock is **intentional**, not an
# accidental bottleneck. The trade-off:
#
#   1. Surya's ``DetectionPredictor`` is **not** documented as
#      thread-safe for concurrent forward passes (it owns a torch
#      model on a single device; two concurrent passes contend for
#      the same CUDA stream / MPS queue).
#   2. A local single-GPU target gains nothing from concurrent
#      detection — the second pass just queues behind the first and
#      the wall-clock is dominated by serialised forward time.
#   3. The lock is process-wide (one shared detector, one shared
#      lock), so multi-process workers get true parallelism; the
#      bottleneck only applies to requests hitting the same uvicorn
#      worker.
#
# A future maintainer who wants intra-process parallel detection
# should:
#   - Confirm Surya's thread-safety on the target device (CUDA +
#     PyTorch + a pinned batch size can be safe, but it is *not*
#     guaranteed by the upstream contract).
#   - Replace ``_shared_predictor_lock`` with a per-batch lock
#     (``threading.Semaphore(MAX_CONCURRENT_BATCHES)``), keeping the
#     init lock for the singleton construction.
#   - Verify throughput with a regression benchmark before flipping
#     the default; the audit's worry was that removing the lock
#     without confirming Surya's thread-safety was a silent
#     correctness regression.
#
# The current behaviour is the safe default. We document it here and
# pin it with a regression test in ``tests/core/test_aligner.py`` so a
# future refactor that "removes the unnecessary lock" lands a test
# failure rather than a torch device-side race.
_shared_predictor_lock = threading.Lock()


def get_shared_detection_predictor() -> DetectionPredictor:
    """Return the process-wide Surya :class:`DetectionPredictor`.

    Lazily constructed on first call; subsequent calls return the same
    instance so concurrent requests share one loaded model instead of
    each paying the load cost (and holding a second resident copy in
    VRAM). Detection calls through :class:`HybridAligner` are serialized
    on the same lock, which is the right behaviour for a local GPU
    anyway — two concurrent forward passes would contend for the same
    device.
    """
    global _shared_predictor
    if _shared_predictor is None:
        with _shared_predictor_lock:
            if _shared_predictor is None:
                _shared_predictor = DetectionPredictor()
    return _shared_predictor


def reset_shared_detection_predictor() -> None:
    """Drop the shared predictor. Test helper."""
    global _shared_predictor
    with _shared_predictor_lock:
        _shared_predictor = None


_shared_aligner: "HybridAligner | None" = None
_shared_aligner_lock = threading.Lock()


def get_shared_hybrid_aligner() -> "HybridAligner":
    """Return the process-wide shared :class:`HybridAligner`.

    The aligner is stateless apart from the predictor it wraps, so the
    API-layer pipeline factory reuses one instance across requests
    instead of constructing (and model-loading) a new one each time.
    """
    global _shared_aligner
    if _shared_aligner is None:
        with _shared_aligner_lock:
            if _shared_aligner is None:
                _shared_aligner = HybridAligner()
    return _shared_aligner


def reset_shared_hybrid_aligner() -> None:
    """Drop the shared aligner. Test helper."""
    global _shared_aligner
    with _shared_aligner_lock:
        _shared_aligner = None


class HybridAligner:
    """
    Detection-only aligner with DP-based line-to-box alignment.

    Phase 1 (detect): Surya produces layout boxes, no text.
    Phase 2 (align): LLM-produced lines are mapped to boxes using
    Needleman-Wunsch, with cost proportional to how well each line's
    character count matches each box's estimated capacity (area-based),
    monotonic so reading order is preserved. Skip ops on both sides
    handle missing/extra lines and boxes. Unmatched LLM lines are
    attached to the nearest matched box so no text is lost.
    """

    def __init__(self, detection_predictor: DetectionPredictor | None = None) -> None:
        # Default to the process-wide shared predictor (audit P2-9). An
        # injected predictor gets its own detection lock so it never
        # contends with the shared one.
        if detection_predictor is None:
            self.detection_predictor = get_shared_detection_predictor()
            self._detection_lock = _shared_predictor_lock
        else:
            self.detection_predictor = detection_predictor
            self._detection_lock = threading.Lock()

    def get_detected_boxes_batch(
        self, images_bytes_list: list[bytes]
    ) -> list[list[BBox]]:
        """
        Run Surya detection on a batch of page images in a single call.
        Returns one list of normalized boxes per page, sorted in reading order.

        This is the byte-oriented entry point kept for every existing caller
        and for duck-typed stand-ins that only implement this method. It
        simply decodes the bytes and delegates to
        :meth:`get_detected_boxes_from_images`, so both paths run identical
        post-decode logic and return byte-identical results.
        """
        if not images_bytes_list:
            return []

        # H1 audit fix: ``with`` block guarantees buffer close for every
        # image in the batch list. We load each image inside its own
        # context manager so the underlying BytesIO is closed even though
        # we copy the loaded pixel data into a new RGB image.
        images: list[Image.Image] = []
        for b in images_bytes_list:
            with Image.open(io.BytesIO(b)) as src:
                src.load()
                images.append(src.copy())
        return self.get_detected_boxes_from_images(images)

    def get_detected_boxes_from_images(
        self, images: list[Image.Image]
    ) -> list[list[BBox]]:
        """
        Run Surya detection on a batch of already-decoded page images.

        Same contract as :meth:`get_detected_boxes_batch` — one list of
        normalized boxes per page, sorted in reading order — but it accepts
        decoded :class:`PIL.Image.Image` objects so a caller that already
        paid for the JPEG decode (e.g. the layout stage, via
        ``decode_chunk_bytes`` and its ``on_decoded`` cache callback) does not
        force a second identical decode.
        """
        if not images:
            return []
        images = [img.convert("RGB") if img.mode != "RGB" else img for img in images]
        sizes = [img.size for img in images]

        def run_detection() -> list[list[BBox]] | None:
            # Detection is serialized on the predictor's lock — the shared
            # predictor (audit P2-9) must never run two forward passes at
            # once, and a local GPU gains nothing from concurrent passes.
            with self._detection_lock:
                predictions = self.detection_predictor(images)
            try:
                # Audit P2-9: ``strict=True`` — a short/long Surya batch is
                # a real contract violation. The old ``strict=False`` zip
                # silently dropped the tail pages, then raised an
                # ``IndexError`` far away from here when per-page code
                # reached past the truncated list and killed the whole job.
                pairs = list(zip(sizes, predictions, strict=True))
            except ValueError:
                return None
            all_boxes: list[list[BBox]] = []
            for (img_w, img_h), pred in pairs:
                boxes: list[BBox] = []
                # F1.6 audit fix: Surya occasionally returns NaN/inf
                # for bbox coordinates. Previously these slipped
                # through ``_clamp`` (NaN propagates; inf expands to a
                # full-page box), and the silent drop was invisible
                # to operators. We now count and log per page so a
                # Surya regression surfaces in the server log.
                nan_count = 0
                inf_count = 0
                for bbox in pred.bboxes or []:
                    x0, y0, x1, y1 = bbox.bbox
                    if any(math.isnan(v) for v in (x0, y0, x1, y1)):
                        nan_count += 1
                        continue
                    if any(math.isinf(v) for v in (x0, y0, x1, y1)):
                        inf_count += 1
                        continue
                    cx0 = _clamp(x0 / img_w)
                    cy0 = _clamp(y0 / img_h)
                    cx1 = _clamp(x1 / img_w)
                    cy1 = _clamp(y1 / img_h)
                    if cx1 > cx0 and cy1 > cy0:
                        boxes.append((cx0, cy0, cx1, cy1))
                if nan_count or inf_count:
                    logger.warning(
                        "Surya returned %d NaN and %d inf bboxes for image "
                        "%sx%s; dropped",
                        nan_count,
                        inf_count,
                        img_w,
                        img_h,
                    )
                # Stable row-major default. The actual reading-order choice for
                # the DP happens inside align_text, which tries both row-major
                # and column-major orderings and picks the lower-cost result.
                boxes.sort(key=lambda b: (b[1], b[0]))
                all_boxes.append(boxes)
            return all_boxes

        all_boxes = run_detection()
        if all_boxes is None:
            # Chunk-level degradation (audit P2-9): a mismatched batch
            # degrades this detection chunk to no detected boxes rather
            # than aborting the job. Alignment falls back to a full-page
            # box per page, so the LLM text still lands in the output.
            logger.warning(
                "Surya returned a mismatched prediction count for %d page(s); "
                "degrading chunk to no detected boxes.",
                len(images),
            )
            return [[] for _ in images]
        # Surya can occasionally return an entirely empty batch immediately
        # after predictor initialization on local backends. Retry only when every
        # page is empty; a real blank page inside a mixed batch must stay blank.
        # Audit P2-9: retries reuse the (now shared) predictor instead of
        # rebuilding it — rebuilding was loading the model weights up to
        # three more times for legitimately blank scans.
        for attempt in range(1, 4):
            if any(all_boxes):
                break
            logger.warning(
                "Surya returned an entirely empty batch; retrying detection "
                "(%d/3) on the same predictor.",
                attempt,
            )
            retried = run_detection()
            if retried is None:
                return [[] for _ in images]
            all_boxes = retried
        return all_boxes

    def align_text(
        self,
        structured_data: list[tuple[BBox, str]],
        llm_text: str | list[str],
    ) -> list[tuple[BBox, str]]:
        """
        Bind LLM text to detected boxes via Needleman-Wunsch line-to-box DP.

        VLMs disagree on reading order: OlmOCR-2 emits column-major on
        multi-column pages (entire left column, then right), while other
        VLMs may emit row-major (top-to-bottom across the whole page).
        We can't normalize this via prompt because OlmOCR's prompt is
        RL-locked. Instead, the DP is run twice — once with boxes in
        row-major order and once in column-major — and whichever gives
        the lower total cost wins. The DP cost itself is a robust signal
        for which ordering matches the LLM's emission, so the same code
        path works for any model without per-model branching.

        Always returns one (box, text) tuple per input box, in input order.
        `text` is "" for boxes the algorithm could not confidently match —
        the pipeline uses that signal to trigger per-box crop re-OCR.
        """
        lines = _normalize_lines(llm_text)
        boxes = [item[0] for item in structured_data]

        if not boxes and not lines:
            return []
        if not boxes:
            # Degenerate: LLM produced text but Surya found nothing.
            # Embed all text in a full-page box so search still works.
            return [((0.0, 0.0, 1.0, 1.0), "\n".join(lines))]
        if not lines:
            return [(box, "") for box in boxes]

        # Permutations to try: row-major (y, x) and column-major
        # (column groups, then y within column). For single-column pages
        # both collapse to the same order — we still run both, but the DP
        # is O(N*M) which is negligible at typical page sizes (~30 boxes).
        row_major = sorted(range(len(boxes)), key=lambda i: (boxes[i][1], boxes[i][0]))
        col_major = _reading_order_indices(boxes)

        candidates = [row_major]
        if col_major != row_major:
            candidates.append(col_major)

        best_cost = float("inf")
        best_perm: list[int] = candidates[0]
        best_mapping: dict[int, list[str]] = {}
        best_match_count = 0
        for perm in candidates:
            ordered_boxes = [boxes[i] for i in perm]
            cost, mapping, match_count = _dp_align(lines, ordered_boxes)
            if cost < best_cost:
                best_cost = cost
                best_perm = perm
                best_mapping = mapping
                best_match_count = match_count

        # Degenerate-alignment safety net. Two failure modes pack all the
        # LLM text into one box and leave the rest empty (which users see
        # as "all text packed in the top-left corner"):
        #
        #   1. Zero real matches — every line was skip_line'd and attached
        #      to box 0 by the fallback. Unlikely with default cost
        #      constants but cheap to check.
        #   2. The LLM emitted ONE giant line but Surya found many boxes
        #      (e.g. an OlmOCR variant that doesn't break lines on
        #      handwritten content). The DP can only match that single
        #      line to one box; every other box stays empty. Output is
        #      effectively a single block in whichever box won the match.
        #
        # In both cases, embedding the LLM text in a single full-page bbox
        # keeps the output searchable across the whole page instead of
        # corralled into one corner. We only consider case 2 when there
        # are several boxes — a 1-line / 1-box page is the normal trivial
        # case and should keep the existing placement.
        is_zero_match = best_match_count == 0 and len(lines) > 1
        is_single_line_many_boxes = len(lines) == 1 and len(boxes) >= 5
        if is_zero_match or is_single_line_many_boxes:
            reason = (
                "no line→box matches"
                if is_zero_match
                else (
                    "LLM emitted a single line for many boxes — likely a model "
                    "variant that doesn't break visual lines"
                )
            )
            logger.warning(
                "Degenerate hybrid alignment: %s (lines=%d, boxes=%d). "
                "Falling back to a full-page text layer so output stays "
                "searchable. Try grounded mode or a different model.",
                reason,
                len(lines),
                len(boxes),
            )
            return [((0.0, 0.0, 1.0, 1.0), "\n".join(lines))]

        # Translate the per-perm-index mapping back to per-input-index text.
        text_per_input: list[str] = ["" for _ in boxes]
        for perm_idx, texts in best_mapping.items():
            text_per_input[best_perm[perm_idx]] = " ".join(texts).strip()

        logger.debug(
            "DEBUG: DP aligned %d lines → %d/%d boxes (cost=%.3f)",
            len(lines),
            best_match_count,
            len(boxes),
            best_cost,
        )
        return [(box, text) for box, text in zip(boxes, text_per_input, strict=False)]


# --- module-level helpers ---------------------------------------------------


def _clamp(v: float) -> float:
    """Clamp ``v`` to ``[0, 1]``.

    NaN is treated as ``0`` so the downstream ``cx1 > cx0`` / ``cy1 > cy0``
    check in :func:`get_detected_boxes_batch` drops the box rather than
    propagating NaN into the DP. ``inf`` is clamped to ``1`` by ``max(0,
    min(1, v))``; we also explicitly drop boxes with any inf coordinate
    upstream of this function so a single inf doesn't expand to a
    full-page box.
    """
    if math.isnan(v):
        return 0.0
    return max(0.0, min(1.0, v))


# Threshold (in normalized x-center space) above which a gap between
# consecutive boxes is treated as a column break. 2-column page gutters
# tend to push x-center distance well past 0.2 (typical column-center
# separation is ~0.3-0.5). Lower values risk treating a single marginal
# box (page number, sidebar note) as its own column and re-ordering it
# ahead of body text.
_COLUMN_GAP_THRESHOLD = 0.2


def _reading_order_indices(boxes: list[BBox], depth: int = 0) -> list[int]:
    """
    Permutation of ``boxes`` indices in column-major reading order.

    Multi-column pages: split at the largest x-center gap if it exceeds
    ``_COLUMN_GAP_THRESHOLD`` AND both sides hold ≥2 boxes. The size
    constraint prevents a lone marginal box (e.g. a page number) from
    creating a fake column that swaps reading order. Recurses to handle
    3+ column layouts; falls back to plain row-major for single-column
    pages and very short sequences. Limits recursion to `depth < 50` to
    prevent stack overflow on adversarial layouts.
    """
    n = len(boxes)
    if n < 4 or depth >= 50:
        return sorted(range(n), key=lambda i: (boxes[i][1], boxes[i][0]))

    sorted_idx = sorted(range(n), key=lambda i: (boxes[i][0] + boxes[i][2]) / 2)
    centers_sorted = [(boxes[i][0] + boxes[i][2]) / 2 for i in sorted_idx]

    biggest_gap = 0.0
    biggest_gap_pos = -1
    for k in range(1, len(centers_sorted)):
        gap = centers_sorted[k] - centers_sorted[k - 1]
        if gap > biggest_gap:
            biggest_gap = gap
            biggest_gap_pos = k

    if (
        biggest_gap < _COLUMN_GAP_THRESHOLD
        or biggest_gap_pos < 2
        or biggest_gap_pos > len(centers_sorted) - 2
    ):
        return sorted(range(n), key=lambda i: (boxes[i][1], boxes[i][0]))

    left_indices = sorted_idx[:biggest_gap_pos]
    right_indices = sorted_idx[biggest_gap_pos:]

    # Recurse on each side, mapping sub-permutations back to global indices.
    left_subboxes = [boxes[i] for i in left_indices]
    right_subboxes = [boxes[i] for i in right_indices]
    left_perm = _reading_order_indices(left_subboxes, depth + 1)
    right_perm = _reading_order_indices(right_subboxes, depth + 1)
    return [left_indices[k] for k in left_perm] + [right_indices[k] for k in right_perm]


def _reading_order_sort(boxes: list[BBox]) -> list[BBox]:
    """
    Return ``boxes`` sorted in column-major reading order.

    Thin wrapper around :func:`_reading_order_indices`. Kept as a public-ish
    helper for tests and diagnostic scripts.
    """
    return [boxes[i] for i in _reading_order_indices(boxes)]


def _normalize_lines(llm_text: str | list[str]) -> list[str]:
    """Accept str or list[str]; return non-empty stripped lines."""
    if not llm_text:
        return []
    if isinstance(llm_text, str):
        raw = llm_text.split("\n")
    else:
        raw = []
        for item in llm_text:
            raw.extend(str(item).split("\n"))
    return [s.strip() for s in raw if s and s.strip()]


# Cost constants — tuned to favor matching over skipping, with cheap box-skips
# (many detected boxes are decorative / rules / empty regions) and relatively
# expensive line-skips (LLM text should all land somewhere).
_SKIP_LINE_COST = 1.0
_SKIP_BOX_COST = 0.4


def _estimated_capacities(boxes: list[BBox]) -> list[float]:
    """
    Estimate each box's character capacity from its normalized area.

    We don't know the actual font size, so we calibrate relatively: compute
    area and treat the total area as proportional to total chars. What matters
    for the DP is the relative capacity across boxes, not absolute chars.
    """
    # Area proxy for "how much text fits here". Taller boxes hold wrapped
    # text; wider boxes hold long lines. Using area captures both.
    areas = [max(1e-6, abs(b[2] - b[0]) * abs(b[3] - b[1])) for b in boxes]
    return areas


def _match_cost(line_chars: int, expected_chars: float) -> float:
    """
    Asymmetric char-count mismatch cost in [0, 1].

    Over-fill (line longer than the box's area-derived capacity) is
    penalized harder than equivalent under-fill. Rationale: a box's
    expected capacity is proportional to area at typical font size, so
    when a line's character count substantially exceeds that capacity
    the line cannot fit at the same density — it would force the
    embedding to compress horizontally and visually misregister against
    the source layout. Under-fill is benign: a short line in a wide box
    just leaves slack.

    Symptom this fixes: when Surya splits a wrapped paragraph into N
    boxes but the LLM joins it into 1 line, the symmetric cost gave
    near-zero penalty for matching short LLM lines (e.g. ``Date: 9/14/19``)
    to the wrong narrow box just because the char counts happened to
    align, displacing every subsequent line by one box. The empty
    "real" box was then filled by the refine stage, producing a visible
    duplicate in the OCR text layer.

    Formula notes:
    - Overfill uses ``(actual-expected)/actual`` so the cost is bounded
      in [0, 1) and matches the original symmetric-cost shape on the
      overfill side. Mild overfills stay matchable (cheaper than
      ``skip_box + skip_line``); only severe overfills approach 1.
    - Underfill uses ``(expected-actual)/expected * 0.5`` so a short
      line in a wide box costs at most 0.5, and a perfect-width-but-
      short line costs ~0. This breaks symmetry: a 2x overfill costs
      ~0.5 but the same 0.5x under-fill costs only 0.25.
    """
    expected = max(1.0, expected_chars)
    actual = max(1, line_chars)
    if actual > expected:
        # Overfill: bounded in [0, 1).
        return (actual - expected) / actual
    # Underfill: halved.
    return (expected - actual) / expected * 0.5


def _dp_align(
    lines: list[str], boxes: list[BBox]
) -> tuple[float, dict[int, list[str]], int]:
    """
    Monotonic Needleman-Wunsch alignment of lines → boxes.

    Returns: ``(total_cost, mapping, match_count)`` where ``mapping`` is
    ``{box_index: [line_text, ...]}``, ``total_cost`` is the DP's
    minimum alignment cost, and ``match_count`` is the number of
    ``op=match`` operations the DP made (i.e. real line→box pairings,
    excluding lines attached via the skip-line fallback). The cost
    lets callers compare the same alignment under different box
    orderings; ``match_count`` lets callers detect a degenerate
    alignment where every line was skipped and dumped onto box 0.

    Unmatched lines are attached to the nearest preceding matched box
    (or the first box if no prior match exists) so no LLM text is lost.
    """
    N = len(lines)
    M = len(boxes)
    if N == 0 or M == 0:
        return 0.0, {}, 0
    total_chars = max(1, sum(len(line) for line in lines))

    # Distribute total chars across boxes by area.
    caps = _estimated_capacities(boxes)
    total_cap = sum(caps)
    expected = [c / total_cap * total_chars for c in caps]

    INF = float("inf")
    dp = [[INF] * (M + 1) for _ in range(N + 1)]
    # Back-pointer ops: 0 = match, 1 = skip_line, 2 = skip_box
    back = [[0] * (M + 1) for _ in range(N + 1)]
    dp[0][0] = 0.0

    # Boundary rows/cols: cumulative gap penalties.
    for j in range(1, M + 1):
        dp[0][j] = dp[0][j - 1] + _SKIP_BOX_COST
        back[0][j] = 2
    for i in range(1, N + 1):
        dp[i][0] = dp[i - 1][0] + _SKIP_LINE_COST
        back[i][0] = 1

    line_lens = [len(line) for line in lines]
    for i in range(1, N + 1):
        line_len = line_lens[i - 1]
        dp_i_prev = dp[i - 1]
        dp_i = dp[i]
        back_i = back[i]
        for j in range(1, M + 1):
            m_cost = dp_i_prev[j - 1] + _match_cost(line_len, expected[j - 1])
            sl_cost = dp_i_prev[j] + _SKIP_LINE_COST
            sb_cost = dp_i[j - 1] + _SKIP_BOX_COST

            best = m_cost
            op = 0
            if sl_cost < best:
                best, op = sl_cost, 1
            if sb_cost < best:
                best, op = sb_cost, 2
            dp_i[j] = best
            back_i[j] = op

    # Backtrack to produce the ordered op list.
    mapping: dict[int, list[str]] = {}
    i, j = N, M
    ops: list[tuple[int, int, int]] = []  # (op, line_idx, box_idx)
    while i > 0 or j > 0:
        op = back[i][j]
        if op == 0 and i > 0 and j > 0:
            ops.append((0, i - 1, j - 1))
            i, j = i - 1, j - 1
        elif op == 1 and i > 0:
            ops.append((1, i - 1, j - 1 if j > 0 else -1))
            i -= 1
        elif op == 2 and j > 0:
            ops.append((2, i - 1 if i > 0 else -1, j - 1))
            j -= 1
        else:  # safety: shouldn't happen but avoids infinite loop
            if i > 0:
                i -= 1
            elif j > 0:
                j -= 1
    ops.reverse()

    # Replay ops in reading order. Track the most recent matched box so that
    # skip_line ops can attach their text to it (no lost LLM text).
    last_matched_box: int | None = None
    match_count = 0
    for op, li, bj in ops:
        if op == 0:
            mapping.setdefault(bj, []).append(lines[li])
            last_matched_box = bj
            match_count += 1
        elif op == 1 and li >= 0:
            # Unmatched line: attach to last matched box, or to first box
            # if we haven't matched anything yet.
            target = last_matched_box if last_matched_box is not None else 0
            mapping.setdefault(target, []).append(lines[li])
        # op == 2 (skip_box): nothing to add for this box
    return dp[N][M], mapping, match_count
