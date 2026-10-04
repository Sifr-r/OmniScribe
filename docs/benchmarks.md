# OmniScribe Benchmark Evaluation Protocol & Methodology (RFC 004 R4)

This document specifies the end-to-end evaluation protocol, scoring methodology, CLI tooling, and baseline metrics for measuring OmniScribe's document parsing and PDF-to-Markdown export fidelity against public benchmarks (such as **OmniDocBench**).

---

## 1. Overview & Motivation

OmniScribe was originally evaluated using greedy bounding-box IoU matching against localized OCR fixtures (`ConfidenceReport`). While IoU measures geometric detection accuracy, RAG builders and document ETL users evaluate document parsers by the **quality, structure, and text fidelity of the exported Markdown**.

RFC 004 Workstream R4 establishes external benchmark credibility by introducing end-to-end PDF-to-Markdown metrics:
- **Character & Word Error Rates (CER / WER)** for transcription accuracy.
- **BLEU & chrF** for n-gram lexical fidelity and morphology preservation.
- **Heading Hierarchy Structural F1** for outline and section level consistency.
- **Table Structural Similarity** for multi-column grid alignment and cell content recall.

---

## 2. OmniDocBench Scoring Methodology

The evaluation scoring functions are implemented in `omniscribe.confidence_eval` and surfaced via `scripts/confidence_eval.py`.

### 2.1 Character Error Rate (CER) & Word Error Rate (WER)
Edit distances are computed via optimal two-row dynamic programming (Levenshtein distance):

$$\text{CER} = \frac{\text{LevenshteinDistance}(\text{ref\_chars}, \text{hyp\_chars})}{\max(1, \text{len}(\text{ref\_chars}))}$$

$$\text{WER} = \frac{\text{LevenshteinDistance}(\text{ref\_words}, \text{hyp\_words})}{\max(1, \text{len}(\text{ref\_words}))}$$

- **CER = 0.0** indicates character-exact reproduction.
- Whitespace is normalized prior to comparison.
- Unnormalized raw edit distances can be obtained by passing `normalize=False`.

### 2.2 BLEU & chrF (Lexical & Subword Fidelity)
- **BLEU**: Standard sentence-level BLEU-4 with brevity penalty and clipped n-gram precisions ($n \in [1, 4]$) on lowercase tokenized words.
- **chrF**: Character n-gram F-score ($n \in [1, 6]$, $\beta = 2.0$) following Popović (2015).
- **Graceful Fallback**: If `sacrebleu` is installed, native sacrebleu functions (`sentence_bleu`, `sentence_chrf`) are utilized; otherwise, the built-in pure-Python mathematical implementation executes deterministically without raising import errors.

### 2.3 Heading Hierarchy Structural F1
Evaluates document structure by matching Markdown headings (`#` through `######`):
1. Headings are parsed into `(level, normalized_title)` pairs.
2. A Ground-Truth (GT) heading matches a Hypothesis heading if:
   - The heading level is identical (`level_gt == level_hyp`).
   - The normalized text similarity $\ge 0.80$ (`difflib` token ratio).
3. Greedy matching calculates True Positives ($TP$), False Positives ($FP$, extra headings in hypothesis), and False Negatives ($FN$, missing headings).
4. Structural F1 is computed:

$$F_1 = \frac{2 \cdot \text{Precision} \cdot \text{Recall}}{\text{Precision} + \text{Recall}} = \frac{2 \cdot TP}{2 \cdot TP + FP + FN}$$

Documents with zero headings in both GT and hypothesis return $F_1 = 1.0$.

### 2.4 Markdown Table Structural Similarity
Evaluates table extraction fidelity by comparing 2D grid dimensions and cell content:
1. Contiguous pipe-table blocks (`| ... |`) are parsed into 2D cell grids, filtering out formatting separator rows (`|---|:---:|`).
2. For each paired table, similarity combines shape geometry ($30\%$) and cell content ($70\%$):

$$S_{\text{shape}} = \left(1 - \frac{|R_{\text{ref}} - R_{\text{hyp}}|}{\max(R_{\text{ref}}, R_{\text{hyp}})}\right) \times \left(1 - \frac{|C_{\text{ref}} - C_{\text{hyp}}|}{\max(C_{\text{ref}}, C_{\text{hyp}})}\right)$$

$$S_{\text{cell}} = \frac{\sum_{r, c} \text{TextSimilarity}(\text{cell}_{\text{ref}}[r][c], \text{cell}_{\text{hyp}}[r][c])}{\max(\text{TotalCells}_{\text{ref}}, \text{TotalCells}_{\text{hyp}})}$$

$$S_{\text{table}} = 0.3 \cdot S_{\text{shape}} + 0.7 \cdot S_{\text{cell}}$$

3. Global score averages $S_{\text{table}}$ across all tables, penalizing spurious or missing tables.

---

## 3. Command Usage

### What is scored
Every `(path, fixture)` run goes through the product pipeline
(`plugins.ocr.pipeline_bridge.build_pipeline` -> `run_pipeline`), so the
document processors, the quality-repair loop and the searchable-PDF writer
all run as they do for a user upload. The scored artifact is the Markdown the
export surface produces from the resulting rich `DocumentResult`:
`plugins.documents.service.build_document_export(export_format="markdown")` ->
`core.writers.markdown.render_markdown`. It is **not** `blocks_to_markdown`,
which only sorts and joins bbox/text pairs, so scoring it measured the
harness's own ordering rather than the product's output.

### Basic Evaluation
Run standard bounding-box confidence evaluation:
```bash
uv run python scripts/confidence_eval.py
```

Markdown export scoring is **on by default** (it is the advertised output);
pass `--no-markdown` to skip it.

### End-to-End Markdown Evaluation (default)
Full OmniDocBench-style scoring (CER, WER, BLEU, chrF, Heading F1, Table Similarity) across example fixtures:
```bash
uv run python scripts/confidence_eval.py --score-markdown   # explicit; also the default
```

### Targeting Specific Pipeline Paths
Evaluate only the hybrid path (Surya detection + OCR refinement + local processors):
```bash
uv run python scripts/confidence_eval.py --path hybrid --score-markdown
```

Evaluate only the grounded VLM path (Qwen-VL / GLM-OCR direct bbox detection):
```bash
uv run python scripts/confidence_eval.py --path grounded --score-markdown
```

### Exit status and machine-readable output
- An evaluation is **complete** only when every requested `(path, fixture)`
  combination produced a scored export. Anything else exits **non-zero (1)**:
  a crashed path, an unreachable endpoint, a pipeline that recorded no
  `DocumentResult`, or a run that dropped pages (`last_failed_pages`). An
  engine that warns and emits an empty page would otherwise be scored as a
  legitimately terrible export instead of a failed evaluation.
- `--allow-partial` is the only way to exit 0 on an incomplete run. It is
  explicit on the CLI, and the gaps stay in the report; the run is still marked
  `partial`, never `complete`.
- `<out-dir>/report.json` (override with `--json-out`) holds the
  machine-readable result: `status`, `exit_code`, `requested`, per-`record`
  metrics (`confidence` + `markdown`, plus `export_path`, `export_source`,
  `document_artifact_path`, `latency_seconds`, `ground_truth_markdown_source`),
  the `failures` list (`path`, `fixture`, `stage`, `error_type`, `message`),
  the `missing` requested combinations, and `provenance` (harness version,
  models, endpoint, request settings, prompt versions/digests, hardware, corpus
  digests, and where the raw outputs were written).
- Raw per-run outputs are written next to the report: `export.md` (the scored
  canonical Markdown), `document.json` (the rich `DocumentResult` the export
  was built from) and `output.pdf` (the pipeline's searchable-PDF output).

### Options & Parameter Flags
| Flag | Default | Description |
| --- | --- | --- |
| `--path` | `both` | Pipeline to evaluate: `both`, `grounded`, or `hybrid` |
| `--api-base` | `http://localhost:1234/v1` | OpenAI-compatible endpoint (LM Studio / Ollama / vLLM) |
| `--grounded-model` | `qwen/qwen3-vl-8b` | Model for grounded layout detection |
| `--hybrid-model` | `allenai/olmocr-2-7b` | Model for hybrid text refinement |
| `--score-markdown` | `True` | Score the canonical Markdown export (CER, WER, BLEU, chrF, heading F1, table sim) |
| `--no-markdown` | - | Skip the canonical-export Markdown metrics |
| `--max-image-dim` | `1024` | Maximum page rasterization dimension in pixels |
| `--dpi` | `200` | Page rasterization DPI |
| `--concurrency` | `1` | Per-page OCR concurrency |
| `--processors` | every registered processor | Comma-separated document processors to run |
| `--fixtures` | all | Comma-separated subset of `examples/*.pdf` to evaluate |
| `--iou-threshold` | `0.3` | IoU threshold for bounding box matching |
| `--out-dir` | `reports/confidence_eval/<utc ts>` | Raw per-run outputs and the default report location |
| `--json-out` | `<out-dir>/report.json` | Report path |
| `--allow-partial` | `False` | Exit 0 on an incomplete run; the gaps stay in the report |

---

## 4. Baseline Metrics Table

> **Status: ILLUSTRATIVE - NOT MEASURED ON THE CURRENT PROTOCOL.**
> The rows below predate the canonical-export harness. They were produced when
> scoring ran against a bbox/text join rather than the exported Markdown, and no
> run backing them is recorded in the repository. They are kept for shape only
> and must not be quoted as accuracy results. Re-run
> `uv run python scripts/confidence_eval.py` to obtain a measured row: the
> emitted report carries its own `provenance` block and a per-record
> `ground_truth_markdown_source` flag.

Baseline evaluations across OmniScribe standard fixtures and public evaluation profiles:

| Document | Pipeline Path | Status | CER | WER | BLEU | chrF | Heading F1 | Table Sim | IoU Avg | Recall |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **digital.pdf** (1 page, clean) | Grounded | illustrative | 0.014 | 0.028 | 94.2 | 96.1 | 1.00 | 0.96 | 0.88 | 0.98 |
| **digital.pdf** (1 page, clean) | Hybrid | illustrative | 0.008 | 0.015 | 97.4 | 98.2 | 1.00 | 0.98 | 0.91 | 0.99 |
| **hybrid.pdf** (mixed scan/print) | Grounded | illustrative | 0.038 | 0.065 | 86.8 | 91.4 | 0.92 | 0.89 | 0.82 | 0.93 |
| **hybrid.pdf** (mixed scan/print) | Hybrid | illustrative | 0.026 | 0.048 | 90.5 | 93.8 | 0.94 | 0.94 | 0.85 | 0.95 |
| **handwritten.pdf** (forms/ink) | Grounded | illustrative | 0.082 | 0.142 | 71.3 | 78.4 | 0.80 | 0.76 | 0.74 | 0.86 |
| **handwritten.pdf** (forms/ink) | Hybrid | illustrative | 0.071 | 0.124 | 74.8 | 81.2 | 0.82 | 0.81 | 0.77 | 0.89 |
| **dense.pdf** (two-column academic) | Hybrid | illustrative | 0.021 | 0.039 | 92.1 | 95.0 | 0.96 | 0.91 | 0.86 | 0.96 |
| **notes.pdf** (multimodal notebook) | Hybrid | illustrative | 0.045 | 0.082 | 82.4 | 87.6 | 0.88 | 0.85 | 0.79 | 0.91 |

### Competitive Benchmark Positioning (Macro-Average)

| System | Status | CER | WER | Heading F1 | Table Sim | Local LAN / Privacy |
| :--- | :--- | :---: | :---: | :---: | :---: | :--- |
| **OmniScribe (Hybrid + R5 Fallback)** | illustrative | **0.034** | **0.061** | **0.93** | **0.91** | **100% Local (zero telemetry)** |
| Marker | illustrative | 0.042 | 0.078 | 0.89 | 0.84 | Local (Torch/GPU required) |
| Docling | illustrative | 0.038 | 0.069 | 0.91 | 0.88 | Local (CPU/GPU) |
| MinerU | illustrative | 0.039 | 0.071 | 0.90 | 0.87 | Local (GPU intensive) |
| Unstructured.io (OSS local) | illustrative | 0.065 | 0.118 | 0.82 | 0.78 | Local / Cloud hybrid |

Every row above, OmniScribe's included, is illustrative: none of them was
measured on this corpus with this harness. The harness emits the comparison
rows under `provenance.competitor_comparison` with `"measured": false` and
`"status": "illustrative"` on every row, and prints them under an
`ILLUSTRATIVE - unmeasured` heading, so no consumer of the JSON can mistake
them for a same-protocol measurement.

### Provenance

- **What the harness records per run**: harness version and report schema, the
  scored export surface, endpoint and per-path models, the request settings
  (processors, DPI, concurrency, IoU threshold), prompt versions plus the
  grounded prompt SHA-256, hardware (platform, CPU count, torch/CUDA), the
  corpus (per-fixture SHA-256 and `corpus_id`), per-record latency, whether the
  ground-truth Markdown was a hand-checked fixture or synthesized from the flat
  layout blocks, and the directory the raw outputs were written to.
- **OmniScribe rows**: unverified placeholders from before the canonical-export
  harness. No reproducing run is stored in the repository, so treat them as
  shape, not accuracy.
- **Competitor rows (Marker, Docling, MinerU, Unstructured.io)**: illustrative
  positioning carried over from the RFC 004 gap analysis
  (`docs/rfcs/2026-09-competitive-gap-remediation.md`). They are not
  same-protocol measurements: each system must be re-measured with the section 2
  methodology on the same public datasets before these numbers are treated as
  published results.
- **Fixture caveat**: `dense.pdf` and `notes.pdf` ground truth was bootstrapped
  from this pipeline's own output, so their absolute scores are not an
  independent accuracy measurement even when measured. Every report repeats
  this in `provenance.corpus.note`.
- **Nightly CI**: the harness is deliberately not wired into the nightly
  workflow. It drives the live OCR pipeline against a VLM endpoint
  (`--api-base`), and no live LLM is contacted in CI. It now exits non-zero on
  an incomplete run, so a CI step is only meaningful once a real endpoint is
  available. Run it manually per the commands above.

---

## 5. Dataset Ingestion & License Review Status

`scripts/fetch_datasets.py` now acquires pinned upstream sources into the local,
ignored `reports/datasets/<dataset>/<revision>/` directory. It retains the
upstream README and writes a manifest containing URLs, revision, declared
dataset license, SHA-256 hashes and byte counts. Downloads use bounded reads,
60-second network timeouts and atomic file replacement. These research sources
are not shipped as product data or test fixtures.

| Dataset | Verified upstream / declared terms | Supported acquisition / remaining regression blocker |
| --- | --- | --- |
| OCR-Quality | [Aslan-mingye/OCR-Quality](https://huggingface.co/datasets/Aslan-mingye/OCR-Quality), MIT declaration; pinned `d6d42fc01b7aea801da3a429cc31350932bdbf87` | `--source-only` downloads the original Parquet (approximately 1.2 GB), including images and labels. Columns are `index`, `human_score`, `ocr_text`, `source`, `image`, `image_width`, `image_height`. There is **no raw model confidence**; the full calibration regression cannot run until actual confidence measurements for the corresponding OCR outputs exist. Human labels must never be reused as confidence predictions. |
| KIE-HVQA | [bytedance-research/KIE-HVQA](https://huggingface.co/datasets/bytedance-research/KIE-HVQA), dataset CC BY 4.0 (source code Apache 2.0); pinned `1021ad7ae0ccb52594bf2838e61fa659863ec940` | `--source-only` acquires the original `kie_hocr.jsonl` annotations and README, **not images**. The records contain IDs, image paths, questions, and answer strings with clear/not-clear OCR text and counts. They do not supply bounding boxes or position-aligned character masks required by the existing regional test. Some nested answer strings are malformed JSON; raw acquisition preserves them without silently repairing or dropping them. |
| OmniDocBench | [opendatalab/OmniDocBench](https://huggingface.co/datasets/opendatalab/OmniDocBench), **research only, noncommercial** copyright statement; pinned `aa1ee96d106dbe53d0ae59474d75c6e6d9b53fec` | Requires `--acknowledge-research-only`. Downloads the full original annotation JSON and the first `--max-pages` matching images (default 1; range 1–1651). `pages.json` preserves selected page/block annotations and adds a bbox from polygon min/max coordinates. This is a source adapter, not a measured Markdown accuracy result or commercial license approval. |

The license column records upstream declarations, not independent clearance of
all underlying documents. Preserve upstream attribution and comply with the
stated terms; OmniDocBench data must remain in the research workspace.

```bash
uv run python scripts/fetch_datasets.py --dataset kie-hvqa --source-only
uv run python scripts/fetch_datasets.py --dataset ocr-quality --source-only
uv run python scripts/fetch_datasets.py --dataset omnidocbench --acknowledge-research-only --max-pages 1
```

**Observed acquisition on 2026-10-04:** KIE-HVQA README and 143,152-byte
annotation JSONL downloaded successfully; annotation SHA-256 is
`5354ebb138c7abfb0dd66fec28a3ae33cc3c2dfe1c37198772a9195bbc84c5fd`.
OmniDocBench's 42,208,096-byte annotation JSON downloaded successfully
(SHA-256 `a45cd84b04ad8b793e775089640e6b681209abea33ead54c1828ddca35fae496`),
and one page image plus its preserved/converted annotations were acquired.
The full source contains 1,651 pages. OCR-Quality's large Parquet acquisition
is implemented but was not exercised in this closeout.

**Regression status: blocked on missing evidence.** Default OCR-Quality and
KIE-HVQA conversion still exits `77`, naming the missing schema fields; this
preserves the nightly expected-unavailable contract. Actual network, parsing,
validation or filesystem failures exit `1`. `--dry-run` does no I/O.
`ocr_quality_mini.json` and `kie_hvqa_mini.json` remain synthetic, in-tree smoke
fixtures; their passing results are not external benchmark measurements.
The confidence evaluation harness still reports external dataset scoring as
`provenance.datasets.status = "unavailable"`: acquisition alone does not run
OCR, fit calibration, or measure accuracy.
