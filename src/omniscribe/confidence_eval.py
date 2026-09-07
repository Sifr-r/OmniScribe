"""
Confidence evaluation: compare pipeline output against ground-truth fixtures.

Ground-truth fixtures are in Z.AI hosted GLM-OCR response format:
    {"data": {"layout": [{"block_content": "...", "bbox": [...],
                          "block_label": "text", "page_index": 0}, ...],
              "data_info": {"pages": [{"width": W, "height": H}, ...]}}}

Bbox convention auto-detection: the captured fixtures sometimes use
`[x0, y0, x1, y1]` (handwritten.pdf) and sometimes `[y0, x0, y1, x1]`
(hybrid.pdf / digital.pdf). `_detect_bbox_axis_order` infers which by
aspect ratio across the fixture's boxes and normalizes to `[x0, y0, x1, y1]`
before comparison.

Metrics per document:
    - block_recall: fraction of GT blocks matched with IoU >= threshold
    - text_similarity: avg difflib ratio over matched (text-normalized) pairs
    - unmatched: GT blocks with no sufficient pipeline counterpart

Blocks are matched greedily by IoU (best available pipeline box per GT box),
without replacement. Not optimal in the Hungarian sense, but deterministic
and close enough for a confidence summary.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import TypeVar

from omniscribe.core.document import BBox
from omniscribe.utils.text import normalize_text  # L-7 audit: shared helper

T = TypeVar("T")

try:
    import sacrebleu

    _HAS_SACREBLEU = True
except ImportError:
    _HAS_SACREBLEU = False

# --- data classes ----------------------------------------------------------


@dataclass
class GTBlock:
    bbox: BBox  # normalized [x0, y0, x1, y1] in 0..1
    text: str
    page_index: int = 0
    label: str = "text"


# Labels that describe *structural* regions rather than selectable text —
# skipping them keeps the confidence eval focused on content blocks.
NON_CONTENT_LABELS = frozenset(
    {
        "image",
        "empty_line",  # underline placeholder for unfilled fields
        "signature_line",  # "_______________________ _______________________"
        "list_marker",  # bare "-" / bullet glyphs
    }
)


@dataclass
class BlockMatch:
    gt_text: str
    gt_bbox: BBox
    pipeline_text: str | None
    pipeline_bbox: BBox | None
    iou: float
    text_similarity: float  # 0..1, difflib ratio


@dataclass
class ConfidenceReport:
    document: str
    iou_threshold: float
    gt_count: int
    pipeline_count: int
    matches: list[BlockMatch] = field(default_factory=list)

    @property
    def matched(self) -> list[BlockMatch]:
        return [m for m in self.matches if m.iou >= self.iou_threshold]

    @property
    def block_recall(self) -> float:
        return len(self.matched) / max(1, self.gt_count)

    @property
    def avg_text_similarity(self) -> float:
        ms = self.matched
        if not ms:
            return 0.0
        return sum(m.text_similarity for m in ms) / len(ms)

    @property
    def avg_iou(self) -> float:
        ms = self.matched
        if not ms:
            return 0.0
        return sum(m.iou for m in ms) / len(ms)

    def summary_line(self) -> str:
        return (
            f"{self.document:<20} "
            f"gt={self.gt_count:<3} "
            f"pipeline={self.pipeline_count:<3} "
            f"matched={len(self.matched):<3} "
            f"recall={self.block_recall:.2f} "
            f"iou_avg={self.avg_iou:.2f} "
            f"text_sim_avg={self.avg_text_similarity:.2f}"
        )


# --- bbox helpers ----------------------------------------------------------


def iou(a: BBox, b: BBox) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, (ax1 - ax0)) * max(0.0, (ay1 - ay0))
    area_b = max(0.0, (bx1 - bx0)) * max(0.0, (by1 - by0))
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _detect_bbox_axis_order(raw_boxes: list[BBox]) -> str:
    """
    Return "xyxy" if boxes look like [x0,y0,x1,y1], else "yxyx".

    Heuristic: if the majority of boxes have (v3-v1) >> (v2-v0) — i.e.
    they're heavily "portrait" when interpreted as xyxy — they're almost
    certainly yxyx (height and width got swapped in the source).
    """
    portrait = 0
    counted = 0
    for b in raw_boxes:
        if len(b) != 4:
            continue
        w_xy = abs(b[2] - b[0])
        h_xy = abs(b[3] - b[1])
        if w_xy <= 0 or h_xy <= 0:
            continue
        counted += 1
        if h_xy > 1.5 * w_xy:
            portrait += 1
    if counted == 0:
        return "xyxy"
    return "yxyx" if portrait > counted / 2 else "xyxy"


def _swap_axes(b: BBox) -> BBox:
    """[y0, x0, y1, x1] -> [x0, y0, x1, y1]."""
    return (b[1], b[0], b[3], b[2])


# --- fixture loader --------------------------------------------------------


def load_ground_truth(
    fixture_path: Path | str,
) -> tuple[list[GTBlock], tuple[int, int]]:
    """
    Load a fixture JSON and return (blocks, (fixture_width, fixture_height)).

    Blocks are normalized to `[x0, y0, x1, y1]` in 0..1 space. Non-text
    blocks (label == "image") are skipped. Axis order is auto-detected.

    The on-disk JSON may be:
      * ``{"data": {"layout": [...], "data_info": ...}}`` (Z.AI / GLM-OCR wrapper)
      * ``{"layout": [...], "data_info": ...}`` (already unwrapped)
      * ``[ {...block...}, ... ]`` (raw layout array — list fixtures are
        normalized as ``{"layout": <list>}``)
    """
    with open(fixture_path) as f:
        data = json.load(f)

    # JSON load returns Any; constrain to dict[str, Any] for downstream use.
    d: dict
    if isinstance(data, list):
        # Raw layout array: no data_info.pages → the existing
        # "missing data_info.pages" guard will fire if a caller forgot it.
        d = {"layout": data}
    elif isinstance(data, dict):
        d = data.get("data", data)
    else:
        raise TypeError(
            f"{fixture_path}: expected JSON object or array, got {type(data).__name__}"
        )
    raw_layout = d.get("layout", [])
    raw_items = [
        b for b in raw_layout if b.get("block_label") not in NON_CONTENT_LABELS
    ]
    raw_boxes = [b["bbox"] for b in raw_items]

    order = _detect_bbox_axis_order(raw_boxes)
    pages = d.get("data_info", {}).get("pages", [])
    if not pages:
        raise ValueError(f"{fixture_path}: missing data_info.pages")
    fw = int(pages[0]["width"])
    fh = int(pages[0]["height"])

    blocks: list[GTBlock] = []
    for item in raw_items:
        bbox = item["bbox"]
        if order == "yxyx":
            bbox = _swap_axes(bbox)
        x0, y0, x1, y1 = bbox
        # Clamp and normalize. Use fixture-declared page dims — that's the
        # coord frame the bboxes were written against.
        blocks.append(
            GTBlock(
                bbox=(
                    max(0.0, min(1.0, x0 / fw)),
                    max(0.0, min(1.0, y0 / fh)),
                    max(0.0, min(1.0, x1 / fw)),
                    max(0.0, min(1.0, y1 / fh)),
                ),
                text=(item.get("block_content") or "").strip(),
                page_index=item.get("page_index", 0),
                label=item.get("block_label", "text"),
            )
        )
    return blocks, (fw, fh)


# --- text similarity -------------------------------------------------------


def text_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize_text(a), normalize_text(b)).ratio()


# --- matching --------------------------------------------------------------


def compute_report(
    document: str,
    ground_truth: list[GTBlock],
    pipeline_output: list[tuple[BBox, str]],
    iou_threshold: float = 0.3,
) -> ConfidenceReport:
    """
    Greedy best-IoU matching of GT blocks to pipeline blocks, no re-use.

    Small enough inputs (tens of blocks per page) that O(N*M) is fine.
    """
    used: set[int] = set()
    matches: list[BlockMatch] = []
    for gt in ground_truth:
        best_i, best_iou = -1, 0.0
        for i, (pbox, _ptext) in enumerate(pipeline_output):
            if i in used:
                continue
            score = iou(gt.bbox, pbox)
            if score > best_iou:
                best_iou, best_i = score, i
        if best_i >= 0 and best_iou >= iou_threshold:
            used.add(best_i)
            pbox, ptext = pipeline_output[best_i]
            matches.append(
                BlockMatch(
                    gt_text=gt.text,
                    gt_bbox=gt.bbox,
                    pipeline_text=ptext,
                    pipeline_bbox=pbox,
                    iou=best_iou,
                    text_similarity=text_similarity(gt.text, ptext),
                )
            )
        else:
            matches.append(
                BlockMatch(
                    gt_text=gt.text,
                    gt_bbox=gt.bbox,
                    pipeline_text=None,
                    pipeline_bbox=None,
                    iou=best_iou,
                    text_similarity=0.0,
                )
            )
    return ConfidenceReport(
        document=document,
        iou_threshold=iou_threshold,
        gt_count=len(ground_truth),
        pipeline_count=len(pipeline_output),
        matches=matches,
    )


# --- End-to-End PDF-to-Markdown Benchmark Scoring (RFC 004 R4) -------------


def compute_levenshtein_distance(seq1: Sequence[T], seq2: Sequence[T]) -> int:
    """Compute exact Levenshtein distance between two sequences (chars or tokens)."""
    if seq1 == seq2:
        return 0
    if not seq1:
        return len(seq2)
    if not seq2:
        return len(seq1)

    # Ensure seq2 is the shorter sequence to minimize memory in 2-row DP
    if len(seq1) < len(seq2):
        seq1, seq2 = seq2, seq1

    prev_row = list(range(len(seq2) + 1))
    for i, item1 in enumerate(seq1):
        curr_row = [i + 1] * (len(seq2) + 1)
        for j, item2 in enumerate(seq2):
            cost = 0 if item1 == item2 else 1
            curr_row[j + 1] = min(
                curr_row[j] + 1,       # insertion
                prev_row[j + 1] + 1,   # deletion
                prev_row[j] + cost,    # substitution
            )
        prev_row = curr_row
    return prev_row[-1]


def compute_cer(reference: str, hypothesis: str, normalize: bool = True) -> float:
    """Compute Character Error Rate (CER).

    Args:
        reference: Ground-truth reference text.
        hypothesis: Pipeline hypothesis text.
        normalize: If True, divides edit distance by max(1, len(reference)).

    Returns:
        float >= 0.0 (0.0 represents a perfect character match).
    """
    if reference == hypothesis:
        return 0.0
    if not reference:
        return 1.0 if hypothesis else 0.0

    dist = compute_levenshtein_distance(reference, hypothesis)
    return dist / len(reference) if normalize else float(dist)


def compute_wer(reference: str, hypothesis: str, normalize: bool = True) -> float:
    """Compute Word Error Rate (WER).

    Tokenizes text on whitespace and computes token-level Levenshtein distance.

    Args:
        reference: Ground-truth reference text.
        hypothesis: Pipeline hypothesis text.
        normalize: If True, divides edit distance by max(1, len(reference_words)).

    Returns:
        float >= 0.0 (0.0 represents a perfect word match).
    """
    ref_words = reference.split()
    hyp_words = hypothesis.split()
    if ref_words == hyp_words:
        return 0.0
    if not ref_words:
        return 1.0 if hyp_words else 0.0

    dist = compute_levenshtein_distance(ref_words, hyp_words)
    return dist / len(ref_words) if normalize else float(dist)


def _tokenize_words(text: str) -> list[str]:
    """Tokenize text into lowercase alphanumeric words and punctuation."""
    return re.findall(r"\w+|[^\w\s]", text.lower())


def _compute_fallback_bleu(reference: str, hypothesis: str, max_n: int = 4) -> float:
    """Compute pure-Python sentence-level BLEU with smoothing and brevity penalty."""
    ref_tokens = _tokenize_words(reference)
    hyp_tokens = _tokenize_words(hypothesis)

    if not hyp_tokens:
        return 0.0 if ref_tokens else 100.0
    if not ref_tokens:
        return 0.0

    hyp_len = len(hyp_tokens)
    ref_len = len(ref_tokens)

    # Brevity penalty
    if hyp_len > ref_len:
        bp = 1.0
    elif hyp_len == 0:
        bp = 0.0
    else:
        bp = math.exp(1.0 - (ref_len / hyp_len))

    # N-gram precisions
    log_precisions: list[float] = []
    for n in range(1, max_n + 1):
        if hyp_len < n:
            break
        hyp_ngrams = Counter(
            tuple(hyp_tokens[i : i + n]) for i in range(hyp_len - n + 1)
        )
        ref_ngrams = Counter(
            tuple(ref_tokens[i : i + n]) for i in range(ref_len - n + 1)
        )

        overlap = sum(
            min(count, ref_ngrams[ngram]) for ngram, count in hyp_ngrams.items()
        )
        total = sum(hyp_ngrams.values())

        # Add-1 smoothing for higher n-grams if overlap is 0
        p_n = 1.0 / (total + 1.0) if overlap == 0 else overlap / total
        log_precisions.append(math.log(p_n))

    if not log_precisions:
        return 0.0

    score = bp * math.exp(sum(log_precisions) / len(log_precisions)) * 100.0
    return max(0.0, min(100.0, score))


def compute_bleu(reference: str, hypothesis: str) -> float:
    """Compute BLEU score (0..100). Uses sacrebleu if installed, else fallback."""
    ref_clean = reference.strip()
    hyp_clean = hypothesis.strip()

    if not ref_clean and not hyp_clean:
        return 100.0
    if ref_clean == hyp_clean:
        return 100.0
    if not ref_clean or not hyp_clean:
        return 0.0

    if _HAS_SACREBLEU:
        try:
            return float(sacrebleu.sentence_bleu(hyp_clean, [ref_clean]).score)
        except Exception:
            pass

    return _compute_fallback_bleu(ref_clean, hyp_clean)


def _compute_fallback_chrf(
    reference: str, hypothesis: str, max_n: int = 6, beta: float = 2.0
) -> float:
    """Compute character n-gram F-score (chrF) without sacrebleu."""
    ref_chars = "".join(reference.split())
    hyp_chars = "".join(hypothesis.split())

    if not hyp_chars:
        return 0.0 if ref_chars else 100.0
    if not ref_chars:
        return 0.0

    precisions: list[float] = []
    recalls: list[float] = []

    for n in range(1, max_n + 1):
        if len(hyp_chars) < n or len(ref_chars) < n:
            continue
        hyp_ngrams = Counter(
            hyp_chars[i : i + n] for i in range(len(hyp_chars) - n + 1)
        )
        ref_ngrams = Counter(
            ref_chars[i : i + n] for i in range(len(ref_chars) - n + 1)
        )

        overlap = sum(
            min(count, ref_ngrams[ngram]) for ngram, count in hyp_ngrams.items()
        )
        total_hyp = sum(hyp_ngrams.values())
        total_ref = sum(ref_ngrams.values())

        p_n = overlap / total_hyp if total_hyp > 0 else 0.0
        r_n = overlap / total_ref if total_ref > 0 else 0.0
        precisions.append(p_n)
        recalls.append(r_n)

    if not precisions:
        return 0.0

    avg_p = sum(precisions) / len(precisions)
    avg_r = sum(recalls) / len(recalls)

    if avg_p + avg_r == 0:
        return 0.0

    beta_sq = beta**2
    f_score = (1.0 + beta_sq) * (avg_p * avg_r) / (beta_sq * avg_p + avg_r)
    return max(0.0, min(100.0, f_score * 100.0))


def compute_chrf(reference: str, hypothesis: str, beta: float = 2.0) -> float:
    """Compute chrF score (0..100). Uses sacrebleu if installed, else fallback."""
    ref_clean = reference.strip()
    hyp_clean = hypothesis.strip()

    if not ref_clean and not hyp_clean:
        return 100.0
    if ref_clean == hyp_clean:
        return 100.0
    if not ref_clean or not hyp_clean:
        return 0.0

    if _HAS_SACREBLEU:
        try:
            return float(sacrebleu.sentence_chrf(hyp_clean, [ref_clean]).score)
        except Exception:
            pass

    return _compute_fallback_chrf(ref_clean, hyp_clean, beta=beta)


def extract_markdown_headings(md_text: str) -> list[tuple[int, str]]:
    """Extract headings from markdown as a list of (level, normalized_title)."""
    headings: list[tuple[int, str]] = []
    for line in md_text.splitlines():
        line = line.strip()
        m = re.match(r"^(#{1,6})\s+(.+)$", line)
        if m:
            level = len(m.group(1))
            title = normalize_text(m.group(2))
            if title:
                headings.append((level, title))
    return headings


def compute_heading_hierarchy_f1(
    reference_md: str, hypothesis_md: str, similarity_threshold: float = 0.80
) -> float:
    """Compute structural F1 score for markdown heading hierarchy matching.

    Matches headings between reference and hypothesis requiring exact heading
    level match and high text similarity (>= similarity_threshold).
    """
    gt_headings = extract_markdown_headings(reference_md)
    hyp_headings = extract_markdown_headings(hypothesis_md)

    if not gt_headings and not hyp_headings:
        return 1.0
    if not gt_headings or not hyp_headings:
        return 0.0

    used_hyp: set[int] = set()
    true_positives = 0

    for gt_level, gt_title in gt_headings:
        best_i = -1
        best_sim = 0.0
        for i, (hyp_level, hyp_title) in enumerate(hyp_headings):
            if i in used_hyp:
                continue
            if gt_level != hyp_level:
                continue
            sim = text_similarity(gt_title, hyp_title)
            if sim >= similarity_threshold and sim > best_sim:
                best_sim = sim
                best_i = i

        if best_i >= 0:
            used_hyp.add(best_i)
            true_positives += 1

    precision = true_positives / len(hyp_headings) if hyp_headings else 0.0
    recall = true_positives / len(gt_headings) if gt_headings else 0.0

    if precision + recall == 0.0:
        return 0.0

    return (2.0 * precision * recall) / (precision + recall)


_MD_TABLE_SEPARATOR_RE = re.compile(
    r"^\s*\|?(\s*:?-+:?\s*\|)+\s*:?-+:?\s*\|?\s*$"
)


def extract_markdown_tables(md_text: str) -> list[list[list[str]]]:
    """Parse all markdown pipe tables from text into 2D cell grids."""
    tables: list[list[list[str]]] = []
    current_table: list[list[str]] = []

    for line in md_text.splitlines():
        trimmed = line.strip()
        if "|" in trimmed:
            if _MD_TABLE_SEPARATOR_RE.match(trimmed):
                continue
            raw_cells = trimmed.split("|")
            if raw_cells and not raw_cells[0].strip():
                raw_cells = raw_cells[1:]
            if raw_cells and not raw_cells[-1].strip():
                raw_cells = raw_cells[:-1]
            cells = [c.strip() for c in raw_cells]
            if cells:
                current_table.append(cells)
        else:
            if len(current_table) >= 2 and max(len(r) for r in current_table) >= 2:
                tables.append(current_table)
            current_table = []

    if len(current_table) >= 2 and max(len(r) for r in current_table) >= 2:
        tables.append(current_table)

    return tables


def _table_pair_similarity(
    t1: list[list[str]], t2: list[list[str]]
) -> float:
    """Compute structural and cell content similarity between two 2D table grids."""
    r1, c1 = len(t1), max(len(r) for r in t1)
    r2, c2 = len(t2), max(len(r) for r in t2)

    # Dimensional shape similarity
    shape_sim = (1.0 - abs(r1 - r2) / max(r1, r2)) * (
        1.0 - abs(c1 - c2) / max(c1, c2)
    )

    # Cell-by-cell content alignment
    matched_sim = 0.0
    min_r = min(r1, r2)
    for r in range(min_r):
        row1 = t1[r]
        row2 = t2[r]
        min_c = min(len(row1), len(row2))
        for c in range(min_c):
            matched_sim += text_similarity(row1[c], row2[c])

    total_cells = max(r1 * c1, r2 * c2)
    content_sim = matched_sim / total_cells if total_cells > 0 else 1.0

    return 0.3 * shape_sim + 0.7 * content_sim


def compute_table_similarity(reference_md: str, hypothesis_md: str) -> float:
    """Compute structural similarity across all markdown tables in two documents."""
    gt_tables = extract_markdown_tables(reference_md)
    hyp_tables = extract_markdown_tables(hypothesis_md)

    if not gt_tables and not hyp_tables:
        return 1.0
    if not gt_tables or not hyp_tables:
        return 0.0

    used_hyp: set[int] = set()
    scores: list[float] = []

    for gt_t in gt_tables:
        best_i = -1
        best_sim = 0.0
        for i, hyp_t in enumerate(hyp_tables):
            if i in used_hyp:
                continue
            sim = _table_pair_similarity(gt_t, hyp_t)
            if sim > best_sim:
                best_sim = sim
                best_i = i

        if best_i >= 0:
            used_hyp.add(best_i)
            scores.append(best_sim)
        else:
            scores.append(0.0)

    # Penalize extra hypothesis tables
    unmatched_hyp = len(hyp_tables) - len(used_hyp)
    total_eval = len(gt_tables) + unmatched_hyp
    return sum(scores) / max(1, total_eval)


@dataclass
class MarkdownScoreReport:
    """Evaluation summary for end-to-end PDF-to-Markdown export (OmniDocBench style)."""

    document: str
    cer: float
    wer: float
    bleu: float
    chrf: float
    heading_f1: float
    table_similarity: float

    def summary_line(self) -> str:
        return (
            f"{self.document:<20} "
            f"CER={self.cer:.3f} "
            f"WER={self.wer:.3f} "
            f"BLEU={self.bleu:.1f} "
            f"chrF={self.chrf:.1f} "
            f"HeadingF1={self.heading_f1:.2f} "
            f"TableSim={self.table_similarity:.2f}"
        )


def score_markdown_pair(
    document: str, reference_md: str, hypothesis_md: str
) -> MarkdownScoreReport:
    """Score hypothesis markdown against reference markdown across all R4 metrics."""
    return MarkdownScoreReport(
        document=document,
        cer=compute_cer(reference_md, hypothesis_md),
        wer=compute_wer(reference_md, hypothesis_md),
        bleu=compute_bleu(reference_md, hypothesis_md),
        chrf=compute_chrf(reference_md, hypothesis_md),
        heading_f1=compute_heading_hierarchy_f1(reference_md, hypothesis_md),
        table_similarity=compute_table_similarity(reference_md, hypothesis_md),
    )


def blocks_to_markdown(
    blocks: Sequence[tuple[BBox, str] | GTBlock],
) -> str:
    """Render a sequence of bounding-box text blocks to a markdown string in reading order."""
    normalized_items: list[tuple[float, float, str]] = []
    for b in blocks:
        if isinstance(b, GTBlock):
            y0, x0 = b.bbox[1], b.bbox[0]
            text = b.text.strip()
        else:
            bbox, text = b
            y0, x0 = bbox[1], bbox[0]
            text = text.strip()
        if text:
            normalized_items.append((y0, x0, text))

    # Sort primarily top-to-bottom, secondarily left-to-right
    normalized_items.sort(key=lambda item: (item[0], item[1]))

    return "\n\n".join(item[2] for item in normalized_items)

