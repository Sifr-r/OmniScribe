#!/usr/bin/env python3
"""
Confidence evaluation: run each pipeline path against the example PDFs and
score the *canonical export* against the ground-truth fixtures.

What is scored
--------------
Each ``(path, fixture)`` combination is run through the product pipeline
(``plugins.ocr.pipeline_bridge.build_pipeline`` -> ``run_pipeline``), which
means the document processors, the quality-repair loop and the PDF writer all
run exactly as they do for a user upload. The scored artifact is the Markdown
the export surface produces from the resulting rich ``DocumentResult``:
``plugins.documents.service.build_document_export(export_format="markdown")``
-> ``core.writers.markdown.render_markdown``. It is *not*
``blocks_to_markdown`` -- that helper only sorts and joins bbox/text pairs, so
scoring it measured the harness's own ordering instead of the product's
output.

Markdown scoring reports CER, WER, BLEU, chrF, heading F1 and table similarity
for that export, plus IoU-based block recall over the same document result.

Exit status
-----------
An evaluation is complete when every requested ``(path, fixture)``
combination produced a scored export. Anything less -- a crashed path, an
unreachable endpoint, a pipeline that recorded no rich document -- exits
non-zero. ``--allow-partial`` downgrades that to exit 0 while keeping every
gap in the JSON report; it must be asked for explicitly.

Usage:
    uv run scripts/confidence_eval.py                            # both paths
    uv run scripts/confidence_eval.py --path grounded            # just grounded
    uv run scripts/confidence_eval.py --path hybrid              # just hybrid
    uv run scripts/confidence_eval.py --model allenai/olmocr-2-7b
    uv run scripts/confidence_eval.py --allow-partial            # exit 0 on gaps

Assumes LM Studio / Ollama is serving the target model at --api-base.
Writes a machine-readable report (records, failures, provenance) and the raw
per-run outputs (scored Markdown, rich document JSON, output PDF) under
--out-dir; the path is printed and recorded in the report.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
import platform
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

os.environ.setdefault("TQDM_DISABLE", "1")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rich.console import Console
from rich.table import Table

from omniscribe.confidence_eval import (
    EXIT_INCOMPLETE,
    EXIT_OK,
    HARNESS_VERSION,
    REPORT_SCHEMA,
    EvaluationFailure,
    EvaluationReport,
    GTBlock,
    MarkdownScoreReport,
    PathResult,
    blocks_from_document_result,
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
from omniscribe.config import RuntimeSettings
from omniscribe.core.document import DocumentResult
from omniscribe.plugins.documents.service import build_document_export
from omniscribe.plugins.ocr.pipeline_bridge import build_pipeline, run_pipeline
from omniscribe.plugins.ocr.schemas import OCRRequest

FIXTURES = ROOT / "tests" / "fixtures"
EXAMPLES = ROOT / "examples"

#: Default parent directory for raw run outputs. ``/reports/`` is
#: git-ignored scratch space, so a run never pollutes the tracked tree.
DEFAULT_OUT_ROOT = ROOT / "reports" / "confidence_eval"

JOBS: list[tuple[str, str]] = [
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

#: Every pipeline the harness can evaluate, in report order.
PATHS: tuple[str, ...] = ("grounded", "hybrid")

#: Every registered document processor. Requesting all of them is what makes
#: a canonical export structurally meaningful (reading order, structure,
#: sections, tables); the exact list used is recorded in the provenance so a
#: number is never read without knowing which processors produced it.
ALL_PROCESSORS: tuple[str, ...] = (
    "reading_order",
    "quality_analysis",
    "structure_analysis",
    "section_analysis",
    "layout_enrichment",
    "table_extraction",
    "table_fallback",
)

#: The export surface whose output is scored. Stamped into every report so a
#: score is never read as "the pipeline's markdown" without knowing which
#: function produced the text.
CANONICAL_EXPORT_SOURCE = (
    "omniscribe.plugins.documents.service.build_document_export"
    "(export_format='markdown', document=DocumentResult) "
    "-> omniscribe.core.writers.markdown.render_markdown"
)

#: Competitor rows carried over from the RFC 004 gap analysis. They are NOT
#: same-protocol measurements: nothing in this repository has run those
#: systems over the corpus above with this harness, so they are emitted
#: explicitly flagged and must never be quoted as measured results.
ILLUSTRATIVE_COMPETITOR_ROWS: tuple[dict[str, Any], ...] = (
    {
        "system": "Marker",
        "cer": 0.042,
        "wer": 0.078,
        "heading_f1": 0.89,
        "table_similarity": 0.84,
    },
    {
        "system": "Docling",
        "cer": 0.038,
        "wer": 0.069,
        "heading_f1": 0.91,
        "table_similarity": 0.88,
    },
    {
        "system": "MinerU",
        "cer": 0.039,
        "wer": 0.071,
        "heading_f1": 0.90,
        "table_similarity": 0.87,
    },
    {
        "system": "Unstructured.io (OSS local)",
        "cer": 0.065,
        "wer": 0.118,
        "heading_f1": 0.82,
        "table_similarity": 0.78,
    },
)

#: Machine-readable status carried by every competitor row.
ILLUSTRATIVE_ROW_STATUS: dict[str, Any] = {
    "measured": False,
    "status": "illustrative",
    "protocol": "not run with this harness on this corpus",
    "source": "docs/rfcs/2026-09-competitive-gap-remediation.md",
    "caveat": (
        "Illustrative positioning only. Each system must be re-measured with "
        "this protocol on this corpus before these rows are treated as "
        "published results."
    ),
}

#: Full-dataset downloads are license-gated stubs, not a live capability.
DATASET_DOWNLOAD_STATUS: dict[str, Any] = {
    "status": "unavailable",
    "reason": (
        "scripts/fetch_datasets.py is a license-gated stub; no external "
        "benchmark corpus has been downloaded or measured in this run."
    ),
    "datasets": ["OmniDocBench", "OCR-Quality", "KIE-HVQA"],
}


class HarnessError(RuntimeError):
    """A run could not produce a scorable canonical export.

    Raised instead of returning a degraded artifact: a path that cannot
    produce the export the product serves is a failed evaluation, not a
    partial one.
    """


# ---------------------------------------------------------------------------
# canonical export
# ---------------------------------------------------------------------------


def canonical_markdown_export(
    document: DocumentResult, *, metadata: Mapping[str, Any] | None = None
) -> str:
    """Return the Markdown the export route would serve for ``document``.

    Goes through the same builder the export route uses
    (:func:`omniscribe.plugins.documents.service.build_document_export`) with
    the rich document attached, so the scored text carries what the user
    downloads: GFM tables, heading levels, list nesting, page-break markers.
    A bbox-ordered text join is a different artifact and is not scored.
    """
    page_text = {
        page: [text for _bbox, text in blocks]
        for page, blocks in document.to_pages_data().items()
    }
    payload = build_document_export(
        page_text=page_text,
        metadata=dict(metadata) if metadata else None,
        export_format="markdown",
        document=document,
    )
    if not isinstance(payload, str):
        raise HarnessError(
            f"markdown export returned {type(payload).__name__}, expected str"
        )
    return payload


def build_request(
    *,
    path: str,
    model: str,
    api_base: str,
    max_image_dim: int,
    processors: Sequence[str],
    dpi: int,
    concurrency: int,
) -> OCRRequest:
    """Build the product request that drives one pipeline path."""
    return OCRRequest.model_validate(
        {
            "pipeline_mode": path,
            "model": model,
            "api_base": api_base,
            "max_image_dim": max_image_dim,
            "document_processors": list(processors),
            "dpi": dpi,
            "concurrency": concurrency,
        }
    )


def model_for(args: argparse.Namespace, path: str) -> str:
    """Return the model configured for one pipeline path."""
    model = args.hybrid_model if path == "hybrid" else args.grounded_model
    if not isinstance(model, str) or not model.strip():
        raise HarnessError(f"{path} model must be a non-empty string")
    return model


async def run_path_document(
    *,
    pdf: Path,
    output_pdf: Path,
    request: OCRRequest,
    settings: RuntimeSettings,
) -> tuple[DocumentResult, float]:
    """Run one fixture through the product pipeline; return its rich result.

    Returns ``(document, latency_seconds)``. Raises
    :class:`HarnessError` when the run cannot be scored honestly:

    * the pipeline recorded no ``DocumentResult`` -- there is no canonical
      export at all;
    * the pipeline dropped pages (``last_failed_pages``). An engine that
      logs a warning and emits an empty page produces an export that *looks*
      complete to a scorer, so a run with lost pages would otherwise be
      reported as a very low score instead of a failed evaluation;
    * the document carries no recognized text.
    """
    pipeline = build_pipeline(settings, request)
    started = time.perf_counter()
    await run_pipeline(
        pipeline,
        settings=settings,
        request=request,
        input_path=str(pdf),
        output_path=str(output_pdf),
    )
    latency = time.perf_counter() - started
    document = pipeline.last_document_result
    if document is None:
        raise HarnessError(
            "pipeline recorded no DocumentResult, so no canonical export exists"
        )
    failed_pages = [int(page) for page in (pipeline.last_failed_pages or [])]
    if failed_pages:
        raise HarnessError(
            f"pipeline dropped {len(failed_pages)} page(s) at index "
            f"{failed_pages}; the canonical export is partial, so it is not "
            "scored (the output PDF is kept for inspection)"
        )
    if not blocks_from_document_result(document):
        raise HarnessError(
            "pipeline produced a document with no recognized text; nothing to score"
        )
    return document, latency


# ---------------------------------------------------------------------------
# one (path, fixture) combination
# ---------------------------------------------------------------------------


def load_ground_truth_markdown(fixture: Path, gt: list[GTBlock]) -> tuple[str, str]:
    """Return ``(markdown, source)`` for the ground truth of one fixture.

    ``source`` is ``"fixture"`` when a hand-checked ``.md`` fixture exists and
    ``"ground_truth_blocks"`` when the Markdown had to be synthesized from
    the flat layout fixture. A synthesized reference has no structure of its
    own, so heading/table scores against it are not comparable with a
    hand-checked one -- the source is recorded on every result for that
    reason.
    """
    md_fixture = fixture.with_suffix(".md")
    if md_fixture.exists():
        return md_fixture.read_text(encoding="utf-8"), "fixture"
    return blocks_to_markdown(gt), "ground_truth_blocks"


async def evaluate_combination(
    *,
    path: str,
    pdf_name: str,
    fixture_name: str,
    args: argparse.Namespace,
    settings: RuntimeSettings,
    out_dir: Path,
) -> PathResult:
    """Run, export, and score one ``(path, fixture)`` combination.

    Never raises for a run failure: an exception becomes a failure record so
    the caller can keep evaluating the remaining combinations and the report
    can account for this one.
    """
    pdf = EXAMPLES / pdf_name
    fixture = FIXTURES / fixture_name
    run_dir = out_dir / path / Path(pdf_name).stem
    request = build_request(
        path=path,
        model=model_for(args, path),
        api_base=args.api_base,
        max_image_dim=args.max_image_dim,
        processors=args.processors,
        dpi=args.dpi,
        concurrency=args.concurrency,
    )

    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        document, latency = await run_path_document(
            pdf=pdf,
            output_pdf=run_dir / "output.pdf",
            request=request,
            settings=settings,
        )
    except Exception as exc:  # a failed path is a record, not a crash
        return PathResult.from_failure(
            EvaluationFailure.from_exception(
                path=path, fixture=fixture_name, stage="pipeline", exc=exc
            )
        )

    try:
        export_markdown = canonical_markdown_export(
            document,
            metadata={
                "document": pdf_name,
                "pipeline_path": path,
                "model": request.model or "",
                "harness_version": HARNESS_VERSION,
            },
        )
        export_path = run_dir / "export.md"
        export_path.write_text(export_markdown, encoding="utf-8")
        document_path = run_dir / "document.json"
        document_path.write_text(
            json.dumps(document.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:  # a broken export is a failed evaluation
        return PathResult.from_failure(
            EvaluationFailure.from_exception(
                path=path, fixture=fixture_name, stage="export", exc=exc
            )
        )

    try:
        gt, _page_size = load_ground_truth(fixture)
        gt_markdown, gt_markdown_source = (
            load_ground_truth_markdown(fixture, gt)
            if args.score_markdown
            else ("", "skipped")
        )
        confidence = compute_report(
            pdf_name,
            gt,
            blocks_from_document_result(document),
            iou_threshold=args.iou_threshold,
        )
        markdown = (
            score_markdown_pair(pdf_name, gt_markdown, export_markdown)
            if args.score_markdown
            else None
        )
    except Exception as exc:
        return PathResult.from_failure(
            EvaluationFailure.from_exception(
                path=path, fixture=fixture_name, stage="scoring", exc=exc
            )
        )

    return PathResult(
        path=path,
        fixture=fixture_name,
        status="scored",
        export_source=CANONICAL_EXPORT_SOURCE,
        export_path=str(export_path),
        document_artifact_path=str(document_path),
        latency_seconds=latency,
        confidence=confidence,
        markdown=markdown,
        ground_truth_markdown_source=gt_markdown_source,
    )


# ---------------------------------------------------------------------------
# provenance
# ---------------------------------------------------------------------------


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _file_ref(path: Path) -> dict[str, Any]:
    """Digest a file so the report identifies the exact bytes it scored."""
    if not path.exists():
        return {"path": str(path), "exists": False}
    payload = path.read_bytes()
    return {
        "path": str(path),
        "exists": True,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def hardware_fingerprint() -> dict[str, Any]:
    """Record the machine the numbers were produced on."""
    info: dict[str, Any] = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
    }
    if importlib.util.find_spec("torch") is not None:
        try:
            import torch

            info["torch"] = torch.__version__
            info["cuda_available"] = bool(torch.cuda.is_available())
            if torch.cuda.is_available():
                info["cuda_devices"] = [
                    torch.cuda.get_device_name(index)
                    for index in range(torch.cuda.device_count())
                ]
        except Exception as exc:  # provenance must never break a run
            info["torch"] = f"unavailable: {type(exc).__name__}"
    return info


def prompt_provenance() -> dict[str, Any]:
    """Record the prompt revisions and digests the runs used."""
    from omniscribe.core.grounded.prompted import (
        DEFAULT_GROUNDING_PROMPT,
        GROUNDED_PROMPT_VERSION,
    )
    from omniscribe.core.ocr.prompts import PROMPT_VERSION as OCR_PROMPT_VERSION

    return {
        "grounded": {
            "version": GROUNDED_PROMPT_VERSION,
            "module": "omniscribe.core.grounded.prompted",
            "sha256": _sha256_text(DEFAULT_GROUNDING_PROMPT),
        },
        "hybrid_ocr": {
            "version": OCR_PROMPT_VERSION,
            "module": "omniscribe.core.ocr.prompts",
        },
    }


def corpus_provenance(jobs: Sequence[tuple[str, str]]) -> dict[str, Any]:
    """Identify the exact ground truth the scores were computed against."""
    fixtures: list[dict[str, Any]] = []
    for pdf_name, fixture_name in jobs:
        pdf = EXAMPLES / pdf_name
        fixture = FIXTURES / fixture_name
        entry: dict[str, Any] = {
            "document": pdf_name,
            "pdf": _file_ref(pdf),
            "ground_truth": _file_ref(fixture),
            "ground_truth_markdown": _file_ref(fixture.with_suffix(".md")),
        }
        if fixture.exists():
            gt, page_size = load_ground_truth(fixture)
            entry["gt_blocks"] = len(gt)
            entry["gt_page_size"] = list(page_size)
        fixtures.append(entry)

    corpus_id = _sha256_text(
        "|".join(
            str(entry["ground_truth"].get("sha256", "missing")) for entry in fixtures
        )
    )[:16]
    return {
        "corpus_id": corpus_id,
        "fixtures": fixtures,
        "note": (
            "dense.pdf / notes.pdf ground truth was bootstrapped from this "
            "pipeline's own output, so their absolute scores are not an "
            "independent accuracy measurement."
        ),
    }


def build_provenance(
    *,
    args: argparse.Namespace,
    paths: Sequence[str],
    jobs: Sequence[tuple[str, str]],
    out_dir: Path,
    started_at: datetime,
    finished_at: datetime,
    report_path: Path,
) -> dict[str, Any]:
    """Assemble the reproducibility record written next to every report."""
    return {
        "harness": {
            "script": "scripts/confidence_eval.py",
            "version": HARNESS_VERSION,
            "report_schema": REPORT_SCHEMA,
            "scored_artifact": CANONICAL_EXPORT_SOURCE,
        },
        "run": {
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "wall_seconds": (finished_at - started_at).total_seconds(),
        },
        "endpoint": {
            "api_base": args.api_base,
            "models": {path: model_for(args, path) for path in paths},
        },
        "settings": {
            "paths": list(paths),
            "document_processors": list(args.processors),
            "max_image_dim": args.max_image_dim,
            "dpi": args.dpi,
            "concurrency": args.concurrency,
            "iou_threshold": args.iou_threshold,
            "score_markdown": args.score_markdown,
            "allow_partial": args.allow_partial,
        },
        "prompts": prompt_provenance(),
        "hardware": hardware_fingerprint(),
        "corpus": corpus_provenance(jobs),
        "raw_outputs": {
            "directory": str(out_dir),
            "per_run": [
                "export.md (the scored canonical Markdown)",
                "document.json (the rich DocumentResult the export was built from)",
                "output.pdf (the pipeline's searchable-PDF output)",
            ],
            "report": str(report_path),
        },
        "datasets": DATASET_DOWNLOAD_STATUS,
        "competitor_comparison": {
            "measured": False,
            "rows": [
                {**row, **ILLUSTRATIVE_ROW_STATUS}
                for row in ILLUSTRATIVE_COMPETITOR_ROWS
            ],
        },
    }


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def render_report(console: Console, records: Sequence[PathResult]) -> None:
    """Print the geometric table plus the unmatched-block detail."""
    table = Table(title="Canonical-export confidence evaluation", show_lines=True)
    table.add_column("document")
    table.add_column("path")
    table.add_column("GT", justify="right")
    table.add_column("Pipe", justify="right")
    table.add_column("Matched", justify="right")
    table.add_column("Recall", justify="right")
    table.add_column("Avg IoU", justify="right")
    table.add_column("Avg TextSim", justify="right")
    table.add_column("Latency s", justify="right")

    scored = [record for record in records if record.confidence is not None]
    for record in scored:
        report = record.confidence
        assert report is not None  # narrowed by the comprehension filter
        latency = (
            f"{record.latency_seconds:.1f}"
            if record.latency_seconds is not None
            else "-"
        )
        table.add_row(
            report.document,
            record.path,
            str(report.gt_count),
            str(report.pipeline_count),
            str(len(report.matched)),
            f"{report.block_recall:.2f}",
            f"{report.avg_iou:.2f}",
            f"{report.avg_text_similarity:.2f}",
            latency,
        )
    if scored:
        console.print(table)

    for record in scored:
        report = record.confidence
        assert report is not None
        unmatched = [m for m in report.matches if m.iou < report.iou_threshold]
        if not unmatched:
            continue
        console.print(
            f"\n[yellow]Unmatched GT blocks in {report.document} ({record.path}):[/]"
        )
        for match in unmatched[:6]:
            snippet = match.gt_text[:80].replace("\n", " ")
            console.print(f"  - {snippet!r}  (best_iou={match.iou:.2f})")
        if len(unmatched) > 6:
            console.print(f"  ... and {len(unmatched) - 6} more.")


def render_markdown_report(console: Console, records: Sequence[PathResult]) -> None:
    """Print the export-fidelity table (scored exports only)."""
    scored = [record for record in records if record.markdown is not None]
    if not scored:
        return
    table = Table(
        title="Canonical Markdown export (OmniDocBench metrics)", show_lines=True
    )
    table.add_column("document")
    table.add_column("path")
    table.add_column("GT md", justify="left")
    table.add_column("CER", justify="right")
    table.add_column("WER", justify="right")
    table.add_column("BLEU", justify="right")
    table.add_column("chrF", justify="right")
    table.add_column("Heading F1", justify="right")
    table.add_column("Table Sim", justify="right")

    for record in scored:
        report: MarkdownScoreReport | None = record.markdown
        assert report is not None
        table.add_row(
            report.document,
            record.path,
            record.ground_truth_markdown_source,
            f"{report.cer:.3f}",
            f"{report.wer:.3f}",
            f"{report.bleu:.1f}",
            f"{report.chrf:.1f}",
            f"{report.heading_f1:.2f}",
            f"{report.table_similarity:.2f}",
        )
    console.print(table)


def render_failures(console: Console, report: EvaluationReport) -> None:
    """Print every failure and every requested-but-missing combination."""
    if not report.failures and not report.missing_combinations():
        return
    table = Table(title="Failures (incomplete requested evaluation)", show_lines=True)
    table.add_column("path")
    table.add_column("fixture")
    table.add_column("stage")
    table.add_column("error")
    table.add_column("message")
    for failure in report.failures:
        table.add_row(
            failure.path,
            failure.fixture,
            failure.stage,
            failure.error_type,
            failure.message[:80],
        )
    for missing in report.missing_combinations():
        table.add_row(
            missing["path"], missing["fixture"], "missing", "-", "no record produced"
        )
    console.print(table)


def render_competitor_caveat(console: Console) -> None:
    """Print the comparison rows, explicitly labelled illustrative."""
    console.print(
        "\n[yellow]Competitor rows below are illustrative, not measured.[/] "
        f"{ILLUSTRATIVE_ROW_STATUS['caveat']}"
    )
    table = Table(title="Competitor comparison (ILLUSTRATIVE — unmeasured)")
    table.add_column("system")
    table.add_column("CER", justify="right")
    table.add_column("WER", justify="right")
    table.add_column("Heading F1", justify="right")
    table.add_column("Table Sim", justify="right")
    table.add_column("status")
    for row in ILLUSTRATIVE_COMPETITOR_ROWS:
        table.add_row(
            str(row["system"]),
            f"{float(row['cer']):.3f}",
            f"{float(row['wer']):.3f}",
            f"{float(row['heading_f1']):.2f}",
            f"{float(row['table_similarity']):.2f}",
            "illustrative",
        )
    console.print(table)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def resolve_paths(selection: str) -> tuple[str, ...]:
    """Expand ``--path`` into the concrete list of pipelines to evaluate."""
    if selection == "both":
        return PATHS
    return (selection,)


def resolve_out_dir(out_dir: str | None) -> Path:
    """Pick the raw-output directory, defaulting to a timestamped run dir."""
    if out_dir:
        return Path(out_dir)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return DEFAULT_OUT_ROOT / stamp


def select_jobs(fixtures: str | None) -> list[tuple[str, str]]:
    """Filter ``JOBS`` by ``--fixtures`` (document names, comma separated)."""
    if not fixtures:
        return list(JOBS)
    wanted = {name.strip() for name in fixtures.split(",") if name.strip()}
    unknown = wanted - {pdf for pdf, _ in JOBS}
    if unknown:
        raise SystemExit(
            f"unknown fixture(s): {', '.join(sorted(unknown))}; "
            f"available: {', '.join(pdf for pdf, _ in JOBS)}"
        )
    return [job for job in JOBS if job[0] in wanted]


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI. Markdown scoring is on by default — it is the
    product's advertised output, so it is the default artifact under test."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--path", choices=["both", "grounded", "hybrid"], default="both"
    )
    parser.add_argument("--api-base", default="http://localhost:1234/v1")
    parser.add_argument("--grounded-model", default="qwen/qwen3-vl-8b")
    parser.add_argument("--hybrid-model", default="allenai/olmocr-2-7b")
    parser.add_argument("--max-image-dim", type=int, default=1024)
    parser.add_argument("--iou-threshold", type=float, default=0.3)
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument(
        "--processors",
        default=",".join(ALL_PROCESSORS),
        help=(
            "Comma-separated document processors to run. Default: every "
            "registered processor, so the canonical export is structurally "
            "complete."
        ),
    )
    parser.add_argument(
        "--fixtures",
        default=None,
        help="Comma-separated subset of example documents to evaluate (default: all)",
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="Directory for raw per-run outputs (default: reports/confidence_eval/<utc timestamp>)",
    )
    parser.add_argument(
        "--json-out",
        default=None,
        help="Report path (default: <out-dir>/report.json)",
    )
    parser.add_argument(
        "--score-markdown",
        dest="score_markdown",
        action="store_true",
        default=True,
        help=(
            "Score the canonical Markdown export against ground truth "
            "(default; kept for compatibility). Metrics: CER, WER, BLEU, chrF, "
            "heading F1, table similarity"
        ),
    )
    parser.add_argument(
        "--no-markdown",
        dest="score_markdown",
        action="store_false",
        help="Skip the canonical-export Markdown metrics",
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help=(
            "Exit 0 even when a requested path/fixture failed. The gaps stay "
            "in the JSON report; the run is NOT complete."
        ),
    )
    return parser


async def evaluate_all(
    *,
    args: argparse.Namespace,
    paths: Sequence[str],
    jobs: Sequence[tuple[str, str]],
    out_dir: Path,
) -> list[PathResult]:
    """Evaluate every requested ``(path, fixture)`` combination, in order.

    One event loop for the whole run: the engines share process-wide state
    (the hybrid aligner singleton, the LLM circuit-breaker registry) that is
    not meant to be re-bound per document. A combination that fails becomes a
    failure record; the loop continues so the report can account for it.
    """
    settings = RuntimeSettings()
    records: list[PathResult] = []
    console = Console()
    for pdf_name, fixture_name in jobs:
        for path in paths:
            console.print(f"\n[bold]>> {pdf_name} · {path}[/]")
            record = await evaluate_combination(
                path=path,
                pdf_name=pdf_name,
                fixture_name=fixture_name,
                args=args,
                settings=settings,
                out_dir=out_dir,
            )
            records.append(record)
            _print_record(console, record, args)
    return records


def _print_record(
    console: Console, record: PathResult, args: argparse.Namespace
) -> None:
    """Print the one-line outcome of a single combination."""
    if record.failure is not None:
        console.print(
            f"   [red]{record.failure.stage} failed: "
            f"{record.failure.error_type}: {record.failure.message}[/]"
        )
        return
    if record.confidence is not None:
        console.print(f"   {record.confidence.summary_line()}")
    if record.markdown is not None and args.score_markdown:
        console.print(f"   [green]export md:[/] {record.markdown.summary_line()}")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the evaluation; return the process exit status."""
    args = build_parser().parse_args(argv)
    args.processors = [
        name.strip() for name in str(args.processors).split(",") if name.strip()
    ]

    console = Console()
    paths = resolve_paths(args.path)
    jobs = select_jobs(args.fixtures)
    out_dir = resolve_out_dir(args.out_dir)
    report_path = Path(args.json_out) if args.json_out else out_dir / "report.json"

    started_at = datetime.now(UTC)
    records = asyncio.run(
        evaluate_all(args=args, paths=paths, jobs=jobs, out_dir=out_dir)
    )

    report = EvaluationReport(
        requested_paths=list(paths),
        requested_fixtures=[fixture for _pdf, fixture in jobs],
        records=records,
        provenance=build_provenance(
            args=args,
            paths=paths,
            jobs=jobs,
            out_dir=out_dir,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            report_path=report_path,
        ),
        allow_partial=args.allow_partial,
    )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    console.print()
    render_report(console, records)
    console.print()
    render_markdown_report(console, records)
    render_failures(console, report)
    render_competitor_caveat(console)
    console.print(f"\n[bold]report:[/] {report_path}")
    console.print(f"[bold]raw outputs:[/] {out_dir}")
    console.print(
        f"[bold]status:[/] {report.status} "
        f"({len(records)}/{len(paths) * len(jobs)} requested runs scored, "
        f"{len(report.failures)} failed, {len(report.missing_combinations())} missing)"
    )

    if report.exit_code() == EXIT_INCOMPLETE:
        console.print(
            "[red]Incomplete requested evaluation — failing.[/] "
            "Re-run with --allow-partial only if a partial run is intended."
        )
    return report.exit_code()


__all__ = [
    "ALL_PROCESSORS",
    "CANONICAL_EXPORT_SOURCE",
    "DATASET_DOWNLOAD_STATUS",
    "EXIT_INCOMPLETE",
    "EXIT_OK",
    "ILLUSTRATIVE_COMPETITOR_ROWS",
    "ILLUSTRATIVE_ROW_STATUS",
    "MarkdownScoreReport",
    "blocks_to_markdown",
    "build_parser",
    "build_request",
    "canonical_markdown_export",
    "compute_bleu",
    "compute_cer",
    "compute_chrf",
    "compute_heading_hierarchy_f1",
    "compute_levenshtein_distance",
    "compute_table_similarity",
    "compute_wer",
    "evaluate_all",
    "evaluate_combination",
    "extract_markdown_headings",
    "extract_markdown_tables",
    "load_ground_truth_markdown",
    "main",
    "model_for",
    "render_competitor_caveat",
    "render_failures",
    "render_markdown_report",
    "render_report",
    "resolve_out_dir",
    "resolve_paths",
    "run_path_document",
    "score_markdown_pair",
    "select_jobs",
]


if __name__ == "__main__":
    raise SystemExit(main())
