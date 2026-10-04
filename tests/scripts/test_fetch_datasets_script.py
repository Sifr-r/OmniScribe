"""Offline acquisition, provenance, containment and schema-gate regressions."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"


def _load_fetch_datasets():
    spec = importlib.util.spec_from_file_location(
        "_fetch_datasets_under_test", SCRIPTS_DIR / "fetch_datasets.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_script_is_runnable_help():
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "fetch_datasets.py"), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "ocr-quality" in result.stdout
    assert "kie-hvqa" in result.stdout


@pytest.mark.parametrize("dataset", ["ocr-quality", "kie-hvqa"])
def test_dry_run_exits_cleanly(dataset: str):
    # ``--dry-run`` must succeed without network access so CI can
    # exercise the CLI without a real dataset download.
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "fetch_datasets.py"),
            "--dataset",
            dataset,
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_unknown_dataset_exits_nonzero():
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "fetch_datasets.py"),
            "--dataset",
            "unknown-dataset",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0


@pytest.mark.parametrize("dataset", ["ocr-quality", "kie-hvqa"])
def test_license_gated_fetch_exits_with_dedicated_code(dataset: str):
    """A real (non-dry-run) fetch must exit ``EXIT_LICENSE_GATED`` (77).

    Nightly CI uses this code to tell "expected license-gated skip"
    apart from genuine breakage — a generic error exit would either
    fail every nightly run or get swallowed by an ``|| true`` escape
    hatch (audit P3-12).
    """
    expected = _load_fetch_datasets().EXIT_LICENSE_GATED
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "fetch_datasets.py"),
            "--dataset",
            dataset,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == expected, result.stderr
    # The gate must not leave a partial fixture behind.
    suffix = (
        "ocr_quality_full.json" if dataset == "ocr-quality" else "kie_hvqa_full.json"
    )
    assert not (
        SCRIPTS_DIR.parent / "tests" / "fixtures" / "datasets" / suffix
    ).exists()


def test_source_acquisition_preserves_provenance_and_repeatability(
    tmp_path, monkeypatch
):
    module = _load_fetch_datasets()
    monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(module, "SOURCE_DIR", tmp_path / "reports")
    calls = []

    def response(url, timeout):
        calls.append(url)
        assert timeout == 60
        return io.BytesIO(b"upstream data")

    monkeypatch.setattr(module.urllib.request, "urlopen", response)
    target = module.fetch("kie-hvqa", dry_run=False, source_only=True)
    first = target.read_bytes()
    assert (
        module.fetch("kie-hvqa", dry_run=False, source_only=True).read_bytes() == first
    )
    manifest = json.loads(first)
    assert manifest["dataset"] == "bytedance-research/KIE-HVQA"
    assert manifest["license"] == "CC-BY-4.0"
    assert manifest["regression_fixture_created"] is False
    assert all(manifest["revision"] in url for url in calls)
    assert (
        manifest["files"][0]["sha256"] == hashlib.sha256(b"upstream data").hexdigest()
    )


def test_download_limit_preserves_previous_file_and_removes_partial(
    tmp_path, monkeypatch
):
    module = _load_fetch_datasets()
    monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        module.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: io.BytesIO(b"too large"),
    )
    target = tmp_path / "README.md"
    target.write_text("previous", encoding="utf-8")
    with pytest.raises(module.DatasetInputError, match="byte limit"):
        module._download("owner/repo", "revision", "README.md", tmp_path, 2)
    assert target.read_text(encoding="utf-8") == "previous"
    assert not target.with_suffix(".md.tmp").exists()


@pytest.mark.parametrize(
    "filename",
    [
        "../escape",
        "/absolute",
        "C:/escape",
        "images\\escape",
        "",
        "images/./escape",
        "images//escape",
    ],
)
def test_download_rejects_unsafe_paths_before_network(tmp_path, monkeypatch, filename):
    module = _load_fetch_datasets()
    monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        module.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("network contacted"),
    )
    with pytest.raises(module.DatasetInputError, match=r"unsafe|escapes"):
        module._download("owner/repo", "revision", filename, tmp_path, 20)


def test_omnidocbench_conversion_retains_annotations_and_rejects_invalid_data():
    module = _load_fetch_datasets()
    polygon: list[float] = [8, 2, 3, 2, 3, 9, 8, 9]
    block: dict[str, object] = {
        "category_type": "table",
        "poly": polygon,
        "html": "<table></table>",
        "order": 1,
    }
    page_info: dict[str, object] = {"image_path": "page.png", "width": 10, "height": 10}
    page = {
        "page_info": page_info,
        "layout_dets": [block],
        "extra": {"relation": []},
    }
    converted = module._convert_pages([page, page], 1)
    assert len(converted) == 1
    assert converted[0]["layout_dets"][0] == {**block, "bbox": [3, 2, 8, 9]}
    assert converted[0]["extra"] == page["extra"]
    assert "bbox" not in block  # Source remains unchanged.
    polygon[0] = float("nan")
    with pytest.raises(module.DatasetInputError, match="polygon"):
        module._convert_pages([page], 1)
    page_info["image_path"] = "../escape.png"
    with pytest.raises(module.DatasetInputError, match="unsafe image"):
        module._convert_pages([page], 1)


def test_research_acknowledgement_and_limits_are_enforced_before_acquisition(
    monkeypatch,
):
    module = _load_fetch_datasets()
    monkeypatch.setattr(
        module, "_acquire", lambda *_args: pytest.fail("acquisition started")
    )
    with pytest.raises(module.DatasetUnavailableError, match="research only"):
        module.fetch("omnidocbench", dry_run=False)
    with pytest.raises(module.DatasetInputError, match="max-pages"):
        module.fetch(
            "omnidocbench", dry_run=True, max_pages=0, acknowledge_research_only=True
        )
    assert (
        module.fetch("omnidocbench", dry_run=True, acknowledge_research_only=True).name
        == "manifest.json"
    )


def test_nested_source_root_cannot_escape_workspace(tmp_path, monkeypatch):
    module = _load_fetch_datasets()
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(module, "PROJECT_ROOT", project)
    monkeypatch.setattr(module, "SOURCE_DIR", project / "reports")
    monkeypatch.setattr(
        module.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("network contacted"),
    )
    with pytest.raises(module.DatasetInputError, match="escapes"):
        module._download("owner/repo", "revision", "README.md", tmp_path, 20)
    assert not (tmp_path / "README.md").exists()


def test_failed_refresh_cannot_leave_success_manifest(tmp_path, monkeypatch):
    module = _load_fetch_datasets()
    monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(module, "SOURCE_DIR", tmp_path / "reports")
    revision = module.SOURCES["kie-hvqa"][1]
    manifest = module.SOURCE_DIR / "kie-hvqa" / revision / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("previous success", encoding="utf-8")

    def fail_download(*_args, **_kwargs):
        raise OSError("network unavailable")

    monkeypatch.setattr(module, "_download", fail_download)
    with pytest.raises(OSError, match="network unavailable"):
        module.fetch("kie-hvqa", dry_run=False, source_only=True)
    assert not manifest.exists()
