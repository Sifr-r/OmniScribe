# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the OmniScribe FastAPI server (Phase 4.4, RFC 001
# Option A). Run with:
#
#     uv run --with pyinstaller --no-sync pyinstaller omniscribe_server.spec
#
# or, with the venv that has PyInstaller installed:
#
#     uv run pyinstaller omniscribe_server.spec
#
# Output: ``dist/omniscribe-server/omniscribe-server.exe`` (Windows) or
# ``dist/omniscribe-server/omniscribe-server`` (Linux/macOS). The
# resulting binary is a self-contained onefile that bundles Python
# 3.12 + every transitive runtime dependency (torch, surya-ocr,
# pymupdf, etc.). The first run takes ~5-10 minutes to extract the
# onefile archive; subsequent runs are ~1 second to start.
#
# What this spec must get right:
#
# 1. **Cordis plugin tree.** The harness boots by reading
#    ``src/omniscribe/resources/cordis.yml`` and dynamically
#    importing each plugin's ``:plugin`` attribute. PyInstaller's
#    static analysis can't see those imports, so every plugin
#    module is listed in ``hiddenimports`` below. Forgetting one
#    yields a runtime ``PluginLoadError`` at the user's first
#    ``uv run`` of the bundle.
#
# 2. **Heavy ML deps.** ``torch`` (and its CUDA backends), ``surya``
#    (and its model/recognition/layout/detection submodules), and
#    ``pymupdf`` (a C extension with a hidden ``fitz._extra`` module)
#    are the most common PyInstaller surprises. The
#    ``collect_submodules`` calls below pull in the entire package
#    trees so we don't have to enumerate every leaf.
#
# 3. **Runtime data files.** ``src/omniscribe/resources/`` ships
#    ``cordis.yml`` (the plugin tree) and the bundled
#    ``dictionaries/`` (used by the spellchecker fallback). Both
#    must land in the bundle's data directory. The ``datas`` list
#    below mirrors them under ``omniscribe/resources/`` so the
#    runtime ``Path(__file__).parent / "resources"`` lookup just
#    works.
#
# 4. **No dev/test deps.** Test plugins (``pytest``, ``hypothesis``,
#    ``anyio`` test extras) and linting tools are not in the runtime
#    tree and would bloat the bundle. The ``excludes`` list filters
#    them out; ``collect_submodules`` from the previous step only
#    walks the runtime tree, so this is belt-and-braces.
#
# Things that are explicitly out of scope for v0.2.0:
# - Codesigning (no cert budget; see RFC 001 §"Decision needed").
# - macOS notarization.
# - Linux AppImage packaging.
# - A single-file ``--onefile`` mode would shrink the cold-cache
#   extract cost but PyInstaller 6.x's onefile has known issues
#   with ML models that load lazily; onedir (the default) is the
#   safe choice for v0.2.

import os
import sys
from PyInstaller.utils.hooks import collect_submodules

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

# SPEC is the absolute path to this .spec file, injected by PyInstaller.
SPECPATH = os.path.abspath(SPEC)
ROOT = os.path.dirname(SPECPATH)
SRC = os.path.join(ROOT, "src")

# Sanity-check before we burn a 5-minute build: refuse to run from
# anywhere that doesn't look like the repo root. Catches a class of
# "I cd'd somewhere weird and now nothing works" misconfigurations.
_required_paths = [
    os.path.join(SRC, "omniscribe", "server.py"),
    os.path.join(SRC, "omniscribe", "resources", "cordis.yml"),
    os.path.join(ROOT, "scripts", "run_server.py"),
]
for _p in _required_paths:
    if not os.path.exists(_p):
        raise SystemExit(
            f"omniscribe_server.spec: required path missing: {_p}\n"
            "Run pyinstaller from the repository root, or fix SPECPATH."
        )


# ---------------------------------------------------------------------------
# Hidden imports
# ---------------------------------------------------------------------------

# Cordis plugin tree — every row in src/omniscribe/resources/cordis.yml.
# Listed explicitly so the bundle fails loud at build time if the
# harness gains a new plugin in cordis.yml that the spec doesn't know
# about. (PyInstaller's static analysis misses all of these because
# the harness imports them dynamically via ``importlib``.)
_CORDIS_PLUGINS = [
    "omniscribe.plugins.runtime",
    "omniscribe.plugins.logging",
    "omniscribe.plugins.state_backend",
    "omniscribe.plugins.artifacts",
    "omniscribe.plugins.jobs",
    "omniscribe.plugins.progress",
    "omniscribe.plugins.providers",
    "omniscribe.plugins.health",
    "omniscribe.plugins.documents",
    "omniscribe.plugins.translate",
    "omniscribe.plugins.transcribe",
    "omniscribe.plugins.glossary",
    "omniscribe.plugins.ocr",
    # Sprint 3 (RFC 002 §4 Option b, audit U12). Listed explicitly so
    # the bundle fails loud at build time if a future maintainer
    # removes the ``sample_pdfs`` row from ``cordis.yml`` without
    # removing the spec entry (or vice versa).
    "omniscribe.plugins.sample_pdfs",
]

# Heavy ML deps and stdlib extras that PyInstaller's static analysis
# routinely misses. ``collect_submodules`` pulls in the full tree so
# we don't have to keep this list in lockstep with upstream releases.
_RUNTIME_SUBMODULES = (
    collect_submodules("surya")
    + collect_submodules("torch")
    + collect_submodules("torchvision")
    + collect_submodules("pymupdf")
    + collect_submodules("omniscribe")
    + collect_submodules("starlette")
    + collect_submodules("fastapi")
    + collect_submodules("uvicorn")
    + collect_submodules("sniffio")
    + collect_submodules("h11")
    + collect_submodules("scipy")
    + collect_submodules("pydantic_settings")
    # Phase 4.4 (2026-09-05, re-resolved): with anyio pinned to 3.x
    # (``anyio>=3.7,<4`` in the ``web`` extra), the lazy-import
    # dance that defeated ``collect_submodules`` in 4.x is gone, and
    # this single line bundles the full anyio 3.x submodule tree
    # (abc, streams, from_thread, etc.). Tested 2026-09-05: the
    # resulting 270 MB onefile boots successfully and serves
    # ``/api/health``. If we ever unpin anyio, this line is the
    # first thing to revisit.
    + collect_submodules("anyio")
    # stdlib / third-party that PyInstaller's analysis under-detects
    # for the way the harness uses them (lazy imports, importlib).
    + [
        "scipy._external.array_api_compat.numpy.fft",
        "torch._C",
        "torch.cuda",
        "surya.model.recognition",
        "surya.model.layout",
        "surya.model.detection",
        "surya.model.table_rec",
        "surya.ocr",
        "defusedxml",
        "defusedxml.ElementTree",
        "importlib.metadata",
    ]
)


# ---------------------------------------------------------------------------
# Bundle data
# ---------------------------------------------------------------------------

# Runtime data files. ``omniscribe.server`` resolves the cordis.yml
# path via ``Path(__file__).parent / "resources"`` (see
# ``src/omniscribe/harness/loader.py``), so the bundle's
# ``omniscribe/resources/`` must contain the original layout.
DATAS = [
    # (source_path_relative_to_ROOT, dest_path_inside_bundle)
    (os.path.join("src", "omniscribe", "resources"), "omniscribe/resources"),
]


# ---------------------------------------------------------------------------
# Excludes (dev / test only — keep the bundle small)
# ---------------------------------------------------------------------------

EXCLUDES = [
    "pytest",
    "pytest_asyncio",
    "pytest_cov",
    "hypothesis",
    # NOTE: anyio is intentionally NOT excluded — it is a runtime
    # dependency of FastAPI / Starlette / uvicorn, not a test dep.
    # Excluding it here was a misclassification during the 14-attempt
    # saga that fought ``collect_submodules("anyio")`` on line 133.
    # With the anyio 3.x pin, ``collect_submodules`` returns all 37
    # submodules cleanly; excluding it dropped them all again and
    # the binary booted with ``ModuleNotFoundError: anyio`` at the
    # first await. Verified 2026-09-06: removing this entry plus
    # adding ``import anyio.abc`` to ``scripts/run_server.py`` lets
    # the bundle serve ``/api/health`` -> 200.
    "mypy",
    "ruff",
    "pip",
    "setuptools",
    "wheel",
    "twine",
    "_pytest",
    "tests",
    "IPython",
    "jupyter",
    "notebook",
    "sphinx",
    # NOTE: pydantic-settings is intentionally NOT excluded — it is a
    # runtime dependency of omniscribe's RuntimeSettings (see
    # pyproject.toml line 50, "pydantic-settings>=2.5"). Excluding it
    # here was a misclassification; the bundled binary raised
    # ``ImportError: pydantic_settings`` at startup. Verified
    # 2026-09-06: removing this entry plus adding
    # ``collect_submodules("pydantic_settings")`` below fixes the boot.
    # PyInstaller itself — accidentally self-including this in a
    # bundle is a known footgun.
    "PyInstaller",
]


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

a = Analysis(
    ["scripts/run_server.py"],
    pathex=[SRC, ROOT],
    binaries=[],
    datas=DATAS,
    hiddenimports=_CORDIS_PLUGINS + _RUNTIME_SUBMODULES,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
    # The bundled binary runs as a console app; we do not enable
    # ``--noconsole`` here. A future ``omniscribe-server-gui`` build
    # could add a windowed variant (see RFC 001, "Option B" stretch).
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="omniscribe-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
