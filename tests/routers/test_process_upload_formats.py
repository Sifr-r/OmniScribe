"""Upload-format promises at the HTTP boundary.

Assessment finding 8: the README advertised BMP/TIFF, the rasterizer and
content-sniffer already handled them, but ``/api/process`` rejected them with
415 because the content-type allowlist and magic-byte table never listed
them. These tests exercise the real route.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from .conftest import PDF_BYTES

# Minimal headers — the route sniffs these; the pipeline is faked.
BMP_BYTES = b"BM" + (54).to_bytes(4, "little") + b"\x00" * 8 + b"\x36\x00\x00\x00"
TIFF_LE_BYTES = b"II\x2a\x00\x08\x00\x00\x00" + b"\x00" * 4
TIFF_BE_BYTES = b"MM\x00\x2a\x00\x00\x00\x08" + b"\x00" * 4


def _upload(name: str, data: bytes, content_type: str) -> dict[str, Any]:
    return {"files": {"file": (name, data, content_type)}}


@pytest.mark.parametrize(
    "name, data, content_type",
    [
        ("scan.bmp", BMP_BYTES, "image/bmp"),
        ("scan.tiff", TIFF_LE_BYTES, "image/tiff"),
        ("scan_big.tiff", TIFF_BE_BYTES, "image/tiff"),
    ],
)
def test_raster_formats_are_accepted(
    api_client: TestClient,
    fake_pipeline: dict[str, Any],
    name: str,
    data: bytes,
    content_type: str,
) -> None:
    response = api_client.post("/api/process", **_upload(name, data, content_type))
    assert response.status_code == 200, response.text
    assert response.content == PDF_BYTES
    assert response.headers["x-text-artifact-id"]


@pytest.mark.parametrize(
    "data, content_type",
    [
        # content sniff, because the Flutter picker sends octet-stream
        (BMP_BYTES, "application/octet-stream"),
        (TIFF_LE_BYTES, "application/octet-stream"),
    ],
)
def test_raster_formats_survive_octet_stream_uploads(
    api_client: TestClient,
    fake_pipeline: dict[str, Any],
    data: bytes,
    content_type: str,
) -> None:
    response = api_client.post(
        "/api/process", **_upload("scan.bin", data, content_type)
    )
    assert response.status_code == 200, response.text


def test_an_image_prefix_without_a_valid_magic_is_rejected(
    api_client: TestClient, fake_pipeline: dict[str, Any]
) -> None:
    """Guard the new signature: an "II" prefix alone is not a TIFF."""
    response = api_client.post(
        "/api/process",
        # "II" followed by zeros: the classic TIFF magic is II\\x2a\\x00, so
        # this must not be accepted as a TIFF.
        **_upload("scan.tiff", b"II" + b"\x00" * 10, "application/octet-stream"),
    )
    assert response.status_code == 415


def test_415_message_lists_the_raster_formats(
    api_client: TestClient, fake_pipeline: dict[str, Any]
) -> None:
    response = api_client.post(
        "/api/process",
        **_upload("a.gif", b"GIF89a" + b"\x00" * 6, "image/gif"),
    )
    assert response.status_code == 415
    body = response.text
    for name in ("PDF", "PNG", "JPEG", "WebP", "AVIF", "TIFF", "BMP"):
        assert name in body
