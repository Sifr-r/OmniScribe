#!/usr/bin/env python3
"""Fetch the OCR-Quality and KIE-HVQA datasets for Phase 3 regression tests.

**F5-18 audit fix — this module is a deliberate STUB.**

The actual download URLs are gated behind the license review described in
``docs/benchmarks.md`` §5 (Dataset Ingestion & License Review Status). Until the license review completes:

- ``tests/fixtures/datasets/ocr_quality_mini.json`` (10 records) is
  shipped in-tree and covers the smoke test for ``slow_dataset``.
- ``tests/fixtures/datasets/ocr_quality_full.json`` is *not* present;
  the ``test_ocr_quality_calibration_regression.py::TestFullFixtureRegression``
  tests skip with a clear "fetch datasets" message.

Once the OCR-Quality license is confirmed compatible:

1. Add ``OCR_QUALITY_DOWNLOAD_URL`` to the constants below.
2. The HuggingFace ``imagefolder`` dataset loads from
   ``datasets.load_dataset("Aslan-mingye/OCR-Quality", ...)`` and we
   convert each image+annotation pair to the ``{raw_confidence,
   quality_score}`` records expected by ``scripts/calibrate_model.py``.
3. We write the converted JSON to
   ``tests/fixtures/datasets/ocr_quality_full.json``.

Usage::

    uv run scripts/fetch_datasets.py --dataset ocr-quality
    uv run scripts/fetch_datasets.py --dataset kie-hvqa

Run with ``--dry-run`` to print the planned operations without
hitting the network.

Exit codes (audit P3-12): ``0`` = fetched (or dry-run), ``77``
(``EX_NOPERM``) = the fetch is gated behind the license review and no
data was written, any other non-zero = genuine failure. CI relies on
the ``0`` vs ``77`` vs anything-else distinction to tell an expected
skip apart from real breakage — the old ``|| true`` swallowed both.
The eventual implementation needs ``from datasets import load_dataset``
(``uv sync --extra datasets``); see the conversion plan above.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]
DATASETS_DIR = PROJECT_ROOT / "tests" / "fixtures" / "datasets"

_LOG = logging.getLogger("scripts.fetch_datasets")

# HuggingFace identifiers for the two datasets. The full download
# URLs are not inlined because they may change; we point at the
# dataset pages instead and let ``huggingface_hub`` resolve the
# latest snapshot.
OCR_QUALITY_REPO = "Aslan-mingye/OCR-Quality"
KIE_HVQA_REPO = "ByteDance/KIE-HVQA"  # placeholder until license is confirmed

#: Exit code for "the fetch is gated behind the license review" —
#: sysexits ``EX_NOPERM``. Nightly CI treats this as a clean skip; any
#: other non-zero exit fails the calibration job.
EXIT_LICENSE_GATED = 77


def _ocr_quality_records() -> list[dict[str, object]]:
    """Download OCR-Quality and convert to ``{raw_confidence, quality_score}``."""
    # The OCR-Quality dataset is shipped as ``imagefolder`` — each
    # entry has a PNG (300 DPI page image) and a metadata row with
    # the VLM output and human quality score. We do not have the
    # raw VLM confidences (the dataset only carries the final
    # scores), so the conversion is a placeholder until we get
    # confidences out of the VLM ourselves.
    raise NotImplementedError(
        "OCR-Quality fetch is gated behind the license review in "
        "docs/benchmarks.md §5. "
        "Until that review completes, the mini fixture "
        "(tests/fixtures/datasets/ocr_quality_mini.json) is the only "
        "OCR-Quality-format data we ship."
    )


def _kie_hvqa_records() -> list[dict[str, object]]:
    """Download KIE-HVQA and convert to ``{text, bbox, reliability}``."""
    raise NotImplementedError(
        "KIE-HVQA fetch is gated behind the license review. The mini "
        "fixture (tests/fixtures/datasets/kie_hvqa_mini.json) covers "
        "the smoke test for the slow_dataset regression."
    )


def fetch(dataset: str, *, dry_run: bool) -> Path:
    if dataset == "ocr-quality":
        target = DATASETS_DIR / "ocr_quality_full.json"
        if dry_run:
            _LOG.info("dry-run: would fetch %s → %s", OCR_QUALITY_REPO, target)
            return target
        records = _ocr_quality_records()
    elif dataset == "kie-hvqa":
        target = DATASETS_DIR / "kie_hvqa_full.json"
        if dry_run:
            _LOG.info("dry-run: would fetch %s → %s", KIE_HVQA_REPO, target)
            return target
        records = _kie_hvqa_records()
    else:
        raise SystemExit(f"unknown dataset: {dataset}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    _LOG.info("wrote %d records to %s", len(records), target)
    return target


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        required=True,
        choices=("ocr-quality", "kie-hvqa"),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without hitting the network.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # F5-18 audit fix: loud, single-line stub warning so an operator
    # running this on the assumption it will fetch real data cannot
    # miss that it's a placeholder. The per-dataset NotImplementedError
    # below is the actual gate; this is the surface-level reminder.
    print(
        "WARNING: scripts/fetch_datasets.py is a license-gated STUB. "
        "Real fetching is blocked pending the review in "
        "docs/benchmarks.md §5.",
        file=sys.stderr,
    )
    args = parse_args(argv)
    try:
        fetch(args.dataset, dry_run=args.dry_run)
    except NotImplementedError as exc:
        _LOG.warning("license-gated, skipping: %s", exc)
        return EXIT_LICENSE_GATED
    return 0


__all__ = ["EXIT_LICENSE_GATED", "fetch", "parse_args"]


if __name__ == "__main__":
    raise SystemExit(main())
