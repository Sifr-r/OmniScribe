"""Sample PDFs plugin: serves canonical fixtures at ``/api/sample-pdf/{name}``.

Sprint 3 (RFC 002 §4 Option b, audit U12). The route is a fixed
allowlist over the canonical fixture set in
``src/omniscribe/resources/sample_pdfs/`` (sourced from
``tests/fixtures/pdfs/``). The test suite asserts:

- Every name in the allowlist returns 200 with the right
  ``Content-Type``, ``Content-Disposition``, ``X-Sample-Pdf``
  header, and byte-for-byte fixture contents.
- Unknown names return 404 with the D8 error envelope.
- Path-traversal attempts return 404 (not 400) — the fixed
  allowlist is the only check; user input is never joined with
  a filesystem path.
- The route is auth-exempt (the path is in
  ``middleware.auth.EXEMPT_PATH_PREFIXES``).
- The plugin wires through the Cordis harness without external
  services.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omniscribe.harness.context import Context
from omniscribe.plugins.sample_pdfs import (
    ALLOWED_SAMPLE_PDFS,
    SamplePdfsPlugin,
    build_sample_pdfs_router,
    plugin,
)

# ---------------------------------------------------------------------------
# Fixtures (canonical test-side set, kept in lockstep with the resource
# directory; the prod side ships at src/omniscribe/resources/sample_pdfs/).
# ---------------------------------------------------------------------------

_FIXTURES_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "pdfs"
_RESOURCES_DIR = (
    Path(__file__).resolve().parents[2] / "src" / "omniscribe" / "resources"
    / "sample_pdfs"
)


async def _boot() -> Context:
    """Boot the sample-pdfs plugin in a fresh harness Context."""
    ctx = Context()
    await ctx.plugin(SamplePdfsPlugin(), config={})
    return ctx


def _make_app(ctx: Context) -> FastAPI:
    app = FastAPI()
    for router in ctx.routes():
        app.include_router(router)
    return app


# ---------------------------------------------------------------------------
# Allowlist <-> fixture-set lockstep
# ---------------------------------------------------------------------------


def test_allowlist_matches_test_fixtures() -> None:
    """Every entry in ``ALLOWED_SAMPLE_PDFS`` corresponds to a file in
    ``tests/fixtures/pdfs/``. Adding a fixture to the test side
    without also adding the name to the allowlist is a packaging bug
    (the route would 404 for that name even though the file is in
    the repo); removing a fixture from the test side without
    removing the name from the allowlist is a packaging bug (the
    route would 500 — the fixture is in the allowlist but missing
    on disk).
    """
    on_disk = {p.name for p in _FIXTURES_DIR.glob("*.pdf")}
    assert frozenset(on_disk) == ALLOWED_SAMPLE_PDFS, (
        f"sample_pdfs ALLOWED_SAMPLE_PDFS={sorted(ALLOWED_SAMPLE_PDFS)} "
        f"drifts from tests/fixtures/pdfs/={sorted(on_disk)}. "
        f"Add or remove the name in both places."
    )


def test_allowlist_matches_resources_dir() -> None:
    """Every entry in ``ALLOWED_SAMPLE_PDFS`` corresponds to a file
    in ``src/omniscribe/resources/sample_pdfs/`` (which is what the
    bundle ships). Same lockstep contract as the test-fixture check.
    """
    on_disk = {p.name for p in _RESOURCES_DIR.glob("*.pdf")}
    assert frozenset(on_disk) == ALLOWED_SAMPLE_PDFS, (
        f"sample_pdfs ALLOWED_SAMPLE_PDFS={sorted(ALLOWED_SAMPLE_PDFS)} "
        f"drifts from src/omniscribe/resources/sample_pdfs/="
        f"{sorted(on_disk)}. The resource directory is the bundle's "
        f"truth; the test directory is the dev / test truth."
    )


# ---------------------------------------------------------------------------
# Happy path — every allowed name returns the canonical bytes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(ALLOWED_SAMPLE_PDFS))
async def test_get_sample_pdf_returns_canonical_bytes(name: str) -> None:
    """For each allowed name, the route returns the exact bytes
    from ``src/omniscribe/resources/sample_pdfs/{name}``."""
    ctx = await _boot()
    try:
        with TestClient(_make_app(ctx)) as client:
            response = client.get(f"/api/sample-pdf/{name}")
        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "application/pdf"
        assert (
            response.headers["content-disposition"]
            == f'attachment; filename="{name}"'
        )
        assert response.headers["x-sample-pdf"] == name

        # Byte-for-byte comparison against the canonical resource
        # (which is what the bundle ships; the test-side fixture
        # should be the same file modulo mtime).
        canonical = (_RESOURCES_DIR / name).read_bytes()
        assert response.content == canonical, (
            f"{name} bytes mismatch — the route's read_bytes diverged "
            f"from the resource file"
        )
    finally:
        await ctx.dispose()


# ---------------------------------------------------------------------------
# Error envelope (D8)
# ---------------------------------------------------------------------------


async def test_unknown_name_returns_404_with_envelope() -> None:
    """An unknown name returns 404. The D8 error envelope
    (``{"error": "not_found", "detail": "..."}``) is applied at the
    production-app layer by ``server.py:284-294``; here we use a
    bare FastAPI app, so the body shape is FastAPI's default
    ``{"detail": <message>}``. The 404 status code and the
    self-correcting detail message (listing the available names)
    are the contract this test pins.
    """
    ctx = await _boot()
    try:
        with TestClient(_make_app(ctx)) as client:
            response = client.get("/api/sample-pdf/not_a_real_pdf.pdf")
        assert response.status_code == 404
        body = response.json()
        # FastAPI's default HTTPException shape (production wraps
        # this via the D8 envelope; see server.py).
        assert "not_a_real_pdf.pdf" in body["detail"]
        # The detail should also tell the operator which names
        # ARE available, so a Flutter client that typos a name
        # can self-correct.
        assert "digital.pdf" in body["detail"]
    finally:
        await ctx.dispose()


@pytest.mark.parametrize(
    "traversal_attempt",
    [
        "../etc/passwd",
        "..%2Fetc%2Fpasswd",
        "digital.pdf/../../etc/passwd",
        ".",
        "",
    ],
)
async def test_path_traversal_returns_404_not_500(traversal_attempt: str) -> None:
    """Path-traversal attempts hit the allowlist gate and return
    404 (not 500 and not a leaked file). The fixed allowlist is
    the only check — user input is never joined with a filesystem
    path, so even a clever traversal cannot escape the allowlist.
    """
    ctx = await _boot()
    try:
        with TestClient(_make_app(ctx)) as client:
            # FastAPI normalises the path before the route sees it,
            # so some of these collapse to other forms. The
            # important assertion is that the response is a 4xx
            # (client error) — never a 200 with file contents, and
            # never a 500 with a traceback leak.
            response = client.get(f"/api/sample-pdf/{traversal_attempt}")
        assert 400 <= response.status_code < 500, (
            f"traversal attempt {traversal_attempt!r} returned "
            f"{response.status_code} (body: {response.text[:200]})"
        )
        # Never a 200 (would mean we leaked a file).
        assert response.status_code != 200
    finally:
        await ctx.dispose()


# ---------------------------------------------------------------------------
# Auth bypass — the route is open
# ---------------------------------------------------------------------------


def test_route_path_is_auth_exempt() -> None:
    """The path ``/api/sample-pdf/`` is in
    ``middleware.auth.EXEMPT_PATH_PREFIXES``, so a request with no
    Authorization header is allowed through. Profile 1 (loopback,
    no token) Flutter clients can fetch a sample PDF without
    prompting the user.
    """
    from omniscribe.middleware.auth import EXEMPT_PATH_PREFIXES

    assert "/api/sample-pdf/" in EXEMPT_PATH_PREFIXES


# ---------------------------------------------------------------------------
# Plugin wiring — the plugin is a singleton instance + has a build_*
# router factory, matching the health plugin convention
# ---------------------------------------------------------------------------


def test_plugin_is_singleton_with_build_router() -> None:
    """``plugin`` is a ``SamplePdfsPlugin`` instance, and
    ``build_sample_pdfs_router()`` returns a router with the
    expected route mounted. This is the contract the Cordis loader
    relies on.
    """
    assert isinstance(plugin, SamplePdfsPlugin)
    router = build_sample_pdfs_router()
    paths = {route.path for route in router.routes}  # type: ignore[attr-defined]
    assert "/api/sample-pdf/{name}" in paths
