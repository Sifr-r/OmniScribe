"""Tests for benchmark evaluation scoring functions (RFC 004 R4)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from omniscribe.confidence_eval import (
    GTBlock,
    MarkdownScoreReport,
    blocks_to_markdown,
    compute_bleu,
    compute_cer,
    compute_chrf,
    compute_heading_hierarchy_f1,
    compute_levenshtein_distance,
    compute_table_similarity,
    compute_wer,
    extract_markdown_headings,
    extract_markdown_tables,
    score_markdown_pair,
)
from scripts.confidence_eval import (
    compute_cer as scripts_compute_cer,
)
from scripts.confidence_eval import (
    compute_wer as scripts_compute_wer,
)
from scripts.confidence_eval import (
    score_markdown_pair as scripts_score_markdown_pair,
)

ROOT = Path(__file__).resolve().parents[2]


# --- 1. Levenshtein Distance & Alignment ------------------------------------


def test_levenshtein_distance_exact_and_variations() -> None:
    assert compute_levenshtein_distance("hello", "hello") == 0
    assert compute_levenshtein_distance("", "") == 0
    assert compute_levenshtein_distance("cat", "hat") == 1
    assert compute_levenshtein_distance("kitten", "sitting") == 3
    assert compute_levenshtein_distance("", "abc") == 3
    assert compute_levenshtein_distance("xyz", "") == 3

    # Token sequences
    words1 = ["the", "quick", "brown", "fox"]
    words2 = ["the", "fast", "brown", "fox"]
    assert compute_levenshtein_distance(words1, words2) == 1


# --- 2. Character Error Rate (CER) ------------------------------------------


def test_cer_calculation() -> None:
    ref = "The quick brown fox"
    # Identical text
    assert compute_cer(ref, ref) == 0.0

    # 1 substitution in 19 chars -> 1 / 19
    hyp = "The quick brown fax"
    assert pytest.approx(compute_cer(ref, hyp), abs=1e-4) == 1.0 / len(ref)

    # Empty cases
    assert compute_cer("", "") == 0.0
    assert compute_cer("", "non-empty") == 1.0
    assert compute_cer("non-empty", "") == 1.0

    # Unnormalized returns raw distance
    assert compute_cer(ref, hyp, normalize=False) == 1.0

    # Re-exported function from scripts matches
    assert scripts_compute_cer(ref, hyp) == compute_cer(ref, hyp)


# --- 3. Word Error Rate (WER) ----------------------------------------------


def test_wer_calculation() -> None:
    ref = "Deep learning vision model for document understanding"
    # Identical
    assert compute_wer(ref, ref) == 0.0

    # 1 word substitution out of 7 words
    hyp = "Deep learning vision architecture for document understanding"
    assert pytest.approx(compute_wer(ref, hyp), abs=1e-4) == 1.0 / 7.0

    # Insertion / deletion
    hyp_short = "Deep learning vision model"
    assert pytest.approx(compute_wer(ref, hyp_short), abs=1e-4) == 3.0 / 7.0

    # Empty cases
    assert compute_wer("", "") == 0.0
    assert compute_wer("", "word") == 1.0
    assert compute_wer("word", "") == 1.0

    # Re-exported function from scripts matches
    assert scripts_compute_wer(ref, hyp) == compute_wer(ref, hyp)


# --- 4. BLEU & chrF Metrics ------------------------------------------------


def test_bleu_and_chrf_perfect_and_degraded() -> None:
    ref = "OmniScribe performs high-accuracy optical character recognition."
    hyp_exact = "OmniScribe performs high-accuracy optical character recognition."
    hyp_partial = "OmniScribe performs optical character recognition."
    hyp_unrelated = "Completely unrelated text with zero lexical overlap."

    # Perfect match -> 100.0
    assert pytest.approx(compute_bleu(ref, hyp_exact), abs=1e-2) == 100.0
    assert pytest.approx(compute_chrf(ref, hyp_exact), abs=1e-2) == 100.0

    # Partial match
    bleu_partial = compute_bleu(ref, hyp_partial)
    chrf_partial = compute_chrf(ref, hyp_partial)
    assert 0.0 < bleu_partial < 100.0
    assert 0.0 < chrf_partial < 100.0

    # Unrelated match -> well below the partial-match band. The
    # fallback BLEU applies add-1 smoothing for zero-overlap n-grams
    # so a strict ``< 10`` would flake even with no lexical overlap;
    # 30 keeps the assertion meaningful (below the degraded-match
    # band of the partial case) without coupling to smoothing knobs.
    assert compute_bleu(ref, hyp_unrelated) < 30.0
    assert compute_chrf(ref, hyp_unrelated) < 30.0

    # Empty boundary cases
    assert compute_bleu("", "") == 100.0
    assert compute_bleu("text", "") == 0.0
    assert compute_chrf("", "") == 100.0
    assert compute_chrf("text", "") == 0.0


# --- 5. Heading Hierarchy Structural F1 -------------------------------------


def test_extract_markdown_headings() -> None:
    md = """
# Main Document Title

Some intro text here.

## Section 1: Background
Details on background.

### Subsection 1.1: History
Historical notes.

#### Minor Note
End note.
"""
    headings = extract_markdown_headings(md)
    assert len(headings) == 4
    assert headings[0] == (1, "main document title")
    assert headings[1] == (2, "section 1: background")
    assert headings[2] == (3, "subsection 1.1: history")
    assert headings[3] == (4, "minor note")


def test_heading_hierarchy_f1_matching() -> None:
    gt_md = """
# OmniScribe Overview
## Core Architecture
### VLM Engines
## Evaluation Protocol
"""
    # Identical headings -> F1 = 1.0
    assert compute_heading_hierarchy_f1(gt_md, gt_md) == 1.0

    # Both empty -> F1 = 1.0
    assert compute_heading_hierarchy_f1("Just text no headers", "More text") == 1.0

    # One empty -> F1 = 0.0
    assert compute_heading_hierarchy_f1(gt_md, "Just text") == 0.0

    # Shifted heading levels (e.g. H1 became H2) -> F1 = 0.0
    shifted_md = """
## OmniScribe Overview
### Core Architecture
#### VLM Engines
### Evaluation Protocol
"""
    assert compute_heading_hierarchy_f1(gt_md, shifted_md) == 0.0

    # Partial match: 2 out of 4 headings matched
    partial_md = """
# OmniScribe Overview
## Core Architecture
## Different Section
"""
    # GT count: 4, Hyp count: 3, Matched TP: 2
    # Prec = 2/3, Rec = 2/4 = 0.5, F1 = 2 * (2/3 * 0.5) / (2/3 + 0.5) = 4/7 approx 0.5714
    f1 = compute_heading_hierarchy_f1(gt_md, partial_md)
    assert pytest.approx(f1, abs=1e-3) == 4.0 / 7.0


# --- 6. Markdown Table Structural Similarity -------------------------------


def test_extract_markdown_tables() -> None:
    md = """
Intro paragraph.

| Model | Accuracy | Latency |
| :--- | :---: | ---: |
| Qwen3-VL | 94.2% | 120ms |
| OlmOCR | 92.8% | 85ms |

Follow-up paragraph.

| ID | Status |
|---|---|
| 1 | Passed |
| 2 | Failed |
"""
    tables = extract_markdown_tables(md)
    assert len(tables) == 2
    assert len(tables[0]) == 3  # 1 header + 2 data rows
    assert len(tables[0][0]) == 3  # 3 columns
    assert tables[0][1][0] == "Qwen3-VL"
    assert len(tables[1]) == 3
    assert len(tables[1][0]) == 2


def test_table_similarity_scoring() -> None:
    table_md = """
| Metric | Baseline | OmniScribe |
|---|---|---|
| CER | 0.082 | 0.024 |
| WER | 0.145 | 0.052 |
"""
    # Identical table -> 1.0
    assert compute_table_similarity(table_md, table_md) == 1.0

    # Neither has tables -> 1.0
    assert compute_table_similarity("No tables here", "Also no tables") == 1.0

    # One has table, other does not -> 0.0
    assert compute_table_similarity(table_md, "No tables here") == 0.0

    # Perturbed table (typo in one cell) -> score slightly below 1.0
    perturbed_table = """
| Metric | Baseline | OmniScribe |
|---|---|---|
| CER | 0.082 | 0.030 |
| WER | 0.145 | 0.052 |
"""
    score = compute_table_similarity(table_md, perturbed_table)
    assert 0.80 < score < 1.0

    # Dimension mismatch (missing row) -> penalized
    smaller_table = """
| Metric | Baseline | OmniScribe |
|---|---|---|
| CER | 0.082 | 0.024 |
"""
    score_small = compute_table_similarity(table_md, smaller_table)
    assert 0.40 < score_small < score


# --- 7. End-to-End Pair Scoring & Blocks Conversion ------------------------


def test_score_markdown_pair_and_summary_line() -> None:
    ref = """# Report
| Col A | Col B |
|---|---|
| 1 | 2 |
Summary text.
"""
    hyp = """# Report
| Col A | Col B |
|---|---|
| 1 | 2 |
Summary text.
"""
    report = score_markdown_pair("test_doc.pdf", ref, hyp)
    assert isinstance(report, MarkdownScoreReport)
    assert report.document == "test_doc.pdf"
    assert report.cer == 0.0
    assert report.wer == 0.0
    assert report.bleu == 100.0
    assert report.chrf == 100.0
    assert report.heading_f1 == 1.0
    assert report.table_similarity == 1.0

    summary = report.summary_line()
    assert "CER=0.000" in summary
    assert "HeadingF1=1.00" in summary
    assert "TableSim=1.00" in summary

    # Verify script re-export works
    s_rep = scripts_score_markdown_pair("test_doc.pdf", ref, hyp)
    assert s_rep.cer == report.cer


def test_blocks_to_markdown() -> None:
    blocks = [
        ((0.1, 0.5, 0.8, 0.6), "Second paragraph"),
        ((0.1, 0.1, 0.8, 0.2), "# Top Header"),
    ]
    md = blocks_to_markdown(blocks)
    # Top Header should appear first due to reading-order sort by y
    assert md.startswith("# Top Header")
    assert "Second paragraph" in md

    # Works with GTBlock too
    gt_blocks = [
        GTBlock(bbox=(0.1, 0.1, 0.8, 0.2), text="Title"),
        GTBlock(bbox=(0.1, 0.3, 0.8, 0.4), text="Body text"),
    ]
    gt_md = blocks_to_markdown(gt_blocks)
    assert "Title" in gt_md
    assert "Body text" in gt_md


# --- 8. CLI Smoke Test ------------------------------------------------------


def test_confidence_eval_cli_exposes_score_markdown_flag() -> None:
    script_path = ROOT / "scripts" / "confidence_eval.py"
    result = subprocess.run(
        [sys.executable, str(script_path), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "--score-markdown" in result.stdout
    assert "CER, WER, BLEU, chrF" in result.stdout
