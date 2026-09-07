"""Tiny entry-point wrapper for the PyInstaller bundle.

Phase 4.4 of the remediation plan introduces a single-binary Windows
distribution of the OmniScribe FastAPI server (RFC 001, Option A).
The PyInstaller spec at the repo root analyses this file as its
single entry point, so that:

1. The spec stays small and reviewable (one explicit entry, no
   ``-m`` magic to debug).
2. Users running from source can also do ``python scripts/run_server.py``
   instead of going through the ``omniscribe-server`` console
   script, which is a friendlier dev affordance on platforms where
   the venv's bin/ isn't on ``PATH`` (e.g. some IDE run-configs).
3. The actual server module (``omniscribe.server``) is unchanged.
   PyInstaller's static analysis picks it up via the
   ``from omniscribe.server import main`` line below, plus the
   ``hiddenimports`` list in the spec for the cordis-plugin modules
   that are loaded dynamically at runtime.

See ``omniscribe_server.spec`` for the bundle build, and
``docs/deployment/windows-bundle.md`` for the end-user-facing
install + run guide.
"""

from __future__ import annotations

import argparse

# PyInstaller's static analysis doesn't follow ``import anyio.abc`` deep
# inside FastAPI / Starlette / uvicorn. Force-importing it here ensures
# the PYZ archive contains the anyio module — without this the bundled
# binary raises ``ModuleNotFoundError: anyio`` at the first await.
# Verified 2026-09-06: with the ``anyio>=3.7,<4`` pin in the ``web``
# extra, the 37 anyio submodules import cleanly and the bundle boots.
import anyio.abc  # noqa: F401

from omniscribe.server import main

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run the OmniScribe API server",
        add_help=False,
    )
    args, _unknown = parser.parse_known_args()
    if "--check_imports" in _unknown:
        # Sprint 4 (option b) diagnostic: confirm the bundle's
        # bundled module set actually contains surya. The
        # ``--check_imports`` flag is the same flag PyInstaller
        # uses for the same purpose, so the operator's
        # ``omniscribe-server --check_imports`` invocation is
        # idiomatic.
        import importlib

        for name in ("surya", "surya.detection", "surya.recognition"):
            try:
                m = importlib.import_module(name)
                print(f"OK  {name}: {getattr(m, '__file__', '<namespace>')}")
            except Exception as e:
                print(f"FAIL {name}: {type(e).__name__}: {e}")
        raise SystemExit(0)
    main()
