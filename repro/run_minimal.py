"""Minimal entry point for the anyio + PyInstaller static-analysis bug.

This is the smallest possible program that exercises the failure mode
documented in ``docs/deployment/windows-bundle.md`` §"Known build issue":
importing ``anyio.abc`` and doing nothing else. The point is to
isolate the anyio bundling interaction from the rest of the OmniScribe
dependency tree (torch, surya, pymupdf, the Cordis plugin tree) so
upstream can confirm the bug in 30 lines of spec instead of 235.
"""

import anyio.abc
import anyio.from_thread
import anyio.streams


def main() -> int:
    # The mere fact that ``anyio.abc`` resolves at runtime proves the
    # bundle shipped it. We avoid ``importlib.metadata.version("anyio")``
    # because the dist-info is not bundled — anyio's own runtime
    # needs it for backend selection, so a separate reproducer in
    # ``repro/run_metadata.py`` exercises that path explicitly.
    print(f"anyio {anyio.__name__}.{anyio.abc.__name__} loaded ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
