#!/usr/bin/env python3
"""Acquire pinned benchmark sources; never invent confidence/reliability labels.

Default OCR-Quality/KIE-HVQA regression conversion exits 77: their actual
schemas cannot supply the required calibration/regional evidence. --source-only
preserves upstream files with provenance, outside shipped test fixtures.
OmniDocBench supports bounded page/image acquisition and polygon-to-bbox
conversion for research only, requiring --acknowledge-research-only.
Exit codes: 0 success/dry-run; 77 unavailable conversion/license gate; 1 failure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import urllib.error
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from typing import cast

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASETS_DIR = PROJECT_ROOT / "tests" / "fixtures" / "datasets"
SOURCE_DIR = PROJECT_ROOT / "reports" / "datasets"
OCR_QUALITY_REPO = "Aslan-mingye/OCR-Quality"
KIE_HVQA_REPO = "bytedance-research/KIE-HVQA"
EXIT_LICENSE_GATED = 77  # Preserve the nightly expected-unavailable contract.
_LOG = logging.getLogger("scripts.fetch_datasets")

# Reviewed upstream identities, revisions and dataset (not code) licenses.
SOURCES = {
    "ocr-quality": (
        OCR_QUALITY_REPO,
        "d6d42fc01b7aea801da3a429cc31350932bdbf87",
        "MIT",
        "OCR-Quality.parquet",
    ),
    "kie-hvqa": (
        KIE_HVQA_REPO,
        "1021ad7ae0ccb52594bf2838e61fa659863ec940",
        "CC-BY-4.0",
        "kie_hocr.jsonl",
    ),
    "omnidocbench": (
        "opendatalab/OmniDocBench",
        "aa1ee96d106dbe53d0ae59474d75c6e6d9b53fec",
        "research-only; noncommercial",
        "OmniDocBench.json",
    ),
}


class DatasetUnavailableError(RuntimeError):
    """The license or upstream annotations cannot support this operation."""


class DatasetInputError(ValueError):
    """An upstream record cannot be converted safely."""


def _download(
    repo: str, revision: str, filename: str, root: Path, limit: int
) -> dict[str, object]:
    """Stream a bounded, fixed-host source to an atomic workspace destination."""
    relative = Path(filename)
    if (
        relative.is_absolute()
        or any(part in ("", "..", ".") for part in filename.split("/"))
        or "\\" in filename
        or ":" in filename
        or "?" in filename
        or "#" in filename
    ):
        raise DatasetInputError(f"unsafe upstream filename: {filename!r}")
    target = root / relative
    if not target.resolve().is_relative_to(
        root.resolve()
    ) or not target.resolve().is_relative_to(PROJECT_ROOT.resolve()):
        raise DatasetInputError(
            f"upstream filename escapes source directory: {filename!r}"
        )
    url = f"https://huggingface.co/datasets/{repo}/resolve/{revision}/{filename}"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    if not temporary.resolve().is_relative_to(
        root.resolve()
    ) or not temporary.resolve().is_relative_to(PROJECT_ROOT.resolve()):
        raise DatasetInputError(
            f"temporary output escapes the project workspace: {temporary}"
        )
    digest = hashlib.sha256()
    size = 0
    try:
        with (
            urllib.request.urlopen(url, timeout=60) as response,
            temporary.open("wb") as output,
        ):
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise DatasetInputError(
                        f"{repo}/{filename}: exceeds {limit} byte limit"
                    )
                digest.update(chunk)
                output.write(chunk)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return {"path": filename, "url": url, "sha256": digest.hexdigest(), "bytes": size}


def _write_atomic(target: Path, payload: str) -> None:
    if not target.resolve().is_relative_to(PROJECT_ROOT.resolve()):
        raise DatasetInputError(f"output escapes the project workspace: {target}")
    temporary = target.with_suffix(target.suffix + ".tmp")
    if not temporary.resolve().is_relative_to(PROJECT_ROOT.resolve()):
        raise DatasetInputError(
            f"temporary output escapes the project workspace: {temporary}"
        )
    try:
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def _convert_pages(data: object, max_pages: int) -> list[dict[str, object]]:
    """Preserve page annotations and add bbox coordinates without scoring labels."""
    if not isinstance(data, list) or not data:
        raise DatasetInputError("OmniDocBench: expected a nonempty page array")
    converted: list[dict[str, object]] = []
    for page_number, page in enumerate(data[:max_pages]):
        if (
            not isinstance(page, dict)
            or not isinstance(page.get("page_info"), dict)
            or not isinstance(page.get("layout_dets"), list)
        ):
            raise DatasetInputError(
                f"OmniDocBench page {page_number}: missing page_info/layout_dets"
            )
        info = page["page_info"]
        image = info.get("image_path")
        if (
            not isinstance(image, str)
            or not image
            or Path(image).name != image
            or "\\" in image
            or ":" in image
        ):
            raise DatasetInputError(
                f"OmniDocBench page {page_number}: unsafe image_path"
            )
        for dimension in ("width", "height"):
            if type(info.get(dimension)) is not int or info[dimension] <= 0:
                raise DatasetInputError(
                    f"OmniDocBench page {page_number}: invalid {dimension}"
                )
        blocks: list[dict[str, object]] = []
        for block in page["layout_dets"]:
            if not isinstance(block, dict) or not isinstance(
                block.get("category_type"), str
            ):
                raise DatasetInputError(
                    f"OmniDocBench page {page_number}: invalid block"
                )
            poly = block.get("poly")
            if (
                not isinstance(poly, list)
                or len(poly) != 8
                or any(
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    for value in poly
                )
            ):
                raise DatasetInputError(
                    f"OmniDocBench page {page_number}: invalid polygon"
                )
            xs, ys = poly[::2], poly[1::2]
            blocks.append({**block, "bbox": [min(xs), min(ys), max(xs), max(ys)]})
        converted.append({**page, "layout_dets": blocks})
    return converted


def _acquire(dataset: str, max_pages: int) -> Path:
    repo, revision, license_name, filename = SOURCES[dataset]
    root = SOURCE_DIR / dataset / revision
    if not root.resolve().is_relative_to(PROJECT_ROOT.resolve()):
        raise DatasetInputError(
            "source revision directory escapes the project workspace"
        )
    # A prior manifest must never claim a partially refreshed acquisition succeeded.
    manifest = root / "manifest.json"
    manifest.unlink(missing_ok=True)
    files = [_download(repo, revision, "README.md", root, 1024 * 1024)]
    files.append(
        _download(
            repo,
            revision,
            filename,
            root,
            2 * 1024**3 if dataset == "ocr-quality" else 64 * 1024**2,
        )
    )
    page_count = None
    if dataset == "omnidocbench":
        pages = _convert_pages(
            json.loads((root / filename).read_text(encoding="utf-8")), max_pages
        )
        for page in pages:
            info = cast(dict[str, object], page["page_info"])
            files.append(
                _download(
                    repo, revision, f"images/{info['image_path']}", root, 32 * 1024**2
                )
            )
        payload = (
            json.dumps(pages, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        )
        _write_atomic(root / "pages.json", payload)
        files.append(
            {
                "path": "pages.json",
                "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                "bytes": len(payload.encode("utf-8")),
                "transform": "retain upstream annotations; bbox=min/max polygon coordinates",
            }
        )
        page_count = len(pages)
    _write_atomic(
        manifest,
        json.dumps(
            {
                "dataset": repo,
                "revision": revision,
                "license": license_name,
                "files": files,
                "selected_pages": page_count,
                "regression_fixture_created": False,
            },
            indent=2,
        )
        + "\n",
    )
    return manifest


def fetch(
    dataset: str,
    *,
    dry_run: bool,
    source_only: bool = False,
    max_pages: int = 1,
    acknowledge_research_only: bool = False,
) -> Path:
    if dataset not in SOURCES:
        raise DatasetInputError(f"unknown dataset: {dataset}")
    if type(max_pages) is not int or not 1 <= max_pages <= 1651:
        raise DatasetInputError("max-pages must be in [1,1651]")
    if not SOURCE_DIR.resolve().is_relative_to(PROJECT_ROOT.resolve()):
        raise DatasetInputError("source output must stay inside the project workspace")
    if dataset == "omnidocbench" and not acknowledge_research_only:
        raise DatasetUnavailableError(
            "OmniDocBench permits research only, not commercial use; supply --acknowledge-research-only for research acquisition"
        )
    repo, revision, _, _ = SOURCES[dataset]
    target = (
        SOURCE_DIR / dataset / revision / "manifest.json"
        if source_only or dataset == "omnidocbench"
        else DATASETS_DIR / f"{dataset.replace('-', '_')}_full.json"
    )
    if dry_run:
        _LOG.info(
            "dry-run: %s revision %s → %s; regression conversion remains gated",
            repo,
            revision,
            target,
        )
        return target
    if source_only or dataset == "omnidocbench":
        return _acquire(dataset, max_pages)
    if dataset == "ocr-quality":
        raise DatasetUnavailableError(
            f"{repo} declares MIT, but has human_score and no raw_confidence. Actual model confidence measurements are required; see docs/benchmarks.md §5."
        )
    raise DatasetUnavailableError(
        f"{repo} declares CC-BY-4.0, but kie_hocr.jsonl has question/answer strings and counts, not bbox or position-aligned reliability masks. The regional regression needs verified matching annotations."
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, choices=tuple(SOURCES))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--source-only",
        action="store_true",
        help="Acquire pinned upstream files, without producing a regression fixture",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=1,
        help="OmniDocBench page/image limit (1..1651)",
    )
    parser.add_argument(
        "--acknowledge-research-only",
        action="store_true",
        help="Acknowledge OmniDocBench's research-only, noncommercial restriction",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args(argv)
    try:
        target = fetch(
            args.dataset,
            dry_run=args.dry_run,
            source_only=args.source_only,
            max_pages=args.max_pages,
            acknowledge_research_only=args.acknowledge_research_only,
        )
    except DatasetUnavailableError as exc:
        _LOG.warning("dataset unavailable: %s", exc)
        return EXIT_LICENSE_GATED
    except (
        DatasetInputError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        urllib.error.URLError,
    ) as exc:
        _LOG.error("dataset acquisition failed: %s", exc)
        return 1
    _LOG.info("dataset output: %s", target)
    return 0


__all__ = ["EXIT_LICENSE_GATED", "fetch", "parse_args"]

if __name__ == "__main__":
    raise SystemExit(main())
