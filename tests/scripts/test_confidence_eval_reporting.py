"""Reporting and scoring-surface tests for the confidence-eval harness.

The harness (scripts/confidence_eval.py) exists to measure the artifact a
user downloads. These tests pin three things that were previously false and
easy to regress:

* the scored Markdown comes from the canonical export surface
  (``build_document_export`` -> ``render_markdown``), not from
  ``blocks_to_markdown``'s bbox/text join;
* a failed or missing path is a recorded failure *and* a non-zero exit, so an
  incomplete requested evaluation cannot look like a pass;
* every run emits machine-readable provenance, and the competitor rows stay
  labelled illustrative.

No live LLM is required: the pipeline seam (``run_path_document``) is
replaced, and the end-to-end exit-status test drives the real CLI against an
unreachable endpoint.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from omniscribe.confidence_eval import (
    EXIT_INCOMPLETE,
    EXIT_OK,
    EvaluationFailure,
    EvaluationReport,
    PathResult,
    blocks_from_document_result,
    blocks_to_markdown,
    compute_cer,
    compute_table_similarity,
    score_markdown_pair,
)
from omniscribe.config import RuntimeSettings
from omniscribe.core.block_tree import BlockNode, BlockType, DocumentTree, PageTree
from omniscribe.core.block_tree import TableNode as TreeTableNode
from omniscribe.core.document import DocumentBlock, DocumentPage, DocumentResult
from scripts import confidence_eval as harness

ROOT = Path(__file__).resolve().parents[2]

#: Ground truth for the synthetic fixture. Deliberately richer than a flat
#: text join: a heading, a table, and a body line, in reading order.
GT_LAYOUT = [
    {
        "block_label": "text",
        "bbox": [100, 50, 900, 100],
        "block_content": "Quarterly Report",
        "page_index": 0,
    },
    {
        "block_label": "table",
        "bbox": [100, 200, 900, 350],
        "block_content": "Region Revenue\nEMEA 120",
        "page_index": 0,
    },
    {
        "block_label": "text",
        "bbox": [100, 400, 900, 500],
        "block_content": "Revenue grew across every region.",
        "page_index": 0,
    },
]


def _cell(text: str, bbox: tuple[float, float, float, float]) -> BlockNode:
    return BlockNode(
        block_type=BlockType.TEXT, bbox=bbox, text=text, page_idx=0, level=0
    )


def sample_document() -> DocumentResult:
    """A document whose export carries structure a text join cannot.

    The rich ``tree`` holds a level-2 heading and a GFM table grid; the flat
    ``pages`` carry only the recognized text. Scoring the join would throw
    the heading level and the table shape away.
    """
    heading = BlockNode(
        block_type=BlockType.SECTION_HEADER,
        bbox=(0.1, 0.05, 0.9, 0.1),
        text="Quarterly Report",
        page_idx=0,
        level=2,
    )
    table = TreeTableNode(
        rows=2,
        cols=2,
        page_idx=0,
        bbox=(0.1, 0.2, 0.9, 0.35),
        cells=[
            [
                _cell("Region", (0.1, 0.2, 0.5, 0.27)),
                _cell("Revenue", (0.5, 0.2, 0.9, 0.27)),
            ],
            [
                _cell("EMEA", (0.1, 0.28, 0.5, 0.35)),
                _cell("120", (0.5, 0.28, 0.9, 0.35)),
            ],
        ],
    )
    body = BlockNode(
        block_type=BlockType.TEXT,
        bbox=(0.1, 0.4, 0.9, 0.5),
        text="Revenue grew across every region.",
        page_idx=0,
    )
    page = PageTree(
        page_idx=0, width=1000, height=1000, children=[heading, table, body]
    )
    return DocumentResult(
        pages=[
            DocumentPage(
                page_index=0,
                width=1000,
                height=1000,
                blocks=[
                    DocumentBlock(
                        bbox=(0.1, 0.05, 0.9, 0.1),
                        text="Quarterly Report",
                        kind="section_header",
                    ),
                    DocumentBlock(
                        bbox=(0.1, 0.2, 0.9, 0.35),
                        text="Region Revenue\nEMEA 120",
                        kind="table",
                    ),
                    DocumentBlock(
                        bbox=(0.1, 0.4, 0.9, 0.5),
                        text="Revenue grew across every region.",
                        kind="text",
                    ),
                ],
            )
        ],
        source_path="sample.pdf",
        tree=DocumentTree(pages=[page], tables=[table]),
    )


def _corpus(tmp_path: Path) -> Path:
    """Point the harness at a synthetic one-fixture corpus in ``tmp_path``."""
    examples = tmp_path / "examples"
    fixtures = tmp_path / "fixtures"
    examples.mkdir()
    fixtures.mkdir()
    (examples / "sample.pdf").write_bytes(b"%PDF-1.4 synthetic fixture")
    (fixtures / "ground_truth_sample.json").write_text(
        json.dumps(
            {
                "data": {
                    "layout": GT_LAYOUT,
                    "data_info": {"pages": [{"width": 1000, "height": 1000}]},
                }
            }
        ),
        encoding="utf-8",
    )
    harness.EXAMPLES = examples
    harness.FIXTURES = fixtures
    harness.JOBS = [("sample.pdf", "ground_truth_sample.json")]
    return tmp_path


def _fake_pipeline(
    monkeypatch: pytest.MonkeyPatch, *, failing_paths: frozenset[str] = frozenset()
) -> None:
    """Replace the pipeline seam; raise a connection error for failed paths."""

    async def _run_path_document(
        *, pdf: Path, output_pdf: Path, request: Any, settings: Any
    ) -> tuple[DocumentResult, float]:
        if request.pipeline_mode in failing_paths:
            raise ConnectionRefusedError(
                f"[WinError 10061] no endpoint at {request.api_base}"
            )
        output_pdf.write_bytes(b"%PDF-1.4 pipeline output")
        return sample_document(), 0.5

    monkeypatch.setattr(harness, "run_path_document", _run_path_document)


def _run_harness(tmp_path: Path, *extra: str) -> tuple[int, dict[str, Any]]:
    """Run the harness in-process against the synthetic corpus."""
    out_dir = tmp_path / "run"
    report_path = out_dir / "report.json"
    exit_code = harness.main(
        [
            "--fixtures",
            "sample.pdf",
            "--out-dir",
            str(out_dir),
            "--json-out",
            str(report_path),
            *extra,
        ]
    )
    return exit_code, json.loads(report_path.read_text(encoding="utf-8"))


# --- 1. the scored artifact is the canonical export ------------------------


def test_canonical_export_differs_from_a_bbox_text_join() -> None:
    """The export surface keeps structure the join drops (acceptance pin)."""
    document = sample_document()

    export_md = harness.canonical_markdown_export(document)
    join_md = blocks_to_markdown(blocks_from_document_result(document))

    # Canonical export: heading level, GFM table with a separator row.
    assert "## Quarterly Report" in export_md
    assert "| Region | Revenue |" in export_md
    assert "| --- | --- |" in export_md
    assert "| EMEA | 120 |" in export_md

    # Naive join: same words, no structure at all.
    assert "##" not in join_md
    assert "| --- |" not in join_md
    assert export_md != join_md

    # Structure metrics are only non-zero against the real export.
    assert compute_table_similarity(export_md, export_md) == 1.0
    assert compute_table_similarity(join_md, export_md) == 0.0


def test_harness_scores_the_canonical_export(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The Markdown the harness scores is the exported one, not the join."""
    _corpus(tmp_path)
    _fake_pipeline(monkeypatch)
    document = sample_document()

    exit_code, report = _run_harness(tmp_path)
    assert exit_code == EXIT_OK

    record = report["records"][0]
    scored_md = Path(record["export_path"]).read_text(encoding="utf-8")
    assert scored_md == harness.canonical_markdown_export(document)
    assert scored_md != blocks_to_markdown(blocks_from_document_result(document))
    assert record["export_source"] == harness.CANONICAL_EXPORT_SOURCE

    # A perfect export against a reference that is the export itself scores
    # CER 0. The naive join would not.
    assert score_markdown_pair("sample.pdf", scored_md, scored_md).cer == 0.0
    assert (
        compute_cer(
            scored_md, blocks_to_markdown(blocks_from_document_result(document))
        )
        > 0.0
    )


def test_export_uses_the_documents_plugin_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The export goes through the same builder the export route calls."""
    calls: list[dict[str, Any]] = []
    real_builder = harness.build_document_export

    def _spy(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return real_builder(**kwargs)

    monkeypatch.setattr(harness, "build_document_export", _spy)
    harness.canonical_markdown_export(sample_document())

    assert len(calls) == 1
    assert calls[0]["export_format"] == "markdown"
    assert isinstance(calls[0]["document"], DocumentResult)
    assert calls[0]["page_text"]


# --- 2. failures are records, and the exit status reflects them -------------


def test_missing_endpoint_fails_the_evaluation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unreachable endpoint is a failure record and a failing exit."""
    _corpus(tmp_path)
    _fake_pipeline(monkeypatch, failing_paths=frozenset({"grounded"}))

    exit_code, report = _run_harness(tmp_path)

    assert exit_code == EXIT_INCOMPLETE
    assert report["status"] == "partial"
    assert len(report["failures"]) == 1
    failure = report["failures"][0]
    assert failure["path"] == "grounded"
    assert failure["fixture"] == "ground_truth_sample.json"
    assert failure["stage"] == "pipeline"
    assert failure["error_type"] == "ConnectionRefusedError"
    assert "no endpoint" in failure["message"]
    # The failed record carries no metrics -- absence, not a zero score.
    assert report["records"][0]["status"] == "failed"
    assert report["records"][0]["markdown"] is None
    assert report["records"][0]["confidence"] is None


def test_partial_run_of_both_paths_exits_non_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One failed path in a two-path request fails the whole evaluation."""
    _corpus(tmp_path)
    _fake_pipeline(monkeypatch, failing_paths=frozenset({"hybrid"}))

    exit_code, report = _run_harness(tmp_path, "--path", "both")

    assert exit_code == EXIT_INCOMPLETE
    assert report["status"] == "partial"
    assert report["requested"]["paths"] == ["grounded", "hybrid"]
    assert {record["path"]: record["status"] for record in report["records"]} == {
        "grounded": "scored",
        "hybrid": "failed",
    }
    assert [failure["path"] for failure in report["failures"]] == ["hybrid"]
    assert report["missing"] == []


def test_allow_partial_is_explicit_and_keeps_the_gap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """--allow-partial changes the status only, never the record."""
    _corpus(tmp_path)
    _fake_pipeline(monkeypatch, failing_paths=frozenset({"hybrid"}))

    exit_code, report = _run_harness(tmp_path, "--path", "both", "--allow-partial")

    assert exit_code == EXIT_OK
    assert report["status"] == "partial"
    assert report["allow_partial"] is True
    assert len(report["failures"]) == 1
    assert report["records"][0]["status"] == "scored"
    assert harness.build_parser().parse_args(["--allow-partial"]).allow_partial is True
    assert harness.build_parser().parse_args([]).allow_partial is False


def test_missing_record_counts_as_incomplete() -> None:
    """A requested combination that produced no record is a gap, not a pass."""
    report = EvaluationReport(
        requested_paths=["grounded", "hybrid"],
        requested_fixtures=["ground_truth_sample.json"],
        records=[],
    )
    assert report.missing_combinations() == [
        {"path": "grounded", "fixture": "ground_truth_sample.json"},
        {"path": "hybrid", "fixture": "ground_truth_sample.json"},
    ]
    assert not report.is_complete
    assert report.status == "incomplete"
    assert report.exit_code() == EXIT_INCOMPLETE


def test_failure_record_round_trips_to_json() -> None:
    """A failure carries the four facts a consumer needs to act on."""
    failure = EvaluationFailure.from_exception(
        path="grounded",
        fixture="ground_truth_sample.json",
        stage="pipeline",
        exc=TimeoutError(""),
    )
    record = PathResult.from_failure(failure).to_dict()
    assert record["failure"] == {
        "path": "grounded",
        "fixture": "ground_truth_sample.json",
        "stage": "pipeline",
        "error_type": "TimeoutError",
        "message": "TimeoutError('')",
    }
    assert record["status"] == "failed"


# --- 3. a degraded run is a failure, not a low score -----------------------


def _stub_engine(
    monkeypatch: pytest.MonkeyPatch,
    *,
    document: DocumentResult | None,
    failed: list[int],
) -> None:
    """Replace the engine seam with a fixed DocumentResult / failure set."""

    class _Pipeline:
        def __init__(self) -> None:
            self.last_document_result = document
            self.last_failed_pages = failed

    monkeypatch.setattr(
        harness, "build_pipeline", lambda settings, request: _Pipeline()
    )

    async def _run(*_args: Any, **_kwargs: Any) -> dict[int, list[str]]:
        return {}

    monkeypatch.setattr(harness, "run_pipeline", _run)


def _request() -> Any:
    return harness.build_request(
        path="grounded",
        model="test-model",
        api_base="http://127.0.0.1:1/v1",
        max_image_dim=1024,
        processors=harness.ALL_PROCESSORS,
        dpi=200,
        concurrency=1,
    )


def test_run_with_failed_pages_is_not_scored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An engine that drops pages and warns must not yield a clean score.

    The grounded engine logs ``grounded OCR failed for page N`` and still
    emits a document. Scoring that empty export would report a 100% error
    rate as a legitimate measurement, so the harness rejects the run.
    """
    _stub_engine(monkeypatch, document=sample_document(), failed=[0])

    with pytest.raises(harness.HarnessError) as excinfo:
        asyncio.run(
            harness.run_path_document(
                pdf=tmp_path / "in.pdf",
                output_pdf=tmp_path / "out.pdf",
                request=_request(),
                settings=RuntimeSettings(),
            )
        )
    assert "dropped 1 page" in str(excinfo.value)
    assert "[0]" in str(excinfo.value)


def test_run_without_text_is_not_scored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An empty document has no export content to compare against."""
    empty = DocumentResult(pages=[DocumentPage(page_index=0)])
    _stub_engine(monkeypatch, document=empty, failed=[])

    with pytest.raises(harness.HarnessError) as excinfo:
        asyncio.run(
            harness.run_path_document(
                pdf=tmp_path / "in.pdf",
                output_pdf=tmp_path / "out.pdf",
                request=_request(),
                settings=RuntimeSettings(),
            )
        )
    assert "no recognized text" in str(excinfo.value)


def test_run_without_document_result_is_not_scored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No rich document means no canonical export exists."""
    _stub_engine(monkeypatch, document=None, failed=[])

    with pytest.raises(harness.HarnessError) as excinfo:
        asyncio.run(
            harness.run_path_document(
                pdf=tmp_path / "in.pdf",
                output_pdf=tmp_path / "out.pdf",
                request=_request(),
                settings=RuntimeSettings(),
            )
        )
    assert "no DocumentResult" in str(excinfo.value)


# --- 4. provenance and honest comparison labels ----------------------------


def test_complete_run_records_provenance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Every requested fixture/path gets a record and a provenance block."""
    _corpus(tmp_path)
    _fake_pipeline(monkeypatch)

    exit_code, report = _run_harness(tmp_path, "--path", "both")
    assert exit_code == EXIT_OK

    assert len(report["records"]) == 2
    for record in report["records"]:
        assert record["status"] == "scored"
        assert Path(record["export_path"]).exists()
        assert Path(record["document_artifact_path"]).exists()
        assert record["confidence"] is not None
        assert record["markdown"] is not None
        assert record["latency_seconds"] == 0.5
        assert record["ground_truth_markdown_source"] == "ground_truth_blocks"

    provenance = report["provenance"]
    assert provenance["harness"]["scored_artifact"] == harness.CANONICAL_EXPORT_SOURCE
    assert provenance["endpoint"]["models"] == {
        "grounded": harness.build_parser().get_default("grounded_model"),
        "hybrid": harness.build_parser().get_default("hybrid_model"),
    }
    assert provenance["settings"]["document_processors"] == list(harness.ALL_PROCESSORS)
    assert provenance["prompts"]["grounded"]["version"]
    assert provenance["prompts"]["grounded"]["sha256"]
    assert provenance["hardware"]["python"]
    assert provenance["corpus"]["fixtures"][0]["ground_truth"]["sha256"]
    assert provenance["raw_outputs"]["directory"] == str(tmp_path / "run")
    assert provenance["datasets"]["status"] == "unavailable"
    assert Path(record["export_path"]).is_relative_to(
        Path(provenance["raw_outputs"]["directory"])
    )


def test_competitor_rows_are_marked_illustrative(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Comparison rows are labelled unmeasured in the JSON and on screen."""
    _corpus(tmp_path)
    _fake_pipeline(monkeypatch)

    _exit_code, report = _run_harness(tmp_path)

    comparison = report["provenance"]["competitor_comparison"]
    assert comparison["measured"] is False
    assert comparison["rows"], "the comparison block must not be silently empty"
    for row in comparison["rows"]:
        assert row["measured"] is False
        assert row["status"] == "illustrative"
        assert "Illustrative" in row["caveat"]

    # Every published competitor row carries the same label.
    for row in harness.ILLUSTRATIVE_COMPETITOR_ROWS:
        assert row["system"]
    assert harness.ILLUSTRATIVE_ROW_STATUS["measured"] is False

    console_rows = [
        dict(row, **harness.ILLUSTRATIVE_ROW_STATUS)
        for row in harness.ILLUSTRATIVE_COMPETITOR_ROWS
    ]
    assert all(row["status"] == "illustrative" for row in console_rows)


def test_dataset_downloads_are_reported_unavailable() -> None:
    """License-gated dataset stubs are stated, never presented as measured."""
    status = harness.DATASET_DOWNLOAD_STATUS
    assert status["status"] == "unavailable"
    assert "stub" in status["reason"]
    assert "OmniDocBench" in status["datasets"]


# --- 5. real CLI: an unreachable endpoint exits non-zero -------------------


def test_cli_with_unreachable_endpoint_exits_non_zero(tmp_path: Path) -> None:
    """End-to-end proof: a missing endpoint fails the evaluation.

    Drives the real CLI (no stubs) against a closed local port, so the
    pipeline seam, the failure record, the report file, and the process
    status are all exercised together.
    """
    env = dict(os.environ)
    # A loopback URL is SSRF-guarded unless local targets are allowed; and
    # no retry backoff keeps the check fast (the endpoint is simply absent).
    env["ALLOW_SSRF_LOCAL"] = "1"
    env["OMNISCRIBE_LLM_MAX_RETRIES"] = "0"
    out_dir = tmp_path / "run"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "confidence_eval.py"),
            "--path",
            "grounded",
            "--fixtures",
            "digital.pdf",
            "--api-base",
            "http://127.0.0.1:1/v1",
            "--out-dir",
            str(out_dir),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=600,
    )
    assert result.returncode == EXIT_INCOMPLETE, result.stdout + result.stderr

    report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "partial"
    assert report["requested"]["paths"] == ["grounded"]
    assert report["failures"], "an unreachable endpoint must be recorded"
    failure = report["failures"][0]
    assert failure["path"] == "grounded"
    assert failure["fixture"] == "ground_truth_digital.json"
    assert failure["stage"] in {"pipeline", "scoring"}
    assert failure["message"]
    assert report["provenance"]["corpus"]["corpus_id"]


def test_cli_help_documents_the_exit_status_rule() -> None:
    """The CLI states the failing-status rule and the partial opt-in."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "confidence_eval.py"), "--help"],
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    assert result.returncode == 0
    assert "--allow-partial" in result.stdout
    assert "Exit 0 even when a requested path/fixture failed" in result.stdout
    assert "--json-out" in result.stdout
