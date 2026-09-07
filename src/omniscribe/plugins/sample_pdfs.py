"""Sample PDFs plugin — serves canonical fixture PDFs at ``/api/sample-pdf/{name}``.

Audit U12: a new user has no easy way to confirm the install works
without finding their own PDF. RFC 002 §4 Option (b) addresses this
with a FastAPI route that streams a canonical PDF from the package's
``resources/sample_pdfs/`` directory, plus a Flutter "Try sample PDF"
button on the Workstation screen that calls the route and lands the
result in the normal job pipeline.

The route is **always open** (path-prefix exempt in
:data:`omniscribe.middleware.auth.EXEMPT_PATH_PREFIXES`). The
canonical fixtures are public-domain test assets already in the repo
(``tests/fixtures/pdfs/``); gating them behind a bearer would force
the Profile 1 loopback Flutter client to either send no token (auth
fails) or prompt the user (defeats the "try with sample" UX). For
Profile 3 (public-internet) deployments, the operator can either (a)
front the server with a reverse-proxy access rule that blocks
``/api/sample-pdf/`` at the edge, or (b) accept that the canonical
fixtures are world-readable.

The route uses a fixed allowlist of names — never joins user input
with the filesystem path — so path traversal
(``/api/sample-pdf/../etc/passwd``) is a structural impossibility,
not a sanitisation concern. Unknown names return
``HTTPException(404)`` so the D8 error envelope wraps the response
into ``{"error": "not_found", "detail": "..."}``.

The fixtures are shipped in
``src/omniscribe/resources/sample_pdfs/`` (copied from
``tests/fixtures/pdfs/`` at build time). PyInstaller's
``omniscribe_server.spec`` ``DATAS`` block already copies the
entire ``src/omniscribe/resources/`` tree into the bundle, so no
spec change is needed when this plugin is added.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from omniscribe.harness.context import Context
from omniscribe.harness.plugin import Plugin

#: Fixed allowlist of canonical sample PDFs. Mirrors the fixture set
#: in ``tests/fixtures/pdfs/`` (which is the test-side truth). Adding
#: a fixture to the test side requires also adding the name here; the
#: ``tests/plugins/test_sample_pdfs.py`` suite asserts the two stay
#: in lockstep. Never accept user-provided names against this set —
#: path traversal is a structural impossibility, not a sanitisation
#: concern.
ALLOWED_SAMPLE_PDFS: frozenset[str] = frozenset(
    {
        "digital.pdf",
        "handwritten.pdf",
        "hybrid.pdf",
        "dense.pdf",
        "notes.pdf",
    }
)

#: Resolved at import time: ``<package>/resources/sample_pdfs/``.
#: ``__file__`` is at ``src/omniscribe/plugins/sample_pdfs.py``; the
#: resources live at ``src/omniscribe/resources/sample_pdfs/``,
#: i.e. one level up from the plugin's directory. The spec's
#: ``DATAS`` block copies the whole ``src/omniscribe/resources/``
#: tree into the bundle, so the same relative path resolves
#: correctly when PyInstaller extracts the package to a temp dir
#: at runtime.
_SAMPLES_DIR: Path = Path(__file__).parent.parent / "resources" / "sample_pdfs"


def _resolve_sample_path(name: str) -> Path:
    """Return the absolute path to the canonical fixture for ``name``.

    The fixed allowlist is the *only* check — we never join user
    input with the filesystem path. If the name is not in the
    allowlist, the route raises ``HTTPException(404)`` before this
    helper is called.
    """
    return _SAMPLES_DIR / name


def build_sample_pdfs_router() -> APIRouter:
    """Mount the sample-PDF route at ``/api/sample-pdf/{name}``.

    The route is a single GET. Errors use ``HTTPException`` so the
    D8 envelope handler wraps the response into the standard
    ``{"error": <code>, "detail": <message>}`` shape.
    """
    router = APIRouter(tags=["sample-pdfs"])

    @router.get("/api/sample-pdf/{name}")
    async def get_sample_pdf(name: str) -> Response:
        # Allowlist check: path traversal is a structural
        # impossibility because we never join ``name`` with a
        # filesystem path. An unknown name returns 404, not 400,
        # so a Flutter client that typos a fixture name sees a
        # "not found" rather than a "bad request".
        if name not in ALLOWED_SAMPLE_PDFS:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"unknown sample: {name!r}. "
                    f"Available: {sorted(ALLOWED_SAMPLE_PDFS)}"
                ),
            )

        # The fixture is on disk at this point — the allowlist
        # gate is the only check. A missing file would be a
        # packaging bug, so surface it as 500 (audit M-3 catch-all
        # handler will log the traceback).
        path = _resolve_sample_path(name)
        try:
            data = path.read_bytes()
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=500,
                detail=(
                    f"sample PDF {name!r} is in the allowlist but "
                    f"missing on disk at {path} — packaging bug"
                ),
            ) from exc

        return Response(
            content=data,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{name}"',
                "X-Sample-Pdf": name,
            },
        )

    return router


class SamplePdfsPlugin(Plugin):
    """Mounts the sample-PDF route on the FastAPI app.

    No external dependencies (no RuntimeService, no ProviderService,
    no StateBackend). The route is read-only and stateless, so the
    plugin is a single ``ctx.mount_router`` call.
    """

    async def apply(self, ctx: Context) -> None:
        ctx.mount_router(build_sample_pdfs_router())


#: Singleton plugin instance. The Cordis loader imports this and
#: calls :meth:`SamplePdfsPlugin.apply` during the harness boot.
plugin = SamplePdfsPlugin()

__all__ = [
    "ALLOWED_SAMPLE_PDFS",
    "SamplePdfsPlugin",
    "build_sample_pdfs_router",
    "plugin",
]
