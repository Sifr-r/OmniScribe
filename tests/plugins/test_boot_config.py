"""Boot tree: the shipped cordis.yml mounts all fourteen plugins and services.

Sprint 3 (RFC 002 §4 Option b, audit U12) added the 14th row,
``sample_pdfs``, which serves canonical fixture PDFs at
``/api/sample-pdf/{name}``. Adding a row here requires the
``thirteen -> fourteen`` rename below plus the new entry in the
literal list in :func:`test_shipped_cordis_yml_declares_fourteen_rows_in_boot_order`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omniscribe.harness.context import Context
from omniscribe.harness.loader import Loader, parse_rows
from omniscribe.plugins.artifacts import ArtifactStore
from omniscribe.plugins.jobs import JobQueue, JobRunner
from omniscribe.plugins.ocr.plugin import OCRService
from omniscribe.plugins.progress import ProgressService
from omniscribe.plugins.providers import ProviderManager
from omniscribe.plugins.runtime import RuntimeService
from omniscribe.plugins.state_backend import StateBackend

SHIPPED_CORDIS_YML = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "omniscribe"
    / "resources"
    / "cordis.yml"
)

_CORDIS_ENV_VARS = (
    "OMNISCRIBE_STATE_BACKEND",
    "OMNISCRIBE_STATE_DB_PATH",
    "OMNISCRIBE_LOG_FORMAT",
    "OMNISCRIBE_LOG_LEVEL",
    "OMNISCRIBE_QUALITY_LOOP",
    "OMNISCRIBE_QUALITY_TARGET",
    "OMNISCRIBE_QUALITY_MAX_RETRIES",
)


@pytest.fixture
def clean_cordis_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralize env inputs so the shipped tree expands deterministically."""
    for name in _CORDIS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_shipped_cordis_yml_declares_fourteen_rows_in_boot_order() -> None:
    rows = parse_rows(SHIPPED_CORDIS_YML.read_text(encoding="utf-8"))
    assert [row.id for row in rows] == [
        "runtime",
        "logging",
        "state_backend",
        "artifacts",
        "jobs",
        "progress",
        "providers",
        "health",
        "documents",
        "translate",
        "transcribe",
        "glossary",
        "ocr",
        "sample_pdfs",
    ]


async def test_shipped_cordis_yml_mounts_full_service_tree(
    clean_cordis_env: None,
) -> None:
    ctx = Context()
    try:
        await Loader(ctx).load(SHIPPED_CORDIS_YML)
        for protocol in (
            RuntimeService,
            StateBackend,
            ArtifactStore,
            JobQueue,
            ProgressService,
            ProviderManager,
            OCRService,
            JobRunner,
        ):
            assert ctx.has(protocol), f"{protocol.__name__} not registered"
        # health, providers, progress, documents, translate, transcribe,
        # glossary, ocr, and sample_pdfs each mount one router.
        assert len(ctx.routes()) == 9
    finally:
        await ctx.dispose()


async def test_patch_file_overrides_row_config(
    clean_cordis_env: None, tmp_path: Path
) -> None:
    patch = tmp_path / "cordis.patch.yml"
    patch.write_text(
        "plugins:\n"
        "  - id: runtime\n"
        "    use: omniscribe.plugins.runtime:plugin\n"
        "    config:\n"
        "      cleanup_interval_seconds: 5\n",
        encoding="utf-8",
    )
    ctx = Context()
    try:
        await Loader(ctx).load(SHIPPED_CORDIS_YML, patch_paths=[patch])
        runtime_service = ctx.inject(RuntimeService)
        # Patched field overrides; unlisted fields are inherited.
        assert runtime_service.config.cleanup_interval_seconds == 5
        assert runtime_service.config.artifact_ttl_seconds == 86_400
    finally:
        await ctx.dispose()


async def test_env_override_seeds_quality_defaults(
    clean_cordis_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OMNISCRIBE_PLUGIN_OCR__QUALITY_MAX_RETRIES", "4")
    ctx = Context()
    try:
        await Loader(ctx).load(SHIPPED_CORDIS_YML)
        service = ctx.inject(OCRService)
        assert service.quality_defaults["quality_max_retries"] == 4
        # Unset fields keep the shipped defaults.
        assert service.quality_defaults["quality_target"] == 0.85
    finally:
        await ctx.dispose()
