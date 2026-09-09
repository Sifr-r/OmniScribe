"""
Shared fixtures.

- `examples_dir` / `example_pdfs` — on-disk sample documents under examples/.
- `surya_aligner` — a real HybridAligner instance shared across the session
  (Surya model load is ~5s, so we only want to pay it once).
- `stub_ocr` — an OCRProcessor replacement that returns canned text without
  hitting LM Studio, so tests can run offline.
- `cordis_env` / `harness_ctx` / `api_client` — plugin-harness boot
  fixtures: a temp thirteen-row ``cordis.yml`` (memory backend, small TTLs),
  a loaded harness Context, and a TestClient over ``create_app()``.
- `EXAMPLE_PDF_NAMES` — the canonical list of on-disk example PDF filenames
  (and the few images) so every parametrized test surface stays in
  lock step with the fixtures in this module.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

# Silence Surya's internal tqdm before any module loads it.
os.environ.setdefault("TQDM_DISABLE", "1")
os.environ.setdefault("ALLOW_SSRF_LOCAL", "true")

# Repo root, plus the ``sys.path`` mutation that audit D20 wanted
# scoped. The original was ``sys.path.insert(0, str(ROOT))`` with no
# explanation; the intent (now documented) is to make ``tests`` a
# resolvable package so cross-test imports like
# ``from tests.plugins.test_glossary_service import FakeLexiconStore``
# in ``tests/routers/test_glossary_routes.py`` work. ``tests/`` is a
# PEP 420 namespace package — it has no ``__init__.py`` at the root
# but the subpackages (``tests/plugins/__init__.py``,
# ``tests/routers/__init__.py``, etc.) do — so the repo root has to
# be on ``sys.path`` to anchor the package. Insert only if not
# already present, so repeated ``conftest`` imports are idempotent.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# Canonical list of on-disk example PDF filenames. Add a new file here
# (and to ``examples/`` on disk) and every parametrize site picks it up.
EXAMPLE_PDF_NAMES: list[str] = [
    "digital.pdf",
    "hybrid.pdf",
    "handwritten.pdf",
    "dense.pdf",
    "notes.pdf",
]


FIXTURES_PDFS_DIR: Path = ROOT / "tests" / "fixtures" / "pdfs"

# Bundle-side mirror of the canonical fixture set. Populated by
# ``make pre-build`` before wheel / PyInstaller builds (so the
# ``sample_pdfs`` plugin has the PDFs to stream in production) and
# by the autouse session fixture below for the test suite on a
# fresh checkout. P5 reductive audit: the destination is a build
# artifact, not source — see the gitignore block in ``.gitignore``
# and the matching ``make pre-build`` target in the ``Makefile``.
SAMPLE_PDFS_RESOURCES_DIR: Path = (
    ROOT / "src" / "omniscribe" / "resources" / "sample_pdfs"
)


@pytest.fixture(scope="session", autouse=True)
def _ensure_sample_pdfs_resources() -> None:
    """Copy canonical fixtures into the bundle-side mirror if empty.

    Idempotent: a no-op when the destination already has PDFs (the
    common case for anyone who has run ``make bundle`` or another
    build). Runs once per test session; copies are bit-identical to
    the source so the existing
    ``test_allowlist_matches_resources_dir`` lockstep test still
    passes. A ``.gitkeep`` (also tracked) is what keeps the
    destination directory present on a clean checkout.
    """
    import shutil

    if SAMPLE_PDFS_RESOURCES_DIR.is_dir() and any(
        SAMPLE_PDFS_RESOURCES_DIR.glob("*.pdf")
    ):
        return
    SAMPLE_PDFS_RESOURCES_DIR.mkdir(parents=True, exist_ok=True)
    for pdf in FIXTURES_PDFS_DIR.glob("*.pdf"):
        shutil.copy2(pdf, SAMPLE_PDFS_RESOURCES_DIR / pdf.name)


@pytest.fixture(scope="session")
def examples_dir() -> Path:
    if FIXTURES_PDFS_DIR.is_dir():
        return FIXTURES_PDFS_DIR
    d = ROOT / "examples"
    assert d.is_dir(), f"examples directory missing: {d}"
    return d


@pytest.fixture(scope="session")
def example_pdfs(examples_dir: Path) -> dict[str, Path]:
    fallback_dir = ROOT / "examples"
    paths: dict[str, Path] = {}
    for n in EXAMPLE_PDF_NAMES:
        fixture_path = FIXTURES_PDFS_DIR / n
        if fixture_path.exists():
            paths[n] = fixture_path
        elif (fallback_dir / n).exists():
            paths[n] = fallback_dir / n
        else:
            paths[n] = fixture_path
    missing = [n for n, p in paths.items() if not p.exists()]
    if missing:
        pytest.skip(f"example PDFs not found: {missing}")
    return paths


@pytest.fixture(scope="session")
def surya_aligner():
    """Load HybridAligner once per session — Surya init is expensive."""
    from omniscribe.core.aligner import HybridAligner

    return HybridAligner()


class _StubOCR:
    """
    Drop-in replacement for OCRProcessor.

    Returns a fixed list of lines for full-page OCR and a fixed string for
    crops. Also records every call so tests can assert on behaviour.
    """

    def __init__(
        self, page_lines: list[str] | None = None, crop_text: str = "recovered"
    ):
        self.page_lines = page_lines or [
            "Section heading",
            "First paragraph of body text with several words.",
            "Second paragraph with more content to align.",
            "Closing line.",
        ]
        self.crop_text = crop_text
        self.page_calls = 0
        self.crop_calls = 0
        # ``client`` mirrors OCRProcessor's long-lived AsyncOpenAI; tests
        # that exercise the aclose lifecycle inject a mock here.
        self.client: object | None = None

    async def aclose(self) -> None:
        """Mirror OCRProcessor.aclose: release ``self.client`` if set."""
        if self.client is None:
            return
        client = self.client
        self.client = None
        close_method = getattr(client, "aclose", None) or getattr(client, "close", None)
        if close_method is None:
            return
        result = close_method()
        if asyncio.iscoroutine(result):
            await result

    async def perform_ocr(self, image_base64: str, **kwargs) -> list[str]:
        self.page_calls += 1
        return list(self.page_lines)

    async def perform_ocr_on_crop(self, image_base64: str, **kwargs) -> str:
        self.crop_calls += 1
        return self.crop_text


@pytest.fixture
def stub_ocr():
    return _StubOCR()


@pytest.fixture
def make_stub_ocr():
    """Factory fixture for tests that need a customised stub."""
    return _StubOCR


# ---------------------------------------------------------------------------
# Plugin-harness boot fixtures
# ---------------------------------------------------------------------------

# Thirteen-row test tree: mirrors the shipped thirteen-plugin cordis.yml
# plus the documents plugin, with a forced memory backend and small TTLs
# so tests stay fast and offline.
_TEST_CORDIS_YML = """\
plugins:
  - id: runtime
    use: omniscribe.plugins.runtime:plugin
    config:
      cleanup_interval_seconds: 60
      artifact_ttl_seconds: 60
      channel_ttl_seconds: 60

  - id: logging
    use: omniscribe.plugins.logging:plugin
    config:
      format: text
      level: INFO

  - id: state_backend
    use: omniscribe.plugins.state_backend:plugin
    config:
      backend: memory

  - id: artifacts
    use: omniscribe.plugins.artifacts:plugin

  - id: jobs
    use: omniscribe.plugins.jobs:plugin
    config:
      worker_count: 1

  - id: progress
    use: omniscribe.plugins.progress:plugin
    config:
      frame_cap: 100

  - id: providers
    use: omniscribe.plugins.providers:plugin
    config:
      discovery_timeout_seconds: 1

  - id: health
    use: omniscribe.plugins.health:plugin

  - id: documents
    use: omniscribe.plugins.documents:plugin

  - id: translate
    use: omniscribe.plugins.translate:plugin

  - id: transcribe
    use: omniscribe.plugins.transcribe:plugin

  - id: glossary
    use: omniscribe.plugins.glossary:plugin

  - id: ocr
    use: omniscribe.plugins.ocr:plugin
"""

_CORDIS_ENV_VARS = (
    "OMNISCRIBE_STATE_BACKEND",
    "OMNISCRIBE_STATE_DB_PATH",
    "OMNISCRIBE_LOG_FORMAT",
    "OMNISCRIBE_LOG_LEVEL",
    "OMNISCRIBE_QUALITY_LOOP",
    "OMNISCRIBE_QUALITY_TARGET",
    "OMNISCRIBE_QUALITY_MAX_RETRIES",
    "OMNISCRIBE_CORDIS_CONFIG",
    "OMNISCRIBE_CORDIS_PATCH",
)


@pytest.fixture
def cordis_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Deterministic harness boot: isolated artifact dir + temp cordis.yml."""
    for name in _CORDIS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OMNISCRIBE_ARTIFACT_DIR", str(tmp_path))
    cordis_yml = tmp_path / "cordis.yml"
    cordis_yml.write_text(_TEST_CORDIS_YML, encoding="utf-8")
    return cordis_yml


@pytest.fixture
async def harness_ctx(cordis_env: Path):
    """A loaded harness Context for the ten-plugin test tree."""
    from omniscribe.harness.context import Context
    from omniscribe.harness.loader import Loader

    ctx = Context()
    await Loader(ctx).load(cordis_env)
    try:
        yield ctx
    finally:
        await ctx.dispose()


@pytest.fixture
def api_client(cordis_env: Path, monkeypatch: pytest.MonkeyPatch):
    """TestClient over ``create_app()`` booted from the temp cordis.yml."""
    from fastapi.testclient import TestClient

    from omniscribe.server import create_app

    monkeypatch.setenv("OMNISCRIBE_CORDIS_CONFIG", str(cordis_env))
    with TestClient(create_app()) as client:
        yield client


# Audit-secondary F24 (Phase 4): the Phase 0/1 debug shelf was moved
# from ``tests/_diag/`` to ``scripts/diagnostics/`` so it is no
# longer auto-collected by pytest. The previous
# ``collect_ignore_glob = ["_diag/*"]`` was a fragile single-line
# guard; a future contributor "fixing" the conftest would have
# silently re-enabled collection and broken the fast tier on a
# hidden ``sys.path.insert``. The diagnostic scripts are now run
# by hand (``uv run python scripts/diagnostics/test_*.py``).
