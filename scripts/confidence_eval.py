#!/usr/bin/env python3
"""
Confidence evaluation: run each pipeline path against the example PDFs and
score the output against the ground-truth fixtures.

Usage:
    uv run scripts/confidence_eval.py                            # both paths
    uv run scripts/confidence_eval.py --path grounded            # just grounded
    uv run scripts/confidence_eval.py --path hybrid              # just hybrid
    uv run scripts/confidence_eval.py --model allenai/olmocr-2-7b

Assumes LM Studio / Ollama is serving the target model at --api-base.
Prints a per-document summary and flags the worst unmatched GT blocks.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

os.environ.setdefault("TQDM_DISABLE", "1")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rich.console import Console
from rich.table import Table

from omniscribe import (
    HybridAligner,
    OCRPipeline,
    OCRProcessor,
    PDFHandler,
    PromptedGroundedOCR,
)
from omniscribe.confidence_eval import (
    MarkdownScoreReport,
    blocks_to_markdown,
    compute_bleu,
    compute_cer,
    compute_chrf,
    compute_heading_hierarchy_f1,
    compute_levenshtein_distance,
    compute_report,
    compute_table_similarity,
    compute_wer,
    extract_markdown_headings,
    extract_markdown_tables,
    load_ground_truth,
    score_markdown_pair,
)

FIXTURES = ROOT / "tests" / "fixtures"
EXAMPLES = ROOT / "examples"

JOBS = [
    ("digital.pdf", "ground_truth_digital.json"),
    ("hybrid.pdf", "ground_truth_hybrid.json"),
    ("handwritten.pdf", "ground_truth_handwritten.json"),
    # Dense and notes fixtures were bootstrapped from the hybrid pipeline's
    # own output_*.pdf via scripts/build_fixture.py --from-pdf — too dense to
    # hand-build, and the grounded VLM hits "context size exceeded" on
    # them so the default --from-vlm mode couldn't be used either. Useful for
    # *regression* testing: if a future change degrades the hybrid output
    # against this baseline, recall drops will flag it. Less useful for
    # absolute hybrid-vs-grounded comparison since the bar is set by the
    # hybrid path itself.
    ("dense.pdf", "ground_truth_dense.json"),
    ("notes.pdf", "ground_truth_notes.json"),
]


# ---------------------------------------------------------------------------


async def run_grounded(pdf: Path, api_base: str, model: str, max_dim: int):
    backend = PromptedGroundedOCR(
        api_base=api_base,
        model=model,
        max_image_dim=max_dim,
        max_tokens=16384,
    )
    # Use ocr_document directly — no need to write an output PDF for scoring.
    response = await backend.ocr_document(str(pdf))
    return [(b.bbox, b.text) for b in response.blocks]


async def run_hybrid(pdf: Path, api_base: str, model: str, max_dim: int):
    # Run pipeline to a throwaway file; we only need pages_structured data.
    # Smuggle the alignment result out via a custom output_writer.
    captured: dict = {}

    def capture_writer(_in, _out, pages_data, _dpi):
        captured["pages_data"] = pages_data

    pipe = OCRPipeline(
        aligner=HybridAligner(),
        ocr_processor=OCRProcessor(api_base=api_base, model=model),
        pdf_handler=PDFHandler(),
        output_writer=capture_writer,
    )
    await pipe.run(
        str(pdf),
        str(pdf.with_suffix(".scoring.pdf")),
        max_image_dim=max_dim,
        concurrency=1,
        refine=True,
    )
    blocks = []
    for _page_num, page_blocks in captured.get("pages_data", {}).items():
        for bbox, text in page_blocks:
            if text.strip():
                blocks.append((bbox, text))
    return blocks


# ---------------------------------------------------------------------------


def render_report(console: Console, reports: list):
    table = Table(title="Confidence evaluation", show_lines=True)
    table.add_column("document")
    table.add_column("path")
    table.add_column("GT", justify="right")
    table.add_column("Pipe", justify="right")
    table.add_column("Matched", justify="right")
    table.add_column("Recall", justify="right")
    table.add_column("Avg IoU", justify="right")
    table.add_column("Avg TextSim", justify="right")

    for path, report in reports:
        table.add_row(
            report.document,
            path,
            str(report.gt_count),
            str(report.pipeline_count),
            str(len(report.matched)),
            f"{report.block_recall:.2f}",
            f"{report.avg_iou:.2f}",
            f"{report.avg_text_similarity:.2f}",
        )
    console.print(table)

    # Unmatched-blocks detail
    for path, report in reports:
        unmatched = [m for m in report.matches if m.iou < report.iou_threshold]
        if not unmatched:
            continue
        console.print(
            f"\n[yellow]Unmatched GT blocks in {report.document} ({path}):[/]"
        )
        for m in unmatched[:6]:
            snippet = m.gt_text[:80].replace("\n", " ")
            console.print(f"  - {snippet!r}  (best_iou={m.iou:.2f})")
        if len(unmatched) > 6:
            console.print(f"  ... and {len(unmatched) - 6} more.")


def render_markdown_report(
    console: Console, md_reports: list[tuple[str, MarkdownScoreReport]]
) -> None:
    table = Table(
        title="Markdown evaluation (OmniDocBench metrics)", show_lines=True
    )
    table.add_column("document")
    table.add_column("path")
    table.add_column("CER", justify="right")
    table.add_column("WER", justify="right")
    table.add_column("BLEU", justify="right")
    table.add_column("chrF", justify="right")
    table.add_column("Heading F1", justify="right")
    table.add_column("Table Sim", justify="right")

    for path, report in md_reports:
        table.add_row(
            report.document,
            path,
            f"{report.cer:.3f}",
            f"{report.wer:.3f}",
            f"{report.bleu:.1f}",
            f"{report.chrf:.1f}",
            f"{report.heading_f1:.2f}",
            f"{report.table_similarity:.2f}",
        )
    console.print(table)


# ---------------------------------------------------------------------------


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--path", choices=["both", "grounded", "hybrid"], default="both"
    )
    parser.add_argument("--api-base", default="http://localhost:1234/v1")
    parser.add_argument("--grounded-model", default="qwen/qwen3-vl-8b")
    parser.add_argument("--hybrid-model", default="allenai/olmocr-2-7b")
    parser.add_argument("--max-image-dim", type=int, default=1024)
    parser.add_argument("--iou-threshold", type=float, default=0.3)
    parser.add_argument(
        "--score-markdown",
        action="store_true",
        help="Evaluate markdown export against ground-truth markdown fixtures (CER, WER, BLEU, chrF, heading F1, table similarity)",
    )
    args = parser.parse_args()

    console = Console()
    reports = []
    md_reports: list[tuple[str, MarkdownScoreReport]] = []

    for pdf_name, fixture_name in JOBS:
        pdf = EXAMPLES / pdf_name
        fixture = FIXTURES / fixture_name
        gt, (fw, fh) = load_ground_truth(fixture)
        console.print(f"\n[bold]>> {pdf_name}[/]  (GT: {len(gt)} blocks, {fw}x{fh})")

        # Load or synthesize ground-truth markdown
        gt_md = ""
        if args.score_markdown:
            md_fixture = FIXTURES / fixture_name.replace(".json", ".md")
            if md_fixture.exists():
                gt_md = md_fixture.read_text(encoding="utf-8")
            else:
                gt_md = blocks_to_markdown(gt)

        if args.path in ("both", "grounded"):
            console.print("   [cyan]grounded[/]...")
            try:
                out = await run_grounded(
                    pdf, args.api_base, args.grounded_model, args.max_image_dim
                )
                report = compute_report(
                    pdf_name, gt, out, iou_threshold=args.iou_threshold
                )
                reports.append(("grounded", report))
                console.print(f"   {report.summary_line()}")

                if args.score_markdown:
                    pipe_md = blocks_to_markdown(out)
                    md_rep = score_markdown_pair(pdf_name, gt_md, pipe_md)
                    md_reports.append(("grounded", md_rep))
                    console.print(f"   [green]md:[/] {md_rep.summary_line()}")
            except Exception as e:
                console.print(f"   [red]grounded failed: {type(e).__name__}: {e}[/]")

        if args.path in ("both", "hybrid"):
            console.print("   [cyan]hybrid[/]...")
            try:
                out = await run_hybrid(
                    pdf, args.api_base, args.hybrid_model, args.max_image_dim
                )
                report = compute_report(
                    pdf_name, gt, out, iou_threshold=args.iou_threshold
                )
                reports.append(("hybrid", report))
                console.print(f"   {report.summary_line()}")

                if args.score_markdown:
                    pipe_md = blocks_to_markdown(out)
                    md_rep = score_markdown_pair(pdf_name, gt_md, pipe_md)
                    md_reports.append(("hybrid", md_rep))
                    console.print(f"   [green]md:[/] {md_rep.summary_line()}")
            except Exception as e:
                console.print(f"   [red]hybrid failed: {type(e).__name__}: {e}[/]")

    console.print()
    render_report(console, reports)
    if args.score_markdown and md_reports:
        console.print()
        render_markdown_report(console, md_reports)


__all__ = [
    "MarkdownScoreReport",
    "blocks_to_markdown",
    "compute_bleu",
    "compute_cer",
    "compute_chrf",
    "compute_heading_hierarchy_f1",
    "compute_levenshtein_distance",
    "compute_table_similarity",
    "compute_wer",
    "extract_markdown_headings",
    "extract_markdown_tables",
    "main",
    "render_markdown_report",
    "render_report",
    "run_grounded",
    "run_hybrid",
    "score_markdown_pair",
]


if __name__ == "__main__":
    asyncio.run(main())
